#!/usr/bin/env python3
"""
SoproLife OS Local Core — Gerador de Saúde Operacional (M3 v2).

Produz painel-soprolife/data/saude-operacional-summary.local.json
(gitignored) com o retrato REAL da saúde do pipeline, usando SOMENTE
metadados de arquivos seguros já existentes em painel-soprolife/data/:
existência, validade do JSON, flags safeToDisplay/containsPersonalData,
timestamps (mtime + generatedAt) e contagens agregadas.

O que este script NUNCA faz:
  - não abre data-private/, ~/.config, ADC ou qualquer credencial;
  - não acessa rede, VPS, systemctl ou journal;
  - não copia conteúdo dos summaries — só metadados e contagens;
  - não imprime dado sensível (nem nomes de campos suspeitos).

Regras principais:
  - fonte com containsPersonalData=true  → alerta CRÍTICO;
  - fonte com JSON inválido              → alerta CRÍTICO;
  - pipeline sem atualização > 24h       → CRÍTICO (dado velho);
  - pipeline sem atualização > 30 min    → ATENÇÃO;
  - check-access: só via --check-access-exit N (senão "desconhecido" —
    este script não inventa resultado de segurança);
  - fonte esperada ausente               → indicador degrada, nunca quebra.

Uso:
    python3 painel-soprolife/scripts/generate-saude-operacional.py            # dry-run
    python3 painel-soprolife/scripts/generate-saude-operacional.py --write
    python3 painel-soprolife/scripts/generate-saude-operacional.py --write --check-access-exit 0
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Guarda de PII compartilhada (M2) — mesma pasta deste script.
import pii_guard

# Defaults de produção — inalterados. --data-dir/--out existem para TESTES
# (fixtures sintéticas em diretório temporário), nunca para mudar produção.
DEFAULT_DATA = Path("painel-soprolife/data")
DEFAULT_OUT = DEFAULT_DATA / "saude-operacional-summary.local.json"

# Limiares de frescor (minutos) — constantes nomeadas, fáceis de ajustar.
FRESCO_MIN = 30          # até aqui: ok
VELHO_CRITICO_MIN = 24 * 60  # além daqui: crítico (painel com dado velho)

# Fontes seguras que o gerador PODE ler (allowlist fechada — nada além).
# has_flags: o arquivo carrega source/meta com safeToDisplay etc.
FONTES = {
    "runtime-status":      {"arquivo": "runtime-status.local.json",             "has_flags": False},
    "resumo-dashboard":    {"arquivo": "resumo-dashboard.local.json",           "has_flags": False},
    "leads":               {"arquivo": "leads-summary.local.json",              "has_flags": True},
    "followup-pacientes":  {"arquivo": "followup-pacientes-summary.local.json", "has_flags": True},
    "followup-clinicas":   {"arquivo": "followup-clinicas-summary.local.json",  "has_flags": True},
    "financeiro":          {"arquivo": "financeiro-summary.local.json",         "has_flags": True},
    "custos":              {"arquivo": "custos-investimentos-summary.local.json", "has_flags": True},
    "contatos-b2b":        {"arquivo": "crm-contatos-b2b-summary.local.json",   "has_flags": True},
    "marketing-seo":       {"arquivo": "marketing-seo.local.json",              "has_flags": True},
    "auditoria":           {"arquivo": "auditoria-summary.local.json",          "has_flags": True},
    "ultimos-lancamentos": {"arquivo": "ultimos-lancamentos-summary.local.json", "has_flags": True},
    "pastore":             {"arquivo": "parcerias-pastore-summary.local.json",  "has_flags": True},
}

# Fontes cujo frescor define o "pulso" do pipeline (as regeneradas a cada ciclo).
FONTES_PULSO = ["leads", "followup-pacientes", "followup-clinicas",
                "ultimos-lancamentos", "auditoria", "resumo-dashboard"]

# Regras da guarda de PII para o payload gerado: textos são templates
# gerados AQUI (nunca vindos de dados) — isentos só do detector de nome
# ("Google Analytics" tem 2 palavras capitalizadas); scans continuam.
_PII_RULES = {
    "campos_pessoa": [],
    "campos_institucionais": ["label", "detalhe", "tip", "titulo", "mensagem",
                              "proximo_passo", "nota", "type"],
    "chaves_permitidas_excecao": ["nota"],
}


def _agora():
    return datetime.now().astimezone()


def _idade_min(path: Path):
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        return (_agora() - mtime).total_seconds() / 60.0, mtime
    except OSError:
        return None, None


def _flags_ok(data: dict):
    """Retorna (ok, motivo). Aceita wrapper 'source' ou 'meta'."""
    src = data.get("source") or data.get("meta") or {}
    if not isinstance(src, dict):
        return True, ""  # arquivos sem wrapper (runtime/resumo) — sem flags
    if "containsPersonalData" in src and src.get("containsPersonalData") is not False:
        return False, "containsPersonalData nao e false"
    if "safeToDisplay" in src and src.get("safeToDisplay") is not True:
        return False, "safeToDisplay nao e true"
    return True, ""


def coletar(data_dir: Path = DEFAULT_DATA):
    """Lê metadados das fontes. Nunca falha destrutivamente."""
    estado = {}
    for nome, cfg in FONTES.items():
        path = Path(data_dir) / cfg["arquivo"]
        info = {"existe": path.exists(), "valido": None, "flags_ok": None,
                "idade_min": None, "mtime": None, "data": None}
        if info["existe"]:
            info["idade_min"], info["mtime"] = _idade_min(path)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                info["valido"] = True
                ok, motivo = _flags_ok(data if isinstance(data, dict) else {})
                info["flags_ok"] = ok
                info["flags_motivo"] = motivo
                info["data"] = data if isinstance(data, dict) else {}
            except Exception:
                info["valido"] = False
                info["flags_ok"] = None
        estado[nome] = info
    return estado


def construir(estado, check_access_exit):
    indicadores = []
    alertas = []

    def ind(id_, label, status, detalhe, tip):
        indicadores.append({"id": id_, "label": label, "status": status,
                            "detalhe": detalhe, "tip": tip})

    def alerta(id_, nivel, titulo, mensagem, proximo_passo):
        alertas.append({"id": id_, "nivel": nivel, "titulo": titulo,
                        "mensagem": mensagem, "proximo_passo": proximo_passo})

    # ── Integridade transversal: JSON inválido ou flag de PII → CRÍTICO ─────
    invalidos = [n for n, i in estado.items() if i["existe"] and i["valido"] is False]
    pii_flag = [n for n, i in estado.items() if i["valido"] and i["flags_ok"] is False]
    for n in invalidos:
        alerta(f"ALERTA-JSON-{n}", "critico", f"Arquivo de dados corrompido: {n}",
               "Um arquivo que o painel lê está com JSON inválido — a seção correspondente pode ficar vazia ou desatualizada.",
               "Rodar a atualização de dados de novo; se persistir, verificar o gerador dessa fonte.")
    for n in pii_flag:
        alerta(f"ALERTA-PII-{n}", "critico", f"Arquivo sem marcação de segurança: {n}",
               "Um arquivo público está sem as flags de segurança esperadas (safeToDisplay/containsPersonalData).",
               "NÃO usar o painel para dados sensíveis até revisar; rodar check-access.sh e corrigir a fonte.")

    # ── 1. Pipeline/update de dados (frescor do pulso) ──────────────────────
    idades = [(n, estado[n]["idade_min"]) for n in FONTES_PULSO
              if estado[n]["existe"] and estado[n]["idade_min"] is not None]
    mais_recente = min((i for _, i in idades), default=None)
    ultima = max((estado[n]["mtime"] for n, _ in idades if estado[n]["mtime"]), default=None)
    if mais_recente is None:
        ind("pipeline_update", "Pipeline de dados", "desconhecido",
            "Nenhuma fonte do ciclo encontrada",
            "Frescor dos arquivos que o update automático regenera a cada ciclo.")
    elif mais_recente <= FRESCO_MIN:
        ind("pipeline_update", "Pipeline de dados", "ok",
            f"Atualizado há {int(mais_recente)} min",
            "Frescor dos arquivos que o update automático regenera a cada ciclo.")
    elif mais_recente <= VELHO_CRITICO_MIN:
        ind("pipeline_update", "Pipeline de dados", "atencao",
            f"Última atualização há {int(mais_recente)} min (esperado: até {FRESCO_MIN} min)",
            "Frescor dos arquivos que o update automático regenera a cada ciclo.")
        alerta("ALERTA-PIPELINE-ATRASO", "atencao", "Dados do painel sem atualização recente",
               f"O ciclo de atualização não roda há {int(mais_recente)} minutos.",
               "Se o painel estiver na VPS, verificar o timer; localmente, rodar update-local-data.sh.")
    else:
        horas = int(mais_recente / 60)
        ind("pipeline_update", "Pipeline de dados", "critico",
            f"Sem atualização há {horas}h",
            "Frescor dos arquivos que o update automático regenera a cada ciclo.")
        alerta("ALERTA-PIPELINE-PARADO", "critico", "Painel com dados velhos",
               f"Nenhuma atualização automática há {horas} horas — o que o painel mostra pode não refletir a operação.",
               "Verificar o timer de atualização na VPS (journal) antes de confiar nos números.")

    # ── 2. Arquivos locais do painel ─────────────────────────────────────────
    total = len(FONTES)
    presentes = sum(1 for i in estado.values() if i["existe"])
    ausentes = [n for n, i in estado.items() if not i["existe"]]
    if invalidos or pii_flag:
        st, det = "critico", f"{len(invalidos)+len(pii_flag)} arquivo(s) com problema"
    elif ausentes:
        st, det = "atencao", f"{presentes}/{total} presentes (faltam: {', '.join(ausentes[:3])}{'…' if len(ausentes) > 3 else ''})"
        alerta("ALERTA-FONTES-AUSENTES", "atencao", "Fontes de dados ausentes",
               f"{len(ausentes)} arquivo(s) esperado(s) ainda não existem neste ambiente.",
               "Rodar update-local-data.sh; se a fonte for nova, é normal até o primeiro ciclo.")
    else:
        st, det = "ok", f"{presentes}/{total} presentes e válidos"
    ind("arquivos_locais", "Arquivos do painel", st, det,
        "Os arquivos locais seguros que alimentam cada seção do painel.")

    # ── 3. Check de segurança (só se informado — nunca inventado) ───────────
    if check_access_exit is None:
        ind("check_access", "Check de segurança", "desconhecido",
            "Não executado nesta geração",
            "Auditoria que confere se nenhum dado sensível vazou para arquivos públicos.")
    elif check_access_exit == 0:
        ind("check_access", "Check de segurança", "ok",
            "check-access.sh passou (exit 0)",
            "Auditoria que confere se nenhum dado sensível vazou para arquivos públicos.")
    else:
        ind("check_access", "Check de segurança", "critico",
            f"check-access.sh FALHOU (exit {check_access_exit})",
            "Auditoria que confere se nenhum dado sensível vazou para arquivos públicos.")
        alerta("ALERTA-CHECK-ACCESS", "critico", "Check de segurança falhou",
               "A auditoria automática encontrou um problema nos arquivos públicos do painel.",
               "Ver a saída do check-access.sh e corrigir ANTES de qualquer commit/deploy.")

    # ── 4. Fonte operacional canônica (M23) ──────────────────────────────────
    # Antes este indicador media a conexão com o Google Sheets. Desde o M23 a
    # planilha não é fonte, não é dependência e não pode aparecer como saúde
    # de produção: o que importa é se os snapshots do PostgreSQL chegaram.
    rt = estado["runtime-status"]
    canonica = None
    sheets_decom = None
    if rt["valido"]:
        data_rt = rt["data"] or {}
        src = data_rt.get("dataSource", {})
        if isinstance(src, dict):
            canonica = src.get("canonical")
        gs = data_rt.get("googleSheets", {})
        if isinstance(gs, dict):
            sheets_decom = gs.get("decommissioned")

    derivados = ["leads", "resumo-dashboard", "followup-pacientes"]
    derivados_ok = all(estado[n]["existe"] and estado[n]["valido"] for n in derivados)
    tip_fonte = ("PostgreSQL do Núcleo M15 — fonte única de leads, CRM, "
                 "financeiro e follow-ups.")

    if canonica == "postgresql" and derivados_ok:
        ind("fonte_operacional", "Fonte operacional", "ok",
            "PostgreSQL gerando os snapshots do painel", tip_fonte)
    elif canonica == "postgresql":
        ind("fonte_operacional", "Fonte operacional", "critico",
            "PostgreSQL declarado, mas faltam snapshots derivados", tip_fonte)
        alerta("ALERTA-FONTES-POSTGRES", "critico",
               "Snapshots do banco não chegaram",
               "A fonte canônica é o PostgreSQL, mas os arquivos derivados estão "
               "ausentes ou inválidos. O painel NÃO substitui isso por exemplos.",
               "Rodar a atualização local ou conferir o timer soprolife-update-data.")
    else:
        ind("fonte_operacional", "Fonte operacional", "desconhecido",
            "Fonte canônica não declarada", tip_fonte)

    # Declaração explícita: a planilha legada não é mais dependência.
    if sheets_decom is True:
        ind("google_sheets_legado", "Google Sheets (legado)", "ok",
            "Descomissionado — não é dependência de produção",
            "Integração desativada no M23. Autenticação de planilha não afeta "
            "a saúde do painel.")
    elif sheets_decom is not None:
        ind("google_sheets_legado", "Google Sheets (legado)", "atencao",
            "Ainda declarado como dependência",
            "Integração deveria estar descomissionada desde o M23.")

    # ── 5/6. Search Console e GA4 (flags do marketing-seo) ──────────────────
    mkt = estado["marketing-seo"]
    fontes_mkt = {}
    if mkt["valido"]:
        fontes_mkt = ((mkt["data"] or {}).get("meta", {}) or {}).get("sources", {}) or {}
    for key, id_, label in (("searchConsole", "search_console", "Search Console"),
                            ("ga4", "ga4", "Google Analytics 4")):
        val = fontes_mkt.get(key)
        if val is True:
            ind(id_, label, "ok", "Métricas agregadas recebidas",
                f"Integração {label} — só métricas agregadas, nunca dado individual.")
        elif val is False:
            ind(id_, label, "atencao", "Sem dados na última atualização",
                f"Integração {label} — só métricas agregadas, nunca dado individual.")
        else:
            ind(id_, label, "desconhecido", "Status não disponível",
                f"Integração {label} — só métricas agregadas, nunca dado individual.")

    # ── 7. Auditoria (M1) ────────────────────────────────────────────────────
    aud = estado["auditoria"]
    if aud["valido"]:
        stats = (aud["data"] or {}).get("stats", {}) or {}
        erros = stats.get("erros", 0)
        eventos = stats.get("total_eventos", 0)
        if erros == 0:
            ind("auditoria", "Trilha de auditoria", "ok",
                f"{eventos} escrita(s) registrada(s), sem erros",
                "Registro de quem alterou o quê pelo painel (M1).")
        else:
            ind("auditoria", "Trilha de auditoria", "atencao",
                f"{erros} erro(s) de escrita registrado(s)",
                "Registro de quem alterou o quê pelo painel (M1).")
            alerta("ALERTA-AUDITORIA-ERROS", "atencao", "Erros de escrita na auditoria",
                   f"A trilha de auditoria registrou {erros} tentativa(s) de escrita com erro.",
                   "Ver o card Últimas alterações e a aba Log Auditoria da planilha.")
    else:
        ind("auditoria", "Trilha de auditoria", "desconhecido", "Resumo não disponível",
            "Registro de quem alterou o quê pelo painel (M1).")

    # ── 8. Últimos lançamentos ───────────────────────────────────────────────
    ul = estado["ultimos-lancamentos"]
    if ul["valido"] and ul["idade_min"] is not None:
        st = "ok" if ul["idade_min"] <= VELHO_CRITICO_MIN else "atencao"
        ind("ultimos_lancamentos", "Últimos lançamentos", st,
            f"Timeline gerada há {int(ul['idade_min'])} min",
            "Feed de eventos operacionais do painel.")
    else:
        ind("ultimos_lancamentos", "Últimos lançamentos", "desconhecido",
            "Timeline não disponível", "Feed de eventos operacionais do painel.")

    # ── 9. Pós-I1 (sem systemctl: docs + pipeline vivo = evidência) ─────────
    docs_i1 = list(Path("painel-soprolife/docs").glob("i1-*.md"))
    pipeline_ok = mais_recente is not None and mais_recente <= VELHO_CRITICO_MIN
    if docs_i1 and pipeline_ok:
        ind("pos_i1", "Pós-migração I1", "ok",
            "Pipeline gerando dados após a migração para usuário sem root",
            "A migração do update de root para soprolife (I1); ok = dados continuam sendo gerados.")
    elif docs_i1:
        ind("pos_i1", "Pós-migração I1", "atencao",
            "Migração documentada, mas pipeline sem dados recentes",
            "A migração do update de root para soprolife (I1); ok = dados continuam sendo gerados.")
    else:
        ind("pos_i1", "Pós-migração I1", "desconhecido", "Sem evidência local",
            "A migração do update de root para soprolife (I1).")

    # ── 10. Painel HTTP (sem teste de rede — honesto) ────────────────────────
    ind("painel_http", "Painel no ar", "desconhecido",
        "Sem teste de rede nesta geração",
        "Este gerador não faz chamadas de rede; confirmar pelo próprio acesso ao painel.")

    # ── status geral: pior nível entre CRÍTICO/ATENÇÃO; desconhecido não
    #    rebaixa (é ausência de informação, visível nos próprios cards) ──────
    niveis = [i["status"] for i in indicadores]
    if "critico" in niveis or any(a["nivel"] == "critico" for a in alertas):
        geral = "critico"
    elif "atencao" in niveis or alertas:
        geral = "atencao"
    else:
        geral = "ok"

    return {
        "source": {
            "type": "saude_operacional_real",
            "safeToDisplay": True,
            "containsPersonalData": False,
            "containsHealthData": False,
            "dadosReais": True,
            "generatedAt": _agora().isoformat(timespec="seconds"),
            "nota": "Gerado por generate-saude-operacional.py a partir de metadados dos arquivos seguros do painel (existência, validade, flags e timestamps — nunca conteúdo).",
        },
        "status_geral": geral,
        "ultima_atualizacao": ultima.isoformat(timespec="seconds") if ultima else _agora().isoformat(timespec="seconds"),
        "indicadores": indicadores,
        "alertas": alertas,
    }


def validar(payload):
    """Allowlist estrita (mesma do check-access) + guarda de PII."""
    ALLOWED_IND = {"id", "label", "status", "detalhe", "tip"}
    ALLOWED_AL = {"id", "nivel", "titulo", "mensagem", "proximo_passo"}
    for i in payload["indicadores"]:
        assert set(i) <= ALLOWED_IND, f"indicador com campo fora da allowlist: {set(i) - ALLOWED_IND}"
    for a in payload["alertas"]:
        assert set(a) <= ALLOWED_AL, f"alerta com campo fora da allowlist: {set(a) - ALLOWED_AL}"
    pii_guard.ensure_summary_safe(payload, rules=_PII_RULES, context="saude-operacional")


def main() -> int:
    parser = argparse.ArgumentParser(description="Gerador de Saúde Operacional (M3 v2) — SoproLife")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Mostra o resultado sem gravar (padrão)")
    group.add_argument("--write", action="store_true", help="Grava data/saude-operacional-summary.local.json")
    parser.add_argument("--check-access-exit", type=int, default=None,
                        help="Exit code de uma execução REAL do check-access.sh (opcional; sem ele o indicador fica 'desconhecido')")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA,
                        help="Diretório das fontes (SÓ para testes com fixtures sintéticas; padrão: produção)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Arquivo de saída (SÓ para testes; padrão: produção)")
    args = parser.parse_args()
    mode = "write" if args.write else "dry-run"
    out_path = args.out if args.out is not None else DEFAULT_OUT

    print("SoproLife — Saúde Operacional (M3 v2)")
    print(f"mode: {mode}")
    print()

    estado = coletar(args.data_dir)
    payload = construir(estado, args.check_access_exit)

    print("Validando payload (allowlist + pii_guard)...")
    validar(payload)
    print("Validação OK.")
    print()
    print(f"status_geral: {payload['status_geral']}")
    print(f"indicadores:  {len(payload['indicadores'])}")
    for i in payload["indicadores"]:
        print(f"  [{i['status']:<12}] {i['label']}: {i['detalhe']}")
    print(f"alertas:      {len(payload['alertas'])}")
    for a in payload["alertas"]:
        print(f"  [{a['nivel']:<8}] {a['titulo']}")

    if mode == "dry-run":
        print()
        print("next_step: use --write para gravar o resumo local.")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_path.chmod(0o644)  # servido ao navegador; sem PII por construção
    print()
    print(f"Gravado: {out_path}  (chmod 644)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
