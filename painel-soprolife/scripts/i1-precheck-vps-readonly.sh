#!/usr/bin/env bash
# I1 F0 — Pré-check READ-ONLY da VPS (roda LOCALMENTE na estação).
#
# USO:
#   bash painel-soprolife/scripts/i1-precheck-vps-readonly.sh root@<VPS_TAILSCALE>
#
# O QUE FAZ:
#   1. Conecta via SSH ao alvo informado e roda SOMENTE comandos de leitura
#      (whoami/hostname/pwd/date/systemctl status|cat|show|list-*/find/id/
#      getent/passwd -S/git status|log/ls/curl GET local do painel).
#   2. Analisa a saída LOCALMENTE (FAIL/WARN) — nenhum grep/análise no remoto.
#   3. SANITIZA a saída localmente (tokens, URLs, e-mails, telefones, CPF,
#      IP Tailscale) ANTES de gravar qualquer coisa.
#   4. Salva o relatório em:
#      ~/Documents/SoproLife/_REVISOES_GPT/i1-precheck-vps-readonly-YYYYMMDD-HHMMSS.txt
#
# O QUE NUNCA FAZ:
#   - nenhum comando remoto de escrita (rm/cp/mv/chmod/chown/useradd/mkdir/
#     touch/systemctl start|stop|restart|enable|disable|daemon-reload/
#     apt/dnf/pip/npm) e nenhum sudo;
#   - nunca imprime/grava conteúdo de arquivo privado ou credencial;
#   - nunca grava saída NÃO sanitizada.
#
# Exit: 0 = relatório gerado | 1 = falha de SSH/coleta | 2 = uso incorreto.
# Docs: painel-soprolife/docs/i1-precheck-vps.md

set -u

