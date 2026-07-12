#!/usr/bin/env python3
"""
SoproLife — Testes do contrato de frescor operacional (M14.3A.1).

Valida, 100% offline e com relógio injetado (nunca depende da hora real):
  - estados fresh/stale/unknown/authentication_required/unavailable/error;
  - fixtures sintéticas de scripts/fixtures/freshness/;
  - snapshot legado v1 (formato de produção pré-M14.3A.1);
  - snapshot anterior preservado quando uma fonte falha;
  - GA4 e Search Console independentes;
  - escrita atômica (tmp + validação + rename; inválido não substitui válido);
  - ausência de dado é diferente de zero;
  - schema inválido rejeitado com exit code próprio;
  - nenhum segredo/path privado nas saídas.

Uso: python3 painel-soprolife/scripts/test-freshness-contract.py
Exit: 0 = todos passaram | 1 = houve falha.
"""

import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import freshness_contract as fc  # noqa: E402

_spec = importlib.util.spec_from_file_location("rm", SCRIPTS / "read-marketing-seo-adc.py")
rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rm)

FIXTURES = SCRIPTS / "fixtures" / "freshness"
AGORA = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)

FALHAS = 0


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + detalhe if detalhe else ''}")


def fixture(nome):
    return json.loads((FIXTURES / f"{nome}.json").read_text(encoding="utf-8"))


print("── Estados de frescor por fixture (relógio injetado: 2026-07-12T12:00Z) ──")
ESPERADOS = {
    "fresh": (fc.FRESH, fc.EXIT_FRESH),
    "stale": (fc.STALE, fc.EXIT_STALE),
    "authentication-required": (fc.AUTH_REQUIRED_STATE, fc.EXIT_AUTH_REQUIRED),
    "unavailable": (fc.UNAVAILABLE, fc.EXIT_UNAVAILABLE),
    "legacy-v1": (fc.AUTH_REQUIRED_STATE, fc.EXIT_AUTH_REQUIRED),
}
for nome, (estado, codigo) in ESPERADOS.items():
    av = rm._avaliar_snapshot(fixture(nome), agora=AGORA)
    caso(f"{nome} → {estado}", av["overall"] == estado, f"obteve {av['overall']}")
    caso(f"{nome} → exit {codigo}", av["exit"] == codigo, f"obteve {av['exit']}")

caso("snapshot inexistente → unknown/exit 15",
     rm._avaliar_snapshot(None, agora=AGORA)["exit"] == fc.EXIT_UNKNOWN)

