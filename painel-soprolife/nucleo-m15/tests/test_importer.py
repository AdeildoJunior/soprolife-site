"""Importador: dry-run puro, execução idempotente, rollback e identidade."""

import pathlib

import pytest
from sqlalchemy import func, select

from app.importer import csv_import
from app.importer.csv_import import run_import, sha256_bytes
from app.models import (
    Followup,
    ImportBatch,
    ImportRow,
    Lead,
    LegacyAlias,
    Person,
    SpirometryExam,
)

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"


def _count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def test_dry_run_nao_escreve_nada(db):
    content = (FIXTURES / "leads.csv").read_bytes()
    report = run_import(db, "leads", "leads.csv", content, execute=False)
    db.commit()
    assert report["status"] == "dry_run"
    assert report["total"] == 7
    assert report["validas"] == 4          # 3 ok + 1 ambíguo
    assert report["rejeitadas"] == 3       # sem nome, PCMSO, duplicado
    assert report["ambiguas"] == 1         # telefone repetido do L-0001
    assert _count(db, Person) == 0
    assert _count(db, Lead) == 0
    assert _count(db, ImportBatch) == 0
    assert _count(db, ImportRow) == 0


def test_execute_importa_e_preserva_ids_legados(db):
    content = (FIXTURES / "leads.csv").read_bytes()
    report = run_import(db, "leads", "leads.csv", content, execute=True)
    db.commit()
    assert report["status"] == "executado"
    assert report["sha256"] == sha256_bytes(content)
    assert _count(db, Lead) == 4
    assert _count(db, Person) == 4
    alias = db.execute(
        select(LegacyAlias).where(
            LegacyAlias.entidade == "leads", LegacyAlias.legacy_id == "L-0001"
        )
    ).scalar_one()
    assert alias.legacy_source == "leads_csv"
    lead = db.get(Lead, alias.entity_id)
    assert lead.legacy_id == "L-0001"
    assert lead.import_batch_id == report["batch_id"]
    # data incompleta normalizada com metadados
    alias2 = db.execute(
        select(LegacyAlias).where(
            LegacyAlias.entidade == "leads", LegacyAlias.legacy_id == "L-0002"
        )
    ).scalar_one()
    lead2 = db.get(Lead, alias2.entity_id)
    assert lead2.data_primeiro_contato.isoformat() == "2026-06-01"
    assert lead2.data_primeiro_contato_original == "06/2026"
    assert lead2.data_primeiro_contato_dia_assumido is True


def test_reexecucao_idempotente(db):
    content = (FIXTURES / "leads.csv").read_bytes()
    run_import(db, "leads", "leads.csv", content, execute=True)
    db.commit()
    people_before = _count(db, Person)
    report = run_import(db, "leads", "leads.csv", content, execute=True)
    db.commit()
    assert report["status"] == "ja_importado"
    assert _count(db, Person) == people_before
    assert _count(db, ImportBatch) == 1


def test_pcmso_rejeitado_no_import(db):
    content = (FIXTURES / "leads.csv").read_bytes()
    report = run_import(db, "leads", "leads.csv", content, execute=True)
    db.commit()
    motivos = [r["motivo"] for r in report["rejeicoes_amostra"]]
    assert "pcmso_fora_da_operacao" in motivos


def test_pacientes_depois_espirometrias_vincula_por_alias(db):
    run_import(db, "crm_pacientes", "p.csv",
               (FIXTURES / "crm_pacientes.csv").read_bytes(), execute=True)
    db.commit()
    report = run_import(db, "crm_espirometria", "e.csv",
                        (FIXTURES / "crm_espirometria.csv").read_bytes(), execute=True)
    db.commit()
    assert report["validas"] == 3
    assert report["rejeitadas"] == 2  # sem paciente + PCMSO
    # ESP-0001 vinculado à MESMA pessoa do PAC-0001 (via alias, não por nome)
    pac_alias = db.execute(select(LegacyAlias).where(
        LegacyAlias.entidade == "people", LegacyAlias.legacy_id == "PAC-0001"
    )).scalar_one()
    esp_alias = db.execute(select(LegacyAlias).where(
        LegacyAlias.entidade == "spirometry_exams", LegacyAlias.legacy_id == "ESP-0001"
    )).scalar_one()
    exam = db.get(SpirometryExam, esp_alias.entity_id)
    assert exam.person_id == pac_alias.entity_id
    # exame realizado gera follow-up de 6 meses
    fup = db.execute(select(Followup).where(
        Followup.origem_id == exam.id, Followup.tipo == "pos_exame"
    )).scalar_one()
    assert fup.due_date.isoformat() == "2026-07-10"  # 10/01/2026 + 6 meses


