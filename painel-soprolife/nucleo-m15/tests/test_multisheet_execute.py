"""Execução final multiaba M15.6C — 100% sintética e determinística.

Cobre: portões de bloqueio (revisão, financeiro, duplicata, checksum/hash,
backup, admin, frase), decisões executáveis (vincular por telefone, criar
pessoa, exclusão), ordem completa de dependência, todas as entidades
suportadas, exclusão PCMSO, fronteira monetária única, transação única,
rollback seletivo seguro, idempotência, reconciliação com fechamento exato,
preservação de registros preexistentes, ausência de caminho browser/API e
ausência de PII em logs. Nenhum dado real é usado.
"""

import json

import pytest
from sqlalchemy import select

from app import cli
from app.main import create_app
from app.migration import executor
from app.migration.executor import (
    ORDEM_ENTIDADES,
    build_final_plan,
    confirmation_phrase_multiaba,
    execute_multi_sheet,
    generate_rollback_plan,
    preflight_multi_sheet_execution,
    reconcile_multi_sheet_execution,
    rollback_multi_sheet_execution,
    rollback_phrase_multiaba,
    rollback_plan_report,
)
from app.migration.multisheet import (
    MultiSheetError,
    decide_multi_sheet_review,
    multi_sheet_review_queue,
    multi_sheet_status,
    run_multi_sheet_dry_run,
)
from app.models import (
    AuditLog,
    Consent,
    FinancialEntry,
    ImportBatch,
    ImportRow,
    LegacyAlias,
    MigrationProvenance,
    PartnerUnitConfig,
    Person,
    SpirometryExam,
)

from tests._migration_fixtures import write_backup_evidence
from tests._multisheet_fixtures import synthetic_row, write_raw_envelope

PHONE_A = "(21) 0000-9101"
PHONE_B = "(21) 0000-9102"
PHONE_C = "(21) 0000-9103"

ADMIN = "admin@teste.local"


def executable_sheets():
    """Cenário completo: todas as entidades operacionais suportadas."""
    return [
        {"kind": "crm_clinicas", "alias": "Clinicas Sinteticas", "rows": [
            synthetic_row(
                "crm_clinicas", clinica_id="CL-001",
                nome_clinica="Clinica Sintetica 001",
                bairro="Bairro Sintetico", tipo_clinica="geral"),
        ]},
        {"kind": "contatos_b2b", "alias": "Contatos Sinteticos", "rows": [
            synthetic_row(
                "contatos_b2b", contato_id="B-001", clinica_id="CL-001",
                nome_contato="Contato Sintetico 001", cargo="gestor"),
        ]},
        {"kind": "crm_pacientes", "alias": "Pacientes Sinteticos", "rows": [
            synthetic_row(
                "crm_pacientes", paciente_id="P-001",
                primeiro_nome="Pessoa Sintetica Alpha", telefone=PHONE_A,
                consentimento_whatsapp="sim", data_cadastro="01/07/2026"),
            synthetic_row(
                "crm_pacientes", paciente_id="P-002",
                primeiro_nome="Pessoa Sintetica Beta", telefone=PHONE_B,
                consentimento_whatsapp="nao contatar"),
        ]},
        {"kind": "leads", "alias": "Leads Sinteticos", "rows": [
            synthetic_row(
                "leads", lead_id="L-001", nome="Pessoa Sintetica Alpha",
                entidade_id="P-001", data_contato="07/2026"),
            synthetic_row(
                "leads", lead_id="L-002", nome="Lead Sintetico Sem Vinculo"),
        ]},
        {"kind": "crm_espirometria", "alias": "Espirometrias Sinteticas",
         "rows": [
            synthetic_row(
                "crm_espirometria", exame_id="E-001",
                primeiro_nome="Pessoa Sintetica Alpha", telefone=PHONE_A,
                data_exame="01/07/2026"),
            synthetic_row(
                "crm_espirometria", exame_id="E-002",
                primeiro_nome="Pessoa Sintetica Gama", telefone=PHONE_C),
        ]},
        {"kind": "crm_consultas", "alias": "Consultas Sinteticas", "rows": [
            synthetic_row(
                "crm_consultas", consulta_id="C-001", paciente_id="P-001",
                data_consulta="2026-07", consentimento_whatsapp="sim"),
        ]},
        {"kind": "followup_whatsapp", "alias": "Followups Sinteticos",
         "rows": [
            synthetic_row(
                "followup_whatsapp", followup_id="FW-001",
                primeiro_nome="Pessoa Sintetica Alpha", telefone=PHONE_A,
                data_prevista="15/07/2026"),
        ]},
        {"kind": "financeiro_lancamentos", "alias": "Financeiro Sintetico",
         "rows": [
            synthetic_row(
                "financeiro_lancamentos", id_lancamento="F-001",
                id_atendimento="E-001", tipo_movimento="receita",
                valor_cobrado="100,00", data_exame="01/07/2026"),
        ]},
        {"kind": "pastore_config", "alias": "Pastore Config Sintetica",
         "rows": [
            synthetic_row(
                "pastore_config", unidade="Unidade Sintetica 001",
                status="ativo", dia_semana="terca",
                valor_exame_sem_bd="999,00"),
        ]},
        {"kind": "pastore_atendimentos", "alias": "Pastore Atend Sinteticos",
         "rows": [
            synthetic_row(
                "pastore_atendimentos", data_atendimento="02/07/2026",
                unidade="Unidade Sintetica 001",
                paciente_nome="Pessoa Sintetica Alpha",
                paciente_whatsapp=PHONE_A, valor_cobrado="999,00"),
        ]},
        {"kind": "pastore_custos", "alias": "Pastore Custos Sinteticos",
         "rows": [
            synthetic_row(
                "pastore_custos", data="01/07/2026",
                unidade="Unidade Sintetica 001", valor="50,00"),
        ]},
        {"kind": "pcmso_historico", "alias": "Historico Sintetico", "rows": [
            synthetic_row(
                "pcmso_historico",
                **{"Nome da clínica/empresa": "Empresa Historica Sintetica"}),
        ]},
    ]


