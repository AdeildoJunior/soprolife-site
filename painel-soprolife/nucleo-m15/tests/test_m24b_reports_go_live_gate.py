"""Independent reports go-live gate; all enabled cases are synthetic."""

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import reports_go_live_gate as gate  # noqa: E402

BASE = "https://reports-gate.example.invalid"


# M26.21 — a marcação do workspace passou a ser provada NO RELEASE (fonte local
# versionada), e não mais por GET anônimo do painel; o repositório sintético
# precisa, portanto, conter o index administrativo e o script da bancada.
INDEX_COM_WORKSPACE = (
    '<html><body><section id="laudos-espirometria"></section>'
    '<script src="./js/report-workflow.js?v=1" defer></script>'
    "</body></html>"
)
LOGIN_HTML = (
    "<!doctype html><html><body>"
    '<form id="loginForm"><input id="password" type="password" /></form>'
    '<script src="./js/m15-security.js"></script>'
    "</body></html>"
)
CORPO_401 = rb'{"ok": false, "error": "Sess\u00e3o necess\u00e1ria."}'


def _repo(tmp_path: Path, *, reports_enabled: bool) -> Path:
    repo = tmp_path / "synthetic-repo"
    config = repo / "painel-soprolife/data/m15-config.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "enabled": True,
                "reports_enabled": reports_enabled,
                # O gate único (M24B/M24C) só é exercitado com enabled=true em
                # modo produção — que permanece bloqueado incondicionalmente.
                "reports_mode": "production" if reports_enabled else "disabled",
                "api_base": "/painel-soprolife/api/m15",
            }
        ),
        encoding="utf-8",
    )
    index = repo / "painel-soprolife/index.html"
    index.write_text(INDEX_COM_WORKSPACE, encoding="utf-8")
    workflow = repo / "painel-soprolife/js/report-workflow.js"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("// bancada de laudos\n", encoding="utf-8")
    return repo


def _private_root(tmp_path: Path) -> Path:
    root = tmp_path / "synthetic-private-reports"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    return root


def _https_responses(*, effective: str = gate.EFFECTIVE_DISABLED):
    """A superfície ANÔNIMA real do painel desde a M25.23.

    O painel devolve a tela de login (200) e o manifesto de boot devolve 401.
    O único canal que declara o estado de laudos sem sessão é
    `/api/m15/laudos`, e ele distingue os três casos.
    """
    api = {
        gate.EFFECTIVE_PILOT: (401, {"erro": {"codigo": "http_401"}}),
        gate.EFFECTIVE_DISABLED: (
            503,
            {"erro": {"codigo": "relatorios_desabilitados"}},
        ),
        gate.EFFECTIVE_PRODUCTION_BLOCKED: (
            503,
            {"erro": {"codigo": "relatorios_producao_bloqueada"}},
        ),
    }[effective]
    return {
        BASE + gate.REPORTS_PANEL_PATH: (200, LOGIN_HTML.encode()),
        BASE + gate.REPORTS_CONFIG_PATH: (401, CORPO_401),
        BASE + gate.REPORTS_API_PATH: (api[0], json.dumps(api[1]).encode()),
    }


def _getter(responses):
    def fake(url, _deadline):
        return responses[url]

    return fake


def _enabled_check(repo, root, *, unit_text=None, **overrides):
    values = {
        "repo_root": repo,
        "backend_flag": "true",
        "reports_authorization": gate.REPORTS_AUTHORIZATION_PHRASE,
        "storage_root_value": str(root),
        "backup_attestation": gate.BACKUP_ATTESTATION_PHRASE,
        "effective_unit_text": unit_text
        or f"[Service]\nReadWritePaths=/unrelated/var {root}\n",
        "expected_uid": os.getuid(),
        "expected_gid": os.getgid(),
        "https_base_url": BASE,
        "http_get": _getter(_https_responses()),
    }
    values.update(overrides)
    return gate.check_preflight(**values)


def test_default_release_is_accepted_as_reports_disabled(tmp_path):
    # M24D: usa um repositório SINTÉTICO com reports_enabled=false — o
    # repositório real deste branch pode estar com o piloto ativado
    # (reports_mode=pilot), e este teste prova o comportamento do gate
    # único para um release desligado, não o estado atual do checkout.
    repo_root = _repo(tmp_path, reports_enabled=False)
    result = gate.check_preflight(
        repo_root=repo_root,
        backend_flag=None,
        reports_authorization=None,
        storage_root_value=None,
        backup_attestation=None,
        effective_unit_text="",
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        https_base_url=None,
    )
    assert result.enabled is False


