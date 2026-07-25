#!/usr/bin/env python3
"""M21 — credencial durável de leitura para Search Console e GA4.

Prova, sem rede e sem nenhuma chave real:
  - a conta de serviço tem precedência sobre o ADC pessoal;
  - com conta de serviço EXIGIDA e ausente, o conector falha FECHADO
    (credential_pending) em vez de voltar a depender de ADC pessoal;
  - "Reautenticação necessária" não aparece mais quando a credencial é conta
    de serviço: 403/permissão vira "credencial/configuração pendente";
  - falha nunca apaga o último snapshot válido;
  - a retentativa é limitada e NÃO retenta erro de credencial/permissão;
  - o snapshot registra só o TIPO da credencial, nunca identidade ou chave;
  - nenhuma escrita em Google Sheets existe neste caminho de código.

Uso:  python3 painel-soprolife/scripts/test-marketing-credencial.py
Exit: 0 = todos passaram | 1 = houve falha.
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

_spec = importlib.util.spec_from_file_location(
    "read_marketing_seo_adc", SCRIPT_DIR / "read-marketing-seo-adc.py"
)
mkt = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(mkt)

import freshness_contract as fc  # noqa: E402

FALHAS = 0


def caso(nome, cond, det=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + det if det else ''}")


def _limpar_env():
    for chave in ("SOPROLIFE_MARKETING_CREDENTIALS",
                  "SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT",
                  "GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ.pop(chave, None)


# Arquivo de conta de serviço SINTÉTICO: estrutura mínima, sem chave real.
# `private_key` é deliberadamente um marcador inválido — nada aqui é usável.
_SA_SINTETICA = {
    "type": "service_account",
    "project_id": "projeto-sintetico",
    "private_key_id": "0" * 40,
    "private_key": "-----BEGIN PRIVATE KEY-----\nSINTETICO-INVALIDO\n"
                   "-----END PRIVATE KEY-----\n",
    "client_email": "sintetica@projeto-sintetico.iam.gserviceaccount.invalid",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def _adc_falso(scopes=None):
    """Substituto de google.auth.default: devolve credencial de ADC pessoal."""
    return object(), "projeto-dev"


print("M21 — resolução da credencial de leitura de Marketing")
_limpar_env()

with tempfile.TemporaryDirectory() as tmp:
    sa_path = Path(tmp) / "marketing-readonly.json"
    sa_path.write_text(json.dumps(_SA_SINTETICA), encoding="utf-8")
    sa_path.chmod(0o640)
    nao_sa = Path(tmp) / "adc-pessoal.json"
    nao_sa.write_text(json.dumps({"type": "authorized_user"}), encoding="utf-8")
    nao_sa.chmod(0o640)

    # ── precedência ─────────────────────────────────────────────────────────
    os.environ["SOPROLIFE_MARKETING_CREDENTIALS"] = str(sa_path)
    caso("o caminho explícito da conta de serviço é encontrado",
         mkt._service_account_path() == sa_path)
    caso("o tipo do arquivo é classificado como service_account",
         mkt._classificar_arquivo_credencial(sa_path) == "service_account")

    # A criação real da credencial falha (chave sintética inválida) e isso
    # DEVE virar credential_pending, nunca cair para ADC pessoal.
    creds, kind, erro = mkt.resolver_credencial([mkt.SC_SCOPE], _adc_falso)
    caso("chave inválida vira credential_pending (nunca ADC pessoal)",
         creds is None and erro == "CREDENTIAL_PENDING" and kind == mkt.CRED_NONE,
         f"kind={kind} erro={erro}")

    # ── arquivo presente mas que NÃO é conta de serviço ─────────────────────
    os.environ["SOPROLIFE_MARKETING_CREDENTIALS"] = str(nao_sa)
    creds, kind, erro = mkt.resolver_credencial([mkt.SC_SCOPE], _adc_falso)
    caso("arquivo que não é conta de serviço é recusado, não usado às cegas",
         creds is None and erro == "CREDENTIAL_PENDING")

    # ── arquivo legível por qualquer usuário: falha fechado ─────────────────
    os.environ["SOPROLIFE_MARKETING_CREDENTIALS"] = str(sa_path)
    sa_path.chmod(0o644)
    creds, kind, erro = mkt.resolver_credencial([mkt.SC_SCOPE], _adc_falso)
    caso("credencial legível por 'other' é recusada",
         creds is None and erro == "CREDENTIAL_PENDING" and kind == mkt.CRED_NONE)
    sa_path.chmod(0o640)

    # ── conta de serviço EXIGIDA e ausente: fail-closed ─────────────────────
    _limpar_env()
    os.environ["SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT"] = "1"
    caso("com conta de serviço exigida, é exigida de verdade",
         mkt._requer_service_account() is True)
    creds, kind, erro = mkt.resolver_credencial([mkt.SC_SCOPE], _adc_falso)
    caso("sem conta de serviço, produção NÃO cai para ADC pessoal",
         creds is None and erro == "CREDENTIAL_PENDING" and kind == mkt.CRED_NONE,
         f"kind={kind} erro={erro}")
    caso("o diagnóstico offline devolve exit 16 (credencial pendente)",
         mkt.cmd_credential_check() == fc.EXIT_CREDENTIAL_PENDING)

    # ── desenvolvimento: ADC pessoal continua permitido ─────────────────────
    _limpar_env()
    creds, kind, erro = mkt.resolver_credencial([mkt.SC_SCOPE], _adc_falso)
    caso("em desenvolvimento o ADC pessoal continua servindo",
         creds is not None and kind == mkt.CRED_PERSONAL_ADC and erro is None)

_limpar_env()

# ── estados exibidos: sem "Reautenticação necessária" com conta de serviço ──
print()
print("Estados exibidos e preservação do último snapshot válido")

_CFG = {"searchConsoleSiteUrl": "https://exemplo.invalid/",
        "ga4PropertyId": "000000000"}
_PERIODO = ("2026-07-01", "2026-07-24", 24)

anterior = mkt._montar_snapshot(
    _CFG, None,
    {"searchConsole": {"ok": True, "data": {"totals": {"impressions": 10, "clicks": 2}},
                       "raw_warnings": [], "error_code": None},
     "ga4": {"ok": True, "data": {"totals": {"users": 5}},
             "raw_warnings": [], "error_code": None}},
    _PERIODO, "2026-07-24T00:00:00+00:00", 26, mkt.CRED_SERVICE_ACCOUNT)

caso("snapshot bem-sucedido registra o tipo de credencial",
     anterior["meta"]["credentialKind"] == mkt.CRED_SERVICE_ACCOUNT)
caso("snapshot não contém e-mail, projeto nem chave da credencial",
     all(t not in json.dumps(anterior) for t in
         ("iam.gserviceaccount", "private_key", "projeto-sintetico",
          "client_email")))

falha_403 = mkt._montar_snapshot(
    _CFG, anterior,
    {"searchConsole": {"ok": False, "data": {}, "raw_warnings": ["403 forbidden"],
                       "error_code": "PERMISSION_DENIED"},
     "ga4": {"ok": False, "data": {}, "raw_warnings": ["403 forbidden"],
             "error_code": "PERMISSION_DENIED"}},
    _PERIODO, "2026-07-25T00:00:00+00:00", 26, mkt.CRED_SERVICE_ACCOUNT)

sc = falha_403["meta"]["sourceStatus"]["searchConsole"]
caso("com conta de serviço, 403 vira CREDENTIAL_PENDING",
     sc["errorCode"] == "CREDENTIAL_PENDING")
caso("com conta de serviço, authenticationRequired é falso",
     sc["authenticationRequired"] is False and sc["credentialPending"] is True)
caso("a mensagem exibida não fala de reautenticação de ADC",
     "Reautenticação" not in sc["errorMessageSafe"])
caso("o estado de frescor é credential_pending",
     fc.avaliar_frescor(sc, 26)["freshnessStatus"] == fc.CREDENTIAL_PENDING)
caso("a falha preservou os dados anteriores de Search Console",
     falha_403["searchConsole"] == anterior["searchConsole"])
caso("a falha preservou os dados anteriores de GA4",
     falha_403["ga4"] == anterior["ga4"])
caso("a falha preservou o último lastSuccessAt",
     sc["lastSuccessAt"] == "2026-07-24T00:00:00+00:00")
caso("a última tentativa foi registrada mesmo tendo falhado",
     sc["lastAttemptAt"] == "2026-07-25T00:00:00+00:00")

falha_adc = mkt._montar_snapshot(
    _CFG, anterior,
    {"searchConsole": {"ok": False, "data": {}, "raw_warnings": ["invalid_grant"],
                       "error_code": "AUTH_REQUIRED"}},
    _PERIODO, "2026-07-25T00:00:00+00:00", 26, mkt.CRED_PERSONAL_ADC)
caso("com ADC pessoal, AUTH_REQUIRED continua sendo reautenticação (diagnóstico honesto)",
     falha_adc["meta"]["sourceStatus"]["searchConsole"]["errorCode"] == "AUTH_REQUIRED")

# ── retentativa limitada ────────────────────────────────────────────────────
print()
print("Retentativa limitada e o que NÃO deve ser retentado")

chamadas = {"n": 0}


def _consulta_transitoria():
    chamadas["n"] += 1
    if chamadas["n"] < 3:
        return {}, ["Search Console: 503 service unavailable"]
    return {"totals": {"impressions": 1}}, []


dormidas = []
dados, _w = mkt._com_retentativa("Search Console", _consulta_transitoria,
                                 dormir=dormidas.append)
caso("erro transitório é retentado até obter dados", bool(dados) and chamadas["n"] == 3)
caso("o backoff é limitado e crescente", dormidas == [2, 6], str(dormidas))

chamadas_auth = {"n": 0}


def _consulta_permissao():
    chamadas_auth["n"] += 1
    return {}, ["GA4: 403 PERMISSION_DENIED"]


dormidas_auth = []
mkt._com_retentativa("GA4", _consulta_permissao, dormir=dormidas_auth.append)
caso("erro de permissão NÃO é retentado (não melhora tentando de novo)",
     chamadas_auth["n"] == 1 and dormidas_auth == [])

chamadas_max = {"n": 0}


def _consulta_sempre_falha():
    chamadas_max["n"] += 1
    return {}, ["Search Console: 500 internal"]


mkt._com_retentativa("Search Console", _consulta_sempre_falha, dormir=lambda s: None)
caso("a retentativa tem teto (nunca martela a API)",
     chamadas_max["n"] == mkt.RETRY_MAX_TENTATIVAS)

# ── somente leitura: nenhuma escrita em Sheets neste caminho ────────────────
print()
print("Somente leitura")

fonte = (SCRIPT_DIR / "read-marketing-seo-adc.py").read_text(encoding="utf-8")
caso("os escopos declarados são somente leitura",
     mkt.SC_SCOPE.endswith(".readonly") and mkt.GA4_SCOPE.endswith(".readonly"))
import re  # noqa: E402

# Todo escopo OAuth citado no conector precisa terminar em .readonly. Isto olha
# os escopos de verdade (googleapis.com/auth/...), não o nome da API no build().
_escopos = re.findall(r"https://www\.googleapis\.com/auth/[\w.]+", fonte)
caso("nenhum escopo de escrita aparece no conector",
     bool(_escopos) and all(e.endswith(".readonly") for e in _escopos),
     str(sorted(set(_escopos))))
caso("nenhuma chamada de escrita em Sheets/Drive existe no conector",
     all(t not in fonte for t in ("values().update", "values().append",
                                  "batchUpdate", "files().create")))
caso("o conector nunca imprime a chave nem o e-mail da credencial",
     "private_key" in mkt.FORBIDDEN_OUTPUT and "client_email" not in fonte)

print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} falha(s).")
    sys.exit(1)
print("RESULTADO: todos os casos passaram.")
