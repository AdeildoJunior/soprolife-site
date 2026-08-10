"""M25.17 — nome humano para o PDF baixado.

Até aqui o download saía como ``laudo-ESP-000017-v3-laudo_liberado.pdf``.
Quem recebe o arquivo — a médica, a recepção, o próprio paciente — não
reconhece nada ali, e uma pasta com vinte desses é indistinguível. O nome
pedido é ``Geoffrey Kirk Barnes - Assinado.pdf``.

Nome de arquivo com dado de pessoa é um lugar clássico de acidente, então a
sanitização é única e centralizada:

* os separadores de caminho e os caracteres proibidos no Windows saem;
* caracteres de controle saem — **CR e LF em especial**, porque o valor é
  interpolado num cabeçalho HTTP e newline ali é injeção de cabeçalho;
* ``.`` e ``..`` nunca podem ser o nome inteiro;
* acentos são PRESERVADOS: o cabeçalho leva `filename*` em RFC 5987, e o
  `filename` ASCII fica como reserva para clientes antigos.

Sobre a palavra "Assinado": ela descreve o arquivo que a operação entrega,
não o estado criptográfico do documento. O PDF continua declarando por
escrito que a liberação institucional não é assinatura qualificada
ICP-Brasil, e nenhum campo de status muda por causa deste nome.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

# Proibidos em Windows (< > : " / \\ | ? *) mais os separadores de caminho.
_PROIBIDOS = re.compile(r'[<>:"/\\|?*]')
# Qualquer categoria Unicode de controle, o que inclui CR, LF e NUL.
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
_ESPACOS = re.compile(r"\s+")

MAX_BASE_LEN = 120

# M25.18 — o PDF que sai da SoproLife ainda NÃO está assinado.
#
# A M25.17 chamava o laudo de "Assinado", o que descrevia o arquivo errado:
# a assinatura qualificada acontece FORA do sistema, com o certificado da
# médica. O arquivo que este endpoint entrega é o que ela vai levar para
# assinar — daí "Para assinatura". Depois de assinado externamente, o
# arquivo final é que pode se chamar "- Assinado.pdf", e quem o nomeia é o
# fluxo externo, não este código.
SUFIXO_LAUDO = "Para assinatura"
SUFIXO_MIR = "Exame técnico"
# M25.20 — o arquivo que VOLTOU assinado por fora. Este sufixo nomeia
# exatamente o que a M25.18 previu: "`- Assinado.pdf` é o nome do arquivo que
# volta depois". Ele descreve o ARQUIVO, e continua sem afirmar que a cadeia
# ICP-Brasil foi verificada por este sistema — quem responde por isso é o
# estado do documento, não o nome do download.
SUFIXO_ASSINADO = "Assinado"


def sanitize_filename_base(nome: str | None) -> str:
    """Base de nome de arquivo segura, ou string vazia se nada sobrar.

    Devolve vazio em vez de um placeholder para que quem chama decida o
    fallback — só ele sabe qual código institucional usar no lugar.
    """

    texto = _CONTROLE.sub(" ", nome or "")
    texto = _PROIBIDOS.sub(" ", texto)
    # Separadores Unicode de linha/parágrafo não são categoria de controle,
    # mas continuam sendo quebra de linha para muitos clientes.
    texto = "".join(
        " " if unicodedata.category(c) in {"Zl", "Zp"} else c for c in texto
    )
    texto = _ESPACOS.sub(" ", texto).strip()
    # Ponto no fim faz o Windows recusar o arquivo; e "." / ".." como nome
    # inteiro é travessia de diretório.
    texto = texto.strip(". ").strip()
    if texto in {"", ".", ".."}:
        return ""
    return texto[:MAX_BASE_LEN].strip()


def report_download_filename(
    *,
    patient_name: str | None,
    fallback_code: str,
    is_technical_exam: bool,
) -> str:
    """``<Paciente> - Assinado.pdf`` ou ``<Paciente> - Exame técnico.pdf``.

    Sem nome utilizável, cai no código institucional — nunca num nome
    genérico como "documento.pdf", que produziria colisões numa pasta de
    downloads.
    """

    base = sanitize_filename_base(patient_name)
    if not base:
        base = sanitize_filename_base(fallback_code) or "documento"
    sufixo = SUFIXO_MIR if is_technical_exam else SUFIXO_LAUDO
    return f"{base} - {sufixo}.pdf"


def named_download_filename(
    *, patient_name: str | None, fallback_code: str, sufixo: str
) -> str:
    """``<Paciente> - <sufixo>.pdf`` com a mesma sanitização única.

    Existe para que nenhum ponto do sistema monte esse nome à mão. Um
    `f"{nome} - Assinado.pdf"` solto perderia a sanitização e devolveria
    um nome com barra, dois-pontos ou quebra de linha para o cabeçalho HTTP.
    """

    base = sanitize_filename_base(patient_name)
    if not base:
        base = sanitize_filename_base(fallback_code) or "documento"
    return f"{base} - {sufixo}.pdf"


def content_disposition(nome_arquivo: str, *, disposition: str) -> str:
    """Cabeçalho com `filename` ASCII e `filename*` UTF-8 (RFC 5987).

    O valor ASCII é o que clientes antigos leem, e por isso passa por uma
    redução que não pode falhar. `quote` no `filename*` cobre acento e
    qualquer resto — e, como codifica percentualmente, nenhum caractere
    consegue fechar as aspas ou emendar um cabeçalho novo.
    """

    seguro = sanitize_filename_base(nome_arquivo) or "documento.pdf"
    ascii_nome = (
        unicodedata.normalize("NFKD", seguro)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    ascii_nome = ascii_nome.replace('"', "").strip() or "documento.pdf"
    utf8_nome = quote(seguro, safe="")
    return (
        f'{disposition}; filename="{ascii_nome}"; '
        f"filename*=UTF-8''{utf8_nome}"
    )
