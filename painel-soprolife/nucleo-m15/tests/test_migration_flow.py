"""Fluxo governado M15.6A: dry-run sem escrita, portões, aprovação humana,
execução explícita, idempotência, reconciliação e evidência de rollback."""

import pytest
from sqlalchemy import func, select

from app.importer.csv_import import run_import
from app.migration.mapping import MAPPING_VERSION
from app.migration.report import neutralize_cell, render_csv
from app.migration.service import (
    MigrationError,
    approve_snapshot,
    confirmation_phrase,
    dry_run_snapshot,
    execute_snapshot,
    preflight,
    reconcile_snapshot,
    register_snapshot,
    revoke_approval,
    snapshot_status,
)
from app.models import (
    IdentityCandidate,
    ImportBatch,
    ImportSnapshot,
    Lead,
    Person,
)

from tests._migration_fixtures import (
    LEADS_HEADERS,
    csv_bytes,
    write_backup_evidence,
    write_snapshot_files,
)


def _registra(db, base, **kwargs):
    name = write_snapshot_files(base, **kwargs)
    result = register_snapshot(db, name, base)
    db.commit()
    return result


def _dry(db, base, snapshot_id):
    report = dry_run_snapshot(db, snapshot_id, base)
    db.commit()
    return report


def _aprova(db, base, snapshot_id):
    st = snapshot_status(db, snapshot_id)
    result = approve_snapshot(
        db, snapshot_id, st["sha256"], MAPPING_VERSION,
        st["dry_run"]["batch_id"])
    db.commit()
    return result


def _executa(db, base, snapshot_id, evidencia=None, frase=None, batch=None):
    st = snapshot_status(db, snapshot_id)
    evidencia = evidencia or write_backup_evidence(base)
    report = execute_snapshot(
        db, snapshot_id,
        batch or st["dry_run"]["batch_id"],
        frase if frase is not None else confirmation_phrase(snapshot_id),
        evidencia, base,
    )
    db.commit()
    return report


# ------------------------------------------------------------------- dry-run

def test_dry_run_nao_grava_registro_operacional(db, tmp_path):
    reg = _registra(db, tmp_path)
    report = _dry(db, tmp_path, reg["snapshot_id"])
    assert report["criticos"] == 0
    assert report["validas"] == 3
    assert report["aviso"].startswith("DRY-RUN")
    # nenhuma pessoa/lead criada; apenas snapshot + lote de staging
    assert db.execute(select(func.count()).select_from(Person)).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(Lead)).scalar_one() == 0
    batch = db.get(ImportBatch, report["batch_id"])
    assert batch.modo == "dry_run"
    assert batch.params["snapshot_id"] == reg["snapshot_id"]
    snap = db.get(ImportSnapshot, reg["snapshot_id"])
    assert snap.status == "dry_run_ok"


def test_dry_run_deterministico(db, tmp_path):
    reg = _registra(db, tmp_path)
    r1 = _dry(db, tmp_path, reg["snapshot_id"])
    r2 = _dry(db, tmp_path, reg["snapshot_id"])
    campos = ("total", "validas", "rejeitadas", "excluidas", "ambiguas",
              "criticos", "motivos", "perfil_datas", "rejeicoes_amostra")
    assert {c: r1[c] for c in campos} == {c: r2[c] for c in campos}


def test_perfil_de_datas_parciais_e_invalidas(db, tmp_path):
    rows = [
        ["L1", "Lead Teste A", "(21) 0000-9201", "01/06/2026", "site", ""],
        ["L2", "Lead Teste B", "(21) 0000-9202", "06/2026", "site", ""],
        ["L3", "Lead Teste C", "(21) 0000-9203", "2026", "site", ""],
        ["L4", "Lead Teste D", "(21) 0000-9204", "32/13/2026", "site", ""],
        ["L5", "Lead Teste E", "(21) 0000-9205", "", "site", ""],
    ]
    reg = _registra(db, tmp_path, rows=rows)
    report = _dry(db, tmp_path, reg["snapshot_id"])
    perfil = report["perfil_datas"]["colunas"]["data_primeiro_contato"]
    assert perfil["dia"] == 1
    assert perfil["mes"] == 1
    assert perfil["ano"] == 1
    assert perfil["desconhecida"] == 1  # data inválida = aviso de parser
    assert perfil["vazia"] == 1
    assert perfil["dia_assumido"] == 2  # mês e ano assumem dia 1 SEMPRE marcado
    assert report["avisos"] >= 1
    # data inválida não vira NULL silencioso: linha rejeitada como crítica
    assert report["criticos"] == 1
    assert report["motivos"].get("data_invalida") == 1


