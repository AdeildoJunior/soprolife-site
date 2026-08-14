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
        # Mapas cujas CHAVES são rótulos contados, não nomes de campo.
        "mapas_de_contagem": ["por_acao", ...],
        # Campos escalares que carregam rótulo de vocabulário fechado. Junto
        # com as chaves de "mapas_de_contagem", são as posições em que um
        # rótulo BEM-FORMADO (VOCABULARY_SLUG_RE) dispensa os dois scans de
        # texto livre — termo clínico e token/ID longo. Todo o resto
        # (telefone, CPF, e-mail, bearer, chave de API, URL de planilha)
        # continua valendo. Ver M25.29C.
        "campos_vocabulario": ["acao", "entidade_tipo", ...],
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

#: Rótulo do scan de termo clínico. Só ele pode ser dispensado, e apenas nos
#: campos declarados em ``campos_busca_agregada`` — ver _norm_rules.
_SCAN_TERMO_CLINICO = "termo clinico livre"

#: Rótulo do scan de token/ID longo. Ver _SCANS_DISPENSAVEIS_EM_VOCABULARIO.
_SCAN_TOKEN_LONGO = "possivel token/ID longo"

# ---------------------------------------------------------------------------
# Vocabulário institucional fechado (M25.29C).
#
# Alguns campos e mapas NÃO carregam texto livre: carregam o NOME de uma
# operação, de uma tabela, de um papel ou de um resultado — emitido pelo
# próprio código, não digitado por ninguém. "laudo_conteudo_entregue",
# "report_documents", "admin", "ok".
#
# Esses rótulos foram desenhados para ser lidos por humano e por isso são
# descritivos. Dois scans de TEXTO LIVRE acusam falso positivo neles:
#
#   - termo clínico: "laudo_conteudo_entregue" contém "laudo" (M25.28);
#   - token/ID longo: "manutencao.remocao_laudos_teste_pastore_m2529b" tem um
#     segmento de 35 caracteres com dígito (M25.29B) — e o evento é
#     append-only, não dá para renomear.
#
# Em ambos os casos TODOS os snapshots pararam de ser gerados, porque a
# validação é tudo-ou-nada.
#
# A dispensa NÃO é um esconderijo: ela só vale para valor que TEM FORMA de
# rótulo (VOCABULARY_SLUG_RE) e só dispensa esses dois scans. Telefone, CPF,
# RG, e-mail, bearer, chave de API, URL de planilha e chave pix continuam
# valendo — um CPF só de dígitos tem forma de slug e segue barrado pelo scan
# de CPF. O que não tiver forma de rótulo é violação explícita E continua
# sendo varrido por tudo.
# ---------------------------------------------------------------------------

#: Forma canônica de um rótulo de vocabulário: minúsculas, dígitos, ``_`` e
#: ``.``, até 80 caracteres — exatamente o que o código emite
#: ("auth.token_emitido", "laudo_conteudo_entregue", "report_documents").
#:
#: FONTE ÚNICA do projeto: ``scripts/audit_summary_contract.py`` importa esta
#: constante em vez de manter a sua. Duas definições divergentes desta forma
#: seriam duas portas com fechaduras diferentes.
VOCABULARY_SLUG_RE = re.compile(r"^[a-z0-9_.]{1,80}$")

#: Os ÚNICOS scans dispensados em posição de vocabulário. Qualquer outro
#: continua valendo. Manter esta lista curta é o que impede que a dispensa
#: vire um buraco genérico.
_SCANS_DISPENSAVEIS_EM_VOCABULARIO = frozenset({
    _SCAN_TERMO_CLINICO,
    _SCAN_TOKEN_LONGO,
})


