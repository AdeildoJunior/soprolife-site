"""A feature de laudos não acompanha o go-live geral do Núcleo M15."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app

PANEL_ROOT = Path(__file__).resolve().parents[2]


def test_backend_default_e_producao_permanecem_desabilitados(monkeypatch):
    monkeypatch.delenv("M15_REPORTS_ENABLED", raising=False)
    assert Settings().reports_enabled is False
    production = Settings(
        env="prod",
        auth_secret="0123456789abcdefghijklmnopqrstuvwxyz-SEGREDO",
        api_host="127.0.0.1",
    )
    assert production.reports_enabled is False


def test_api_recusa_antes_de_autenticacao_ou_parser_quando_flag_esta_off(
    monkeypatch,
):
    monkeypatch.delenv("M15_REPORTS_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            response = client.post(
                "/api/v1/laudos",
                content=b"corpo que nao deve ser interpretado",
                headers={"Content-Type": "multipart/form-data; boundary=x"},
            )
        assert response.status_code == 503
        assert response.json()["erro"]["codigo"] == "relatorios_desabilitados"
        assert isinstance(response.json()["erro"]["mensagem"], str)
    finally:
        get_settings.cache_clear()


def test_habilitar_o_nucleo_nao_habilita_relatorios_no_frontend():
    config = json.loads((PANEL_ROOT / "data" / "m15-config.json").read_text())
    assert config["enabled"] is True
    # M24D: reports_enabled é um campo próprio, nunca derivado do `enabled`
    # geral do M15 — a única coisa que decide seu valor é reports_mode
    # (disabled<->false; pilot/production<->true), ativado por um commit
    # de decisão separado (ver docs/m24d-reports-pilot.md), nunca por
    # efeito colateral de ligar o Núcleo M15 inteiro.
    assert config["reports_mode"] in ("disabled", "pilot", "production")
    assert config["reports_enabled"] == (config["reports_mode"] != "disabled")

    workflow = (PANEL_ROOT / "js" / "report-workflow.js").read_text()
    assert "config.reports_enabled === true" in workflow
    assert 'localStorage.getItem("soproM24AReports") === "on"' in workflow
    assert '["127.0.0.1", "::1", "localhost"]' in workflow


def test_backend_so_abre_a_borda_quando_flag_dedicada_e_explicita(monkeypatch):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/laudos")
        # A flag deixou passar; a autenticação ainda bloqueia o uso.
        assert response.status_code == 401
        assert response.json()["erro"]["codigo"] == "http_401"
    finally:
        get_settings.cache_clear()


def test_backend_default_mode_e_disabled(monkeypatch):
    monkeypatch.delenv("M15_REPORTS_MODE", raising=False)
    assert Settings().reports_mode == "disabled"


def test_flag_geral_isolada_nao_basta_sem_modo_pilot(monkeypatch):
    """M24D: M15_REPORTS_ENABLED=true sozinho (sem M15_REPORTS_MODE=pilot)
    continua desligado — a variável geral do M15 nunca é suficiente."""
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.delenv("M15_REPORTS_MODE", raising=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/laudos")
        assert response.status_code == 503
        assert response.json()["erro"]["codigo"] == "relatorios_desabilitados"
    finally:
        get_settings.cache_clear()


def test_modo_producao_permanece_bloqueado_mesmo_com_flag_ligada(monkeypatch):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "production")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/laudos")
        assert response.status_code == 503
        assert response.json()["erro"]["codigo"] == "relatorios_producao_bloqueada"
    finally:
        get_settings.cache_clear()
