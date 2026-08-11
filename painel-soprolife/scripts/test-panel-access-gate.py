#!/usr/bin/env python3
"""
SoproLife — Testes do gate de acesso estático (M25.23).

Fixa o contrato que fechou a exposição medida em produção antes desta etapa:
o Command Center inteiro, os summaries financeiros, a pasta `data-private`
(PII de paciente + apiToken vivo) e o `.git` respondiam 200 sem sessão.

Offline, sem rede, sem servidor. Roda no quality gate.

Uso:
    python3 painel-soprolife/scripts/test-panel-access-gate.py
Exit: 0 = contrato preservado | 1 = regressão.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from panel_access_gate import (  # noqa: E402
    FORBIDDEN,
    PROTECTED_DATA,
    PROTECTED_PAGE,
    PUBLIC,
    InvalidPath,
    classify,
    is_panel_entry,
)

FALHAS = 0


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FALHA: {nome}" + (f" — {detalhe}" if detalhe else ""))


def espera(alvo, esperado):
    try:
        obtido = classify(alvo)
    except InvalidPath as exc:
        obtido = f"InvalidPath({exc})"
    caso(f"{alvo} → {esperado}", obtido == esperado, f"obtido {obtido}")


def espera_invalido(alvo):
    try:
        classify(alvo)
    except InvalidPath:
        caso(f"{alvo} → recusado", True)
        return
    caso(f"{alvo} → recusado", False, "foi aceito")


print("── A exposição real medida na M25.23 (regressão proibida) ──")
# Estes seis caminhos devolviam 200 em produção. Nenhum pode voltar a ser
# público nem sequer alcançável.
espera("/painel-soprolife/", PROTECTED_PAGE)
espera("/painel-soprolife/index.html", PROTECTED_PAGE)
espera("/painel-soprolife/data/financeiro-summary.local.json", PROTECTED_DATA)
espera("/painel-soprolife/data-private/followup-pacientes.local.json", FORBIDDEN)
espera("/painel-soprolife/data-private/command-center-config.local.json", FORBIDDEN)
espera("/.git/config", FORBIDDEN)

print("── Fonte privada: diretório inteiro, não só os arquivos citados ──")
espera("/painel-soprolife/data-private/", FORBIDDEN)
espera("/painel-soprolife/data-private", FORBIDDEN)
espera("/painel-soprolife/data-private/financeiro-lancamentos.local.json", FORBIDDEN)
espera("/painel-soprolife/data-private/custos-investimentos.local.json", FORBIDDEN)
espera("/painel-soprolife/data-private/README.local.txt", FORBIDDEN)

print("── Repositório e arquivos ocultos ──")
espera("/.git/HEAD", FORBIDDEN)
espera("/.git/index", FORBIDDEN)
espera("/.git/logs/HEAD", FORBIDDEN)
espera("/.gitignore", FORBIDDEN)
espera("/painel-soprolife/data/.gitignore", FORBIDDEN)

print("── Código-fonte e backend ──")
espera("/painel-soprolife/nucleo-m15/app/config.py", FORBIDDEN)
espera("/painel-soprolife/nucleo-m15/.env.example", FORBIDDEN)
espera("/painel-soprolife/nucleo-m15/", FORBIDDEN)
espera("/painel-soprolife/scripts/command-center-local-server.py", FORBIDDEN)
espera("/painel-soprolife/scripts/panel_access_gate.py", FORBIDDEN)
espera("/painel-soprolife/nucleo-m15/var/m15_nucleo.db", FORBIDDEN)

print("── Todo dado operacional do painel exige sessão ──")
for nome in (
    "financeiro-summary.local.json",
    "ultimos-lancamentos-summary.local.json",
    "custos-investimentos-summary.local.json",
    "marketing-seo.local.json",
    "leads-summary.local.json",
    "followup-pacientes-summary.local.json",
    "crm-clinicas.local.json",
    "crm-contatos-b2b-summary.local.json",
    "auditoria-summary.local.json",
    "resumo-dashboard.local.json",
    "parcerias-pastore-summary.local.json",
    "saude-operacional-summary.local.json",
    "runtime-status.local.json",
    "followup-clinicas-summary.local.json",
    "resumo.json",
    "leads.json",
    "marketing.json",
    "m15-config.json",
):
    espera(f"/painel-soprolife/data/{nome}", PROTECTED_DATA)

print("── Apresentação continua pública (a tela de login precisa dela) ──")
espera("/painel-soprolife/login.html", PUBLIC)
espera("/painel-soprolife/js/login.js", PUBLIC)
espera("/painel-soprolife/js/m15-security.js", PUBLIC)
espera("/painel-soprolife/css/style.css", PUBLIC)
espera("/painel-soprolife/assets/soprolife-logo.png", PUBLIC)

print("── Documentação interna não sai por HTTP ──")
# Os relatórios descrevem arquitetura, caminhos internos e números
# operacionais; o da própria M25.23 descreve o desenho do gate.
espera("/RELATORIO_M25_23_GATE_AUTENTICACAO_PRIVACIDADE_20260811.md", FORBIDDEN)
espera("/RELATORIO_M25_22_VERIFICACAO_FINANCEIRO_INTEGRADO_20260811.md", FORBIDDEN)
espera("/CLAUDE.md", FORBIDDEN)
espera("/docs/qualquer-nota.md", FORBIDDEN)
espera("/painel-soprolife/README.md", FORBIDDEN)
espera("/painel-soprolife/SECURITY.md", FORBIDDEN)

print("── Site institucional intacto (não pode regredir) ──")
espera("/", PUBLIC)
espera("/index.html", PUBLIC)
espera("/espirometria.html", PUBLIC)
espera("/servicos/", PUBLIC)
espera("/assets/logo.svg", PUBLIC)
espera("/img/hero.png", PUBLIC)
espera("/sitemap.xml", PUBLIC)
espera("/robots.txt", PUBLIC)
espera("/favicon.ico", PUBLIC)

print("── Fail-closed: caminho novo sob o painel nasce protegido ──")
espera("/painel-soprolife/qualquer-coisa-nova.html", PROTECTED_PAGE)
espera("/painel-soprolife/js/app.js", PROTECTED_PAGE)
espera("/painel-soprolife/templates/", PROTECTED_PAGE)
espera("/painel-soprolife/docs/", PROTECTED_PAGE)

print("── Travessia e ofuscação são recusadas, nunca normalizadas ──")
espera_invalido("/painel-soprolife/../.git/config")
espera_invalido("/painel-soprolife/data/../data-private/leads.local.json")
espera_invalido("/painel-soprolife\\data-private\\leads.local.json")
espera_invalido("/painel-soprolife/data/%00.json")
espera_invalido("/painel-soprolife/./index.html")

print("── Percent-encoding não contorna a proibição ──")
# %2e%2e = ".." — precisa ser recusado DEPOIS de decodificar.
espera_invalido("/painel-soprolife/%2e%2e/.git/config")
# %2E%67it = ".git" com caixa e encoding misturados.
espera("/%2Egit/config", FORBIDDEN)
espera("/painel-soprolife/DATA-PRIVATE/leads.local.json", FORBIDDEN)
espera("/PAINEL-SOPROLIFE/data/resumo.json", PROTECTED_DATA)

print("── Só a porta de entrada vira tela de login ──")
caso("/painel-soprolife/ é porta de entrada", is_panel_entry("/painel-soprolife/"))
caso("/painel-soprolife/index.html é porta de entrada",
     is_panel_entry("/painel-soprolife/index.html"))
caso("um .json NÃO devolve HTML de login",
     not is_panel_entry("/painel-soprolife/data/resumo.json"))
caso("data-private NÃO devolve HTML de login",
     not is_panel_entry("/painel-soprolife/data-private/leads.local.json"))
caso("querystring não muda a porta de entrada",
     is_panel_entry("/painel-soprolife/index.html?v=2026"))

print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} regressão(ões) detectada(s).")
    sys.exit(1)
print("RESULTADO: gate de acesso estático íntegro.")
sys.exit(0)
