"""M24D follow-up — fiação do deploy oficial para o piloto de laudos.

Cobre exclusivamente o que este follow-up mudou: seleção do gate certo a
partir do modo alvo versionado, aceitação da primeira transição
disabled → pilot no preflight, exigência de concordância enabled/pilot no
postflight, bloqueio contínuo de produção, e as garantias estáticas do
script de preparação (idempotente, nunca reinicia/habilita) e do escopo do
commit de ativação. O restante do contrato do piloto (F2/F3/F4, ceiling de
estado, backup manifest, etc.) já tem cobertura em test_m24d_reports_pilot.py
e não é duplicado aqui.
"""

from __future__ import annotations

import json
import os
import pty
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
NUCLEO_M15_DIR = SCRIPTS_DIR.parent
REPO_ROOT = NUCLEO_M15_DIR.parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))
import reports_go_live_gate as gate  # noqa: E402

BASE = "https://pilot-deploy.example.invalid"
PREPARE_SCRIPT = SCRIPTS_DIR / "prepare-reports-pilot-vps.sh"
BACKUP_SCRIPT = SCRIPTS_DIR / "backup-reports-pilot.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_in_pty(
    argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float = 15
) -> tuple[int, str]:
    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            if time.monotonic() >= deadline:
                process.kill()
                raise AssertionError(f"processo excedeu {timeout}s")
            readable, _, _ = select.select([master_fd], [], [], 0.1)
            if readable:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError:
                    chunk = b""
                if chunk:
                    chunks.append(chunk)
                elif process.poll() is not None:
                    break
            if process.poll() is not None and not readable:
                break
        return process.wait(), b"".join(chunks).decode(errors="replace")
    finally:
        os.close(master_fd)


def _synthetic_repo(tmp_path: Path, *, mode: str, enabled: bool) -> Path:
    repo = tmp_path / "synthetic-repo"
    config = repo / "painel-soprolife/data/m15-config.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "enabled": True,
                "reports_enabled": enabled,
                "reports_mode": mode,
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


def _https_responses(*, served_enabled: bool, served_mode: str):
    api_status = 401 if served_enabled else 503
    api_code = "http_401" if served_enabled else "relatorios_desabilitados"
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
                    "reports_enabled": served_enabled,
                    "reports_mode": served_mode,
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


def _artifacts(tmp_path: Path) -> tuple[Path, Path]:
    dump = tmp_path / "pg.dump"
    dump.write_bytes(b"synthetic-dump")
    archive = tmp_path / "storage.tar"
    archive.write_bytes(b"synthetic-archive")
    return dump, archive


def _manifest(tmp_path: Path) -> Path:
    import reports_pilot_backup as backup_tool

    dump, archive = _artifacts(tmp_path)
    manifest = backup_tool.build_manifest(
        postgresql_dump_path=dump,
        storage_archive_path=archive,
        report_documents=1,
        report_document_versions=1,
        physician_profiles=1,
    )
    path = tmp_path / "manifest.json"
    backup_tool.write_manifest_atomic(path, manifest)
    return path


# ------------------------------------------------- read_target_frontend_mode


def test_read_target_mode_valida_consistencia_com_reports_enabled(tmp_path):
    disabled_ok = _synthetic_repo(tmp_path / "a", mode="disabled", enabled=False)
    assert gate.read_target_frontend_mode(disabled_ok) == "disabled"

    pilot_ok = _synthetic_repo(tmp_path / "b", mode="pilot", enabled=True)
    assert gate.read_target_frontend_mode(pilot_ok) == "pilot"

    production_ok = _synthetic_repo(tmp_path / "c", mode="production", enabled=True)
    assert gate.read_target_frontend_mode(production_ok) == "production"


@pytest.mark.parametrize(
    "mode,enabled",
    [("disabled", True), ("pilot", False), ("production", False)],
)
def test_read_target_mode_recusa_inconsistencia(tmp_path, mode, enabled):
    repo = _synthetic_repo(tmp_path, mode=mode, enabled=enabled)
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.read_target_frontend_mode(repo)
    assert str(caught.value) == "versioned_reports_mode_flag_mismatch"


def test_read_target_mode_recusa_valor_desconhecido(tmp_path):
    repo = tmp_path / "synthetic-repo"
    config = repo / "painel-soprolife/data/m15-config.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "enabled": True,
                "reports_enabled": True,
                "reports_mode": "beta",
                "api_base": "/painel-soprolife/api/m15",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.read_target_frontend_mode(repo)
    assert str(caught.value) == "versioned_reports_mode_invalid"


