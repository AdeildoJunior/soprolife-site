#!/usr/bin/env python3
"""M25.30 — regulariza laudos assinados que voltaram fora do fluxo.

**O problema que este script existe para resolver.** Três laudos foram
concluídos e assinados de verdade, com certificado, durante o período em que
a recepção de assinados estava quebrada. O PDF assinado ficou com a médica; o
sistema nunca recebeu os bytes. Resultado: documentos que estão assinados no
mundo real aparecem para a administração como "Aguardando assinatura", e
ninguém consegue entregá-los pela tela.

O caminho normal (a médica reenviar pelo painel) não serve aqui por um motivo
que não é técnico: **ela não vai executar um upload agora**. Registrar o
recebimento como se ela tivesse feito colocaria na trilha uma ação que não
aconteceu, com data errada e ator errado. Isto aqui é MANUTENÇÃO
ADMINISTRATIVA HISTÓRICA, e a trilha diz exatamente isso — o ator é a conta
administrativa informada em `--ator`, e `audit_logs` registra o contexto.

**As guardas são as mesmas da M25.29H, sem exceção nem atalho.** O que este
script faz é substituir o transporte (HTTP multipart pela médica autenticada)
por um transporte administrativo; ele não relaxa NENHUMA das verificações que
decidem se um arquivo pode representar o laudo assinado:

* a origem é a versão final CORRENTE do laudo (`current_version_id`);
* o arquivo não é prévia;
* o arquivo não é byte a byte igual ao PDF final sem assinatura;
* o arquivo traz estrutura de assinatura (`/ByteRange` + `/Sig`);
* a associação com o laudo é FORTE — carimbo coerente, código de verificação
  ou o final inteiro contido no arquivo — nunca o código LAU impresso
  sozinho, que a prévia também carrega.

Sobre essas, acrescenta as verificações que só fazem sentido numa importação
manual e nomeada: SHA-256 do arquivo, código de verificação, código do exame
e número da versão final precisam bater com um MANIFESTO versionado. Um
arquivo que não estiver no manifesto não entra, mesmo que passe em tudo.

**Identificação é pelo CONTEÚDO.** O nome do arquivo nunca decide a qual
laudo ele pertence — ele é apenas gravado como `received_filename`, para que
a médica reconheça o arquivo depois. Foi associar por semelhança de nome que
sempre esteve fora de questão neste sistema, e continua.

**O que este script NUNCA faz:**

* não apaga nada — nem versão, nem blob, nem hash, nem lote, nem trilha;
* não toca nos `ExternalSignedDocument` históricos recusados, que continuam
  como evidência do que aconteceu;
* não reescreve `audit_logs` (a tabela é append-only nas duas camadas);
* não marca entrega: "assinado recebido" e "entregue ao paciente" são fatos
  diferentes, e só o primeiro é verdade aqui;
* não cria `validado_externamente` nem preenche `validated_by_user_id` /
  `validated_at` — ninguém conferiu assinatura fora daqui, e afirmar que
  alguém conferiu seria falso;
* não afirma assinatura qualificada: `qualified_signature` continua FALSO em
  toda a trilha. Este script confere DOCUMENTO, não criptografia — não
  valida cadeia ICP-Brasil, certificado, revogação nem digest.

**Tudo ou nada.** A análise dos arquivos roda inteira antes de qualquer
escrita. Se um único caso divergir, nada é gravado e a divergência é
impressa. Não existe `--forcar`.

**Idempotente.** O mesmo arquivo, no mesmo laudo, não vira segunda versão: a
constraint `uq_assinado_documento_sha256` garante isso no banco, e o script
detecta antes, informa e segue sem escrever.

Por padrão roda em **dry-run**. Escrever exige `--apply` explícito.

Uso:
    python scripts/importar_assinados_historicos.py \\
        --diretorio /caminho/dos/assinados --ator admin@empresa
    python scripts/importar_assinados_historicos.py \\
        --arquivo a.pdf --arquivo b.pdf --arquivo c.pdf \\
        --ator admin@empresa --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.audit import audit  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import build_engine  # noqa: E402
from app.models import (  # noqa: E402
    ASSINADO_ACEITO,
    ASSINADO_RECUSADO,
    BATCH_DIRECAO_UPLOAD,
    PAREAMENTO_CODIGO_LAUDO,
    PAREAMENTO_CODIGO_VALIDACAO,
    PAREAMENTO_METADADO,
    ExternalSignedDocument,
    PhysicianProfile,
    ReportAssignment,
    ReportDocument,
    SpirometryExam,
    User,
)
from app.security import ADMINISTRATIVE_ROLES, ROLE_MEDICO  # noqa: E402
from app.services.report_publication import (  # noqa: E402
    report_publication_transaction,
)
from app.services.signature_acceptance import (  # noqa: E402
    VALIDATION_CODE_RE,
    GuardasDocumentais,
    avaliar,
)
from app.services.signature_batch import (  # noqa: E402
    read_codes_from_content,
    read_markers_from_metadata,
)

# Importar do router é deliberado: gravar a versão por um caminho PRÓPRIO
# seria escrever uma segunda implementação do mesmo contrato — caminho de
# armazenamento, validação do PDF, releitura de integridade e numeração de
# versão — e duas implementações divergem com o tempo. O que muda aqui é
# quem age e por quê, nunca como o arquivo é gravado.
from app.routers.reports import (  # noqa: E402
    KIND_LAUDO_ASSINADO_EXTERNO,
    _abrir_lote,
    _read_stored_version,
    _store_new_version,
    _versao_para_assinatura,
)

# --------------------------------------------------------------- manifesto
#
# O manifesto é VERSIONADO porque a pergunta "estes bytes são mesmo o laudo
# assinado do LAU-000010?" precisa de uma resposta que não dependa de quem
# está rodando o script hoje. Ele guarda somente identificadores técnicos e
# hashes: nenhum nome de paciente, nenhum dado clínico. O nome do arquivo
# entra em `received_filename` lido do disco, nunca daqui.

CHAVES_MANIFESTO = ("lau", "esp", "validation_code", "versao_final", "sha256")


@dataclass(frozen=True)
class CasoEsperado:
    """Um laudo histórico e a prova de qual arquivo lhe pertence."""

    lau: str
    esp: str
    validation_code: str
    versao_final: int
    sha256: str


# Os três laudos assinados durante o período em que a recepção estava
# quebrada. SHA-256 conferido no arquivo original antes de qualquer cópia.
CASOS_M25_30 = (
    CasoEsperado(
        lau="LAU-000010",
        esp="ESP-000029",
        validation_code="QYGKDTTVEY9D",
        versao_final=3,
        sha256=(
            "c600fe6340c8a18f8721632ac58b1ae0d301e3a983f13b0db6914467ebf65816"
        ),
    ),
    CasoEsperado(
        lau="LAU-000014",
        esp="ESP-000025",
        validation_code="RC7N7JCZJXHY",
        versao_final=3,
        sha256=(
            "05ef4c075865e21f4f197de100d3d0401ff94bccab73e32abee97ba2afdc7c87"
        ),
    ),
    CasoEsperado(
        lau="LAU-000015",
        esp="ESP-000030",
        validation_code="W4YB3AVFZDQ5",
        versao_final=4,
        sha256=(
            "b4f1d78049483cc7e2e191fc03eded9753aad1975933497d21ce08fc4dc84c2a"
        ),
    ),
)


def carregar_manifesto(caminho: Path) -> tuple[CasoEsperado, ...]:
    """Lê um manifesto externo — usado pelos testes e por casos futuros."""

    dados = json.loads(caminho.read_text(encoding="utf-8"))
    casos = []
    for entrada in dados:
        faltando = [c for c in CHAVES_MANIFESTO if c not in entrada]
        if faltando:
            raise ValueError(
                f"manifesto incompleto; faltam {', '.join(faltando)}"
            )
        casos.append(
            CasoEsperado(
                lau=str(entrada["lau"]).strip().upper(),
                esp=str(entrada["esp"]).strip().upper(),
                validation_code=str(entrada["validation_code"]).strip().upper(),
                versao_final=int(entrada["versao_final"]),
                sha256=str(entrada["sha256"]).strip().lower(),
            )
        )
    return tuple(casos)


# ------------------------------------------------------------ identificação


def _codigo_util(codigo: str | None) -> str | None:
    """Normaliza um código, tratando o travessão da prévia como ausência."""

    if codigo is None:
        return None
    limpo = codigo.strip().upper()
    return None if limpo in {"", "-", "—", "--", "N/A", "NA"} else limpo


@dataclass(frozen=True)
class Identificacao:
    """O que os BYTES do arquivo dizem sobre a que laudo ele pertence."""

    lau: str | None
    validation_code: str | None
    version_number: int | None
    match_method: str | None


def identificar_pelo_conteudo(dados: bytes) -> Identificacao:
    """Lê os códigos do PDF. O nome do arquivo não participa desta função.

    A ordem é a da confiança, igual à da recepção pelo painel: carimbo em
    metadado primeiro (o caminho normal), texto impresso como reserva para
    laudos concluídos antes de o carimbo existir.
    """

    meta = read_markers_from_metadata(dados)
    impresso = read_codes_from_content(
        dados, validation_code_pattern=VALIDATION_CODE_RE
    )

    lau_meta = _codigo_util(meta.report_code)
    validacao_meta = _codigo_util(meta.validation_code)
    lau_impresso = _codigo_util(impresso.report_code)
    validacao_impressa = _codigo_util(impresso.validation_code)

    if lau_meta or validacao_meta:
        metodo = PAREAMENTO_METADADO
    elif lau_impresso:
        metodo = PAREAMENTO_CODIGO_LAUDO
    elif validacao_impressa:
        metodo = PAREAMENTO_CODIGO_VALIDACAO
    else:
        metodo = None

    return Identificacao(
        lau=lau_meta or lau_impresso,
        validation_code=validacao_meta or validacao_impressa,
        version_number=meta.version_number,
        match_method=metodo,
    )


# ----------------------------------------------------------------- análise


@dataclass
class Achado:
    """Um arquivo, tudo o que se sabe dele, e o veredito."""

    caminho: Path
    sha256: str
    tamanho: int
    identificacao: Identificacao
    caso: CasoEsperado | None = None
    document: ReportDocument | None = None
    exam: SpirometryExam | None = None
    origem: object | None = None
    profile: PhysicianProfile | None = None
    guardas: GuardasDocumentais | None = None
    ja_regularizado: ExternalSignedDocument | None = None
    divergencias: list[str] = field(default_factory=list)

    @property
    def nome(self) -> str:
        return self.caminho.name

    @property
    def ok(self) -> bool:
        return not self.divergencias

    @property
    def a_gravar(self) -> bool:
        return self.ok and self.ja_regularizado is None


def _assinado_vigente(
    db: Session, document_id: str
) -> ExternalSignedDocument | None:
    """O assinado que REPRESENTA o laudo hoje. Recusado não representa."""

    return db.execute(
        select(ExternalSignedDocument)
        .where(ExternalSignedDocument.report_document_id == document_id)
        .where(ExternalSignedDocument.status != ASSINADO_RECUSADO)
        .order_by(ExternalSignedDocument.received_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _perfil_atribuido(
    db: Session, document_id: str
) -> PhysicianProfile | None:
    """A médica responsável JÁ atribuída ao laudo. Não se escolhe uma aqui."""

    atribuicao = db.execute(
        select(ReportAssignment)
        .where(ReportAssignment.report_document_id == document_id)
        .where(ReportAssignment.active.is_(True))
    ).scalar_one_or_none()
    if atribuicao is None:
        return None
    return db.get(PhysicianProfile, atribuicao.physician_profile_id)


def analisar_arquivo(
    db: Session, caminho: Path, *, por_lau: dict[str, CasoEsperado]
) -> Achado:
    """Reúne a evidência de UM arquivo e diz se ele pode ser gravado.

    Nada é escrito aqui. A função é pura o suficiente para que o dry-run e o
    `--apply` vejam exatamente o mesmo veredito — se divergissem, o dry-run
    deixaria de ser um ensaio e passaria a ser uma opinião.
    """

    dados = caminho.read_bytes()
    achado = Achado(
        caminho=caminho,
        sha256=hashlib.sha256(dados).hexdigest(),
        tamanho=len(dados),
        identificacao=identificar_pelo_conteudo(dados),
    )
    problema = achado.divergencias.append

    if achado.identificacao.match_method is None:
        problema(
            "o conteúdo do PDF não traz código de laudo nem código de "
            "verificação; não é identificável sem confiar no nome do arquivo"
        )
        return achado
    if achado.identificacao.lau is None:
        problema("o conteúdo do PDF não traz o código do laudo (LAU-XXXXXX)")
        return achado

    achado.caso = por_lau.get(achado.identificacao.lau)
    if achado.caso is None:
        problema(
            f"o conteúdo aponta para {achado.identificacao.lau}, que não está "
            "no manifesto desta regularização"
        )
        return achado
    caso = achado.caso

    if achado.sha256 != caso.sha256:
        problema(
            f"SHA-256 do arquivo ({achado.sha256}) diverge do manifesto "
            f"({caso.sha256})"
        )
    if achado.identificacao.validation_code != caso.validation_code:
        problema(
            "código de verificação no arquivo "
            f"({achado.identificacao.validation_code or '—'}) diverge do "
            f"manifesto ({caso.validation_code})"
        )
    if (
        achado.identificacao.version_number is not None
        and achado.identificacao.version_number != caso.versao_final
    ):
        problema(
            "o carimbo do arquivo cita a versão "
            f"{achado.identificacao.version_number}, e o manifesto espera a "
            f"versão final {caso.versao_final}"
        )

    achado.document = db.execute(
        select(ReportDocument).where(ReportDocument.public_code == caso.lau)
    ).scalar_one_or_none()
    if achado.document is None:
        problema(f"laudo {caso.lau} não existe neste banco")
        return achado
    document = achado.document

    if _codigo_util(document.validation_code) != caso.validation_code:
        problema(
            f"o código de verificação gravado em {caso.lau} "
            f"({document.validation_code or '—'}) diverge do manifesto "
            f"({caso.validation_code})"
        )

    achado.exam = db.get(SpirometryExam, document.spirometry_exam_id)
    if achado.exam is None:
        problema(f"o exame vinculado a {caso.lau} não existe")
    elif achado.exam.public_code != caso.esp:
        problema(
            f"{caso.lau} está vinculado a {achado.exam.public_code}, e o "
            f"manifesto espera {caso.esp}"
        )

    achado.origem = _versao_para_assinatura(db, document)
    if achado.origem is None:
        problema(
            f"{caso.lau} não tem versão final concluída corrente disponível"
        )
        return achado
    origem = achado.origem

    if origem.version_number != caso.versao_final:
        problema(
            f"a versão final corrente de {caso.lau} é a "
            f"{origem.version_number}, e o manifesto espera a "
            f"{caso.versao_final}"
        )

    achado.profile = _perfil_atribuido(db, document.id)
    if achado.profile is None:
        problema(
            f"{caso.lau} não tem médica responsável atribuída e ativa; sem "
            "ela não há a quem associar o documento assinado"
        )

    # ---- as guardas documentais da M25.29H, inteiras -------------------
    try:
        finais = _read_stored_version(origem).data
    except Exception as erro:  # ReportDomainError e falhas de armazenamento
        problema(
            "não foi possível ler o PDF final gravado para comparar "
            f"({type(erro).__name__}); sem ele nenhuma guarda pode ser "
            "aplicada honestamente"
        )
        return achado

    achado.guardas = avaliar(
        dados,
        final=finais,
        document_code=document.public_code,
        validation_code=document.validation_code,
        final_version_number=origem.version_number,
        origem_e_a_versao_final=(document.current_version_id == origem.id),
    )
    if not achado.guardas.aceito:
        problema(
            f"guardas documentais recusam o arquivo: {achado.guardas.motivo}"
        )

    # ---- idempotência e conflito ---------------------------------------
    mesmo_arquivo = db.execute(
        select(ExternalSignedDocument)
        .where(ExternalSignedDocument.report_document_id == document.id)
        .where(ExternalSignedDocument.sha256 == achado.sha256)
    ).scalar_one_or_none()
    if mesmo_arquivo is not None:
        if mesmo_arquivo.status == ASSINADO_RECUSADO:
            # Recusar e regularizar o MESMO arquivo são afirmações opostas
            # sobre os mesmos bytes. Escolher uma delas é decisão humana.
            problema(
                "estes bytes já constam como RECUSADOS neste laudo "
                f"({mesmo_arquivo.id}); regularizá-los agora contradiria a "
                "recusa registrada — leve o caso a uma decisão humana"
            )
        else:
            achado.ja_regularizado = mesmo_arquivo
        return achado

    vigente = _assinado_vigente(db, document.id)
    if vigente is not None:
        problema(
            f"{caso.lau} já tem um documento assinado vigente ({vigente.id}, "
            f"status {vigente.status}) com outro SHA-256; substituir um "
            "assinado vigente não é manutenção, é decisão humana"
        )

    return achado


def analisar(
    db: Session, caminhos: list[Path], *, casos: tuple[CasoEsperado, ...]
) -> list[Achado]:
    por_lau = {caso.lau: caso for caso in casos}
    return [analisar_arquivo(db, c, por_lau=por_lau) for c in sorted(caminhos)]


# ------------------------------------------------------------------ escrita


def _resolver_ator(db: Session, identificador: str) -> User:
    """A conta administrativa que responde por esta manutenção.

    Precisa ser administrativa e precisa ser explícita. Não se adivinha quem
    assina uma escrita histórica no lugar de ninguém — e a conta escolhida
    não pode ser a da médica, justamente porque o ponto inteiro é não lhe
    atribuir um upload que ela não fez.
    """

    alvo = identificador.strip()
    usuario = db.get(User, alvo) or db.execute(
        select(User).where(User.email == alvo.lower())
    ).scalar_one_or_none()
    if usuario is None:
        raise LookupError(f"conta não encontrada: {identificador}")
    if not usuario.ativo:
        raise LookupError(f"conta inativa: {usuario.email}")
    papeis = {papel.name for papel in usuario.roles}
    if not (papeis & set(ADMINISTRATIVE_ROLES)):
        raise LookupError(
            f"{usuario.email} não tem papel administrativo; a manutenção "
            "precisa de um ator administrativo identificável"
        )
    return usuario


def gravar(db: Session, achados: list[Achado], *, ator: User) -> int:
    """Grava os assinados aprovados. Só é chamada quando TODOS passaram."""

    agora = datetime.now(timezone.utc)
    a_gravar = [a for a in achados if a.a_gravar]
    lotes: dict[str, object] = {}
    gravados = 0

    for achado in a_gravar:
        document = achado.document
        origem = achado.origem
        profile = achado.profile
        caso = achado.caso

        lote = lotes.get(profile.id)
        if lote is None:
            # Um lote por médica, com a conta administrativa como criadora:
            # o lote é o registro de que uma DEVOLUÇÃO foi registrada, e quem
            # a registrou foi a manutenção, não a médica.
            lote = _abrir_lote(
                db,
                direction=BATCH_DIRECAO_UPLOAD,
                profile=profile,
                user=ator,
                document_count=sum(
                    1 for a in a_gravar if a.profile.id == profile.id
                ),
            )
            lotes[profile.id] = lote

        dados = achado.caminho.read_bytes()
        with report_publication_transaction(db) as publicacao:
            versao = _store_new_version(
                db,
                publication=publicacao,
                document=document,
                exam_id=document.spirometry_exam_id,
                kind=KIND_LAUDO_ASSINADO_EXTERNO,
                data=dados,
                created_by_user_id=ator.id,
            )
            assinado = ExternalSignedDocument(
                report_document_id=document.id,
                report_document_version_id=versao.id,
                source_version_id=origem.id,
                source_sha256=origem.sha256,
                batch_id=lote.id,
                # A médica responsável pelo laudo — o vínculo clínico, que é
                # verdadeiro. Ela continua sendo a autora do documento.
                physician_profile_id=profile.id,
                # Quem executou o registro — a manutenção. Estes dois campos
                # respondem perguntas diferentes, e é por isso que carregam
                # valores diferentes neste caso.
                uploader_user_id=ator.id,
                sha256=versao.sha256,
                size_bytes=versao.size_bytes,
                received_filename=achado.nome[:260],
                match_method=achado.identificacao.match_method,
                status=ASSINADO_ACEITO,
                received_at=agora,
                confirmed_at=agora,
                # validated_* ficam NULOS de propósito: ninguém conferiu a
                # assinatura fora daqui, e `validado_externamente` seria uma
                # afirmação sobre um testemunho que não existe.
            )
            db.add(assinado)
            db.flush()
            audit(
                db,
                "laudo_assinado_regularizado_historicamente",
                entidade="report_documents",
                entidade_id=document.id,
                user_id=ator.id,
                request_id=None,
                detalhes={
                    "report_code": document.public_code,
                    "exam_code": caso.esp,
                    "validation_code": caso.validation_code,
                    "signed_document_id": assinado.id,
                    "report_version_id": versao.id,
                    "version_number": origem.version_number,
                    "sha256": versao.sha256,
                    "document_sha256": origem.sha256,
                    "size_bytes": versao.size_bytes,
                    "match_method": achado.identificacao.match_method,
                    "status": ASSINADO_ACEITO,
                    "batch_id": lote.public_code,
                    "physician_profile_id": profile.id,
                    # O ponto inteiro da trilha deste script: quem agiu e sob
                    # que título. A médica não executou upload nenhum agora.
                    "contexto": "manutencao_administrativa_historica",
                    "modo": "regularizacao_m25_30",
                    "motivo": "assinado_recebido_fora_do_fluxo_regularizado",
                    "aceito": True,
                    **achado.guardas.para_auditoria(),
                },
            )
            publicacao.commit()
        gravados += 1

    return gravados


# ---------------------------------------------------------------- impressão


def _linha(rotulo: str, valor) -> None:
    print(f"    {rotulo:.<38} {valor if valor is not None else '—'}")


def _imprimir(achado: Achado) -> None:
    print(f"\n  --- {achado.nome}")
    _linha("sha256 do arquivo", achado.sha256)
    _linha("tamanho (bytes)", achado.tamanho)
    _linha("identificado como", achado.identificacao.lau)
    _linha("identificado por", achado.identificacao.match_method)
    _linha("código de verificação lido", achado.identificacao.validation_code)
    _linha("versão citada no carimbo", achado.identificacao.version_number)
    if achado.caso is not None:
        _linha("manifesto: exame", achado.caso.esp)
        _linha("manifesto: versão final", achado.caso.versao_final)
    if achado.origem is not None:
        _linha("versão final corrente", achado.origem.version_number)
        _linha("source_version_id", achado.origem.id)
        _linha("source_sha256", achado.origem.sha256)
    if achado.profile is not None:
        _linha("physician_profile_id", achado.profile.id)
    if achado.guardas is not None:
        print("    guardas documentais M25.29H:")
        for chave, valor in achado.guardas.para_auditoria().items():
            _linha(f"  {chave}", valor)
        _linha("  veredito", "ACEITO" if achado.guardas.aceito else "RECUSADO")
        print(
            "      (guarda DOCUMENTAL — não é validação ICP-Brasil, não "
            "confere\n       cadeia, certificado, revogação nem digest)"
        )
    if achado.ja_regularizado is not None:
        _linha("já regularizado", achado.ja_regularizado.id)
        _linha("status existente", achado.ja_regularizado.status)
    for divergencia in achado.divergencias:
        print(f"    !! {divergencia}")


def executar(
    db: Session,
    *,
    caminhos: list[Path],
    casos: tuple[CasoEsperado, ...],
    ator_id: str,
    aplicar: bool,
) -> int:
    print("\n=== M25.30 — REGULARIZAÇÃO DE ASSINADOS HISTÓRICOS ===")
    print(
        f"  modo...: {'APLICANDO ESCRITA' if aplicar else 'DRY-RUN (não escreve)'}"
    )
    print(f"  arquivos: {len(caminhos)}   manifesto: {len(casos)} caso(s)")

    try:
        ator = _resolver_ator(db, ator_id)
    except LookupError as erro:
        print(f"\n  PARE: {erro}\n")
        return 2
    papeis = sorted(papel.name for papel in ator.roles)
    print(f"  ator...: {ator.email}  papéis={','.join(papeis)}")
    if ROLE_MEDICO in papeis:
        print(
            "\n  PARE: a conta informada tem papel médico. A regularização "
            "existe\n  justamente para NÃO atribuir à médica um upload que "
            "ela não fez.\n  Use uma conta administrativa sem papel clínico."
            "\n"
        )
        return 2

    achados = analisar(db, caminhos, casos=casos)
    for achado in achados:
        _imprimir(achado)

    esperados = {caso.lau for caso in casos}
    identificados = [
        a.caso.lau for a in achados if a.caso is not None and a.ok
    ]
    faltando = sorted(esperados - set(identificados))
    repetidos = sorted(
        {lau for lau in identificados if identificados.count(lau) > 1}
    )

    divergentes = [a for a in achados if not a.ok]
    print("\n  --- resumo ---")
    _linha("arquivos analisados", len(achados))
    _linha("aprovados", len(achados) - len(divergentes))
    _linha("divergentes", len(divergentes))
    _linha("já regularizados", sum(1 for a in achados if a.ja_regularizado))
    _linha("a gravar", sum(1 for a in achados if a.a_gravar))

    if divergentes:
        print(
            "\n  PARE: há divergência. NADA foi escrito — a regularização é "
            "tudo\n  ou nada, e forçar um aceite é exatamente o erro que as "
            "guardas\n  documentais existem para impedir.\n"
        )
        return 4
    if faltando:
        print(
            f"\n  PARE: o manifesto espera {', '.join(faltando)} e nenhum "
            "arquivo\n  aprovado corresponde a esse(s) laudo(s). NADA foi "
            "escrito.\n"
        )
        return 4
    if repetidos:
        print(
            f"\n  PARE: {', '.join(repetidos)} apareceu em mais de um "
            "arquivo.\n  NADA foi escrito.\n"
        )
        return 4

    if not aplicar:
        print("\n  --- o que MUDA com --apply ---")
        for achado in achados:
            if achado.a_gravar:
                print(
                    f"  {achado.caso.lau}: + 1 versão "
                    f"{KIND_LAUDO_ASSINADO_EXTERNO} + 1 "
                    f"external_signed_documents({ASSINADO_ACEITO}) + 1 "
                    "audit_log"
                )
        print("\n  --- o que NÃO muda ---")
        print("  exame, laudo, status clínico, current_version_id, versões")
        print("  existentes (nenhuma apagada), assinados históricos recusados,")
        print("  audit_logs anteriores, entrega (delivered_at continua nulo),")
        print("  validação externa (validated_* continuam nulos),")
        print("  qualified_signature (continua FALSO)")
        print("\n  DRY-RUN — nada foi escrito. Use --apply para aplicar.\n")
        return 0

    gravados = gravar(db, achados, ator=ator)
    print(f"\n  APLICADO. {gravados} documento(s) assinado(s) regularizado(s).")
    print("  Nada foi apagado. Nenhum documento foi marcado como entregue.\n")
    return 0


def _coletar(args) -> list[Path]:
    caminhos: list[Path] = [Path(a).expanduser() for a in (args.arquivo or [])]
    if args.diretorio:
        raiz = Path(args.diretorio).expanduser()
        caminhos.extend(sorted(p for p in raiz.glob("*.pdf") if p.is_file()))
        caminhos.extend(sorted(p for p in raiz.glob("*.PDF") if p.is_file()))
    return caminhos


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arquivo", action="append", help="PDF assinado (repetível)"
    )
    parser.add_argument("--diretorio", help="diretório com os PDFs assinados")
    parser.add_argument(
        "--ator",
        required=True,
        help="conta administrativa responsável pela manutenção (id ou e-mail)",
    )
    parser.add_argument(
        "--manifesto",
        help="JSON com os casos esperados; sem isto usa o manifesto versionado",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="escreve de verdade; sem isto roda em dry-run",
    )
    args = parser.parse_args()

    caminhos = _coletar(args)
    if not caminhos:
        print("\n  PARE: nenhum PDF informado (--arquivo ou --diretorio).\n")
        return 2
    ausentes = [c for c in caminhos if not c.is_file()]
    if ausentes:
        for c in ausentes:
            print(f"\n  PARE: arquivo inexistente: {c}")
        print()
        return 2

    casos = (
        carregar_manifesto(Path(args.manifesto).expanduser())
        if args.manifesto
        else CASOS_M25_30
    )

    settings = get_settings()
    engine = build_engine(settings.database_url)
    with Session(engine) as db:
        return executar(
            db,
            caminhos=caminhos,
            casos=casos,
            ator_id=args.ator,
            aplicar=args.apply,
        )


if __name__ == "__main__":
    raise SystemExit(main())
