#!/usr/bin/env python3
"""SoproLife — M14.3A.2 — biblioteca de paridade do Apps Script.

Análise estática 100% offline de arquivos .gs/.js (dialeto Apps Script):

- remoção de comentários, strings, template literals e regex literals
  preservando a estrutura de linhas e chaves;
- extração de símbolos globais (function/var/let/const/class no nível 0);
- classificação de entrypoints (Web App, triggers simples, triggers
  nomeados, funções manuais);
- detecção de operações de escrita/destrutivas em planilha;
- detecção de dependências entre arquivos canônicos;
- utilidades de hash e varredura de padrões com cara de segredo.

Usada por:
  generate-apps-script-publication-manifest.py
  compare-apps-script-export.py

Nunca acessa rede, Google, ADC, credencial ou data-private.
"""

import hashlib
import re

# Ordem canônica dos 9 arquivos esperados no projeto Apps Script remoto.
ARQUIVOS_CANONICOS = [
    "command-center-api.gs",
    "contratos-canonicos.gs",
    "pastore-staging.gs",
    "converter-lead-em-paciente.gs",
    "sync-crm-pacientes.gs",
    "organizar-leads-operacionais.gs",
    "soprolife-sheets-template.gs",
    "manual-das-abas.gs",
    "limpar-leads-e-manual-abas.gs",
]

DIR_APPS_SCRIPT = "painel-soprolife/apps-script"

# Operações de escrita monitoradas (chamada de método: ".op(").
OPERACOES_ESCRITA = [
    "clear",
    "clearContents",
    "deleteRow",
    "deleteRows",
    "deleteSheet",
    "appendRow",
    "insertSheet",
    "setValue",
    "setValues",
]

# Subconjunto considerado destrutivo (remove/limpa dados existentes).
OPERACOES_DESTRUTIVAS = ["clearContents", "deleteRow", "deleteRows", "deleteSheet"]

# Símbolos de implementações antigas que nunca podem voltar ao remoto.
SIMBOLOS_PROIBIDOS = [
    "_nextId",
    "_reescreverAbaPacientes",
    "_cvConjuntoTelefones",
    "_conteudoManualAbas",
    "onOpen_syncCRM",
]

# Entrypoints que só podem existir UMA vez no projeto inteiro.
ENTRYPOINTS_UNICOS = [
    "doPost",
    "doGet",
    "onOpen",
    "onEdit",
    "_registrarAtendimentoPastore",
    "_cvConverterLeadCore",
]

# Funções bloqueadas → marcador de bloqueio que PRECISA existir no arquivo
# que as define (stub de segurança). Definição sem o marcador = reativação.
FUNCOES_BLOQUEADAS = {
    "converterLeadSelecionadoSoproLife": "_CV_CONVERSAO_BLOQUEADA",
    "_cvConverterLeadCore": "_CV_CONVERSAO_BLOQUEADA",
    "sincronizarCRMPacientesSoproLife": "_SYNC_CRM_PACIENTES_BLOQUEADO",
    "organizarLeadsOperacionaisSoproLife": "_OP_LEADS_BLOQUEADO",
}

# Padrões (regex, case-insensitive) de NOMES de arquivos remotos antigos/
# perigosos. Aplicados somente a arquivos fora da lista canônica.
PADROES_NOME_PROIBIDO = [
    r"pastore-planilha",
    r"pastore-formulas",
    r"resumo-dashboard-auto",
    r"(^|[^a-z])old([^a-z]|$)",
    r"backup",
    r"c[oó]pia",
    r"copy",
    r"antig[ao]",
    r"(^|[^a-z0-9])v[0-9]+([^a-z0-9]|$)",
]

# Padrões com cara de segredo/credencial/URL privada. Nenhum conteúdo que
# case com isso pode entrar no Git nem no pacote de publicação.
PADROES_SEGREDO = [
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"AIza[0-9A-Za-z_\-]{30,}",
    r"ya29\.[0-9A-Za-z_\-]{20,}",
    r"\"private_key\"",
    r"script\.google\.com/macros/s/[A-Za-z0-9_\-]{20,}",
    r"(?i)\b(api[_-]?token|apitoken|auth[_-]?token|client[_-]?secret|private[_-]?key|senha|password|credencial)\b\s*[:=]\s*[\"'][^\"']{10,}[\"']",
]


def sha256_hex(dados):
    """SHA-256 hexadecimal de bytes."""
    return hashlib.sha256(dados).hexdigest()


# Palavras-chave após as quais um '/' só pode iniciar um literal regex —
# nunca divisão — mesmo terminando em caractere alfanumérico (o que faz a
# heurística ingênua baseada só no último caractere errar). Lista alinhada
# com o conjunto "beforeExpr" de tokenizadores JS de referência.
PALAVRAS_CHAVE_ANTES_DE_REGEX = {
    "return", "throw", "case", "yield", "await",
    "typeof", "instanceof", "delete", "void", "new", "do", "else", "in", "of",
}


