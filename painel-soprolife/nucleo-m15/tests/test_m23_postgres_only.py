"""M23 — PostgreSQL como fonte operacional exclusiva.

Prova, sem rede e sem credencial, que:

* os snapshots do painel nascem do banco, não de planilha;
* nenhum snapshot carrega PII;
* um payload inseguro aborta ANTES de tocar o disco (o snapshot válido
  anterior sobrevive);
* a escrita é atômica;
* nada de exemplo/demo é usado no lugar de dado operacional;
* Search Console e GA4 continuam declarados como integrações permitidas;
* as salvaguardas de Pastore e de conciliação financeira seguem valendo sobre
  os agregados exportados.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app import snapshots
from app.models import (
    AuditLog,
    FinancialEntry,
    Followup,
    Lead,
    Partner,
    PartnerSettlement,
    PartnerUnit,
    PartnerUnitConfig,
    Person,
    SpirometryExam,
    User,
)
from app.security import ensure_roles_exist, get_role, hash_password

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = REPO_ROOT / "painel-soprolife" / "core" / "contracts" / "data-source-mode.json"

# Mesmo módulo que scripts/check-access.sh importa em produção (mesmo
# sys.path.insert relativo à raiz do repo). Este teste usa o contrato REAL,
# não uma cópia fixture-only do allowlist — foi exatamente a divergência
# entre as duas que derrubou o primeiro deploy do M23.
sys.path.insert(0, str(REPO_ROOT / "painel-soprolife" / "scripts"))
from audit_summary_contract import (  # noqa: E402
    ALLOWED_EVENT_KEYS,
    validate_auditoria_payload,
)


def _make_user_with_role(db, *, email: str, role: str) -> User:
    ensure_roles_exist(db)
    user = User(email=email, nome=f"Teste {role}", password_hash=hash_password("senha-teste-123"))
    user.roles.append(get_role(db, role))
    db.add(user)
    db.commit()
    return user


# --------------------------------------------------------------------- fixtures

@pytest.fixture()
def povoado(db):
    """Banco mínimo mas representativo: pessoa, lead, parceiro, exame, receita."""
    pessoa = Person(id="p-1", public_code="PES-000001",
                    nome_completo="Paciente Sintetico",
                    nome_normalizado="paciente sintetico")
    db.add(pessoa)
    db.flush()

    db.add(Lead(
        id="l-1", public_code="LEA-000001", person_id="p-1",
        origem="instagram", canal_entrada="direct",
        servico_interesse="espirometria", etapa="novo",
        data_primeiro_contato=date(2026, 6, 1),
        responsavel="Comercial",
    ))

    parceiro = Partner(id="pa-1", public_code="CLI-000001", nome="Pastore",
                       tipo="clinica", status="ativa", cidade="Rio de Janeiro")
    db.add(parceiro)
    unidade = PartnerUnit(id="u-1", public_code="UNI-000001", partner_id="pa-1",
                          nome="Pastore Ipanema", bairro="Ipanema", ativo=True)
    db.add(unidade)
    db.flush()
    db.add(PartnerUnitConfig(id="uc-1", partner_unit_id="u-1",
                             dia_semana="Terça-feira", horario_inicio="08:00",
                             horario_fim="12:00", status="ativa"))

    db.add(SpirometryExam(
        id="e-1", public_code="ESP-000001", person_id="p-1",
        data_exame=date(2026, 7, 14), status="Liberado",
        partner_id="pa-1", partner_unit_id="u-1", broncodilatador=False,
    ))

    db.add(FinancialEntry(
        id="f-1", public_code="LAN-000001", tipo="receita",
        categoria="espirometria", valor=Decimal("238.58"),
        status="Recebido", data_recebimento=date(2026, 6, 20),
        spirometry_exam_id=None,
    ))

    db.add(Followup(id="fu-1", public_code="FUP-000001", person_id="p-1",
                    tipo="pos_exame", status="pendente",
                    due_date=date.today() - timedelta(days=2)))

    db.add(AuditLog(acao="lead.criado", entidade="leads", entidade_id="l-1",
                    ts_utc=datetime.now(timezone.utc),
                    detalhes={"segredo": "nunca deve sair"}))
    db.commit()
    return db


# ----------------------------------------------------------------- fonte canônica

def test_contrato_declara_postgresql_only():
    contrato = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contrato["modo"] == "postgresql_only"
    assert contrato["fonte_canonica"]["tipo"] == "postgresql"
    proibido = contrato["proibido_em_runtime"]
    assert proibido["google_sheets_leitura"] is True
    assert proibido["google_sheets_escrita"] is True
    assert proibido["apps_script_web_app"] is True
    assert proibido["fallback_de_exemplo_para_dado_operacional"] is True


def test_search_console_e_ga4_continuam_permitidos():
    contrato = json.loads(CONTRACT.read_text(encoding="utf-8"))
    ids = {i["id"] for i in contrato["integracoes_permitidas"]}
    assert {"search_console", "ga4"} <= ids
    for item in contrato["integracoes_permitidas"]:
        # São leitura de marketing — nunca dado de negócio.
        assert item["dado_de_negocio"] is False
        assert item["acesso"] == "somente leitura"


def test_dominios_de_negocio_sao_exclusivos_do_postgres():
    contrato = json.loads(CONTRACT.read_text(encoding="utf-8"))
    dominios = set(contrato["dominios_exclusivos_postgres"])
    # Todo domínio citado no M23 precisa estar declarado.
    assert {
        "pacientes", "clinicas", "leads", "espirometrias", "consultas",
        "lancamentos_financeiros", "parcerias", "pastore_exames",
        "pastore_fechamentos", "crm_followup", "auditoria", "documentos",
    } <= dominios


def test_nenhuma_unit_systemd_libera_o_escape_manual():
    systemd = REPO_ROOT / "painel-soprolife" / "systemd"
    for unit in list(systemd.glob("*.service")) + list(systemd.glob("*.timer")):
        for linha in unit.read_text(encoding="utf-8").splitlines():
            if linha.lstrip().startswith("#"):
                continue
            assert not linha.startswith(
                "Environment=SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION"
            ), f"{unit.name} libera o escape de migração legada"


# ------------------------------------------------------------------- exportação

def test_exporta_todos_os_snapshots_esperados(povoado, tmp_path):
    resultado = snapshots.export_snapshots(povoado, tmp_path, write=True)
    assert resultado["fonte"] == "postgresql_nucleo_m15"
    for nome in snapshots.SNAPSHOT_FILES:
        assert (tmp_path / nome).exists(), nome


def test_dry_run_nao_escreve_nada(povoado, tmp_path):
    # Diretório próprio: tmp_path já hospeda o SQLite da fixture de banco.
    saida = tmp_path / "saida-dry-run"
    saida.mkdir()
    resultado = snapshots.export_snapshots(povoado, saida, write=False)
    assert resultado["modo"] == "dry-run"
    assert list(saida.iterdir()) == []


def test_todo_snapshot_se_declara_seguro_e_derivado_do_banco(povoado, tmp_path):
    snapshots.export_snapshots(povoado, tmp_path, write=True)
    for nome in snapshots.SNAPSHOT_FILES:
        payload = json.loads((tmp_path / nome).read_text(encoding="utf-8"))
        source = payload.get("source") or {}
        assert source.get("safeToDisplay") is True, nome
        assert source.get("containsPersonalData") is False, nome
        assert source["type"].startswith("postgresql_nucleo_m15"), nome
        assert source["official_source"] == "PostgreSQL (Núcleo M15)", nome


def test_nenhum_snapshot_carrega_pii(povoado, tmp_path):
    snapshots.export_snapshots(povoado, tmp_path, write=True)
    for nome in snapshots.SNAPSHOT_FILES:
        texto = (tmp_path / nome).read_text(encoding="utf-8")
        # Nome de PACIENTE nunca sai; nome de EMPRESA parceira é institucional.
        assert "Paciente Sintetico" not in texto, nome
        for proibido in ("cpf", "nunca deve sair"):
            assert proibido not in texto.lower(), f"{nome}: {proibido}"
        # A palavra pode aparecer em nota explicativa; a CHAVE, nunca.
        assert '"telefone"' not in texto, nome


def test_leads_exportam_codigo_publico_sem_identificacao(povoado, tmp_path):
    payload = snapshots.build_leads_summary(povoado)
    assert payload["leads"][0]["lead_id"] == "LEA-000001"
    assert payload["leads"][0]["etapa"] == "novo"
    for lead in payload["leads"]:
        assert "nome" not in lead
        assert "person_id" not in lead


def test_auditoria_nunca_exporta_detalhes(povoado, tmp_path):
    payload = snapshots.build_auditoria_summary(povoado)
    assert payload["stats"]["total_eventos"] == 1
    # Nenhum evento exportado carrega a chave 'detalhes' nem seu conteúdo,
    # e nenhuma chave sai fora do contrato REAL validado por check-access.sh
    # (não uma cópia fixture-only do allowlist — ver audit_summary_contract).
    for evento in payload["ultimos_eventos"]:
        assert set(evento) <= ALLOWED_EVENT_KEYS, set(evento) - ALLOWED_EVENT_KEYS
    assert "nunca deve sair" not in json.dumps(payload, ensure_ascii=False)


def test_auditoria_summary_passa_no_contrato_real_do_check_access(povoado, tmp_path):
    """Regressão do M23: o primeiro deploy falhou porque o exportador e o
    allowlist REAL de scripts/check-access.sh divergiram em silêncio. Este
    teste roda o exportador de verdade e valida a saída com o MESMO
    validador que check-access.sh usa em produção — não uma cópia."""
    snapshots.export_snapshots(povoado, tmp_path, write=True)
    caminho = tmp_path / "auditoria-summary.local.json"
    payload = json.loads(caminho.read_text(encoding="utf-8"))

    erros = validate_auditoria_payload(payload)
    assert erros == [], erros

    for evento in payload["ultimos_eventos"]:
        assert set(evento) <= ALLOWED_EVENT_KEYS, set(evento) - ALLOWED_EVENT_KEYS


def test_auditoria_contrato_real_rejeita_chave_extra():
    """Prova a causa exata da falha do primeiro deploy: um evento com o
    schema antigo/quebrado ({'acao', 'entidade', 'ts'}) precisa ser
    REJEITADO pelo contrato real, exatamente como aconteceu em produção."""
    payload_quebrado = {
        "source": {"safeToDisplay": True, "containsPersonalData": False},
        "ultimos_eventos": [
            {"acao": "lead.criado", "entidade": "leads", "ts": "2026-01-01T00:00:00+00:00"},
        ],
    }
    erros = validate_auditoria_payload(payload_quebrado)
    assert erros, "o schema quebrado do primeiro deploy deveria falhar o contrato real"
    assert any("entidade" in e and "ts" in e for e in erros)


def test_auditoria_contrato_real_aceita_apenas_chaves_permitidas():
    """Um evento com uma chave qualquer fora da allowlist falha, mesmo que
    as demais chaves estejam corretas — sem exceção silenciosa."""
    payload = {
        "source": {"safeToDisplay": True, "containsPersonalData": False},
        "ultimos_eventos": [
            {"acao": "lead.criado", "entidade_tipo": "leads", "entidade_id": "l-1",
             "operador": "operacional", "resultado": "ok",
             "timestamp": "2026-01-01T00:00:00+00:00",
             "campo_nao_permitido": "qualquer coisa"},
        ],
    }
    erros = validate_auditoria_payload(payload)
    assert erros
    assert any("campo_nao_permitido" in e for e in erros)


def test_auditoria_operador_e_papel_institucional_nunca_uuid_ou_nome(db):
    """'operador' vem do papel institucional (admin/gestor/operacional/
    leitura) do autor do evento — nunca o UUID interno nem o nome pessoal
    do usuário, que jamais podem sair no summary seguro."""
    usuario = _make_user_with_role(db, email="operador.teste@soprolife.local",
                                   role="operacional")
    db.add(AuditLog(acao="followup.criado", entidade="followups",
                    entidade_id="fu-9", user_id=usuario.id,
                    ts_utc=datetime.now(timezone.utc)))
    db.commit()

    payload = snapshots.build_auditoria_summary(db)
    eventos = payload["ultimos_eventos"]
    assert len(eventos) == 1
    assert eventos[0]["operador"] == "operacional"
    assert payload["stats"]["por_operador"] == {"operacional": 1}

    texto = json.dumps(payload, ensure_ascii=False)
    assert usuario.id not in texto
    assert usuario.email not in texto
    assert usuario.nome not in texto

    erros = validate_auditoria_payload(payload)
    assert erros == [], erros


def test_auditoria_operador_ausente_quando_sem_usuario_ou_papel(db):
    """Evento sem user_id: 'operador' não tem fonte confiável e não é
    inventado — a chave fica ausente (o contrato permite eventos parciais,
    só proíbe chaves extras)."""
    db.add(AuditLog(acao="auth.token_emitido", entidade="users",
                    entidade_id="u-1", ts_utc=datetime.now(timezone.utc)))
    db.commit()

    payload = snapshots.build_auditoria_summary(db)
    evento = payload["ultimos_eventos"][0]
    assert "operador" not in evento
    assert payload["stats"]["por_operador"] == {}


def test_auditoria_resultado_nao_fabrica_sucesso_para_falha(db):
    """'resultado' é derivado do texto REAL de 'acao' já gravado pela API —
    nunca 'ok' fabricado quando a própria ação sinaliza falha/rejeição."""
    agora = datetime.now(timezone.utc)
    db.add(AuditLog(acao="auth.falha", ts_utc=agora))
    db.add(AuditLog(acao="pcmso.rejeitado", entidade="attendances",
                    entidade_id="a-1", ts_utc=agora))
    db.add(AuditLog(acao="followup.criado", entidade="followups",
                    entidade_id="fu-1", ts_utc=agora))
    db.commit()

    payload = snapshots.build_auditoria_summary(db)
    por_resultado = {e["acao"]: e["resultado"] for e in payload["ultimos_eventos"]}
    assert por_resultado["auth.falha"] == "falha"
    assert por_resultado["pcmso.rejeitado"] == "falha"
    assert por_resultado["followup.criado"] == "ok"
    assert payload["stats"]["erros"] == 2


def test_financeiro_deriva_apenas_de_financial_entries(povoado):
    payload = snapshots.build_financeiro_summary(povoado)
    assert payload["totais"]["receita_recebida"] == 238.58
    assert payload["receita_exames"] == 238.58
    # Saldo bancário não é derivável do banco operacional: nunca fabricar 0,00.
    assert payload["saldo_operacional"] is None
    # Sem tabela de preço canônica, não se inventa valor base.
    assert payload["valor_base_exame"] is None


def test_ticket_medio_nao_e_inventado_sem_exame_pago(povoado):
    payload = snapshots.build_financeiro_summary(povoado)
    # O único lançamento não está vinculado a exame — nada de média fictícia.
    assert payload["exames_pagos"] == 0
    assert payload["ticket_medio_real"] is None


# ------------------------------------------------------------ salvaguardas M22

def test_pastore_nao_infere_valor_nem_repasse(povoado):
    payload = snapshots.build_pastore_summary(povoado)
    assert payload["disponivel"] is True
    assert payload["parceria"]["nome"] == "Pastore"
    params = payload["financeiro_parametros"]
    assert params["valor_exame_sem_broncodilatador"] is None
    assert params["valor_exame_com_broncodilatador"] is None
    assert params["repasse_percentual_pastore"] is None
    # Sem fechamento, não existe receita confirmada.
    assert payload["kpis"]["receita_confirmada"] is None


def test_pastore_so_conta_receita_de_fechamento_recebido(povoado):
    povoado.add(PartnerSettlement(
        id="ps-1", partner_id="pa-1", partner_unit_id="u-1",
        competencia=date(2026, 7, 1), status="aberto", valor_total=None,
    ))
    povoado.commit()
    payload = snapshots.build_pastore_summary(povoado)
    # Fechamento aberto e sem valor não vira receita.
    assert payload["kpis"]["receita_confirmada"] == 0.0
    assert payload["kpis"]["fechamentos"] == {"aberto": 1}


def test_pastore_ausente_falha_honestamente(db):
    payload = snapshots.build_pastore_summary(db)
    assert payload["disponivel"] is False
    assert payload["parceria"] is None


def test_agenda_pastore_vem_da_configuracao_da_unidade(povoado):
    payload = snapshots.build_pastore_summary(povoado)
    agenda = payload["agenda"]
    assert len(agenda) == 1
    assert agenda[0]["unidade"] == "Pastore Ipanema"
    assert agenda[0]["dia_semana"] == "Terça-feira"
    assert agenda[0]["horario"] == "08:00–12:00"


# ---------------------------------------------------------------- fail-closed

def test_payload_inseguro_aborta_antes_de_gravar(povoado, tmp_path, monkeypatch):
    anterior = tmp_path / "leads-summary.local.json"
    snapshots.export_snapshots(povoado, tmp_path, write=True)
    valido = anterior.read_text(encoding="utf-8")

    def vazado(_db):
        return {
            "source": {"type": "postgresql_nucleo_m15_leads_summary",
                       "official_source": "PostgreSQL (Núcleo M15)",
                       "generator": snapshots.GENERATOR,
                       "safeToDisplay": True, "containsPersonalData": False,
                       "containsHealthData": False, "generatedAt": "2026-07-25T00:00:00+00:00"},
            "leads": [{"lead_id": "LEA-000001", "telefone": "(21) 0000-9001"}],
        }

    monkeypatch.setitem(snapshots.BUILDERS, "leads-summary.local.json", vazado)
    with pytest.raises(ValueError, match="Snapshot inseguro"):
        snapshots.export_snapshots(povoado, tmp_path, write=True)

    # O snapshot válido anterior continua intacto no disco.
    assert anterior.read_text(encoding="utf-8") == valido


def test_escrita_e_atomica_sem_temporario_residual(povoado, tmp_path):
    snapshots.export_snapshots(povoado, tmp_path, write=True)
    residuais = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert residuais == []


def test_guarda_de_pii_compartilhada_e_obrigatoria(povoado, tmp_path, monkeypatch):
    """Sem a guarda do painel, a exportação falha — nunca grava 'no escuro'."""
    monkeypatch.setattr(snapshots, "_PII_GUARD_PATH", tmp_path / "inexistente.py")
    with pytest.raises(RuntimeError, match="Guarda de PII"):
        snapshots.export_snapshots(povoado, tmp_path, write=True)


def test_banco_vazio_gera_zeros_reais_e_nao_exemplos(db, tmp_path):
    snapshots.export_snapshots(db, tmp_path, write=True)
    leads = json.loads((tmp_path / "leads-summary.local.json").read_text(encoding="utf-8"))
    resumo = json.loads((tmp_path / "resumo-dashboard.local.json").read_text(encoding="utf-8"))
    assert leads["leads"] == []
    valores = {c["key"]: c["value"] for c in resumo["cards"]}
    # Zero real do banco — nunca os números do resumo.json de demonstração.
    assert valores["totalLeads"] == 0
    assert valores["leadsNovos"] == 0
    assert valores["examesEspirometriaRealizados"] == 0
    assert valores["clinicasCadastradas"] == 0


def test_cards_do_resumo_respeitam_a_allowlist_do_check_access(povoado):
    """check-access.sh só aceita key/label/value e uma lista fechada de chaves."""
    allowlist = {
        "totalLeads", "leadsNovos", "leadsAgendados", "leadsConcluidos",
        "clinicasCadastradas", "tarefasPendentes", "receitaPrevista",
        "receitaRecebida", "conteudosPlanejados", "eventosAgendados",
        "pacientesEmAcompanhamento", "examesEspirometriaRealizados",
        "teleconsultasRealizadas", "followupsPendentes",
        "lembretesWhatsAppPendentes", "recorrenciasAtivas", "consultasPrevistas",
    }
    payload = snapshots.build_resumo_dashboard(povoado)
    for card in payload["cards"]:
        assert set(card) == {"key", "label", "value"}, card
        assert card["key"] in allowlist, card["key"]


def test_crm_clinicas_respeita_a_allowlist_do_check_access(povoado):
    """check-access.sh rejeita qualquer campo fora desta lista fechada."""
    allowlist = {
        "clinica_id", "nome_clinica", "bairro", "regiao", "tipo_clinica",
        "etapa", "ultima_interacao", "tem_proxima_acao", "data_proxima_acao",
        "responsavel", "prioridade",
    }
    payload = snapshots.build_crm_clinicas(povoado)
    assert payload["clinicas"], "fixture deveria produzir ao menos uma clínica"
    for registro in payload["clinicas"]:
        assert set(registro) <= allowlist, set(registro) - allowlist
