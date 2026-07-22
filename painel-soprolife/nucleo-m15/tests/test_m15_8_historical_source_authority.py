"""M15.8 — cohort histórico CRM Espirometria como fonte de autoridade.

Cobre, com dados 100% sintéticos:

1. o exame do coorte é autoridade de identidade quando a fonte não tem
   CRM Pacientes correspondente (criar_pessoa a partir do exame órfão);
2/4. projeção CRM Pacientes/CRM Espirometria que compartilha telefone com
   uma pessoa já autoritativa resolve como candidato único e vira UMA só
   pessoa via vincular_candidato — nenhuma pessoa duplicada, nenhuma fusão
   automática;
3. CRM Consultas só vincula pessoa já resolvida, nunca cria uma nova;
5. substituição estruturada menor/responsável (guardian_override)
   operador-autorizada quando a fonte não separa nome/telefone do menor e
   do responsável em colunas próprias (um único campo de nome combinando
   os dois): menor e responsável nascem como pessoas distintas, vínculo
   legal explícito, exame nunca vai para o responsável, contato do
   responsável nunca herda dado do menor;
6. referências de custo Pastore nunca produzem lançamento financeiro; duas
   agendas da mesma unidade física nunca viram duas unidades;
7. FIN-0001/0002/0003 permanecem excluídos da reconstrução financeira;
8. candidato ambíguo (telefone com mais de uma pessoa) continua bloqueado
   fail-closed mesmo com decisão de vínculo registrada.

Nenhum dado real de paciente é usado; nomes/telefones são sintéticos.
"""

from datetime import date

import pytest

from app.migration.executor import build_execution_plan
from app.migration.multisheet import (
    GUARDIAN_RELATIONSHIP_TYPE_ALIASES,
    MultiSheetError,
    decide_multi_sheet_review,
    multi_sheet_review_queue,
    run_multi_sheet_dry_run,
    validate_guardian_override,
)
from app.migration.operational_completion import (
    ADMIN_TOTAL_CENTS,
    EXPECTED_EXAMINATIONS,
    FIXED_ENTRY_CENTS,
    REMAINING_ENTRY_CENTS,
    REQUIRED_LEGACY_EXCLUSIONS,
    OperationalCompletionError,
    _fingerprint,
    build_financial_reconstruction,
    validate_financial_reconstruction,
)
from tests._multisheet_fixtures import synthetic_row, write_raw_envelope

PHONE_COHORT = "(21) 0000-9501"
PHONE_MULTI_A = "(21) 0000-9601"


def _decide_first_pending(db, batch_id, category, decision, user_id,
                          guardian_override=None):
    queue = multi_sheet_review_queue(db, batch_id)
    item = next(value for value in queue["items"]
                if value["category"] == category and value["status"] == "pendente")
    decide_multi_sheet_review(
        db, batch_id, item["private_reference_token"], decision,
        item["mapping_version"], user_id,
        guardian_override=guardian_override,
    )
    return item


# ---------------------------------------------------------------- item 2 (nota)


def _cohort_exam_and_followup_same_person_sheets():
    phone = "(21) 0000-9801"
    return [
        {
            "kind": "crm_espirometria",
            "alias": "Coorte Espirometria Sintetico Followup",
            "rows": [synthetic_row(
                "crm_espirometria", exame_id="ESP-SYN-FOLLOWUP-COHORT",
                primeiro_nome="Pessoa Coorte Com Followup Sintetico",
                telefone=phone, data_exame="03/07/2026",
            )],
        },
        {
            "kind": "followup_whatsapp",
            "alias": "Followup Coorte Sintetico",
            "rows": [synthetic_row(
                "followup_whatsapp", followup_id="FW-SYN-FOLLOWUP-COHORT",
                primeiro_nome="Pessoa Coorte Com Followup Sintetico",
                telefone=phone, data_prevista="20/07/2026",
            )],
        },
    ]