def _eh_inicio_regex(anterior_char, anterior_palavra):
    """Heurística: decide se '/' inicia um literal regex ou é divisão.

    Usa o último caractere significativo E a última palavra/identificador
    completo emitidos. Um '/' após ')' ou ']' é divisão (resultado de
    chamada/indexação). Após um identificador ou número comum também é
    divisão — EXCETO quando esse "identificador" é, na verdade, uma
    palavra-chave que introduz uma expressão (return, throw, case, yield,
    await, ...), caso em que é regex. Após qualquer operador/pontuação
    (=, (, [, {, ,, :, ?, !, &&, ||, =>) ou no início do arquivo, é regex.
    """
    if not anterior_char:
        return True
    if anterior_char in ")]":
        return False
    if anterior_char.isalnum() or anterior_char in "_$":
        return anterior_palavra in PALAVRAS_CHAVE_ANTES_DE_REGEX
    return True


def limpar_codigo(fonte):
    """Substitui comentários, strings, templates e regex por espaços.

    Preserva quebras de linha e todos os caracteres estruturais restantes,
    para que profundidade de chaves e números de linha continuem válidos.
    """
    saida = []
    i = 0
    n = len(fonte)
    anterior = ""  # último caractere significativo emitido
    anterior_palavra = ""  # última palavra/identificador/número completo
    palavra_atual = []  # acumulador da palavra em formação
    while i < n:
        c = fonte[i]
        eh_char_palavra = c.isalnum() or c in "_$"
        if not eh_char_palavra and palavra_atual:
            anterior_palavra = "".join(palavra_atual)
            palavra_atual = []
        prox = fonte[i + 1] if i + 1 < n else ""
        if c == "/" and prox == "/":
            while i < n and fonte[i] != "\n":
                saida.append(" ")
                i += 1
        elif c == "/" and prox == "*":
            saida.append("  ")
            i += 2
            while i < n and not (fonte[i] == "*" and i + 1 < n and fonte[i + 1] == "/"):
                saida.append("\n" if fonte[i] == "\n" else " ")
                i += 1
            if i < n:
                saida.append("  ")
                i += 2
        elif c in ('"', "'"):
            alvo = c
            saida.append(" ")
            i += 1
            while i < n and fonte[i] != alvo:
                if fonte[i] == "\\" and i + 1 < n:
                    saida.append("  ")
                    i += 2
                else:
                    saida.append("\n" if fonte[i] == "\n" else " ")
                    i += 1
            if i < n:
                saida.append(" ")
                i += 1
        elif c == "`":
            # Template literal inteiro vira espaço (interpolações inclusas).
            saida.append(" ")
            i += 1
            while i < n and fonte[i] != "`":
                if fonte[i] == "\\" and i + 1 < n:
                    saida.append("  ")
                    i += 2
                else:
                    saida.append("\n" if fonte[i] == "\n" else " ")
                    i += 1
            if i < n:
                saida.append(" ")
                i += 1
        elif c == "/" and _eh_inicio_regex(anterior, anterior_palavra):
            saida.append(" ")
            i += 1
            em_classe = False
            while i < n:
                ch = fonte[i]
                if ch == "\\" and i + 1 < n:
                    saida.append("  ")
                    i += 2
                    continue
                if ch == "[":
                    em_classe = True
                elif ch == "]":
                    em_classe = False
                elif ch == "/" and not em_classe:
                    saida.append(" ")
                    i += 1
                    break
                elif ch == "\n":
                    # regex não cruza linha; heurística errou — devolve.
                    break
                saida.append(" ")
                i += 1
        else:
            saida.append(c)
            if eh_char_palavra:
                palavra_atual.append(c)
            if not c.isspace():
                anterior = c
            i += 1
    return "".join(saida)