def is_vocabulary_label(text) -> bool:
    """True se ``text`` tem a forma de rótulo institucional fechado."""
    return bool(VOCABULARY_SLUG_RE.fullmatch(str(text)))

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
        # Mapas de CONTAGEM: dicionários cujas chaves são rótulos contados
        # (vocabulário fechado do próprio código), não nomes de campo que
        # carregam conteúdo. Em "stats.por_acao" a chave
        # "laudo_conteudo_entregue" é o NOME DA OPERAÇÃO auditada e o valor é
        # um inteiro — não existe ali texto clínico de paciente. Sem esta
        # distinção o guarda lia o rótulo como se fosse um campo chamado
        # "laudo" (M25.28). O rótulo continua passando pelos scans de
        # conteúdo como se fosse um valor: um telefone travestido de chave
        # segue barrado.
        "contagem":      {str(f).lower() for f in rules.get("mapas_de_contagem", [])},
        # Campos ESCALARES que carregam rótulo de vocabulário fechado
        # (ultimos_eventos[].acao, .entidade_tipo, .operador, .resultado).
        # As CHAVES de "mapas_de_contagem" também são posição de vocabulário
        # — ver _walk. Ambos passam por _scan_text(vocabulario=True).
        "vocabulario":   {str(f).lower() for f in rules.get("campos_vocabulario", [])},
        # Campos que carregam TERMO DE BUSCA AGREGADO devolvido pelo Google
        # (Search Console). Dispensam APENAS o scan de termo clínico — ver
        # _SCAN_TERMO_CLINICO. Telefone, CPF, e-mail, token e detector de
        # nome continuam valendo.
        "busca":         {str(f).lower() for f in rules.get("campos_busca_agregada", [])},
    }