def test_pcmso_e_excluido_nunca_critico(db, tmp_path):
    rows = [
        ["L1", "Lead Teste A", "(21) 0000-9301", "01/06/2026", "site", ""],
        ["L2", "Lead Teste B", "(21) 0000-9302", "01/06/2026", "site",
         "exame PCMSO da empresa"],
    ]
    reg = _registra(db, tmp_path, rows=rows)
    report = _dry(db, tmp_path, reg["snapshot_id"])
    assert report["excluidas"] == 1
    assert report["criticos"] == 0  # exclusão deliberada não bloqueia aprovação
    assert report["motivos"].get("pcmso_fora_da_operacao") == 1


def test_pcmso_historico_categoria_excluida(db, tmp_path):
    reg = _registra(
        db, tmp_path, source_type="pcmso_historico",
        headers=["col_a", "col_b"], rows=[["a", "b"], ["c", "d"]])
    report = _dry(db, tmp_path, reg["snapshot_id"])
    assert report["excluidas"] == 2
    assert report["validas"] == 0
    assert report["execucao"] == "excluida_historica"
    pf = preflight(db, reg["snapshot_id"], base_dir=tmp_path)
    assert pf["gates"]["execucao_disponivel"]["ok"] is False
    with pytest.raises(MigrationError) as exc:
        _aprova(db, tmp_path, reg["snapshot_id"])
    assert exc.value.codigo == "execucao_indisponivel_para_source_type"


def test_financeiro_fronteira_e_pii(db, tmp_path):
    headers = ["lancamento_id", "tipo", "categoria", "descricao", "valor",
               "data_competencia", "exame_id"]
    rows = [
        ["F1", "receita", "espirometria", "atendimento avulso", "180.00",
         "01/06/2026", "E001"],
        ["F2", "receita", "espirometria", "exame de Maria Silva", "200.00",
         "01/06/2026", ""],  # PII (nome) na descrição financeira
        ["F3", "receita", "", "repasse combinado", "-50", "01/06/2026", ""],
        ["F4", "consulta", "", "tipo invalido", "100", "01/06/2026", ""],
        ["F1", "receita", "", "id duplicado", "10", "01/06/2026", ""],
    ]
    reg = _registra(
        db, tmp_path, source_type="financeiro_lancamentos",
        headers=headers, rows=rows)
    report = _dry(db, tmp_path, reg["snapshot_id"])
    assert report["proposta_entidade"] == "financial_entries"
    assert report["validas"] == 1
    assert report["motivos"].get("pii_em_descricao_financeira") == 1
    assert report["motivos"].get("valor_invalido") == 1
    assert report["motivos"].get("tipo_financeiro_invalido") == 1
    assert report["motivos"].get("legacy_id_repetido_no_arquivo") == 1
    # execução de financeiro segue bloqueada (mapeamento preparado)
    pf = preflight(db, reg["snapshot_id"], base_dir=tmp_path)
    assert pf["gates"]["execucao_disponivel"]["ok"] is False


# -------------------------------------------------------------------- portões

def test_preflight_matriz_de_portoes(db, tmp_path):
    reg = _registra(db, tmp_path)
    sid = reg["snapshot_id"]
    pf = preflight(db, sid, base_dir=tmp_path)
    assert pf["ok"] is False
    assert pf["gates"]["dry_run_realizado"]["ok"] is False
    assert pf["gates"]["backup_validado"]["ok"] is False
    assert pf["gates"]["aprovacao_humana"]["ok"] is False

    _dry(db, tmp_path, sid)
    ev = write_backup_evidence(tmp_path)
    pf = preflight(db, sid, ev, tmp_path)
    assert pf["gates"]["dry_run_realizado"]["ok"]
    assert pf["gates"]["sem_erros_criticos"]["ok"]
    assert pf["gates"]["checksum_inalterado"]["ok"]
    assert pf["gates"]["mapping_version_revisada"]["ok"]
    assert pf["gates"]["execucao_disponivel"]["ok"]
    assert pf["gates"]["backup_validado"]["ok"]
    assert pf["gates"]["evidencia_rollback_disponivel"]["ok"]
    assert pf["gates"]["idempotencia"]["ok"]
    assert pf["gates"]["aprovacao_humana"]["ok"] is False  # nunca automática
    assert pf["ok"] is False

    _aprova(db, tmp_path, sid)
    pf = preflight(db, sid, ev, tmp_path)
    assert pf["ok"] is True
    assert pf["frase_confirmacao"] == confirmation_phrase(sid)


