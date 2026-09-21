#!/usr/bin/env python3
"""Independent, fail-closed reports go-live gate (M24B/M24C).

This contract is deliberately separate from the general M15 gate.  It never
creates a directory, changes a unit, writes configuration, or enables reports.
M24C also keeps an unconditional production blocker while legal footer text
and a qualified signature provider remain unapproved/unconfigured.

M26.21 — por que este gate parou de ler o workspace por HTTPS anônimo
---------------------------------------------------------------------
Até aqui ``check_https_workspace`` fazia um GET ANÔNIMO de
``/painel-soprolife/`` e exigia encontrar no HTML ``id="laudos-espirometria"``
e ``report-workflow.js``; depois fazia um GET ANÔNIMO de
``data/m15-config.json`` e exigia 200. Os dois eram possíveis quando o gate
nasceu (M24B), porque o painel inteiro ainda era servido sem autenticação.
A M25.23 fechou esse vazamento: hoje o painel devolve ``login.html`` a quem
não tem sessão e recusa o manifesto com 401. O resultado foi o deploy do
commit f9c0761 abortar em ``reports_https_workspace_markup_missing`` — o
workspace ESTAVA no release; o que chegou ao gate foi a tela de login.

A correção não afrouxa nada e não devolve nada ao anonimato. O contrato foi
separado em duas metades:

* **superfície anônima** (``go_live_https_gate``): prova positiva de que a tela
  de login é servida em ``/painel-soprolife/`` e prova NEGATIVA de que nem a
  casca administrativa nem a bancada de laudos vazam ali, mais o 401 do
  manifesto de boot;
* **release e backend efetivo**: o workspace de laudos e os flags são lidos do
  checkout implantado (fonte local versionada, a mesma que o servidor serve), e
  o estado REAL do backend vem do probe anônimo de ``/api/m15/laudos``, que
  distingue os três casos sem sessão nenhuma — 401 (piloto servindo),
  503+``relatorios_desabilitados`` e 503+``relatorios_producao_bloqueada``.

Preflight e postflight passaram a diferir de propósito: no preflight o checkout
já é o release ALVO enquanto os serviços ainda rodam o release ANTERIOR, então
exigir concordância ali tornaria a primeira ativação impossível; o postflight,
esse sim, exige que o backend efetivo seja exatamente o do release implantado.
"""

from __future__ import annotations

import grp
import hashlib
import json
import os
import pathlib
import pwd
import shlex
import stat
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import go_live_https_gate as https_transport

REPORTS_AUTHORIZATION_PHRASE = "AUTORIZO GO-LIVE DE LAUDOS"
BACKUP_ATTESTATION_PHRASE = "POSTGRESQL_E_STORAGE_CONFIRMADOS"
# M24D — autorização dedicada do piloto interno controlado. Independente da
# frase geral de go-live de laudos acima (essa continua exigida só para
# produção) e do go-live geral do M15 (SOPROLIFE_M15_GO_LIVE), que sozinho
# nunca é suficiente para habilitar nada aqui.
PILOT_AUTHORIZATION_PHRASE = "HABILITAR PILOTO DE LAUDOS"
BACKUP_MANIFEST_MAX_AGE_SECONDS = 24 * 60 * 60
REPORTS_API_BASE = "/painel-soprolife/api/m15"
REPORTS_CONFIG_PATH = "/painel-soprolife/data/m15-config.json"
REPORTS_PANEL_PATH = "/painel-soprolife/"
REPORTS_API_PATH = REPORTS_API_BASE + "/laudos"
REPORTS_WORKFLOW_MARKERS = (
    'id="laudos-espirometria"',
    "report-workflow.js",
)
# M26.21 — onde o workspace passou a ser provado: no checkout implantado.
REPORTS_INDEX_SOURCE = "painel-soprolife/index.html"
REPORTS_WORKFLOW_SOURCE = "painel-soprolife/js/report-workflow.js"
# Estados de laudos que o backend consegue declarar a um cliente ANÔNIMO.
# Não são opinião do gate: saem direto de `_require_reports_enabled` na API.
EFFECTIVE_DISABLED = "disabled"
EFFECTIVE_PILOT = "pilot"
EFFECTIVE_PRODUCTION_BLOCKED = "production_blocked"
M24C_PRODUCTION_BLOCKER = "m24c_signature_and_legal_approval_missing"
# Tradução estável das rejeições da superfície anônima (go_live_https_gate)
# para o vocabulário deste contrato.
_TRADUCAO_SUPERFICIE_ANONIMA = {
    https_transport.CODIGO_PAINEL_NAO_200: "reports_https_panel_not_200",
    https_transport.CODIGO_PAINEL_VAZOU_ADMIN: (
        "reports_https_workspace_markup_leaked"
    ),
    https_transport.CODIGO_PAINEL_SEM_LOGIN: "reports_https_login_screen_missing",
}