# Manifesto esperado do cenário completo (contrato fechado do teste).
MANIFESTO_ESPERADO = {
    "partners": 2,
    "partner_units": 2,
    "partner_unit_configs": 1,
    "partner_contacts": 1,
    "partnerships": 1,
    "people": 4,
    "person_contacts": 3,
    "consents": 3,
    "leads": 2,
    "spirometry_exams": 3,
    "consultations": 1,
    "followups": 1,
    "financial_entries": 1,
    "legacy_aliases": 12,
}

DECISAO_PADRAO = {
    "candidato_identidade_provavel": "vincular_candidato",
    "exame_orfao": "criar_pessoa",
    "consulta_orfa": "criar_pessoa",
    "registro_orfao": "criar_pessoa",
    "relacao_financeira_nao_resolvida": "excluido",
    "fonte_monetaria_nao_autorizada": "excluido",
    "identificador_conflitante": "excluido",
    "identificador_nao_encontrado": "excluido",
    "data_invalida_ativa": "excluido",
}


def sheets_com_duplicata_pastore(*, status_segunda="inativo"):
    sheets = executable_sheets()
    config = next(s for s in sheets if s["kind"] == "pastore_config")
    config["rows"].append(synthetic_row(
        "pastore_config",
        unidade="  unidade sintetica 001  ",
        status=status_segunda,
        dia_semana="quarta",
    ))
    return sheets


def dry_run(db, base, sheets, users):
    nome = write_raw_envelope(base, sheets)
    resultado = run_multi_sheet_dry_run(
        db, nome, base_dir=base, user_id=users["admin"].id)
    db.commit()
    return nome, resultado["batch_id"]


def decidir_pendentes(db, batch_id, users, overrides=None):
    fila = multi_sheet_review_queue(db, batch_id)
    for item in fila["items"]:
        if item["status"] != "pendente":
            continue
        decisao = (overrides or {}).get(
            item["category"], DECISAO_PADRAO.get(item["category"]))
        if decisao is None or item.get("decision_state") == decisao:
            continue
        decide_multi_sheet_review(
            db, batch_id, item["private_reference_token"], decisao,
            item["mapping_version"], users["gestor"].id)
    db.commit()


def preparar(db, base, users, sheets=None, decidir=True, com_plano=True):
    nome, batch_id = dry_run(db, base, sheets or executable_sheets(), users)
    if decidir:
        decidir_pendentes(db, batch_id, users)
    if com_plano:
        generate_rollback_plan(
            db, nome, batch_id, base_dir=base, user_id=users["admin"].id)
        db.commit()
    evidencia = write_backup_evidence(base)
    return nome, batch_id, evidencia


def executar_ok(db, base, users, nome, batch_id, evidencia):
    resultado = execute_multi_sheet(
        db, nome, batch_id, confirmation_phrase_multiaba(batch_id),
        evidencia, admin_email=ADMIN, base_dir=base)
    db.commit()
    return resultado