def mascarar_literais_regex(fonte):
    """Substitui SOMENTE o miolo de literais regex JS por espaços.

    Ao contrário de limpar_codigo (que também apaga comentários, strings e
    templates), esta função preserva tudo isso intacto — só o conteúdo
    ENTRE as barras delimitadoras de um literal regex vira espaço; as
    próprias barras e as flags depois delas ficam de fora do literal e
    passam pelo caminho normal (preservadas). Usa a mesma heurística
    regex-vs-divisão (_eh_inicio_regex/PALAVRAS_CHAVE_ANTES_DE_REGEX) de
    limpar_codigo, então concorda com ela sobre onde um regex começa.

    Uso: rodar a varredura de segredos (varrer_segredos) sobre o resultado
    desta função em vez do código-fonte bruto, para que texto com cara de
    segredo dentro de um literal regex (ex.: /apiToken="x"/) não dispare
    falso positivo — enquanto uma atribuição real (const apiToken = "x";)
    continua intacta e detectável, pois strings não são tocadas aqui.
    """
    saida = []
    i = 0
    n = len(fonte)
    anterior = ""  # último caractere significativo emitido
    anterior_palavra = ""  # última palavra/identificador/número completo
    palavra_atual = []  # acumulador da palavra em formação
    while i < n:
        c = fonte[i]
        eh_char_palavra = c.isalnum() or c in "_$"
        if not eh_char_palavra and palavra_atual:
            anterior_palavra = "".join(palavra_atual)
            palavra_atual = []
        prox = fonte[i + 1] if i + 1 < n else ""
        if c == "/" and prox == "/":
            while i < n and fonte[i] != "\n":
                saida.append(fonte[i])
                i += 1
        elif c == "/" and prox == "*":
            saida.append(c)
            saida.append(prox)
            i += 2
            while i < n and not (fonte[i] == "*" and i + 1 < n and fonte[i + 1] == "/"):
                saida.append(fonte[i])
                i += 1
            if i < n:
                saida.append(fonte[i])
                saida.append(fonte[i + 1])
                i += 2
        elif c in ('"', "'"):
            alvo = c
            saida.append(c)
            i += 1
            while i < n and fonte[i] != alvo:
                if fonte[i] == "\\" and i + 1 < n:
                    saida.append(fonte[i])
                    saida.append(fonte[i + 1])
                    i += 2
                else:
                    saida.append(fonte[i])
                    i += 1
            if i < n:
                saida.append(fonte[i])
                i += 1
        elif c == "`":
            saida.append(c)
            i += 1
            while i < n and fonte[i] != "`":
                if fonte[i] == "\\" and i + 1 < n:
                    saida.append(fonte[i])
                    saida.append(fonte[i + 1])
                    i += 2
                else:
                    saida.append(fonte[i])
                    i += 1
            if i < n:
                saida.append(fonte[i])
                i += 1
        elif c == "/" and _eh_inicio_regex(anterior, anterior_palavra):
            saida.append("/")  # barra delimitadora de abertura: preservada
            i += 1
            em_classe = False
            while i < n:
                ch = fonte[i]
                if ch == "\\" and i + 1 < n:
                    saida.append("  ")
                    i += 2
                    continue
                if ch == "[":
                    em_classe = True
                elif ch == "]":
                    em_classe = False
                elif ch == "/" and not em_classe:
                    saida.append("/")  # barra delimitadora de fechamento
                    i += 1
                    break
                elif ch == "\n":
                    # regex não cruza linha; heurística errou — devolve.
                    break
                saida.append("\n" if ch == "\n" else " ")
                i += 1
        else:
            saida.append(c)
            if eh_char_palavra:
                palavra_atual.append(c)
            if not c.isspace():
                anterior = c
            i += 1
    return "".join(saida)


