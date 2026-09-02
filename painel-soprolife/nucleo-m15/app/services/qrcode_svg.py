"""Gerador de QR Code (modo byte, nível de correção M) em SVG puro.

Existe aqui, e não como dependência nova, por três motivos concretos:

1. O conteúdo do QR é o **link seguro do paciente**. Gerá-lo dentro do
   processo que já conhece o segredo evita mandar o token para qualquer
   biblioteca, serviço ou CDN de terceiro — o modo mais barato de vazar um
   link de resultado médico é pedir a imagem dele a um servidor alheio.
2. A saída é SVG (vetor, texto), então não entra Pillow, nem canvas, nem
   binário externo no caminho de uma resposta autenticada.
3. `requirements.lock` é o contrato de produção da API. Uma dependência a
   mais para desenhar quadradinhos pretos custaria mais em revisão e em
   superfície do que estas ~300 linhas fechadas e testadas.

Escopo deliberadamente estreito: versões 1 a 10, nível M, modo byte. É o
suficiente para 213 caracteres — o link do portal tem ~85. Conteúdo maior é
recusado, e não silenciosamente truncado.

A correção é provada em `tests/test_m26_4_portal_resultados.py` de duas
formas: um leitor independente escrito no próprio teste recupera a URL de
volta da matriz final (sempre), e a matriz é comparada módulo a módulo com o
`qrencode` do sistema quando ele está instalado.

Uma divergência conhecida e inofensiva: em símbolos versão 1 a MÁSCARA
escolhida pode diferir da do libqrencode, que pontua a regra 3 de penalidade
com uma varredura própria. Máscara é heurística de legibilidade, não de
corretude — as oito produzem símbolos decodificáveis, e o link do portal cai
sempre na versão 5, onde as duas implementações concordam.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------- tabelas
#
# Só o nível M. Cada entrada: (total de codewords, EC por bloco, blocos).
# `blocos` é uma lista de quantos codewords de DADO cada bloco carrega —
# escrita por extenso porque a forma comprimida ("2 de 38, 2 de 39") é
# exatamente onde erros de transcrição se escondem.

_VERSOES_M: dict[int, tuple[int, int, list[int]]] = {
    1: (26, 10, [16]),
    2: (44, 16, [28]),
    3: (70, 26, [44]),
    4: (100, 18, [32, 32]),
    5: (134, 24, [43, 43]),
    6: (172, 16, [27, 27, 27, 27]),
    7: (196, 18, [31, 31, 31, 31]),
    8: (242, 22, [38, 38, 39, 39]),
    9: (292, 22, [36, 36, 36, 37, 37]),
    10: (346, 26, [43, 43, 43, 43, 44]),
}

# Centros dos padrões de alinhamento por versão (a versão 1 não tem nenhum).
_ALINHAMENTO: dict[int, list[int]] = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
}

_MAX_VERSAO = 10
_EC_NIVEL_M = 0b00  # bits do nível M no bloco de formato

_ALFA = [0] * 512
_LOG = [0] * 256


def _preparar_gf() -> None:
    """Tabelas de log/antilog de GF(256), polinômio primitivo 0x11D."""

    x = 1
    for i in range(255):
        _ALFA[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _ALFA[i] = _ALFA[i - 255]


_preparar_gf()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _ALFA[_LOG[a] + _LOG[b]]


def _polinomio_gerador(grau: int) -> list[int]:
    poly = [1]
    for i in range(grau):
        novo = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            novo[j] ^= _gf_mul(coef, 1)
            novo[j + 1] ^= _gf_mul(coef, _ALFA[i])
        poly = novo
    return poly


def _ec_codewords(dados: list[int], quantidade: int) -> list[int]:
    gerador = _polinomio_gerador(quantidade)
    resto = list(dados) + [0] * quantidade
    for i in range(len(dados)):
        coef = resto[i]
        if coef == 0:
            continue
        for j, g in enumerate(gerador):
            resto[i + j] ^= _gf_mul(g, coef)
    return resto[len(dados):]


class QrCodeError(ValueError):
    """Conteúdo que não cabe no escopo suportado. Nunca trunca em silêncio."""


def _escolher_versao(tamanho: int) -> int:
    for versao in range(1, _MAX_VERSAO + 1):
        total, ec_por_bloco, blocos = _VERSOES_M[versao]
        dados_codewords = total - ec_por_bloco * len(blocos)
        bits_contagem = 8 if versao < 10 else 16
        capacidade = dados_codewords - (4 + bits_contagem + 7) // 8
        if tamanho <= capacidade:
            return versao
    raise QrCodeError(
        "Conteúdo grande demais para QR nível M até a versão 10."
    )


def _bitstream(dados: bytes, versao: int) -> list[int]:
    total, ec_por_bloco, blocos = _VERSOES_M[versao]
    capacidade_bits = (total - ec_por_bloco * len(blocos)) * 8
    bits: list[int] = []

    def escrever(valor: int, largura: int) -> None:
        for deslocamento in range(largura - 1, -1, -1):
            bits.append((valor >> deslocamento) & 1)

    escrever(0b0100, 4)                       # modo byte
    escrever(len(dados), 8 if versao < 10 else 16)
    for byte in dados:
        escrever(byte, 8)
    # Terminador: até 4 bits zero, e só se couber.
    escrever(0, min(4, capacidade_bits - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    preenchimento = (0xEC, 0x11)
    indice = 0
    while len(bits) < capacidade_bits:
        escrever(preenchimento[indice % 2], 8)
        indice += 1
    return bits


def _codewords_finais(dados: bytes, versao: int) -> list[int]:
    """Bits → codewords → blocos → EC → intercalação (a ordem da norma)."""

    bits = _bitstream(dados, versao)
    codewords = [
        int("".join(str(b) for b in bits[i:i + 8]), 2)
        for i in range(0, len(bits), 8)
    ]
    _total, ec_por_bloco, tamanhos = _VERSOES_M[versao]

    blocos_dados: list[list[int]] = []
    blocos_ec: list[list[int]] = []
    cursor = 0
    for tamanho in tamanhos:
        bloco = codewords[cursor:cursor + tamanho]
        cursor += tamanho
        blocos_dados.append(bloco)
        blocos_ec.append(_ec_codewords(bloco, ec_por_bloco))

    saida: list[int] = []
    for i in range(max(tamanhos)):
        for bloco in blocos_dados:
            if i < len(bloco):
                saida.append(bloco[i])
    for i in range(ec_por_bloco):
        for bloco in blocos_ec:
            saida.append(bloco[i])
    return saida


# ------------------------------------------------------------- a matriz


class _Matriz:
    def __init__(self, versao: int):
        self.versao = versao
        self.n = versao * 4 + 17
        self.modulos = [[0] * self.n for _ in range(self.n)]
        self.reservado = [[False] * self.n for _ in range(self.n)]

    def marcar(self, linha: int, coluna: int, valor: int) -> None:
        self.modulos[linha][coluna] = valor
        self.reservado[linha][coluna] = True


def _desenhar_funcoes(m: _Matriz) -> None:
    n = m.n

    def finder(topo: int, esquerda: int) -> None:
        for dl in range(-1, 8):
            for dc in range(-1, 8):
                linha, coluna = topo + dl, esquerda + dc
                if not (0 <= linha < n and 0 <= coluna < n):
                    continue
                borda = max(abs(dl - 3), abs(dc - 3))
                m.marcar(linha, coluna, 1 if borda in (0, 1, 3) else 0)

    finder(0, 0)
    finder(0, n - 7)
    finder(n - 7, 0)

    for i in range(8, n - 8):
        valor = 1 if i % 2 == 0 else 0
        m.marcar(6, i, valor)
        m.marcar(i, 6, valor)

    centros = _ALINHAMENTO[m.versao]
    for linha in centros:
        for coluna in centros:
            # Os três cantos já são finder pattern.
            if (linha, coluna) in ((6, 6), (6, n - 7), (n - 7, 6)):
                continue
            for dl in range(-2, 3):
                for dc in range(-2, 3):
                    borda = max(abs(dl), abs(dc))
                    m.marcar(linha + dl, coluna + dc, 1 if borda != 1 else 0)

    # Módulo escuro obrigatório.
    m.marcar(4 * m.versao + 9, 8, 1)

    # Reserva das áreas de formato (valor definitivo entra depois).
    for i in range(9):
        if not m.reservado[8][i]:
            m.marcar(8, i, 0)
        if not m.reservado[i][8]:
            m.marcar(i, 8, 0)
    for i in range(8):
        if not m.reservado[8][n - 1 - i]:
            m.marcar(8, n - 1 - i, 0)
        if not m.reservado[n - 1 - i][8]:
            m.marcar(n - 1 - i, 8, 0)

    if m.versao >= 7:
        bits = _bits_versao(m.versao)
        for i in range(18):
            bit = (bits >> i) & 1
            linha, coluna = i // 3, i % 3
            m.marcar(linha, n - 11 + coluna, bit)
            m.marcar(n - 11 + coluna, linha, bit)


def _bits_versao(versao: int) -> int:
    """BCH(18,6) do bloco de versão — divisão polinomial por 0x1F25."""

    resto = versao << 12
    for grau in range(17, 11, -1):
        if resto & (1 << grau):
            resto ^= 0x1F25 << (grau - 12)
    return (versao << 12) | resto


def _bits_formato(mascara: int) -> int:
    dados = (_EC_NIVEL_M << 3) | mascara
    resto = dados << 10
    for grau in range(14, 9, -1):
        if resto & (1 << grau):
            resto ^= 0x537 << (grau - 10)
    return ((dados << 10) | resto) ^ 0x5412


def _aplicar_formato(m: _Matriz, mascara: int) -> None:
    """Escreve as DUAS cópias do bloco de formato, na ordem da norma.

    O bit 0 é o menos significativo. A primeira cópia sobe a coluna 8 e vira
    à direita; a segunda começa na borda direita da linha 8 e termina subindo
    a coluna 8 pela borda inferior. Trocar linha por coluna aqui produz um QR
    que parece perfeito e nenhum leitor abre — foi exatamente o defeito que a
    comparação com o `qrencode` pegou.
    """

    n = m.n
    bits = _bits_formato(mascara)

    def bit(indice: int) -> int:
        return (bits >> indice) & 1

    # Cópia 1 — em volta do finder superior esquerdo.
    for i in range(6):
        m.modulos[i][8] = bit(i)
    m.modulos[7][8] = bit(6)
    m.modulos[8][8] = bit(7)
    m.modulos[8][7] = bit(8)
    for i in range(9, 15):
        m.modulos[8][14 - i] = bit(i)

    # Cópia 2 — repartida entre os outros dois finders.
    for i in range(8):
        m.modulos[8][n - 1 - i] = bit(i)
    for i in range(8, 15):
        m.modulos[n - 15 + i][8] = bit(i)
    m.modulos[n - 8][8] = 1  # módulo escuro, sempre


def _colocar_dados(m: _Matriz, codewords: list[int]) -> None:
    n = m.n
    bits = [(c >> d) & 1 for c in codewords for d in range(7, -1, -1)]
    indice = 0
    coluna = n - 1
    subindo = True
    while coluna > 0:
        if coluna == 6:  # coluna do timing vertical não entrega dado
            coluna -= 1
        alcance = range(n - 1, -1, -1) if subindo else range(n)
        for linha in alcance:
            for delta in (0, 1):
                c = coluna - delta
                if m.reservado[linha][c]:
                    continue
                m.modulos[linha][c] = bits[indice] if indice < len(bits) else 0
                indice += 1
        coluna -= 2
        subindo = not subindo


def _mascarar(valor: int, linha: int, coluna: int, mascara: int) -> int:
    if mascara == 0:
        condicao = (linha + coluna) % 2 == 0
    elif mascara == 1:
        condicao = linha % 2 == 0
    elif mascara == 2:
        condicao = coluna % 3 == 0
    elif mascara == 3:
        condicao = (linha + coluna) % 3 == 0
    elif mascara == 4:
        condicao = (linha // 2 + coluna // 3) % 2 == 0
    elif mascara == 5:
        condicao = (linha * coluna) % 2 + (linha * coluna) % 3 == 0
    elif mascara == 6:
        condicao = ((linha * coluna) % 2 + (linha * coluna) % 3) % 2 == 0
    else:
        condicao = ((linha + coluna) % 2 + (linha * coluna) % 3) % 2 == 0
    return valor ^ 1 if condicao else valor


def _penalidade(modulos: list[list[int]]) -> int:
    n = len(modulos)
    total = 0

    linhas = modulos
    colunas = [[modulos[l][c] for l in range(n)] for c in range(n)]

    for grupo in (linhas, colunas):
        for sequencia in grupo:
            # Regra 1 — corridas de 5 ou mais.
            corrida, anterior = 1, sequencia[0]
            for valor in sequencia[1:]:
                if valor == anterior:
                    corrida += 1
                else:
                    if corrida >= 5:
                        total += 3 + (corrida - 5)
                    corrida, anterior = 1, valor
            if corrida >= 5:
                total += 3 + (corrida - 5)
            # Regra 3 — 1011101 cercado por quatro claros.
            texto = "".join(str(v) for v in sequencia)
            total += 40 * texto.count("10111010000")
            total += 40 * texto.count("00001011101")

    # Regra 2 — blocos 2x2 de mesma cor.
    for linha in range(n - 1):
        for coluna in range(n - 1):
            bloco = (
                modulos[linha][coluna],
                modulos[linha][coluna + 1],
                modulos[linha + 1][coluna],
                modulos[linha + 1][coluna + 1],
            )
            if bloco[0] == bloco[1] == bloco[2] == bloco[3]:
                total += 3

    # Regra 4 — desequilíbrio de escuros.
    escuros = sum(sum(linha) for linha in modulos)
    proporcao = escuros * 100 // (n * n)
    total += 10 * (abs(proporcao - 50) // 5)
    return total


@dataclass(frozen=True)
class QrCode:
    versao: int
    mascara: int
    modulos: tuple[tuple[int, ...], ...]

    @property
    def tamanho(self) -> int:
        return len(self.modulos)


def encode(texto: str) -> QrCode:
    """Codifica `texto` (UTF-8) num QR nível M, escolhendo versão e máscara."""

    dados = texto.encode("utf-8")
    versao = _escolher_versao(len(dados))
    codewords = _codewords_finais(dados, versao)

    melhor: QrCode | None = None
    melhor_penalidade = -1
    for mascara in range(8):
        m = _Matriz(versao)
        _desenhar_funcoes(m)
        _colocar_dados(m, codewords)
        for linha in range(m.n):
            for coluna in range(m.n):
                if not m.reservado[linha][coluna]:
                    m.modulos[linha][coluna] = _mascarar(
                        m.modulos[linha][coluna], linha, coluna, mascara
                    )
        _aplicar_formato(m, mascara)
        pontos = _penalidade(m.modulos)
        if melhor is None or pontos < melhor_penalidade:
            melhor_penalidade = pontos
            melhor = QrCode(
                versao=versao,
                mascara=mascara,
                modulos=tuple(tuple(linha) for linha in m.modulos),
            )
    assert melhor is not None
    return melhor


def to_svg(texto: str, *, borda: int = 4, escala: int = 8) -> str:
    """SVG autocontido, sem script, sem fonte externa, sem referência de rede.

    `borda` é a zona silenciosa exigida pela norma (4 módulos). Reduzi-la faz
    leitores falharem em tela — é o defeito mais comum de QR gerado à mão.
    """

    if borda < 4:
        raise QrCodeError("A zona silenciosa do QR não pode ser menor que 4.")
    qr = encode(texto)
    lado = qr.tamanho + borda * 2
    partes: list[str] = []
    for linha in range(qr.tamanho):
        for coluna in range(qr.tamanho):
            if qr.modulos[linha][coluna]:
                partes.append(f"M{coluna + borda} {linha + borda}h1v1h-1z")
    caminho = "".join(partes)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {lado} {lado}" width="{lado * escala}" '
        f'height="{lado * escala}" shape-rendering="crispEdges" '
        f'role="img" aria-label="QR Code do link de resultado">'
        f'<rect width="{lado}" height="{lado}" fill="#ffffff"/>'
        f'<path d="{caminho}" fill="#0b2a3d"/>'
        f"</svg>"
    )