def alias_id(db, entidade, fonte, legacy_id):
    alias = db.execute(
        select(LegacyAlias).where(
            LegacyAlias.entidade == entidade,
            LegacyAlias.legacy_source == fonte,
            LegacyAlias.legacy_id == legacy_id,
        )
    ).scalar_one()
    return alias.entity_id


def contar(db, modelo) -> int:
    return len(db.execute(select(modelo)).scalars().all())


# --------------------------------------------------------------- bloqueios


def test_execucao_bloqueada_com_revisao_pendente(db, users, tmp_path):
    nome, batch_id, evidencia = preparar(
        db, tmp_path, users, decidir=False, com_plano=False)
    pf = preflight_multi_sheet_execution(
        db, nome, batch_id, evidencia, ADMIN, base_dir=tmp_path)
    assert pf["ok"] is False
    assert pf["gates"]["revisoes_resolvidas"]["ok"] is False
    with pytest.raises(MultiSheetError) as exc:
        execute_multi_sheet(
            db, nome, batch_id, confirmation_phrase_multiaba(batch_id),
            evidencia, admin_email=ADMIN, base_dir=tmp_path)
    assert exc.value.codigo == "portoes_reprovados"
    assert contar(db, MigrationProvenance) == 0
    assert contar(db, Person) == 0


def test_execucao_bloqueada_com_financeiro_nao_resolvido(db, users, tmp_path):
    sheets = executable_sheets()
    sheets[7]["rows"].append(synthetic_row(
        "financeiro_lancamentos", id_lancamento="F-002",
        id_atendimento="E-INEXISTENTE", tipo_movimento="receita",
        valor_cobrado="80,00"))
    nome, batch_id, evidencia = preparar(
        db, tmp_path, users, sheets=sheets,
        decidir=False, com_plano=False)
    decidir_pendentes(db, batch_id, users,
                      overrides={"relacao_financeira_nao_resolvida": None})
    pf = preflight_multi_sheet_execution(
        db, nome, batch_id, evidencia, ADMIN, base_dir=tmp_path)
    assert pf["ok"] is False
    assert pf["gates"]["financeiro_resolvido"]["ok"] is False
    # decidida a exclusão explícita, o portão financeiro fecha
    decidir_pendentes(db, batch_id, users)
    pf2 = preflight_multi_sheet_execution(
        db, nome, batch_id, evidencia, ADMIN, base_dir=tmp_path)
    assert pf2["gates"]["financeiro_resolvido"]["ok"] is True


def test_execucao_bloqueada_com_duplicata_nao_resolvida(db, users, tmp_path):
    sheets = executable_sheets()
    sheets[2]["rows"].append(synthetic_row(
        "crm_pacientes", paciente_id="P-001",
        primeiro_nome="Pessoa Sintetica Divergente"))
    nome, batch_id, evidencia = preparar(
        db, tmp_path, users, sheets=sheets, decidir=False, com_plano=False)
    decidir_pendentes(db, batch_id, users,
                      overrides={"identificador_conflitante": None})
    pf = preflight_multi_sheet_execution(
        db, nome, batch_id, evidencia, ADMIN, base_dir=tmp_path)
    assert pf["ok"] is False
    assert pf["gates"]["duplicatas_conflitos_resolvidos"]["ok"] is False
    decidir_pendentes(db, batch_id, users)
    pf2 = preflight_multi_sheet_execution(
        db, nome, batch_id, evidencia, ADMIN, base_dir=tmp_path)
    assert pf2["gates"]["duplicatas_conflitos_resolvidos"]["ok"] is True


def test_duplicata_pastore_recebe_token_oficial_e_permanece_pendente(
    db, users, tmp_path,
):
    nome, batch_id = dry_run(
        db, tmp_path, sheets_com_duplicata_pastore(), users)
    fila = multi_sheet_review_queue(db, batch_id)
    duplicatas = [
        item for item in fila["items"]
        if item["category"] == "duplicata_execucao"
    ]
    assert len(duplicatas) == 1
    assert duplicatas[0]["private_reference_token"].startswith("priv-")
    assert duplicatas[0]["status"] == "pendente"

    decidir_pendentes(db, batch_id, users)
    plano = build_final_plan(db, nome, batch_id, base_dir=tmp_path)
    assert plano.conflitos_nao_resolvidos == 1
    assert plano.revisoes_pendentes_sem_decisao == 1
    assert any(
        linha["kind"] == "pastore_config"
        and linha["status_final"] == "duplicado"
        for linha in plano.linhas
    )