def test_cohort_followup_projection_without_own_crm_pacientes_requires_decision(
    db, users, tmp_path,
):
    """Nota de arquitetura (M15.8, achado real): quando um membro do coorte
    NÃO tem CRM Pacientes próprio (caso comum: 11 dos 12), tanto o exame
    quanto a linha de Follow-up WhatsApp da MESMA pessoa chegam como
    registros órfãos independentes na fila de revisão — o staging nunca
    indexa o telefone de uma linha de exame/follow-up pendente como
    candidato (só CRM Pacientes indexa). Isso é fail-closed (nunca funde
    automaticamente, nunca ignora em silêncio), mas tem uma consequência
    operacional importante: se um revisor decidir "criar_pessoa" nas DUAS
    linhas de forma independente, hoje o sistema cria DUAS pessoas
    distintas para o mesmo indivíduo (risco de duplicidade dependente de
    disciplina do operador). O caminho seguro e JÁ disponível hoje sem
    nenhuma mudança de código é excluir a linha de follow-up redundante
    (decisão "excluido", sempre permitida para registro_orfao) e deixar
    que o follow-up operacional real seja criado depois via API, contra a
    pessoa canônica já existente. Ampliar o vocabulário fechado de
    vincular_candidato para cobrir órfãos indexados no mesmo lote é uma
    melhoria de arquitetura maior, fora do escopo aditivo mínimo desta
    etapa — fica registrada aqui como constatação, não como correção."""
    sheets = _cohort_exam_and_followup_same_person_sheets()
    envelope = write_raw_envelope(tmp_path, sheets)
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    queue = multi_sheet_review_queue(db, dry["batch_id"])
    exame_item = next(v for v in queue["items"] if v["category"] == "exame_orfao")
    followup_item = next(
        v for v in queue["items"] if v["category"] == "registro_orfao"
    )
    # nem exame nem follow-up resolvem sozinhos: os dois exigem decisão.
    assert exame_item["requires_executable_decision"] is True
    assert followup_item["requires_executable_decision"] is True
    # "vincular_candidato" não é uma opção para nenhum dos dois hoje —
    # confirma que o staging NUNCA indexa candidato a partir de exame ou
    # de follow-up pendente (só CRM Pacientes gera candidato indexado).
    assert "vincular_candidato" not in exame_item["executable_decisions"]
    assert "vincular_candidato" not in followup_item["executable_decisions"]

    # caminho seguro de HOJE: exame cria a pessoa canônica; follow-up
    # redundante é excluído (não recriado) — zero duplicidade.
    decide_multi_sheet_review(
        db, dry["batch_id"], exame_item["private_reference_token"],
        "criar_pessoa", exame_item["mapping_version"], users["admin"].id,
    )
    decide_multi_sheet_review(
        db, dry["batch_id"], followup_item["private_reference_token"],
        "excluido", followup_item["mapping_version"], users["admin"].id,
    )
    plan_safe = build_execution_plan(
        db, envelope, dry["batch_id"], base_dir=tmp_path
    )
    assert plan_safe.bloqueios == []
    assert plan_safe.manifesto()["people"] == 1
    assert plan_safe.manifesto().get("followups", 0) == 0


# ---------------------------------------------------------------- item 1/3


def test_cohort_exam_without_crm_pacientes_is_sole_identity_authority(
    db, users, tmp_path,
):
    """Sem linha CRM Pacientes correspondente, o exame do coorte é a ÚNICA
    fonte de identidade: decisão criar_pessoa cria exatamente uma pessoa."""
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "crm_espirometria",
        "alias": "Coorte Espirometria Sintetico",
        "rows": [synthetic_row(
            "crm_espirometria",
            exame_id="ESP-SYN-COHORT-001",
            primeiro_nome="Paciente Coorte Sintetico",
            telefone=PHONE_COHORT,
            data_exame="08/07/2026",
        )],
    }])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    _decide_first_pending(
        db, dry["batch_id"], "exame_orfao", "criar_pessoa", users["admin"].id
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan.bloqueios == []
    assert plan.manifesto() == {
        "people": 1, "person_contacts": 1, "spirometry_exams": 1,
    }
    pessoa = next(op for op in plan.ops if op.entidade == "people")
    exame = next(op for op in plan.ops if op.entidade == "spirometry_exams")
    assert exame.refs["person_id"] == pessoa.chave


def test_crm_consultas_only_links_never_creates_person(db, users, tmp_path):
    """CRM Consultas com paciente_id explícito só vincula; sem CRM Pacientes
    resolvido, a linha fica pendente — NUNCA cria uma pessoa por si só."""
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "crm_consultas",
        "alias": "Consultas Sinteticas",
        "rows": [synthetic_row(
            "crm_consultas",
            consulta_id="CONS-SYN-001",
            primeiro_nome="Paciente Consulta Sintetico",
            paciente_id="PAC-INEXISTENTE-SYN",
            data_consulta="09/07/2026",
        )],
    }])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan.manifesto().get("people", 0) == 0
    assert plan.manifesto().get("consultations", 0) == 0
    linha = next(item for item in plan.linhas if item["kind"] == "crm_consultas")
    assert linha["status_final"] == "pendente"
    queue = multi_sheet_review_queue(db, dry["batch_id"])
    item = next(value for value in queue["items"]
                if value["category"] == "identificador_nao_encontrado")
    assert item["executable_decisions"] == ["excluido"]


# -------------------------------------------------------------- item 2/4


