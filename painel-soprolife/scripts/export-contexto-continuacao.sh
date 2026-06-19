#!/usr/bin/env bash
# Exporta contexto de continuidade seguro para nova janela de IA.
# Nunca imprime: spreadsheet_id, URLs privadas, tokens, client_id,
# client_secret, refresh_token, dados de pacientes ou caminhos privados.
set -euo pipefail

# ---------------------------------------------------------------------------
# Caminhos — todos relativos ao repositório
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
_REPO_ROOT="$(cd "$_SCRIPT_DIR/../.." && pwd)"
_SCRIPTS_DIR="$_REPO_ROOT/painel-soprolife/scripts"
_DATA_DIR="$_REPO_ROOT/painel-soprolife/data"

_OUT="$HOME/soprolife-contexto-continuacao.txt"

_ADC_PY="$_SCRIPTS_DIR/read-sheets-summary-adc.py"
_VENV_PYTHON="$HOME/.local/share/soprolife/venvs/google-sheets/bin/python"
_SHEETS_CONFIG="$HOME/.config/soprolife/painel/google-sheets.local.json"
_ADC_CONFIG="$HOME/.config/gcloud/application_default_credentials.json"

_DASHBOARD_JSON="$_DATA_DIR/resumo-dashboard.local.json"
_RUNTIME_JSON="$_DATA_DIR/runtime-status.local.json"

# ---------------------------------------------------------------------------
# Função auxiliar — escreve linha e bloco no arquivo de saída
# ---------------------------------------------------------------------------
w()  { printf '%s\n' "$*" >> "$_OUT"; }
wl() { printf '\n' >> "$_OUT"; }
sep() { printf '%.0s─' {1..72} >> "$_OUT"; printf '\n' >> "$_OUT"; }

# ---------------------------------------------------------------------------
# Inicializa arquivo (limpa versão anterior)
# ---------------------------------------------------------------------------
: > "$_OUT"

# ---------------------------------------------------------------------------
# 1. Título
# ---------------------------------------------------------------------------
sep
w "Contexto de Continuação — SoproLife Command Center"
w "Gerado em: $(date '+%Y-%m-%d %H:%M:%S %Z')"
sep

# ---------------------------------------------------------------------------
# 2. Marca e operação
# ---------------------------------------------------------------------------
wl
w "## MARCA E OPERAÇÃO"
wl
w "Empresa:     SoproLife"
w "WhatsApp:    (21) 99890-1775"
w "Serviços:"
w "  - Espirometria"
w "  - Teleconsulta respiratória"
w "  - Atendimento domiciliar (sob avaliação)"
w "  - Parcerias com clínicas e consultórios"
w "  - PCMSO / saúde ocupacional"
w "  - Prospecção B2B"
w "  - Marketing digital local"

# ---------------------------------------------------------------------------
# 3. Fluxo técnico
# ---------------------------------------------------------------------------
wl
sep
w "## FLUXO TÉCNICO"
sep
wl
w "Google Sheets privado (\"SoproLife - Painel Interno - Dados Privados\")"
w "  └─► painel-soprolife/scripts/update-local-data.sh"
w "        └─► read-sheets-summary-adc.py --write"
w "              └─► ~/.config/soprolife/painel/resumo-dashboard.json  (privado, fora do repo)"
w "                    └─► sync-dashboard-summary.sh"
w "                          └─► painel-soprolife/data/resumo-dashboard.local.json"
w "                                └─► Painel SoproLife / Command Center"
wl
w "Arquivos de estado local (rastreados no repo — apenas dados agregados):"
w "  painel-soprolife/data/resumo-dashboard.local.json"
w "  painel-soprolife/data/runtime-status.local.json"
wl
w "Conector ADC:"
w "  painel-soprolife/scripts/read-sheets-summary-adc.py"
w "  Auth: Application Default Credentials (gcloud auth application-default login)"
w "  Escopos: https://www.googleapis.com/auth/cloud-platform"
w "           https://www.googleapis.com/auth/spreadsheets.readonly"
w "  Nunca grava ID privado da planilha, URL ou tokens no repositório."

