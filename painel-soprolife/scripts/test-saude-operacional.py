#!/usr/bin/env python3
"""
SoproLife — Testes automatizados do gerador de Saúde Operacional (M3 v3).

100% local e offline: fixtures SINTÉTICAS em diretório temporário
(tempfile.mkdtemp), mtimes definidos RELATIVOS ao agora (os.utime) — sem
horário fixo frágil, sem rede, sem Google, sem VPS, sem data-private,
sem credenciais. Nenhum dado real é lido ou escrito.

Uso:
    python3 painel-soprolife/scripts/test-saude-operacional.py
Exit: 0 = todos os casos passaram | 1 = houve falha.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Carrega o gerador (nome com hífen) e garante que 'import pii_guard'
# dentro dele resolva a partir da mesma pasta.
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "gen_saude", SCRIPTS / "generate-saude-operacional.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

FALHAS = 0


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + detalhe if detalhe else ''}")


def indicador(payload, id_):
    return next((i for i in payload["indicadores"] if i["id"] == id_), None)


def tem_alerta(payload, nivel=None, id_prefix=None):
    for a in payload["alertas"]:
        if nivel and a["nivel"] != nivel:
            continue
        if id_prefix and not str(a["id"]).startswith(id_prefix):
            continue
        return True
    return False


def fixture_dir(idade_min=1, omitir=(), corromper=(), pii_em=()):
    """Cria um data-dir sintético completo e seguro; mtime relativo ao agora."""
    d = Path(tempfile.mkdtemp(prefix="saude-teste-"))
    agora = time.time()
    mtime = agora - idade_min * 60

    conteudos = {
        "runtime-status": {"googleSheets": {"configured": True, "safeToDisplay": True}},
        "resumo-dashboard": {"totalLeads": 5, "leadsNovos": 1},
        "marketing-seo": {"meta": {"safeToDisplay": True, "containsPersonalData": False,
                                   "sources": {"searchConsole": True, "ga4": True}},
                          "warnings": []},
        "auditoria": {"source": {"safeToDisplay": True, "containsPersonalData": False},
                      "stats": {"total_eventos": 3, "erros": 0}},
    }
    generico = {"source": {"safeToDisplay": True, "containsPersonalData": False},
                "total": 2}

    for nome, cfg in gen.FONTES.items():
        if nome in omitir:
            continue
        path = d / cfg["arquivo"]
        if nome in corromper:
            path.write_text("{isto nao e json", encoding="utf-8")
        else:
            data = dict(conteudos.get(nome, generico))
            if nome in pii_em:
                data = json.loads(json.dumps(data))
                data.setdefault("source", {})["containsPersonalData"] = True
            path.write_text(json.dumps(data), encoding="utf-8")
        os.utime(path, (mtime, mtime))
    return d


def gerar(d, check_exit=None):
    estado = gen.coletar(d)
    return gen.construir(estado, check_exit)


def main() -> int:
    print("M3 v3 — testes do gerador de Saúde Operacional (fixtures sintéticas)")
    dirs = []

    # ── 1. Cenário feliz ─────────────────────────────────────────────────────
    d = fixture_dir(idade_min=1); dirs.append(d)
    p = gerar(d, check_exit=0)
    caso("cenario feliz: flags do payload corretas",
         p["source"]["safeToDisplay"] is True
         and p["source"]["containsPersonalData"] is False
         and p["source"]["dadosReais"] is True)
    caso("cenario feliz: status geral ok", p["status_geral"] == "ok",
         f"veio {p['status_geral']} — alertas: {[a['id'] for a in p['alertas']]}")
    caso("cenario feliz: 10 indicadores", len(p["indicadores"]) == 10)

    # ── 2/3. check-access exit ───────────────────────────────────────────────
    caso("check-access-exit 0 -> indicador ok",
         indicador(p, "check_access")["status"] == "ok")
    p2 = gerar(d, check_exit=3)
    caso("check-access-exit != 0 -> indicador critico",
         indicador(p2, "check_access")["status"] == "critico")
    caso("check-access-exit != 0 -> alerta critico",
         tem_alerta(p2, nivel="critico", id_prefix="ALERTA-CHECK-ACCESS"))
    p3 = gerar(d, check_exit=None)
    caso("sem check-access-exit -> desconhecido (nunca inventa)",
         indicador(p3, "check_access")["status"] == "desconhecido")

    # ── 4. JSON inválido -> critico ──────────────────────────────────────────
    d = fixture_dir(idade_min=1, corromper=("financeiro",)); dirs.append(d)
    p = gerar(d, check_exit=0)
    caso("JSON invalido -> alerta critico",
         tem_alerta(p, nivel="critico", id_prefix="ALERTA-JSON-"))
    caso("JSON invalido -> status geral critico", p["status_geral"] == "critico")

    # ── 5. containsPersonalData=true -> critico ──────────────────────────────
    d = fixture_dir(idade_min=1, pii_em=("leads",)); dirs.append(d)
    p = gerar(d, check_exit=0)
    caso("flag de PII -> alerta critico",
         tem_alerta(p, nivel="critico", id_prefix="ALERTA-PII-"))

    # ── 6. arquivo ausente -> atencao, sem crash ─────────────────────────────
    d = fixture_dir(idade_min=1, omitir=("pastore", "custos")); dirs.append(d)
    p = gerar(d, check_exit=0)
    caso("fontes ausentes -> arquivos_locais atencao",
         indicador(p, "arquivos_locais")["status"] == "atencao")
    caso("fontes ausentes -> nao vira critico geral", p["status_geral"] == "atencao")

    # ── 7. arquivos antigos > 24h -> pipeline critico ────────────────────────
    d = fixture_dir(idade_min=25 * 60); dirs.append(d)
    p = gerar(d, check_exit=0)
    caso("dados > 24h -> pipeline critico",
         indicador(p, "pipeline_update")["status"] == "critico")
    caso("dados > 24h -> alerta 'painel com dados velhos'",
         tem_alerta(p, nivel="critico", id_prefix="ALERTA-PIPELINE-PARADO"))

    # ── 8. arquivo recente -> pipeline ok (e faixa intermediaria -> atencao) ─
    d = fixture_dir(idade_min=1); dirs.append(d)
    caso("dados recentes -> pipeline ok",
         indicador(gerar(d, 0), "pipeline_update")["status"] == "ok")
    d = fixture_dir(idade_min=90); dirs.append(d)
    caso("dados de 90 min -> pipeline atencao",
         indicador(gerar(d, 0), "pipeline_update")["status"] == "atencao")

    # ── 9. allowlist estrita em indicadores/alertas ──────────────────────────
    d = fixture_dir(idade_min=1); dirs.append(d)
    p = gerar(d, check_exit=0)
    ALLOWED_IND = {"id", "label", "status", "detalhe", "tip"}
    ALLOWED_AL = {"id", "nivel", "titulo", "mensagem", "proximo_passo"}
    caso("indicadores só com campos da allowlist",
         all(set(i) <= ALLOWED_IND for i in p["indicadores"]))
    caso("alertas só com campos da allowlist",
         all(set(a) <= ALLOWED_AL for a in p["alertas"]))

    # ── 10. pii_guard não acusa PII no payload final ─────────────────────────
    try:
        gen.validar(p)
        caso("pii_guard/validar aceita o payload final", True)
    except SystemExit:
        caso("pii_guard/validar aceita o payload final", False, "pii_guard abortou")
    except AssertionError as exc:
        caso("pii_guard/validar aceita o payload final", False, str(exc))

    # limpeza dos temporários sintéticos
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)

    print()
    total_fail = FALHAS
    if total_fail:
        print(f"RESULTADO: {total_fail} caso(s) FALHARAM.")
        return 1
    print("RESULTADO: todos os casos passaram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
