#!/usr/bin/env python3
"""
SoproLife — Gerador de Últimos Lançamentos

Lê os arquivos .local.json existentes e monta uma timeline operacional dos
eventos mais recentes do painel. Não acessa APIs externas nem dados de pacientes.

Regras de segurança:
  - Nunca copia nome, telefone, CPF, observação privada ou dado clínico.
  - Usa apenas campos agregados e seguros dos arquivos de resumo.
  - Leads individuais são derivados do leads-summary (já sanitizado).
  - Antes de gravar, valida ausência de termos proibidos.

Saídas:
  data/ultimos-lancamentos-summary.local.json  (seguro, exibido no painel)
  data-private/ultimos-lancamentos.local.json  (privado, gitignored — reservado)

Uso:
  python3 painel-soprolife/scripts/generate-ultimos-lancamentos.py --dry-run
  python3 painel-soprolife/scripts/generate-ultimos-lancamentos.py --write
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Guarda de PII compartilhada (M2) — mesma pasta deste script.
# A validação local validate_output_safety abaixo permanece como redundância.
import pii_guard

# Regras da guarda para o ultimos-lancamentos-summary:
# - titulo/descricao são TEMPLATES gerados por este script (mk() abaixo) a
#   partir de contagens e rótulos institucionais ("CRM de clínicas
#   atualizado", nome de parceria) — isentos do detector de nome, mas os
#   scans de telefone/CPF/e-mail/segredos continuam valendo para eles.
_PII_RULES = {
    "campos_pessoa": [],
    "campos_institucionais": ["titulo", "descricao", "categoria"],
    "chaves_proibidas_extras": ["proxima_acao"],
}

BASE = Path(__file__).resolve().parent.parent.parent
DATA = BASE / "painel-soprolife" / "data"
DATA_PRIVATE = BASE / "painel-soprolife" / "data-private"

SUMMARY_OUT = DATA / "ultimos-lancamentos-summary.local.json"
PRIVATE_OUT = DATA_PRIVATE / "ultimos-lancamentos.local.json"

BRT = timezone(timedelta(hours=-3))
NOW = datetime.now(timezone.utc)

# Termos absolutamente proibidos no arquivo de saída seguro
_FORBIDDEN_TERMS = [
    "cpf", "telefone", "celular",
    # "whatsapp" é permitido como nome de canal agregado ("Com WhatsApp: 4 clínicas")
    # — a proteção contra números de WhatsApp é feita pelo _FONE_RE abaixo.
    "pedido médico", "pedido medico", "laudo",
    "diagnóstico", "diagnostico", "endereço", "endereco",
    "data de nascimento", "nome completo",
    "access_token", "refresh_token", "private_key",
    "client_secret", "client_email",
    "spreadsheet_id", "/spreadsheets/d/", "https://docs.google.com",
]
_CPF_RE  = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")

# Campos PII que nunca devem aparecer em evento exportado
_BLOCKED_EVENT_FIELDS = {"nome", "telefone", "telefone_whatsapp", "celular", "whatsapp",
                          "cpf", "observacao", "observação", "laudo", "diagnostico"}


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


def parse_br_date(s):
    """Converte 'DD/MM/YYYY' ou 'DD/MM/YYYY HH:MM' → datetime aware (BRT)."""
    if not s:
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip()[:16], fmt).replace(tzinfo=BRT)
        except ValueError:
            continue
    return None


def fmt_brl(v):
    """Formata número no padrão brasileiro: 1.573,80"""
    s = f"{v:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def safe_read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def br_date_to_iso(s):
    """Converte 'DD/MM/YYYY [HH:MM]' → ISO string UTC, ou None."""
    dt = parse_br_date(s)
    if not dt:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize_event(evt):
    """Remove qualquer campo PII de um dicionário de evento."""
    return {k: v for k, v in evt.items() if k.lower() not in _BLOCKED_EVENT_FIELDS}


def validate_output_safety(payload_text):
    """Retorna lista de violações encontradas no texto do payload."""
    lower = payload_text.lower()
    erros = []
    for term in _FORBIDDEN_TERMS:
        if term in lower:
            erros.append(f"termo proibido: '{term}'")
    if _CPF_RE.search(lower):
        erros.append("padrão de CPF detectado")
    if _FONE_RE.search(lower):
        erros.append("padrão de telefone detectado")
    return erros


def collect_eventos():
    eventos = []
    _id = [0]

    def new_id():
        _id[0] += 1
        return f"evt-{_id[0]:03d}"

    def mk(categoria, tipo, titulo, descricao, timestamp, prioridade="media", status="ok"):
        return dict(
            id=new_id(),
            timestamp=timestamp,
            data_br=fmt_ts(timestamp),
            categoria=categoria,
            tipo=tipo,
            titulo=titulo,
            descricao=descricao,
            prioridade=prioridade,
            status=status,
            safeToDisplay=True,
        )

    # ── 1. Leads: eventos individuais para leads recentes ─────────────────────
    d = safe_read(DATA / "leads-summary.local.json")
    if d and d.get("source", {}).get("safeToDisplay") is True:
        leads = d.get("leads", [])
        ts_source = d.get("source", {}).get("generatedAt") or iso_now()

        # Distribuição por etapa
        etapas = Counter(l.get("etapa", "—") for l in leads if l.get("etapa"))
        etapa_txt = " · ".join(f"{e}: {n}" for e, n in etapas.most_common(4))
        total = len(leads)
        prioridade_leads = "alta" if total > 0 else "baixa"

        eventos.append(mk(
            categoria="leads",
            tipo="Leads",
            titulo="Leads atualizados",
            descricao=f"{total} lead(s) cadastrado(s)"
                      + (f" · {etapa_txt}" if etapa_txt else ""),
            timestamp=ts_source,
            prioridade=prioridade_leads,
        ))

        # Eventos individuais para leads recentes (últimos 7 dias)
        sete_dias = NOW.astimezone(BRT).date() - timedelta(days=7)
        for lead in leads:
            dt_contato = parse_br_date(lead.get("data_contato", ""))
            if not dt_contato:
                continue
            if dt_contato.astimezone(BRT).date() < sete_dias:
                continue
            # Apenas campos estruturados seguros — campos de texto livre
            # (proxima_acao, observacao, etc.) são excluídos deliberadamente
            # para evitar que dados sensíveis futuros vazem para o summary.
            servico = lead.get("servico_interesse", "—")
            origem  = lead.get("origem", "—")
            etapa   = lead.get("etapa", "—")
            resp    = lead.get("responsavel", "—")
            ts_lead = br_date_to_iso(lead.get("data_contato")) or ts_source
            descr   = f"Serviço: {servico} · Origem: {origem} · Etapa: {etapa} · Resp.: {resp}"
            eventos.append(mk(
                categoria="leads",
                tipo="Novo lead",
                titulo="Novo lead registrado",
                descricao=descr,
                timestamp=ts_lead,
                prioridade="alta",
            ))

    # ── 2. CRM Clínicas ───────────────────────────────────────────────────────
    d = safe_read(DATA / "crm-clinicas.local.json")
    if d:
        ts = d.get("source", {}).get("generatedAt") or iso_now()
        clinicas = d.get("clinicas", [])
        total = len(clinicas)
        etapas_crm = Counter(c.get("etapa", "") for c in clinicas if c.get("etapa"))
        alta = sum(1 for c in clinicas if str(c.get("prioridade", "")).lower() == "alta")
        etapa_txt = " · ".join(f"{e}: {n}" for e, n in etapas_crm.most_common(3))
        descr = f"{total} clínica(s) no CRM"
        if alta:
            descr += f" · Alta prioridade: {alta}"
        if etapa_txt:
            descr += f" · {etapa_txt}"
        eventos.append(mk(
            categoria="crm",
            tipo="CRM Clínicas",
            titulo="CRM de clínicas atualizado",
            descricao=descr,
            timestamp=ts,
            prioridade="media",
        ))

    # ── 3. Follow-up Pacientes ────────────────────────────────────────────────
    d = safe_read(DATA / "followup-pacientes-summary.local.json")
    if d:
        ts = d.get("geradoEm") or iso_now()
        espi   = d.get("espirometria", {})
        cons   = d.get("consultas", {})
        total  = espi.get("total", 0) + cons.get("total", 0)
        atras  = espi.get("atrasados", 0) + cons.get("atrasados", 0)
        prox7  = espi.get("proximos7dias", 0) + cons.get("proximos7dias", 0)
        hoje_n = espi.get("hoje", 0) + cons.get("hoje", 0)
        partes = [f"Espirometrias: {espi.get('total',0)}", f"Consultas: {cons.get('total',0)}"]
        if atras:
            partes.append(f"Atrasados: {atras}")
        if prox7:
            partes.append(f"Próx. 7 dias: {prox7}")
        if hoje_n:
            partes.append(f"Hoje: {hoje_n}")
        prioridade_fu = "alta" if atras > 0 else "media"
        eventos.append(mk(
            categoria="followup",
            tipo="Follow-up Pacientes",
            titulo="Follow-up de pacientes atualizado",
            descricao=f"{total} acompanhamento(s) · " + " · ".join(partes),
            timestamp=ts,
            prioridade=prioridade_fu,
        ))

    # ── 4. Follow-up B2B (Clínicas) ───────────────────────────────────────────
    d = safe_read(DATA / "followup-clinicas-summary.local.json")
    if d:
        ts = d.get("geradoEm") or iso_now()
        cl  = d.get("clinicas", {})
        total   = cl.get("total", 0)
        atras   = cl.get("atrasados", 0)
        wa      = cl.get("comWhatsApp", 0)
        prox7   = cl.get("proximos7dias", 0)
        partes  = [f"{total} clínica(s)"]
        if wa:
            partes.append(f"Com WhatsApp: {wa}")
        if atras:
            partes.append(f"Atrasados: {atras}")
        if prox7:
            partes.append(f"Próx. 7 dias: {prox7}")
        prioridade_b2b = "alta" if atras > 0 else "media"
        eventos.append(mk(
            categoria="b2b",
            tipo="Follow-up B2B",
            titulo="Follow-up B2B atualizado",
            descricao=" · ".join(partes),
            timestamp=ts,
            prioridade=prioridade_b2b,
        ))

    # ── 5. Marketing & SEO ────────────────────────────────────────────────────
    d = safe_read(DATA / "marketing-seo.local.json")
    if d:
        ts  = d.get("meta", {}).get("generatedAt") or iso_now()
        sc  = d.get("searchConsole", {})
        ga4 = d.get("ga4", {})
        partes = []
        if sc:
            t = sc.get("totals", {})
            imp  = t.get("impressions", 0)
            clk  = t.get("clicks", 0)
            ctr  = t.get("ctr", 0)
            pos  = t.get("avgPosition")
            s = f"SC: {imp:,} impressões · {clk} cliques · CTR {ctr*100:.1f}%"
            if pos:
                s += f" · Pos. {pos:.1f}"
            partes.append(s)
        if ga4:
            t = ga4.get("totals", {})
            partes.append(f"GA4: {t.get('users',0)} usuários · {t.get('sessions',0)} sessões · {t.get('pageviews',0)} pageviews")
        eventos.append(mk(
            categoria="marketing",
            tipo="Marketing & SEO",
            titulo="Marketing & SEO atualizado",
            descricao=" | ".join(partes) if partes else "Sem dados de marketing disponíveis",
            timestamp=ts,
            prioridade="baixa",
        ))

    # ── 6. Financeiro ─────────────────────────────────────────────────────────
    d = safe_read(DATA / "financeiro-summary.local.json")
    if d:
        ts      = d.get("source", {}).get("generatedAt") or iso_now()
        receita = d.get("receita_exames", 0)
        total_l = d.get("total_lancamentos", 0)
        espi_p  = d.get("espirometrias_pagas", 0)
        saldo   = d.get("saldo_operacional")
        partes  = [f"Receita: R${fmt_brl(receita)}"]
        if espi_p:
            partes.append(f"{espi_p} espirometria(s) pagas")
        if total_l:
            partes.append(f"{total_l} lançamento(s)")
        if saldo is not None:
            partes.append(f"Saldo: R${fmt_brl(saldo)}")
        eventos.append(mk(
            categoria="financeiro",
            tipo="Financeiro",
            titulo="Financeiro atualizado",
            descricao=" · ".join(partes),
            timestamp=ts,
            prioridade="media",
        ))

    # ── 7. Custos & Investimentos ─────────────────────────────────────────────
    d = safe_read(DATA / "custos-investimentos-summary.local.json")
    if d and d.get("source", {}).get("safeToDisplay"):
        ts           = d.get("source", {}).get("generatedAt") or iso_now()
        total_m      = d.get("total_mensal_atual", 0)
        itens        = d.get("itens_ativos", 0)
        pend         = d.get("pendencias_cadastro", 0)
        rec          = d.get("total_mensal_recorrente", 0)
        parc         = d.get("total_mensal_parcelas", 0)
        partes       = [f"{itens} item(s) ativos", f"Total mensal: R${fmt_brl(total_m)}"]
        if rec:
            partes.append(f"Recorrente: R${fmt_brl(rec)}")
        if parc:
            partes.append(f"Parcelas: R${fmt_brl(parc)}")
        if pend:
            partes.append(f"{pend} pendência(s)")
        prioridade_ci = "alta" if pend > 0 else "baixa"
        eventos.append(mk(
            categoria="custos",
            tipo="Custos & Investimentos",
            titulo="Custos & Investimentos atualizados",
            descricao=" · ".join(partes),
            timestamp=ts,
            prioridade=prioridade_ci,
        ))

    # ── 8. Resumo Dashboard (Google Sheets) ───────────────────────────────────
    d = safe_read(DATA / "resumo-dashboard.local.json")
    if d:
        ts    = d.get("source", {}).get("generatedAt") or iso_now()
        cards = d.get("cards", [])
        n     = len(cards)
        total_leads_card = next((c.get("value") for c in cards if c.get("key") == "totalLeads"), None)
        descr = f"{n} indicadores sincronizados do Google Sheets"
        if total_leads_card is not None:
            descr += f" · Total de leads: {total_leads_card}"
        eventos.append(mk(
            categoria="sistema",
            tipo="Dashboard",
            titulo="Resumo do dashboard atualizado",
            descricao=descr,
            timestamp=ts,
            prioridade="baixa",
        ))

    # ── Fallback ──────────────────────────────────────────────────────────────
    if not eventos:
        eventos.append(mk(
            categoria="sistema",
            tipo="Sistema",
            titulo="Painel inicializado",
            descricao="Execute update-local-data.sh para sincronizar os módulos",
            timestamp=iso_now(),
            prioridade="baixa",
            status="info",
        ))

    # Sanitiza (garante ausência de campos PII por construção)
    eventos = [sanitize_event(e) for e in eventos]

    def sort_key(e):
        dt = parse_iso(e.get("timestamp", ""))
        return dt or datetime.min.replace(tzinfo=timezone.utc)

    eventos.sort(key=sort_key, reverse=True)
    return eventos[:20]


def build_payload(eventos):
    hoje      = sum(1 for e in eventos if is_today(e.get("timestamp", "")))
    pendencias = sum(1 for e in eventos if e.get("status") not in ("ok", "info"))
    alta       = sum(1 for e in eventos if e.get("prioridade") == "alta")

    cat_count = Counter(e.get("categoria", "") for e in eventos)

    return {
        "source": {
            "safeToDisplay": True,
            "containsPersonalData": False,
            "containsHealthData": False,
            "generatedAt": iso_now(),
            "generator": "generate-ultimos-lancamentos.py",
        },
        "stats": {
            "totalEventos": len(eventos),
            "hoje": hoje,
            "prioridade_alta": alta,
            "pendencias": pendencias,
            "por_categoria": dict(cat_count),
        },
        "eventos": eventos,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Mostra eventos sem gravar")
    parser.add_argument("--write",   action="store_true", help="Grava os arquivos de saída")
    args = parser.parse_args()

    if not args.dry_run and not args.write:
        parser.print_help()
        sys.exit(1)

    print("Coletando eventos dos arquivos locais...")
    eventos = collect_eventos()
    print(f"  {len(eventos)} evento(s) coletado(s).")

    if args.dry_run:
        icons = {"leads": "👤", "crm": "🏥", "followup": "📋", "b2b": "🤝",
                 "marketing": "📊", "financeiro": "💰", "custos": "💼", "sistema": "⚙"}
        prio_mark = {"alta": "!", "media": "·", "baixa": " "}
        print("\nPré-visualização (dry-run — nenhum arquivo escrito):")
        for e in eventos:
            ico  = icons.get(e.get("categoria", ""), "·")
            pm   = prio_mark.get(e.get("prioridade", "media"), "·")
            print(f"  {pm} {ico} [{e['data_br']}] {e['titulo']}")
            print(f"       {e['descricao']}")
        return

    payload = build_payload(eventos)

    # Validação de segurança antes de gravar
    payload_text = json.dumps(payload, ensure_ascii=False)
    erros = validate_output_safety(payload_text)
    if erros:
        print("ERRO DE SEGURANÇA: payload contém dados proibidos:")
        for err in erros:
            print(f"  - {err}")
        print("Arquivo NÃO gravado.")
        sys.exit(1)

    # 2ª validação: guarda de PII compartilhada (M2) — aborta com exit 1 se
    # encontrar violação; nunca imprime o valor sensível.
    pii_guard.ensure_summary_safe(payload, rules=_PII_RULES, context="ultimos-lancamentos-summary")

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
