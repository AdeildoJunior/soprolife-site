#!/usr/bin/env python3
"""
SoproLife OS Local Core — Conector Financeiro (Google Sheets ADC) — M14.2.

FONTE FINANCEIRA ÚNICA: lê a aba "Financeiro_Lancamentos" — a única fonte
oficial de valores, receitas, pagamentos, descontos e status financeiros do
painel — e gera dois arquivos locais, no mesmo padrão dos demais conectores:

  1. painel-soprolife/data-private/financeiro-lancamentos.local.json  ← completo (gitignored)
     Registros normalizados linha a linha (inclui observacao_financeira,
     texto livre já sanitizado na escrita, mas mantido fora do summary).
     safeToDisplay: false

  2. painel-soprolife/data/financeiro-summary.local.json  ← resumo seguro (gitignored)
     Apenas agregados: totais, contagens, séries e lançamentos com descrição
     TEMPLATE (serviço — local). Nunca observação livre, nome, telefone,
     CPF, e-mail ou dado bancário.
     safeToDisplay: true | containsPersonalData: false

Separação de papéis (ver docs/financeiro-fonte-unica.md):
  - "CRM Espirometria"        → fonte OPERACIONAL (paciente, exame, status).
  - "Financeiro_Lancamentos"  → fonte FINANCEIRA (valores e pagamentos).
  Os dois fluxos compartilham id_atendimento; NENHUM valor monetário do
  painel pode ser derivado do CRM Espirometria.

A antiga aba "Financeiro" foi removida da planilha e NÃO é lida nem
recriada por nenhum fluxo ativo.

Regras defensivas de agregação (nunca confia só no que está gravado):
  - upsert é por id_atendimento → duplicatas são deduplicadas (a última
    linha da planilha vence, espelhando o comportamento do upsert);
  - Pendente/Cortesia/Cancelado nunca contam como receita recebida, mesmo
    que a célula valor_recebido tenha algo;
  - linha sem valor_cobrado válido não entra em nenhuma soma (contada em
    linhas_invalidas);
  - valores desconhecidos ficam null (nunca R$ 0,00 inventado).

Configuração necessária:
    ~/.config/soprolife/painel/google-sheets.local.json
    Campo obrigatório: "spreadsheet_id"
    Campo opcional:    "financeiro_lancamentos_sheet_name"
                       (padrão: "Financeiro_Lancamentos")

Uso:
    python3 painel-soprolife/scripts/read-financeiro-lancamentos-adc.py --show-structure
    python3 painel-soprolife/scripts/read-financeiro-lancamentos-adc.py --dry-run
    python3 painel-soprolife/scripts/read-financeiro-lancamentos-adc.py --write
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

# Guarda de PII compartilhada (M2) — mesma pasta deste script.
import pii_guard

# Regras da guarda para o financeiro-summary: todos os rótulos são gerados
# por template a partir de enums fechados (serviço/local/forma/status) —
# nunca texto digitado. "descricao" é EXCEÇÃO de chave (é template
# "Serviço — Local"), mas o valor continua passando por todos os scans.
_PII_RULES = {
    "campos_pessoa": [],
    "campos_institucionais": ["servico", "local", "forma", "status",
                              "origem", "mes", "nota", "type",
                              "official_source", "generator"],
    "chaves_permitidas_excecao": ["descricao", "nota"],
}

_CONFIG_PATH = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
_OUT_PRIVATE = Path("painel-soprolife/data-private/financeiro-lancamentos.local.json")
_OUT_SUMMARY = Path("painel-soprolife/data/financeiro-summary.local.json")

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

DEFAULT_SHEET_NAME = "Financeiro_Lancamentos"

# Cabeçalho canônico — mesmo shape de _FINANCEIRO_LANCAMENTOS_CABECALHO em
# apps-script/command-center-api.gs (a escrita e a leitura compartilham o
# contrato; mudou lá, muda aqui e em docs/financeiro-fonte-unica.md).
CANONICAL_COLUMNS = [
    "id_lancamento", "id_atendimento", "criado_em", "data_exame", "tipo_movimento",
    "servico", "local_atendimento", "valor_tabela", "valor_cobrado", "valor_recebido",
    "desconto", "status_exame", "status_pagamento", "forma_pagamento", "origem_preco",
    "observacao_financeira", "fonte",
]

# Campos que ficam SÓ no arquivo privado (texto livre, mesmo sanitizado).
PRIVATE_ONLY = {"observacao_financeira"}

# Colunas que NUNCA podem ser exportadas, nem para o privado — se a aba
# financeira ganhar uma coluna dessas um dia, é erro de modelagem (dado de
# pessoa não pertence à planilha financeira; ver _EF_CAMPOS_PROIBIDOS no
# Apps Script, que já rejeita esses campos na escrita).
BLOCKED_FIELDS = {
    "nome", "primeiro_nome", "paciente", "paciente_nome", "responsavel",
    "telefone", "whatsapp", "paciente_whatsapp", "cpf", "rg", "email",
    "e-mail", "endereco", "endereço", "data_nascimento", "observacao",
    "senha", "token", "access_token", "private_key", "client_secret",
    "chave_pix", "numero_cartao", "cvv", "numero_conta", "agencia",
}

# Enums fechados — mesmos de js/espirometria-financeiro.js e do Apps Script.
STATUS_PAGAMENTO = ["Recebido", "Pendente", "Parcial", "Cortesia", "Cancelado"]
STATUS_EXAME = ["Aguardando", "Realizado", "Cancelado", "Remarcado"]

MAX_LANCAMENTOS_AGREGADOS = 10

_CPF_RE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm(s: str) -> str:
    return _strip_accents(str(s or "")).strip().lower()


def _parse_number(raw) -> Optional[float]:
    """Número monetário tolerante a formato BR; None para vazio/lixo/negativo
    (valor financeiro negativo não existe neste contrato — desconto tem campo
    próprio e despesas usarão tipo_movimento próprio no futuro)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        n = float(raw)
        return round(n, 2) if n >= 0 else None
    cleaned = str(raw).strip().replace("R$", "").replace("\xa0", "").replace(" ", "")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        n = float(cleaned)
    except ValueError:
        return None
    return round(n, 2) if n >= 0 else None


