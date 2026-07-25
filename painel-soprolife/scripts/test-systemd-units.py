#!/usr/bin/env python3
"""
SoproLife — Validação sintática/segura das unidades systemd (M14.3A.1).

100% offline e determinístico: NÃO roda systemctl nem systemd-analyze.
Valida estrutura INI, diretivas obrigatórias (lock, timeout, journal) e
ausência de segredos em todas as unidades + EnvironmentFile de exemplo.

Uso: python3 painel-soprolife/scripts/test-systemd-units.py
Exit: 0 = todos os casos passaram | 1 = houve falha.
"""

import configparser
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SYSTEMD = RAIZ / "systemd"

FALHAS = 0


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + detalhe if detalhe else ''}")


def parse_unit(path):
    """Parse INI de unidade systemd (preserva case das chaves)."""
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string(path.read_text(encoding="utf-8"))
    return cp


SEGREDOS = re.compile(
    r"(?i)(password|client_secret|refresh_token|access_token|api[_-]?key|"
    r"AIza[0-9A-Za-z_-]{10,}|ya29\.|BEGIN [A-Z]+ PRIVATE KEY)")

SERVICE = SYSTEMD / "soprolife-operational-refresh.service"
TIMER = SYSTEMD / "soprolife-operational-refresh.timer"
ENV_EX = SYSTEMD / "operational-refresh.env.example"

print("── Unidade .service ──")
caso("arquivo existe", SERVICE.exists())
svc = parse_unit(SERVICE)
caso("seções Unit/Service/Install presentes",
     all(s in svc for s in ("Unit", "Service", "Install")))
caso("Type=oneshot", svc["Service"].get("Type") == "oneshot")
exec_start = svc["Service"].get("ExecStart", "")
caso("lock via flock (nunca duas execuções)", "flock -n" in exec_start)
caso("timeout definido", "TimeoutStartSec" in svc["Service"])
caso("timeout razoável (<= 1h)",
     float(svc["Service"].get("TimeoutStartSec", "9999")) <= 3600)
caso("saída no journal", svc["Service"].get("StandardOutput") == "journal")
caso("logs limitados (LogRateLimit*)",
     "LogRateLimitIntervalSec" in svc["Service"] and "LogRateLimitBurst" in svc["Service"])
caso("modo padrão seguro é check (offline)",
     "SOPROLIFE_REFRESH_MODE=check" in SERVICE.read_text(encoding="utf-8"))
caso("EnvironmentFile opcional (prefixo -)",
     svc["Service"].get("EnvironmentFile", "").startswith("-"))
caso("chama o comando operacional (nunca Apps Script)",
     "soprolife-operational-refresh.sh" in exec_start and
     ".gs" not in exec_start and "apps-script" not in exec_start.lower())
caso("stale (10) não derruba a unidade; auth (11) derruba de propósito",
     svc["Service"].get("SuccessExitStatus") == "0 10")

print("── Unidade .timer ──")
caso("arquivo existe", TIMER.exists())
tmr = parse_unit(TIMER)
caso("seções Unit/Timer/Install presentes",
     all(s in tmr for s in ("Unit", "Timer", "Install")))
caso("frequência razoável (OnUnitActiveSec=6h)",
     tmr["Timer"].get("OnUnitActiveSec") == "6h")
caso("Persistent=true (recupera disparo perdido)",
     tmr["Timer"].get("Persistent") == "true")
caso("RandomizedDelaySec definido", "RandomizedDelaySec" in tmr["Timer"])
caso("timer exige o service correto",
     tmr["Unit"].get("Requires") == "soprolife-operational-refresh.service")

print("── EnvironmentFile de exemplo ──")
caso("arquivo existe", ENV_EX.exists())
env_txt = ENV_EX.read_text(encoding="utf-8")
caso("padrão do exemplo é check", "SOPROLIFE_REFRESH_MODE=check" in env_txt)
caso("exemplo proíbe segredos explicitamente", "NUNCA" in env_txt)

