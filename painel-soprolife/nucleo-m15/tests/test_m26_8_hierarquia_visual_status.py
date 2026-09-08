"""M26.8 — a etapa do exame precisa ser reconhecível pelo olhar.

A Dra. Ana relatou que lia cartão por cartão para saber o que faltava laudar,
o que faltava assinar e o que já estava assinado. A auditoria do código
encontrou por quê, e encontrou algo pior que "os cartões são parecidos":

* dos oito estados de `report_documents.status`, só TRÊS tinham regra de cor
  de selo (`assinatura_pendente`, `assinado` e `liberado`); os outros cinco
  caíam no mesmo `--sky-soft` do padrão;
* o fundo do cartão era branco em todos, em qualquer etapa, e não havia faixa
  lateral nenhuma;
* e a terceira regra pintava de **VERDE** justamente `liberado` — cujo próprio
  rótulo diz "Concluído — AGUARDANDO assinatura qualificada" — com o mesmo
  `--ok-soft` de `assinado`. Na fila, um laudo que ainda precisava ir para o
  certificado da médica ficava com a cor de documento pronto.

Os dois primeiros pontos são desconforto. O terceiro é uma afirmação falsa
sobre assinatura, feita em cor, num sistema de saúde.

Estes testes existem para que a correção não dependa de alguém lembrar da
regra. Eles verificam, nesta ordem:

1. **cobertura** — todo estado que EXISTE no backend tem família declarada no
   frontend, lendo as constantes reais de `app/models.py` e
   `app/routers/reports.py`. Um estado novo no banco quebra o teste antes de
   chegar à tela;
2. **a regra do verde** — executando as funções REAIS em Node, o conjunto de
   estados que recebem família verde é exatamente a allowlist de estados
   pós-assinatura. Não é "liberado não é verde": é "nada além destes é";
3. **não depender só de cor** — todo selo sai com ícone, texto por extenso e
   contraste calculado (WCAG) contra o próprio fundo;
4. **regressão** — os estados, os rótulos, as filas e o RBAC continuam
   exatamente como estavam. Esta é uma missão de UI: nada de clínico,
   nenhuma regra e nenhum estado podem ter mudado.

Somente dados sintéticos.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest
from sqlalchemy import select

from app.models import (
    ASSINADO_STATUS_VALUES,
    QUALIFIED_SIGNATURE_STATUSES,
    STATUS_LAUDO_VALUES,
    User,
)
from app.routers.reports import FILA_ROTULOS
from app.security import issue_token, user_effective_roles

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_JS_PATH = PANEL_ROOT / "js" / "report-workflow.js"
WORKFLOW_CSS_PATH = PANEL_ROOT / "css" / "report-workflow.css"
WORKFLOW_JS = WORKFLOW_JS_PATH.read_text()
WORKFLOW_CSS = WORKFLOW_CSS_PATH.read_text()

NODE = shutil.which("node")

FAMILIAS = (
    "warning", "info", "signature", "success", "success-muted", "danger",
    "neutral",
)
FAMILIAS_VERDES = ("success", "success-muted")


# =====================================================================
# Ferramentas
# =====================================================================


def _corpo_da_funcao(fonte: str, nome: str) -> str:
    """Extrai `function <nome>(...) { ... }` contando chaves."""

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


def _funcao_completa(fonte: str, nome: str) -> str:
    """A declaração inteira, `function nome(...) { ... }`."""

    inicio = fonte.index(f"function {nome}(")
    abre = fonte.index("{", inicio)
    profundidade = 0
    for indice in range(abre, len(fonte)):
        if fonte[indice] == "{":
            profundidade += 1
        elif fonte[indice] == "}":
            profundidade -= 1
            if profundidade == 0:
                return fonte[inicio : indice + 1]
    raise AssertionError(f"função {nome} sem fechamento")


def _bloco_literal(fonte: str, declaracao: str) -> str:
    """Recorta `const X = { ... };` inteiro, contando chaves."""

    inicio = fonte.index(declaracao)
    abre = fonte.index("{", inicio)
    profundidade = 0
    for indice in range(abre, len(fonte)):
        if fonte[indice] == "{":
            profundidade += 1
        elif fonte[indice] == "}":
            profundidade -= 1
            if profundidade == 0:
                return fonte[inicio : indice + 2]
    raise AssertionError(f"{declaracao} sem fechamento")


def _regras_css(fonte: str) -> list[tuple[str, str, dict[str, str]]]:
    """CSS → (media, seletor, declarações). Parser mínimo, como na M25.21."""

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
                        regras.append((media, seletor.strip(), declaracoes(bloco)))
            posicao = fim + 1

    varrer(limpo, "")
    return regras


REGRAS = _regras_css(WORKFLOW_CSS)


def _tokens_da_familia(familia: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for media, seletor, decls in REGRAS:
        if seletor == f".report-family-{familia}" and not media:
            tokens.update(
                {k: v for k, v in decls.items() if k.startswith("--report-family-")}
            )
    return tokens


def _rgb(hexa: str) -> tuple[float, float, float]:
    valor = hexa.strip().lstrip("#")
    if len(valor) == 3:
        valor = "".join(c * 2 for c in valor)
    return tuple(int(valor[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore


def _luminancia(hexa: str) -> float:
    def canal(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (canal(c) for c in _rgb(hexa))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contraste(a: str, b: str) -> float:
    la, lb = _luminancia(a), _luminancia(b)
    claro, escuro = max(la, lb), min(la, lb)
    return (claro + 0.05) / (escuro + 0.05)


def _matiz_e_saturacao(hexa: str) -> tuple[float, float]:
    r, g, b = _rgb(hexa)
    maior, menor = max(r, g, b), min(r, g, b)
    delta = maior - menor
    if delta == 0:
        return 0.0, 0.0
    if maior == r:
        matiz = 60 * (((g - b) / delta) % 6)
    elif maior == g:
        matiz = 60 * ((b - r) / delta + 2)
    else:
        matiz = 60 * ((r - g) / delta + 4)
    claridade = (maior + menor) / 2
    saturacao = delta / (1 - abs(2 * claridade - 1)) if claridade not in (0, 1) else 0.0
    return matiz, saturacao


def _rodar_em_node(script: str) -> str:
    resultado = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert resultado.returncode == 0, resultado.stderr
    return resultado.stdout.strip()


def _preludio_js() -> str:
    """As funções REAIS do painel, isoladas o bastante para rodar em Node.

    Nada é reescrito aqui: os trechos são recortados do arquivo de produção.
    Um teste que copiasse o mapa provaria apenas que a cópia está certa.
    """

    return "\n".join(
        [
            'function esc(v){return String(v==null?"":v)'
            ".replace(/&/g,'&amp;').replace(/</g,'&lt;')"
            ".replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}",
            # As sete constantes de família, recortadas do arquivo real.
            "\n".join(
                re.findall(r'^  const FAMILY_[A-Z_]+ = "[a-z-]+";', WORKFLOW_JS, re.M)
            ),
            _bloco_literal(WORKFLOW_JS, "const STATUS_FAMILIES = "),
            _bloco_literal(WORKFLOW_JS, "const FAMILY_ICON_PATHS = "),
            _funcao_completa(WORKFLOW_JS, "statusFamily"),
            _funcao_completa(WORKFLOW_JS, "familyClass"),
            _funcao_completa(WORKFLOW_JS, "familyIcon"),
            _funcao_completa(WORKFLOW_JS, "statusChip"),
            _funcao_completa(WORKFLOW_JS, "statusCardClass"),
            _funcao_completa(WORKFLOW_JS, "statusLabel"),
            "",
        ]
    )


def _bloco_const(fonte: str, nome: str) -> str:
    """Recorta `const <nome> = "...";` do arquivo real."""

    achado = re.search(rf'^  const {nome} = "[^"]*";', fonte, re.M)
    assert achado, nome
    return achado.group(0)


def _familias(consultas: list[tuple[str, str]]) -> dict[str, str]:
    """`[(dominio, estado)]` → família, decidido pelo código de produção."""

    script = (
        _preludio_js()
        + f"\nconst consultas = {json.dumps(consultas)};\n"
        + 'const saida = {};\nfor (const [d, e] of consultas) '
        + '{ saida[d + "/" + e] = statusFamily(d, e); }\n'
        + "console.log(JSON.stringify(saida));"
    )
    return json.loads(_rodar_em_node(script))


requer_node = pytest.mark.skipif(NODE is None, reason="node indisponível")


# =====================================================================
# 1. Cobertura: nenhum estado do backend fica sem família
# =====================================================================


DOMINIOS_DO_BACKEND = {
    "laudo": tuple(STATUS_LAUDO_VALUES),
    "entrega": tuple(FILA_ROTULOS),
    "assinado": tuple(ASSINADO_STATUS_VALUES),
    "qualificada": tuple(QUALIFIED_SIGNATURE_STATUSES),
}


@requer_node
@pytest.mark.parametrize("dominio", sorted(DOMINIOS_DO_BACKEND))
def test_todo_estado_real_do_backend_tem_familia_declarada(dominio):
    """A lista de estados vem do BANCO, não de uma cópia no teste.

    Se alguém acrescentar um status em `app/models.py` e esquecer o painel,
    é aqui que aparece — e não numa fila em produção, em cinza, para a
    médica descobrir sozinha o que aquilo significa."""

    estados = DOMINIOS_DO_BACKEND[dominio]
    familias = _familias([(dominio, estado) for estado in estados])
    faltando = [
        estado
        for estado in estados
        if familias[f"{dominio}/{estado}"] == "neutral"
    ]
    assert not faltando, (
        f"estados de `{dominio}` sem família declarada em STATUS_FAMILIES: "
        f"{faltando}"
    )


@requer_node
def test_nenhuma_familia_inventada():
    consultas = [
        (dominio, estado)
        for dominio, estados in DOMINIOS_DO_BACKEND.items()
        for estado in estados
    ]
    usadas = set(_familias(consultas).values())
    assert usadas <= set(FAMILIAS), usadas - set(FAMILIAS)


def test_cada_dominio_tem_o_proprio_mapa():
    """`rascunho` existe no ciclo do laudo E no da assinatura qualificada.

    Num mapa único um dos dois herdaria a cor do outro em silêncio — que é a
    forma exata do defeito que esta etapa corrige."""

    bloco = _bloco_literal(WORKFLOW_JS, "const STATUS_FAMILIES = ")
    for dominio in DOMINIOS_DO_BACKEND:
        assert re.search(rf"^\s{{4}}{dominio}: \{{", bloco, re.M), dominio
    assert "rascunho" in STATUS_LAUDO_VALUES
    assert "rascunho" in QUALIFIED_SIGNATURE_STATUSES


# =====================================================================
# 2. A REGRA: verde é reservado a documentos já assinados
# =====================================================================


# Os únicos estados, em todo o sistema, que podem receber família verde.
# Cada um é um ponto em que existe PDF ASSINADO recebido e aceito, ou uma
# etapa posterior a isso. Acrescentar um item aqui é uma decisão sobre
# assinatura, não sobre cor.
VERDE_PERMITIDO = {
    # `report_documents.status`: o PDF assinado voltou e passou nas guardas
    # documentais do servidor.
    ("laudo", "assinado"),
    # Fila de entrega: derivado de `recebido_assinado` ou
    # `validado_externamente` (ver `_FILA_POR_ASSINADO`).
    ("entrega", "pronto_para_entrega"),
    ("entrega", "entregue"),
    # `external_signed_documents.status`.
    ("assinado", "recebido_assinado"),
    ("assinado", "validado_externamente"),
    ("assinado", "entregue"),
    # VIDaaS: assinatura qualificada ICP-Brasil concluída.
    ("qualificada", "assinado_liberado"),
}


@requer_node
def test_verde_e_exatamente_a_allowlist_de_estados_pos_assinatura():
    """O teste central da M26.8.

    Não pergunta "liberado deixou de ser verde?" — pergunta "o que ficou
    verde?", contra a lista fechada dos estados em que existe assinatura. É a
    diferença entre corrigir um caso e tornar a classe de erro impossível."""

    consultas = [
        (dominio, estado)
        for dominio, estados in DOMINIOS_DO_BACKEND.items()
        for estado in estados
    ]
    familias = _familias(consultas)
    verdes = {
        tuple(chave.split("/", 1))
        for chave, familia in familias.items()
        if familia in FAMILIAS_VERDES
    }
    assert verdes == VERDE_PERMITIDO, (
        "a fronteira do verde mudou. Entraram: "
        f"{sorted(verdes - VERDE_PERMITIDO)}; saíram: "
        f"{sorted(VERDE_PERMITIDO - verdes)}"
    )


@requer_node
@pytest.mark.parametrize(
    "dominio,estado,esperada",
    [
        # A fila da médica, na ordem em que ela lê.
        ("laudo", "atribuido", "warning"),
        ("laudo", "em_elaboracao", "info"),
        # "Laudado — aguardando assinatura qualificada". Congelado ≠ assinado.
        ("laudo", "assinatura_pendente", "signature"),
        # "Concluído — aguardando assinatura qualificada". ERA VERDE.
        ("laudo", "liberado", "signature"),
        ("laudo", "assinado", "success"),
        # Legados M24A: todos anteriores à assinatura.
        ("laudo", "rascunho", "info"),
        ("laudo", "em_revisao", "info"),
        ("laudo", "finalizado", "signature"),
        # Fila administrativa de entrega.
        ("entrega", "aguardando_laudo", "warning"),
        ("entrega", "aguardando_assinatura", "signature"),
        ("entrega", "assinado_recebido_validacao_pendente", "danger"),
        ("entrega", "pronto_para_entrega", "success"),
        ("entrega", "entregue", "success-muted"),
        # Documento assinado recebido.
        ("assinado", "em_conferencia", "info"),
        ("assinado", "recusado", "danger"),
    ],
)
def test_familia_de_cada_estado_real(dominio, estado, esperada):
    familias = _familias([(dominio, estado)])
    assert familias[f"{dominio}/{estado}"] == esperada


@requer_node
def test_estado_desconhecido_cai_em_neutro_e_nunca_em_verde():
    """Um status novo no banco não pode aparecer como assinado só porque
    ninguém atualizou o painel."""

    consultas = [
        ("laudo", "estado_que_nao_existe"),
        ("laudo", ""),
        ("entrega", "inventado"),
        ("dominio_inexistente", "assinado"),
        # `toString` e `constructor` existem em todo objeto JS: sem
        # `hasOwnProperty`, o mapa devolveria uma função como se fosse a
        # família, e `familia || NEUTRAL` deixaria passar a string dela.
        ("laudo", "constructor"),
        ("laudo", "toString"),
        ("laudo", "__proto__"),
    ]
    familias = _familias(consultas)
    for chave, familia in familias.items():
        assert familia == "neutral", (chave, familia)


@requer_node
def test_nenhum_estado_anterior_a_assinatura_e_verde():
    """A leitura direta do pedido da Dra. Ana, estado por estado."""

    anteriores = [
        ("laudo", "atribuido"), ("laudo", "em_elaboracao"),
        ("laudo", "assinatura_pendente"), ("laudo", "liberado"),
        ("laudo", "rascunho"), ("laudo", "em_revisao"),
        ("laudo", "finalizado"),
        ("entrega", "aguardando_laudo"), ("entrega", "aguardando_assinatura"),
        ("entrega", "assinado_recebido_validacao_pendente"),
        ("assinado", "em_conferencia"),
        ("assinado", "recebido_validacao_pendente"),
        ("assinado", "recusado"),
    ]
    familias = _familias(anteriores)
    verdes = [c for c, f in familias.items() if f in FAMILIAS_VERDES]
    assert not verdes, f"estado sem assinatura pintado de verde: {verdes}"


# =====================================================================
# 3. A cor no CSS corresponde à família (e verde é verde de verdade)
# =====================================================================


def test_as_sete_familias_existem_com_os_cinco_tokens():
    for familia in FAMILIAS:
        tokens = _tokens_da_familia(familia)
        assert tokens, f".report-family-{familia} não existe no CSS"
        for token in (
            "--report-family-band", "--report-family-bg",
            "--report-family-chip-bg", "--report-family-chip-line",
            "--report-family-ink",
        ):
            assert token in tokens, (familia, token)


@pytest.mark.parametrize(
    "familia,faixa_matiz",
    [
        ("warning", (25, 60)),      # âmbar
        ("info", (190, 245)),       # azul
        ("signature", (250, 300)),  # roxo/lilás
        ("success", (120, 175)),    # verde
        ("success-muted", (120, 175)),
        ("danger", (-20, 20)),      # vermelho, em volta do zero
    ],
)
def test_a_faixa_lateral_de_cada_familia_tem_o_matiz_prometido(familia, faixa_matiz):
    """Um `--report-family-band` roxo com nome `success` passaria em todos os
    testes de classe e mentiria na tela. O matiz é conferido no pixel."""

    banda = _tokens_da_familia(familia)["--report-family-band"]
    matiz, saturacao = _matiz_e_saturacao(banda)
    piso, teto = faixa_matiz
    if piso < 0 and matiz > 180:
        matiz -= 360
    assert piso <= matiz <= teto, (familia, banda, matiz)
    # `success-muted` é dessaturado de propósito (é histórico); as demais
    # faixas precisam de cor de verdade para funcionar como faixa.
    piso_saturacao = 0.15 if familia == "success-muted" else 0.35
    assert saturacao > piso_saturacao, (familia, banda, saturacao)


def test_nenhuma_familia_nao_verde_usa_verde():
    """A guarda no pixel: nem por engano de token, nem por copiar-e-colar."""

    problemas = []
    for familia in FAMILIAS:
        if familia in FAMILIAS_VERDES:
            continue
        for nome, valor in _tokens_da_familia(familia).items():
            if not valor.startswith("#"):
                continue
            matiz, saturacao = _matiz_e_saturacao(valor)
            if 100 <= matiz <= 180 and saturacao > 0.18:
                problemas.append((familia, nome, valor))
    assert not problemas, problemas


def test_entregue_e_da_familia_verde_mas_visivelmente_diferente_de_pronto():
    """O pedido: entregue continua verde (é necessariamente assinado) e ainda
    assim se distingue de "pronto para entrega" sem ler o texto."""

    pronto = _tokens_da_familia("success")
    entregue = _tokens_da_familia("success-muted")
    assert pronto["--report-family-band"] != entregue["--report-family-band"]
    assert pronto["--report-family-bg"] != entregue["--report-family-bg"]
    _, sat_pronto = _matiz_e_saturacao(pronto["--report-family-band"])
    _, sat_entregue = _matiz_e_saturacao(entregue["--report-family-band"])
    assert sat_entregue < sat_pronto, (
        "o verde de 'entregue' precisa ser mais suave que o de 'pronto para "
        "entrega' — é o que separa histórico de trabalho a fazer"
    )


def test_o_verde_antigo_de_liberado_nao_existe_mais_no_css():
    """`.report-liberado { background: var(--ok-soft) }` era o defeito. O
    seletor precisa ter SUMIDO, não ficado escondido atrás de outra regra."""

    seletores = {seletor for _, seletor, _ in REGRAS}
    assert ".report-liberado" not in seletores
    assert ".report-liberado-flag" not in seletores
    # E ninguém pode ter recolocado a cor por baixo do selo do estado.
    for media, seletor, decls in REGRAS:
        if seletor.startswith(".report-") and "family" not in seletor:
            fundo = decls.get("background", "")
            assert "--ok-soft" not in fundo or "locked" not in seletor, (
                media, seletor, fundo
            )


def test_conteudo_bloqueado_deixou_de_ser_verde():
    """"Conteúdo bloqueado"/"concluído" é conteúdo congelado, não assinatura."""

    for _, seletor, decls in REGRAS:
        if seletor == ".report-queue-flag.is-locked":
            assert "--ok-soft" not in decls.get("background", "")
            return
    raise AssertionError("a marca de conteúdo bloqueado sumiu da fila")


def test_o_selo_do_ativo_de_assinatura_saiu_da_paleta_de_status():
    """Ele dizia se a IMAGEM manuscrita está cadastrada e emprestava
    `report-liberado-flag` (verde) para isso. Ativo cadastrado não é
    documento assinado."""

    corpo = _corpo_da_funcao(WORKFLOW_JS, "renderSignatureAssetAdmin")
    # Só o que é EMITIDO. O comentário logo acima cita os nomes antigos de
    # propósito — é ele que explica por que o selo saiu da paleta de status.
    emitido = re.sub(r"/\*.*?\*/", "", corpo, flags=re.S)
    assert "report-liberado-flag" not in emitido
    assert "report-atribuido" not in emitido
    assert "report-asset-ok" in emitido and "report-asset-missing" in emitido


# =====================================================================
# 4. A cor não é a única informação
# =====================================================================


@requer_node
def test_todo_selo_traz_icone_e_o_texto_do_estado_por_extenso():
    consultas = [
        ("laudo", estado) for estado in STATUS_LAUDO_VALUES
    ] + [("entrega", estado) for estado in FILA_ROTULOS]
    script = (
        _preludio_js()
        + f"\nconst consultas = {json.dumps(consultas)};\n"
        + "const saida = {};\nfor (const [d, e] of consultas) {\n"
        + '  saida[d + "/" + e] = statusChip(d, e, statusLabel(e));\n}\n'
        + "console.log(JSON.stringify(saida));"
    )
    selos = json.loads(_rodar_em_node(script))
    for chave, html in selos.items():
        estado = chave.split("/", 1)[1]
        assert "<svg" in html and "report-family-icon" in html, chave
        assert 'aria-hidden="true"' in html, chave
        assert "report-status-chip-text" in html, chave
        # O texto do estado continua presente — o ícone reforça, não
        # substitui.
        texto = re.sub(r"<[^>]+>", "", html).strip()
        assert texto, chave
        assert texto not in ("", "—"), chave
        # E o gancho por estado continua, para o E2E de navegador da M24A.
        assert f"report-{estado}" in html, chave


@requer_node
def test_cada_familia_tem_um_desenho_diferente():
    """Sete cores com o mesmo ícone deixariam o daltônico onde ele estava."""

    script = (
        _preludio_js()
        + f"\nconst familias = {json.dumps(list(FAMILIAS))};\n"
        + "const saida = {};\nfor (const f of familias) "
        + "{ saida[f] = familyIcon(f); }\n"
        + "console.log(JSON.stringify(saida));"
    )
    icones = json.loads(_rodar_em_node(script))
    desenhos = [re.sub(r"<svg[^>]*>", "", html) for html in icones.values()]
    assert len(set(desenhos)) == len(FAMILIAS), "há famílias com o mesmo ícone"


@pytest.mark.parametrize("familia", FAMILIAS)
def test_contraste_do_selo_atende_wcag_aa(familia):
    """Texto pequeno sobre fundo pastel é onde a "cor suave" vira ilegível.
    4,5:1 é o mínimo AA para texto normal."""

    tokens = _tokens_da_familia(familia)
    tinta = tokens["--report-family-ink"]
    for fundo_token in ("--report-family-chip-bg", "--report-family-bg"):
        razao = _contraste(tinta, tokens[fundo_token])
        assert razao >= 4.5, (familia, fundo_token, round(razao, 2))


@pytest.mark.parametrize("familia", FAMILIAS)
def test_fundo_do_cartao_e_muito_claro_e_o_texto_normal_continua_legivel(familia):
    """"Tons pastel muito leves no card" (item 9) medido, não julgado: o
    fundo fica claro o bastante para o texto padrão do painel."""

    fundo = _tokens_da_familia(familia)["--report-family-bg"]
    assert _luminancia(fundo) > 0.82, (familia, fundo)
    assert _contraste("#132033", fundo) >= 7, familia   # --text
    assert _contraste("#0c1f3d", fundo) >= 7, familia   # --navy
    # O contexto do cartão (local, data) tem tom próprio na M26.8 justamente
    # porque `--muted` não alcançava 4,5:1 sobre estes fundos.
    assert _contraste("#54657f", fundo) >= 4.5, familia


def test_o_cartao_tem_faixa_lateral_alem_do_fundo():
    faixa = [
        decls for media, seletor, decls in REGRAS
        if seletor == ".report-status-card::before" and not media
    ]
    assert faixa, "a faixa lateral do cartão sumiu"
    assert "--report-family-band" in faixa[-1].get("background", "")
    largura = faixa[-1].get("width", "")
    assert int(re.sub(r"\D", "", largura)) >= 4, largura


def test_a_faixa_nao_e_border_left():
    """`border-color` é atalho: a regra de seleção (`.is-selected` pinta a
    borda de teal) apagaria a faixa da família junto. Foi por isso que ela
    virou um `::before`."""

    encontrou = False
    for media, seletor, decls in REGRAS:
        if seletor == ".report-status-card" and not media:
            encontrou = True
            assert "border-left" not in decls
            assert decls.get("position") == "relative"
    assert encontrou, ".report-status-card sumiu do CSS"


def test_selecao_e_hover_nao_repintam_o_fundo_do_cartao():
    """Se repintassem, o cartão sob o cursor (ou aberto) perderia a etapa —
    exatamente o sintoma relatado, reintroduzido pela interação."""

    for _, seletor, decls in REGRAS:
        if seletor in (
            ".report-queue-item:hover", ".report-operation-row:hover",
            ".report-queue-item.is-selected",
            ".report-operation-row.is-selected",
        ):
            fundo = decls.get("background", "")
            assert not fundo or "--report-family-bg" in fundo, (seletor, fundo)


def test_seguindo_o_modo_de_alto_contraste_a_faixa_sobrevive():
    assert any(
        "forced-colors" in media and seletor == ".report-status-card::before"
        for media, seletor, _ in REGRAS
    )


# =====================================================================
# 5. As telas que a médica usa aplicam a família
# =====================================================================


@pytest.mark.parametrize(
    "funcao,dominio",
    [
        ("renderQueue", "laudo"),              # "Meus laudos"
        ("renderOperationalList", "laudo"),    # acompanhamento operacional
        ("renderSignatureItem", "laudo"),      # "Assinatura externa"
        ("renderDeliveryRow", "entrega"),      # fila de entrega
    ],
)
def test_cada_fila_pinta_o_cartao_pela_familia(funcao, dominio):
    corpo = _corpo_da_funcao(WORKFLOW_JS, funcao)
    assert "statusCardClass(" in corpo, funcao
    assert f'statusCardClass("{dominio}"' in corpo, (funcao, dominio)
    assert "statusChip(" in corpo, funcao


def test_a_central_de_assinatura_declara_o_estado_que_o_servidor_garante():
    """A linha da central não traz `status` no payload
    (`_linha_assinatura_externa`); o filtro do servidor
    (`_aguardando_assinatura_externa`) garante `liberado`. A constante é a
    forma honesta de dizer isso — e amarra as duas telas ao mesmo mapa."""

    assert 'const ESTADO_DA_CENTRAL = "liberado";' in WORKFLOW_JS
    from app.routers import reports as rotas

    fonte = pathlib.Path(rotas.__file__).read_text()
    trecho = fonte[fonte.index("def _aguardando_assinatura_externa") :][:2000]
    assert "ReportDocument.status == STATUS_LAUDO_LIBERADO" in trecho


@requer_node
def test_o_rotulo_curto_da_central_e_o_mesmo_que_a_fila_administrativa_usa():
    """Todos os cartões da central estão no mesmo estado e o título da seção
    já o nomeia; repetir a frase inteira em cada linha custava 60px de altura
    por cartão num iPhone. O texto curto não é invenção desta tela — é o
    rótulo que o servidor já usa para este ponto do percurso."""

    assert (
        f'const ROTULO_CURTO_DA_CENTRAL = "{FILA_ROTULOS["aguardando_assinatura"]}";'
        in WORKFLOW_JS
    )
    # E o estado por trás dele continua sendo o mesmo, com a mesma família:
    # curto no texto, idêntico na cor.
    assert _familias([("laudo", "liberado"), ("entrega", "aguardando_assinatura")]) == {
        "laudo/liberado": "signature",
        "entrega/aguardando_assinatura": "signature",
    }


def test_o_filtro_da_fila_de_entrega_usa_a_mesma_familia():
    """Item 5: o indicador do filtro fala a língua do status. "Todos" fica
    neutro — ele não é um estado."""

    corpo = _corpo_da_funcao(WORKFLOW_JS, "renderDeliveryQueue")
    assert 'familyClass("entrega", estado.chave)' in corpo
    todos = corpo[corpo.index('data-delivery-filter=""') - 300 :]
    assert "report-family-" not in todos.split(">Todos<")[0].split("chips")[-1]


def test_somente_o_filtro_ativo_ganha_destaque_maior():
    ativo = [
        decls for _, seletor, decls in REGRAS
        if "report-delivery-chip" in seletor and "is-active" in seletor
        and "family" in seletor
    ]
    inativo = [
        decls for _, seletor, decls in REGRAS
        if "report-delivery-chip" in seletor and "::before" in seletor
    ]
    assert ativo, "o chip ativo não recebe a cor da família"
    assert inativo, "o chip inativo perdeu o ponto colorido"
    # O inativo recebe um ponto, não um fundo forte.
    assert "background" not in inativo[-1] or "band" in inativo[-1]["background"]


# =====================================================================
# 6. Regressão: nada de regra, estado ou permissão mudou
# =====================================================================


def test_os_estados_do_dominio_continuam_exatamente_os_mesmos():
    """Congelamento explícito. Esta é uma missão de UI: se um destes conjuntos
    mudou, a mudança não era de UI."""

    assert STATUS_LAUDO_VALUES == (
        "atribuido", "em_elaboracao", "assinatura_pendente", "assinado",
        "liberado", "rascunho", "em_revisao", "finalizado",
    )
    assert tuple(FILA_ROTULOS) == (
        "aguardando_laudo", "aguardando_assinatura",
        "assinado_recebido_validacao_pendente", "pronto_para_entrega",
        "entregue",
    )
    assert ASSINADO_STATUS_VALUES == (
        "em_conferencia", "recebido_validacao_pendente", "recebido_assinado",
        "validado_externamente", "entregue", "recusado",
    )


def test_os_rotulos_visiveis_dos_estados_continuam_os_mesmos():
    """A M26.8 mexeu em COR. Trocar um rótulo aqui seria trocar o que o
    sistema afirma sobre assinatura."""

    corpo = _corpo_da_funcao(WORKFLOW_JS, "statusLabel")
    for esperado in (
        '"Pendente de laudo"',
        '"Em elaboração clínica"',
        '"Laudado — aguardando assinatura qualificada"',
        '"Assinado — assinatura conferida"',
        '"Concluído — aguardando assinatura qualificada"',
    ):
        assert esperado in corpo, esperado
    assert tuple(FILA_ROTULOS.values()) == (
        "Aguardando laudo", "Aguardando assinatura",
        "Exceção técnica — documento sem aceite", "Pronto para entrega",
        "Entregue",
    )


def test_o_filtro_de_status_da_fila_medica_nao_perdeu_opcoes():
    corpo = _corpo_da_funcao(WORKFLOW_JS, "renderQueue")
    for valor in ("", "atribuido", "em_elaboracao", "assinatura_pendente",
                  "assinado", "liberado"):
        assert f'["{valor}",' in corpo, valor


def test_nenhuma_fila_mudou_de_conteudo():
    """"Não alterar o que aparece ou deixa de aparecer em cada fila."" Os
    filtros de cada lista continuam sendo os mesmos."""

    assert 'item.status === "assinatura_pendente"' in WORKFLOW_JS
    assert 'PRONTOS_PARA_ENTREGA = ["recebido_assinado", "validado_externamente"]' \
        in WORKFLOW_JS
    assert 'estado.chave !== "assinado_recebido_validacao_pendente"' in WORKFLOW_JS


@pytest.fixture()
def medico_auth(db):
    """Um usuário com papel `medico` PURO — sintético, nunca reaproveitado.

    O `auth` do conftest só conhece admin/gestor/operacional/leitura; o papel
    médico é criado aqui como na M24C, para que a asserção seja sobre o papel
    real e não sobre um admin disfarçado."""

    from app.security import (
        ROLE_MEDICO, ensure_roles_exist, get_role, hash_password,
    )

    ensure_roles_exist(db)
    user = User(
        email="medico-m26-8@teste.local",
        nome="TESTE APAGAR Médico M26.8",
        password_hash=hash_password("senha-medico-sintetica-123"),
    )
    user.roles.append(get_role(db, ROLE_MEDICO))
    db.add(user)
    db.commit()
    return {"Authorization": f"Bearer {issue_token(user.id, user.password_hash)}"}


def test_medico_continua_sem_permissoes_administrativas(client, medico_auth, db):
    """Uma etapa de UI não pode ter afrouxado RBAC."""

    usuario = db.execute(
        select(User).where(User.email == "medico-m26-8@teste.local")
    ).scalar_one()
    assert user_effective_roles(usuario) == {"medico"}
    for path in (
        "/api/v1/pessoas", "/api/v1/crm/workspace", "/api/v1/financeiro",
        "/api/v1/auditoria", "/api/v1/admin/usuarios",
    ):
        assert client.get(path, headers=medico_auth).status_code in {
            403, 404, 405,
        }, path


def test_admin_nao_herda_papel_medico(client, auth, db):
    admin = db.execute(
        select(User).where(User.email == "admin@teste.local")
    ).scalar_one()
    assert "medico" not in user_effective_roles(admin)


def test_nada_de_migration_foi_acrescentado_nesta_etapa():
    """A mudança é de cor: ela não pode ter tocado o banco."""

    versoes = PANEL_ROOT / "nucleo-m15" / "migrations" / "versions"
    assert not list(versoes.glob("*m26_8*")), "M26.8 não deveria criar migration"
    assert not list(versoes.glob("*m26.8*"))


@requer_node
def test_o_selo_da_central_e_curto_o_bastante_para_uma_linha_no_celular():
    """Guarda de altura, sem navegador: o rótulo da central precisa caber
    numa linha num cartão de ~200px de corpo útil a 360px de largura. A
    conta é grosseira de propósito — ela existe para barrar a volta de uma
    frase de 45 caracteres, não para prever a métrica exata da fonte."""

    script = (
        _preludio_js()
        + "\nconsole.log(JSON.stringify({"
        + "curto: ROTULO_CURTO_DA_CENTRAL, longo: statusLabel('liberado')}));"
    )
    rotulos = json.loads(
        _rodar_em_node(
            _bloco_const(WORKFLOW_JS, "ROTULO_CURTO_DA_CENTRAL") + "\n" + script
        )
    )
    assert len(rotulos["curto"]) <= 24, rotulos["curto"]
    # A fila clínica, onde o estado VARIA, continua com a frase inteira.
    assert len(rotulos["longo"]) > 24