class ReportsGateError(RuntimeError):
    """Any unmet requirement rejects the release before mutation."""


@dataclass(frozen=True)
class ReportsGateResult:
    enabled: bool
    storage_root: pathlib.Path | None = None


REPORTS_MODES = ("disabled", "pilot", "production")


def _load_target_frontend_config(repo_root: pathlib.Path) -> dict:
    config_path = repo_root / "painel-soprolife/data/m15-config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ReportsGateError("versioned_frontend_config_invalid") from exc
    if not isinstance(config, dict):
        raise ReportsGateError("versioned_frontend_config_invalid")
    return config


def _read_target_frontend_flag(repo_root: pathlib.Path) -> bool:
    config = _load_target_frontend_config(repo_root)
    enabled = config.get("reports_enabled")
    if enabled is not True and enabled is not False:
        raise ReportsGateError("versioned_reports_flag_not_boolean")
    if config.get("api_base") != REPORTS_API_BASE:
        raise ReportsGateError("versioned_api_base_invalid")
    return enabled


def read_target_frontend_mode(repo_root: pathlib.Path) -> str:
    """M24D — modo alvo versionado (disabled/pilot/production), validado
    fail-closed contra ``reports_enabled`` no MESMO arquivo antes de
    qualquer gate rodar: ``disabled`` exige ``enabled=false``; ``pilot`` e
    ``production`` exigem ``enabled=true``. Isso deixa o deploy escolher o
    gate certo sem depender de nenhum gate já ter rodado."""

    repo_root = repo_root.resolve(strict=True)
    config = _load_target_frontend_config(repo_root)
    mode = config.get("reports_mode")
    if mode not in REPORTS_MODES:
        raise ReportsGateError("versioned_reports_mode_invalid")
    enabled = _read_target_frontend_flag(repo_root)
    if mode == "disabled" and enabled is not False:
        raise ReportsGateError("versioned_reports_mode_flag_mismatch")
    if mode in ("pilot", "production") and enabled is not True:
        raise ReportsGateError("versioned_reports_mode_flag_mismatch")
    return mode


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


def check_release_workspace(repo_root: pathlib.Path) -> tuple[bool, str]:
    """M26.21 — o workspace de laudos existe MESMO no release implantado?

    Esta é a metade administrativa da prova, e ela é feita na fonte local
    versionada — exatamente os arquivos que o servidor entrega a uma sessão
    válida. Provar isso por HTTPS anônimo exigiria republicar o Command Center
    sem sessão, que é justamente o vazamento que a M25.23 fechou.

    Devolve ``(reports_enabled, reports_mode)`` do release, já validados
    fail-closed um contra o outro por ``read_target_frontend_mode``.
    """

    repo_root = repo_root.resolve(strict=True)
    mode = read_target_frontend_mode(repo_root)
    enabled = _read_target_frontend_flag(repo_root)

    try:
        html = (repo_root / REPORTS_INDEX_SOURCE).read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportsGateError("reports_release_index_unreadable") from exc
    # Mesmo código de erro histórico: o que mudou foi a FONTE da prova, não a
    # exigência. Se o workspace sumir do release, o deploy continua abortando
    # aqui, e agora por um motivo que é de fato o motivo.
    if any(marker not in html for marker in REPORTS_WORKFLOW_MARKERS):
        raise ReportsGateError("reports_https_workspace_markup_missing")

    workflow = repo_root / REPORTS_WORKFLOW_SOURCE
    try:
        script_bytes = workflow.read_bytes()
    except OSError as exc:
        raise ReportsGateError("reports_release_workflow_script_missing") from exc
    if not script_bytes.strip():
        raise ReportsGateError("reports_release_workflow_script_missing")
    return enabled, mode