def _parse_date(raw) -> Optional[date]:
    raw = str(raw or "").strip()
    if not raw:
        return None
    # ISO com hora (criado_em) ou só data (data_exame), e formatos BR.
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Parsing das linhas da aba (por cabeçalho, nunca por posição fixa)
# ---------------------------------------------------------------------------

def parse_records(rows: list) -> tuple[list[dict], list[str]]:
    """rows crus da API (linha 0 = cabeçalho) → (registros normalizados,
    avisos). Colunas bloqueadas nunca são exportadas; colunas desconhecidas
    são ignoradas com aviso."""
    avisos: list[str] = []
    if not rows:
        return [], avisos

    raw_headers = [str(c).strip() for c in rows[0]]
    headers_norm = [_norm(h).replace(" ", "_") for h in raw_headers]

    col_map: dict[str, int] = {}
    for i, hn in enumerate(headers_norm):
        if hn in BLOCKED_FIELDS:
            avisos.append(f"coluna BLOQUEADA ignorada: {raw_headers[i]!r} (dado de pessoa não pertence à aba financeira)")
            continue
        if hn in CANONICAL_COLUMNS and hn not in col_map:
            col_map[hn] = i
        elif hn not in CANONICAL_COLUMNS:
            avisos.append(f"coluna desconhecida ignorada: {raw_headers[i]!r}")

    faltando = [c for c in ("data_exame", "valor_cobrado", "status_pagamento") if c not in col_map]
    if faltando:
        avisos.append(f"colunas essenciais ausentes: {', '.join(faltando)}")

    registros = []
    for row in rows[1:]:
        if not any(str(c).strip() for c in row):
            continue
        rec = {}
        for campo, idx in col_map.items():
            rec[campo] = str(row[idx]).strip() if idx < len(row) else ""
        registros.append(rec)
    return registros, avisos


# ---------------------------------------------------------------------------
# Agregação — função PURA (testável offline com fixtures sintéticas)
# ---------------------------------------------------------------------------

