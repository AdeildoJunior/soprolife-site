#!/usr/bin/env python3
"""Independent, fail-closed reports go-live gate (M24B/M24C).

This contract is deliberately separate from the general M15 gate.  It never
creates a directory, changes a unit, writes configuration, or enables reports.
M24C also keeps an unconditional production blocker while legal footer text
and a qualified signature provider remain unapproved/unconfigured.
"""

from __future__ import annotations

import grp
import json
import os
import pathlib
import pwd
import shlex
import stat
import sys
import time
from dataclasses import dataclass

import go_live_https_gate as https_transport

REPORTS_AUTHORIZATION_PHRASE = "AUTORIZO GO-LIVE DE LAUDOS"
BACKUP_ATTESTATION_PHRASE = "POSTGRESQL_E_STORAGE_CONFIRMADOS"
REPORTS_API_BASE = "/painel-soprolife/api/m15"
REPORTS_CONFIG_PATH = "/painel-soprolife/data/m15-config.json"
REPORTS_PANEL_PATH = "/painel-soprolife/"
REPORTS_API_PATH = REPORTS_API_BASE + "/laudos"
REPORTS_WORKFLOW_MARKERS = (
    'id="laudos-espirometria"',
    "report-workflow.js",
)
M24C_PRODUCTION_BLOCKER = "m24c_signature_and_legal_approval_missing"


class ReportsGateError(RuntimeError):
    """Any unmet requirement rejects the release before mutation."""


@dataclass(frozen=True)
class ReportsGateResult:
    enabled: bool
    storage_root: pathlib.Path | None = None


def _read_target_frontend_flag(repo_root: pathlib.Path) -> bool:
    config_path = repo_root / "painel-soprolife/data/m15-config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ReportsGateError("versioned_frontend_config_invalid") from exc
    enabled = config.get("reports_enabled")
    if enabled is not True and enabled is not False:
        raise ReportsGateError("versioned_reports_flag_not_boolean")
    if config.get("api_base") != REPORTS_API_BASE:
        raise ReportsGateError("versioned_api_base_invalid")
    return enabled


def _parse_backend_flag(value: str | None) -> bool:
    if value is None or value == "":
        return False
    if value == "true":
        return True
    if value == "false":
        return False
    raise ReportsGateError("backend_reports_flag_not_exact_boolean")


def _assert_no_symlink_ancestor(path: pathlib.Path) -> None:
    current = pathlib.Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise ReportsGateError("reports_storage_root_missing") from exc
        except OSError as exc:
            raise ReportsGateError("reports_storage_root_unreadable") from exc
        if stat.S_ISLNK(mode):
            raise ReportsGateError("reports_storage_symlink_ancestor")


def _validate_storage_root(
    value: str | None,
    *,
    repo_root: pathlib.Path,
    expected_uid: int,
    expected_gid: int,
) -> pathlib.Path:
    if not value:
        raise ReportsGateError("reports_storage_root_absent")
    if any(character.isspace() or character == "\x00" for character in value):
        raise ReportsGateError("reports_storage_root_unsafe_characters")
    root = pathlib.Path(value)
    if not root.is_absolute():
        raise ReportsGateError("reports_storage_root_not_absolute")
    _assert_no_symlink_ancestor(root)
    try:
        root_stat = root.stat(follow_symlinks=False)
        resolved = root.resolve(strict=True)
        resolved_repo = repo_root.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ReportsGateError("reports_storage_root_unavailable") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or resolved != root:
        raise ReportsGateError("reports_storage_root_not_exact_directory")
    try:
        root.relative_to(resolved_repo)
    except ValueError:
        pass
    else:
        raise ReportsGateError("reports_storage_root_inside_git")
    if stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise ReportsGateError("reports_storage_root_mode_not_0700")
    if root_stat.st_uid != expected_uid or root_stat.st_gid != expected_gid:
        raise ReportsGateError("reports_storage_root_owner_mismatch")
    return root


def _readwritepaths(unit_text: str) -> tuple[pathlib.Path, ...]:
    entries: list[pathlib.Path] = []
    in_service = False
    for raw_line in unit_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_service = line == "[Service]"
            continue
        if not in_service or not line.startswith("ReadWritePaths="):
            continue
        value = line.split("=", 1)[1].strip()
        if not value:
            entries.clear()
            continue
        try:
            tokens = shlex.split(value, posix=True)
        except ValueError as exc:
            raise ReportsGateError("systemd_readwritepaths_malformed") from exc
        for token in tokens:
            if (
                not token.startswith("/")
                or token.startswith(("-", "+", "!"))
                or ":" in token
            ):
                raise ReportsGateError("systemd_readwritepaths_ambiguous")
            entries.append(pathlib.Path(os.path.normpath(token)))
    return tuple(entries)


def _validate_exact_readwritepath(
    unit_text: str,
    *,
    storage_root: pathlib.Path,
) -> None:
    entries = _readwritepaths(unit_text)
    if storage_root not in entries:
        raise ReportsGateError("systemd_exact_storage_readwritepath_missing")
    for entry in entries:
        if entry == storage_root:
            continue
        try:
            storage_root.relative_to(entry)
        except ValueError:
            continue
        raise ReportsGateError("systemd_broad_writable_parent_forbidden")


def _json_object(body: bytes, code: str) -> dict:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ReportsGateError(code) from exc
    if not isinstance(value, dict):
        raise ReportsGateError(code)
    return value


def _report_error_code(payload: dict) -> str | None:
    error = payload.get("erro")
    return error.get("codigo") if isinstance(error, dict) else None