def test_candidato_identidade_sem_fusao(db):
    run_import(db, "leads", "leads.csv",
               (FIXTURES / "leads.csv").read_bytes(), execute=True)
    db.commit()
    from app.models import IdentityCandidate

    candidates = db.execute(select(IdentityCandidate)).scalars().all()
    assert len(candidates) >= 1
    assert all(c.status == "pendente" for c in candidates)
    # o lead ambíguo virou pessoa própria (sem fusão silenciosa)
    assert _count(db, Person) == 4


def test_rollback_completo_em_erro(db, monkeypatch):
    content = (FIXTURES / "leads.csv").read_bytes()
    original = csv_import._handle_leads
    calls = {"n": 0}

    def explode(ctx, row, import_row_id):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("falha simulada no meio do lote")
        return original(ctx, row, import_row_id)

    monkeypatch.setitem(csv_import.HANDLERS, "leads", explode)
    with pytest.raises(RuntimeError):
        run_import(db, "leads", "leads.csv", content, execute=True)
    db.rollback()
    assert _count(db, Person) == 0
    assert _count(db, Lead) == 0
    assert _count(db, ImportBatch) == 0


def test_espirometria_sem_pacientes_falha_fechado(db):
    """Ordem segura: exame com paciente_id SEM pacientes importados -> rejeição."""
    report = run_import(db, "crm_espirometria", "e.csv",
                        (FIXTURES / "crm_espirometria.csv").read_bytes(), execute=True)
    db.commit()
    motivos = [r["motivo"] for r in report["rejeicoes_amostra"]]
    assert "requer_importacao_previa_de_pacientes" in motivos
    # a linha só-com-nome vira pessoa placeholder marcada
    placeholders = db.execute(select(Person).where(
        Person.observacao.like("%PLACEHOLDER-IMPORT%")
    )).scalars().all()
    assert len(placeholders) == 1


def test_paciente_reimportado_e_enriquecido(db):
    """Reimportar paciente já existente preenche lacunas auditavelmente."""
    csv1 = "paciente_id,nome\nPAC-E1,Paciente Enriquecer 001\n".encode()
    run_import(db, "crm_pacientes", "p1.csv", csv1, execute=True)
    db.commit()
    csv2 = ("paciente_id,nome,telefone,data_nascimento\n"
            "PAC-E1,Paciente Enriquecer 001,(21) 0000-7001,10/02/1980\n").encode()
    report = run_import(db, "crm_pacientes", "p2.csv", csv2, execute=True)
    db.commit()
    assert report["ja_existentes"] == 1
    assert report["enriquecidos"] == 1
    from app.models import PersonContact

    person = db.execute(select(Person).where(Person.legacy_id == "PAC-E1")).scalar_one()
    assert person.data_nascimento is not None
    contato = db.execute(select(PersonContact).where(
        PersonContact.person_id == person.id
    )).scalar_one()
    assert contato.valor_normalizado == "552100007001"
    linha = db.execute(select(ImportRow).where(ImportRow.motivo == "paciente_enriquecido")).scalar_one()
    assert linha.entity_id == person.id


def test_data_invalida_rejeitada(db):
    csv_data = ("paciente_id,nome,data_nascimento\n"
                "PAC-D1,Paciente Data Ruim,31/02/2026\n").encode()
    report = run_import(db, "crm_pacientes", "d.csv", csv_data, execute=True)
    db.commit()
    assert report["validas"] == 0
    assert report["rejeitadas"] == 1
    assert report["rejeicoes_amostra"][0]["motivo"] == "data_invalida"


def test_encoding_invalido_falha_fechado(db):
    conteudo = "paciente_id,nome\nPAC-X,José\n".encode("latin-1")
    with pytest.raises(csv_import.ImportFormatError, match="Encoding inválido"):
        run_import(db, "crm_pacientes", "latin.csv", conteudo, execute=True)
    db.rollback()
    assert _count(db, Person) == 0