print("── Relógio injetável: mesma fixture, horas diferentes ──")
snap = fixture("fresh")
cedo = rm._avaliar_snapshot(snap, agora=datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc))
tarde = rm._avaliar_snapshot(snap, agora=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
caso("fresh às 10h do mesmo dia", cedo["overall"] == fc.FRESH)
caso("stale 3 dias depois (sem tocar o arquivo)", tarde["overall"] == fc.STALE)
caso("--max-age-hours aperta o limite",
     rm._avaliar_snapshot(snap, max_age_hours=1,
                          agora=datetime(2026, 7, 12, 14, 0, tzinfo=timezone.utc))["overall"] == fc.STALE)

print("── GA4 e Search Console independentes ──")
misto = fixture("fresh")
misto["meta"]["sourceStatus"]["ga4"].update({
    "status": "failed", "errorCode": "AUTH_REQUIRED",
    "errorMessageSafe": fc.mensagem_segura("AUTH_REQUIRED"),
    "authenticationRequired": True, "sourceAvailable": False,
})
av = rm._avaliar_snapshot(misto, agora=AGORA)
caso("SC continua fresh com GA4 em auth",
     av["fontes"]["searchConsole"]["freshnessStatus"] == fc.FRESH)
caso("GA4 em authentication_required",
     av["fontes"]["ga4"]["freshnessStatus"] == fc.AUTH_REQUIRED_STATE)
caso("agregado reflete o pior estado", av["overall"] == fc.AUTH_REQUIRED_STATE)

print("── Preservação do snapshot anterior em falha ──")
prev = fixture("fresh")
resultados_falha = {
    "searchConsole": {"ok": False, "data": {}, "raw_warnings":
                      ["Search Console: Reauthentication is needed."],
                      "error_code": "AUTH_REQUIRED"},
    "ga4": {"ok": True, "data": {"totals": {"users": 99, "sessions": 130, "pageviews": 350}},
            "raw_warnings": [], "error_code": None},
}
cfg_fake = {"searchConsoleSiteUrl": "https://exemplo", "ga4PropertyId": "123"}
novo = rm._montar_snapshot(cfg_fake, prev, resultados_falha,
                           ("2026-06-14", "2026-07-11", 28),
                           "2026-07-12T12:00:00+00:00", 26)
caso("dados do SC preservados da última versão válida",
     novo.get("searchConsole", {}).get("totals", {}).get("impressions") == 1200)
caso("dados novos do GA4 aplicados",
     novo.get("ga4", {}).get("totals", {}).get("users") == 99)
caso("lastSuccessAt do SC mantém a data antiga",
     novo["meta"]["sourceStatus"]["searchConsole"]["lastSuccessAt"] == "2026-07-12T09:00:00+00:00")
caso("lastSuccessAt do GA4 é o da tentativa atual",
     novo["meta"]["sourceStatus"]["ga4"]["lastSuccessAt"] == "2026-07-12T12:00:00+00:00")
caso("sourceDataThrough do SC preservado",
     novo["meta"]["sourceStatus"]["searchConsole"]["sourceDataThrough"] == "2026-07-11")
caso("erro classificado como AUTH_REQUIRED",
     novo["meta"]["sourceStatus"]["searchConsole"]["errorCode"] == "AUTH_REQUIRED")

print("── Ausência é diferente de zero ──")
sem_dados = rm._montar_snapshot(cfg_fake, None,
                                {"searchConsole": {"ok": False, "data": {}, "raw_warnings": [],
                                                   "error_code": "AUTH_REQUIRED"},
                                 "ga4": {"ok": False, "data": {}, "raw_warnings": [],
                                         "error_code": "AUTH_REQUIRED"}},
                                ("2026-06-14", "2026-07-11", 28),
                                "2026-07-12T12:00:00+00:00", 26)
caso("fonte sem dados NÃO gera bloco zerado",
     "searchConsole" not in sem_dados and "ga4" not in sem_dados)
caso("estado agregado é auth, nunca 'sem tráfego'",
     rm._avaliar_snapshot(sem_dados, agora=AGORA)["overall"] == fc.AUTH_REQUIRED_STATE)

print("── Escrita atômica e proteção contra schema inválido ──")
with tempfile.TemporaryDirectory() as tmp:
    destino = Path(tmp) / "snap.json"
    fc.escrever_json_atomico(destino, novo, validador=fc.validar_snapshot_marketing)
    caso("escrita atômica grava JSON válido",
         json.loads(destino.read_text(encoding="utf-8"))["meta"]["schemaVersion"] == 2)
    caso("nenhum tmp órfão sobra no diretório",
         [p.name for p in Path(tmp).iterdir()] == ["snap.json"])

    invalido = fixture("invalid-schema")
    try:
        fc.escrever_json_atomico(destino, invalido, validador=fc.validar_snapshot_marketing)
        caso("payload inválido rejeitado", False, "escreveu sem levantar erro")
    except ValueError:
        caso("payload inválido rejeitado", True)
    caso("snapshot válido anterior intacto após rejeição",
         json.loads(destino.read_text(encoding="utf-8"))["meta"]["schemaVersion"] == 2)

print("── Classificação de erro e mensagens seguras ──")
caso("Reauthentication → AUTH_REQUIRED",
     fc.classificar_erro("503 Reauthentication is needed. Please run gcloud...") == "AUTH_REQUIRED")
caso("quota project → AUTH_REQUIRED",
     fc.classificar_erro("The API requires a quota project") == "AUTH_REQUIRED")
caso("404 → SOURCE_NOT_FOUND", fc.classificar_erro("HttpError 404") == "SOURCE_NOT_FOUND")
caso("ImportError → DEPENDENCY_MISSING",
     fc.classificar_erro("No module named google.analytics") == "DEPENDENCY_MISSING")
caso("desconhecido → SYNC_FAILED", fc.classificar_erro("boom") == "SYNC_FAILED")

texto_snapshot = json.dumps(novo, ensure_ascii=False).lower()
caso("nenhum padrão de segredo no snapshot gerado",
     not any(p in texto_snapshot for p in
             ["refresh_token", "access_token", "client_secret", "private_key",
              "/home/", "application_default", "application-default", "gcloud auth"]))
caso("warnings usam só mensagens do catálogo",
     all(any(m in w for m in fc.CATALOGO_ERROS.values()) for w in novo["warnings"]))
caso("detector de segredo pega path privado",
     fc.contem_segredo("veja /home/usuario/.config/x") and
     fc.contem_segredo("meu access_token=abc") and
     not fc.contem_segredo("Reautenticação necessária."))

print("── Exit codes do contrato ──")
caso("mapa estado→exit coerente",
     fc.exit_code_para(fc.FRESH) == 0 and fc.exit_code_para(fc.STALE) == 10 and
     fc.exit_code_para(fc.AUTH_REQUIRED_STATE) == 11 and
     fc.exit_code_para(fc.UNAVAILABLE) == 13 and fc.exit_code_para(fc.ERROR) == 14 and
     fc.exit_code_para(fc.UNKNOWN) == 15)

print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} falha(s).")
    sys.exit(1)
print("RESULTADO: todos os casos passaram.")
