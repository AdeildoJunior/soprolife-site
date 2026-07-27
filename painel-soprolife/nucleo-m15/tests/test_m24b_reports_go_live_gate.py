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


def _repo(tmp_path: Path, *, reports_enabled: bool) -> Path:
    repo = tmp_path / "synthetic-repo"
    config = repo / "painel-soprolife/data/m15-config.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "enabled": True,
                "reports_enabled": reports_enabled,
                "api_base": "/painel-soprolife/api/m15",
            }
        ),
        encoding="utf-8",
    )
    return repo


def _private_root(tmp_path: Path) -> Path:
    root = tmp_path / "synthetic-private-reports"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    return root


def _https_responses(*, enabled: bool):
    api_status = 401 if enabled else 503
    api_code = "http_401" if enabled else "relatorios_desabilitados"
    return {
        BASE + gate.REPORTS_PANEL_PATH: (
            200,
            b'<section id="laudos-espirometria"></section>'
            b'<script src="./js/report-workflow.js"></script>',
        ),
        BASE + gate.REPORTS_CONFIG_PATH: (
            200,
            json.dumps(
                {
                    "reports_enabled": enabled,
                    "api_base": gate.REPORTS_API_BASE,
                }
            ).encode(),
        ),
        BASE + gate.REPORTS_API_PATH: (
            api_status,
            json.dumps({"erro": {"codigo": api_code}}).encode(),
        ),
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
        "http_get": _getter(_https_responses(enabled=False)),
    }
    values.update(overrides)
    return gate.check_preflight(**values)


def test_default_release_is_accepted_as_reports_disabled():
    repo_root = Path(__file__).resolve().parents[3]
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


def test_exact_private_path_passes_only_in_fully_synthetic_fixture(tmp_path):
    repo = _repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    result = _enabled_check(repo, root)
    assert result.enabled is True
    assert result.storage_root == root


def test_backup_attestation_is_independent_and_exact(tmp_path):
    repo = _repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    for value in (None, "", "YES", "postgresql_e_storage_confirmados"):
        with pytest.raises(gate.ReportsGateError) as caught:
            _enabled_check(repo, root, backup_attestation=value)
        assert str(caught.value) == "reports_coordinated_backup_not_attested"


def test_https_preflight_and_postflight_require_api_frontend_agreement():
    assert (
        gate.check_https_workspace(
            BASE,
            expected_enabled=False,
            http_get=_getter(_https_responses(enabled=False)),
        )
        is False
    )
    assert (
        gate.check_https_workspace(
            BASE,
            expected_enabled=True,
            http_get=_getter(_https_responses(enabled=True)),
        )
        is True
    )

    mismatched = _https_responses(enabled=True)
    mismatched[BASE + gate.REPORTS_API_PATH] = (
        503,
        b'{"erro":{"codigo":"relatorios_desabilitados"}}',
    )
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_https_workspace(
            BASE,
            expected_enabled=True,
            http_get=_getter(mismatched),
        )
    assert str(caught.value) == "reports_https_api_frontend_disagree"


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
