"""Atendimento único (M20) — paciente + tipo, em UMA transação atômica.

Substitui as três criações separadas (paciente / espirometria / consulta)
por um fluxo canônico. Regras de negócio impostas aqui:

- o paciente é entidade estável: o atendimento carrega a origem, a pessoa
  nunca é classificada permanentemente como "Pastore" ou "SoproLife";
- Pastore só se aplica ao tipo `espirometria_pastore`, e nele a unidade
  operacional é obrigatória;
- consulta SoproLife: a receita BRUTA pertence à SoproLife e o repasse ao
  médico é um lançamento separado — um repasse de 100% zera a margem, não
  a titularidade da receita;
- nenhum valor é inferido: o financeiro só nasce com valor explícito ou
  com uma regra de repasse determinística sobre um bruto explícito;
- tudo ou nada: qualquer falha desfaz paciente/exame/consulta/lançamentos.
"""

from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..dates import add_months, parse_incomplete_date
from ..db import get_db
from ..domain import ensure_sem_pcmso
from ..finance_categories import (
    CATEGORIA_CONSULTA,
    CATEGORIA_ESPIROMETRIA,
    CATEGORIA_REPASSE_MEDICO,
)
from ..ids import allocate_public_code
from ..models import (
    Consultation,
    FinancialEntry,
    Partner,
    PartnerUnit,
    Person,
    SpirometryExam,
    User,
)
from ..normalize import normalize_name, normalize_phone
from ..schemas import (
    TIPO_PASTORE,
    TIPOS_COM_CONSULTA,
    TIPOS_COM_ESPIROMETRIA,
    AtendimentoCreate,
    AtendimentoNovoPacienteCreate,
)
from ..security import ROLE_LEITURA, ROLE_OPERACIONAL, require_role
from ..serializers import ser_consultation, ser_exam
from ..services.followup import (
    due_after_attendance,
    sync_followup_for_origin,
)
from ..services.cpf import CPFInvalidoError
from ..services.identity import find_person_candidates
from ..services.idempotency import idempotent_create
from ..services.person_registration import build_person, cadastro_pendencias
from ..services.pastore import canonical_pastore, pastore_unit
from ..services.report_origin import (
    OriginDerivationError,
    validar_combinacao_no_cadastro,
)

router = APIRouter(tags=["atendimentos"])

CENTAVOS = Decimal("0.01")


def _apply_date(obj, prefix: str, raw: str | None, rotulo: str | None = None) -> None:
    """Grava a data normalizada — ou recusa o atendimento inteiro.

    M25.26: antes desta missão, um texto que o parser não entendesse (o caso
    real: ``12082026``, digitado sem barras) NÃO levantava erro — devolvia
    ``value=None`` e o exame era criado com data em branco, ``precisao =
    desconhecida`` e follow-up desligado (``nao_aplicavel``). O operador via
    "criado com sucesso" e só semanas depois, na emissão do laudo, o exame
    aparecia como pendente por falta de data de realização.

    Aceitar a digitação é trabalho da máscara na tela; o que NÃO pode
    acontecer é o servidor transformar silenciosamente um dado ilegível em
    ausência de dado. Campo vazio continua sendo ausência legítima — só o
    texto preenchido e incompreensível é recusado.
    """

    nd = parse_incomplete_date(raw)
    if (raw or "").strip() and nd.value is None:
        nome = rotulo or prefix.replace("_", " ")
        raise _erro(
            "data_ilegivel",
            f"{nome}: não foi possível ler a data informada. "
            "Use DD/MM/AAAA, MM/AAAA ou AAAA.",
        )
    setattr(obj, prefix, nd.value)
    setattr(obj, f"{prefix}_original", nd.original or None)
    setattr(obj, f"{prefix}_precisao", nd.precision)
    setattr(obj, f"{prefix}_dia_assumido", nd.day_assumed)


def _erro(codigo: str, mensagem: str, status: int = 422) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"codigo": codigo, "mensagem": mensagem}
    )


def _entry(
    db: Session,
    *,
    tipo: str,
    categoria: str,
    descricao: str,
    valor: Decimal,
    status: str,
    data_competencia: str | None,
    data_recebimento,
    forma_pagamento: str | None,
    origem_preco: str | None,
    spirometry_exam_id: str | None = None,
    consultation_id: str | None = None,
) -> FinancialEntry:
    entry = FinancialEntry(
        public_code=allocate_public_code(db, "financial_entries"),
        tipo=tipo,
        categoria=categoria,
        descricao=descricao,
        valor=valor,
        status=status,
        data_recebimento=data_recebimento,
        forma_pagamento=forma_pagamento,
        origem_preco=origem_preco,
        spirometry_exam_id=spirometry_exam_id,
        consultation_id=consultation_id,
    )
    _apply_date(entry, "data_competencia", data_competencia, "Competência do lançamento")
    db.add(entry)
    db.flush()
    return entry


