"""M27 — configuração fail-closed: artefatos fiscais e senha do PKCS#12 restrito.

Não duplica toda a bateria de `test_m24a_report_storage.py` (symlink
ancestral, corrida de chmod/mkdir, contenção pós-criação): esses casos
exercitam as MESMAS funções privadas de `app/config.py` que
`resolved_fiscal_artifacts_storage_dir` reaproveita. Aqui fica só o que é
específico deste método.
"""
import stat

import pytest

from app.config import Settings


def test_missing_dir_fails_closed():
    with pytest.raises(ValueError):
        Settings(nfse_fiscal_artifacts_dir=None).resolved_fiscal_artifacts_storage_dir()


def test_relative_path_rejected(tmp_path):
    with pytest.raises(ValueError):
        Settings(nfse_fiscal_artifacts_dir="relative/path").resolved_fiscal_artifacts_storage_dir()


def test_creates_private_directory(tmp_path):
    root = tmp_path / "fiscal-artifacts"
    resolved = Settings(nfse_fiscal_artifacts_dir=root).resolved_fiscal_artifacts_storage_dir()
    assert resolved.is_dir()
    assert stat.S_IMODE(resolved.stat().st_mode) == 0o700


def test_rejects_path_inside_git_worktree():
    from app.config import _find_git_repo_root

    repo_root = _find_git_repo_root()
    if repo_root is None:
        pytest.skip("not running inside a git worktree")
    inside = repo_root / "var" / "should-not-be-allowed-fiscal-artifacts"
    with pytest.raises(ValueError):
        Settings(nfse_fiscal_artifacts_dir=inside).resolved_fiscal_artifacts_storage_dir()


def test_fiscal_artifacts_dir_independent_from_reports_dir(tmp_path):
    reports_root = tmp_path / "reports"
    fiscal_root = tmp_path / "fiscal"
    settings = Settings(reports_storage_dir=reports_root, nfse_fiscal_artifacts_dir=fiscal_root)
    resolved_reports = settings.resolved_reports_storage_dir()
    resolved_fiscal = settings.resolved_fiscal_artifacts_storage_dir()
    assert resolved_reports != resolved_fiscal


def test_certificate_password_missing_fails_closed():
    with pytest.raises(ValueError):
        Settings(nfse_restricted_certificate_password=None).resolved_nfse_restricted_certificate_password()


def test_certificate_password_returned_when_set():
    settings = Settings(nfse_restricted_certificate_password="a-configured-secret")
    assert settings.resolved_nfse_restricted_certificate_password() == "a-configured-secret"


def test_restricted_base_url_requires_https():
    with pytest.raises(ValueError):
        Settings(nfse_restricted_base_url="http://restrito.nfse.gov.br")


def test_restricted_network_gate_defaults_to_false():
    assert Settings().nfse_restricted_network_enabled is False


def test_restricted_layout_version_has_an_explicit_default():
    assert Settings().nfse_restricted_layout_version == "restricted-v1.01-20260727"