def test_reports_true_without_reports_specific_authorization_is_rejected(tmp_path):
    repo = _repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    with pytest.raises(gate.ReportsGateError) as caught:
        _enabled_check(repo, root, reports_authorization=None)
    assert str(caught.value) == "reports_specific_authorization_missing"


def test_general_m15_authorization_alone_is_insufficient(tmp_path, monkeypatch):
    monkeypatch.setenv("SOPROLIFE_M15_GO_LIVE", "YES")
    repo = _repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    with pytest.raises(gate.ReportsGateError) as caught:
        _enabled_check(repo, root, reports_authorization=None)
    assert str(caught.value) == "reports_specific_authorization_missing"


@pytest.mark.parametrize(
    "frontend,backend",
    [(True, "false"), (False, "true")],
)
def test_frontend_backend_flag_mismatch_is_rejected(tmp_path, frontend, backend):
    repo = _repo(tmp_path, reports_enabled=frontend)
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_preflight(
            repo_root=repo,
            backend_flag=backend,
            reports_authorization=gate.REPORTS_AUTHORIZATION_PHRASE,
            storage_root_value=None,
            backup_attestation=gate.BACKUP_ATTESTATION_PHRASE,
            effective_unit_text="",
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
            https_base_url=BASE,
        )
    assert str(caught.value) == "reports_frontend_backend_flag_mismatch"


def test_absent_relative_symlinked_permissive_or_wrong_owner_root_is_rejected(
    tmp_path,
):
    repo = _repo(tmp_path, reports_enabled=True)
    real = _private_root(tmp_path)
    link = tmp_path / "storage-link"
    link.symlink_to(real, target_is_directory=True)
    relative = "relative/reports"

    cases = [
        (None, {}, "reports_storage_root_absent"),
        (relative, {}, "reports_storage_root_not_absolute"),
        (str(link), {}, "reports_storage_symlink_ancestor"),
    ]
    for value, overrides, code in cases:
        with pytest.raises(gate.ReportsGateError) as caught:
            _enabled_check(
                repo,
                real,
                storage_root_value=value,
                **overrides,
            )
        assert str(caught.value) == code

    os.chmod(real, 0o750)
    with pytest.raises(gate.ReportsGateError) as caught:
        _enabled_check(repo, real)
    assert str(caught.value) == "reports_storage_root_mode_not_0700"
    os.chmod(real, 0o700)

    with pytest.raises(gate.ReportsGateError) as caught:
        _enabled_check(repo, real, expected_uid=os.getuid() + 1)
    assert str(caught.value) == "reports_storage_root_owner_mismatch"


def test_storage_root_inside_git_checkout_is_rejected(tmp_path):
    repo = _repo(tmp_path, reports_enabled=True)
    root = repo / "private-reports"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    with pytest.raises(gate.ReportsGateError) as caught:
        _enabled_check(repo, root)
    assert str(caught.value) == "reports_storage_root_inside_git"


def test_missing_or_broad_readwritepaths_are_rejected(tmp_path):
    repo = _repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    with pytest.raises(gate.ReportsGateError) as caught:
        _enabled_check(repo, root, unit_text="[Service]\nReadWritePaths=/other\n")
    assert str(caught.value) == "systemd_exact_storage_readwritepath_missing"

    broad = root.parent
    unit = f"[Service]\nReadWritePaths={root} {broad}\n"
    with pytest.raises(gate.ReportsGateError) as caught:
        _enabled_check(repo, root, unit_text=unit)
    assert str(caught.value) == "systemd_broad_writable_parent_forbidden"


def test_m24c_bloqueia_enable_mesmo_com_precondicoes_tecnicas_sinteticas(
    tmp_path,
):
    repo = _repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    with pytest.raises(gate.ReportsGateError) as caught:
        _enabled_check(repo, root)
    assert str(caught.value) == gate.M24C_PRODUCTION_BLOCKER


def test_backup_attestation_is_independent_and_exact(tmp_path):
    repo = _repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    for value in (None, "", "YES", "postgresql_e_storage_confirmados"):
        with pytest.raises(gate.ReportsGateError) as caught:
            _enabled_check(repo, root, backup_attestation=value)
        assert str(caught.value) == "reports_coordinated_backup_not_attested"