# --------------------------------------------------- configuração do fluxo

# M25.26 — modalidade e local pararam de competir.
#
# A tela oferecia MODALIDADE (residencial / cowork / clínica parceira) e, ao
# lado, um "Local / unidade" com OUTRA lista de categorias equivalentes
# (Domiciliar / Clínica / Empresa / Parceiro / Outro). Duas taxonomias para a
# mesma pergunta permitem contradição — "residencial" + "Clínica" era aceito.
#
# A auditoria dos dados reais resolveu a ambiguidade: nenhum exame em
# produção usa `local_atendimento` como categoria. Os valores gravados são
# NOMES DE LUGAR ("Pastore Ipanema", "Coworking"), e `report_locations` já
# documenta o campo como "texto operacional livre... complemento do rótulo,
# nunca endereço estruturado". Então:
#
#   MODALIDADE     = a natureza do atendimento (campo fechado, decide a
#                    origem do laudo via MODALIDADE_PARA_ORIGEM).
#   LOCAL/UNIDADE  = onde especificamente aconteceu (texto livre, sugerido
#                    de acordo com a modalidade, nunca uma segunda categoria).
#
# `clinica_parceira` NÃO é oferecida no fluxo SoproLife de propósito: o
# schema proíbe parceiro/unidade fora do tipo Pastore, então escolhê-la
# criaria um exame que a emissão do laudo recusa por falta de unidade. Existe
# um registro assim em produção, nascido justamente dessa armadilha.
MODALIDADES_SOPROLIFE = [
    {
        "valor": "residencial",
        "rotulo": "Domiciliar (no endereço do paciente)",
        "ajuda": "Exame realizado na casa do paciente.",
        "local_rotulo": "Local do atendimento",
        "local_padrao": "Domicílio do paciente",
        "local_sugestoes": ["Domicílio do paciente"],
        "local_obrigatorio": False,
        "exige_unidade": False,
    },
    {
        "valor": "cowork",
        "rotulo": "Cowork / espaço SoproLife",
        "ajuda": "Exame realizado em espaço de atendimento da SoproLife.",
        "local_rotulo": "Nome do espaço",
        "local_padrao": "",
        "local_sugestoes": ["Coworking"],
        "local_obrigatorio": True,
        "exige_unidade": False,
    },
]

MODALIDADE_INDISPONIVEL_SOPROLIFE = {
    "valor": "clinica_parceira",
    "rotulo": "Clínica parceira",
    "motivo": (
        "Exame em clínica parceira é lançado pelo tipo 'Espirometria "
        "Pastore', que já vincula o parceiro e a unidade."
    ),
}


@router.get("/atendimentos/configuracao")
def attendance_configuration(
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Fonte única do que o formulário de atendimento oferece.

    Preço e vocabulário de modalidade/local vivem no servidor para que a tela
    não carregue número mágico nem uma segunda lista que possa divergir desta.
    """

    settings = get_settings()
    return {
        "espirometria_soprolife": {
            "valor_padrao": str(settings.espirometria_soprolife_valor_padrao),
            "valor_padrao_editavel": True,
            "modalidades": MODALIDADES_SOPROLIFE,
            "modalidades_indisponiveis": [MODALIDADE_INDISPONIVEL_SOPROLIFE],
        }
    }


@router.post("/atendimentos", status_code=201)
def create_attendance(
    payload: AtendimentoCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    try:
        def resolver(_db: Session) -> Person:
            person = _db.get(Person, payload.person_id)
            if person is None:
                raise HTTPException(status_code=404, detail="Pessoa não encontrada.")
            return person

        return _create_attendance(payload, resolver, request, db, user)
    except Exception:
        # Atomicidade explícita: nenhum paciente, exame, consulta ou
        # lançamento parcial sobrevive a uma falha em qualquer etapa.
        db.rollback()
        raise


@router.post("/atendimentos/novo-paciente", status_code=201)
def create_attendance_with_new_person(
    payload: AtendimentoNovoPacienteCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Paciente novo + atendimento numa transação só (M25.26).

    Qualquer falha adiante — data ilegível, modalidade contraditória, repasse
    maior que o bruto — desfaz também o paciente. É a diferença entre "o
    exame não foi lançado" e "o exame não foi lançado E ficou um paciente
    fantasma no cadastro para alguém descobrir depois".
    """

    try:
        def resolver(_db: Session) -> Person:
            candidatos = _candidatos_de_identidade(_db, payload.pessoa)
            if candidatos and not payload.confirmar_duplicado:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "codigo": "possivel_duplicado",
                        "mensagem": (
                            "Já existe cadastro parecido. Use o paciente "
                            "existente ou confirme a criação."
                        ),
                        "candidatos": candidatos,
                    },
                )
            return build_person(
                _db,
                nome_completo=payload.pessoa.nome_completo,
                cpf=payload.pessoa.cpf,
                data_nascimento=payload.pessoa.data_nascimento,
                sexo=payload.pessoa.sexo,
                observacao=payload.pessoa.observacao,
                contatos=payload.pessoa.contatos,
                consentimento_whatsapp=payload.pessoa.consentimento_whatsapp,
                registrado_por=user.id,
            )

        resultado = _create_attendance(payload, resolver, request, db, user)
        resultado["pessoa_criada"] = True
        return resultado
    except HTTPException:
        db.rollback()
        raise
    except CPFInvalidoError as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={"codigo": exc.codigo, "mensagem": exc.mensagem},
        ) from None
    except Exception:
        db.rollback()
        raise