def build_summary(registros: list[dict], hoje: Optional[date] = None) -> dict:
    """Agrega os registros de Financeiro_Lancamentos no resumo seguro do
    painel. Nunca lê rede/arquivo; nunca inclui observacao_financeira nem
    qualquer texto livre no resultado."""
    hoje = hoje or date.today()
    mes_atual = f"{hoje.year:04d}-{hoje.month:02d}"

    # 1. Dedupe espelhando o upsert: id_atendimento é a chave primária de
    #    negócio; sem ele, id_lancamento; sem ambos, a linha fica como está.
    duplicados = 0
    por_chave: dict[str, dict] = {}
    sem_chave: list[dict] = []
    for rec in registros:
        chave = rec.get("id_atendimento") or rec.get("id_lancamento") or ""
        if chave:
            if chave in por_chave:
                duplicados += 1
            por_chave[chave] = rec  # última linha vence (comportamento do upsert)
        else:
            sem_chave.append(rec)
    base = list(por_chave.values()) + sem_chave

    # 2. Classificação linha a linha com regras defensivas.
    receita_recebida = 0.0
    receita_pendente = 0.0
    descontos = 0.0
    cortesias = 0
    cancelados = 0
    exames_pagos = 0
    invalidas = 0
    inconsistentes = 0
    nao_receita = 0

    por_servico = defaultdict(float)
    por_local = defaultdict(float)
    por_status = defaultdict(lambda: {"quantidade": 0, "valor_recebido": 0.0})
    por_mes = defaultdict(float)
    valores_tabela = Counter()
    linhas_validas: list[dict] = []

    for rec in base:
        tipo = _norm(rec.get("tipo_movimento") or "receita") or "receita"
        if tipo != "receita":
            nao_receita += 1
            continue

        valor_cobrado = _parse_number(rec.get("valor_cobrado"))
        status_pag = str(rec.get("status_pagamento") or "").strip()
        status_exame = str(rec.get("status_exame") or "").strip()
        if valor_cobrado is None or status_pag not in STATUS_PAGAMENTO:
            invalidas += 1
            continue

        valor_tabela = _parse_number(rec.get("valor_tabela"))
        valor_recebido_raw = _parse_number(rec.get("valor_recebido")) or 0.0
        desconto_raw = _parse_number(rec.get("desconto"))
        data_exame = _parse_date(rec.get("data_exame"))
        servico = str(rec.get("servico") or "").strip() or "Espirometria"
        local = str(rec.get("local_atendimento") or "").strip() or "Não informado"

        cancelado = status_exame == "Cancelado" or status_pag == "Cancelado"
        cortesia = status_pag == "Cortesia"
        pendente = status_pag == "Pendente"

        # Receita defensiva: Pendente/Cortesia/Cancelado zeram o recebido
        # aqui de novo, mesmo que a célula traga valor (a escrita já zera,
        # mas o resumo não confia em ninguém).
        if cancelado or cortesia or pendente:
            valor_recebido = 0.0
        else:
            valor_recebido = valor_recebido_raw
            if status_pag == "Recebido" and round(valor_recebido_raw, 2) != round(valor_cobrado, 2):
                inconsistentes += 1
            elif status_pag == "Parcial" and not (0 < valor_recebido_raw < valor_cobrado):
                inconsistentes += 1

        if cancelado:
            cancelados += 1
        else:
            if cortesia:
                cortesias += 1
            if pendente:
                receita_pendente += valor_cobrado
            elif status_pag == "Parcial":
                receita_pendente += max(0.0, valor_cobrado - valor_recebido)

            # Desconto só conta em exame que aconteceu: usa o campo gravado;
            # se ausente, deriva de tabela - cobrado (nunca negativo).
            if desconto_raw is not None:
                descontos += desconto_raw
            elif valor_tabela is not None:
                descontos += max(0.0, valor_tabela - valor_cobrado)

        receita_recebida += valor_recebido
        if valor_recebido > 0:
            exames_pagos += 1
            por_servico[servico] += valor_recebido
            por_local[local] += valor_recebido
            if data_exame:
                por_mes[f"{data_exame.year:04d}-{data_exame.month:02d}"] += valor_recebido

        st = por_status[status_pag]
        st["quantidade"] += 1
        st["valor_recebido"] = round(st["valor_recebido"] + valor_recebido, 2)

        if valor_tabela is not None:
            valores_tabela[valor_tabela] += 1

        linhas_validas.append({
            "data": data_exame,
            "servico": servico,
            "local": local,
            "valor_recebido": round(valor_recebido, 2),
            "valor_cobrado": round(valor_cobrado, 2),
            "status": status_pag,
        })

    lancamentos_validos = len(linhas_validas)
    total_mes_atual = round(por_mes.get(mes_atual, 0.0), 2)
    ticket_medio = round(receita_recebida / exames_pagos, 2) if exames_pagos > 0 else None
    valor_base = valores_tabela.most_common(1)[0][0] if valores_tabela else None

    datas_validas = sorted(d["data"] for d in linhas_validas if d["data"])
    periodo = {
        "de": datas_validas[0].isoformat() if datas_validas else None,
        "ate": datas_validas[-1].isoformat() if datas_validas else None,
        "mes_atual": mes_atual,
    }

    # Últimos lançamentos (descrição = template "Serviço — Local", nunca
    # texto livre). Pendentes aparecem com o valor COBRADO para o operador
    # saber quanto falta entrar — o status ao lado deixa claro que não é
    # receita recebida.
    recentes = sorted(
        linhas_validas,
        key=lambda r: (r["data"] or date.min, r["valor_recebido"]),
        reverse=True,
    )[:MAX_LANCAMENTOS_AGREGADOS]
    lancamentos_agregados = [{
        "descricao": f"{r['servico']} — {r['local']}",
        "servico": r["servico"],
        "local": r["local"],
        "valor": r["valor_recebido"] if r["valor_recebido"] > 0 else r["valor_cobrado"],
        "status": r["status"],
        "data": r["data"].strftime("%d/%m/%Y") if r["data"] else "—",
    } for r in recentes]

    summary = {
        "source": {
            "type": "financeiro_lancamentos_summary",
            "official_source": "Financeiro_Lancamentos",
            "generator": "painel-soprolife/scripts/read-financeiro-lancamentos-adc.py",
            "safeToDisplay": True,
            "containsPersonalData": False,
            "containsHealthData": False,
            "containsBankingData": False,
            "generatedAt": _now_iso(),
            "nota": "Fonte financeira única: aba Financeiro_Lancamentos (1 lançamento por exame, upsert por id_atendimento). Nenhum valor é derivado do CRM Espirometria.",
        },
        "periodo": periodo,
        "totais": {
            "receita_recebida": round(receita_recebida, 2),
            "receita_pendente": round(receita_pendente, 2),
            "descontos_concedidos": round(descontos, 2),
            "cortesias": cortesias,
            "cancelados": cancelados,
            "lancamentos_validos": lancamentos_validos,
            "linhas_invalidas": invalidas,
            "linhas_inconsistentes": inconsistentes,
            "duplicados_ignorados": duplicados,
            "movimentos_nao_receita": nao_receita,
        },
        "exames_pagos": exames_pagos,
        "ticket_medio_real": ticket_medio,
        "valor_base_exame": valor_base,
        "por_servico": [{"servico": s, "valor": round(v, 2)}
                        for s, v in sorted(por_servico.items(), key=lambda kv: -kv[1])],
        "por_local": [{"local": l, "valor": round(v, 2)}
                      for l, v in sorted(por_local.items(), key=lambda kv: -kv[1])],
        "por_status": [{"status": s, **vals} for s, vals in sorted(por_status.items())],
        "por_mes": [{"mes": m, "valor": round(v, 2)} for m, v in sorted(por_mes.items())],
        "lancamentos_agregados": lancamentos_agregados,

        # Chaves de compatibilidade — consumidas por app.js (Painel Geral),
        # operational-brain.js, generate-ultimos-lancamentos.py e
        # check-access.sh. Mesmos nomes do resumo anterior; valores agora
        # derivados EXCLUSIVAMENTE de Financeiro_Lancamentos.
        "receita_exames": round(receita_recebida, 2),
        "espirometrias_pagas": exames_pagos,
        "total_lancamentos": lancamentos_validos,
        "total_entradas_mes_atual": total_mes_atual,
        # Saldo bancário não é derivável da fonte oficial — null (UI mostra
        # "—"/omite o card; nunca inventar R$ 0,00).
        "saldo_operacional": None,
    }
    return summary