def test_candidate_phone_match_resolves_to_single_cross_referenced_person(
    db, users, tmp_path,
):
    """Padrão real de um membro do coorte que também tem CRM Pacientes e
    CRM Consultas próprios: CRM Pacientes cria a pessoa primeiro; o exame do
    coorte com o MESMO telefone vira candidato único (nunca duplicidade
    silenciosa, nunca fusão automática) — só um vincular_candidato
    operador-autorizado produz UMA pessoa só, com exame e consulta
    apontando para ela."""
    envelope = write_raw_envelope(tmp_path, [
        {
            "kind": "crm_pacientes",
            "alias": "Pacientes Sinteticos",
            "rows": [synthetic_row(
                "crm_pacientes",
                paciente_id="PAC-SYN-CROSSREF",
                primeiro_nome="Pessoa Sintetica CrossRef",
                telefone=PHONE_COHORT,
            )],
        },
        {
            "kind": "crm_espirometria",
            "alias": "Coorte Espirometria Sintetico",
            "rows": [synthetic_row(
                "crm_espirometria",
                exame_id="ESP-SYN-CROSSREF",
                primeiro_nome="Pessoa Sintetica CrossRef",
                telefone=PHONE_COHORT,
                data_exame="18/06/2026",
            )],
        },
        {
            "kind": "crm_consultas",
            "alias": "Consultas Sinteticas",
            "rows": [synthetic_row(
                "crm_consultas",
                consulta_id="CONS-SYN-CROSSREF",
                primeiro_nome="Pessoa Sintetica CrossRef",
                paciente_id="PAC-SYN-CROSSREF",
                tipo_consulta="Teleconsulta respiratoria",
                status="Consulta realizada",
                data_consulta="18/06/2026",
            )],
        },
    ])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    queue = multi_sheet_review_queue(db, dry["batch_id"])
    item = next(value for value in queue["items"]
                if value["category"] == "candidato_identidade_provavel")
    decide_multi_sheet_review(
        db, dry["batch_id"], item["private_reference_token"],
        "vincular_candidato", item["mapping_version"], users["admin"].id,
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan.bloqueios == []
    assert plan.manifesto()["people"] == 1
    pessoa = next(op for op in plan.ops if op.entidade == "people")
    exame = next(op for op in plan.ops if op.entidade == "spirometry_exams")
    consulta = next(op for op in plan.ops if op.entidade == "consultations")
    assert exame.refs["person_id"] == pessoa.chave
    assert consulta.refs["person_id"] == pessoa.chave


def test_ambiguous_multi_candidate_stays_fail_closed(db, users, tmp_path):
    """Duas pessoas com o MESMO telefone: candidato múltiplo nunca vincula
    sozinho. Mesmo com vincular_candidato registrado, o plano bloqueia."""
    envelope = write_raw_envelope(tmp_path, [
        {
            "kind": "crm_pacientes",
            "alias": "Pacientes Sinteticos Ambiguos",
            "rows": [
                synthetic_row(
                    "crm_pacientes", paciente_id="PAC-SYN-AMB-1",
                    primeiro_nome="Pessoa Ambigua Um",
                    telefone=PHONE_MULTI_A,
                ),
                synthetic_row(
                    "crm_pacientes", paciente_id="PAC-SYN-AMB-2",
                    primeiro_nome="Pessoa Ambigua Dois",
                    telefone=PHONE_MULTI_A,
                ),
            ],
        },
        {
            "kind": "crm_espirometria",
            "alias": "Exame Ambiguo Sintetico",
            "rows": [synthetic_row(
                "crm_espirometria",
                exame_id="ESP-SYN-AMB",
                primeiro_nome="Pessoa Ambigua Exame",
                telefone=PHONE_MULTI_A,
                data_exame="10/07/2026",
            )],
        },
    ])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    queue = multi_sheet_review_queue(db, dry["batch_id"])
    item = next(value for value in queue["items"]
                if value["category"] == "candidato_identidade_provavel")
    decide_multi_sheet_review(
        db, dry["batch_id"], item["private_reference_token"],
        "vincular_candidato", item["mapping_version"], users["admin"].id,
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert "vinculo_aprovado_sem_candidato_unico" in plan.bloqueios
    linha = next(item for item in plan.linhas if item["kind"] == "crm_espirometria")
    assert linha["status_final"] == "pendente"
    assert linha["motivo"] == "vinculo_aprovado_sem_candidato_unico"


# ---------------------------------------------------------------- item 5


def _combined_name_minor_guardian_envelope(tmp_path):
    # Padrão real do coorte: um único campo de nome combinado (menor +
    # responsável em um só texto) e um único telefone (do responsável) —
    # SEM colunas
    # nome_responsavel_legal/telefone_responsavel_legal preenchidas.
    return write_raw_envelope(tmp_path, [{
        "kind": "crm_espirometria",
        "alias": "Coorte Espirometria Sintetico Guardian",
        "rows": [synthetic_row(
            "crm_espirometria",
            exame_id="ESP-SYN-GUARDIAN",
            primeiro_nome="Menor E Responsavel Combinado Sintetico",
            telefone="(21) 0000-9701",
            data_exame="05/07/2026",
        )],
    }])


def test_guardian_override_splits_minor_and_guardian_into_two_people(
    db, users, tmp_path,
):
    """Padrão real do coorte: a fonte não separa nome/telefone do
    menor e do responsável. Uma decisão operador-autorizada com
    guardian_override cria o menor como paciente do exame e o responsável
    como pessoa distinta, com vínculo legal explícito — nunca uma fusão."""
    envelope = _combined_name_minor_guardian_envelope(tmp_path)
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    override = {
        "minor_name": "Menor Sintetico Guardian",
        "guardian_name": "Responsavel Sintetico Guardian",
        "guardian_phone": "(21) 0000-9701",
        "relationship_type": "mae",
    }
    _decide_first_pending(
        db, dry["batch_id"], "exame_orfao",
        "create_minor_patient_with_guardian", users["admin"].id,
        guardian_override=override,
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan.bloqueios == []
    assert plan.manifesto() == {
        "people": 2, "person_contacts": 1, "person_relationships": 1,
        "spirometry_exams": 1,
    }
    minor = next(op for op in plan.ops
                 if op.entidade == "people" and "minor-decision" in op.chave)
    guardian = next(op for op in plan.ops
                    if op.entidade == "people" and "guardian-decision" in op.chave)
    contact = next(op for op in plan.ops if op.entidade == "person_contacts")
    exam = next(op for op in plan.ops if op.entidade == "spirometry_exams")
    relationship = next(
        op for op in plan.ops if op.entidade == "person_relationships"
    )
    assert minor.campos["nome_completo"] == "Menor Sintetico Guardian"
    assert guardian.campos["nome_completo"] == "Responsavel Sintetico Guardian"
    # o exame nunca vai para o responsável
    assert exam.refs["person_id"] == minor.chave
    assert exam.refs["person_id"] != guardian.chave
    # o contato do responsável nunca fica com o menor
    assert contact.refs["person_id"] == guardian.chave
    assert relationship.refs == {
        "minor_person_id": minor.chave, "guardian_person_id": guardian.chave,
    }
    assert relationship.campos["is_legal_guardian"] is True
    assert relationship.campos["relationship_type"] == "mother"


def test_guardian_override_replay_creates_no_duplicates(db, users, tmp_path):
    """Reprocessar o mesmo lote com a mesma decisão nunca duplica menor,
    responsável, contato, vínculo ou exame."""
    envelope = _combined_name_minor_guardian_envelope(tmp_path)
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    override = {
        "minor_name": "Menor Sintetico Guardian",
        "guardian_name": "Responsavel Sintetico Guardian",
        "guardian_phone": "(21) 0000-9701",
        "relationship_type": "mae",
    }
    _decide_first_pending(
        db, dry["batch_id"], "exame_orfao",
        "create_minor_patient_with_guardian", users["admin"].id,
        guardian_override=override,
    )
    plan_a = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    plan_b = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan_a.manifesto() == plan_b.manifesto()
    assert {op.chave for op in plan_a.ops} == {op.chave for op in plan_b.ops}
    assert (
        {op.idempotency_key for op in plan_a.ops}
        == {op.idempotency_key for op in plan_b.ops}
    )


def test_guardian_override_wins_over_ambiguous_combined_source_name(
    db, users, tmp_path,
):
    """Padrão real: a fonte tem só UM campo de nome, combinando menor e
    responsável (um só texto misturando os dois nomes). A substituição estruturada
    operador-autorizada é quem decide o split — ela é autoritativa para
    ESTA linha, exatamente porque o campo bruto da fonte não separa os
    dois nomes; nunca é uma adivinhação automática do sistema."""
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "crm_espirometria",
        "alias": "Coorte com nome combinado",
        "rows": [synthetic_row(
            "crm_espirometria",
            exame_id="ESP-SYN-OVERRIDE-WINS",
            primeiro_nome="Responsavel Filha Menor Sintetico",
            telefone="(21) 0000-9702",
            data_exame="06/07/2026",
        )],
    }])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    override = {
        "minor_name": "Menor Sintetico Separado",
        "guardian_name": "Responsavel Sintetico Separado",
        "guardian_phone": "(21) 0000-9702",
        "relationship_type": "pai",
    }
    _decide_first_pending(
        db, dry["batch_id"], "exame_orfao",
        "create_minor_patient_with_guardian", users["admin"].id,
        guardian_override=override,
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan.bloqueios == []
    minor = next(op for op in plan.ops
                 if op.entidade == "people" and "minor-decision" in op.chave)
    guardian = next(op for op in plan.ops
                    if op.entidade == "people" and "guardian-decision" in op.chave)
    assert minor.campos["nome_completo"] == "Menor Sintetico Separado"
    assert guardian.campos["nome_completo"] == "Responsavel Sintetico Separado"


@pytest.mark.parametrize("broken_field", sorted(
    {"minor_name", "guardian_name", "guardian_phone", "relationship_type"}
))
def test_validate_guardian_override_rejects_missing_field(broken_field):
    payload = {
        "minor_name": "Menor Sintetico",
        "guardian_name": "Responsavel Sintetico",
        "guardian_phone": "(21) 0000-9704",
        "relationship_type": "mae",
    }
    payload[broken_field] = ""
    with pytest.raises(MultiSheetError):
        validate_guardian_override(payload)


def test_validate_guardian_override_rejects_unknown_relationship_type():
    payload = {
        "minor_name": "Menor Sintetico",
        "guardian_name": "Responsavel Sintetico",
        "guardian_phone": "(21) 0000-9704",
        "relationship_type": "vizinha",
    }
    with pytest.raises(MultiSheetError, match="tipo_relacao_invalido"):
        validate_guardian_override(payload)


def test_validate_guardian_override_rejects_same_name_for_both():
    payload = {
        "minor_name": "Mesma Pessoa Sintetica",
        "guardian_name": "Mesma Pessoa Sintetica",
        "guardian_phone": "(21) 0000-9704",
        "relationship_type": "mae",
    }
    with pytest.raises(MultiSheetError, match="menor_e_responsavel_iguais"):
        validate_guardian_override(payload)


def test_validate_guardian_override_accepts_every_known_relationship_alias():
    for alias, canonical in GUARDIAN_RELATIONSHIP_TYPE_ALIASES.items():
        result = validate_guardian_override({
            "minor_name": "Menor Sintetico",
            "guardian_name": "Responsavel Sintetico",
            "guardian_phone": "(21) 0000-9704",
            "relationship_type": alias,
        })
        assert result["relationship_type"] == canonical


def test_guardian_override_rejected_when_decision_is_not_guardian_type(
    db, users, tmp_path,
):
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "crm_espirometria",
        "alias": "Coorte Sintetico Decisao Errada",
        "rows": [synthetic_row(
            "crm_espirometria",
            exame_id="ESP-SYN-WRONG-DECISION",
            primeiro_nome="Paciente Sintetico Decisao Errada",
            telefone="(21) 0000-9705",
            data_exame="07/07/2026",
        )],
    }])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    queue = multi_sheet_review_queue(db, dry["batch_id"])
    item = next(value for value in queue["items"]
                if value["category"] == "exame_orfao")
    with pytest.raises(MultiSheetError, match="guardian_override_requer_decisao_guardian"):
        decide_multi_sheet_review(
            db, dry["batch_id"], item["private_reference_token"],
            "criar_pessoa", item["mapping_version"], users["admin"].id,
            guardian_override={
                "minor_name": "Menor Sintetico",
                "guardian_name": "Responsavel Sintetico",
                "guardian_phone": "(21) 0000-9706",
                "relationship_type": "mae",
            },
        )