def test_csv_injection_rejeitada(db):
    csv_data = ("paciente_id,nome,observacao\n"
                "PAC-I1,=HYPERLINK(http://evil),formula no nome\n"
                "PAC-I2,Paciente Ok,@import perigoso\n"
                "PAC-I3,Paciente Bom,observacao normal\n").encode()
    report = run_import(db, "crm_pacientes", "inj.csv", csv_data, execute=True)
    db.commit()
    assert report["validas"] == 1
    assert report["rejeitadas"] == 2
    motivos = [r["motivo"] for r in report["rejeicoes_amostra"]]
    assert all(m.startswith("csv_injection") for m in motivos)
    # nomes com fórmula nunca entram no banco
    assert _count(db, Person) == 1


def test_telefone_negativo_nao_e_injection(db):
    """'+55...' e valores numéricos com sinal não são fórmula."""
    csv_data = ("paciente_id,nome,telefone\n"
                "PAC-T1,Paciente Telefone Mais,+55 21 0000-7002\n").encode()
    report = run_import(db, "crm_pacientes", "tel.csv", csv_data, execute=True)
    db.commit()
    assert report["validas"] == 1


def test_limite_de_tamanho(db):
    grande = b"a" * (csv_import.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(csv_import.ImportFormatError, match="excede"):
        run_import(db, "crm_pacientes", "grande.csv", grande, execute=False)
    db.rollback()


def test_limite_de_colunas(db):
    header = ",".join(f"c{i}" for i in range(csv_import.MAX_COLS + 1))
    with pytest.raises(csv_import.ImportFormatError, match="colunas"):
        run_import(db, "crm_pacientes", "cols.csv",
                   (header + "\n").encode(), execute=False)
    db.rollback()


def test_csv_malformado_falha_fechado(db):
    with pytest.raises(csv_import.ImportFormatError, match="mais campos"):
        run_import(
            db,
            "crm_pacientes",
            "extra.csv",
            b"paciente_id,nome\nPAC-1,Paciente Teste,campo-extra\n",
            execute=False,
        )
    with pytest.raises(csv_import.ImportFormatError, match="duplicadas"):
        run_import(
            db,
            "crm_pacientes",
            "duplicada.csv",
            b"paciente_id,nome,nome\nPAC-1,Paciente,Repetido\n",
            execute=False,
        )
    with pytest.raises(csv_import.ImportFormatError, match="NUL"):
        run_import(
            db,
            "crm_pacientes",
            "nul.csv",
            b"paciente_id,nome\nPAC-1,Paciente\x00Teste\n",
            execute=False,
        )


def test_source_name_sem_caminho(db):
    """Path traversal: relatório/registro guardam só o basename."""
    report = run_import(db, "crm_pacientes", "../../../etc/passwd",
                        (FIXTURES / "crm_pacientes.csv").read_bytes(), execute=False)
    assert report["source_name"] == "passwd"


def test_contatos_b2b(db):
    report = run_import(db, "contatos_b2b", "b2b.csv",
                        (FIXTURES / "contatos_b2b.csv").read_bytes(), execute=True)
    db.commit()
    from app.models import Partner, PartnerContact

    assert report["validas"] == 2
    assert report["rejeitadas"] == 2  # PCMSO + sem clínica
    assert _count(db, Partner) == 2
    assert _count(db, PartnerContact) == 1


def test_consultas_importadas_apos_pacientes(db):
    """Ordem segura: pacientes primeiro, depois consultas."""
    run_import(db, "crm_pacientes", "p.csv",
               (FIXTURES / "crm_pacientes.csv").read_bytes(), execute=True)
    db.commit()
    report = run_import(db, "crm_consultas", "c.csv",
                        (FIXTURES / "crm_consultas.csv").read_bytes(), execute=True)
    db.commit()
    from app.models import Consultation

    assert report["validas"] == 2
    assert report["rejeitadas"] == 1
    assert _count(db, Consultation) == 2


def test_relatorios_json_e_md(db, tmp_path):
    from app.importer.csv_import import write_reports

    content = (FIXTURES / "leads.csv").read_bytes()
    report = run_import(db, "leads", "leads.csv", content, execute=False)
    json_path, md_path = write_reports(report, tmp_path)
    assert pathlib.Path(json_path).is_file()
    text = pathlib.Path(md_path).read_text(encoding="utf-8")
    assert "SHA-256" in text
    assert "DRY-RUN" in text