def validate_summary(payload, rules=None, context="") -> list:
    """Valida um payload de summary. Retorna lista de violações (strings sem
    o valor sensível). Lista vazia = seguro para gravar."""
    r = _norm_rules(rules)
    violations = []

    def _scan_text(text, path, key_lower, vocabulario=False):
        """Aplica os scans de conteúdo a um texto solto (valor ou rótulo).

        ``vocabulario=True`` indica posição declarada de vocabulário fechado
        (M25.29C): o valor é validado pela FORMA e, se tiver forma de rótulo,
        os dois scans de texto livre que dão falso positivo nele são
        dispensados. Fora da forma, é violação E segue varrido por tudo.
        """
        e_rotulo = False
        if vocabulario:
            # A FORMA é avaliada ANTES do atalho de valor seguro, de propósito.
            # _SAFE_VALUE_RES trata "número" e "slug sem limite" como seguros
            # por construção — o que faria um CPF de 11 dígitos ou um slug de
            # 200 caracteres passarem direto, sem chegar aos scans. Numa
            # posição de vocabulário isso não vale: aqui só passa o que TEM
            # FORMA DE RÓTULO e ainda assim sobrevive aos scans de PII.
            # Resultado: posição de vocabulário ficou mais ESTRITA que antes,
            # nunca mais frouxa.
            e_rotulo = is_vocabulary_label(text)
            if not e_rotulo:
                # Default-fechado: posição de vocabulário que recebeu algo sem
                # forma de rótulo (frase com espaço, maiúsculas, acima do
                # limite de 80) é suspeita por si só — e continua sendo varrida.
                violations.append(f"rotulo fora do formato de vocabulario em '{path}'")
        elif _is_safe_value(text):
            return

        dispensa_clinico = key_lower in r["busca"]
        for label, rx in _CONTENT_SCANS:
            # Termo de busca agregado do Search Console é palavra-chave de
            # SEO, não texto clínico sobre alguém: "precisa de pedido
            # médico?" é exatamente o que a SoproLife quer ranquear. Só este
            # scan é dispensado, e só nos campos declarados.
            if dispensa_clinico and label == _SCAN_TERMO_CLINICO:
                continue
            # Rótulo institucional bem-formado: dispensa APENAS os dois scans
            # de texto livre. PII e segredo continuam barrando.
            if e_rotulo and label in _SCANS_DISPENSAVEIS_EM_VOCABULARIO:
                continue
            if rx.search(text):
                violations.append(f"{label} em '{path}'")

        # Possível nome de pessoa em campo NÃO institucional.
        if key_lower not in r["institucional"] and _NAME_RE.search(text):
            violations.append(f"possivel nome de pessoa em campo nao institucional '{path}'")

    def _walk(node, path, key_name):
        key_lower = str(key_name).lower() if key_name is not None else ""

        if isinstance(node, dict):
            # Num mapa de contagem as chaves são rótulos contados, não nomes
            # de campo — são varridas como VALOR, nunca pela lista de chaves
            # proibidas.
            em_contagem = key_lower in r["contagem"]
            for k, v in node.items():
                kl = str(k).lower()
                aqui = f"{path}.{k}" if path else str(k)
                if em_contagem:
                    rotulo = str(k).strip()
                    if rotulo:
                        # A chave de um mapa de contagem É vocabulário fechado
                        # por definição (M25.29C).
                        _scan_text(rotulo, aqui, "", vocabulario=True)
                    _walk(v, aqui, k)
                    continue
                # Exceção declarada: a chave não bloqueia, mas o valor ainda
                # passa por todos os scans de conteúdo ao descer.
                if kl not in r["excecao"]:
                    if _key_tokens(k) & _FORBIDDEN_KEY_TOKENS or kl in r["extra_keys"]:
                        violations.append(f"chave proibida '{k}' em {path or 'raiz'}")
                        continue  # não desce: o conteúdo é proibido por definição
                _walk(v, aqui, k)
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

        _scan_text(text, path, key_lower, vocabulario=key_lower in r["vocabulario"])

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
            "modalidade", "responsavel", "acao", "categoria",
            "dia_semana", "horario", "mes", "dia", "key",
            # Auditoria (M23, contrato real de scripts/check-access.sh via
            # scripts/audit_summary_contract.py): entidade_tipo é nome de
            # tabela/domínio (ex.: "leads", "partners"); operador é papel
            # institucional (admin/gestor/operacional/leitura); resultado é
            # "ok"/"falha" derivado do próprio texto de 'acao'; timestamp é
            # ISO 8601. Todos de domínio fechado — nenhum é nome pessoal nem
            # identificador de registro.
            # entidade_id NÃO consta aqui de propósito: o resumo público de
            # auditoria deixou de exportar identificador de linha no 2º
            # incidente do M23. Recolocá-lo aqui reabriria o vazamento.
            "entidade_tipo", "operador", "resultado", "timestamp",
        ],
        "chaves_permitidas_excecao": ["nota"],
        # M25.28 — os mapas de "stats" contam eventos por rótulo. As chaves
        # são o vocabulário de ações/tabelas/papéis do próprio código
        # ("laudo_conteudo_entregue", "report_documents", "admin"), com um
        # inteiro por valor. Quando a operação de laudos entrou em produção
        # (09/08/2026) esses rótulos passaram a conter o token "laudo" e o
        # guarda os leu como nome de campo proibido: TODOS os snapshots do
        # painel pararam de ser gerados, não só a auditoria — a validação é
        # tudo-ou-nada. Os rótulos seguem passando pelos scans de conteúdo.
        "mapas_de_contagem": [
            "por_acao", "por_entidade", "por_operador", "por_resultado",
        ],
        # M25.29C — os MESMOS quatro vocabulários aparecem também como campo
        # escalar em cada evento de "ultimos_eventos". Os nomes espelham
        # VOCABULARY_STAT_MAPS/VOCABULARY_EVENT_FIELDS de
        # scripts/audit_summary_contract.py de propósito: é um contrato só,
        # descrito nas duas pontas que validam o mesmo arquivo.
        #
        # Sem isto, "manutencao.remocao_laudos_teste_pastore_m2529b" (evento
        # append-only da M25.29B, 35 caracteres após o ponto) era lido como
        # possível token e derrubava os nove snapshots do PostgreSQL a cada
        # ciclo do timer.
        "campos_vocabulario": [
            "acao", "entidade_tipo", "operador", "resultado",
        ],
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

        # ── M25.28 — mapa de contagem: a chave é rótulo, não nome de campo ──
        ("rotulo de acao com 'laudo' em mapa de contagem passa",
         _clone_with("stats", {"por_acao": {"laudo_conteudo_entregue": 12,
                                            "exame_reaberto_para_laudo": 2,
                                            "auth.token_emitido": 400}}),
         {**base_rules, "mapas_de_contagem": ["por_acao"]}, True),
        ("mesmo rotulo SEM o mapa declarado falha",
         _clone_with("stats", {"por_acao": {"laudo_conteudo_entregue": 12}}),
         base_rules, False),
        ("mapa de contagem NAO vira esconderijo (telefone no rotulo falha)",
         _clone_with("stats", {"por_acao": {"retorno (21) 98888-7777": 1}}),
         {**base_rules, "mapas_de_contagem": ["por_acao"]}, False),
        ("mapa de contagem NAO vira esconderijo (CPF no rotulo falha)",
         _clone_with("stats", {"por_acao": {"doc 123.456.789-09": 1}}),
         {**base_rules, "mapas_de_contagem": ["por_acao"]}, False),
        ("chave proibida fora de mapa de contagem continua falhando",
         _clone_with("stats", {"detalhes": {"laudo": "texto do laudo"}}),
         {**base_rules, "mapas_de_contagem": ["por_acao"]}, False),

        # ── M25.28 — termo de busca agregado do Search Console ──
        ("termo clinico em campo de busca agregada passa",
         _clone_with("query", "precisa de pedido médico?"),
         {**base_rules, "campos_busca_agregada": ["query"]}, True),
        ("mesmo termo SEM o campo declarado falha",
         _clone_with("query", "precisa de pedido médico?"), base_rules, False),
        ("busca agregada dispensa SO o termo clinico (telefone falha)",
         _clone_with("query", "espirometria (21) 98888-7777"),
         {**base_rules, "campos_busca_agregada": ["query"]}, False),
        ("busca agregada dispensa SO o termo clinico (e-mail falha)",
         _clone_with("query", "laudo fulano@example.com"),
         {**base_rules, "campos_busca_agregada": ["query"]}, False),
        ("termo clinico em campo NAO declarado continua falhando",
         _clone_with("campo_x", "resultado de exame do paciente"),
         {**base_rules, "campos_busca_agregada": ["query"]}, False),
        ("slug longo de URL sem digito passa",
         _clone_with("campo_x", "/consulta-pneumologista-rio-de-janeiro/"), base_rules, True),
        ("ID longo com digitos falha",
         _clone_with("campo_x", "1AbC2dEf3GhI4jKl5MnO6pQr7StU8vWx9Yz0aBcDe"), base_rules, False),
    ] + _vocab_cases(base_rules, clean)

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