def build_private(registros: list[dict], avisos: list[str]) -> dict:
    return {
        "source": {
            "type": "financeiro_lancamentos_private",
            "official_source": "Financeiro_Lancamentos",
            "generator": "painel-soprolife/scripts/read-financeiro-lancamentos-adc.py",
            "safeToDisplay": False,
            "containsPersonalData": False,
            "generatedAt": _now_iso(),
            "nota": "Registros completos da aba financeira (inclui observacao_financeira, texto livre sanitizado). Uso local/auditoria — nunca servido ao painel.",
        },
        "avisos": avisos,
        "registros": registros,
    }


# ---------------------------------------------------------------------------
# Validação de segurança do summary (redundância local + pii_guard)
# ---------------------------------------------------------------------------

def validate_summary(summary: dict) -> list[str]:
    problemas: list[str] = []
    texto = json.dumps(summary, ensure_ascii=False)
    if "observacao_financeira" in texto:
        problemas.append("observacao_financeira vazou para o summary.")
    if _CPF_RE.search(texto):
        problemas.append("padrão de CPF no summary.")
    if _FONE_RE.search(texto):
        problemas.append("padrão de telefone no summary.")
    src = summary.get("source", {})
    if src.get("safeToDisplay") is not True or src.get("containsPersonalData") is not False:
        problemas.append("flags de segurança do source incorretas.")
    problemas.extend(pii_guard.validate_summary(summary, rules=_PII_RULES,
                                                context="financeiro-summary"))
    return problemas