def test_guardian_decision_without_override_and_without_source_still_blocks(
    db, users, tmp_path,
):
    """Sem override E sem colunas de responsável na fonte, o comportamento
    é o mesmo de antes do M15.8: bloqueio fail-closed, nunca adivinhação."""
    envelope = _combined_name_minor_guardian_envelope(tmp_path)
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    _decide_first_pending(
        db, dry["batch_id"], "exame_orfao",
        "create_minor_patient_with_guardian", users["admin"].id,
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert "minor_guardian_names_required_from_source" in plan.bloqueios
    linha = next(item for item in plan.linhas if item["kind"] == "crm_espirometria")
    assert linha["motivo"] == "minor_guardian_names_required_from_source"


# ---------------------------------------------------------------- item 6


def test_pastore_cost_references_never_produce_financial_entry(
    db, users, tmp_path,
):
    """Referências de custo Pastore (ex.: filtro a R$7,30) nunca viram
    lançamento financeiro: só "excluido" é decisão executável e nenhuma
    operação financial_entries nasce daqui, mesmo decidida."""
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "pastore_custos",
        "alias": "Pastore Custos Sinteticos Duplos",
        "rows": [
            synthetic_row(
                "pastore_custos", data="01/07/2026",
                unidade="Unidade Sintetica Pastore", valor="7,30",
            ),
            synthetic_row(
                "pastore_custos", data="08/07/2026",
                unidade="Unidade Sintetica Pastore", valor="7,30",
            ),
        ],
    }])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    queue = multi_sheet_review_queue(db, dry["batch_id"])
    custo_items = [
        item for item in queue["items"]
        if item["category"] == "fonte_monetaria_nao_autorizada"
    ]
    assert len(custo_items) == 2
    for item in custo_items:
        assert item["executable_decisions"] == ["excluido"]
        with pytest.raises(MultiSheetError, match="decisao_incompativel_com_categoria"):
            decide_multi_sheet_review(
                db, dry["batch_id"], item["private_reference_token"],
                "criar_pessoa", item["mapping_version"], users["admin"].id,
            )
        decide_multi_sheet_review(
            db, dry["batch_id"], item["private_reference_token"],
            "excluido", item["mapping_version"], users["admin"].id,
        )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan.manifesto().get("financial_entries", 0) == 0