def test_duplicata_pastore_manter_primeira_exclui_segunda(
    db, users, tmp_path,
):
    nome, batch_id = dry_run(
        db, tmp_path, sheets_com_duplicata_pastore(), users)
    decidir_pendentes(
        db, batch_id, users,
        overrides={"duplicata_execucao": "manter_primeira"},
    )
    plano = build_final_plan(db, nome, batch_id, base_dir=tmp_path)
    linhas = [l for l in plano.linhas if l["kind"] == "pastore_config"]
    assert [l["status_final"] for l in linhas] == ["importado", "excluido"]
    assert linhas[1]["decisao"] == "manter_primeira"
    assert plano.conflitos_nao_resolvidos == 0
    configs = [op for op in plano.ops if op.entidade == "partner_unit_configs"]
    assert len(configs) == 1
    assert configs[0].campos["status"] == "ativo"


def test_duplicata_pastore_manter_segunda_substitui_primeira(
    db, users, tmp_path,
):
    nome, batch_id = dry_run(
        db, tmp_path,
        sheets_com_duplicata_pastore(status_segunda="inativo"), users,
    )
    decidir_pendentes(
        db, batch_id, users,
        overrides={"duplicata_execucao": "manter_segunda"},
    )
    plano = build_final_plan(db, nome, batch_id, base_dir=tmp_path)
    linhas = [l for l in plano.linhas if l["kind"] == "pastore_config"]
    assert [l["status_final"] for l in linhas] == ["excluido", "importado"]
    assert all(l["decisao"] == "manter_segunda" for l in linhas)
    assert plano.conflitos_nao_resolvidos == 0
    configs = [op for op in plano.ops if op.entidade == "partner_unit_configs"]
    assert len(configs) == 1
    assert configs[0].campos["status"] == "inativo"
    assert configs[0].campos["dia_semana"] == "quarta"


def test_duplicata_pastore_manter_ambas_exige_identidades_distintas(
    db, users, tmp_path,
):
    nome, batch_id = dry_run(
        db, tmp_path, sheets_com_duplicata_pastore(), users)
    decidir_pendentes(
        db, batch_id, users,
        overrides={"duplicata_execucao": "manter_ambas"},
    )
    plano = build_final_plan(db, nome, batch_id, base_dir=tmp_path)
    assert plano.conflitos_nao_resolvidos == 1
    assert "duplicatas_com_identidade_tecnica_igual" in plano.bloqueios
    assert any(
        linha["kind"] == "pastore_config"
        and linha["status_final"] == "duplicado"
        and linha["decisao"] == "manter_ambas"
        for linha in plano.linhas
    )


def test_execucao_bloqueada_por_checksum_alterado(db, users, tmp_path):
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    (tmp_path / "sheet-01.json").write_bytes(b"conteudo adulterado")
    pf = preflight_multi_sheet_execution(
        db, nome, batch_id, evidencia, ADMIN, base_dir=tmp_path)
    assert pf["ok"] is False
    assert pf["gates"]["envelope_privado_integro"]["ok"] is False
    with pytest.raises(MultiSheetError):
        execute_multi_sheet(
            db, nome, batch_id, confirmation_phrase_multiaba(batch_id),
            evidencia, admin_email=ADMIN, base_dir=tmp_path)
    assert contar(db, MigrationProvenance) == 0


def test_execucao_bloqueada_por_plan_hash_divergente(db, users, tmp_path):
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    # drift entre dry-run e execute: outra importação criou alias de P-001,
    # então o plano re-derivado muda e o hash deixa de bater (fail-closed)
    outra = Person(public_code="PES-888888",
                   nome_completo="Pessoa De Outro Lote",
                   nome_normalizado="pessoa de outro lote")
    db.add(outra)
    db.flush()
    db.add(LegacyAlias(entidade="people", legacy_source="crm_pacientes",
                       legacy_id="P-001", entity_id=outra.id))
    db.commit()
    pf = preflight_multi_sheet_execution(
        db, nome, batch_id, evidencia, ADMIN, base_dir=tmp_path)
    assert pf["ok"] is False
    assert pf["gates"]["plano_hash_identico"]["ok"] is False

    # envelope regravado com conteúdo diferente = sha novo = lote não confere
    sheets = executable_sheets()
    sheets[3]["rows"].append(synthetic_row(
        "leads", lead_id="L-003", nome="Lead Novo Pos Dry Run"))
    write_raw_envelope(tmp_path, sheets)  # regrava o MESMO nome de envelope
    pf2 = preflight_multi_sheet_execution(
        db, nome, batch_id, evidencia, ADMIN, base_dir=tmp_path)
    assert pf2["ok"] is False
    assert pf2["gates"]["plano_derivavel"]["ok"] is False


