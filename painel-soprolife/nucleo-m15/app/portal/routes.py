"""As SEIS coisas que a superfície pública sabe fazer.

1. validar o acesso (token do link + data de nascimento);
2. conferir o segundo fator;
3. listar os documentos daquele acesso;
4. servir o PDF assinado correspondente;
5. servir o PDF técnico correspondente;
6. confirmar que um código impresso no laudo corresponde a um documento
   real e liberado (M26.19) — sem sessão nenhuma, e sem devolver paciente,
   exame nem conteúdo clínico.

Não existe rota de busca, de listagem, de paciente, de usuário nem de
qualquer outra coisa — e o teste
`test_18_e_23_superficie_publica_tem_exatamente_estas_rotas` congela isso
comparando o conjunto de rotas registradas com esta lista. Crescer a lista
acima (a sétima coisa) exige atualizar esse teste na MESMA mudança — é a
fricção deliberada que existe para isso.

`/health` acompanha, e é institucional: devolve `{"status": "ok"}` e mais
nada. Sem ele não existe smoke de produção nem monitoração.

Regras de conversa com quem está do outro lado:

- **mensagem genérica sempre.** Token inexistente e data de nascimento
  errada produzem a MESMA resposta. Um erro diferente para cada caso seria
  um oráculo: bastaria varrer tokens até a mensagem mudar para saber que um
  paciente existe.
- **nada de identidade antes da validação.** A resposta do primeiro passo
  não tem nome, iniciais, contagem de documentos nem data de exame.
"""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..errors import ReportDomainError
from ..models import STATUS_LAUDO_LIBERADO
from ..services import patient_results as prs
from ..services.crm_display import format_crm_full
from ..services.download_names import (
    SUFIXO_ASSINADO,
    SUFIXO_MIR,
    content_disposition,
    named_download_filename,
)
from ..services.report_storage import (
    ReportStorageError,
    StoredPdfIntegrityError,
    StoredPdfMissingError,
    read_and_validate_stored_pdf,
)
from .security import (
    aplicar_cabecalhos,
    definir_cookie,
    ler_cookie,
    limitador,
    limpar_cookie,
    montar_cookie,
    origem_da_requisicao,
)

# O prefixo é único e literal. `main.py` o usa para recusar, na camada mais
# externa, qualquer caminho que não comece por ele.
PREFIXO_PUBLICO = "/p/v1/"

router = APIRouter(prefix=PREFIXO_PUBLICO.rstrip("/"), tags=["portal-resultados"])

# As três únicas mensagens que o portal produz para uma falha. Curtas,
# genéricas e acionáveis — a pessoa do outro lado é um paciente no celular,
# não um operador com o manual aberto.
MSG_INVALIDO = (
    "Não foi possível abrir este resultado. Confira o link recebido e a data "
    "de nascimento informada."
)
MSG_TENTATIVAS = (
    "Muitas tentativas seguidas. Aguarde alguns minutos e tente novamente."
)
MSG_EXPIRADO = (
    "Este acesso expirou. Entre em contato com a SoproLife para gerar um "
    "novo link."
)
# M26.19 — mesma mensagem para código inexistente e para código de laudo
# ainda não liberado (prévia, corretiva em elaboração etc.): a mesma regra
# de "resposta genérica sempre" das outras rotas.
MSG_LAUDO_NAO_LOCALIZADO = (
    "Nenhum laudo liberado corresponde a este código. Confira o código "
    "impresso no documento."
)

# Mesmo formato aceito pela verificação administrativa equivalente
# (`GET /laudos/validacao/{codigo}` no Command Center) — os dois lêem o
# mesmo `validation_code`.
_CODIGO_VALIDACAO_RE = re.compile(r"^[A-Z0-9]{8,24}$")


def _invalido() -> ReportDomainError:
    return ReportDomainError(401, "acesso_invalido", MSG_INVALIDO)


def _tentativas() -> ReportDomainError:
    return ReportDomainError(429, "muitas_tentativas", MSG_TENTATIVAS)


def _expirado() -> ReportDomainError:
    return ReportDomainError(410, "acesso_expirado", MSG_EXPIRADO)


def _laudo_nao_localizado() -> ReportDomainError:
    return ReportDomainError(404, "laudo_nao_localizado", MSG_LAUDO_NAO_LOCALIZADO)