def test_aprovacao_exige_identificadores_exatos(db, tmp_path):
    reg = _registra(db, tmp_path)
    sid = reg["snapshot_id"]
    dr = _dry(db, tmp_path, sid)
    with pytest.raises(MigrationError) as exc:
        approve_snapshot(db, sid, "f" * 64, MAPPING_VERSION, dr["batch_id"])
    assert exc.value.codigo == "sha256_nao_confere"
    with pytest.raises(MigrationError) as exc:
        approve_snapshot(db, sid, reg["sha256"], "outra", dr["batch_id"])
    assert exc.value.codigo == "mapping_version_nao_confere"
    with pytest.raises(MigrationError) as exc:
        approve_snapshot(db, sid, reg["sha256"], MAPPING_VERSION, "x" * 36)
    assert exc.value.codigo == "dry_run_batch_nao_confere"
    _aprova(db, tmp_path, sid)
    with pytest.raises(MigrationError) as exc:
        _aprova(db, tmp_path, sid)
    assert exc.value.codigo == "ja_aprovado"  # repetição nunca duplica


def test_aprovacao_bloqueada_com_erros_criticos(db, tmp_path):
    rows = [["L1", "Lead Teste A", "(21) 0000-9401", "data-quebrada", "site", ""]]
    reg = _registra(db, tmp_path, rows=rows)
    _dry(db, tmp_path, reg["snapshot_id"])
    with pytest.raises(MigrationError) as exc:
        _aprova(db, tmp_path, reg["snapshot_id"])
    assert exc.value.codigo == "erros_criticos_nao_resolvidos"


def test_execucao_bloqueada_sem_aprovacao(db, tmp_path):
    reg = _registra(db, tmp_path)
    _dry(db, tmp_path, reg["snapshot_id"])
    with pytest.raises(MigrationError) as exc:
        _executa(db, tmp_path, reg["snapshot_id"])
    assert exc.value.codigo == "portoes_reprovados"
    assert "aprovacao_humana" in exc.value.detalhes


def test_execucao_bloqueada_com_erros_criticos(db, tmp_path):
    rows = [["L1", "Lead Teste A", "(21) 0000-9402", "invalida", "site", ""]]
    reg = _registra(db, tmp_path, rows=rows)
    _dry(db, tmp_path, reg["snapshot_id"])
    with pytest.raises(MigrationError) as exc:
        _executa(db, tmp_path, reg["snapshot_id"])
    assert exc.value.codigo == "portoes_reprovados"
    assert "sem_erros_criticos" in exc.value.detalhes


def test_execucao_exige_frase_e_lote_exatos(db, tmp_path):
    reg = _registra(db, tmp_path)
    sid = reg["snapshot_id"]
    _dry(db, tmp_path, sid)
    _aprova(db, tmp_path, sid)
    with pytest.raises(MigrationError) as exc:
        _executa(db, tmp_path, sid, frase="EXECUTAR")
    assert exc.value.codigo == "frase_de_confirmacao_incorreta"
    with pytest.raises(MigrationError) as exc:
        _executa(db, tmp_path, sid, batch="y" * 36)
    assert exc.value.codigo == "batch_id_nao_confere"
    assert db.execute(select(func.count()).select_from(Lead)).scalar_one() == 0


def test_checksum_alterado_apos_registro_bloqueia_tudo(db, tmp_path):
    reg = _registra(db, tmp_path)
    sid = reg["snapshot_id"]
    _dry(db, tmp_path, sid)
    # snapshot deixa de ser imutável: arquivo muda depois do registro
    (tmp_path / "leads-snap.csv").write_bytes(
        csv_bytes(LEADS_HEADERS, [["L9", "Lead Teste X",
                                   "(21) 0000-9999", "01/06/2026", "site", ""]]))
    pf = preflight(db, sid, base_dir=tmp_path)
    assert pf["gates"]["checksum_inalterado"]["ok"] is False
    with pytest.raises(MigrationError):
        dry_run_snapshot(db, sid, tmp_path)