def test_execucao_bloqueada_sem_backup(db, users, tmp_path):
    nome, batch_id, _ = preparar(db, tmp_path, users)
    pf = preflight_multi_sheet_execution(
        db, nome, batch_id, None, ADMIN, base_dir=tmp_path)
    assert pf["ok"] is False
    assert pf["gates"]["backup_validado"]["ok"] is False
    assert pf["gates"]["evidencia_rollback_disponivel"]["ok"] is False


def test_execucao_bloqueada_sem_admin(db, users, tmp_path):
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    pf = preflight_multi_sheet_execution(
        db, nome, batch_id, evidencia, "gestor@teste.local",
        base_dir=tmp_path)
    assert pf["ok"] is False
    assert pf["gates"]["administrador_exato"]["ok"] is False
    pf_sem = preflight_multi_sheet_execution(
        db, nome, batch_id, evidencia, None, base_dir=tmp_path)
    assert pf_sem["gates"]["administrador_exato"]["ok"] is False
    with pytest.raises(MultiSheetError) as exc:
        execute_multi_sheet(
            db, nome, batch_id, confirmation_phrase_multiaba(batch_id),
            evidencia, admin_email="gestor@teste.local", base_dir=tmp_path)
    assert exc.value.codigo == "portoes_reprovados"
    assert contar(db, MigrationProvenance) == 0


def test_execucao_bloqueada_com_frase_errada(db, users, tmp_path):
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    with pytest.raises(MultiSheetError) as exc:
        execute_multi_sheet(
            db, nome, batch_id, "EXECUTAR MIGRACAO MULTIABA errada",
            evidencia, admin_email=ADMIN, base_dir=tmp_path)
    assert exc.value.codigo == "frase_de_confirmacao_incorreta"
    db.rollback()
    assert contar(db, MigrationProvenance) == 0
    assert contar(db, Person) == 0


# ------------------------------------------------- decisões e entidades


def test_execucao_completa_decisoes_ordem_e_entidades(db, users, tmp_path):
    antes_pessoas = contar(db, Person)
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    resultado = executar_ok(db, tmp_path, users, nome, batch_id, evidencia)
    assert resultado["ok"] is True
    assert resultado["manifesto"] == MANIFESTO_ESPERADO

    # decisão aprovada por telefone: exame E-001 vinculado à pessoa P-001
    pessoa_p1 = alias_id(db, "people", "crm_pacientes", "P-001")
    exame_e1 = db.get(SpirometryExam, alias_id(
        db, "spirometry_exams", "crm_espirometria", "E-001"))
    assert exame_e1.person_id == pessoa_p1

    # criar_pessoa: E-002 criou exatamente UMA pessoa determinística
    gamas = db.execute(select(Person).where(
        Person.nome_completo == "Pessoa Sintetica Gama")).scalars().all()
    assert len(gamas) == 1
    exame_e2 = db.get(SpirometryExam, alias_id(
        db, "spirometry_exams", "crm_espirometria", "E-002"))
    assert exame_e2.person_id == gamas[0].id

    # exclusão decidida (custo Pastore) registrada como exclusão
    exec_batch = db.get(ImportBatch, resultado["batch_execucao_id"])
    excluidas = db.execute(select(ImportRow).where(
        ImportRow.batch_id == exec_batch.id,
        ImportRow.status == "excluido",
        ImportRow.motivo == "excluido_por_decisao_humana",
    )).scalars().all()
    assert len(excluidas) == 1

    # não-contatar aplicado só à pessoa criada pelo lote
    pessoa_p2 = db.get(Person, alias_id(db, "people", "crm_pacientes", "P-002"))
    assert pessoa_p2.nao_contatar is True

    # lead sem pessoa: placeholder determinístico, nunca vínculo por nome
    lead2 = alias_id(db, "leads", "leads", "L-002")
    assert lead2 is not None
    assert contar(db, Person) == antes_pessoas + MANIFESTO_ESPERADO["people"]

    # ordem completa de dependência: proveniência monotônica por estágio
    provenancias = db.execute(
        select(MigrationProvenance)
        .where(MigrationProvenance.batch_id == exec_batch.id)
        .order_by(MigrationProvenance.ordem)
    ).scalars().all()
    estagios = [ORDEM_ENTIDADES[p.entidade] for p in provenancias]
    assert estagios == sorted(estagios)
    assert len(provenancias) == sum(MANIFESTO_ESPERADO.values())

    # toda linha criada tem proveniência completa
    for prov in provenancias:
        assert prov.source_domain
        assert len(prov.source_fingerprint) == 64
        assert prov.mapping_version == "m15-6b.1"
        assert len(prov.idempotency_key) == 64

    # PCMSO permanece excluído e não cria linha operacional
    assert not [p for p in provenancias
                if p.source_domain == "pcmso_historico"]
    pcmso_rows = db.execute(select(ImportRow).where(
        ImportRow.batch_id == exec_batch.id,
        ImportRow.motivo == "pcmso_historico_excluido",
    )).scalars().all()
    assert len(pcmso_rows) == 1