# ---------------------------------------------------------------------------
# Conexão Google (idêntica ao padrão dos demais read-*-adc.py)
# ---------------------------------------------------------------------------

def _load_google_libs():
    try:
        from googleapiclient.discovery import build
        from google.auth import default as google_auth_default
        return build, google_auth_default
    except ImportError as exc:
        print(f"ERRO: dependências não instaladas — {exc}")
        print()
        print("Instale com:")
        print("  pip install -r painel-soprolife/requirements-google.txt")
        sys.exit(1)


def _load_config() -> tuple[str, str]:
    if not _CONFIG_PATH.exists():
        print("ERRO: configuração não encontrada.")
        print(f"  Esperado em: {_CONFIG_PATH}")
        print("  Campo obrigatório: spreadsheet_id")
        sys.exit(1)
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERRO: configuração JSON inválida — {exc}")
        sys.exit(1)
    if not isinstance(cfg, dict):
        print("ERRO: configuração deve ser um objeto JSON.")
        sys.exit(1)
    sid = cfg.get("spreadsheet_id", "").strip()
    if not sid:
        print("ERRO: spreadsheet_id ausente ou vazio na configuração.")
        sys.exit(1)
    sheet = cfg.get("financeiro_lancamentos_sheet_name", DEFAULT_SHEET_NAME).strip() or DEFAULT_SHEET_NAME
    return sid, sheet


def _get_credentials(google_auth_default):
    try:
        credentials, _ = google_auth_default(scopes=[SHEETS_SCOPE])
        return credentials
    except Exception as exc:
        print(f"ERRO: falha na autenticação ADC — {exc}")
        print()
        print("Execute para autenticar:")
        print(f"  gcloud auth application-default login --scopes={SHEETS_SCOPE}")
        sys.exit(1)


def _fetch_rows(build, spreadsheet_id: str, sheet_name: str, credentials) -> list:
    print(f"Lendo aba: {sheet_name}")
    try:
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A:Z")
            .execute()
        )
    except Exception as exc:
        msg = str(exc)
        if "403" in msg or "PERMISSION_DENIED" in msg:
            print(f"ERRO: acesso negado à aba '{sheet_name}'.")
            sys.exit(1)
        elif "404" in msg or "NOT_FOUND" in msg:
            print(f"AVISO: aba '{sheet_name}' não encontrada — tratando como vazia.")
            print("  (a aba é criada pelo Apps Script na primeira gravação da Nova Espirometria)")
            return []
        elif "401" in msg or "UNAUTHENTICATED" in msg:
            print("ERRO: credenciais inválidas ou expiradas.")
            print("  Execute: gcloud auth application-default login")
            sys.exit(1)
        else:
            print(f"ERRO: falha ao ler a aba '{sheet_name}' — {exc}")
            sys.exit(1)
    rows = result.get("values", [])
    print(f"  rows_read: {len(rows)}")
    return rows