_RE_DECL_GLOBAL = re.compile(
    r"^\s*(function|var|let|const|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)


def extrair_globais(codigo_limpo):
    """Lista de dicts {nome, tipo, linha} das declarações no nível 0."""
    simbolos = []
    profundidade = 0
    for num, linha in enumerate(codigo_limpo.split("\n"), 1):
        if profundidade == 0:
            m = _RE_DECL_GLOBAL.match(linha)
            if m:
                simbolos.append({"nome": m.group(2), "tipo": m.group(1), "linha": num})
        profundidade += linha.count("{") - linha.count("}")
        if profundidade < 0:
            profundidade = 0
    return simbolos


def classificar_entrypoint(nome):
    """Classifica uma função global como entrypoint (ou None)."""
    if nome in ("doPost", "doGet"):
        return "web_app"
    if nome in ("onOpen", "onEdit", "onInstall"):
        return "trigger_simples"
    if nome.startswith("onOpen_") or re.match(r"^onEdit[A-Z_]", nome):
        return "trigger_nomeado"
    if nome.startswith("_"):
        return None
    return "manual"


def extrair_entrypoints(globais):
    """Entrypoints a partir dos globals: funções acionáveis pelo editor."""
    resultado = []
    for s in globais:
        if s["tipo"] != "function":
            continue
        tipo = classificar_entrypoint(s["nome"])
        if tipo:
            resultado.append({"nome": s["nome"], "tipo": tipo})
    return resultado


def extrair_identificadores(codigo_limpo):
    """Conjunto de todos os identificadores presentes no código limpo."""
    return set(re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b", codigo_limpo))


def contar_operacoes_escrita(codigo_limpo):
    """Conta chamadas de método '.op(' para cada operação monitorada."""
    contagens = {}
    for op in OPERACOES_ESCRITA:
        padrao = re.compile(r"\." + re.escape(op) + r"\s*\(")
        achados = padrao.findall(codigo_limpo)
        if op == "clear":
            # não contar '.clearContents(' como '.clear('
            achados = re.findall(r"\.clear\s*\(", codigo_limpo)
        if achados:
            contagens[op] = len(achados)
    return contagens


def analisar_fonte(fonte_bytes):
    """Análise completa de um arquivo .gs/.js. Retorna dict determinístico."""
    texto = fonte_bytes.decode("utf-8")
    limpo = limpar_codigo(texto)
    globais = extrair_globais(limpo)
    return {
        "bytes": len(fonte_bytes),
        "linhas": texto.count("\n") + (0 if texto.endswith("\n") or texto == "" else 1),
        "sha256": sha256_hex(fonte_bytes),
        "globais": globais,
        "entrypoints": extrair_entrypoints(globais),
        "identificadores": extrair_identificadores(limpo),
        "operacoes_escrita": contar_operacoes_escrita(limpo),
    }


def nome_logico_remoto(nome_arquivo):
    """Normaliza nome de arquivo remoto exportado.

    Ferramentas externas exportam arquivos Apps Script como '.js'; o editor
    usa '.gs'. Remove UMA extensão final .gs/.js e devolve o nome lógico
    (sem extensão) — 'pastore-planilha.gs.gs' vira 'pastore-planilha.gs',
    o que denuncia a dupla extensão.
    """
    for ext in (".gs", ".js"):
        if nome_arquivo.endswith(ext):
            return nome_arquivo[: -len(ext)]
    return nome_arquivo


def varrer_segredos(texto):
    """Retorna lista de padrões de segredo que casaram (sem expor o valor)."""
    achados = []
    for padrao in PADROES_SEGREDO:
        if re.search(padrao, texto):
            achados.append(padrao)
    return achados


def nome_arquivo_proibido(nome_arquivo):
    """Retorna lista de padrões proibidos que o nome do arquivo casa."""
    achados = []
    minusculo = nome_arquivo.lower()
    for padrao in PADROES_NOME_PROIBIDO:
        if re.search(padrao, minusculo):
            achados.append(padrao)
    return achados


_TIPOS_JSON = {"object": dict, "array": list, "string": str, "boolean": bool}


def _tipo_json_bate(valor, tipo):
    """Confere um valor Python contra um nome de tipo JSON Schema."""
    if tipo == "integer":
        return isinstance(valor, int) and not isinstance(valor, bool)
    if tipo == "number":
        return isinstance(valor, (int, float)) and not isinstance(valor, bool)
    esperado = _TIPOS_JSON.get(tipo)
    if esperado is None:
        return True
    if esperado is dict and isinstance(valor, bool):
        return False
    return isinstance(valor, esperado)


def validar_schema(instancia, schema, caminho="$"):
    """Validador determinístico de um subconjunto de JSON Schema.

    Cobre exatamente as palavras-chave usadas pelos contratos deste
    projeto: type (str ou lista), properties, required,
    additionalProperties, items, pattern. Sem dependência externa —
    evita exigir o pacote 'jsonschema' em ambientes onde ele pode não
    estar garantido.

    Retorna uma lista de mensagens de erro. Cada mensagem cita apenas
    caminho/nome de campo/tipo — NUNCA o valor real do dado (para não
    reproduzir conteúdo potencialmente sensível em logs/relatórios).
    Lista vazia = válido.
    """
    erros = []
    tipo = schema.get("type")
    if tipo is not None:
        tipos = tipo if isinstance(tipo, list) else [tipo]
        if not any(_tipo_json_bate(instancia, t) for t in tipos):
            erros.append(
                f"{caminho}: tipo inválido (esperado {'/'.join(tipos)}, "
                f"obtido {type(instancia).__name__})"
            )
            return erros

    if isinstance(instancia, dict):
        propriedades = schema.get("properties", {})
        for nome_campo in schema.get("required", []):
            if nome_campo not in instancia:
                erros.append(f"{caminho}: campo obrigatório ausente: '{nome_campo}'")
        if schema.get("additionalProperties") is False:
            for chave in instancia:
                if chave not in propriedades:
                    erros.append(f"{caminho}: propriedade não permitida: '{chave}'")
        for chave, subschema in propriedades.items():
            if chave in instancia:
                erros.extend(
                    validar_schema(instancia[chave], subschema, f"{caminho}.{chave}")
                )
    elif isinstance(instancia, list):
        item_schema = schema.get("items")
        if item_schema is not None:
            for indice, elemento in enumerate(instancia):
                erros.extend(
                    validar_schema(elemento, item_schema, f"{caminho}[{indice}]")
                )
    elif isinstance(instancia, str):
        padrao = schema.get("pattern")
        if padrao and not re.search(padrao, instancia):
            erros.append(f"{caminho}: valor não casa o padrão exigido")

    return erros