def test_financeiro_fonte_unica_na_execucao(db, users, tmp_path):
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    executar_ok(db, tmp_path, users, nome, batch_id, evidencia)
    entradas = db.execute(select(FinancialEntry)).scalars().all()
    assert len(entradas) == 1
    assert float(entradas[0].valor) == 100.0
    assert entradas[0].spirometry_exam_id == alias_id(
        db, "spirometry_exams", "crm_espirometria", "E-001")
    proveniencia = db.execute(select(MigrationProvenance).where(
        MigrationProvenance.entidade == "financial_entries")).scalars().all()
    assert all(p.source_domain == "financeiro_lancamentos"
               for p in proveniencia)
    # config Pastore migrada SEM nenhum campo monetário
    configs = db.execute(select(PartnerUnitConfig)).scalars().all()
    assert len(configs) == 1
    colunas = {c.name for c in PartnerUnitConfig.__table__.columns}
    assert not colunas & {"valor", "valor_exame_sem_bd", "repasse",
                          "custo", "receita"}


# ------------------------------------- transação, idempotência e rollback


def test_falha_no_meio_reverte_lote_inteiro(db, users, tmp_path, monkeypatch):
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    original = executor._instanciar

    def falha_sintetica(db_, op, refs, batch_id_):
        if op.entidade == "financial_entries":
            raise RuntimeError("falha sintetica no meio da execucao")
        return original(db_, op, refs, batch_id_)

    monkeypatch.setattr(executor, "_instanciar", falha_sintetica)
    with pytest.raises(RuntimeError):
        execute_multi_sheet(
            db, nome, batch_id, confirmation_phrase_multiaba(batch_id),
            evidencia, admin_email=ADMIN, base_dir=tmp_path)
    db.rollback()  # transação única: o chamador descarta TUDO
    assert contar(db, MigrationProvenance) == 0
    assert contar(db, Person) == 0
    assert contar(db, FinancialEntry) == 0
    assert db.execute(select(ImportBatch).where(
        ImportBatch.modo == "executado")).scalars().first() is None


def test_reexecucao_do_mesmo_lote_cria_zero_linhas(db, users, tmp_path):
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    executar_ok(db, tmp_path, users, nome, batch_id, evidencia)
    pessoas = contar(db, Person)
    proveniencias = contar(db, MigrationProvenance)
    aliases = contar(db, LegacyAlias)
    segunda = execute_multi_sheet(
        db, nome, batch_id, confirmation_phrase_multiaba(batch_id),
        evidencia, admin_email=ADMIN, base_dir=tmp_path)
    assert segunda["status"] == "ja_executado"
    assert segunda["novas_linhas"] == 0
    assert contar(db, Person) == pessoas
    assert contar(db, MigrationProvenance) == proveniencias
    assert contar(db, LegacyAlias) == aliases


def test_reconciliacao_fecha_e_conclui_o_lote(db, users, tmp_path):
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    resultado = executar_ok(db, tmp_path, users, nome, batch_id, evidencia)
    exec_id = resultado["batch_execucao_id"]
    assert db.get(ImportBatch, exec_id).status == \
        "executado_aguardando_reconciliacao"
    recon = reconcile_multi_sheet_execution(
        db, exec_id, base_dir=tmp_path, user_id=users["admin"].id)
    db.commit()
    assert recon["ok"] is True
    assert all(c["ok"] for c in recon["checks"].values())
    assert db.get(ImportBatch, exec_id).status == "concluido"
    # status sanitizado exposto (UI): reconciliação fechada, sem PII
    status = multi_sheet_status(db, batch_id)
    assert status["execution"]["reconciliacao_ok"] is True
    assert status["execution"]["status_execucao"] == "concluido"
    assert status["execution_allowed"] is False