def _candidatos_de_identidade(db: Session, pessoa) -> list[dict]:
    """Candidatos a duplicado — aviso para decisão humana, nunca fusão."""

    telefones = [
        t for t in (
            normalize_phone(c.valor)
            for c in pessoa.contatos
            if c.tipo in ("whatsapp", "telefone")
        ) if t
    ]
    encontrados = find_person_candidates(
        db, normalize_name(pessoa.nome_completo), telefones
    )
    return [
        {
            "id": p.id,
            "public_code": p.public_code,
            "nome_completo": p.nome_completo,
            "data_nascimento": p.data_nascimento.isoformat()
            if p.data_nascimento else None,
            "motivo": motivo,
        }
        for p, motivo in encontrados
    ]


def _create_attendance(
    payload, resolver_pessoa, request: Request, db: Session, user: User
):
    bloco_texto: dict = {}
    for bloco in (payload.espirometria, payload.consulta):
        if bloco is not None:
            bloco_texto.update(bloco.model_dump(exclude_none=True))
    try:
        ensure_sem_pcmso(bloco_texto, "atendimentos.create")
    except HTTPException as exc:
        audit(db, "pcmso.rejeitado", user_id=user.id,
              request_id=request.state.request_id,
              detalhes=exc.detail if isinstance(exc.detail, dict) else None)
        db.commit()
        raise

    person = resolver_pessoa(db)

    resultado: dict = {
        "tipo": payload.tipo,
        "person_id": person.id,
        "person_public_code": person.public_code,
        "espirometria": None,
        "consulta": None,
        "lancamentos": [],
        # M25.26 — o que falta no cadastro viaja JUNTO com o sucesso.
        #
        # Não bloqueia: o exame aconteceu de verdade e recusá-lo por falta de
        # CPF tiraria da operação um dado real. Mas some da tela é pior
        # ainda — era assim que um paciente sem CPF só revelava o problema
        # semanas depois, quando a médica tentava emitir o laudo.
        "cadastro_pendencias": cadastro_pendencias(person),
    }

    exam = None
    if payload.tipo in TIPOS_COM_ESPIROMETRIA:
        exam = _criar_exame(db, person, payload)
        resultado["espirometria"] = ser_exam(exam)

    consultation = None
    if payload.tipo in TIPOS_COM_CONSULTA:
        consultation = _criar_consulta(db, person, payload)
        resultado["consulta"] = ser_consultation(consultation)

    lancamentos = _criar_financeiro(db, payload, exam, consultation)
    resultado["lancamentos"] = lancamentos

    # Follow-ups sincronizados na MESMA transação.
    if exam is not None:
        resultado["espirometria"]["followup"] = _followup_exame(db, person, exam, payload)
    if consultation is not None:
        resultado["consulta"]["followup"] = _followup_consulta(
            db, person, consultation, payload
        )

    audit(db, "atendimento.criado", "people", person.id, user.id,
          request.state.request_id,
          {"tipo": payload.tipo,
           "public_code": person.public_code,
           "campos": [c for c in (
               exam.public_code if exam else None,
               consultation.public_code if consultation else None,
           ) if c]})
    db.commit()
    return resultado


# ------------------------------------------------------------------ blocos