# ---------------------------------------------------------------------------
# 4. Estado git
# ---------------------------------------------------------------------------
wl
sep
w "## ESTADO GIT"
sep
wl
w "Branch atual:"
w "  $(git -C "$_REPO_ROOT" branch --show-current 2>/dev/null || echo '(não disponível)')"
wl
w "Status (resumido):"
_git_status="$(git -C "$_REPO_ROOT" status --short 2>/dev/null || echo '(não disponível)')"
if [ -z "$_git_status" ]; then
    w "  (clean — sem alterações pendentes)"
else
    while IFS= read -r line; do w "  $line"; done <<< "$_git_status"
fi
wl
w "Últimos commits / checkpoints:"
git -C "$_REPO_ROOT" log --oneline --decorate -8 2>/dev/null \
    | while IFS= read -r line; do w "  $line"; done \
    || w "  (não disponível)"
wl
w "Tags checkpoint recentes:"
_tags="$(git -C "$_REPO_ROOT" tag --sort=-creatordate 2>/dev/null | head -10 || true)"
if [ -z "$_tags" ]; then
    w "  (nenhuma tag encontrada)"
else
    while IFS= read -r line; do w "  $line"; done <<< "$_tags"
fi

# ---------------------------------------------------------------------------
# 5. Scripts principais em painel-soprolife/scripts
# ---------------------------------------------------------------------------
wl
sep
w "## SCRIPTS PRINCIPAIS (painel-soprolife/scripts/)"
sep
wl
if [ -d "$_SCRIPTS_DIR" ]; then
    find "$_SCRIPTS_DIR" -maxdepth 1 -type f \( -name "*.sh" -o -name "*.py" \) \
        | sort \
        | while IFS= read -r f; do
            w "  $(basename "$f")"
          done
else
    w "  (diretório não encontrado)"
fi

# ---------------------------------------------------------------------------
# 6. Estrutura segura da aba Resumo Dashboard (via --show-structure)
# ---------------------------------------------------------------------------
wl
sep
w "## ESTRUTURA SEGURA — ABA RESUMO DASHBOARD"
sep
wl
_can_show_structure=false
if [ -f "$_ADC_PY" ] && [ -f "$_SHEETS_CONFIG" ] && [ -f "$_ADC_CONFIG" ] && [ -f "$_VENV_PYTHON" ]; then
    _can_show_structure=true
fi

if [ "$_can_show_structure" = true ]; then
    w "Lendo estrutura via: read-sheets-summary-adc.py --show-structure"
    w "(ID privado da planilha e URL não são impressos)"
    wl
    _struct_out="$("$_VENV_PYTHON" "$_ADC_PY" --show-structure 2>&1)" \
        && _struct_ok=true || _struct_ok=false

    if [ "$_struct_ok" = true ]; then
        while IFS= read -r line; do w "  $line"; done <<< "$_struct_out"
    else
        w "AVISO: falha ao ler estrutura via ADC."
        w "  Para reautenticar, execute:"
        w "    gcloud auth application-default login \\"
        w "        --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets.readonly"
        w "  Mensagem original:"
        while IFS= read -r line; do w "    $line"; done <<< "$_struct_out"
    fi
else
    w "Pré-requisitos do conector ADC não disponíveis nesta máquina:"
    [ ! -f "$_ADC_PY" ]       && w "  - read-sheets-summary-adc.py não encontrado"
    [ ! -f "$_SHEETS_CONFIG" ] && w "  - google-sheets.local.json não encontrado em ~/.config/soprolife/painel/"
    [ ! -f "$_ADC_CONFIG" ]    && w "  - application_default_credentials.json não encontrado"
    [ ! -f "$_VENV_PYTHON" ]   && w "  - venv Python não encontrado em ~/.local/share/soprolife/venvs/google-sheets/"
    wl
    w "Para configurar o conector, consulte:"
    w "  painel-soprolife/core/connectors/google-sheets-adc.md"