def _effective_backend_state(getter, base: str, deadline: float) -> str:
    """Estado REAL de laudos no backend em execução, provado sem sessão.

    ``/api/m15/laudos`` é anônimo-observável por construção: a dependência
    ``_require_reports_enabled`` roda ANTES da autenticação e devolve três
    respostas distinguíveis. Um 401 só aparece quando o piloto está servindo
    de verdade — é a autenticação recusando, não a feature.
    """

    status, body = getter(base + REPORTS_API_PATH, deadline)
    payload = _json_object(body, "reports_https_api_response_invalid")
    code = _report_error_code(payload)
    if status == 401:
        if code in ("relatorios_desabilitados", "relatorios_producao_bloqueada"):
            raise ReportsGateError("reports_https_api_response_invalid")
        return EFFECTIVE_PILOT
    if status == 503 and code == "relatorios_desabilitados":
        return EFFECTIVE_DISABLED
    if status == 503 and code == "relatorios_producao_bloqueada":
        return EFFECTIVE_PRODUCTION_BLOCKED
    raise ReportsGateError("reports_https_api_response_invalid")


def _expected_backend_state(enabled: bool, mode: str) -> str:
    if mode == "production":
        return EFFECTIVE_PRODUCTION_BLOCKED
    if mode == "pilot" and enabled:
        return EFFECTIVE_PILOT
    return EFFECTIVE_DISABLED


def check_https_workspace(
    base_url: str,
    *,
    repo_root: pathlib.Path,
    expected_enabled: bool | None,
    expected_mode: str | None = None,
    http_get=None,
) -> bool:
    """Prova o estado real de laudos sem exigir sessão nem expor nada.

    Três provas independentes, todas fail-closed:

    1. **superfície anônima** — ``/painel-soprolife/`` responde 200 com a tela
       de login e SEM nenhuma marcação do Command Center (inclusive sem a
       bancada de laudos), e ``data/m15-config.json`` responde 401. A segunda
       metade é prova negativa: se o vazamento da M25.23 voltasse, o deploy
       aborta;
    2. **release implantado** — o workspace e os flags vêm do checkout local;
    3. **backend efetivo** — o probe anônimo de ``/api/m15/laudos``.

    ``expected_enabled``/``expected_mode`` são ``None`` no PREFLIGHT: ali o
    checkout já é o release alvo enquanto os serviços ainda rodam o release
    anterior, então o único requisito sobre o backend é que ele declare um
    estado RECONHECIDO. O postflight passa os valores exatos e aí sim exige
    que o backend efetivo seja o do release implantado.
    """

    base = https_transport.validar_base_url(base_url)
    getter = http_get or https_transport.http_get
    deadline = time.monotonic() + https_transport.TOTAL_TIMEOUT_S

    try:
        https_transport.checar_entrada_anonima(base, deadline, get=getter)
    except https_transport.GateError as exc:
        # Tradução por CÓDIGO, não por texto: a mensagem é para o operador.
        raise ReportsGateError(
            _TRADUCAO_SUPERFICIE_ANONIMA.get(
                getattr(exc, "codigo", None), "reports_https_panel_not_200"
            )
        ) from exc
    try:
        https_transport.checar_protegidos_nao_vazam(base, deadline, get=getter)
    except https_transport.GateError as exc:
        raise ReportsGateError("reports_https_config_not_protected") from exc

    release_enabled, release_mode = check_release_workspace(repo_root)
    effective = _effective_backend_state(getter, base, deadline)

    if expected_enabled is None and expected_mode is None:
        return release_enabled

    if expected_enabled is not None and release_enabled is not expected_enabled:
        raise ReportsGateError("reports_https_target_flag_mismatch")
    if expected_mode is not None and release_mode != expected_mode:
        raise ReportsGateError("reports_https_target_mode_mismatch")

    esperado = _expected_backend_state(
        release_enabled if expected_enabled is None else expected_enabled,
        release_mode if expected_mode is None else expected_mode,
    )
    if effective != esperado:
        if effective == EFFECTIVE_PILOT or esperado == EFFECTIVE_PILOT:
            raise ReportsGateError("reports_https_target_flag_mismatch")
        raise ReportsGateError("reports_https_target_mode_mismatch")
    return release_enabled


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
    # M26.21 — preflight aceita o estado servido atual (o backend ainda roda o
    # release anterior) desde que a superfície anônima esteja correta, o
    # workspace exista no release e o backend declare um estado reconhecido.
    check_https_workspace(
        https_base_url,
        repo_root=repo_root,
        expected_enabled=None,
        http_get=http_get,
    )
    # M24C deliberately has no production signing success path and only a
    # TEST footer. A later, separately authorized milestone must replace this
    # blocker after legal wording and a qualified provider are approved.
    raise ReportsGateError(M24C_PRODUCTION_BLOCKER)