def check_https_workspace(
    base_url: str,
    *,
    expected_enabled: bool | None,
    http_get=None,
) -> bool:
    """Prove frontend workspace/API agreement over verified HTTPS."""

    base = https_transport.validar_base_url(base_url)
    getter = http_get or https_transport.http_get
    deadline = time.monotonic() + https_transport.TOTAL_TIMEOUT_S

    panel_status, panel_body = getter(
        base + REPORTS_PANEL_PATH,
        deadline,
    )
    if panel_status != 200:
        raise ReportsGateError("reports_https_panel_not_200")
    panel = panel_body.decode("utf-8", errors="replace")
    if any(marker not in panel for marker in REPORTS_WORKFLOW_MARKERS):
        raise ReportsGateError("reports_https_workspace_markup_missing")

    config_status, config_body = getter(
        base + REPORTS_CONFIG_PATH,
        deadline,
    )
    if config_status != 200:
        raise ReportsGateError("reports_https_config_not_200")
    config = _json_object(config_body, "reports_https_config_invalid")
    frontend_enabled = config.get("reports_enabled")
    if frontend_enabled is not True and frontend_enabled is not False:
        raise ReportsGateError("reports_https_frontend_flag_invalid")
    if config.get("api_base") != REPORTS_API_BASE:
        raise ReportsGateError("reports_https_api_base_invalid")
    if expected_enabled is not None and frontend_enabled is not expected_enabled:
        raise ReportsGateError("reports_https_target_flag_mismatch")

    api_status, api_body = getter(base + REPORTS_API_PATH, deadline)
    api_payload = _json_object(api_body, "reports_https_api_response_invalid")
    error_code = _report_error_code(api_payload)
    if frontend_enabled:
        if api_status != 401 or error_code == "relatorios_desabilitados":
            raise ReportsGateError("reports_https_api_frontend_disagree")
    elif api_status != 503 or error_code != "relatorios_desabilitados":
        raise ReportsGateError("reports_https_api_frontend_disagree")
    return frontend_enabled


def check_preflight(
    *,
    repo_root: pathlib.Path,
    backend_flag: str | None,
    reports_authorization: str | None,
    storage_root_value: str | None,
    backup_attestation: str | None,
    effective_unit_text: str,
    expected_uid: int,
    expected_gid: int,
    https_base_url: str | None,
    http_get=None,
) -> ReportsGateResult:
    """Reject every enabled release unless all independent gates pass."""

    repo_root = repo_root.resolve(strict=True)
    frontend_enabled = _read_target_frontend_flag(repo_root)
    backend_enabled = _parse_backend_flag(backend_flag)
    if frontend_enabled is not backend_enabled:
        raise ReportsGateError("reports_frontend_backend_flag_mismatch")
    if not frontend_enabled:
        return ReportsGateResult(enabled=False)

    if reports_authorization != REPORTS_AUTHORIZATION_PHRASE:
        raise ReportsGateError("reports_specific_authorization_missing")
    if backup_attestation != BACKUP_ATTESTATION_PHRASE:
        raise ReportsGateError("reports_coordinated_backup_not_attested")
    storage_root = _validate_storage_root(
        storage_root_value,
        repo_root=repo_root,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    _validate_exact_readwritepath(
        effective_unit_text,
        storage_root=storage_root,
    )
    if not https_base_url:
        raise ReportsGateError("reports_https_base_url_missing")
    # Preflight accepts either currently-disabled or currently-enabled state,
    # but only if the served frontend and unauthenticated API agree.
    check_https_workspace(
        https_base_url,
        expected_enabled=None,
        http_get=http_get,
    )
    # M24C deliberately has no production signing success path and only a
    # TEST footer. A later, separately authorized milestone must replace this
    # blocker after legal wording and a qualified provider are approved.
    raise ReportsGateError(M24C_PRODUCTION_BLOCKER)


def _service_ids() -> tuple[int, int]:
    try:
        return pwd.getpwnam("soprolife").pw_uid, grp.getgrnam("soprolife").gr_gid
    except KeyError as exc:
        raise ReportsGateError("reports_service_identity_missing") from exc


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"preflight", "postflight"}:
        print(
            "usage: reports_go_live_gate.py preflight|postflight REPO_ROOT",
            file=sys.stderr,
        )
        return 2
    phase = argv[1]
    repo_root = pathlib.Path(argv[2])
    try:
        if phase == "preflight":
            uid, gid = _service_ids()
            unit_text = sys.stdin.read()
            result = check_preflight(
                repo_root=repo_root,
                backend_flag=os.environ.get("M15_REPORTS_ENABLED"),
                reports_authorization=os.environ.get(
                    "SOPROLIFE_REPORTS_GO_LIVE"
                ),
                storage_root_value=os.environ.get("M15_REPORTS_STORAGE_DIR"),
                backup_attestation=os.environ.get(
                    "SOPROLIFE_REPORTS_BACKUP_COORDINATED"
                ),
                effective_unit_text=unit_text,
                expected_uid=uid,
                expected_gid=gid,
                https_base_url=os.environ.get(
                    "SOPROLIFE_M15_HTTPS_BASE_URL"
                ),
            )
            print("true" if result.enabled else "false")
        else:
            expected = _parse_backend_flag(
                os.environ.get("M15_REPORTS_ENABLED")
            )
            if expected:
                raise ReportsGateError(M24C_PRODUCTION_BLOCKER)
            print("true" if expected else "false")
    except (ReportsGateError, OSError, ValueError) as exc:
        print(
            f"ERRO REPORTS GO-LIVE (fail-closed): {exc}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