fi

# ---------------------------------------------------------------------------
# 7. Abas e cabeçalhos do Google Sheets (somente nomes — sem dados)
# ---------------------------------------------------------------------------
wl
sep
w "## ABAS DO GOOGLE SHEETS (somente nomes e cabeçalhos da linha 1)"
sep
wl
if [ -f "$_ADC_CONFIG" ] && [ -f "$_SHEETS_CONFIG" ] && [ -f "$_VENV_PYTHON" ]; then
    _py_list_tabs="$(mktemp /tmp/soprolife-tabs-XXXXXX.py)"
    trap 'rm -f "${_py_list_tabs:-}"' EXIT
    cat > "$_py_list_tabs" << 'PYEOF'
import json, sys
cfg_path = sys.argv[1]
try:
    cfg = json.load(open(cfg_path))
    sid = (cfg.get('spreadsheetId') or cfg.get('spreadsheet_id') or
           cfg.get('id') or '')
    if not sid:
        print('AVISO: chave spreadsheetId nao encontrada no config.')
        sys.exit(1)
    import google.auth
    from googleapiclient.discovery import build
    creds, _ = google.auth.default(
        scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
    )
    service = build('sheets', 'v4', credentials=creds)
    meta = service.spreadsheets().get(spreadsheetId=sid).execute()
    tabs = meta.get('sheets', [])
    for tab in tabs:
        name = tab['properties']['title']
        rng = "'" + name.replace("'", "\\'") + "'!1:1"
        result = service.spreadsheets().values().get(
            spreadsheetId=sid, range=rng
        ).execute()
        values = result.get('values', [[]])
        headers = values[0] if values else []
        print('Aba: ' + name)
        if headers:
            print('  Cabecalhos: ' + ' | '.join(str(h) for h in headers))
        else:
            print('  (sem cabecalhos na linha 1)')
        print()
except Exception as e:
    print('ERRO: ' + str(e))
    sys.exit(1)
PYEOF
    w "(ID e URL da planilha não são impressos)"
    wl
    _tabs_out="$("$_VENV_PYTHON" "$_py_list_tabs" "$_SHEETS_CONFIG" 2>&1)" \
        && _tabs_ok=true || _tabs_ok=false
    if [ "$_tabs_ok" = true ]; then
        if [ -n "$_tabs_out" ]; then
            while IFS= read -r line; do w "  $line"; done <<< "$_tabs_out"
        else
            w "  (nenhuma aba encontrada na planilha)"
        fi
    else
        w "AVISO: falha ao listar abas via ADC."
        w "  Para reautenticar, execute:"
        w "    gcloud auth application-default login \\"
        w "        --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets.readonly"
        if [ -n "$_tabs_out" ]; then
            w "  Detalhes:"
            while IFS= read -r line; do w "    $line"; done <<< "$_tabs_out"
        fi
    fi
elif [ -f "$_ADC_CONFIG" ]; then
    w "AVISO: ADC encontrado mas config ou venv incompletos."
    w "  Reautentique se necessário:"
    w "    gcloud auth application-default login \\"
    w "        --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets.readonly"
else
    w "ADC não configurado. Para autenticar:"
    w "  gcloud auth application-default login \\"
    w "      --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets.readonly"
fi

# ---------------------------------------------------------------------------
# 8. Indicadores seguros atuais do dashboard
# ---------------------------------------------------------------------------
wl
sep
w "## INDICADORES SEGUROS DO DASHBOARD"
sep
wl
if [ -f "$_DASHBOARD_JSON" ]; then
    w "Fonte: painel-soprolife/data/resumo-dashboard.local.json"
    wl

    # Flags de segurança
    _safe="$(python3 -c "
import json, sys
try:
    d = json.load(open('$_DASHBOARD_JSON'))
    src = d.get('source', {})
    print('safeToDisplay:       ' + str(src.get('safeToDisplay', 'N/A')))
    print('containsPersonalData:' + str(src.get('containsPersonalData', 'N/A')))
    print('containsHealthData:  ' + str(src.get('containsHealthData', 'N/A')))
    print('generatedAt:         ' + str(src.get('generatedAt', 'N/A')))