def _validate_backup_manifest(
    value: str | None,
    *,
    expected_uid: int,
    expected_gid: int,
) -> None:
    """M24D — o piloto exige backup verificado do PostgreSQL e do storage de
    laudos ANTES da mutação (habilitação). Falha fechado em qualquer
    divergência: manifesto ausente, expirado, contagem inválida, artefato
    ausente/symlink/dono errado, ou hash que não bate com o arquivo real."""

    if not value:
        raise ReportsGateError("reports_backup_manifest_missing")
    manifest_path = pathlib.Path(value)
    if not manifest_path.is_absolute():
        raise ReportsGateError("reports_backup_manifest_not_absolute")
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportsGateError("reports_backup_manifest_unreadable") from exc
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise ReportsGateError("reports_backup_manifest_invalid_json") from exc
    if not isinstance(manifest, dict):
        raise ReportsGateError("reports_backup_manifest_invalid_json")

    required_keys = (
        "created_at",
        "postgresql_dump_path",
        "postgresql_dump_sha256",
        "storage_archive_path",
        "storage_archive_sha256",
        "counts",
    )
    for key in required_keys:
        if key not in manifest:
            raise ReportsGateError("reports_backup_manifest_missing_field")

    try:
        created_at = datetime.fromisoformat(str(manifest["created_at"]))
    except ValueError as exc:
        raise ReportsGateError("reports_backup_manifest_created_at_invalid") from exc
    if created_at.tzinfo is None:
        raise ReportsGateError("reports_backup_manifest_created_at_invalid")
    age_seconds = (
        datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)
    ).total_seconds()
    if age_seconds < 0 or age_seconds > BACKUP_MANIFEST_MAX_AGE_SECONDS:
        raise ReportsGateError("reports_backup_manifest_stale")

    counts = manifest["counts"]
    if not isinstance(counts, dict):
        raise ReportsGateError("reports_backup_manifest_counts_invalid")
    for count_key in (
        "report_documents",
        "report_document_versions",
        "physician_profiles",
    ):
        count_value = counts.get(count_key)
        if (
            not isinstance(count_value, int)
            or isinstance(count_value, bool)
            or count_value < 0
        ):
            raise ReportsGateError("reports_backup_manifest_counts_invalid")

    for path_key, hash_key in (
        ("postgresql_dump_path", "postgresql_dump_sha256"),
        ("storage_archive_path", "storage_archive_sha256"),
    ):
        raw_path = manifest[path_key]
        raw_hash = manifest[hash_key]
        if not isinstance(raw_path, str) or not isinstance(raw_hash, str):
            raise ReportsGateError("reports_backup_manifest_invalid_field_type")
        artifact_path = pathlib.Path(raw_path)
        if not artifact_path.is_absolute():
            raise ReportsGateError("reports_backup_manifest_artifact_not_absolute")
        try:
            artifact_stat = artifact_path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ReportsGateError("reports_backup_manifest_artifact_missing") from exc
        if stat.S_ISLNK(artifact_stat.st_mode) or not stat.S_ISREG(
            artifact_stat.st_mode
        ):
            raise ReportsGateError(
                "reports_backup_manifest_artifact_not_regular_file"
            )
        if (
            artifact_stat.st_uid != expected_uid
            or artifact_stat.st_gid != expected_gid
        ):
            raise ReportsGateError("reports_backup_manifest_artifact_owner_mismatch")
        digest = hashlib.sha256()
        with artifact_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != raw_hash:
            raise ReportsGateError("reports_backup_manifest_hash_mismatch")


