"""M25.21 — a bancada clínica volta a ocupar a tela, e a contagem volta a ser
um número.

Duas falhas nasceram juntas na M25.20 e têm a mesma forma: uma estrutura nova
foi encaixada numa estrutura antiga que continuou obedecendo à própria regra.

1. **A grade de duas colunas com três filhos.**
   `.report-physician-shell` era `minmax(260px,320px) minmax(0,1fr)` com
   exatamente dois filhos (fila | bancada). A central de assinatura entrou
   como TERCEIRO filho e o posicionamento automático empurrou a bancada
   clínica inteira para a linha de baixo, na coluna ESTREITA:

       linha 1 | central (~300px) | fila (resto)
       linha 2 | BANCADA (~300px) | vazio

   Daí tudo o que foi relatado: PDF da MIR microscópico, painel do laudo
   microscópico, botões de conclusão numa coluna altíssima, nome de paciente
   descendo letra por letra e 70–80% da página em branco.

2. **O envelope adivinhado.**
   A carga tinha uma heurística única — "se vier `{itens:[...]}`, a lista é
   `itens`; senão a resposta É a lista". Os dois endpoints da M25.20 têm
   envelopes próprios: `/assinatura-externa/pendentes` devolve
   `{total, laudos}` e `/assinatura-externa/fila` devolve `{estados, itens}`.
   O primeiro virava objeto (`lista.length` → `undefined`, o texto
   "Aguardando assinatura qualificada — undefined") e o segundo perdia os
   contadores por estado.

Os testes aqui são ESTRUTURAIS de propósito. Procurar `width: 100%` no CSS
não distinguiria o conserto do disfarce: o que precisa ser verdade é que a
bancada tenha contêiner próprio em largura inteira, e não que alguém tenha
escrito uma largura em algum lugar. Por isso o arquivo:

* monta a árvore DOM de `renderPhysicianWorkspace` e verifica NINHO
  (quem é filho de quem), não texto;
* interpreta o CSS em regras (seletor, declarações, media query) e verifica
  as faixas da grade e as larguras aplicáveis à bancada;
* executa em Node as funções reais de normalização e contagem contra cinco
  payloads, inclusive respostas quebradas.

O terceiro grupo é regressão: a M25.20 inteira precisa continuar de pé, e
nada de clínico pode ter sido tocado por uma tarefa de UX.

Somente dados sintéticos.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_JS_PATH = PANEL_ROOT / "js" / "report-workflow.js"
WORKFLOW_CSS_PATH = PANEL_ROOT / "css" / "report-workflow.css"
WORKFLOW_JS = WORKFLOW_JS_PATH.read_text()
WORKFLOW_CSS = WORKFLOW_CSS_PATH.read_text()
INDEX_HTML = (PANEL_ROOT / "index.html").read_text()


# =====================================================================
# Ferramentas de leitura estrutural
# =====================================================================


class _Node:
    """Um elemento da árvore, com classes e filhos. O suficiente para
    perguntar "quem está dentro de quem" sem depender de indentação."""

    def __init__(self, tag: str, classes: tuple[str, ...], parent=None):
        self.tag = tag
        self.classes = classes
        self.parent = parent
        self.children: list[_Node] = []
        self.slots: list[str] = []

    def descendants(self):
        for filho in self.children:
            yield filho
            yield from filho.descendants()

    def find(self, classe: str):
        for no in self.descendants():
            if classe in no.classes:
                return no
        return None

    def all_slots(self) -> set[str]:
        marcas = set(self.slots)
        for filho in self.children:
            marcas |= filho.all_slots()
        return marcas

    def has_ancestor(self, classe: str) -> bool:
        atual = self.parent
        while atual is not None:
            if classe in atual.classes:
                return True
            atual = atual.parent
        return False


class _TreeBuilder(HTMLParser):
    VOID = {"input", "img", "br", "hr", "meta", "link"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", ())
        self.atual = self.root

    def handle_starttag(self, tag, attrs):
        atributos = dict(attrs)
        classes = tuple((atributos.get("class") or "").split())
        no = _Node(tag, classes, self.atual)
        self.atual.children.append(no)
        if tag not in self.VOID:
            self.atual = no

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        no = self.atual
        while no is not self.root and no.tag != tag:
            no = no.parent
        if no is not self.root:
            self.atual = no.parent

    def handle_data(self, data):
        for marca in re.findall(r"@@SLOT:([A-Za-z0-9_]+)@@", data):
            self.atual.slots.append(marca)


def _corpo_da_funcao(fonte: str, nome: str) -> str:
    """Extrai o corpo de `function <nome>(...) { ... }` contando chaves."""

    inicio = fonte.index(f"function {nome}(")
    abre = fonte.index("{", inicio)
    profundidade = 0
    for indice in range(abre, len(fonte)):
        if fonte[indice] == "{":
            profundidade += 1
        elif fonte[indice] == "}":
            profundidade -= 1
            if profundidade == 0:
                return fonte[abre + 1 : indice]
    raise AssertionError(f"função {nome} sem fechamento")


def _arvore_do_template(corpo: str) -> _Node:
    """Transforma o template literal da função em árvore DOM.

    Cada `${chamada()}` vira um marcador textual, para que o teste possa
    perguntar em que elemento cada bloco renderizado CAI.
    """

    template = corpo[corpo.index("`") + 1 : corpo.rindex("`")]

    def marcar(match: re.Match) -> str:
        expressao = match.group(1).strip()
        chamada = re.match(r"([A-Za-z0-9_]+)\s*\(", expressao)
        nome = chamada.group(1) if chamada else "expressao"
        return f"@@SLOT:{nome}@@"

    marcado = re.sub(r"\$\{([^{}]*)\}", marcar, template)
    construtor = _TreeBuilder()
    construtor.feed(marcado)
    construtor.close()
    return construtor.root


def _regras_css(fonte: str) -> list[tuple[str, str, dict[str, str]]]:
    """CSS → lista de (media, seletor, declarações).

    Parser mínimo: este arquivo não usa aninhamento nativo nem `@supports`,
    só regras simples e blocos `@media`.
    """

    limpo = re.sub(r"/\*.*?\*/", "", fonte, flags=re.S)
    regras: list[tuple[str, str, dict[str, str]]] = []

    def declaracoes(bloco: str) -> dict[str, str]:
        saida: dict[str, str] = {}
        for pedaco in bloco.split(";"):
            if ":" not in pedaco:
                continue
            propriedade, _, valor = pedaco.partition(":")
            saida[propriedade.strip()] = valor.strip()
        return saida

    def varrer(texto: str, media: str) -> None:
        posicao = 0
        while True:
            abre = texto.find("{", posicao)
            if abre == -1:
                return
            prefixo = texto[posicao:abre].strip()
            profundidade = 0
            fim = abre
            for indice in range(abre, len(texto)):
                if texto[indice] == "{":
                    profundidade += 1
                elif texto[indice] == "}":
                    profundidade -= 1
                    if profundidade == 0:
                        fim = indice
                        break
            bloco = texto[abre + 1 : fim]
            if prefixo.startswith("@media"):
                varrer(bloco, prefixo)
            elif prefixo.startswith("@"):
                pass
            else:
                for seletor in prefixo.split(","):
                    if seletor.strip():
                        regras.append(
                            (media, seletor.strip(), declaracoes(bloco))
                        )
            posicao = fim + 1

    varrer(limpo, "")
    return regras


REGRAS = _regras_css(WORKFLOW_CSS)


def _declaracao(seletor: str, propriedade: str, media: str = "") -> str | None:
    valor = None
    for regra_media, regra_seletor, decls in REGRAS:
        if regra_seletor == seletor and regra_media == media:
            valor = decls.get(propriedade, valor)
    return valor


# =====================================================================
# 1. A bancada saiu da coluna estreita — verificado por NINHO, não por texto
# =====================================================================


ARVORE_WORKSPACE = _arvore_do_template(
    _corpo_da_funcao(WORKFLOW_JS, "renderPhysicianWorkspace")
)


def test_workspace_tem_dois_niveis_e_nao_tres_irmaos_numa_grade():
    shell = ARVORE_WORKSPACE.find("report-physician-shell")
    assert shell is not None, "o shell da conta médica sumiu"
    filhos = [filho for filho in shell.children]
    assert len(filhos) == 2, (
        "o shell voltou a ter mais de dois filhos diretos — foi exatamente "
        "assim que a bancada caiu na coluna estreita na M25.20"
    )
    classes = {classe for filho in filhos for classe in filho.classes}
    assert "report-physician-summary" in classes
    assert "report-physician-workbench" in classes


def test_bancada_clinica_nao_esta_dentro_da_coluna_da_assinatura():
    workbench = ARVORE_WORKSPACE.find("report-physician-workbench")
    assert workbench is not None
    assert not workbench.has_ancestor("report-physician-summary"), (
        "a bancada voltou a ser filha da faixa de resumo"
    )
    assert not workbench.has_ancestor("report-signature"), (
        "a bancada está dentro do cartão da central de assinatura"
    )
    assert "renderPhysicianDetail" in workbench.all_slots()


def test_central_e_fila_ficam_na_faixa_de_resumo():
    resumo = ARVORE_WORKSPACE.find("report-physician-summary")
    assert resumo is not None
    marcas = resumo.all_slots()
    assert {"renderSignatureCenter", "renderQueue"} <= marcas
    assert "renderPhysicianDetail" not in marcas, (
        "a bancada continua sendo renderizada dentro da faixa de resumo"
    )


def test_template_do_workspace_fecha_todas_as_tags():
    """Fechamento incorreto de `div` num template literal produz exatamente o
    sintoma relatado — um bloco herdando o contêiner do bloco anterior."""

    template = _corpo_da_funcao(WORKFLOW_JS, "renderPhysicianWorkspace")
    corpo = template[template.index("`") + 1 : template.rindex("`")]
    assert corpo.count("<div") == corpo.count("</div>")


# =====================================================================
# 2. A grade: o shell empilha, só a faixa de resumo tem colunas
# =====================================================================


def _faixas(valor: str) -> list[str]:
    """Divide `grid-template-columns` em faixas, respeitando `minmax(...)`."""

    faixas: list[str] = []
    atual = ""
    profundidade = 0
    for caractere in valor:
        if caractere == "(":
            profundidade += 1
        elif caractere == ")":
            profundidade -= 1
        if caractere == " " and profundidade == 0:
            if atual:
                faixas.append(atual)
                atual = ""
            continue
        atual += caractere
    if atual:
        faixas.append(atual)
    return faixas


def test_shell_da_conta_medica_e_uma_unica_coluna():
    valor = _declaracao(".report-physician-shell", "grid-template-columns")
    assert valor is not None
    assert len(_faixas(valor)) == 1, (
        f"o shell voltou a ter mais de uma coluna ({valor!r}): qualquer bloco "
        "acrescentado cairia de novo numa faixa estreita"
    )


def test_faixa_de_resumo_da_prioridade_a_meus_laudos():
    valor = _declaracao(".report-physician-summary", "grid-template-columns")
    assert valor is not None
    faixas = _faixas(valor)
    assert len(faixas) == 2
    assinatura, laudos = faixas
    # A coluna da assinatura é limitada por um teto proporcional; a de
    # "Meus laudos" fica com o resto — e é o resto que precisa ser maior.
    teto = re.search(r"(\d+)%", assinatura)
    assert teto is not None, (
        "a coluna da assinatura precisa de teto relativo, não de pixel fixo"
    )
    assert 25 <= int(teto.group(1)) <= 40
    assert "1fr" in laudos


def test_grade_clinica_usa_minmax_com_piso_zero():
    """`minmax(0, …)` é o que permite o filho encolher sem estourar a grade —
    e é o que falta quando o texto começa a quebrar letra por letra."""

    for seletor in (".report-clinical-split", ".report-exam-context"):
        valor = _declaracao(seletor, "grid-template-columns")
        assert valor is not None, seletor
        assert "minmax(0," in valor.replace(" ", ""), (seletor, valor)


def test_nenhuma_largura_estreita_alcanca_a_bancada():
    """Nenhuma regra pode fixar a bancada (ou seus painéis) em uma faixa
    estreita — nem por `width`, nem por `max-width`, nem por `flex-basis`."""

    alvos = (
        "report-physician-workbench",
        "report-clinical-panel",
        "report-clinical-split",
        "report-source-pane",
        "report-work-pane",
    )
    problemas = []
    for media, seletor, decls in REGRAS:
        if not any(alvo in seletor for alvo in alvos):
            continue
        for propriedade in ("width", "max-width", "flex-basis", "flex"):
            valor = decls.get(propriedade)
            if not valor:
                continue
            for numero in re.findall(r"(\d+(?:\.\d+)?)px", valor):
                if float(numero) < 400:
                    problemas.append((media, seletor, propriedade, valor))
    assert not problemas, f"largura estreita aplicada à bancada: {problemas}"


def test_pdf_da_mir_tem_altura_de_leitura_no_desktop():
    alturas = [
        decls.get("min-height")
        for media, seletor, decls in REGRAS
        if seletor == ".report-pdf-frame" and "min-width" in media
    ]
    assert alturas, "nenhuma altura ampliada de PDF para desktop"
    assert any(
        int(re.sub(r"\D", "", altura)) >= 500 for altura in alturas if altura
    ), f"o visualizador do exame continua baixo no desktop: {alturas}"


# =====================================================================
# 3. Texto de paciente não quebra letra por letra
# =====================================================================


def test_nenhum_word_break_break_all_no_workspace():
    """A verificação é sobre DECLARAÇÕES aplicadas, não sobre o texto do
    arquivo — os comentários explicam por que `break-all` está proibido e
    citam o nome dele."""

    aplicadas = [
        (media, seletor, valor)
        for media, seletor, decls in REGRAS
        for propriedade, valor in decls.items()
        if propriedade in ("word-break", "line-break", "overflow-wrap")
        and "break-all" in valor
    ]
    assert not aplicadas, aplicadas


def test_dados_de_paciente_quebram_por_palavra():
    """`overflow-wrap: anywhere` também zera a largura mínima do elemento: em
    contêiner estreito ele verticaliza o nome. Nos seletores que carregam
    nome, endereço e contexto de paciente, a quebra é por palavra."""

    seletores = (
        ".report-item-name",
        ".report-context-main",
        ".report-context-meta",
        ".report-signature-name",
        ".report-queue-item span",
        ".report-operation-row span",
        ".report-exam-pick span",
        ".report-documents-list span",
        ".report-location-readonly strong",
    )
    for seletor in seletores:
        valor = _declaracao(seletor, "overflow-wrap")
        assert valor is not None, f"{seletor} perdeu a regra de quebra"
        assert valor == "break-word", (seletor, valor)


def test_cabecalho_do_paciente_nao_tem_mais_coluna_fixa_de_rotulos():
    """A coluna de 96px para o rótulo deixava ~60px para o valor dentro de um
    cartão estreito. O cartão passou a ter um protagonista e linhas de apoio."""

    assert _declaracao(".report-exam-context > article > div",
                       "grid-template-columns") is None
    corpo = _corpo_da_funcao(WORKFLOW_JS, "renderExamAndLocation")
    assert "report-context-main" in corpo
    assert "report-context-meta" in corpo
    # Nenhum dado do cabeçalho pode ter sumido junto com os rótulos.
    for campo in ("full_name", "date_of_birth", "public_code",
                  "clinical_indication", "endereco"):
        assert campo in corpo, f"{campo} sumiu do cabeçalho do paciente"


def test_conclusoes_ficam_em_grade_e_nao_em_coluna_infinita():
    display = _declaracao(".report-chip-grid", "display")
    colunas = _declaracao(".report-chip-grid", "grid-template-columns")
    assert display == "grid"
    assert colunas is not None and "minmax(" in colunas
    largura = re.search(r"minmax\((\d+)px", colunas)
    assert largura is not None
    # Faixa larga o bastante para a sigla respirar, estreita o bastante para
    # caber 3–4 colunas no painel do laudo em 1440px+.
    assert 120 <= int(largura.group(1)) <= 200


def test_botoes_de_conclusao_mantem_alvo_de_toque():
    altura = _declaracao(".report-conclusion-chip", "min-height")
    assert altura is not None and int(re.sub(r"\D", "", altura)) >= 44


# =====================================================================
# 4. A contagem é sempre um inteiro — verificado executando o código real
# =====================================================================


NODE = shutil.which("node")


def _rodar_em_node(script: str) -> str:
    resultado = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(PANEL_ROOT),
    )
    assert resultado.returncode == 0, resultado.stderr
    return resultado.stdout.strip()


def test_envelope_dos_endpoints_da_m25_20_e_declarado():
    """A causa do `undefined` foi a heurística de envelope, não o `<h3>`."""

    assert "PAYLOAD_ENVELOPES" in WORKFLOW_JS
    trecho = WORKFLOW_JS[WORKFLOW_JS.index("const PAYLOAD_ENVELOPES"):]
    trecho = trecho[: trecho.index("}")]
    assert "signaturePending" in trecho and "laudos" in trecho
    assert "deliveryQueue" in trecho


@pytest.mark.skipif(NODE is None, reason="node indisponível neste ambiente")
def test_contagem_da_central_nunca_vira_undefined_nan_ou_null():
    """Executa as funções REAIS do painel contra os cinco cenários exigidos:
    0, 1, 2+, payload sem contagem e falha parcial de carregamento."""

    fonte = WORKFLOW_JS_PATH.read_text()
    envelopes = fonte[fonte.index("const PAYLOAD_ENVELOPES"):]
    envelopes = envelopes[: envelopes.index("};") + 2]
    unwrap = "function " + _extrair_funcao_completa(fonte, "unwrapPayload")
    pending = "function " + _extrair_funcao_completa(
        fonte, "pendingSignatureList"
    )

    cenarios = {
        "vazio": {"total": 0, "laudos": []},
        "um": {"total": 1, "laudos": [{"document_id": "d1"}]},
        "varios": {
            "total": 3,
            "laudos": [{"document_id": f"d{i}"} for i in range(3)],
        },
        "sem_contagem": {"laudos": [{"document_id": "d1"}]},
        "resposta_quebrada": None,
        "envelope_errado": {"itens": [{"document_id": "d1"}]},
        "lista_crua": [{"document_id": "d1"}],
    }

    script = f"""
{envelopes}
const state = {{ signaturePending: [] }};
{unwrap}
{pending}
const cenarios = {json.dumps(cenarios)};
const saida = {{}};
for (const [nome, payload] of Object.entries(cenarios)) {{
  state.signaturePending = unwrapPayload("signaturePending", payload);
  const total = pendingSignatureList().length;
  saida[nome] = {{
    total,
    titulo: `Aguardando assinatura qualificada — ${{total}}`,
    inteiro: Number.isInteger(total),
  }};
}}
console.log(JSON.stringify(saida));
"""
    resultado = json.loads(_rodar_em_node(script))

    for nome, valores in resultado.items():
        assert valores["inteiro"], (nome, valores)
        assert valores["total"] >= 0, (nome, valores)
        for proibido in ("undefined", "NaN", "null"):
            assert proibido not in valores["titulo"], (nome, valores)

    assert resultado["vazio"]["total"] == 0
    assert resultado["um"]["total"] == 1
    assert resultado["varios"]["total"] == 3
    assert resultado["sem_contagem"]["total"] == 1
    assert resultado["resposta_quebrada"]["total"] == 0
    # Envelope desconhecido é lista vazia, nunca um objeto travestido de lista.
    assert resultado["envelope_errado"]["total"] == 0
    assert resultado["lista_crua"]["total"] == 0


def _extrair_funcao_completa(fonte: str, nome: str) -> str:
    inicio = fonte.index(f"function {nome}(")
    abre = fonte.index("{", inicio)
    profundidade = 0
    for indice in range(abre, len(fonte)):
        if fonte[indice] == "{":
            profundidade += 1
        elif fonte[indice] == "}":
            profundidade -= 1
            if profundidade == 0:
                return fonte[inicio + len("function ") : indice + 1]
    raise AssertionError(nome)


def test_titulo_da_central_nao_concatena_contagem_no_h3():
    corpo = _corpo_da_funcao(WORKFLOW_JS, "renderSignatureCenter")
    titulo = re.search(r"<h3[^>]*>(.*?)</h3>", corpo, flags=re.S)
    assert titulo is not None
    assert "${" not in titulo.group(1), (
        "a contagem voltou para dentro do título; ela é um selo próprio"
    )
    assert "Aguardando assinatura qualificada" in titulo.group(1)


def test_estado_vazio_da_central_e_explicito():
    corpo = _corpo_da_funcao(WORKFLOW_JS, "renderSignatureCenter")
    assert "Nenhum laudo aguardando assinatura." in corpo


def test_fila_de_entrega_recebe_o_objeto_inteiro_com_os_estados():
    """A heurística antiga acertava `itens` e jogava fora `estados` — a fila
    administrativa perdia os contadores e listava sempre vazio."""

    corpo = _corpo_da_funcao(WORKFLOW_JS, "unwrapPayload")
    assert "PAYLOAD_ENVELOPES" in corpo
    corpo_fila = _corpo_da_funcao(WORKFLOW_JS, "renderDeliveryQueue")
    assert "fila.estados" in corpo_fila and "fila.itens" in corpo_fila


# =====================================================================
# 5. Regressão: a M25.20 inteira continua de pé
# =====================================================================


@pytest.mark.parametrize("marca", [
    "renderSignatureCenter",
    "renderSignatureItem",
    "renderSignatureUpload",
    "renderSignatureReview",
    "renderDeliveryQueue",
    "/laudos/assinatura-externa/pendentes",
    "/laudos/assinatura-externa/baixar",
    "/laudos/assinatura-externa/enviar",
    "/laudos/assinatura-externa/confirmar",
    "/laudos/assinatura-externa/fila",
    "data-signature-all",
    "data-signature-download",
    "data-signature-upload",
    "data-signature-confirm",
    "data-signature-discard",
    "data-delivery-filter",
])
def test_central_de_assinatura_externa_preservada(marca):
    assert marca in WORKFLOW_JS


def test_upload_continua_aceitando_pdf_e_zip():
    corpo = _corpo_da_funcao(WORKFLOW_JS, "renderSignatureUpload")
    assert "multiple" in corpo
    assert ".pdf" in corpo and ".zip" in corpo
    assert "application/zip" in corpo


def test_alvos_de_toque_de_44px_continuam_no_css():
    for seletor in (".report-signature-item", ".report-signature-actions .m15-btn"):
        valor = _declaracao(seletor, "min-height")
        assert valor is not None, seletor
        assert int(re.sub(r"\D", "", valor)) >= 44, (seletor, valor)


def test_nada_de_clinico_foi_tocado():
    """A missão é UX. Conclusões, complementos BD, confirmações e o texto do
    laudo continuam vindo do servidor e das constantes existentes."""

    assert "ASSINAR E LIBERAR" in WORKFLOW_JS
    assert "PUBLICAR ADENDO" in WORKFLOW_JS
    assert "PERSONALIZADO" in WORKFLOW_JS
    assert "catalog.conclusoes" in WORKFLOW_JS
    assert "catalog.complementos_bd" in WORKFLOW_JS
    # Nenhuma conclusão pode estar escrita no navegador.
    assert "DVO Leve" not in WORKFLOW_JS
    assert "RBD+" not in WORKFLOW_JS


def test_assets_versionados_para_forcar_recarga():
    """Sem trocar a query o navegador da médica continuaria com o CSS quebrado
    em cache — o conserto existiria no servidor e não na tela dela."""

    for arquivo in ("report-workflow.css", "report-workflow.js"):
        marcas = re.findall(rf"{re.escape(arquivo)}\?v=(\d+)", INDEX_HTML)
        assert marcas, arquivo
        assert all(int(marca) >= 2026081002 for marca in marcas), (
            arquivo, marcas
        )