def _criar_exame(db: Session, person: Person, payload: AtendimentoCreate) -> SpirometryExam:
    bloco = payload.espirometria
    modalidade = bloco.modalidade
    local_atendimento = bloco.local_atendimento
    partner_id = bloco.partner_id
    partner_unit_id = bloco.partner_unit_id
    origem = bloco.origem
    if payload.tipo == TIPO_PASTORE:
        partner = canonical_pastore(db)
        if bloco.partner_id != partner.id:
            raise _erro(
                "parceiro_pastore_invalido",
                "Espirometria Pastore exige o parceiro Pastore canônico.",
            )
        unit = pastore_unit(db, bloco.partner_unit_id, partner)
        # Estes campos são domínio derivado, não escolhas do cliente.
        modalidade = "clinica_parceira"
        local_atendimento = unit.nome
        partner_id = partner.id
        partner_unit_id = unit.id
        origem = partner.nome

    # M25.26 — a coerência modalidade × unidade é conferida AQUI, com as
    # mesmas regras que a emissão do laudo aplica (M25.17). Antes, um exame
    # contraditório era aceito no cadastro e só travava semanas depois, na
    # hora em que a médica tentava emitir — e aí o operador que digitou já
    # não estava por perto para dizer onde o exame realmente aconteceu.
    try:
        validar_combinacao_no_cadastro(modalidade, bool(partner_unit_id))
    except OriginDerivationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "codigo": exc.codigo,
                "mensagem": exc.mensagem,
                "como_corrigir": exc.como_corrigir,
            },
        ) from None

    def factory(key, fingerprint):
        exam = SpirometryExam(
            public_code=allocate_public_code(db, "spirometry_exams"),
            person_id=person.id,
            modalidade=modalidade,
            local_atendimento=local_atendimento,
            partner_id=partner_id,
            partner_unit_id=partner_unit_id,
            status=bloco.status,
            broncodilatador=bloco.broncodilatador,
            origem=origem,
            responsavel=bloco.responsavel,
            idempotency_key=key,
            idempotency_fingerprint=fingerprint,
            observacao=bloco.observacao,
        )
        _apply_date(exam, "data_exame", bloco.data_exame, "Data do exame")
        db.add(exam)
        db.flush()
        return exam

    key = f"{payload.idempotency_key}:esp" if payload.idempotency_key else None
    exam, ja_existia = idempotent_create(
        db, SpirometryExam, key, payload.model_dump(mode="json"), factory
    )
    if ja_existia:
        raise _erro(
            "atendimento_repetido",
            "Este atendimento já foi criado (chave de idempotência repetida).",
            status=409,
        )
    return exam


def _criar_consulta(
    db: Session, person: Person, payload: AtendimentoCreate
) -> Consultation:
    bloco = payload.consulta

    def factory(key, fingerprint):
        consultation = Consultation(
            public_code=allocate_public_code(db, "consultations"),
            person_id=person.id,
            modalidade=bloco.modalidade,
            profissional=bloco.profissional,
            status=bloco.status,
            origem=bloco.origem,
            responsavel=bloco.responsavel,
            idempotency_key=key,
            idempotency_fingerprint=fingerprint,
            observacao=bloco.observacao,
        )
        _apply_date(consultation, "data_consulta", bloco.data_consulta, "Data da consulta")
        db.add(consultation)
        db.flush()
        return consultation

    key = f"{payload.idempotency_key}:con" if payload.idempotency_key else None
    consultation, ja_existia = idempotent_create(
        db, Consultation, key, payload.model_dump(mode="json"), factory
    )
    if ja_existia:
        raise _erro(
            "atendimento_repetido",
            "Este atendimento já foi criado (chave de idempotência repetida).",
            status=409,
        )
    return consultation


