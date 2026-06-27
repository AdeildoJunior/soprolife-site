#!/usr/bin/env python3
"""
SoproLife — Gerador de Últimos Lançamentos

Lê os arquivos .local.json existentes e monta uma timeline dos eventos mais
recentes do painel (quando cada módulo foi atualizado, quantos registros foram
processados, se houve erros). Não acessa APIs externas nem dados de pacientes.

Saídas:
  data/ultimos-lancamentos-summary.local.json  (seguro, exibido no painel)
  data-private/ultimos-lancamentos.local.json  (privado, gitignored — reservado)

Uso:
  python3 painel-soprolife/scripts/generate-ultimos-lancamentos.py --dry-run
  python3 painel-soprolife/scripts/generate-ultimos-lancamentos.py --write
"""

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
DATA = BASE / "painel-soprolife" / "data"
DATA_PRIVATE = BASE / "painel-soprolife" / "data-private"

SUMMARY_OUT = DATA / "ultimos-lancamentos-summary.local.json"
PRIVATE_OUT = DATA_PRIVATE / "ultimos-lancamentos.local.json"

BRT = timezone(timedelta(hours=-3))
NOW = datetime.now(timezone.utc)


def iso_now():
    return NOW.strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_br(dt):
    return dt.astimezone(BRT).strftime("%d/%m/%Y %H:%M")


def br_now():
    return fmt_br(NOW)


def parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def fmt_ts(ts_str):
    dt = parse_iso(ts_str)
    return fmt_br(dt) if dt else "—"


def is_today(ts_str):
    dt = parse_iso(ts_str)
    if not dt:
        return False
    return dt.astimezone(BRT).date() == NOW.astimezone(BRT).date()


def safe_read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def collect_eventos():
    eventos = []
    _id = [0]

    def new_id():
        _id[0] += 1
        return f"evt-{_id[0]:03d}"

    # 1. Resumo Dashboard
    d = safe_read(DATA / "resumo-dashboard.local.json")
    if d:
        ts = d.get("source", {}).get("generatedAt") or iso_now()
        n = len(d.get("cards", []))
        eventos.append(dict(id=new_id(), timestamp=ts, data_br=fmt_ts(ts),
                            origem="Planilha", tipo="Dashboard",
                            resumo=f"Resumo geral atualizado: {n} indicadores",
                            status="ok"))

    # 2. CRM Clínicas
    d = safe_read(DATA / "crm-clinicas.local.json")
    if d:
        ts = d.get("source", {}).get("generatedAt") or iso_now()
        n = len(d.get("clinicas", []))
        eventos.append(dict(id=new_id(), timestamp=ts, data_br=fmt_ts(ts),
                            origem="Planilha", tipo="CRM Clínicas",
                            resumo=f"CRM Clínicas atualizado: {n} registros",
                            status="ok"))

    # 3. Leads Summary
    d = safe_read(DATA / "leads-summary.local.json")
    if d:
        ts = d.get("source", {}).get("generatedAt") or iso_now()
        n = len(d.get("leads", []))
        eventos.append(dict(id=new_id(), timestamp=ts, data_br=fmt_ts(ts),
                            origem="Planilha", tipo="Leads",
                            resumo=f"Leads atualizados: {n} registros",
                            status="ok"))

    # 4. Follow-up Pacientes Summary
    d = safe_read(DATA / "followup-pacientes-summary.local.json")
    if d:
        ts = d.get("geradoEm") or iso_now()
        espi = d.get("espirometria", {}).get("total", 0)
        cons = d.get("consultas", {}).get("total", 0)
        eventos.append(dict(id=new_id(), timestamp=ts, data_br=fmt_ts(ts),
                            origem="Painel", tipo="Follow-up Pacientes",
                            resumo=f"Follow-up atualizado: {espi} espirometrias, {cons} consultas",
                            status="ok"))

    # 5. Follow-up Clínicas Summary
    d = safe_read(DATA / "followup-clinicas-summary.local.json")
    if d:
        ts = d.get("geradoEm") or iso_now()
        total = d.get("clinicas", {}).get("total", 0)
        eventos.append(dict(id=new_id(), timestamp=ts, data_br=fmt_ts(ts),
                            origem="Painel", tipo="Follow-up B2B",
                            resumo=f"Follow-up B2B atualizado: {total} clínicas",
                            status="ok"))

    # 6. Financeiro Summary
    d = safe_read(DATA / "financeiro-summary.local.json")
    if d:
        ts = d.get("source", {}).get("generatedAt") or iso_now()
        total = d.get("total_lancamentos", 0)
        receita = d.get("receita_exames", 0)
        eventos.append(dict(id=new_id(), timestamp=ts, data_br=fmt_ts(ts),
                            origem="Planilha", tipo="Financeiro",
                            resumo=f"Financeiro atualizado: {total} lançamentos, receita R${receita:,.2f}",
                            status="ok"))

    # 7. Marketing & SEO
    d = safe_read(DATA / "marketing-seo.local.json")
    if d:
        ts = d.get("meta", {}).get("generatedAt") or iso_now()
        sc  = d.get("searchConsole") is not None
        ga4 = d.get("ga4") is not None
        fontes = " e ".join(f for f, ok in [("Search Console", sc), ("GA4", ga4)] if ok) or "sem fontes configuradas"
        eventos.append(dict(id=new_id(), timestamp=ts, data_br=fmt_ts(ts),
                            origem="Planilha", tipo="Marketing & SEO",
                            resumo=f"Marketing & SEO atualizado com {fontes}",
                            status="ok"))

    # 8. Runtime Status (Google Sheets)
    d = safe_read(DATA / "runtime-status.local.json")
    if d:
        gs = d.get("googleSheets", {})
        ts = gs.get("lastCheckedAt") or iso_now()
        configured = gs.get("configured", False)
        label = "Conectado" if configured else "Não configurado"
        status = "ok" if configured else "pendente"
        eventos.append(dict(id=new_id(), timestamp=ts, data_br=fmt_ts(ts),
                            origem="Painel", tipo="Status Conexão",
                            resumo=f"Google Sheets: {label}",
                            status=status))

    # Fallback quando nenhum arquivo local existe
    if not eventos:
        eventos.append(dict(id=new_id(), timestamp=iso_now(), data_br=br_now(),
                            origem="Painel", tipo="Sistema",
                            resumo="Painel inicializado — execute update-local-data.sh para sincronizar",
                            status="info"))

    def sort_key(e):
        dt = parse_iso(e.get("timestamp", ""))
        return dt or datetime.min.replace(tzinfo=timezone.utc)

    eventos.sort(key=sort_key, reverse=True)
    return eventos[:10]