except Exception as e:
    print('Erro ao ler flags: ' + str(e))
" 2>&1)"
    w "Flags de segurança:"
    while IFS= read -r line; do w "  $line"; done <<< "$_safe"
    wl

    # Cards agregados
    _cards="$(python3 -c "
import json, sys
try:
    d = json.load(open('$_DASHBOARD_JSON'))
    cards = d.get('cards', [])
    for c in cards:
        key   = c.get('key', '')
        label = c.get('label', '')
        value = c.get('value', '')
        print(f'  {label:<30} {value}  ({key})')
except Exception as e:
    print('Erro ao ler cards: ' + str(e))
" 2>&1)"
    w "Cards agregados:"
    while IFS= read -r line; do w "$line"; done <<< "$_cards"
else
    w "Arquivo não encontrado: painel-soprolife/data/resumo-dashboard.local.json"
    w "Execute: painel-soprolife/scripts/update-local-data.sh"
fi
wl

# Runtime status
if [ -f "$_RUNTIME_JSON" ]; then
    w "Runtime status (painel-soprolife/data/runtime-status.local.json):"
    _runtime="$(python3 -c "
import json, sys
try:
    d = json.load(open('$_RUNTIME_JSON'))
    gs = d.get('googleSheets', {})
    print('  configured:   ' + str(gs.get('configured', 'N/A')))
    print('  name:         ' + str(gs.get('name', 'N/A')))
    print('  statusLabel:  ' + str(gs.get('statusLabel', 'N/A')))
    print('  safeToDisplay:' + str(gs.get('safeToDisplay', 'N/A')))
    print('  lastCheckedAt:' + str(gs.get('lastCheckedAt', 'N/A')))
except Exception as e:
    print('  Erro ao ler runtime-status: ' + str(e))
" 2>&1)"
    while IFS= read -r line; do w "$line"; done <<< "$_runtime"
fi

# ---------------------------------------------------------------------------
# 9. Regras de privacidade
# ---------------------------------------------------------------------------
wl
sep
w "## REGRAS DE PRIVACIDADE E SEGURANÇA"
sep
wl
w "1. Dados privados ficam apenas nas abas internas do Google Sheets."
w "   O dashboard recebe SOMENTE indicadores agregados (sem nomes, CPF, telefone)."
wl
w "2. Os arquivos *.local.json no repositório contêm APENAS:"
w "   - Totais numéricos (ex: totalLeads: 5)"
w "   - Flags de segurança (safeToDisplay, containsPersonalData, containsHealthData)"
w "   Eles NUNCA contêm dados individuais de pacientes ou clientes."
wl
w "3. Paciente recorrente exige cuidado especial com consentimento (LGPD)."
w "   Não vincular histórico clínico sem autorização expressa."
wl
w "4. Mensagens WhatsApp automáticas devem usar texto genérico e seguro."
w "   Nunca incluir diagnóstico, laudo, pedido médico ou dado clínico."
wl
w "5. Não imprimir, compartilhar ou colar em chats:"
w "   - ID privado da planilha ou URL dela"
w "   - tokens OAuth (acesso, renovação, ID e segredo do cliente)"
w "   - conteúdo do ADC (~/.config/gcloud/application_default_credentials.json)"
w "   - CPF, telefone, endereço, laudo, diagnóstico, pedido médico de pacientes"
wl
w "6. Arquivos .local.json e *.local.* estão no .gitignore."
w "   Nunca fazer commit de dados privados ou credenciais."