def test_discrepancia_impede_conclusao(db, users, tmp_path):
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    resultado = executar_ok(db, tmp_path, users, nome, batch_id, evidencia)
    exec_id = resultado["batch_execucao_id"]
    consent = db.execute(select(Consent)).scalars().first()
    db.delete(consent)  # discrepância artificial pós-execução
    db.flush()
    recon = reconcile_multi_sheet_execution(
        db, exec_id, base_dir=tmp_path, user_id=users["admin"].id)
    db.commit()
    assert recon["ok"] is False
    assert "linhas_criadas_presentes" in recon["problemas"]
    assert db.get(ImportBatch, exec_id).status == "executado_com_divergencia"


def test_rollback_seletivo_seguro_e_fail_closed(db, users, tmp_path):
    # registro PREEXISTENTE nunca pode ser tocado pela execução nem rollback
    preexistente = Person(
        public_code="PES-999999", nome_completo="Pessoa Preexistente 001",
        nome_normalizado="pessoa preexistente 001")
    db.add(preexistente)
    db.commit()
    contagem_pre = contar(db, Person)

    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    resultado = executar_ok(db, tmp_path, users, nome, batch_id, evidencia)
    exec_id = resultado["batch_execucao_id"]

    plano_rb = rollback_plan_report(db, exec_id)
    assert plano_rb["linhas_a_remover"] == sum(MANIFESTO_ESPERADO.values())
    assert [item["entidade"] for item in plano_rb["ordem_reversa"]] == \
        sorted([item["entidade"] for item in plano_rb["ordem_reversa"]],
               key=lambda e: ORDEM_ENTIDADES[e], reverse=True)

    # dependência EXTERNA: lançamento manual sobre exame criado pelo lote
    exame = alias_id(db, "spirometry_exams", "crm_espirometria", "E-002")
    externo = FinancialEntry(
        public_code="FIN-999999", tipo="receita", valor="10.00",
        spirometry_exam_id=exame)
    db.add(externo)
    db.commit()
    with pytest.raises(MultiSheetError) as exc:
        rollback_multi_sheet_execution(
            db, exec_id, rollback_phrase_multiaba(exec_id), ADMIN)
    assert exc.value.codigo == "rollback_nao_provado_seguro"
    db.rollback()

    # removida a dependência externa, o rollback prova-se seguro
    db.delete(db.get(FinancialEntry, externo.id))
    db.commit()
    with pytest.raises(MultiSheetError):
        rollback_multi_sheet_execution(db, exec_id, "frase errada", ADMIN)
    db.rollback()
    evidencia_rb = rollback_multi_sheet_execution(
        db, exec_id, rollback_phrase_multiaba(exec_id), ADMIN)
    db.commit()
    assert evidencia_rb["total_removido"] == sum(MANIFESTO_ESPERADO.values())
    assert db.get(ImportBatch, exec_id).status == "revertido"
    # linhas do lote removidas; preexistente intacto
    assert contar(db, Person) == contagem_pre
    intacto = db.get(Person, preexistente.id)
    assert intacto is not None
    assert intacto.nome_completo == "Pessoa Preexistente 001"
    assert contar(db, FinancialEntry) == 0
    assert contar(db, LegacyAlias) == 0
    # proveniência preservada como evidência de rollback
    assert contar(db, MigrationProvenance) == sum(MANIFESTO_ESPERADO.values())


def test_registros_preexistentes_viram_ja_existentes(db, users, tmp_path):
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    executar_ok(db, tmp_path, users, nome, batch_id, evidencia)
    pessoas = contar(db, Person)
    # NOVO envelope com o mesmo conteúdo: novo dry-run marca já-existentes
    nome2 = write_raw_envelope(
        tmp_path, executable_sheets(), envelope_name="segundo-envelope.json")
    resultado = run_multi_sheet_dry_run(
        db, nome2, base_dir=tmp_path, user_id=users["admin"].id)
    db.commit()
    assert resultado["totals"]["already_existing"] > 0
    plano = build_final_plan(
        db, nome2, resultado["batch_id"], base_dir=tmp_path)
    novas = {op.idempotency_key for op in plano.ops} - {
        p.idempotency_key
        for p in db.execute(select(MigrationProvenance)).scalars().all()
    }
    assert novas == set()  # repetir não criaria NENHUMA linha nova
    assert contar(db, Person) == pessoas