class PedidoDeAcesso(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    # Aceita ISO (`2001-04-09`), que é o que o `<input type="date">` envia.
    nascimento: date

    @field_validator("token")
    @classmethod
    def _token_sem_ruido(cls, v: str) -> str:
        return v.strip()


def _pessoa_e_exame(db: Session, acesso):
    pessoa = prs.load_patient(db, acesso.person_id)
    exame = prs.load_exam(db, acesso.spirometry_exam_id)
    if pessoa is None or exame is None:
        raise _invalido()
    return pessoa, exame


def _corpo_do_resultado(db: Session, acesso) -> dict:
    """O que a página mostra DEPOIS de autenticar.

    Nome, tipo de exame, data e os dois botões. Nenhum identificador
    interno, nenhum hash, nenhum código LAU/ESP/PES, nada de financeiro e
    nada clínico além do próprio documento que a pessoa vai baixar.
    """

    pessoa, exame = _pessoa_e_exame(db, acesso)
    assinado = prs.signed_version(db, acesso)
    tecnico = prs.technical_version(db, acesso)
    return {
        "paciente": {"nome": pessoa.nome_completo},
        "exame": {
            "tipo": "Espirometria",
            "data": exame.data_exame.isoformat() if exame.data_exame else None,
        },
        "status": "Resultado disponível",
        "documentos": [
            {
                "chave": "laudo-assinado",
                "rotulo": "Baixar laudo assinado",
                "disponivel": assinado is not None,
            },
            {
                "chave": "exame-tecnico",
                "rotulo": "Baixar exame técnico",
                "disponivel": tecnico is not None,
            },
        ],
        "orientacao": (
            "Em caso de dúvida sobre o resultado, converse com seu médico."
        ),
    }


@router.get("/health")
def health() -> dict:
    """Vivacidade. Não toca no banco e não conta nada sobre pacientes."""

    return {"status": "ok", "servico": "portal-resultados"}


@router.post("/acesso")
def abrir_acesso(
    payload: PedidoDeAcesso,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """Os dois fatores, nesta ordem, com a mesma recusa para qualquer falha."""

    _exigir_portal_ligado()
    origem = origem_da_requisicao(request)
    if limitador.bloqueado(origem):
        raise _tentativas()

    acesso = prs.find_by_token(db, payload.token)
    if acesso is None:
        # Token inexistente NÃO cria linha, então o freio aqui é o de rede.
        limitador.registrar_falha(origem)
        raise _invalido()

    if acesso.status == "revogado" or prs.is_expired(acesso):
        raise _expirado()
    if prs.is_locked(acesso):
        raise _tentativas()

    pessoa = prs.load_patient(db, acesso.person_id)
    if pessoa is None or pessoa.arquivado:
        raise _invalido()

    if not prs.check_birthdate(pessoa, payload.nascimento):
        prs.register_failure(acesso)
        limitador.registrar_falha(origem)
        audit(
            db,
            "resultado_portal_tentativa_invalida",
            entidade="patient_result_accesses",
            entidade_id=acesso.id,
            detalhes={"motivo": "segundo_fator", "status": acesso.status},
        )
        db.commit()
        raise _invalido()

    prs.register_success(acesso)
    segredo, sessao = prs.create_session(db, acesso)
    audit(
        db,
        "resultado_portal_autenticado",
        entidade="patient_result_accesses",
        entidade_id=acesso.id,
        detalhes={"status": acesso.status, "canal": "portal"},
    )
    db.commit()
    definir_cookie(response, montar_cookie(sessao.id, segredo))
    return _corpo_do_resultado(db, acesso)


def _sessao_ativa(request: Request, db: Session):
    """Fail-closed: sem cookie válido, o portal não conhece ninguém."""

    settings = get_settings()
    bruto = request.cookies.get(settings.portal_cookie_name)
    lido = ler_cookie(bruto)
    if lido is None:
        raise _invalido()
    carregada = prs.load_session(db, lido[0], lido[1])
    if carregada is None:
        raise _invalido()
    return carregada


@router.get("/documentos")
def listar_documentos(request: Request, db: Session = Depends(get_db)) -> dict:
    """Recarregar a página não obriga a digitar a data de nascimento de novo."""

    _exigir_portal_ligado()
    _sessao, acesso = _sessao_ativa(request, db)
    return _corpo_do_resultado(db, acesso)


@router.post("/sair")
def sair(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    """Encerra a sessão do aparelho. Não altera o acesso nem o link."""

    _exigir_portal_ligado()
    sessao, _acesso = _sessao_ativa(request, db)
    sessao.revoked_at = prs.agora()
    db.commit()
    limpar_cookie(response)
    return {"status": "encerrada"}


def _entregar(
    db: Session,
    *,
    acesso,
    versao,
    sufixo: str,
    tipo: str,
) -> Response:
    """Lê os bytes EXATOS daquela versão, confere hash e devolve.

    A releitura passa por `read_and_validate_stored_pdf`, que compara
    tamanho, páginas e sha256 com os metadados gravados. Um arquivo trocado
    no disco vira erro, nunca um download silencioso do conteúdo errado.
    """

    if versao is None:
        raise ReportDomainError(
            404,
            "documento_indisponivel",
            "Este documento não está disponível. Entre em contato com a "
            "SoproLife.",
        )
    settings = get_settings()
    try:
        armazenado = read_and_validate_stored_pdf(
            settings.resolved_reports_storage_dir() / versao.storage_path,
            root=settings.resolved_reports_storage_dir(),
            expected_sha256=versao.sha256,
            expected_size_bytes=versao.size_bytes,
            expected_page_count=versao.page_count,
            max_size_bytes=settings.reports_max_upload_bytes,
            allow_signature_form=True,
        )
    except (StoredPdfMissingError, StoredPdfIntegrityError, ReportStorageError,
            OSError, ValueError):
        raise ReportDomainError(
            503,
            "documento_indisponivel",
            "Não foi possível preparar o arquivo agora. Tente novamente em "
            "alguns minutos.",
        ) from None

    pessoa = prs.load_patient(db, acesso.person_id)
    nome = named_download_filename(
        patient_name=pessoa.nome_completo if pessoa else None,
        fallback_code="Resultado",
        sufixo=sufixo,
    )
    prs.register_download(acesso)
    audit(
        db,
        "resultado_portal_documento_baixado",
        entidade="patient_result_accesses",
        entidade_id=acesso.id,
        detalhes={"tipo": tipo, "sha256": versao.sha256, "canal": "portal"},
    )
    db.commit()
    return Response(
        content=armazenado.data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                nome, disposition="attachment"
            ),
        },
    )


@router.get("/documentos/laudo-assinado")
def baixar_laudo(request: Request, db: Session = Depends(get_db)) -> Response:
    """EXATAMENTE `ExternalSignedDocument.report_document_version_id`.

    Nunca `source_version_id` (o PDF que foi assinar), nunca
    `current_version_id` genérico, nunca "o último PDF do sistema".
    """

    _exigir_portal_ligado()
    _sessao, acesso = _sessao_ativa(request, db)
    return _entregar(
        db,
        acesso=acesso,
        versao=prs.signed_version(db, acesso),
        sufixo=SUFIXO_ASSINADO,
        tipo="laudo_assinado",
    )


@router.get("/documentos/exame-tecnico")
def baixar_tecnico(request: Request, db: Session = Depends(get_db)) -> Response:
    """A MIR original do MESMO laudo — versão `original` daquele documento."""

    _exigir_portal_ligado()
    _sessao, acesso = _sessao_ativa(request, db)
    return _entregar(
        db,
        acesso=acesso,
        versao=prs.technical_version(db, acesso),
        sufixo=SUFIXO_MIR,
        tipo="exame_tecnico",
    )


@router.get("/verificar/{codigo}")
def verificar_laudo(
    codigo: str, request: Request, db: Session = Depends(get_db)
) -> dict:
    """Confirma que um código impresso no laudo é real e liberado.

    Sem sessão, sem cookie, sem 2º fator — é o próprio código (alta entropia,
    impresso no papel) que autoriza a consulta, do mesmo jeito que conferir
    a autenticidade de um diploma ou de uma certidão. A resposta é SÓ
    institucional: nada de nome de paciente, nada de código de exame, nada
    clínico. O mesmo código inexistente e o mesmo código de um laudo ainda
    não liberado (prévia, corretiva em elaboração) produzem a mensagem
    idêntica — não é um oráculo de "este laudo existe mas não terminou".
    """

    _exigir_portal_ligado()
    origem = origem_da_requisicao(request)
    if limitador.bloqueado(origem):
        raise _tentativas()

    normalizado = (codigo or "").strip().upper()
    if not _CODIGO_VALIDACAO_RE.fullmatch(normalizado):
        limitador.registrar_falha(origem)
        raise _laudo_nao_localizado()

    laudo = prs.find_report_by_validation_code(db, normalizado)
    if laudo is None or laudo.status != STATUS_LAUDO_LIBERADO:
        limitador.registrar_falha(origem)
        raise _laudo_nao_localizado()

    medica = (
        prs.load_physician_public(db, laudo.released_physician_profile_id)
        if laudo.released_physician_profile_id
        else None
    )
    sha256 = (
        prs.version_sha256(db, laudo.current_version_id)
        if laudo.current_version_id
        else None
    )
    audit(
        db,
        "laudo_verificado_publicamente",
        entidade="report_documents",
        entidade_id=laudo.id,
        detalhes={
            "validation_code": laudo.validation_code,
            "report_code": laudo.public_code,
        },
    )
    db.commit()
    return {
        "laudo": laudo.public_code,
        "codigo_verificacao": laudo.validation_code,
        "liberado_em": laudo.released_at.isoformat() if laudo.released_at else None,
        "medica_nome": medica.professional_name if medica else None,
        "medica_crm": (
            format_crm_full(
                medica.crm_number,
                medica.crm_state,
                crm_display=medica.crm_display,
            )
            if medica
            else None
        ),
        "medica_rqe": medica.rqe if medica else None,
        "documento_sha256": sha256,
        "instituicao": "SoproLife Diagnósticos e Soluções em Saúde",
    }


def _exigir_portal_ligado() -> None:
    if not get_settings().portal_enabled:
        raise ReportDomainError(
            503,
            "portal_desabilitado",
            "O portal de resultados não está disponível no momento.",
        )
