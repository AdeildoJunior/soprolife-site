#!/usr/bin/env bash
# check-vps-google-adc.sh
#
# Diagnóstico de disponibilidade do Google Sheets ADC na VPS.
# Verifica pré-requisitos de forma segura — nunca imprime spreadsheet_id,
# token, client_secret, refresh_token, URLs privadas ou dados pessoais.
#
# Uso:
#   painel-soprolife/scripts/check-vps-google-adc.sh

set -uo pipefail

cd "$(dirname "$0")/../../" || exit 1

# ---------------------------------------------------------------------------
# Caminhos (overridáveis por variáveis de ambiente)
# ---------------------------------------------------------------------------
_ADC_CONFIG="${GCLOUD_ADC_CONFIG:-$HOME/.config/gcloud/application_default_credentials.json}"
_SHEETS_CONFIG="${SOPROLIFE_SHEETS_CONFIG:-$HOME/.config/soprolife/painel/google-sheets.local.json}"
_VENV_PYTHON="$HOME/.local/share/soprolife/venvs/google-sheets/bin/python"
_REQUIREMENTS="painel-soprolife/requirements-google.txt"
_SHEETS_SCRIPT="painel-soprolife/scripts/read-sheets-summary-adc.py"
_LEADS_SCRIPT="painel-soprolife/scripts/read-leads-sheets.py"

_PASS=0
_FAIL=0

_pass() { echo "  [OK]    $*"; _PASS=$((_PASS+1)); }
_fail() { echo "  [FALTA] $*"; _FAIL=$((_FAIL+1)); }
_info() { echo "  [INFO]  $*"; }
_warn() { echo "  [AVISO] $*"; }

echo "======================================================="
echo " SoproLife — Diagnóstico ADC Google Sheets"
echo "======================================================="
echo " Usuário: $(whoami)"
echo " Diretório: $(pwd)"
echo " Data: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "======================================================="
echo

# ---------------------------------------------------------------------------
# 1. ADC (gcloud application-default-credentials)
# ---------------------------------------------------------------------------
echo "1. Credenciais ADC (gcloud)..."

if [ -f "$_ADC_CONFIG" ]; then
  # Verifica JSON válido sem imprimir conteúdo
  if python3 -c "
import json, sys
try:
    with open('$_ADC_CONFIG') as f:
        data = json.load(f)
    t = data.get('type', 'desconhecido')
    print(f'  [OK]    ADC config existe — type={t!r} (client_secret e tokens não exibidos)')
except Exception as e:
    print(f'  [ERRO]  ADC config inválido: {e}')
    sys.exit(1)
" 2>/dev/null; then
    _PASS=$((_PASS+1))
  else
    _fail "ADC config inválido ou ilegível: $_ADC_CONFIG"
  fi
else
  _fail "ADC config não encontrado: $_ADC_CONFIG"
  _info "Configure com:"
  _info "  gcloud auth application-default login \\"
  _info "      --scopes=https://www.googleapis.com/auth/spreadsheets.readonly"
fi

echo

# ---------------------------------------------------------------------------
# 2. Configuração Google Sheets (sem imprimir spreadsheet_id)
# ---------------------------------------------------------------------------
echo "2. Configuração Google Sheets..."

if [ -f "$_SHEETS_CONFIG" ]; then
  python3 - <<PYEOF
import json, sys

path = "$_SHEETS_CONFIG"
try:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
except Exception as e:
    print(f"  [FALTA] Configuração inválida: {e}")
    sys.exit(1)

sid   = cfg.get("spreadsheet_id", "").strip()
sheet = cfg.get("sheet_name", "").strip()

if not sid:
    print("  [FALTA] spreadsheet_id ausente ou vazio na configuração.")
    sys.exit(1)
if not sheet:
    print("  [FALTA] sheet_name ausente ou vazio na configuração.")
    sys.exit(1)

# Nunca imprime spreadsheet_id
print(f"  [OK]    Configuração válida (sheet_name={sheet!r}, spreadsheet_id presente — não exibido).")
PYEOF
  _py_rc=$?
  if [ "$_py_rc" -eq 0 ]; then
    _PASS=$((_PASS+1))
  else
    _FAIL=$((_FAIL+1))
    _info "Crie: ~/.config/soprolife/painel/google-sheets.local.json"
    _info "  { \"spreadsheet_id\": \"ID_AQUI\", \"sheet_name\": \"Resumo Dashboard\" }"
  fi
else
  _fail "Configuração não encontrada: $_SHEETS_CONFIG"
  _info "Crie: ~/.config/soprolife/painel/google-sheets.local.json"
  _info "  { \"spreadsheet_id\": \"ID_AQUI\", \"sheet_name\": \"Resumo Dashboard\" }"
fi

echo

# ---------------------------------------------------------------------------
# 3. Python venv
# ---------------------------------------------------------------------------
echo "3. Python venv para Google Sheets..."

