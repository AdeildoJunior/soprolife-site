"""M15.6B: adapters reais sanitizados, envelope e staging multiaba sintético."""

import json
import os

import pytest
from sqlalchemy import func, select

from app.migration.adapters import (
    ADAPTERS,
    ADAPTERS_VERSION,
    PAPEL_REF_MONETARIA_BLOQUEADA,
    PAPEL_VALOR_MONETARIO,
    adapt_row,
    check_real_headers,
)
from app.migration.manifest import ManifestError
from app.migration.multisheet import (
    MultiSheetError,
    build_multi_sheet_report,
    decide_multi_sheet_review,
    multi_sheet_review_queue,
    run_multi_sheet_dry_run,
)
from app.migration.rawsnapshot import load_raw_envelope
from app.migration.staging import StagingError, stage_multi_sheet
from app.models import (
    Consultation,
    FinancialEntry,
    Followup,
    ImportBatch,
    Lead,
    Partner,
    Person,
    SpirometryExam,
)
from tests._multisheet_fixtures import (
    RAW_ENVELOPE_SCHEMA,
    headers_for,
    minimal_row,
    representative_sheets,
    synthetic_row,
    write_raw_envelope,
)


def test_todos_os_layouts_sanitizados_tem_adapter_explicito():
    assert set(ADAPTERS) == {
        "crm_pacientes", "crm_espirometria", "crm_consultas", "leads",
        "crm_clinicas", "contatos_b2b", "followup_whatsapp",
        "financeiro_lancamentos", "pastore_config", "pastore_atendimentos",
        "pastore_custos", "pcmso_historico",
    }
    for kind, adapter in ADAPTERS.items():
        assert adapter.layout_version.startswith("m15-6b.")
        assert not check_real_headers(adapter, headers_for(kind))


def test_primeiro_nome_paciente_adapta_sem_perder_proveniencia():
    adapter = ADAPTERS["crm_pacientes"]
    headers = tuple(headers_for("crm_pacientes"))
    source = synthetic_row(
        "crm_pacientes", paciente_id="P-001", primeiro_nome="Nome Sintetico"
    )
    row = adapt_row(adapter, headers, tuple(source[h] for h in headers))
    assert row.campos["nome_pessoa"] == "Nome Sintetico"
    assert row.proveniencia["nome_pessoa"] == "primeiro_nome"


@pytest.mark.parametrize("mode", ["missing", "unknown", "duplicate"])
def test_header_mismatch_falha_fechado(mode):
    adapter = ADAPTERS["crm_pacientes"]
    headers = headers_for("crm_pacientes")
    if mode == "missing":
        headers.remove("primeiro_nome")
    elif mode == "unknown":
        headers.append("campo_critico_novo")
    else:
        headers.append("paciente_id")
    assert check_real_headers(adapter, headers)


def test_fronteira_monetaria_eh_explicita():
    for kind, adapter in ADAPTERS.items():
        monetary = [field for field in adapter.campos
                    if field.papel == PAPEL_VALOR_MONETARIO]
        if kind == "financeiro_lancamentos":
            assert monetary
        else:
            assert monetary == []
    assert any(
        field.papel == PAPEL_REF_MONETARIA_BLOQUEADA
        for field in ADAPTERS["pastore_config"].campos
    )


def test_envelope_valido_preserva_ordem_linha_e_origem(tmp_path):
    name = write_raw_envelope(tmp_path, [{
        "kind": "crm_pacientes",
        "alias": "Pacientes Sinteticos",
        "rows": [minimal_row("crm_pacientes")],
    }])
    envelope = load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))
    assert envelope.schema_version == RAW_ENVELOPE_SCHEMA
    assert envelope.sheets[0].sheet_alias == "Pacientes Sinteticos"
    assert envelope.sheets[0].rows[0].linha == 2


@pytest.mark.parametrize("target", ["envelope", "sheet"])
def test_raw_snapshot_schema_mismatch(target, tmp_path):
    def envelope_mutator(payload):
        if target == "envelope":
            payload["schema_version"] = "m15.raw-envelope.999"

    def sheet_mutator(_index, payload):
        if target == "sheet":
            payload["schema_version"] = "m15.raw-sheet.999"

    name = write_raw_envelope(
        tmp_path, [{"kind": "crm_pacientes", "rows": []}],
        envelope_mutator=envelope_mutator, sheet_mutator=sheet_mutator,
    )
    with pytest.raises(ManifestError, match="schema_version"):
        load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))