def _show_structure(rows: list, sheet_name: str) -> None:
    print(f"\nEstrutura da aba: {sheet_name}")
    print(f"  rows_total: {len(rows)}")
    if not rows:
        print("  (aba vazia ou inexistente)")
        return
    headers = [str(c).strip() for c in rows[0]]
    print(f"  columns_count: {len(headers)}")
    print(f"  headers: {' | '.join(headers)}")
    print()
    for h in headers:
        hl = _norm(h).replace(" ", "_")
        if hl in BLOCKED_FIELDS:
            print(f"  BLOQUEADA (nunca exportada):  {h}")
        elif hl in PRIVATE_ONLY:
            print(f"  PRIVADA (só em data-private): {h}")
        elif hl in CANONICAL_COLUMNS:
            print(f"  OK (entra na agregação):      {h}")
        else:
            print(f"  IGNORADA (não reconhecida):   {h}")
    print()


def main() -> int:
    # M23 — guarda de fonte canônica. O painel opera em modo
    # postgresql_only: nenhum leitor de Google Sheets pode ser executado
    # pelo pipeline automático nem pelo timer de produção. Só uma decisão
    # humana explícita (SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1) libera
    # este utilitário, e apenas para migração/forense pontual.
    import sys as _sys, pathlib as _pathlib
    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    import data_source_mode
    data_source_mode.block_legacy_sheets('read-financeiro-lancamentos-adc.py')

    parser = argparse.ArgumentParser(
        description="Conector Financeiro_Lancamentos (fonte financeira única) — SoproLife M14.2")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show-structure", action="store_true",
                       help="Mostra cabeçalho/classificação das colunas e sai")
    group.add_argument("--dry-run", action="store_true",
                       help="Lê e agrega sem gravar (padrão)")
    group.add_argument("--write", action="store_true",
                       help="Grava o privado e o summary seguro")
    args = parser.parse_args()
    mode = "write" if args.write else ("show-structure" if args.show_structure else "dry-run")

    print("SoproLife — Financeiro_Lancamentos (fonte financeira única, M14.2)")
    print(f"mode: {mode}")
    print()

    build, google_auth_default = _load_google_libs()
    spreadsheet_id, sheet_name = _load_config()
    credentials = _get_credentials(google_auth_default)
    rows = _fetch_rows(build, spreadsheet_id, sheet_name, credentials)

    if mode == "show-structure":
        _show_structure(rows, sheet_name)
        return 0

    registros, avisos = parse_records(rows)
    for a in avisos:
        print(f"AVISO: {a}")

    summary = build_summary(registros)
    problemas = validate_summary(summary)
    if problemas:
        print("ERRO: summary reprovado na validação de segurança:")
        for p in problemas:
            print(f"  - {p}")
        return 1

    t = summary["totais"]
    print()
    print(f"registros lidos:       {len(registros)}")
    print(f"lançamentos válidos:   {t['lancamentos_validos']}")
    print(f"receita recebida:      R$ {t['receita_recebida']:.2f}")
    print(f"receita pendente:      R$ {t['receita_pendente']:.2f}")
    print(f"descontos concedidos:  R$ {t['descontos_concedidos']:.2f}")
    print(f"exames pagos:          {summary['exames_pagos']}")
    print(f"duplicados ignorados:  {t['duplicados_ignorados']}")
    print(f"linhas inválidas:      {t['linhas_invalidas']}")

    if mode == "dry-run":
        print()
        print("next_step: use --write para gravar o privado e o summary.")
        return 0

    _OUT_PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PRIVATE.write_text(
        json.dumps(build_private(registros, avisos), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    _OUT_PRIVATE.chmod(0o600)

    _OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    _OUT_SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _OUT_SUMMARY.chmod(0o644)  # servido ao navegador; sem PII por construção

    print()
    print(f"Gravado (privado, 600): {_OUT_PRIVATE}")
    print(f"Gravado (summary, 644): {_OUT_SUMMARY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