# --------------------------------------- primeira ativação: disabled → pilot


def test_pilot_preflight_aceita_primeira_ativacao_partindo_de_disabled(tmp_path):
    """O release atualmente SERVIDO nunca teve laudos habilitados (served
    disabled); o preflight precisa aceitar essa transição para o piloto
    quando todas as condições dedicadas estão satisfeitas."""

    repo = _synthetic_repo(tmp_path, mode="pilot", enabled=True)
    root = _private_root(tmp_path)
    manifest = _manifest(tmp_path)
    result = gate.check_pilot_preflight(
        repo_root=repo,
        mode_value="pilot",
        backend_flag="true",
        pilot_authorization=gate.PILOT_AUTHORIZATION_PHRASE,
        storage_root_value=str(root),
        backup_manifest_path=str(manifest),
        effective_unit_text=f"[Service]\nReadWritePaths={root}\n",
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        https_base_url=BASE,
        http_get=_getter(
            _https_responses(served_enabled=False, served_mode="disabled")
        ),
    )
    assert result.enabled is True


def test_pilot_preflight_tambem_aceita_piloto_ja_ativo(tmp_path):
    """Um segundo deploy do MESMO release pilot, com o piloto já servido,
    também precisa passar (não é só a primeira ativação)."""

    repo = _synthetic_repo(tmp_path, mode="pilot", enabled=True)
    root = _private_root(tmp_path)
    manifest = _manifest(tmp_path)
    result = gate.check_pilot_preflight(
        repo_root=repo,
        mode_value="pilot",
        backend_flag="true",
        pilot_authorization=gate.PILOT_AUTHORIZATION_PHRASE,
        storage_root_value=str(root),
        backup_manifest_path=str(manifest),
        effective_unit_text=f"[Service]\nReadWritePaths={root}\n",
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        https_base_url=BASE,
        http_get=_getter(
            _https_responses(served_enabled=True, served_mode="pilot")
        ),
    )
    assert result.enabled is True


@pytest.mark.parametrize(
    "overrides,expected_code",
    [
        ({"mode_value": "disabled"}, "reports_pilot_mode_not_selected"),
        ({"backend_flag": "false"}, "reports_pilot_backend_flag_missing"),
        ({"pilot_authorization": None}, "reports_pilot_authorization_missing"),
    ],
)
def test_primeira_ativacao_recusada_sem_todas_as_condicoes(
    tmp_path, overrides, expected_code
):
    repo = _synthetic_repo(tmp_path, mode="pilot", enabled=True)
    root = _private_root(tmp_path)
    manifest = _manifest(tmp_path)
    values = dict(
        repo_root=repo,
        mode_value="pilot",
        backend_flag="true",
        pilot_authorization=gate.PILOT_AUTHORIZATION_PHRASE,
        storage_root_value=str(root),
        backup_manifest_path=str(manifest),
        effective_unit_text=f"[Service]\nReadWritePaths={root}\n",
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        https_base_url=BASE,
        http_get=_getter(
            _https_responses(served_enabled=False, served_mode="disabled")
        ),
    )
    values.update(overrides)
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_pilot_preflight(**values)
    assert str(caught.value) == expected_code


# ------------------------------------------------------ postflight do piloto


def test_pilot_postflight_exige_enabled_e_modo_pilot_servidos(tmp_path):
    result = gate.check_pilot_postflight(
        mode_value="pilot",
        backend_flag="true",
        https_base_url=BASE,
        http_get=_getter(
            _https_responses(served_enabled=True, served_mode="pilot")
        ),
    )
    assert result is True


def test_pilot_postflight_recusa_quando_env_local_nao_esta_em_pilot_habilitado():
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_pilot_postflight(
            mode_value="pilot",
            backend_flag="false",
            https_base_url=BASE,
        )
    assert str(caught.value) == "reports_pilot_mode_not_selected"

    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_pilot_postflight(
            mode_value="disabled",
            backend_flag="true",
            https_base_url=BASE,
        )
    assert str(caught.value) == "reports_pilot_mode_not_selected"


def test_pilot_postflight_recusa_quando_servido_diverge_do_alvo():
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_pilot_postflight(
            mode_value="pilot",
            backend_flag="true",
            https_base_url=BASE,
            http_get=_getter(
                _https_responses(served_enabled=False, served_mode="disabled")
            ),
        )
    assert str(caught.value) == "reports_https_target_flag_mismatch"