def build_payload(eventos):
    hoje = sum(1 for e in eventos if is_today(e.get("timestamp", "")))
    planilha = sum(1 for e in eventos if e.get("origem") == "Planilha")
    painel = sum(1 for e in eventos if e.get("origem") == "Painel")
    pendencias = sum(1 for e in eventos if e.get("status") not in ("ok", "info"))

    return {
        "source": {
            "safeToDisplay": True,
            "containsPersonalData": False,
            "containsHealthData": False,
            "generatedAt": iso_now(),
            "generator": "generate-ultimos-lancamentos.py",
        },
        "stats": {
            "total": len(eventos),
            "hoje": hoje,
            "planilha": planilha,
            "painel": painel,
            "pendencias": pendencias,
        },
        "eventos": eventos,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Mostra eventos sem gravar")
    parser.add_argument("--write", action="store_true", help="Grava os arquivos de saída")
    args = parser.parse_args()

    if not args.dry_run and not args.write:
        parser.print_help()
        sys.exit(1)

    print("Coletando eventos dos arquivos locais...")
    eventos = collect_eventos()
    print(f"  {len(eventos)} evento(s) coletado(s).")

    if args.dry_run:
        print("\nPré-visualização (dry-run — nenhum arquivo escrito):")
        for e in eventos:
            status_icon = {"ok": "✓", "erro": "✗", "pendente": "⚠", "info": "ℹ"}.get(e["status"], "?")
            print(f"  [{e['data_br']}] {status_icon} {e['origem']} · {e['tipo']}: {e['resumo']}")
        return

    payload = build_payload(eventos)

    DATA.mkdir(parents=True, exist_ok=True)
    SUMMARY_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Resumo seguro gravado: {SUMMARY_OUT.relative_to(BASE)}")

    private_payload = {**payload, "source": {**payload["source"],
                                              "note": "Arquivo privado — gitignored"}}
    DATA_PRIVATE.mkdir(parents=True, exist_ok=True)
    PRIVATE_OUT.write_text(json.dumps(private_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    PRIVATE_OUT.chmod(0o600)
    print(f"  Privado gravado (gitignored): {PRIVATE_OUT.relative_to(BASE)}")

    print("Concluído.")


if __name__ == "__main__":
    main()
