#!/usr/bin/env python3
"""
SoproLife OS Local Core — pii_guard: guarda de PII para summaries seguros (M2).

Módulo ÚNICO de validação chamado pelos geradores ANTES de gravar qualquer
summary em painel-soprolife/data/*.local.json. Falhou = não grava.

Princípios (mesmos da Auditoria M1 / skill soprolife-audit-patterns):
  - Default-fechado: o que não é reconhecidamente seguro, bloqueia.
  - A mensagem de erro NUNCA imprime o valor sensível — só o caminho do campo
    e o tipo de violação.
  - Falso positivo em data/moeda/percentual/ID é bug DESTA guarda (ver
    _SAFE_VALUE_RES): datas dd/MM/yyyy [HH:mm[:ss]], ISO, moeda e IDs
    técnicos passam por construção.

O que BLOQUEIA:
  - telefone/WhatsApp, CPF, RG claramente identificável, e-mail;
  - "bearer", chaves/token-like, script.google, docs.google, /spreadsheets/d/,
    IDs longos de planilha/credencial;
  - nome de chave proibida (telefone, cpf, observacao, laudo, token, ...);
  - termos clínicos livres em valores ("laudo", "pedido médico", ...);
  - nome completo (2+ palavras) em campo declarado como campo de pessoa;
  - possível nome de pessoa (2+ palavras Capitalizadas) em campo string NÃO
    declarado institucional — campos institucionais (nome de clínica/empresa,
    equipe) devem ser declarados em rules["campos_institucionais"].

API:
    violations = validate_summary(payload, rules=None, context="...")
    ensure_summary_safe(payload, rules=None, context="...")  # imprime e sai(1)

    rules = {
        "campos_pessoa":         ["primeiro_nome", ...],  # nome de pessoa: 2+ palavras = erro
        "campos_institucionais": ["nome_clinica", "operador", ...],  # isentos do detector de nome
        "chaves_proibidas_extras": ["valor_anterior", ...],
        # Exceções à lista de chaves proibidas: campos CURTOS e estruturados
        # cujo nome colide com um token proibido (ex.: obs_curta, descricao,
        # nota). A chave deixa de bloquear, mas o VALOR continua passando por
        # todos os scans de conteúdo (telefone/CPF/nome/segredos).
        "chaves_permitidas_excecao": ["obs_curta", ...],
    }

Arquivos mantidos à mão (sem gerador): validar com
    python3 painel-soprolife/scripts/pii_guard.py --check-file <path> --ruleset <nome>
Rulesets registrados em _FILE_RULESETS (financeiro-summary, custos-investimentos-summary).

Self-test offline:
    python3 painel-soprolife/scripts/pii_guard.py --self-test
"""

import json
import re
import sys
import unicodedata

# ---------------------------------------------------------------------------
# Valores reconhecidamente seguros (full-match, testados ANTES dos scans).
# ---------------------------------------------------------------------------

_SAFE_VALUE_RES = [
    re.compile(r"^\d{2}/\d{2}/\d{4}( \d{2}:\d{2}(:\d{2})?)?$"),          # dd/MM/yyyy [HH:mm[:ss]]
    re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?([.,]\d+)?(Z|[+-]\d{2}:?\d{2})?)?$"),  # ISO
    re.compile(r"^\d{2}:\d{2}(:\d{2})?$"),                               # hora
    re.compile(r"^-?R?\$?\s?\d{1,3}(\.\d{3})*(,\d{1,2})?$"),             # moeda BR
    re.compile(r"^-?\d+([.,]\d+)?\s?%?$"),                               # número/percentual
    re.compile(r"^[A-Za-z]+-[A-Za-z0-9-]+$"),                            # ID técnico: LEAD-20260703-001, AUD-0001, linha-4
    re.compile(r"^[a-z0-9_]+$"),                                         # slug: update_lead_stage
    re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),  # UUID
]

# ---------------------------------------------------------------------------
# Scans de conteúdo (aplicados a valores string NÃO reconhecidos como seguros).
# Cada item: (nome da violação, regex).
# ---------------------------------------------------------------------------

