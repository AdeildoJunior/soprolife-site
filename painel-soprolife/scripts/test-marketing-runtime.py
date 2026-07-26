#!/usr/bin/env python3
"""
SoproLife — runtime do conector Marketing & SEO (M23, 2º incidente).

O 2º deploy do M23 falhou no Marketing com "No module named
'googleapiclient'". Causa: o conector era invocado por um "python3" solto,
resolvido pelo PATH — e a unit do M23 passou a colocar o venv da API M15 na
frente do PATH. Esse venv instala requirements.lock da API, que não tem (nem
deve ter) as bibliotecas do Google.

Correção: interpretador EXPLÍCITO, venv DEDICADO, dependências fechadas em
painel-soprolife/requirements-marketing.lock e instaladas pelo deploy.

Este teste roda offline. Não faz chamada de rede, não lê credencial e não
toca em produção.

Uso:  python3 painel-soprolife/scripts/test-marketing-runtime.py
Exit: 0 = todos os casos passaram | 1 = houve falha.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent          # painel-soprolife/
UPDATE_SH = RAIZ / "scripts" / "update-local-data.sh"
CONECTOR = RAIZ / "scripts" / "read-marketing-seo-adc.py"
UNIT = RAIZ / "systemd" / "soprolife-update-data.service"
DEPLOY = RAIZ / "nucleo-m15" / "scripts" / "deploy-producao-vps.sh"
REQ_TXT = RAIZ / "requirements-marketing.txt"
REQ_LOCK = RAIZ / "requirements-marketing.lock"
REQ_GOOGLE = RAIZ / "requirements-google.txt"
M15_LOCK = RAIZ / "nucleo-m15" / "requirements.lock"

VENV_MARKETING = "/opt/soprolife/venvs/marketing"

FALHAS = 0


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + detalhe if detalhe else ''}")


def pacotes(path: Path) -> set[str]:
    """Nomes de pacote de um requirements, normalizados (sem versão/extra)."""
    nomes = set()
    for linha in path.read_text(encoding="utf-8").splitlines():
        linha = linha.split("#", 1)[0].strip()
        if not linha or linha.startswith("-"):
            continue
        nome = re.split(r"[=<>!\[;]", linha, maxsplit=1)[0].strip()
        if nome:
            nomes.add(nome.lower().replace("_", "-"))
    return nomes


# --------------------------------------------------------------- declaração
print("── Dependências declaradas ──")

caso("requirements-marketing.txt existe", REQ_TXT.is_file())
caso("requirements-marketing.lock existe", REQ_LOCK.is_file())

diretos = pacotes(REQ_TXT)
travados = pacotes(REQ_LOCK)

for pacote in ("google-api-python-client", "google-auth",
               "google-auth-httplib2", "google-analytics-data"):
    caso(f"'{pacote}' declarado como dependência direta", pacote in diretos)
    caso(f"'{pacote}' presente no lock", pacote in travados)

caso("todo pacote direto aparece no lock", diretos <= travados,
     f"faltando: {sorted(diretos - travados)}")
caso("lock tem o fecho transitivo (mais pacotes que os diretos)",
     len(travados) > len(diretos))
caso("todo pacote do .txt está com versão fixada",
     all("==" in l for l in REQ_TXT.read_text(encoding="utf-8").splitlines()
         if l.split("#", 1)[0].strip() and not l.strip().startswith("#")))

# O M23 descomissionou o Google Sheets. Marketing não pode ser a porta dos
# fundos por onde ele volta.
SHEETS = {"gspread", "google-api-python-client-stubs", "pygsheets",
          "oauth2client", "df2gspread", "gspread-dataframe",
          "google-auth-oauthlib"}
caso("nenhum pacote de Sheets/fluxo interativo no lock de Marketing",
     not (travados & SHEETS), f"encontrado: {sorted(travados & SHEETS)}")

# As dependências do Google NÃO podem entrar no venv que serve a API: manter
# os ambientes separados é o que garante que uma falha de Marketing não
# chegue perto da fonte operacional.
m15 = pacotes(M15_LOCK)
caso("lock da API M15 continua sem as bibliotecas do Google",
     not any(p.startswith("google") for p in m15),
     f"encontrado: {sorted(p for p in m15 if p.startswith('google'))}")

caso("requirements-google.txt está marcado como legado de Sheets",
     "LEGADOS" in REQ_GOOGLE.read_text(encoding="utf-8"))


# ------------------------------------------------------------ esteira / unit
print()
print("── Seleção do interpretador ──")

update_sh = UPDATE_SH.read_text(encoding="utf-8")
unit = UNIT.read_text(encoding="utf-8")
deploy = DEPLOY.read_text(encoding="utf-8")

# O bug era a atribuição INCONDICIONAL no topo do script: com ela, o PATH da
# unit decidia o interpretador. O "python3" só pode sobrar como último
# fallback, dentro do encadeamento de resolução (desenvolvimento local).
caso("esteira não atribui mais o interpretador de Marketing incondicionalmente",
     not re.search(r'(?m)^_MARKETING_PYTHON="python3"$', update_sh))
caso("'python3' sobrou apenas como último fallback do encadeamento",
     re.search(r'(?m)^else\n  _MARKETING_PYTHON="python3"\nfi$', update_sh) is not None)
caso("esteira honra SOPROLIFE_MARKETING_PYTHON",
     "SOPROLIFE_MARKETING_PYTHON" in update_sh)
caso("esteira cai no venv dedicado quando a variável não vem",
     f'_MARKETING_VENV_PYTHON="{VENV_MARKETING}/bin/python"' in update_sh)
caso("esteira registra no log qual interpretador usou",
     'echo "Interpretador: $_MARKETING_PYTHON"' in update_sh)
caso("unit de produção aponta o venv dedicado",
     f"Environment=SOPROLIFE_MARKETING_PYTHON={VENV_MARKETING}/bin/python" in unit)
caso("unit continua exigindo conta de serviço",
     "Environment=SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT=1" in unit)
caso("unit continua sem CLOUDSDK_CONFIG (ADC pessoal descomissionado)",
     "Environment=CLOUDSDK_CONFIG" not in unit)
caso("unit não expõe conteúdo de credencial, só o caminho",
     "marketing-readonly.json" in unit and "private_key" not in unit)

caso("deploy cria o venv dedicado de Marketing",
     f'MARKETING_VENV_DIR="{VENV_MARKETING}"' in deploy
     and "python3 -m venv \"$MARKETING_VENV_DIR\"" in deploy)
caso("deploy instala a partir do lock versionado",
     'install -r "$MARKETING_LOCK"' in deploy)
caso("deploy valida o venv de Marketing com pip check",
     '"$MARKETING_VENV_DIR/bin/pip" check' in deploy)
caso("deploy prova o import antes de dar o venv por pronto",
     "import googleapiclient.discovery, google.oauth2.service_account" in deploy)
caso("falha de Marketing no deploy não derruba o deploy operacional",
     "AVISO: dependências de Marketing não instaladas" in deploy)

caso("falha de Marketing na esteira não conta como falha da fonte canônica",
     "AVISO: Marketing & SEO falhou" in update_sh
     and "não afeta os dados operacionais" in update_sh)

# A mensagem antiga mandava rodar 'pip install -r requirements-google.txt' —
# instalação manual em produção, e apontando para o arquivo do Sheets.
conector = CONECTOR.read_text(encoding="utf-8")
caso("conector não manda mais instalar requirements-google.txt",
     "requirements-google.txt" not in conector)
caso("conector aponta o venv dedicado no diagnóstico",
     f"{VENV_MARKETING}/bin/python" in conector)
caso("conector desaconselha instalação manual em produção",
     "não instale pacotes à mão" in conector)
caso("diagnóstico imprime o interpretador em uso",
     "Interpretador em uso" in conector)


# ------------------------------------------- import real no interpretador real
print()
print("── Import real ──")

# Resolve exatamente como a esteira resolve, na MESMA ordem.
if os.environ.get("SOPROLIFE_MARKETING_PYTHON"):
    interpretador = os.environ["SOPROLIFE_MARKETING_PYTHON"]
    origem = "SOPROLIFE_MARKETING_PYTHON"
elif os.access(f"{VENV_MARKETING}/bin/python", os.X_OK):
    interpretador = f"{VENV_MARKETING}/bin/python"
    origem = "venv dedicado"
else:
    interpretador = "python3"
    origem = "python3 do sistema (desenvolvimento)"

print(f"  interpretador resolvido: {interpretador}  ({origem})")

MODULOS = [
    "googleapiclient.discovery",          # cliente do Search Console
    "google.oauth2.service_account",      # credencial de conta de serviço
    "google.auth",                        # resolução de credencial
    "google_auth_httplib2",               # transporte do cliente discovery
    "google.analytics.data_v1beta",       # GA4
]
for modulo in MODULOS:
    proc = subprocess.run([interpretador, "-c", f"import {modulo}"],
                          capture_output=True, text=True)
    caso(f"'{modulo}' importa no interpretador da esteira",
         proc.returncode == 0, proc.stderr.strip().splitlines()[-1:] and
         proc.stderr.strip().splitlines()[-1] or "")

# O conector precisa continuar carregável (sem rede) nesse interpretador.
proc = subprocess.run(
    [interpretador, str(CONECTOR), "--credential-check"],
    capture_output=True, text=True, cwd=str(RAIZ.parent),
    env={**os.environ, "SOPROLIFE_MARKETING_CREDENTIALS": "/inexistente.json",
         "SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT": "1"},
)
caso("conector roda sem credencial e falha fechado (não cai em ADC pessoal)",
     proc.returncode != 0 or "pendente" in (proc.stdout + proc.stderr).lower(),
     (proc.stdout + proc.stderr).strip()[-200:])
caso("conector não imprime segredo no diagnóstico",
     not re.search(r"(?i)private_key|client_secret|BEGIN [A-Z ]*PRIVATE KEY",
                   proc.stdout + proc.stderr))


print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} falha(s).")
    sys.exit(1)
print("RESULTADO: todos os casos passaram.")