def check_pilot_preflight(
    *,
    repo_root: pathlib.Path,
    mode_value: str | None,
    backend_flag: str | None,
    pilot_authorization: str | None,
    storage_root_value: str | None,
    backup_manifest_path: str | None,
    effective_unit_text: str,
    expected_uid: int,
    expected_gid: int,
    https_base_url: str | None,
    http_get=None,
) -> ReportsGateResult:
    """M24D — gate dedicado do piloto interno controlado.

    Independente do gate de produção acima (``check_preflight``), que
    permanece com bloqueio incondicional. Nunca cria diretório, altera
    unidade, escreve configuração nem habilita nada por si só — só decide
    se TODAS as condições independentes foram satisfeitas. A variável geral
    do M15 e mesmo ``M15_REPORTS_ENABLED`` sozinho nunca bastam: modo,
    autorização dedicada, storage, backup verificado e HTTPS são exigidos
    juntos.
    """

    repo_root = repo_root.resolve(strict=True)
    if mode_value != "pilot":
        raise ReportsGateError("reports_pilot_mode_not_selected")
    if not _parse_backend_flag(backend_flag):
        raise ReportsGateError("reports_pilot_backend_flag_missing")
    if not _read_target_frontend_flag(repo_root):
        raise ReportsGateError("reports_pilot_frontend_flag_missing")
    if pilot_authorization != PILOT_AUTHORIZATION_PHRASE:
        raise ReportsGateError("reports_pilot_authorization_missing")
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
    _validate_backup_manifest(
        backup_manifest_path,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    if not https_base_url:
        raise ReportsGateError("reports_https_base_url_missing")
    # M24D/M26.21 — a primeira ativação do piloto parte de um backend ainda
    # DESABILITADO (nunca houve laudos em produção antes), enquanto o checkout
    # já é o release alvo. O preflight aceita esse estado — ou um piloto já
    # ativo — desde que a superfície anônima esteja correta, o workspace exista
    # no release e o backend declare um estado reconhecido; só o postflight
    # (depois do deploy) exige reports_enabled=true e reports_mode="pilot"
    # efetivos no backend.
    check_https_workspace(
        https_base_url,
        repo_root=repo_root,
        expected_enabled=None,
        expected_mode=None,
        http_get=http_get,
    )
    return ReportsGateResult(enabled=True, storage_root=storage_root)


def verify_storage_and_unit_contract(
    *,
    repo_root: pathlib.Path,
    storage_root_value: str | None,
    effective_unit_text: str,
    expected_uid: int,
    expected_gid: int,
) -> pathlib.Path:
    """M24D — checagem de preparação (sem autorização/backup/HTTPS): prova
    que a raiz de storage e a ``ReadWritePaths`` efetiva já satisfazem o
    contrato exato que o gate do piloto vai exigir depois. Usado pelo
    script de preparação para verificar seu próprio trabalho antes de
    imprimir o caminho do manifesto — nunca autoriza nem habilita nada."""

    repo_root = repo_root.resolve(strict=True)
    storage_root = _validate_storage_root(
        storage_root_value,
        repo_root=repo_root,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    _validate_exact_readwritepath(effective_unit_text, storage_root=storage_root)
    return storage_root


def check_pilot_postflight(
    *,
    repo_root: pathlib.Path,
    mode_value: str | None,
    backend_flag: str | None,
    https_base_url: str | None,
    http_get=None,
) -> bool:
    """M24D — só deve ser chamado pelo deploy quando o modo alvo é "pilot".
    Ao contrário do preflight (que aceita um backend ainda desabilitado), o
    postflight sempre EXIGE que o release implantado esteja com
    reports_enabled=true e reports_mode="pilot" E que o backend em execução
    esteja de fato servindo o piloto — nunca aceita "ainda desabilitado".

    M26.21 — a prova do backend é o probe anônimo de ``/api/m15/laudos``: com
    o piloto servindo, ele responde 401 (autenticação recusando); desabilitado
    ou em produção bloqueada, responde 503 com código próprio. Nada disso
    precisa de sessão, e nenhum artefato protegido é exposto.
    """

    enabled = _parse_backend_flag(backend_flag)
    if mode_value != "pilot" or not enabled:
        raise ReportsGateError("reports_pilot_mode_not_selected")
    if not https_base_url:
        raise ReportsGateError("reports_https_base_url_missing")
    check_https_workspace(
        https_base_url,
        repo_root=repo_root,
        expected_enabled=True,
        expected_mode="pilot",
        http_get=http_get,
    )
    return True


def _service_ids() -> tuple[int, int]:
    try:
        return pwd.getpwnam("soprolife").pw_uid, grp.getgrnam("soprolife").gr_gid
    except KeyError as exc:
        raise ReportsGateError("reports_service_identity_missing") from exc


# M24D — fases de PREPARAÇÃO (verify-*) recebem argumentos diferentes das
# fases de gate (REPO_ROOT sozinho); por isso a validação de argc é por fase.
_PHASES_WITH_REPO_ROOT_ONLY = {
    "preflight",
    "postflight",
    "preflight-pilot",
    "postflight-pilot",
    "read-target-mode",
}
_PHASES_WITH_REPO_AND_STORAGE_ROOT = {"verify-storage-contract"}
_PHASES_WITH_SINGLE_PATH_ARG = {"verify-backup-manifest"}


def main(argv: list[str]) -> int:
    all_phases = (
        _PHASES_WITH_REPO_ROOT_ONLY
        | _PHASES_WITH_REPO_AND_STORAGE_ROOT
        | _PHASES_WITH_SINGLE_PATH_ARG
    )
    phase = argv[1] if len(argv) >= 2 else None
    usage_error = phase not in all_phases
    if not usage_error:
        if phase in _PHASES_WITH_REPO_ROOT_ONLY and len(argv) != 3:
            usage_error = True
        elif phase in _PHASES_WITH_REPO_AND_STORAGE_ROOT and len(argv) != 4:
            usage_error = True
        elif phase in _PHASES_WITH_SINGLE_PATH_ARG and len(argv) != 3:
            usage_error = True
    if usage_error:
        print(
            "usage: reports_go_live_gate.py "
            "preflight|postflight|preflight-pilot|postflight-pilot|"
            "read-target-mode REPO_ROOT\n"
            "   or: reports_go_live_gate.py verify-storage-contract "
            "REPO_ROOT STORAGE_ROOT\n"
            "   or: reports_go_live_gate.py verify-backup-manifest "
            "MANIFEST_PATH",
            file=sys.stderr,
        )
        return 2
    repo_root = pathlib.Path(argv[2]) if len(argv) > 2 else None
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
        elif phase == "preflight-pilot":
            uid, gid = _service_ids()
            unit_text = sys.stdin.read()
            result = check_pilot_preflight(
                repo_root=repo_root,
                mode_value=os.environ.get("M15_REPORTS_MODE"),
                backend_flag=os.environ.get("M15_REPORTS_ENABLED"),
                pilot_authorization=os.environ.get(
                    "SOPROLIFE_REPORTS_PILOT_AUTHORIZATION"
                ),
                storage_root_value=os.environ.get("M15_REPORTS_STORAGE_DIR"),
                backup_manifest_path=os.environ.get(
                    "SOPROLIFE_REPORTS_BACKUP_MANIFEST"
                ),
                effective_unit_text=unit_text,
                expected_uid=uid,
                expected_gid=gid,
                https_base_url=os.environ.get(
                    "SOPROLIFE_M15_HTTPS_BASE_URL"
                ),
            )
            print("true" if result.enabled else "false")
        elif phase == "postflight-pilot":
            check_pilot_postflight(
                repo_root=repo_root,
                mode_value=os.environ.get("M15_REPORTS_MODE"),
                backend_flag=os.environ.get("M15_REPORTS_ENABLED"),
                https_base_url=os.environ.get("SOPROLIFE_M15_HTTPS_BASE_URL"),
            )
            print("true")
        elif phase == "read-target-mode":
            print(read_target_frontend_mode(repo_root))
        elif phase == "verify-storage-contract":
            # M24D — usado pelo script de preparação (não pelo deploy): só
            # prova que storage + ReadWritePaths já estão corretos, sem
            # autorização, backup ou HTTPS. Nunca decide habilitar nada.
            uid, gid = _service_ids()
            unit_text = sys.stdin.read()
            verify_storage_and_unit_contract(
                repo_root=repo_root,
                storage_root_value=argv[3],
                effective_unit_text=unit_text,
                expected_uid=uid,
                expected_gid=gid,
            )
            print("true")
        elif phase == "verify-backup-manifest":
            uid, gid = _service_ids()
            _validate_backup_manifest(
                argv[2], expected_uid=uid, expected_gid=gid
            )
            print("true")
        else:
            expected = _parse_backend_flag(
                os.environ.get("M15_REPORTS_ENABLED")
            )
            if expected:
                raise ReportsGateError(M24C_PRODUCTION_BLOCKER)
            print("true" if expected else "false")
    except (
        ReportsGateError,
        # Falha de rede/TLS/URL vem do transporte compartilhado e sempre
        # rejeita. Sem esta linha ela saía como traceback: o deploy abortava
        # do mesmo jeito, mas o operador lia um stack trace em vez do motivo.
        https_transport.GateError,
        OSError,
        ValueError,
    ) as exc:
        print(
            f"ERRO REPORTS GO-LIVE (fail-closed): {exc}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