# ------------------------------------------------------------------- execução

def test_execucao_completa_e_idempotente(db, tmp_path):
    reg = _registra(db, tmp_path)
    sid = reg["snapshot_id"]
    _dry(db, tmp_path, sid)
    _aprova(db, tmp_path, sid)
    report = _executa(db, tmp_path, sid)
    assert report["status"] == "executado"
    assert db.execute(select(func.count()).select_from(Lead)).scalar_one() == 3
    assert db.execute(select(func.count()).select_from(Person)).scalar_one() == 3
    batch = db.get(ImportBatch, report["batch_id"])
    assert batch.modo == "executado"
    assert batch.params["tabelas_antes"]["leads"] == 0
    assert batch.params["tabelas_depois"]["leads"] == 3
    assert batch.params["evidencia_rollback"]["arquivo_backup"]
    snap = db.get(ImportSnapshot, sid)
    assert snap.status == "executado"
    assert snap.execute_batch_id == batch.id

    # reexecução: portão de idempotência bloqueia ANTES de qualquer escrita
    with pytest.raises(MigrationError) as exc:
        _executa(db, tmp_path, sid)
    assert exc.value.codigo == "portoes_reprovados"
    assert "idempotencia" in exc.value.detalhes
    assert db.execute(select(func.count()).select_from(Lead)).scalar_one() == 3


def test_revogacao_de_aprovacao_bloqueia_execucao(db, tmp_path):
    reg = _registra(db, tmp_path)
    sid = reg["snapshot_id"]
    _dry(db, tmp_path, sid)
    _aprova(db, tmp_path, sid)
    revoke_approval(db, sid)
    db.commit()
    with pytest.raises(MigrationError) as exc:
        _executa(db, tmp_path, sid)
    assert "aprovacao_humana" in exc.value.detalhes


# -------------------------------------------------------------- reconciliação

def test_reconciliacao_deterministica(db, tmp_path):
    reg = _registra(db, tmp_path)
    sid = reg["snapshot_id"]
    _dry(db, tmp_path, sid)
    _aprova(db, tmp_path, sid)
    _executa(db, tmp_path, sid)

    r1 = reconcile_snapshot(db, sid, tmp_path)
    db.commit()
    r2 = reconcile_snapshot(db, sid, tmp_path)
    db.commit()
    assert r1 == r2  # releitura determinística
    assert r1["fonte_aceitas"] == 3
    assert r1["fonte_rejeitadas"] == 0
    assert r1["alvo_inseridos"].get("leads") == 3
    assert r1["identidades_nao_resolvidas"] == 0
    # aliases nascem de IDs explícitos: leads têm id (L00x); pessoas criadas
    # a partir deles não têm paciente_id na fonte, logo sem alias próprio
    assert r1["relacoes_alvo"] == {"leads": 3}
    assert r1["checksum"]["confere"] is True
    assert r1["tabelas_antes"]["leads"] == 0
    assert r1["tabelas_depois"]["leads"] == 3
    assert r1["evidencia_rollback"]["local"].endswith("backup-teste.dump")
    assert db.get(ImportSnapshot, sid).status == "reconciliado"


def test_reconciliacao_exige_execucao(db, tmp_path):
    reg = _registra(db, tmp_path)
    with pytest.raises(MigrationError) as exc:
        reconcile_snapshot(db, reg["snapshot_id"], tmp_path)
    assert exc.value.codigo == "snapshot_nao_executado"


# ------------------------------------------------- identidade e duplicidades