# ── Argumento ────────────────────────────────────────────────────────────────
if [ $# -ne 1 ] || [ -z "${1:-}" ]; then
  echo "Uso: bash $0 root@<VPS_TAILSCALE>"
  echo "  (exatamente 1 argumento: o alvo SSH da VPS via Tailscale)"
  exit 2
fi
SSH_TARGET="$1"

REPORT_DIR="$HOME/Documents/SoproLife/_REVISOES_GPT"
REPORT_FILE="$REPORT_DIR/i1-precheck-vps-readonly-$(date +%Y%m%d-%H%M%S).txt"
REPO_REMOTO="/opt/soprolife/soprolife-site"
PAINEL_REMOTO="$REPO_REMOTO/painel-soprolife"

mkdir -p "$REPORT_DIR"

# ── Coleta remota (READ-ONLY; heredoc com aspas = nada expande localmente) ──
echo "Coletando dados read-only de ${SSH_TARGET%%@*}@<REDIGIDO> ..."
RAW_OUTPUT=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_TARGET" bash -s <<'REMOTE_EOF'
set -u
R="/opt/soprolife/soprolife-site"
P="$R/painel-soprolife"

echo "### [1] identidade"
whoami; hostname; date -Is; pwd

echo "### [2] units instalados"
systemctl list-unit-files 'soprolife-*' --no-pager 2>/dev/null || true

echo "### [3] unit update (cat)"
systemctl cat soprolife-update-data.service 2>/dev/null || echo "(unit update nao instalado)"

echo "### [4] unit painel (cat)"
systemctl cat soprolife-painel.service 2>/dev/null || echo "(unit painel nao instalado)"

echo "### [5] timers e ultima execucao"
systemctl list-timers 'soprolife-*' --all --no-pager 2>/dev/null || true
systemctl show soprolife-update-data.service -p Result -p ExecMainStatus 2>/dev/null || true

echo "### [6] usuario soprolife"
id soprolife 2>/dev/null || echo "(usuario soprolife nao existe)"
getent passwd soprolife 2>/dev/null || true
passwd -S soprolife 2>/dev/null || echo "(passwd -S indisponivel ou usuario ausente)"
ls -la /var/lib/soprolife/.ssh/ 2>/dev/null || echo "(sem /var/lib/soprolife/.ssh)"

echo "### [7] repositorio"
git -C "$R" status --short 2>/dev/null || echo "(repo nao encontrado em $R)"
echo "--- log ---"
git -C "$R" log --oneline -3 2>/dev/null || true

echo "### [8] permissoes data/ e data-private/"
ls -ld "$P/data" "$P/data-private" 2>/dev/null || echo "(diretorios de dados nao encontrados)"
ls -l "$P"/data/*.local.json 2>/dev/null || echo "(nenhum *.local.json em data/)"

echo "### [9] configs/ADC do root (existencia e permissao — nunca conteudo)"
ls -ld /root/.config/soprolife/painel 2>/dev/null || echo "(configs do painel ausentes no root)"
ls -l  /root/.config/soprolife/painel 2>/dev/null || true
ls -l  /root/.config/gcloud/application_default_credentials.json 2>/dev/null || echo "(ADC ausente no root)"

echo "### [10] venvs"
ls -ld /root/.local/share/soprolife/venvs/google-sheets 2>/dev/null || echo "(venv do root ausente)"
ls -ld /var/lib/soprolife/.local/share/soprolife/venvs/google-sheets 2>/dev/null || echo "(venv do soprolife ausente)"

echo "### [11] http painel (localhost da VPS)"
curl -s -o /dev/null -w "http_code=%{http_code}" --max-time 8 http://127.0.0.1:8765/painel-soprolife/ 2>/dev/null || echo "http_code=000"
echo
REMOTE_EOF
)
SSH_EXIT=$?

if [ $SSH_EXIT -ne 0 ] && [ -z "$RAW_OUTPUT" ]; then
  echo "ERRO: falha de SSH para o alvo informado (exit=$SSH_EXIT)."
  echo "  Verifique Tailscale ativo, alvo correto (root@<VPS_TAILSCALE>) e chave carregada."
  exit 1
fi

# ── Sanitização LOCAL (sempre antes de gravar ou exibir) ─────────────────────
sanitize() {
  sed -E \
    -e 's#script\.google\.com[^[:space:]"]*#[URL-APPS-SCRIPT-REDACTED]#g' \
    -e 's#AKfycb[A-Za-z0-9_-]*#[DEPLOYMENT-ID-REDACTED]#g' \
    -e 's#[Bb]earer[[:space:]]+[^[:space:]]+#Bearer [REDACTED]#g' \
    -e 's#ya29\.[A-Za-z0-9._-]*#[TOKEN-REDACTED]#g' \
    -e 's#AIza[A-Za-z0-9_-]*#[API-KEY-REDACTED]#g' \
    -e 's#/spreadsheets/d/[A-Za-z0-9_-]*#/spreadsheets/d/[ID-REDACTED]#g' \
    -e 's#(token|api_key|secret|password)([=: ]+)[^[:space:]]+#\1\2[REDACTED]#Ig' \
    -e 's#[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}#[EMAIL-REDACTED]#g' \
    -e 's#\(?[0-9]{2}\)?[ .-]?9?[0-9]{4}[- ][0-9]{4}#[FONE-REDACTED]#g' \
    -e 's#\+55[0-9 ()-]{10,}#[FONE-REDACTED]#g' \
    -e 's#[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}#[CPF-REDACTED]#g' \
    -e 's#\b100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b#[TAILSCALE-IP-REDACTED]#g'
}
SAFE_OUTPUT=$(printf '%s\n' "$RAW_OUTPUT" | sanitize)
unset RAW_OUTPUT

# ── Análise LOCAL (sobre a saída já sanitizada) ──────────────────────────────
FAILS=0; WARNS=0
NOTES=""
note() { NOTES="${NOTES}  $1: $2"$'\n'; }

if printf '%s' "$SAFE_OUTPUT" | grep -q "(usuario soprolife nao existe)"; then
  note "INFO" "usuario soprolife nao existe — criar na F1 (fase aprovada)"
else
  if printf '%s' "$SAFE_OUTPUT" | grep -E '^uid=.*soprolife' | grep -qE '\((sudo|wheel|admin)\)'; then
    note "FAIL" "usuario soprolife em grupo administrativo (sudo/wheel/admin)"; FAILS=$((FAILS+1))
  else
    note "OK" "usuario existe e nao esta em grupo administrativo"
  fi
  if printf '%s' "$SAFE_OUTPUT" | grep -qE '^soprolife NP '; then
    note "FAIL" "senha do usuario NAO travada (NP) — travar antes de prosseguir"; FAILS=$((FAILS+1))
  fi
  if ! printf '%s' "$SAFE_OUTPUT" | grep -q "(sem /var/lib/soprolife/.ssh)"; then
    note "WARN" "existe ~soprolife/.ssh — revisar authorized_keys manualmente"; WARNS=$((WARNS+1))
  fi
fi

if printf '%s' "$SAFE_OUTPUT" | grep -q "(repo nao encontrado"; then
  note "FAIL" "repo nao encontrado em $REPO_REMOTO"; FAILS=$((FAILS+1))
elif printf '%s' "$SAFE_OUTPUT" | sed -n '/### \[7\]/,/--- log ---/p' | grep -qE '^\s*[MADRU?]{1,2} '; then
  note "WARN" "repo da VPS com mudancas locais — resolver antes da janela"; WARNS=$((WARNS+1))
else
  note "OK" "repo limpo"
fi

if printf '%s' "$SAFE_OUTPUT" | grep -q "^User=root"; then
  note "INFO" "update roda como root hoje (motivo do I1)"
fi
if printf '%s' "$SAFE_OUTPUT" | grep -q "(ADC ausente no root)"; then
  note "WARN" "ADC nao encontrado no HOME do root"; WARNS=$((WARNS+1))
fi
if printf '%s' "$SAFE_OUTPUT" | grep -q "http_code=200"; then
  note "OK" "painel HTTP 200 (localhost da VPS)"
else
  note "WARN" "painel nao respondeu 200 no localhost da VPS"; WARNS=$((WARNS+1))
fi

# Scan /root/ fixo: feito LOCALMENTE sobre a copia local dos scripts (mesmo
# codigo do deploy; comparar o commit da secao [7] com o local).
if grep -rn "/root/" painel-soprolife/scripts/ 2>/dev/null | grep -v '\.pyc' | grep -v "$(basename "$0")" >/dev/null; then
  note "FAIL" "caminho /root/ fixo encontrado nos scripts locais"; FAILS=$((FAILS+1))
else
  note "OK" "nenhum /root/ fixo nos scripts (verificado na copia local)"
fi

VEREDITO="GO condicional: revisar WARNs com o GPT antes da janela F1-F5."
[ "$FAILS" -gt 0 ] && VEREDITO="NO-GO: resolver FAILs antes de agendar F1-F5."

# ── Gravação do relatório (só conteúdo sanitizado) ───────────────────────────
{
  echo "# I1 F0 — pre-check read-only da VPS"
  echo "# gerado em: $(date -Is) | alvo: [REDIGIDO] | ssh_exit=$SSH_EXIT"
  echo "# sanitizado localmente antes da gravacao (ver script)."
  echo
  echo "## ANALISE LOCAL — FAILs: $FAILS | WARNs: $WARNS"
  printf '%s' "$NOTES"
  echo "  VEREDITO: $VEREDITO"
  echo
  echo "## COLETA REMOTA (sanitizada)"
  printf '%s\n' "$SAFE_OUTPUT"
} > "$REPORT_FILE"
chmod 600 "$REPORT_FILE"

echo
echo "== ANALISE — FAILs: $FAILS | WARNs: $WARNS =="
printf '%s' "$NOTES"
echo "  VEREDITO: $VEREDITO"
echo
echo "Relatorio sanitizado salvo em:"
echo "  $REPORT_FILE"
exit 0