if [ -f "$_VENV_PYTHON" ]; then
  _PY_VER=$("$_VENV_PYTHON" --version 2>&1 || echo "versão desconhecida")
  _pass "venv encontrado: $_VENV_PYTHON ($PY_VER)"
  _pass "$_PY_VER"
else
  _fail "venv não encontrado: $_VENV_PYTHON"
  _info "Crie com:"
  _info "  python3 -m venv ~/.local/share/soprolife/venvs/google-sheets"
  _info "  ~/.local/share/soprolife/venvs/google-sheets/bin/pip install -r painel-soprolife/requirements-google.txt"
fi

echo

# ---------------------------------------------------------------------------
# 4. requirements-google.txt
# ---------------------------------------------------------------------------
echo "4. Arquivo requirements-google.txt..."

if [ -f "$_REQUIREMENTS" ]; then
  _pass "$_REQUIREMENTS presente"
else
  _fail "$_REQUIREMENTS não encontrado (repo desatualizado?)"
fi

echo

# ---------------------------------------------------------------------------
# 5. Scripts Python
# ---------------------------------------------------------------------------
echo "5. Scripts Python de leitura..."

if [ -f "$_SHEETS_SCRIPT" ]; then
  _pass "$_SHEETS_SCRIPT"
else
  _fail "$_SHEETS_SCRIPT não encontrado"
fi

if [ -f "$_LEADS_SCRIPT" ]; then
  _pass "$_LEADS_SCRIPT"
else
  _fail "$_LEADS_SCRIPT não encontrado"
fi

echo

# ---------------------------------------------------------------------------
# 6. Sintaxe dos scripts Python
# ---------------------------------------------------------------------------
echo "6. Verificação de sintaxe Python..."

if [ -f "$_SHEETS_SCRIPT" ]; then
  if python3 -m py_compile "$_SHEETS_SCRIPT" 2>/dev/null; then
    _pass "Sintaxe OK: $(basename "$_SHEETS_SCRIPT")"
  else
    _fail "Erro de sintaxe: $(basename "$_SHEETS_SCRIPT")"
  fi
fi

if [ -f "$_LEADS_SCRIPT" ]; then
  if python3 -m py_compile "$_LEADS_SCRIPT" 2>/dev/null; then
    _pass "Sintaxe OK: $(basename "$_LEADS_SCRIPT")"
  else
    _fail "Erro de sintaxe: $(basename "$_LEADS_SCRIPT")"
  fi
fi

echo

# ---------------------------------------------------------------------------
# 7. Dry-run do resumo (só executa se pré-requisitos estiverem presentes)
# ---------------------------------------------------------------------------
echo "7. Teste de leitura — Resumo Dashboard (dry-run)..."

if [ -f "$_VENV_PYTHON" ] && [ -f "$_SHEETS_SCRIPT" ] && \
   [ -f "$_SHEETS_CONFIG" ] && [ -f "$_ADC_CONFIG" ]; then
  echo "  Executando dry-run (pode demorar alguns segundos)..."
  if "$_VENV_PYTHON" "$_SHEETS_SCRIPT" --dry-run 2>&1; then
    _pass "Dry-run do resumo concluído com sucesso."
  else
    _warn "Dry-run do resumo falhou — verifique saída acima."
    _FAIL=$((_FAIL+1))
  fi
else
  _warn "Pré-requisitos ausentes — dry-run do resumo pulado."
  _info "Configure os itens faltantes nas etapas anteriores."
fi

echo

# ---------------------------------------------------------------------------
# 8. Dry-run dos Leads (só executa se pré-requisitos estiverem presentes)
# ---------------------------------------------------------------------------
echo "8. Teste de leitura — Leads (dry-run)..."

if [ -f "$_VENV_PYTHON" ] && [ -f "$_LEADS_SCRIPT" ] && \
   [ -f "$_SHEETS_CONFIG" ] && [ -f "$_ADC_CONFIG" ]; then
  echo "  Executando dry-run (pode demorar alguns segundos)..."
  if "$_VENV_PYTHON" "$_LEADS_SCRIPT" --dry-run 2>&1; then
    _pass "Dry-run de leads concluído com sucesso."
  else
    _warn "Dry-run de leads falhou — verifique saída acima."
    _FAIL=$((_FAIL+1))
  fi
else
  _warn "Pré-requisitos ausentes — dry-run de leads pulado."
fi

echo

# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------
echo "======================================================="
echo " Diagnóstico concluído"
echo "  Verificações OK:       $_PASS"
echo "  Verificações faltando: $_FAIL"
echo "======================================================="

if [ "$_FAIL" -eq 0 ]; then
  echo
  echo "Tudo pronto para atualização automática via Google Sheets ADC."
  echo "Execute: painel-soprolife/scripts/update-local-data.sh"
  exit 0
else
  echo
  echo "Configure os itens faltantes para habilitar a atualização automática."
  echo "Enquanto isso, o painel funcionará com dados anteriores/demonstrativos."
  exit 1
fi