# ------------------------------------------------------- produção bloqueada


def test_producao_permanece_bloqueada_no_gate_unico(tmp_path):
    repo = _synthetic_repo(tmp_path, mode="production", enabled=True)
    root = _private_root(tmp_path)
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_preflight(
            repo_root=repo,
            backend_flag="true",
            reports_authorization=gate.REPORTS_AUTHORIZATION_PHRASE,
            storage_root_value=str(root),
            backup_attestation=gate.BACKUP_ATTESTATION_PHRASE,
            effective_unit_text=f"[Service]\nReadWritePaths={root}\n",
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
            https_base_url=BASE,
            http_get=_getter(
                _https_responses(served_enabled=True, served_mode="production")
            ),
        )
    assert str(caught.value) == gate.M24C_PRODUCTION_BLOCKER


# ------------------------------------- script de preparação (estático/idempotente)


def test_preparacao_mantem_backup_em_terminal_interativo(tmp_path):
    """O preparo não pode capturar stdout do backup: o backup continua vendo
    stdin e stdout como TTY e devolve o manifesto por canal privado 0600."""

    fake_repo = tmp_path / "repo"
    fake_scripts = fake_repo / "painel-soprolife/nucleo-m15/scripts"
    fake_systemd = fake_repo / "painel-soprolife/systemd"
    fake_scripts.mkdir(parents=True)
    fake_systemd.mkdir(parents=True)
    shutil.copy2(PREPARE_SCRIPT, fake_scripts / PREPARE_SCRIPT.name)
    (fake_scripts / "reports_go_live_gate.py").write_text("", encoding="utf-8")
    (fake_systemd / "soprolife-m15-api-reports-pilot.override.conf.example").write_text(
        "[Service]\nReadWritePaths=/synthetic\n", encoding="utf-8"
    )

    manifest = tmp_path / "manifest-sintetico.json"
    _write_executable(
        fake_scripts / BACKUP_SCRIPT.name,
        """#!/usr/bin/env bash
set -Eeuo pipefail
[[ -t 0 && -t 1 ]] || {
  echo "BACKUP_SEM_TTY" >&2
  exit 91
}
[[ -n "${SOPROLIFE_REPORTS_BACKUP_RESULT_FILE-}" ]]
printf '%s\\n' "$TEST_MANIFEST_PATH" >"$SOPROLIFE_REPORTS_BACKUP_RESULT_FILE"
echo "BACKUP_TTY_OK"
""",
    )

    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    _write_executable(mock_bin / "sudo", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(mock_bin / "id", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        mock_bin / "systemctl",
        """#!/usr/bin/env bash
if [[ "${1-}" == "cat" ]]; then
  printf '[Service]\\nReadWritePaths=%s\\n' "$TEST_STORAGE_ROOT"
fi
""",
    )
    _write_executable(mock_bin / "python3", "#!/usr/bin/env bash\nexit 0\n")

    storage = tmp_path / "storage"
    backup_dest = tmp_path / "backups"
    storage.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{mock_bin}:{env['PATH']}",
            "TEST_MANIFEST_PATH": str(manifest),
            "TEST_STORAGE_ROOT": str(storage),
        }
    )
    rc, output = _run_in_pty(
        [
            "/bin/bash",
            str(fake_scripts / PREPARE_SCRIPT.name),
            str(storage),
            str(backup_dest),
        ],
        cwd=fake_repo,
        env=env,
    )

    assert rc == 0, output
    assert "BACKUP_TTY_OK" in output
    assert "BACKUP_SEM_TTY" not in output
    assert f"MANIFEST_PATH={manifest}" in output