_CONTENT_SCANS = [
    ("padrao de telefone",      re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")),
    ("padrao de telefone",      re.compile(r"\+?55\s?\d{2}\s?\d{4,5}[- ]?\d{4}")),
    ("padrao de CPF",           re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")),
    ("padrao de RG",            re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-?[\dXx]?\b")),
    ("padrao de e-mail",        re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9-.]+")),
    ("token bearer",            re.compile(r"(?i)\bbearer\s")),
    ("URL Apps Script",         re.compile(r"(?i)script\.google")),
    ("URL de planilha",         re.compile(r"(?i)docs\.google\.com|/spreadsheets/d/")),
    ("chave de API Google",     re.compile(r"AIza[0-9A-Za-z_-]{10,}|ya29\.")),
    # Exige ao menos um dígito: slugs longos de URL do site público
    # ("consulta-pneumologista-rio-de-janeiro") não são token; IDs de
    # planilha/credencial contêm dígitos.
    ("possivel token/ID longo", re.compile(r"(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{35,}")),
    ("termo clinico livre",     re.compile(r"(?i)\blaudo\b|pedido m[eé]dico|diagn[oó]stico|resultado de exame")),
    ("referencia a chave pix",  re.compile(r"(?i)chave\s*pix")),
]

# Detector de possível nome de pessoa: 2+ palavras Capitalizadas consecutivas
# (cada uma com 3+ letras). Só se aplica fora de campos institucionais.
_NAME_RE = re.compile(r"\b[A-ZÀ-Ý][a-zà-ÿ]{2,}(\s+(d[aeo]s?\s+)?[A-ZÀ-Ý][a-zà-ÿ]{2,})+\b")

# Tokens proibidos em NOMES DE CHAVE (comparação exata por token do nome da
# chave, separado por _/-/espaço — "origem" NÃO casa com "rg").
_FORBIDDEN_KEY_TOKENS = {
    "telefone", "whatsapp", "celular", "fone", "cpf", "rg", "email", "e-mail",
    "endereco", "endereço", "observacao", "observação", "obs", "laudo",
    "diagnostico", "diagnóstico", "token", "senha", "password", "secret",
    "credential", "credencial", "apikey", "authorization", "nascimento",
    # Dados bancários/pagamento — nunca em summary (M2 Etapa 3).
    "pix", "conta", "agencia", "agência", "banco", "comprovante", "cartao", "cartão",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _key_tokens(key: str) -> set:
    return {t for t in re.split(r"[_\-\s]+", _strip_accents(str(key)).lower()) if t}


def _is_safe_value(text: str) -> bool:
    return any(rx.fullmatch(text) for rx in _SAFE_VALUE_RES)


def _norm_rules(rules) -> dict:
    rules = rules or {}
    return {
        "pessoa":        {str(f).lower() for f in rules.get("campos_pessoa", [])},
        "institucional": {str(f).lower() for f in rules.get("campos_institucionais", [])},
        "extra_keys":    {str(f).lower() for f in rules.get("chaves_proibidas_extras", [])},
        "excecao":       {str(f).lower() for f in rules.get("chaves_permitidas_excecao", [])},
    }


def validate_summary(payload, rules=None, context="") -> list:
    """Valida um payload de summary. Retorna lista de violações (strings sem
    o valor sensível). Lista vazia = seguro para gravar."""
    r = _norm_rules(rules)
    violations = []

    def _walk(node, path, key_name):
        key_lower = str(key_name).lower() if key_name is not None else ""

        if isinstance(node, dict):
            for k, v in node.items():
                kl = str(k).lower()
                # Exceção declarada: a chave não bloqueia, mas o valor ainda
                # passa por todos os scans de conteúdo ao descer.
                if kl not in r["excecao"]:
                    if _key_tokens(k) & _FORBIDDEN_KEY_TOKENS or kl in r["extra_keys"]:
                        violations.append(f"chave proibida '{k}' em {path or 'raiz'}")
                        continue  # não desce: o conteúdo é proibido por definição
                _walk(v, f"{path}.{k}" if path else str(k), k)
            return

        if isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]", key_name)
            return

        if not isinstance(node, str):
            return  # números/bool/None são seguros por natureza

        text = node.strip()
        if not text:
            return

        # Campo declarado como nome de pessoa: 2+ palavras = nome completo.
        if key_lower in r["pessoa"]:
            if len(text.split()) >= 2:
                violations.append(f"nome completo em campo de pessoa '{path}'")
            return  # campo de pessoa não passa pelos demais scans (1 palavra tolerada)

        if _is_safe_value(text):
            return

        for label, rx in _CONTENT_SCANS:
            if rx.search(text):
                violations.append(f"{label} em '{path}'")

        # Possível nome de pessoa em campo NÃO institucional.
        if key_lower not in r["institucional"] and _NAME_RE.search(text):
            violations.append(f"possivel nome de pessoa em campo nao institucional '{path}'")

    _walk(payload, "", None)

    ctx = f" [{context}]" if context else ""
    return [f"ERRO PII{ctx}: {v}" for v in violations]


def ensure_summary_safe(payload, rules=None, context="") -> None:
    """Conveniência para geradores: imprime as violações e aborta (exit 1)."""
    violations = validate_summary(payload, rules=rules, context=context)
    if violations:
        for v in violations:
            print(v)
        print(f"ERRO: {len(violations)} violacao(oes) de PII — gravacao do summary abortada.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Rulesets usados via `--check-file <path> --ruleset <nome>` pelo
# check-access.sh — redundância de leitura para summaries mantidos à mão
# (custos-investimentos-summary) ou gerados por script (financeiro-summary).
# Chaves de exceção são campos CURTOS estruturados já existentes nesses
# arquivos; os valores continuam passando por todos os scans.
# ---------------------------------------------------------------------------

_FILE_RULESETS = {
    # Desde a M14.2 o financeiro-summary é GERADO por
    # read-financeiro-lancamentos-adc.py a partir da aba Financeiro_Lancamentos
    # (fonte financeira única); o gerador valida na escrita e este ruleset é a
    # redundância na leitura (check-access.sh). Rótulos vêm de enums fechados;
    # "descricao" é template "Serviço — Local". alerta_nota/consultas_nota são
    # do formato antigo, mantidas até o resumo ser regenerado em todo ambiente.
    "financeiro-summary": {
        "campos_pessoa": [],
        "campos_institucionais": ["origem", "servico", "local", "forma", "status",
                                  "mes", "type", "official_source", "generator"],
        "chaves_permitidas_excecao": ["descricao", "alerta_nota", "consultas_nota", "nota"],
    },
    # M23 — snapshots gerados pelo PostgreSQL (nucleo-m15/app/snapshots.py).
    # Substituem os antigos summaries derivados de planilha. Nenhum campo de
    # pessoa é lido do banco; os campos institucionais abaixo carregam nome de
    # EMPRESA parceira, bairro/cidade da unidade e rótulos de enum — isentos
    # apenas do detector de nome. Telefone, CPF, e-mail, token, termo clínico
    # e chave proibida continuam sendo rejeitados normalmente.
    "m23-snapshots": {
        "campos_pessoa": [],
        "campos_institucionais": [
            "clinica", "nome_clinica", "nome", "unidade", "bairro", "cidade", "local",
            "local_atendimento", "empresa", "periodo", "label", "value",
            "variation", "type", "official_source", "generator", "nota",
            "servico", "status", "etapa", "tipo", "origem", "canal",
            "modalidade", "responsavel", "acao", "entidade", "categoria",
            "dia_semana", "horario", "mes", "dia", "key",
        ],
        "chaves_permitidas_excecao": ["nota"],
    },
    "custos-investimentos-summary": {
        "campos_pessoa": [],
        # nome/item = equipamento ou primeiro nome de sócio (permitido pelo
        # projeto); responsavel = membro da equipe; nota = texto curto curado
        # pelos sócios que cita nomes de EQUIPAMENTO ("Espirômetro Koko") —
        # isento do detector de nome, mas scans de telefone/CPF/pix valem.
        "campos_institucionais": ["nome", "item", "categoria", "responsavel", "alertas", "nota"],
        "chaves_permitidas_excecao": ["obs_curta", "observacao_curta", "nota"],
    },
}


def _check_file(path: str, ruleset_name: str) -> int:
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        print(f"INFO: {path} não existe — nada a validar.")
        return 0
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERRO: JSON inválido em {path} — {exc}")
        return 1

    rules = _FILE_RULESETS.get(ruleset_name)
    if ruleset_name and rules is None:
        print(f"ERRO: ruleset '{ruleset_name}' não registrado em pii_guard._FILE_RULESETS.")
        return 1

    violations = validate_summary(payload, rules=rules, context=ruleset_name or p.name)
    if violations:
        for v in violations:
            print(v)
        print(f"ERRO: {len(violations)} violacao(oes) de PII em {path}.")
        return 1
    print(f"OK: {path} sem PII (ruleset: {ruleset_name or 'padrao'}).")
    return 0


# ---------------------------------------------------------------------------
# Self-test offline
# ---------------------------------------------------------------------------

def _self_test() -> int:
    base_rules = {
        "campos_pessoa": ["primeiro_nome"],
        "campos_institucionais": ["nome_clinica", "unidade", "operador", "origem"],
    }

    clean = {
        "source": {"type": "teste_summary", "safeToDisplay": True,
                   "containsPersonalData": False, "generatedAt": "2026-07-07T09:00:00+00:00"},
        "stats": {"total_eventos": 7, "erros": 1, "por_acao": {"update_lead_stage": 3}},
        "ultimos_eventos": [{
            "timestamp": "06/07/2026 14:32:05",
            "acao": "update_lead_stage",
            "entidade_id": "LEAD-20260703-001",
            "operador": "teste-operador",
            "resultado": "ok",
        }],
        "valores": {"receita": "R$ 2.385,79", "margem": "12,5%", "data_curta": "06/07/2026"},
        "nome_clinica": "Clinica Pastore Ipanema",
    }

    def _clone_with(path_key, value):
        import copy
        p = copy.deepcopy(clean)
        p[path_key] = value
        return p

    cases = [
        ("payload limpo passa",                    clean,                                            base_rules, True),
        ("telefone injetado falha",                _clone_with("campo_x", "ligar para (21) 99999-8888"), base_rules, False),
        ("CPF injetado falha",                     _clone_with("campo_x", "doc 123.456.789-09"),     base_rules, False),
        ("e-mail injetado falha",                  _clone_with("campo_x", "contato fulano@example.com"), base_rules, False),
        ("nome completo em campo de pessoa falha", _clone_with("primeiro_nome", "Maria Aparecida da Silva"), base_rules, False),
        ("nome de clinica institucional passa",    _clone_with("nome_clinica", "Rede Pastore Botafogo"), base_rules, True),
        ("data com segundos passa",                _clone_with("campo_x", "06/07/2026 14:32:05"),    base_rules, True),
        ("valor monetario passa",                  _clone_with("campo_x", "R$ 1.234,56"),            base_rules, True),
        ("chave proibida (observacao) falha",      _clone_with("observacao", "texto livre qualquer"), base_rules, False),
        ("chave proibida (token) falha",           _clone_with("api_token", "x"),                    base_rules, False),
        ("bearer em valor falha",                  _clone_with("campo_x", "Bearer abc"),             base_rules, False),
        ("script.google em valor falha",           _clone_with("campo_x", "https://script.google.com/macros/x"), base_rules, False),
        ("nome composto fora de campo institucional falha", _clone_with("campo_x", "encaminhado por Joao Carvalho"), base_rules, False),
        ("termo clinico livre falha",              _clone_with("campo_x", "paciente trouxe pedido medico"), base_rules, False),
        ("id tecnico passa",                       _clone_with("campo_x", "AUD-0001"),               base_rules, True),
        ("timestamp ISO com fuso passa",           _clone_with("campo_x", "2026-07-07T09:00:00+00:00"), base_rules, True),
        ("chave pix em valor falha",               _clone_with("campo_x", "pagar na chave pix do consultorio"), base_rules, False),
        ("chave bancaria (nome de chave) falha",   _clone_with("chave_pix", "x"),                    base_rules, False),
        ("excecao permite obs_curta com texto seguro",
         _clone_with("obs_curta", "aparelho em uso na unidade"),
         {**base_rules, "chaves_permitidas_excecao": ["obs_curta"]}, True),
        ("excecao NAO desliga scans (telefone em obs_curta falha)",
         _clone_with("obs_curta", "retorno (21) 98888-7777"),
         {**base_rules, "chaves_permitidas_excecao": ["obs_curta"]}, False),
        ("obs_curta sem excecao declarada falha",  _clone_with("obs_curta", "texto qualquer"),       base_rules, False),
        ("slug longo de URL sem digito passa",
         _clone_with("campo_x", "/consulta-pneumologista-rio-de-janeiro/"), base_rules, True),
        ("ID longo com digitos falha",
         _clone_with("campo_x", "1AbC2dEf3GhI4jKl5MnO6pQr7StU8vWx9Yz0aBcDe"), base_rules, False),
    ]

    failures = 0
    for label, payload, rules, expect_clean in cases:
        violations = validate_summary(payload, rules=rules, context="self-test")
        ok = (not violations) if expect_clean else bool(violations)
        print(f"  {'PASS' if ok else 'FAIL'}: {label}")
        if not ok:
            failures += 1
            for v in violations:
                print(f"        {v}")

    print()
    if failures:
        print(f"self-test: {failures} caso(s) FALHARAM.")
        return 1
    print(f"self-test: {len(cases)} casos OK.")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        print("pii_guard — self-test offline (nenhum dado real, nenhuma rede)")
        return _self_test()

    if "--check-file" in sys.argv:
        idx = sys.argv.index("--check-file")
        if idx + 1 >= len(sys.argv):
            print("ERRO: --check-file exige um caminho.")
            return 1
        path = sys.argv[idx + 1]
        ruleset = ""
        if "--ruleset" in sys.argv:
            ridx = sys.argv.index("--ruleset")
            ruleset = sys.argv[ridx + 1] if ridx + 1 < len(sys.argv) else ""
        return _check_file(path, ruleset)

    print(__doc__.strip())
    print()
    print("Uso: python3 painel-soprolife/scripts/pii_guard.py --self-test")
    print("     python3 painel-soprolife/scripts/pii_guard.py --check-file <path> [--ruleset <nome>]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