# ---------------------------------------------------------------------------
# M25.29C — vocabulário institucional fechado.
#
# O rótulo abaixo é o evento REAL, append-only, gravado pela manutenção
# M25.29B. Seu segmento após o ponto tem exatamente 35 caracteres e contém
# dígitos: é o caso que derrubou os nove snapshots do PostgreSQL a cada ciclo
# do timer, por 24 ciclos, até esta correção.
# ---------------------------------------------------------------------------

_ACAO_M2529B = "manutencao.remocao_laudos_teste_pastore_m2529b"


def _vocab_cases(base_rules, clean) -> list:
    import copy

    vocab_rules = {
        **base_rules,
        "mapas_de_contagem": ["por_acao", "por_entidade", "por_operador", "por_resultado"],
        "campos_vocabulario": ["acao", "entidade_tipo", "operador", "resultado"],
    }

    def base():
        """Base com vocabulário BEM-FORMADO, espelhando o snapshot real do M23.

        Não reaproveita o ``clean`` dos outros casos porque lá ``operador`` é
        "teste-operador" — hífen não é forma de rótulo. Sob este ruleset isso
        é violação legítima, e é justamente o que os casos 13/14 provam.
        """
        p = copy.deepcopy(clean)
        p["stats"] = {"total_eventos": 1, "erros": 0, "por_acao": {"update_lead_stage": 1}}
        p["ultimos_eventos"] = [{
            "timestamp": "06/07/2026 14:32:05",
            "acao": "update_lead_stage",
            "entidade_tipo": "leads",
            "operador": "admin",
            "resultado": "ok",
        }]
        return p

    def com_rotulo(rotulo, valor=1):
        """Payload com `rotulo` como CHAVE de stats.por_acao."""
        p = base()
        p["stats"] = {"total_eventos": 1, "erros": 0, "por_acao": {rotulo: valor}}
        return p

    def com_acao(valor):
        """Payload com `valor` no campo escalar ultimos_eventos[0].acao."""
        p = base()
        p["ultimos_eventos"][0]["acao"] = valor
        return p

    def com_campo(chave, valor):
        """Payload com `valor` num campo comum, FORA de posição de vocabulário."""
        p = base()
        p[chave] = valor
        return p

    def com_stats(stats):
        p = base()
        p["stats"] = stats
        return p

    return [
        # ── positivos: rótulo institucional legítimo passa ──
        ("M25.29C 1: acao curta normal passa",
         com_rotulo("update_lead_stage"), vocab_rules, True),
        ("M25.29C 2: laudo_conteudo_entregue passa como rotulo",
         com_rotulo("laudo_conteudo_entregue"), vocab_rules, True),
        ("M25.29C 3: acao real da M25.29B (35 chars pos-ponto) passa",
         com_rotulo(_ACAO_M2529B), vocab_rules, True),
        ("M25.29C 4: o mesmo rotulo em stats.por_acao passa",
         com_rotulo(_ACAO_M2529B, 3), vocab_rules, True),
        ("M25.29C 5: o mesmo rotulo em ultimos_eventos[].acao passa",
         com_acao(_ACAO_M2529B), vocab_rules, True),
        ("M25.29C: vocabulario real do M23 passa inteiro",
         com_stats({
             "total_eventos": 9, "erros": 0,
             "por_acao": {_ACAO_M2529B: 1, "auth.token_emitido": 4,
                          "laudo_conteudo_entregue": 2, "pessoa.criada": 2},
             "por_entidade": {"report_documents": 3, "maintenance": 1},
             "por_operador": {"admin": 5, "gestor": 4},
             "por_resultado": {"ok": 8, "falha": 1},
         }), vocab_rules, True),

        # ── negativos: a dispensa NAO e esconderijo ──
        ("M25.29C 6: telefone em posicao de vocabulario continua rejeitado",
         com_rotulo("21998887777"), vocab_rules, False),
        ("M25.29C 7: CPF so com digitos (tem forma de slug) continua rejeitado",
         com_rotulo("12345678901"), vocab_rules, False),
        ("M25.29C 8: e-mail em posicao de vocabulario continua rejeitado",
         com_rotulo("fulano@example.com"), vocab_rules, False),
        ("M25.29C 9: bearer em posicao de vocabulario continua rejeitado",
         com_acao("bearer abc123def456"), vocab_rules, False),
        ("M25.29C 10: chave AIza continua rejeitada",
         com_acao("AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"), vocab_rules, False),
        ("M25.29C 11: token ya29 (tem forma de slug) continua rejeitado",
         com_rotulo("ya29.a0af_e4b7c9d2f1a8b3c6d5e4f7a9b2c1d8e3f6"), vocab_rules, False),
        ("M25.29C 12: docs.google (tem forma de slug) continua rejeitado",
         com_rotulo("docs.google.com"), vocab_rules, False),
        ("M25.29C 13: frase livre com espacos em vocabulario e rejeitada",
         com_acao("paciente retornou com laudo em maos"), vocab_rules, False),
        ("M25.29C 14: slug acima do limite de 80 e rejeitado",
         com_rotulo("a1" + "_muito_longo" * 8), vocab_rules, False),
        ("M25.29C 14b: rotulo com maiuscula nao e vocabulario",
         com_acao("Manutencao.Remocao"), vocab_rules, False),

        # ── a dispensa e local: nada muda fora da posicao declarada ──
        ("M25.29C 15: campo NAO-vocabulario segue sob o scan de token longo",
         com_campo("campo_x", _ACAO_M2529B), vocab_rules, False),
        ("M25.29C 15b: sem declarar campos_vocabulario, a acao volta a falhar",
         com_acao(_ACAO_M2529B), base_rules, False),
        ("M25.29C 16: M25.28 — busca agregada legitima continua passando",
         com_campo("query", "precisa de pedido médico?"),
         {**vocab_rules, "campos_busca_agregada": ["query"]}, True),
        ("M25.29C 17: 'laudo' como texto livre de paciente continua rejeitado",
         com_campo("campo_x", "laudo do paciente entregue em maos"), vocab_rules, False),
    ]


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