def test_https_postflight_exige_backend_efetivo_igual_ao_release(tmp_path):
    """M26.21 — o acordo deixou de ser lido no config servido (hoje protegido)
    e passou a ser provado contra o BACKEND efetivo, via probe anônimo."""

    desligado = _repo(tmp_path / "off", reports_enabled=False)
    assert (
        gate.check_https_workspace(
            BASE,
            repo_root=desligado,
            expected_enabled=False,
            http_get=_getter(_https_responses()),
        )
        is False
    )

    producao = _repo(tmp_path / "prod", reports_enabled=True)
    assert (
        gate.check_https_workspace(
            BASE,
            repo_root=producao,
            expected_enabled=True,
            expected_mode="production",
            http_get=_getter(
                _https_responses(effective=gate.EFFECTIVE_PRODUCTION_BLOCKED)
            ),
        )
        is True
    )

    # Release alvo em produção, backend ainda desabilitado: divergência.
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_https_workspace(
            BASE,
            repo_root=producao,
            expected_enabled=True,
            expected_mode="production",
            http_get=_getter(_https_responses()),
        )
    assert str(caught.value) == "reports_https_target_mode_mismatch"

    # Release alvo desligado, mas o backend está servindo o piloto.
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_https_workspace(
            BASE,
            repo_root=desligado,
            expected_enabled=False,
            http_get=_getter(_https_responses(effective=gate.EFFECTIVE_PILOT)),
        )
    assert str(caught.value) == "reports_https_target_flag_mismatch"


def test_https_workspace_recusa_vazamento_do_command_center(tmp_path):
    """Prova NEGATIVA: se o painel voltar a sair sem login, o gate aborta."""

    repo = _repo(tmp_path, reports_enabled=False)
    vazando = _https_responses()
    vazando[BASE + gate.REPORTS_PANEL_PATH] = (
        200,
        b'<section id="laudos-espirometria"></section>'
        b'<script src="./js/report-workflow.js"></script>',
    )
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_https_workspace(
            BASE,
            repo_root=repo,
            expected_enabled=False,
            http_get=_getter(vazando),
        )
    assert str(caught.value) == "reports_https_workspace_markup_leaked"


def test_https_workspace_recusa_manifesto_publico(tmp_path):
    repo = _repo(tmp_path, reports_enabled=False)
    publico = _https_responses()
    publico[BASE + gate.REPORTS_CONFIG_PATH] = (
        200,
        json.dumps(
            {"reports_enabled": False, "api_base": gate.REPORTS_API_BASE}
        ).encode(),
    )
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_https_workspace(
            BASE,
            repo_root=repo,
            expected_enabled=False,
            http_get=_getter(publico),
        )
    assert str(caught.value) == "reports_https_config_not_protected"


def test_https_workspace_recusa_release_sem_workspace(tmp_path):
    """A verificação histórica continua existindo — na fonte certa."""

    repo = _repo(tmp_path, reports_enabled=False)
    (repo / "painel-soprolife/index.html").write_text(
        "<html><body>sem bancada</body></html>", encoding="utf-8"
    )
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_https_workspace(
            BASE,
            repo_root=repo,
            expected_enabled=False,
            http_get=_getter(_https_responses()),
        )
    assert str(caught.value) == "reports_https_workspace_markup_missing"


def test_https_workspace_recusa_release_sem_script_da_bancada(tmp_path):
    repo = _repo(tmp_path, reports_enabled=False)
    (repo / "painel-soprolife/js/report-workflow.js").write_text(
        "   \n", encoding="utf-8"
    )
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_https_workspace(
            BASE,
            repo_root=repo,
            expected_enabled=False,
            http_get=_getter(_https_responses()),
        )
    assert str(caught.value) == "reports_release_workflow_script_missing"


def test_no_deployment_mutation_precedes_reports_gate():
    deploy = (SCRIPTS_DIR / "deploy-producao-vps.sh").read_text(encoding="utf-8")
    gate_index = deploy.index("soprolife_reports_go_live_preflight")
    for mutation_marker in (
        "Digite exatamente 'IMPLANTAR M15'",
        "sudo -v",
        'sudo install -d -o root -g root -m 0700 "$BACKUP_DIR"',
        "MUTATION_STARTED=1",
        "apt-get update",
    ):
        assert gate_index < deploy.index(mutation_marker)
    assert "lib-reports-go-live-gate.sh" in deploy
    assert "${SOPROLIFE_M15_GO_LIVE" not in (
        SCRIPTS_DIR / "lib-reports-go-live-gate.sh"
    ).read_text(encoding="utf-8")