def test_nome_igual_nunca_vincula_sozinho(db, tmp_path):
    reg = _registra(db, tmp_path)
    sid = reg["snapshot_id"]
    _dry(db, tmp_path, sid)
    _aprova(db, tmp_path, sid)
    _executa(db, tmp_path, sid)

    # segundo snapshot com MESMO nome e outro telefone: cria pessoa nova
    # + candidato de identidade — nunca fusão nem vínculo por nome
    rows = [["L777", "Lead Teste 001", "(21) 0000-9777", "02/06/2026",
             "site", ""]]
    reg2 = _registra(
        db, tmp_path, rows=rows, arquivo="leads-snap2.csv",
        sheet="aba-teste-2")
    sid2 = reg2["snapshot_id"]
    dr2 = _dry(db, tmp_path, sid2)
    assert dr2["ambiguas"] == 1
    _aprova(db, tmp_path, sid2)
    _executa(db, tmp_path, sid2, evidencia=write_backup_evidence(
        tmp_path, name="ev2.json", backup_name="bk2.dump"))
    assert db.execute(select(func.count()).select_from(Person)).scalar_one() == 4
    pendentes = db.execute(
        select(IdentityCandidate).where(IdentityCandidate.status == "pendente")
    ).scalars().all()
    assert len(pendentes) == 1
    assert pendentes[0].motivo == "nome_igual"


def test_telefone_igual_gera_candidato_ambiguidade(db, tmp_path):
    reg = _registra(db, tmp_path)
    sid = reg["snapshot_id"]
    _dry(db, tmp_path, sid)
    _aprova(db, tmp_path, sid)
    _executa(db, tmp_path, sid)
    rows = [["L888", "Lead Teste Outro Nome", "(21) 0000-9101", "02/06/2026",
             "site", ""]]  # telefone do Lead Teste 001
    reg2 = _registra(db, tmp_path, rows=rows, arquivo="leads-snap3.csv",
                     sheet="aba-teste-3")
    dr2 = _dry(db, tmp_path, reg2["snapshot_id"])
    assert dr2["ambiguas"] == 1
    assert dr2["candidatos_identidade"] >= 1


def test_id_explicito_e_deterministico_e_duplicatas_bloqueadas(db):
    pacientes = csv_bytes(
        ["paciente_id", "nome", "telefone", "data_nascimento"],
        [["P001", "Paciente Teste 001", "(21) 0000-9501", "01/01/1980"]])
    run_import(db, "crm_pacientes", "pacientes.csv", pacientes, execute=True)
    db.commit()

    exames = csv_bytes(
        ["exame_id", "paciente_id", "data_exame", "status"],
        [["E001", "P001", "01/06/2026", "Realizado"]])
    r1 = run_import(db, "crm_espirometria", "exames.csv", exames, execute=True)
    db.commit()
    assert r1["validas"] == 1

    # mesmo exame_id em ARQUIVO diferente: idempotente via alias, sem duplicar
    exames2 = csv_bytes(
        ["exame_id", "paciente_id", "data_exame", "status", "observacao"],
        [["E001", "P001", "01/06/2026", "Realizado", "reenvio"]])
    r2 = run_import(db, "crm_espirometria", "exames2.csv", exames2, execute=True)
    db.commit()
    assert r2["validas"] == 0
    assert r2["ja_existentes"] == 1

    consultas = csv_bytes(
        ["consulta_id", "paciente_id", "data_consulta", "status"],
        [["C001", "P001", "02/06/2026", "Realizada"]])
    run_import(db, "crm_consultas", "consultas.csv", consultas, execute=True)
    db.commit()
    consultas2 = csv_bytes(
        ["consulta_id", "paciente_id", "data_consulta", "status", "obs"],
        [["C001", "P001", "02/06/2026", "Realizada", "reenvio"]])
    r4 = run_import(db, "crm_consultas", "consultas2.csv", consultas2,
                    execute=True)
    db.commit()
    assert r4["validas"] == 0
    assert r4["ja_existentes"] == 1
    # pessoa única com exame + consulta: vínculo por ID explícito
    assert db.execute(select(func.count()).select_from(Person)).scalar_one() == 1


# ------------------------------------------------------ relatório sanitizado

def test_neutralizacao_de_formula_em_relatorio():
    assert neutralize_cell("=SUM(A1:A2)") == "'=SUM(A1:A2)"
    assert neutralize_cell("+55 21 0000") == "'+55 21 0000"
    assert neutralize_cell("-5") == "'-5"
    assert neutralize_cell("@import") == "'@import"
    assert neutralize_cell("\tx") == "'\tx"
    assert neutralize_cell("texto normal") == "texto normal"
    assert neutralize_cell(None) == ""

    csv_text = render_csv({"motivo": "=HYPERLINK(evil)", "n": 1})
    assert "'=HYPERLINK(evil)" in csv_text
    assert "\n=HYPERLINK" not in csv_text