print("── Nenhum segredo em nenhuma unidade/script preparado ──")
for f in [SERVICE, TIMER, ENV_EX,
          RAIZ / "scripts" / "install-operational-refresh.sh",
          RAIZ / "scripts" / "uninstall-operational-refresh.sh",
          RAIZ / "scripts" / "soprolife-operational-refresh.sh"]:
    txt = f.read_text(encoding="utf-8")
    caso(f"sem segredo em {f.name}", not SEGREDOS.search(txt))

print("── Instalador nunca ativa sozinho ──")
inst = (RAIZ / "scripts" / "install-operational-refresh.sh").read_text(encoding="utf-8")
caso("dry-run é o padrão do instalador", "DRY_RUN=1" in inst)
caso("enable exige flag explícita", "--enable-timer" in inst)

print("── M21: atualização automática de Marketing ──")
update_service = SYSTEMD / "soprolife-update-data.service"
update_timer = SYSTEMD / "soprolife-update-data.timer"
m15_service = SYSTEMD / "soprolife-m15-api.service"
caso("unit de atualização existe", update_service.exists())
caso("timer de atualização existe", update_timer.exists())
caso("unit da API M15 existe", m15_service.exists())

upd = parse_unit(update_service)
upd_text = update_service.read_text(encoding="utf-8")
upd_directives = "\n".join(
    line for line in upd_text.splitlines() if not line.lstrip().startswith("#")
)
caso("atualização roda como soprolife, nunca root",
     upd["Service"].get("User") == "soprolife" and
     not re.search(r"(?m)^User=root$", upd_directives))
caso("credencial durável é explícita e obrigatória",
     "SOPROLIFE_MARKETING_CREDENTIALS=/opt/soprolife/secrets/marketing-readonly.json"
     in upd_text and
     "SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT=1" in upd_text)
caso("fila manual privada é explícita",
     "SOPROLIFE_MARKETING_REFRESH_QUEUE=/opt/soprolife/soprolife-site/"
     "painel-soprolife/nucleo-m15/var/marketing-refresh-request.json" in upd_text)
caso("execução de dados tem timeout e hardening",
     upd["Service"].get("TimeoutStartSec") == "480" and
     upd["Service"].get("NoNewPrivileges") == "true")
caso("unit de atualização não contém segredo",
     not SEGREDOS.search(upd_text))

update_sh = (RAIZ / "scripts" / "update-local-data.sh").read_text(encoding="utf-8")
caso("script tem lock não bloqueante",
     "flock -n -E 99" in update_sh)
caso("pedido só é consumido depois da tentativa de Marketing",
     update_sh.index('echo "5/15 - Atualizando Marketing & SEO..."') <
     update_sh.index('_MKT_ATTEMPTED=1') <
     update_sh.index('rm -f -- "$_MKT_QUEUE"') <
     update_sh.index('echo "6/15 - Atualizando Leads..."'))
caso("pedido permanece pendente se conector não executa",
     'if [ "$_MKT_REQUESTED" -eq 1 ] && [ "$_MKT_ATTEMPTED" -eq 1 ]' in update_sh
     and "conector não chegou a executar" in update_sh)
caso("ausência de flock falha fechado",
     "atualização cancelada para evitar concorrência" in update_sh)

m15_text = m15_service.read_text(encoding="utf-8")
caso("API e timer usam exatamente a mesma fila",
     "M15_MARKETING_REFRESH_QUEUE=/opt/soprolife/soprolife-site/"
     "painel-soprolife/nucleo-m15/var/marketing-refresh-request.json" in m15_text)
caso("fila fica no único diretório gravável da API",
     "ReadWritePaths=/opt/soprolife/soprolife-site/painel-soprolife/"
     "nucleo-m15/var" in m15_text)
caso("unit da API não contém segredo", not SEGREDOS.search(m15_text))

timer_cfg = parse_unit(update_timer)
caso("timer automático continua persistente a cada 10 minutos",
     timer_cfg["Timer"].get("OnUnitActiveSec") == "10min" and
     timer_cfg["Timer"].get("Persistent") == "true")

print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} falha(s).")
    sys.exit(1)
print("RESULTADO: todos os casos passaram.")