def test_checksum_mismatch_antes_do_parse(tmp_path):
    name = write_raw_envelope(
        tmp_path, [{"kind": "crm_pacientes", "rows": []}]
    )
    (tmp_path / "sheet-01.json").write_text("json quebrado", encoding="utf-8")
    with pytest.raises(ManifestError, match="checksum_divergente"):
        load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))


def test_path_escape_e_symlink_rejeitados(tmp_path):
    def mutate(payload):
        payload["sheets"][0]["arquivo"] = "../escape.json"

    name = write_raw_envelope(
        tmp_path, [{"kind": "crm_pacientes", "rows": []}],
        envelope_mutator=mutate,
    )
    with pytest.raises(ManifestError, match="fora_do_diretorio"):
        load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))

    other = tmp_path / "real-envelope.json"
    other.write_text("{}", encoding="utf-8")
    link = tmp_path / "link-envelope.json"
    link.symlink_to(other)
    with pytest.raises(ManifestError, match="link_simbolico"):
        load_raw_envelope(link.name, tmp_path, frozenset(ADAPTERS))


def test_ordem_de_linhas_nao_deterministica_rejeitada(tmp_path):
    def mutate(_index, payload):
        payload["rows"] = [
            {"linha": 3, "valores": [""] * len(payload["headers"])},
            {"linha": 2, "valores": [""] * len(payload["headers"])},
        ]

    name = write_raw_envelope(
        tmp_path, [{"kind": "crm_pacientes", "rows": []}],
        sheet_mutator=mutate,
    )
    with pytest.raises(ManifestError, match="ordem_de_linhas"):
        load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))


def test_leitor_ignora_arquivo_extra_e_rejeita_referenciado_ausente(tmp_path):
    name = write_raw_envelope(
        tmp_path, [{"kind": "crm_pacientes", "rows": []}]
    )
    (tmp_path / "nao-referenciado.json").write_text(
        "conteudo que nao deve ser lido", encoding="utf-8"
    )
    assert load_raw_envelope(name, tmp_path, frozenset(ADAPTERS)).sheets
    (tmp_path / "sheet-01.json").unlink()
    with pytest.raises(ManifestError, match="arquivo_de_aba_ausente"):
        load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))


def test_json_invalido_celula_nao_textual_e_estrutura_extra_rejeitados(tmp_path):
    (tmp_path / "quebrado.json").write_text("{", encoding="utf-8")
    with pytest.raises(ManifestError, match="json_invalido"):
        load_raw_envelope("quebrado.json", tmp_path, frozenset(ADAPTERS))

    def non_text(_index, payload):
        payload["rows"] = [{
            "linha": 2,
            "valores": [123] + [""] * (len(payload["headers"]) - 1),
        }]

    name = write_raw_envelope(
        tmp_path, [{"kind": "crm_pacientes", "rows": []}],
        sheet_mutator=non_text, envelope_name="non-text.json",
    )
    with pytest.raises(ManifestError, match="celula_nao_textual"):
        load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))

    def extra(payload):
        payload["campo_desconhecido"] = True

    name = write_raw_envelope(
        tmp_path, [{"kind": "crm_pacientes", "rows": []}],
        envelope_mutator=extra, envelope_name="extra-field.json",
    )
    with pytest.raises(ManifestError, match="estrutura_de_envelope"):
        load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))


def test_mapping_version_desconhecida_falha_fechado(tmp_path):
    name = write_raw_envelope(
        tmp_path, [{"kind": "crm_pacientes", "rows": []}],
        mapping_version="m15-6b.inexistente",
    )
    envelope = load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))
    with pytest.raises(StagingError, match="mapping_version_nao_suportada"):
        stage_multi_sheet(envelope)


def _full_plan(tmp_path):
    name = write_raw_envelope(tmp_path, representative_sheets())
    envelope = load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))
    return name, stage_multi_sheet(envelope)