def _criar_financeiro(
    db: Session,
    payload: AtendimentoCreate,
    exam: SpirometryExam | None,
    consultation: Consultation | None,
) -> list[dict]:
    fin = payload.financeiro
    if payload.tipo == TIPO_PASTORE:
        if fin is not None:
            raise _erro(
                "pagamento_direto_pastore_proibido",
                "Espirometria Pastore não aceita pagamento direto do paciente.",
            )
        return []
    if fin is None:
        return []
    lancamentos: list[dict] = []

    if fin.espirometria is not None:
        if exam is None:
            raise _erro("financeiro_sem_exame", "Financeiro de espirometria sem exame.")
        bloco = fin.espirometria
        entry = _entry(
            db,
            tipo="receita",
            categoria=CATEGORIA_ESPIROMETRIA,
            descricao=f"Espirometria {exam.public_code}",
            valor=bloco.valor,
            status=bloco.status,
            data_competencia=bloco.data_competencia,
            data_recebimento=bloco.data_recebimento,
            forma_pagamento=bloco.forma_pagamento,
            origem_preco=bloco.origem_preco,
            spirometry_exam_id=exam.id,
        )
        lancamentos.append({
            "public_code": entry.public_code,
            "componente": "espirometria",
            "tipo": entry.tipo,
            "valor": str(entry.valor),
        })

    if fin.consulta is not None:
        if consultation is None:
            raise _erro("financeiro_sem_consulta", "Financeiro de consulta sem consulta.")
        bloco = fin.consulta
        # 1. Receita BRUTA da consulta — sempre da SoproLife.
        bruto = _entry(
            db,
            tipo="receita",
            categoria=CATEGORIA_CONSULTA,
            descricao=f"Receita bruta da consulta {consultation.public_code}",
            valor=bloco.valor_bruto,
            status=bloco.status,
            data_competencia=bloco.data_competencia,
            data_recebimento=bloco.data_recebimento,
            forma_pagamento=bloco.forma_pagamento,
            origem_preco=bloco.origem_preco,
            consultation_id=consultation.id,
        )
        lancamentos.append({
            "public_code": bruto.public_code,
            "componente": "consulta_bruto",
            "tipo": bruto.tipo,
            "valor": str(bruto.valor),
        })
        # 2. Repasse ao médico — obrigação SEPARADA, nunca abatida do bruto.
        #    Só nasce com valor explícito ou percentual determinístico.
        repasse = bloco.repasse_medico_valor
        if repasse is None and bloco.repasse_medico_percentual is not None:
            repasse = (
                bloco.valor_bruto * bloco.repasse_medico_percentual / Decimal("100")
            ).quantize(CENTAVOS, rounding=ROUND_HALF_UP)
        if repasse is not None and repasse > 0:
            if repasse > bloco.valor_bruto:
                raise _erro(
                    "repasse_maior_que_bruto",
                    "Repasse ao médico não pode exceder a receita bruta.",
                )
            entry = _entry(
                db,
                tipo="repasse",
                categoria=CATEGORIA_REPASSE_MEDICO,
                descricao=f"Repasse ao médico da consulta {consultation.public_code}",
                valor=repasse,
                status=bloco.repasse_medico_status,
                data_competencia=bloco.data_competencia,
                data_recebimento=None,
                forma_pagamento=None,
                origem_preco=None,
                consultation_id=consultation.id,
            )
            lancamentos.append({
                "public_code": entry.public_code,
                "componente": "consulta_repasse_medico",
                "tipo": entry.tipo,
                "valor": str(entry.valor),
            })
    return lancamentos


# --------------------------------------------------------------- follow-ups

def _followup_exame(
    db: Session, person: Person, exam: SpirometryExam, payload: AtendimentoCreate
) -> dict:
    bloco = payload.espirometria
    ativo = exam.status in ("Realizado", "Laudo Liberado") and exam.data_exame is not None
    due = None
    if bloco.proximo_followup is not None:
        due = bloco.proximo_followup
        ativo = True
    elif ativo:
        due = due_after_attendance(exam.data_exame)
    fup, motivo = sync_followup_for_origin(
        db, person, "pos_exame", "spirometry_exams", exam.id,
        due=due, active=ativo, responsavel=exam.responsavel,
        partner_id=exam.partner_id,
    )
    if fup is not None and bloco.proximo_followup is not None:
        fup.due_date = bloco.proximo_followup
        fup.due_date_manual = True
        db.flush()
    return {"motivo": motivo, "id": fup.id if fup else None}


def _followup_consulta(
    db: Session, person: Person, consultation: Consultation, payload: AtendimentoCreate
) -> dict:
    """Retorno de consulta NUNCA é assumido — só existe se foi escolhido."""
    bloco = payload.consulta
    if bloco.retorno == "sem_retorno" or consultation.data_consulta is None:
        return {"motivo": "sem_retorno_programado", "id": None}
    if bloco.retorno == "data":
        due = bloco.retorno_data
    else:
        due = add_months(consultation.data_consulta, bloco.retorno_intervalo_meses)
    fup, motivo = sync_followup_for_origin(
        db, person, "pos_consulta", "consultations", consultation.id,
        due=due, active=True, responsavel=consultation.responsavel,
    )
    if fup is not None:
        fup.due_date = due
        fup.due_date_manual = True
        db.flush()
    return {"motivo": motivo, "id": fup.id if fup else None}