def test_pastore_two_weekday_schedules_share_one_physical_unit(
    db, users, tmp_path,
):
    """Terça e sábado da MESMA unidade Pastore nunca viram duas unidades
    físicas: só a config de agenda se duplica."""
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "pastore_config",
        "alias": "Agenda Pastore Sintetica",
        "rows": [
            synthetic_row(
                "pastore_config", unidade="Unidade Sintetica Pastore Dupla",
                status="ativo", dia_semana="terca",
                horario_inicio="08:00", horario_fim="12:00",
            ),
            synthetic_row(
                "pastore_config", unidade="Unidade Sintetica Pastore Dupla",
                status="ativo", dia_semana="sabado",
                horario_inicio="08:00", horario_fim="12:00",
            ),
        ],
    }])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    # a segunda agenda da MESMA unidade é uma duplicata bloqueante até um
    # operador decidir explicitamente que as duas coexistem (manter_ambas).
    _decide_first_pending(
        db, dry["batch_id"], "duplicata_execucao", "manter_ambas",
        users["admin"].id,
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan.bloqueios == []
    assert plan.manifesto()["partner_units"] == 1
    assert plan.manifesto()["partner_unit_configs"] == 2
    unidades = {op.chave for op in plan.ops if op.entidade == "partner_units"}
    assert len(unidades) == 1


# ---------------------------------------------------------------- item 7


def test_fin_0001_0002_0003_always_excluded_from_financial_reconstruction():
    fixed = [
        {"examination_id": "EX-SYN-FIX-01", "amount_cents": 22_000},
        {"examination_id": "EX-SYN-FIX-02", "amount_cents": 22_000},
    ]
    remaining = []
    document = build_financial_reconstruction(
        version="m15-8-synthetic-v1",
        fixed_exam_mappings=fixed,
        remaining_exam_mappings=remaining,
        pastore_exam_ids=[],
    )
    assert document["legacy_exclusion_decisions"] == [
        {"legacy_id": "FIN-0001", "decision": "excluded"},
        {"legacy_id": "FIN-0002", "decision": "excluded"},
        {"legacy_id": "FIN-0003", "decision": "excluded"},
    ]
    assert set(REQUIRED_LEGACY_EXCLUSIONS) == {"FIN-0001", "FIN-0002", "FIN-0003"}
    # validado: documento com exclusões corretas passa
    validate_financial_reconstruction(document)

    def _resigned(mutated_document: dict) -> dict:
        """Recalcula o fingerprint canônico após adulterar o conteúdo, para
        provar que a checagem de EXCLUSÕES (não a de integridade) é quem
        rejeita a tentativa de remover/alterar FIN-0001/0002/0003."""
        unsigned = {
            key: value for key, value in mutated_document.items()
            if key != "document_fingerprint"
        }
        return {**unsigned, "document_fingerprint": _fingerprint(unsigned)}

    tampered = _resigned({
        **document,
        "legacy_exclusion_decisions": [
            {"legacy_id": "FIN-0001", "decision": "excluded"},
            {"legacy_id": "FIN-0002", "decision": "excluded"},
            # FIN-0003 removido do documento adulterado
        ],
    })
    with pytest.raises(OperationalCompletionError, match="financial_exclusions_invalid"):
        validate_financial_reconstruction(tampered)

    included = _resigned({
        **document,
        "legacy_exclusion_decisions": [
            {"legacy_id": "FIN-0001", "decision": "excluded"},
            {"legacy_id": "FIN-0002", "decision": "excluded"},
            {"legacy_id": "FIN-0003", "decision": "included"},
        ],
    })
    with pytest.raises(OperationalCompletionError, match="financial_exclusions_invalid"):
        validate_financial_reconstruction(included)


# ---------------------------------------------------------------- item A
# (M15.8 — importação de Leads: nunca vira paciente por conta própria;
# fila de retomada preserva a próxima ação da fonte, cai para 6 meses após
# o contato, ou fica explicitamente pendente — nunca inventa data.)


def test_lead_import_never_creates_patient_examination_or_consultation(
    db, users, tmp_path,
):
    """Todo lead válido vira só prospecto + follow-up de captação — NUNCA
    paciente/exame/consulta por conta própria (item A)."""
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "leads",
        "alias": "Leads Sinteticos M15.8 Item A",
        "rows": [synthetic_row(
            "leads", lead_id="LEAD-SYN-M158-A01",
            nome="Prospecto Sintetico Sem Atendimento",
            telefone_whatsapp="(21) 0000-9801",
            data_contato="01/07/2026",
        )],
    }])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan.bloqueios == []
    manifesto = plan.manifesto()
    assert manifesto.get("spirometry_exams", 0) == 0
    assert manifesto.get("consultations", 0) == 0
    assert manifesto["leads"] == 1
    assert manifesto["followups"] == 1
    lead_op = next(op for op in plan.ops if op.entidade == "leads")
    followup_op = next(op for op in plan.ops if op.entidade == "followups")
    assert followup_op.campos["tipo"] == "lead_sem_atendimento"
    assert followup_op.campos["status"] == "pendente"
    assert followup_op.refs["person_id"] == lead_op.refs["person_id"]
    # sem próxima ação na fonte: cai para 6 meses corridos após o contato.
    assert followup_op.campos["due_date"] == date(2027, 1, 1)