def test_backup_aplica_dono_e_modo_exigidos_pelo_gate(tmp_path):
    """Executa o backup em PTY com sudo/PostgreSQL/tar simulados e comprova
    os comandos de ownership/mode sem tocar banco, storage ou privilégios."""

    storage = tmp_path / "storage"
    destination = tmp_path / "backups"
    storage.mkdir()
    result_file = tmp_path / "backup-result"
    result_file.touch(mode=0o600)
    os.chmod(result_file, 0o600)
    sudo_log = tmp_path / "sudo.log"

    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    _write_executable(
        mock_bin / "sudo",
        """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\n' "$*" >>"$TEST_SUDO_LOG"
if [[ "${1-}" == "-v" ]]; then
  exit 0
fi
if [[ "${1-}" == "-u" ]]; then
  shift 2
  case "${1-}" in
    pg_dump) printf 'synthetic-postgresql-dump' ;;
    psql) printf '0\\n' ;;
    *) exit 93 ;;
  esac
  exit 0
fi
case "${1-}" in
  test|chmod|mv|rm)
    command "$@"
    ;;
  install)
    destination="${!#}"
    mkdir -p "$destination"
    ;;
  tee)
    command tee "$2"
    ;;
  pg_restore|chown)
    exit 0
    ;;
  tar)
    archive=""
    previous=""
    for argument in "$@"; do
      if [[ "$previous" == "--file" ]]; then
        archive="$argument"
      fi
      previous="$argument"
    done
    if [[ " $* " == *" --create "* ]]; then
      printf 'synthetic-storage-archive' >"$archive"
    fi
    ;;
  python3)
    manifest=""
    previous=""
    for argument in "$@"; do
      if [[ "$previous" == "--manifest" ]]; then
        manifest="$argument"
      fi
      previous="$argument"
    done
    printf '{}\\n' >"$manifest"
    ;;
  *)
    exit 94
    ;;
esac
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{mock_bin}:{env['PATH']}",
            "SOPROLIFE_REPORTS_BACKUP_RESULT_FILE": str(result_file),
            "TEST_SUDO_LOG": str(sudo_log),
        }
    )
    rc, output = _run_in_pty(
        ["/bin/bash", str(BACKUP_SCRIPT), str(storage), str(destination)],
        cwd=REPO_ROOT,
        env=env,
    )

    assert rc == 0, output
    manifest_path = Path(result_file.read_text(encoding="utf-8").strip())
    assert manifest_path.is_absolute()
    log = sudo_log.read_text(encoding="utf-8").splitlines()
    dump_chown = [
        line
        for line in log
        if line.startswith("chown soprolife:soprolife ")
        and "soprolife_m15-" in line
        and line.endswith(".dump.partial")
    ]
    archive_chown = [
        line
        for line in log
        if line.startswith("chown soprolife:soprolife ")
        and "reports-storage-" in line
        and line.endswith(".tar.partial")
    ]
    assert len(dump_chown) == 1
    assert len(archive_chown) == 1
    assert f"chown soprolife:soprolife {manifest_path}" in log
    assert f"chmod 0600 {manifest_path}" in log
    dumps = list(destination.glob("*.dump"))
    archives = list(destination.glob("*.tar"))
    assert len(dumps) == 1
    assert len(archives) == 1
    for artifact in dumps:
        assert artifact.stat().st_mode & 0o777 == 0o600
    for artifact in archives:
        assert artifact.stat().st_mode & 0o777 == 0o600


def test_preparacao_nunca_reinicia_habilita_ou_toca_config():
    # Só o código executável importa aqui — o cabeçalho documenta em prosa
    # exatamente essas restrições, então linhas de comentário são ignoradas
    # para não confundir "menciona a restrição" com "faz a coisa proibida".
    codigo = "\n".join(
        line
        for line in (SCRIPTS_DIR / "prepare-reports-pilot-vps.sh")
        .read_text(encoding="utf-8")
        .splitlines()
        if not line.strip().startswith("#")
    )
    proibidos = (
        "systemctl restart",
        "systemctl enable",
        "/opt/soprolife/secrets/m15.env",
        "painel-soprolife/data/m15-config.json",
        "M15_REPORTS_ENABLED=true",
        "git pull",
        "git checkout",
    )
    for termo in proibidos:
        assert termo not in codigo, f"termo proibido encontrado no código: {termo}"
    for termo in ("install -d", "daemon-reload"):
        assert termo in codigo, f"marcador esperado ausente: {termo}"


def test_preparacao_tem_sintaxe_valida_e_e_executavel():
    path = SCRIPTS_DIR / "prepare-reports-pilot-vps.sh"
    assert os.access(path, os.X_OK)
    subprocess.run(["bash", "-n", str(path)], check=True)


# --------------------------------------------- escopo do commit de ativação


def test_commit_de_ativacao_altera_somente_o_config_do_frontend():
    """Quando o HEAD atual for exatamente o commit de ativação do piloto,
    ele precisa mudar SOMENTE painel-soprolife/data/m15-config.json. No
    commit de fiação (este), o teste é pulado — não há nada para checar
    ainda."""

    message = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if message != "feat(m24): activate controlled reports pilot":
        pytest.skip("HEAD não é o commit de ativação do piloto")
    files = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert files == ["painel-soprolife/data/m15-config.json"]