# ------------------------------------------ superfícies e higiene de PII


def test_nao_existe_caminho_de_execute_por_api_ou_browser():
    app = create_app()
    rotas = [r for r in app.routes if hasattr(r, "path")]
    for rota in rotas:
        assert "executar" not in rota.path
        assert "execute" not in rota.path
    post_multiaba = [
        r for r in rotas
        if "multiaba" in r.path and "POST" in getattr(r, "methods", set())
    ]
    assert post_multiaba, "rotas multiaba de leitura/decisão devem existir"
    for rota in post_multiaba:
        assert rota.path.endswith("/dry-run") or rota.path.endswith("/decisao")


def test_cli_executar_multiaba_sem_frase_por_argumento(capsys):
    for comando in ("preflight-execucao-multiaba", "plano-rollback-multiaba",
                    "executar-multiaba", "reconciliar-multiaba",
                    "rollback-multiaba"):
        with pytest.raises(SystemExit) as exc:
            cli.main(["migracao", comando, "--help"])
        assert exc.value.code == 0
        ajuda = capsys.readouterr().out
        assert "--frase" not in ajuda
        assert "--confirmacao" not in ajuda


def test_cli_revisar_multiaba_aceita_decisao_explicita_de_duplicata(
    engine, db, users, tmp_path, monkeypatch, capsys,
):
    from sqlalchemy.orm import sessionmaker

    nome, batch_id = dry_run(
        db, tmp_path, sheets_com_duplicata_pastore(), users)
    item = next(
        i for i in multi_sheet_review_queue(db, batch_id)["items"]
        if i["category"] == "duplicata_execucao"
    )
    reviewer_email = users["gestor"].email
    db.close()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "_session", lambda: SessionLocal())
    assert cli.main([
        "migracao", "revisar-multiaba",
        "--batch", batch_id,
        "--referencia", item["private_reference_token"],
        "--decisao", "manter_primeira",
        "--mapping-version", item["mapping_version"],
        "--email", reviewer_email,
        "--json",
    ]) == 0
    saida = json.loads(capsys.readouterr().out)
    assert saida["decision_state"] == "manter_primeira"


def test_fluxo_completo_pela_cli_com_frase_interativa(
        engine, db, users, tmp_path, monkeypatch, capsys):
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "_session", lambda: SessionLocal())
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))

    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    assert cli.main([
        "migracao", "preflight-execucao-multiaba", "--envelope", nome,
        "--batch", batch_id, "--backup-evidencia", evidencia,
        "--email", ADMIN, "--json"]) == 0
    saida = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert saida["ok"] is True
    assert saida["frase_confirmacao"] == confirmation_phrase_multiaba(batch_id)

    # frase digitada interativamente — nunca por argumento/variável
    monkeypatch.setattr(
        "builtins.input", lambda *_: confirmation_phrase_multiaba(batch_id))
    assert cli.main([
        "migracao", "executar-multiaba", "--envelope", nome,
        "--batch", batch_id, "--backup-evidencia", evidencia,
        "--email", ADMIN, "--json"]) == 0
    execucao = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert execucao["ok"] is True

    assert cli.main([
        "migracao", "reconciliar-multiaba",
        "--batch-execucao", execucao["batch_execucao_id"],
        "--email", ADMIN, "--json"]) == 0
    recon = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert recon["ok"] is True

    assert cli.main([
        "migracao", "plano-rollback-multiaba",
        "--batch-execucao", execucao["batch_execucao_id"], "--json"]) == 0
    plano_rb = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert plano_rb["linhas_a_remover"] == sum(MANIFESTO_ESPERADO.values())


def test_sem_pii_em_logs_parametros_e_decisoes(db, users, tmp_path):
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    resultado = executar_ok(db, tmp_path, users, nome, batch_id, evidencia)
    reconcile_multi_sheet_execution(
        db, resultado["batch_execucao_id"], base_dir=tmp_path,
        user_id=users["admin"].id)
    db.commit()
    trilha = json.dumps([
        {"acao": log.acao, "detalhes": log.detalhes}
        for log in db.execute(select(AuditLog)).scalars().all()
    ], ensure_ascii=False)
    params = json.dumps([
        batch.params
        for batch in db.execute(select(ImportBatch)).scalars().all()
    ], ensure_ascii=False)
    for texto in (trilha, params):
        assert "Sintetica" not in texto
        assert "Sintetico" not in texto
        assert "0000-9101" not in texto
        assert "0000-9102" not in texto
        assert "0000-9103" not in texto