def test_lead_followup_preserves_source_next_action_date_when_present(
    db, users, tmp_path,
):
    """Próxima ação já agendada na fonte (precisão de dia) é preservada tal
    e qual — nunca recalculada (item A)."""
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "leads",
        "alias": "Leads Sinteticos M15.8 Proxima Acao",
        "rows": [synthetic_row(
            "leads", lead_id="LEAD-SYN-M158-A02",
            nome="Prospecto Sintetico Com Proxima Acao",
            telefone_whatsapp="(21) 0000-9802",
            data_contato="01/07/2026",
            data_proxima_acao="20/07/2026",
        )],
    }])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan.bloqueios == []
    followup_op = next(op for op in plan.ops if op.entidade == "followups")
    assert followup_op.campos["due_date"] == date(2026, 7, 20)


def test_lead_followup_ignores_imprecise_next_action_falls_back_to_six_months(
    db, users, tmp_path,
):
    """Próxima ação com precisão de mês (dia assumido) NUNCA vira prazo
    literal — mesma cautela já aplicada ao Follow-up WhatsApp. Cai para o
    fallback de 6 meses após o contato (item A)."""
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "leads",
        "alias": "Leads Sinteticos M15.8 Proxima Acao Imprecisa",
        "rows": [synthetic_row(
            "leads", lead_id="LEAD-SYN-M158-A03",
            nome="Prospecto Sintetico Data Imprecisa",
            telefone_whatsapp="(21) 0000-9803",
            data_contato="01/07/2026",
            data_proxima_acao="08/2026",
        )],
    }])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan.bloqueios == []
    followup_op = next(op for op in plan.ops if op.entidade == "followups")
    assert followup_op.campos["due_date"] == date(2027, 1, 1)


