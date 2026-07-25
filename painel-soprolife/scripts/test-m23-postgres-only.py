#!/usr/bin/env python3
"""
SoproLife M23 — guardas estáticas da arquitetura PostgreSQL-only.

Falha se o Google Sheets voltar a ser fonte de runtime, destino de escrita,
fallback ou dependência de saúde do Command Center.

Verifica, 100% offline e sem credencial:

  A. contrato canônico e guarda de modo;
  B. esteira de atualização sem leitor de planilha;
  C. utilitários legados bloqueados fail-closed;
  D. systemd sem ADC pessoal para dado de negócio;
  E. frontend sem caminho de escrita para Apps Script;
  F. Search Console/GA4 preservados;
  G. nenhum dado de exemplo substituindo dado operacional em produção.

Uso:
    python3 painel-soprolife/scripts/test-m23-postgres-only.py
Exit: 0 = arquitetura íntegra | 1 = regressão detectada.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / "scripts"
SYSTEMD = RAIZ / "systemd"
JS = RAIZ / "js"

FALHAS = 0


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + detalhe if detalhe else ''}")


def ler(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def sem_comentarios_sh(texto: str) -> str:
    """Só as linhas executáveis — comentário que CITA o legado não é uso."""
    return "\n".join(
        linha for linha in texto.splitlines() if not linha.lstrip().startswith("#")
    )


#: Leitores/escritores legados que jamais podem rodar pela esteira automática.
LEITORES_LEGADOS = [
    "read-sheets-summary-adc.py",
    "read-sheets-summary-dry-run.py",
    "read-crm-clinicas-adc.py",
    "read-crm-contatos-b2b-adc.py",
    "read-financeiro-lancamentos-adc.py",
    "read-leads-sheets.py",
    "read-auditoria-adc.py",
    "read-parcerias-pastore-adc.py",
    "read-multisheet-snapshot-adc.py",
    "generate-followup-clinicas.py",
    "generate-followup-pacientes.py",
    "inspect-crm-pacientes.py",
    "promote-pcmso-to-crm.py",
]

#: Utilitários de shell que gravariam por cima de um snapshot canônico.
SHELL_LEGADOS = [
    "sync-dashboard-summary.sh",
    "sync-crm-clinicas.sh",
    "import-summary-csv.sh",
    "check-vps-google-adc.sh",
]


# ─────────────────────────── A) Contrato canônico ───────────────────────────
print("A) Contrato canônico e guarda de modo")

contrato_path = RAIZ / "core" / "contracts" / "data-source-mode.json"
caso("contrato de fonte de dados existe", contrato_path.exists())

contrato = json.loads(ler(contrato_path)) if contrato_path.exists() else {}
caso("modo declarado é postgresql_only", contrato.get("modo") == "postgresql_only")
caso("fonte canônica é PostgreSQL",
     contrato.get("fonte_canonica", {}).get("tipo") == "postgresql")
caso("contrato proíbe dado de negócio em código/Git",
     "planilha" in contrato.get("fonte_canonica", {}).get("regra", "").lower())

guarda = SCRIPTS / "data_source_mode.py"
caso("guarda de modo existe", guarda.exists())

if guarda.exists():
    r = subprocess.run([sys.executable, str(guarda), "--self-test"],
                       capture_output=True, text=True)
    caso("self-test da guarda passa", r.returncode == 0, r.stdout[-200:])

    # Fail-closed de verdade: contrato ausente NÃO pode abrir o portão.
    fonte = ler(guarda)
    caso("guarda mantém fallback fechado quando o contrato some",
         "_FALLBACK" in fonte and 'MODE_POSTGRES_ONLY,' in fonte)
    caso("guarda ignora modo desconhecido no contrato",
         "declared = MODE_POSTGRES_ONLY" in fonte)


# ──────────────────── B) Esteira sem leitor de planilha ─────────────────────
print()
print("B) Esteira de atualização")

update_sh = ler(SCRIPTS / "update-local-data.sh")
exec_sh = sem_comentarios_sh(update_sh)

for nome in LEITORES_LEGADOS + SHELL_LEGADOS:
    caso(f"esteira não executa {nome}", nome not in exec_sh)

caso("esteira gera snapshots do PostgreSQL", "exportar-snapshots" in exec_sh)
caso("esteira valida o modo antes de gerar", "data_source_mode.py --check" in exec_sh)
caso("esteira aborta se o modo não for postgresql_only",
     "atualização abortada" in update_sh)
caso("esteira falha honestamente sem a fonte canônica",
     "ÚLTIMO SNAPSHOT VÁLIDO" in update_sh and "_FALHAS" in exec_sh)
caso("esteira não usa CSV de planilha como fonte",
     "SOPROLIFE_SUMMARY_CSV" not in exec_sh and "resumo-dashboard.csv" not in exec_sh)
caso("esteira não verifica ADC pessoal",
     "application_default_credentials" not in exec_sh)


# ───────────────── C) Utilitários legados bloqueados ────────────────────────
print()
print("C) Utilitários legados fail-closed")

for nome in LEITORES_LEGADOS:
    caminho = SCRIPTS / nome
    if not caminho.exists():
        caso(f"{nome} existe para verificação", False)
        continue
    fonte = ler(caminho)
    caso(f"{nome} bloqueia execução em modo canônico",
         "data_source_mode.block_legacy_sheets" in fonte)

for nome in SHELL_LEGADOS:
    caminho = SCRIPTS / nome
    if not caminho.exists():
        caso(f"{nome} existe para verificação", False)
        continue
    caso(f"{nome} bloqueia execução em modo canônico",
         "data_source_mode.py --check" in ler(caminho))

# O bloqueio precisa funcionar de fato, não só existir no código.
alvo = SCRIPTS / "read-leads-sheets.py"
if alvo.exists():
    r = subprocess.run([sys.executable, str(alvo), "--dry-run"],
                       capture_output=True, text=True, cwd=str(RAIZ.parent))
    caso("leitor legado realmente recusa executar (exit 3)", r.returncode == 3,
         f"exit={r.returncode}")
    caso("mensagem de bloqueio explica o escape humano",
         "SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION" in (r.stdout + r.stderr))


# ─────────────────────────── D) systemd ─────────────────────────────────────
print()
print("D) Unidades systemd")

units = sorted(SYSTEMD.glob("*.service")) + sorted(SYSTEMD.glob("*.timer"))
caso("unidades systemd encontradas", len(units) > 0)

for unit in units:
    texto = ler(unit)
    diretivas = sem_comentarios_sh(texto)
    caso(f"{unit.name} não libera o escape de migração legada",
         not re.search(r"(?m)^Environment=SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=",
                       diretivas))
    caso(f"{unit.name} não aponta ADC pessoal para dado de negócio",
         not re.search(r"(?m)^Environment=CLOUDSDK_CONFIG=", diretivas))

update_unit = ler(SYSTEMD / "soprolife-update-data.service")
caso("timer de dados carrega credencial do banco canônico",
     "EnvironmentFile=/opt/soprolife/secrets/m15.env" in update_unit)
caso("timer de dados não usa o venv de Google Sheets",
     "venvs/google-sheets" not in sem_comentarios_sh(update_unit))


# ─────────────────────────── E) Frontend ────────────────────────────────────
print()
print("E) Caminhos de escrita do painel")

app_js = ler(JS / "app.js")
caso("painel não tem mais o writer do Apps Script",
     "submitToCommandCenter" not in app_js)
caso("painel não consulta status do proxy legado",
     "api/command-center" not in app_js)
caso("mudança de etapa de lead grava pela API",
     "atualizarEtapaLeadNoBanco" in app_js and "/leads/" in app_js)
caso("mudança de etapa de clínica grava pela API",
     "atualizarStatusParceiroNoBanco" in app_js and "/parceiros/" in app_js)
caso("escrita exige sessão autenticada do núcleo",
     "m15Session" in app_js and "hasSession()" in app_js)
caso("vínculo B2B usa o fluxo canônico da Central",
     "abrirVinculoB2BCanonico" in app_js and 'central.open("contato-b2b"' in app_js)
caso("etapas de lead usam o vocabulário canônico da API",
     '"em_contato"' in app_js and '"aguardando_retomada"' in app_js)
caso("status de parceiro usa o vocabulário canônico da API",
     '"em_negociacao"' in app_js and '"prospecto"' in app_js)

servidor = ler(SCRIPTS / "command-center-local-server.py")
caso("servidor local não encaminha mais para o Apps Script",
     "urllib.request" not in servidor)
caso("servidor local não lê configuração com token de Apps Script",
     "apiToken" not in servidor)
caso("rota legada responde 410 e aponta a fonte canônica",
     "410" in servidor and "postgresql" in servidor)
caso("proxy M15 continua ativo", "_M15_PREFIX" in servidor)


# ───────────────────── F) Search Console e GA4 ──────────────────────────────
print()
print("F) Marketing preservado")

ids = {i.get("id") for i in contrato.get("integracoes_permitidas", [])}
caso("contrato permite Search Console", "search_console" in ids)
caso("contrato permite GA4", "ga4" in ids)
caso("esteira ainda atualiza Marketing & SEO",
     "read-marketing-seo-adc.py" in exec_sh)
caso("conector de marketing NÃO foi bloqueado",
     "block_legacy_sheets" not in ler(SCRIPTS / "read-marketing-seo-adc.py"))
caso("credencial de marketing é conta de serviço",
     "SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT=1" in update_sh)

runtime_sh = ler(SCRIPTS / "generate-runtime-status.sh")
caso("status de runtime declara PostgreSQL como canônico",
     '"canonical": "postgresql"' in runtime_sh)
caso("status de runtime declara Sheets descomissionado",
     '"decommissioned": True' in runtime_sh
     and '"requiredForProduction": False' in runtime_sh)
caso("status de runtime mantém marketing habilitado",
     "searchConsole" in runtime_sh and "ga4" in runtime_sh)


# ──────────────── G) Sem exemplo no lugar de dado operacional ───────────────
print()
print("G) Nenhum dado de exemplo em produção")

for demo in ("data/leads.json", "data/crm-clinicas.json", "data/resumo.json",
             "data/parcerias-pastore-summary.json"):
    caso(f"esteira nunca aponta para {demo}", demo not in update_sh)

saude = ler(SCRIPTS / "generate-saude-operacional.py")
caso("saúde operacional mede a fonte canônica, não a planilha",
     "fonte_operacional" in saude)
caso("saúde operacional alerta quando faltam snapshots do banco",
     "ALERTA-FONTES-POSTGRES" in saude)
caso("saúde operacional trata Sheets como legado descomissionado",
     "google_sheets_legado" in saude)

refresh = ler(SCRIPTS / "soprolife-operational-refresh.sh")
caso("frescor não pede renovação de ADC pessoal",
     "gcloud auth application-default login" not in refresh)
caso("frescor usa a conta de serviço de marketing",
     "MARKETING_CREDENTIAL" in refresh)
caso("modos de Apps Script ficam bloqueados",
     "bloquear_legado" in refresh)


print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} falha(s).")
    sys.exit(1)
print("RESULTADO: todas as guardas M23 passaram.")