def test_multiaba_ordem_identidade_datas_pastore_financeiro_e_fechamento(tmp_path):
    _name, plan = _full_plan(tmp_path)
    kinds = [item["kind"] for item in plan["ordem_processamento"]]
    assert kinds.index("pcmso_historico") < kinds.index("crm_clinicas")
    assert kinds.index("crm_clinicas") < kinds.index("contatos_b2b")
    assert kinds.index("crm_pacientes") < kinds.index("crm_espirometria")
    assert kinds[-1] in ("financeiro_lancamentos", "pastore_custos")

    assert plan["perfil_identidade"]["vinculos_deterministicos"] == 2
    assert plan["perfil_identidade"]["candidatos_por_telefone"] >= 1
    assert plan["perfil_identidade"]["correspondencias_somente_nome_ignoradas"] >= 1
    assert plan["perfil_identidade"]["exames_orfaos"] == 1
    assert plan["perfil_identidade"]["consultas_orfas"] == 1
    assert plan["perfil_identidade"]["leads_sem_pessoa"] == 1

    dates = plan["perfil_datas"]["precisao_agregada"]
    assert dates["mes"] == 2
    assert dates["dia_assumido"] == 2
    pcmso_reviews = [
        item for item in plan["fila_revisao"]
        if item["categoria"] == "data_invalida_ativa"
        and item["aba"] == "Historico Sintetico"
    ]
    assert pcmso_reviews == []

    assert plan["propostas"]["restricoes_nao_contatar"] == 1
    assert plan["propostas"]["insercoes"]["partners"] == 2
    assert plan["propostas"]["referencias_monetarias_bloqueadas"] >= 3
    assert plan["cobertura_financeira"]["vinculadas_tecnicamente"] == 1
    assert plan["cobertura_financeira"]["nao_resolvidas"] == 1
    assert plan["propostas"]["insercoes"]["financial_entries"] == 1
    assert plan["reconciliacao_preview"]["fechamento_ok"] is True
    assert plan["reconciliacao_preview"]["registros_operacionais_gravados"] == 0
    assert sum(plan["totais"][state] for state in (
        "valida", "rejeitada", "excluida", "pendente_revisao",
        "duplicada", "ja_existente",
    )) == plan["totais"]["fonte_total"]


def test_invalid_active_date_cria_revisao_e_formula_eh_rejeitada(tmp_path):
    rows = [
        synthetic_row(
            "crm_pacientes", paciente_id="P-001", primeiro_nome="=2+2",
            data_cadastro="31/02/2026",
        )
    ]
    name = write_raw_envelope(
        tmp_path, [{"kind": "crm_pacientes", "rows": rows}]
    )
    plan = stage_multi_sheet(
        load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))
    )
    assert plan["totais"]["rejeitada"] == 1
    rendered = json.dumps(plan, ensure_ascii=False)
    assert "=2+2" not in rendered

    rows[0]["primeiro_nome"] = "Pessoa Sintetica"
    name = write_raw_envelope(
        tmp_path, [{"kind": "crm_pacientes", "rows": rows}],
        envelope_name="invalid-date-envelope.json",
    )
    plan = stage_multi_sheet(
        load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))
    )
    assert plan["totais"]["pendente_revisao"] == 1
    assert plan["fila_revisao_resumo"]["data_invalida_ativa"] == 1


def test_data_privada_preserva_original_normalizada_precisao_e_locale(tmp_path):
    name = write_raw_envelope(tmp_path, [{
        "kind": "crm_consultas",
        "rows": [synthetic_row(
            "crm_consultas", consulta_id="C-001", data_consulta="2026-07"
        )],
    }])
    plan = stage_multi_sheet(
        load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))
    )
    record = next(
        item for item in plan["registros_data_privados"]
        if item["original_text"] == "2026-07"
    )
    assert record == {
        "private_source_reference": record["private_source_reference"],
        "original_text": "2026-07",
        "normalized_date": "2026-07-01",
        "precision": "mes",
        "assumed_day": True,
        "locale": "pt-BR",
        "parser_warning": False,
    }