def test_lead_followup_stays_explicitly_pending_without_any_usable_date(
    db, users, tmp_path,
):
    """Sem nenhuma data utilizável na fonte (nem próxima ação, nem
    contato), o vencimento fica None — pendente explícito, nunca inventado
    (item A)."""
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "leads",
        "alias": "Leads Sinteticos M15.8 Sem Data",
        "rows": [synthetic_row(
            "leads", lead_id="LEAD-SYN-M158-A04",
            nome="Prospecto Sintetico Sem Data Utilizavel",
            telefone_whatsapp="(21) 0000-9804",
        )],
    }])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan.bloqueios == []
    followup_op = next(op for op in plan.ops if op.entidade == "followups")
    assert followup_op.campos["due_date"] is None
    assert followup_op.campos["status"] == "pendente"


def test_lead_followup_replay_creates_no_duplicates(db, users, tmp_path):
    """Reprocessar o mesmo lote de Leads nunca duplica o follow-up de
    captação (mesma disciplina de idempotência do resto do M15.8)."""
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "leads",
        "alias": "Leads Sinteticos M15.8 Replay",
        "rows": [synthetic_row(
            "leads", lead_id="LEAD-SYN-M158-A05",
            nome="Prospecto Sintetico Replay",
            telefone_whatsapp="(21) 0000-9805",
            data_contato="01/07/2026",
        )],
    }])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    plan_a = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    plan_b = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan_a.manifesto() == plan_b.manifesto()
    assert (
        {op.idempotency_key for op in plan_a.ops if op.entidade == "followups"}
        == {op.idempotency_key for op in plan_b.ops if op.entidade == "followups"}
    )


# ---------------------------------------------------------------- item B
# (M15.8 — contato B2B de direção médica de uma clínica parceira: nome e
# cargo reais, telefone/email nunca inventados quando ausentes na fonte.)


def test_partner_contact_role_import_never_invents_phone_or_email(
    db, users, tmp_path,
):
    """Contato B2B com cargo de direção médica e telefone/email em branco
    na fonte: importa nome/cargo reais, mas telefone e email ficam vazios —
    nunca inventados (item B)."""
    envelope = write_raw_envelope(tmp_path, [
        {
            "kind": "crm_clinicas",
            "alias": "Clinicas Sinteticas M15.8 Item B",
            "rows": [synthetic_row(
                "crm_clinicas", clinica_id="CLIN-SYN-M158-B01",
                nome_clinica="Clinica Parceira Sintetica M15.8",
                bairro="Bairro Sintetico", tipo_clinica="consultorio",
            )],
        },
        {
            "kind": "contatos_b2b",
            "alias": "Contatos B2B Sinteticos M15.8 Item B",
            "rows": [synthetic_row(
                "contatos_b2b", contato_id="CONT-SYN-M158-B01",
                clinica_id="CLIN-SYN-M158-B01",
                nome_contato="Contato Diretor Sintetico",
                cargo="Diretor medico",
            )],
        },
    ])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    plan = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan.bloqueios == []
    contato_op = next(op for op in plan.ops if op.entidade == "partner_contacts")
    assert contato_op.campos["nome"] == "Contato Diretor Sintetico"
    assert contato_op.campos["cargo"] == "Diretor medico"
    assert not contato_op.campos["telefone"]
    assert not contato_op.campos["email"]