# ---------------------------------------------------------------------------
# 10. Prompt para nova janela
# ---------------------------------------------------------------------------
wl
sep
w "## PROMPT PARA NOVA JANELA"
sep
wl
w "Cole o bloco abaixo ao abrir uma nova janela de IA para continuar este projeto:"
wl
w "────────────────────────────────────────────────────────────────────────"
w "Você é a IA responsável pelo Painel SoproLife / SoproLife Command Center."
wl
w "CONTEXTO DA EMPRESA"
w "SoproLife — saúde respiratória."
w "WhatsApp: (21) 99890-1775"
w "Serviços: espirometria, teleconsulta respiratória, domiciliar (sob avaliação),"
w "  parcerias B2B com clínicas e PCMSO, marketing digital local."
wl
w "PAPEL DA IA"
w "Você é a Central de CRM + Google Sheets + Command Center da SoproLife."
w "Seu objetivo é:"
w "  1. Apoiar a operação diária: leads, agendamentos, clínicas parceiras, tarefas."
w "  2. Manter o painel atualizado com indicadores agregados (nunca dados pessoais)."
w "  3. Executar o fluxo técnico seguro:"
w "       Google Sheets privado → update-local-data.sh → JSON local → painel."
w "  4. Nunca expor: ID privado da planilha, URL dela, tokens ADC,"
w "     CPF, telefone, laudo, diagnóstico ou dado clínico de pacientes."
wl
w "FLUXO TÉCNICO"
w "  painel-soprolife/scripts/update-local-data.sh   # atualiza dados locais"
w "  painel-soprolife/scripts/start-local.sh         # abre painel local"
w "  painel-soprolife/scripts/read-sheets-summary-adc.py --show-structure  # diagnóstico"
wl
w "REGRAS OBRIGATÓRIAS"
w "  - Dados privados ficam no Google Sheets interno."
w "  - Dashboard exibe apenas totais/indicadores agregados."
w "  - Arquivos *.local.json nunca devem ser commitados."
w "  - Mensagens automáticas WhatsApp devem ser genéricas e seguras."
w "  - Paciente recorrente: verificar consentimento (LGPD) antes de processar."
wl
w "REPOSITÓRIO"
w "  Branch: $(git -C "$_REPO_ROOT" branch --show-current 2>/dev/null || echo 'ver git branch')"
w "  Estrutura principal:"
w "    painel-soprolife/index.html"
w "    painel-soprolife/js/app.js"
w "    painel-soprolife/css/style.css"
w "    painel-soprolife/scripts/"
w "    painel-soprolife/data/"
wl
w "Retome a partir deste contexto e aguarde instruções do usuário."
w "────────────────────────────────────────────────────────────────────────"

# ---------------------------------------------------------------------------
# Permissões seguras
# ---------------------------------------------------------------------------
chmod 600 "$_OUT"

# ---------------------------------------------------------------------------
# Resumo no terminal
# ---------------------------------------------------------------------------
printf '\n'
printf '════════════════════════════════════════════════════════════════════════\n'
printf 'Arquivo gerado: %s\n' "$_OUT"
printf '════════════════════════════════════════════════════════════════════════\n'
printf '\n'
printf 'Prévia (primeiras 20 linhas):\n'
printf '──────────────────────────────────────────────────────────────────────\n'
head -20 "$_OUT"
printf '...\n'
printf '──────────────────────────────────────────────────────────────────────\n'
printf '\n'
printf 'AVISO DE SEGURANÇA:\n'
printf '  O arquivo gerado contém contexto operacional interno.\n'
printf '  Não cole dados sensíveis (tokens, IDs privados, dados de pacientes)\n'
printf '  em janelas de IA sem necessidade.\n'
printf '  O arquivo está com permissão 600 (somente leitura pelo dono).\n'
printf '\n'
printf 'Como usar:\n'
printf '  1. Abra uma nova janela ou sessão de IA.\n'
printf '  2. Cole o conteúdo da seção "PROMPT PARA NOVA JANELA" (final do arquivo).\n'
printf '  3. A IA assumirá o papel de Command Center da SoproLife.\n'
printf '\n'
printf 'Para visualizar o arquivo completo:\n'
printf '  cat ~/soprolife-contexto-continuacao.txt\n'
printf '\n'