def test_report_sanitizado_deterministico_sem_pii(tmp_path):
    name = write_raw_envelope(tmp_path, representative_sheets())
    first = build_multi_sheet_report(name, tmp_path)
    second = build_multi_sheet_report(name, tmp_path)
    assert first == second
    rendered = json.dumps(first, ensure_ascii=False)
    for forbidden in (
        "Pessoa Sintetica Alpha", "(21) 0000-9101", "Pacientes Sinteticos",
        "Financeiro Sintetico", "valor_cobrado",
    ):
        assert forbidden not in rendered
    assert all(
        set(item) == {
            "private_reference_token", "category", "status", "mapping_version"
        }
        for item in first["review_queue"]
    )
    assert first["execution_allowed"] is False
    assert first["reconciliation_preview"]["fechamento_ok"] is True


def test_dry_run_persiste_apenas_resumo_e_replay_idempotente(db, tmp_path):
    name = write_raw_envelope(tmp_path, representative_sheets())
    models = (Person, Lead, SpirometryExam, Consultation, Followup, Partner,
              FinancialEntry)
    before = {
        model.__tablename__: db.scalar(select(func.count()).select_from(model))
        for model in models
    }
    first = run_multi_sheet_dry_run(db, name, tmp_path)
    db.commit()
    second = run_multi_sheet_dry_run(db, name, tmp_path)
    db.commit()
    after = {
        model.__tablename__: db.scalar(select(func.count()).select_from(model))
        for model in models
    }
    assert before == after
    assert first["batch_id"] == second["batch_id"]
    assert first["replay"] is False and second["replay"] is True
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 1


def test_revisao_humana_protegida_e_append_only(db, users, tmp_path):
    name = write_raw_envelope(tmp_path, representative_sheets())
    result = run_multi_sheet_dry_run(db, name, tmp_path, users["admin"].id)
    db.commit()
    queue = multi_sheet_review_queue(db, result["batch_id"])
    item = next(item for item in queue["items"] if item["status"] == "pendente")
    with pytest.raises(MultiSheetError, match="revisor_autenticado"):
        decide_multi_sheet_review(
            db, result["batch_id"], item["private_reference_token"],
            "resolvido", ADAPTERS_VERSION, None,
        )
    decision = decide_multi_sheet_review(
        db, result["batch_id"], item["private_reference_token"],
        "resolvido", ADAPTERS_VERSION, users["gestor"].id,
    )
    db.commit()
    assert decision["decision_state"] == "resolvido"
    updated = multi_sheet_review_queue(db, result["batch_id"])
    chosen = next(
        candidate for candidate in updated["items"]
        if candidate["private_reference_token"] == item["private_reference_token"]
    )
    assert chosen["decision_state"] == "resolvido"


def test_relacao_financeira_nao_pode_usar_nome_ou_descricao(tmp_path):
    sheets = [
        {"kind": "crm_espirometria", "rows": [
            synthetic_row("crm_espirometria", exame_id="E-001")
        ]},
        {"kind": "financeiro_lancamentos", "rows": [
            synthetic_row(
                "financeiro_lancamentos", id_lancamento="F-001",
                tipo_movimento="receita", servico="E-001",
                local_atendimento="E-001", valor_cobrado="10,00",
            )
        ]},
    ]
    name = write_raw_envelope(tmp_path, sheets)
    plan = stage_multi_sheet(
        load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))
    )
    assert plan["cobertura_financeira"]["nao_resolvidas"] == 1
    assert "financial_entries" not in plan["propostas"]["insercoes"]


def test_identificador_conflitante_e_alias_ja_existente_sao_contados(tmp_path):
    rows = [
        synthetic_row(
            "crm_pacientes", paciente_id="P-001", primeiro_nome="Pessoa Alpha"
        ),
        synthetic_row(
            "crm_pacientes", paciente_id="P-001", primeiro_nome="Pessoa Beta"
        ),
    ]
    name = write_raw_envelope(
        tmp_path, [{"kind": "crm_pacientes", "rows": rows}]
    )
    envelope = load_raw_envelope(name, tmp_path, frozenset(ADAPTERS))
    conflict = stage_multi_sheet(envelope)
    assert conflict["perfil_identidade"]["identificadores_conflitantes"] == 1
    assert conflict["totais"]["pendente_revisao"] == 1

    existing = stage_multi_sheet(
        envelope, existentes={("people", "crm_pacientes", "P-001")}
    )
    assert existing["totais"]["ja_existente"] == 2