def test_partner_contact_replay_reuses_existing_contact_never_duplicates(
    db, users, tmp_path,
):
    """Reprocessar o mesmo lote com o mesmo contato B2B (mesmo contato_id)
    nunca cria um segundo partner_contacts — reuso pela mesma proveniência
    (legacy_aliases), nunca um registro novo (item B)."""
    envelope = write_raw_envelope(tmp_path, [
        {
            "kind": "crm_clinicas",
            "alias": "Clinicas Sinteticas M15.8 Item B Replay",
            "rows": [synthetic_row(
                "crm_clinicas", clinica_id="CLIN-SYN-M158-B02",
                nome_clinica="Clinica Parceira Sintetica Replay",
            )],
        },
        {
            "kind": "contatos_b2b",
            "alias": "Contatos B2B Sinteticos M15.8 Item B Replay",
            "rows": [synthetic_row(
                "contatos_b2b", contato_id="CONT-SYN-M158-B02",
                clinica_id="CLIN-SYN-M158-B02",
                nome_contato="Contato Diretor Sintetico Replay",
                cargo="Diretor medico",
            )],
        },
    ])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    plan_a = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    plan_b = build_execution_plan(db, envelope, dry["batch_id"], base_dir=tmp_path)
    assert plan_a.manifesto() == plan_b.manifesto()
    assert (
        {op.idempotency_key for op in plan_a.ops
         if op.entidade == "partner_contacts"}
        == {op.idempotency_key for op in plan_b.ops
            if op.entidade == "partner_contacts"}
    )


# ---------------------------------------------------------------- item C
# (M15.8 — reconstrução financeira dos 13 recibos extra-Pastore: achado
# real é que a contagem de exames sem NENHUM Financeiro_Lancamentos não
# fecha em 13 sem inventar um ID — o documento tem que ficar bloqueado e
# relatar exatamente quantas marcações faltam, nunca forçar o ajuste.)


def test_financial_reconstruction_stays_blocked_with_twelve_of_thirteen_mappings():
    """Achado real do M15.8 (ver relatório): só 1 marcação fixa de R$220
    confirmada (12 IDs técnicos distintos ao todo) quando o contrato exige
    2 fixas + 11 restantes = 13. O documento NUNCA fica "ready_preview" com
    contagem incompleta — relata exatamente 1 marcação faltante, em vez de
    inventar o ID que falta."""
    fixed = [{"examination_id": "EX-SYN-FIX-01", "amount_cents": 22_000}]
    remaining = [
        {"examination_id": f"EX-SYN-REM-{i:02d}",
         "examination_date": f"2026-07-{i:02d}"}
        for i in range(1, 12)
    ]
    document = build_financial_reconstruction(
        version="m15-8-synthetic-incomplete-v1",
        fixed_exam_mappings=fixed,
        remaining_exam_mappings=remaining,
        pastore_exam_ids=[],
    )
    assert document["state"] == "blocked_missing_mappings"
    assert document["missing_mapping_slots"] == 1
    assert document["assignments"] == []


def test_financial_reconstruction_ready_only_with_all_thirteen_mappings():
    """Contraprova: com as 2 marcações fixas + 11 restantes (13 no total),
    o documento fecha em "ready_preview" com o total exato documentado."""
    fixed = [
        {"examination_id": "EX-SYN-FIX-01", "amount_cents": 22_000},
        {"examination_id": "EX-SYN-FIX-02", "amount_cents": 22_000},
    ]
    remaining = [
        {"examination_id": f"EX-SYN-REM-{i:02d}",
         "examination_date": f"2026-07-{i:02d}"}
        for i in range(1, 12)
    ]
    document = build_financial_reconstruction(
        version="m15-8-synthetic-complete-v1",
        fixed_exam_mappings=fixed,
        remaining_exam_mappings=remaining,
        pastore_exam_ids=[],
    )
    assert document["state"] == "ready_preview"
    assert document["missing_mapping_slots"] == 0
    assert document["assigned_total_cents"] == ADMIN_TOTAL_CENTS
    assert len(document["assignments"]) == EXPECTED_EXAMINATIONS


def test_financial_reconstruction_total_math_documented_contract():
    """Contrato de valores do M15.8 (proprietário): 2 entradas fixas de
    R$220 + 10 entradas de R$236,80 + 1 entrada de R$236,79 = R$3.044,79.
    Guarda contra deriva silenciosa das constantes."""
    assert EXPECTED_EXAMINATIONS == 13
    assert len(REMAINING_ENTRY_CENTS) == 11
    assert 2 * FIXED_ENTRY_CENTS + sum(REMAINING_ENTRY_CENTS) == ADMIN_TOTAL_CENTS
