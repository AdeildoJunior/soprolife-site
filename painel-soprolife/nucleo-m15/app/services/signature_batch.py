"""M25.20 — central de assinatura externa em lote.

A médica continua laudando UM a UM. Nada aqui interpreta exame, escolhe
conclusão ou decide conteúdo clínico. O que vira lote é o trabalho
burocrático que vem DEPOIS da conclusão: baixar os PDFs, levar para assinar
fora com o certificado dela, e devolver os assinados.

Três garantias sustentam o módulo:

1. **O arquivo que volta é identificado, não adivinhado.** O PDF concluído
   sai daqui com metadados carimbados; na volta, o pareamento tenta metadado,
   depois código LAU impresso, depois código de verificação. O nome do
   arquivo é pista, nunca prova — o iPhone, o assinador ou a própria médica
   podem renomeá-lo, e "parece o nome da paciente" é o jeito mais fácil de
   anexar o laudo assinado à pessoa errada.

2. **Receber não é validar.** Um PDF que parece assinado não teve a cadeia
   ICP-Brasil conferida por este código. O módulo diz "recebido"; quem diz
   "validado" é uma conferência externa registrada por alguém.

3. **Um arquivo ruim não derruba o lote.** Cada arquivo tem seu veredito.

O ZIP é transporte e contém dado médico: gerado sob demanda, em memória,
nunca em webroot, nunca em Git, nunca em log.
"""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date

from pypdf import PdfReader, PdfWriter

# --------------------------------------------------------------- carimbo

# Chaves privadas no Document Information Dictionary do PDF. São entradas
# customizadas — mecanismo previsto pelo formato — e sobrevivem ao
# incremental update que um assinador aplica ao anexar a assinatura.
#
# Nenhuma delas carrega dado clínico ou identidade de paciente: são o código
# do laudo, o código de verificação já impresso no documento, o número da
# versão e o hash do conteúdo. Tudo isso já está visível no papel.
META_REPORT_CODE = "/SoproLifeReportCode"
META_VALIDATION_CODE = "/SoproLifeValidationCode"
META_VERSION = "/SoproLifeVersion"
META_SOURCE_HASH = "/SoproLifeSourceHash"

# Marca legada da M25.8, gravada em /Keywords. Continua sendo LIDA para que
# um laudo preparado por aquele fluxo ainda seja reconhecido na volta.
LEGACY_MARKER_PREFIX = "soprolife-laudo"
_LEGACY_MARKER_RE = re.compile(
    rf"{LEGACY_MARKER_PREFIX}:(?P<code>[A-Z0-9\-]+):v(?P<version>\d+):"
    r"(?P<sha>[0-9a-f]{64})"
)

# Códigos como impressos no documento. `LAU-` seguido de seis dígitos é o
# formato emitido por `app/ids.py`; o código de verificação é alfanumérico
# maiúsculo e opaco.
_LAU_RE = re.compile(r"\bLAU-\d{6}\b")


class SignatureBatchError(ValueError):
    """Falha esperada, segura para virar erro de domínio na rota."""

    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class ReportMarkers:
    """O que foi possível ler de dentro de um PDF para identificá-lo."""

    report_code: str | None = None
    validation_code: str | None = None
    version_number: int | None = None
    source_sha256: str | None = None

    @property
    def empty(self) -> bool:
        return not (self.report_code or self.validation_code)


def stamp_signing_metadata(
    pdf: bytes,
    *,
    document_code: str,
    validation_code: str | None,
    version_number: int,
    physician_name: str,
    crm: str,
) -> bytes:
    """Carimba a identificação da SoproLife nos metadados do PDF concluído.

    `SoproLifeSourceHash` é o hash do PDF ANTES do carimbo — o hash do
    conteúdo renderizado. Não é, e não pode ser, o hash do arquivo entregue:
    um arquivo não consegue conter o próprio hash. O hash do arquivo que a
    médica leva para assinar fica no banco, na versão gravada.

    O carimbo é RELIDO antes de devolver. Sem essa prova, um escape mal
    feito só apareceria semanas depois, na hora de parear o arquivo que
    voltou — quando já não há como recarimbar o documento assinado.
    """

    source_hash = hashlib.sha256(pdf).hexdigest()
    entradas = {
        "/Title": f"Laudo de espirometria {document_code}",
        "/Author": physician_name,
        "/Subject": f"Laudo {document_code} versão {version_number} — {crm}",
        META_REPORT_CODE: document_code,
        META_VERSION: str(version_number),
        META_SOURCE_HASH: source_hash,
        # Mantém a marca da M25.8 em /Keywords: um painel intermediário que
        # ainda leia só ela continua reconhecendo o documento.
        "/Keywords": (
            f"{LEGACY_MARKER_PREFIX}:{document_code}:"
            f"v{version_number}:{source_hash}"
        ),
    }
    if validation_code:
        entradas[META_VALIDATION_CODE] = validation_code

    try:
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
        writer.add_metadata(entradas)
        buffer = io.BytesIO()
        writer.write(buffer)
    except Exception as exc:
        raise SignatureBatchError(
            "carimbo_nao_aplicado",
            "Não foi possível carimbar a identificação no PDF do laudo.",
        ) from exc

    marcado = buffer.getvalue()
    relido = read_markers_from_metadata(marcado)
    if (
        relido.report_code != document_code
        or relido.version_number != version_number
        or relido.source_sha256 != source_hash
        or (validation_code and relido.validation_code != validation_code)
    ):
        raise SignatureBatchError(
            "carimbo_nao_relido",
            "O carimbo gravado no PDF não pôde ser relido.",
        )
    return marcado


def read_markers_from_metadata(pdf: bytes) -> ReportMarkers:
    """Lê o carimbo pelos metadados do PDF. Vazio se não houver."""

    try:
        metadados = PdfReader(io.BytesIO(pdf)).metadata or {}
    except Exception:
        return ReportMarkers()

    def texto(chave: str) -> str | None:
        valor = metadados.get(chave)
        if valor is None:
            return None
        limpo = str(valor).strip()
        return limpo or None

    report_code = texto(META_REPORT_CODE)
    validation_code = texto(META_VALIDATION_CODE)
    source_hash = texto(META_SOURCE_HASH)
    versao_bruta = texto(META_VERSION)
    version_number = None
    if versao_bruta and versao_bruta.isdigit():
        version_number = int(versao_bruta)

    # Reserva: a marca legada da M25.8 em /Keywords. Só entra onde a chave
    # dedicada faltou, nunca por cima dela.
    if report_code is None or version_number is None or source_hash is None:
        achado = _LEGACY_MARKER_RE.search(texto("/Keywords") or "")
        if achado is not None:
            report_code = report_code or achado.group("code")
            version_number = version_number or int(achado.group("version"))
            source_hash = source_hash or achado.group("sha")

    return ReportMarkers(
        report_code=report_code,
        validation_code=validation_code,
        version_number=version_number,
        source_sha256=source_hash,
    )


def read_codes_from_content(
    pdf: bytes, *, validation_code_pattern: re.Pattern[str] | None = None
) -> ReportMarkers:
    """Lê os códigos IMPRESSOS no PDF — compatibilidade retroativa.

    Laudos concluídos antes da M25.20 não têm carimbo, mas sempre tiveram o
    código LAU e o código de verificação escritos no documento. Extrair o
    texto é mais frágil que ler metadado (fonte, encoding, PDF assinado com
    camada por cima), então este caminho é reserva — nunca o primeiro.
    """

    try:
        leitor = PdfReader(io.BytesIO(pdf))
        texto = "".join(pagina.extract_text() or "" for pagina in leitor.pages)
    except Exception:
        return ReportMarkers()

    achado_lau = _LAU_RE.search(texto)
    report_code = achado_lau.group(0) if achado_lau else None

    validation_code = None
    if validation_code_pattern is not None:
        achado_val = validation_code_pattern.search(texto)
        if achado_val is not None:
            validation_code = achado_val.group(0)

    return ReportMarkers(
        report_code=report_code, validation_code=validation_code
    )


# --------------------------------------------------------- ZIP de saída


def signing_filename(patient_name: str | None, *, fallback_code: str) -> str:
    """``<NOME DA PACIENTE> - Para assinatura.pdf``.

    Reaproveita a sanitização única da M25.17 — separadores de caminho,
    caracteres proibidos no Windows e controles fora, acentos PRESERVADOS.
    Dentro de um ZIP o nome não vai para cabeçalho HTTP, mas continua indo
    para o sistema de arquivos de quem extrai.
    """

    from .download_names import SUFIXO_LAUDO, sanitize_filename_base

    base = sanitize_filename_base(patient_name)
    if not base:
        base = sanitize_filename_base(fallback_code) or "documento"
    return f"{base} - {SUFIXO_LAUDO}.pdf"


def batch_zip_filename(*, generated_on: date) -> str:
    return f"SoproLife - Laudos para assinatura - {generated_on.isoformat()}.zip"


@dataclass(frozen=True)
class BatchFile:
    """Um laudo dentro do pacote de assinatura."""

    document_code: str
    patient_name: str | None
    pdf: bytes

    @property
    def filename(self) -> str:
        return signing_filename(
            self.patient_name, fallback_code=self.document_code
        )


def build_batch_zip(files: list[BatchFile]) -> bytes:
    """ZIP PLANO com os laudos selecionados. Nada além deles.

    Sem subpastas, sem manifesto, sem MIR, sem prévia, sem instruções: o app
    Arquivos do iPhone abre um ZIP plano e mostra os PDFs direto. Cada
    arquivo a mais é uma chance da médica assinar o documento errado.

    Nomes Unicode são preservados. O flag UTF-8 do ZIP é ligado pelo próprio
    zipfile quando o nome não couber em ASCII, que é o caso de qualquer
    paciente com acento.
    """

    if not files:
        raise SignatureBatchError(
            "lote_vazio", "Nenhum laudo foi selecionado para assinatura."
        )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as pacote:
        usados: dict[str, int] = {}
        for arquivo in files:
            nome = arquivo.filename
            # Homônimos existem. Um sufixo com o código do laudo desempata
            # sem esconder que são pessoas diferentes — sobrescrever
            # silenciosamente faria a médica assinar um laudo e perder outro.
            if nome in usados:
                usados[nome] += 1
                raiz, _, extensao = nome.rpartition(".")
                nome = f"{raiz} ({arquivo.document_code}).{extensao}"
            else:
                usados[nome] = 1
            pacote.writestr(nome, arquivo.pdf)
    return buffer.getvalue()


# ------------------------------------------------------ entrada em lote

# Limites do que é aceito de volta. Existem para que um arquivo hostil não
# consiga custar mais memória ou disco do que um lote legítimo de laudos.
MAX_ARQUIVOS_POR_LOTE = 200
MAX_BYTES_POR_PDF = 25 * 1024 * 1024
# Teto do lote INTEIRO já descompactado, e portanto do pico de memória do
# processo. Um laudo assinado real tem centenas de kilobytes: 200 deles não
# passam de ~100 MB. O limite é folgado para a operação e ainda assim
# impede que um ZIP de 25 MB vire gigabytes na extração.
MAX_BYTES_DESCOMPACTADOS = 150 * 1024 * 1024
# Um PDF real comprime pouco (já tem streams comprimidos por dentro). Uma
# razão dessas só aparece em arquivo construído para explodir na extração.
MAX_RAZAO_COMPRESSAO = 200


@dataclass(frozen=True)
class ExtractedFile:
    filename: str
    data: bytes


@dataclass
class ExtractionReport:
    """O que entrou e o que foi recusado ANTES de qualquer pareamento."""

    files: list[ExtractedFile]
    rejected: list[tuple[str, str]]  # (nome, motivo legível)


def _safe_member_name(raw: str) -> str:
    """Só o nome final, sem caminho — zip slip morre aqui.

    Um membro chamado ``../../etc/cron.d/x`` vira ``x``. O nome extraído
    nunca é usado para montar caminho de escrita (o PDF é gravado sob um
    UUID interno), mas reduzi-lo já aqui impede que ele chegue a qualquer
    lugar que faça essa suposição.
    """

    normalizado = raw.replace("\\", "/")
    final = normalizado.rsplit("/", 1)[-1]
    return final.strip() or "arquivo.pdf"


def _looks_like_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def _looks_like_zip(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"


def extract_signed_pdfs(
    uploads: list[tuple[str, bytes]],
    *,
    max_files: int = MAX_ARQUIVOS_POR_LOTE,
    max_pdf_bytes: int = MAX_BYTES_POR_PDF,
    max_total_bytes: int = MAX_BYTES_DESCOMPACTADOS,
) -> ExtractionReport:
    """Achata a entrada: PDFs soltos e/ou UM ZIP contendo PDFs.

    A médica pode mandar vários PDFs assinados, ou um único ZIP com todos —
    as duas formas que um iPhone oferece sem drag-and-drop. Tudo que não for
    PDF válido é recusado com motivo, e a recusa de um arquivo nunca impede
    os outros de seguirem.
    """

    aceitos: list[ExtractedFile] = []
    recusados: list[tuple[str, str]] = []
    total_bytes = 0

    def cabe(nome: str, dados: bytes) -> bool:
        nonlocal total_bytes
        if len(aceitos) >= max_files:
            recusados.append((
                nome,
                f"O envio excede o limite de {max_files} arquivos por lote.",
            ))
            return False
        if len(dados) > max_pdf_bytes:
            recusados.append((nome, "O arquivo excede o tamanho máximo por PDF."))
            return False
        if total_bytes + len(dados) > max_total_bytes:
            recusados.append((nome, "O envio excede o tamanho total permitido."))
            return False
        total_bytes += len(dados)
        return True

    for nome_bruto, dados in uploads:
        nome = _safe_member_name(nome_bruto or "arquivo")
        if _looks_like_zip(dados):
            _extract_from_zip(
                nome,
                dados,
                aceitos=aceitos,
                recusados=recusados,
                cabe=cabe,
                max_files=max_files,
                max_pdf_bytes=max_pdf_bytes,
            )
            continue
        if not _looks_like_pdf(dados):
            recusados.append((
                nome, "O arquivo não é um PDF nem um ZIP de PDFs."
            ))
            continue
        if cabe(nome, dados):
            aceitos.append(ExtractedFile(filename=nome, data=dados))

    return ExtractionReport(files=aceitos, rejected=recusados)


def _extract_from_zip(
    nome_zip: str,
    dados: bytes,
    *,
    aceitos: list[ExtractedFile],
    recusados: list[tuple[str, str]],
    cabe,
    max_files: int,
    max_pdf_bytes: int,
) -> None:
    """Abre UM ZIP com todas as proteções. Nunca abre ZIP aninhado."""

    try:
        pacote = zipfile.ZipFile(io.BytesIO(dados))
    except zipfile.BadZipFile:
        recusados.append((nome_zip, "O arquivo ZIP está corrompido."))
        return

    with pacote:
        membros = pacote.infolist()
        if len(membros) > max_files:
            recusados.append((
                nome_zip,
                f"O ZIP contém mais de {max_files} itens.",
            ))
            return

        for membro in membros:
            nome = _safe_member_name(membro.filename)
            if membro.is_dir():
                continue
            if membro.flag_bits & 0x1:
                recusados.append((nome, "Arquivo protegido por senha."))
                continue
            if not nome.lower().endswith(".pdf"):
                recusados.append((
                    nome, "Somente arquivos PDF são aceitos no ZIP."
                ))
                continue
            # Zip bomb: o tamanho DECLARADO é conferido antes de ler um byte.
            if membro.file_size > max_pdf_bytes:
                recusados.append((
                    nome, "O arquivo excede o tamanho máximo por PDF."
                ))
                continue
            if (
                membro.compress_size > 0
                and membro.file_size / membro.compress_size > MAX_RAZAO_COMPRESSAO
            ):
                recusados.append((
                    nome, "O arquivo tem taxa de compressão suspeita."
                ))
                continue

            try:
                with pacote.open(membro) as handle:
                    # Lê UM byte a mais que o permitido: se vier, o tamanho
                    # declarado no cabeçalho mentiu e o arquivo é recusado.
                    conteudo = handle.read(max_pdf_bytes + 1)
            except Exception:
                recusados.append((nome, "Não foi possível ler o arquivo do ZIP."))
                continue

            if len(conteudo) > max_pdf_bytes:
                recusados.append((
                    nome, "O arquivo excede o tamanho máximo por PDF."
                ))
                continue
            if _looks_like_zip(conteudo):
                recusados.append((
                    nome, "ZIP dentro de ZIP não é aceito."
                ))
                continue
            if not _looks_like_pdf(conteudo):
                recusados.append((nome, "O conteúdo não é um PDF válido."))
                continue
            if cabe(nome, conteudo):
                aceitos.append(ExtractedFile(filename=nome, data=conteudo))


# ------------------------------------------------------------ pareamento

PAREADO = "pareado"
NAO_IDENTIFICADO = "nao_identificado"
JA_RECEBIDO = "ja_recebido"
RECUSADO = "recusado"


@dataclass
class MatchVerdict:
    """Resultado de UM arquivo. O lote é uma lista disto, nunca um booleano."""

    filename: str
    outcome: str
    report_code: str | None = None
    patient_name: str | None = None
    match_method: str | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == PAREADO


def summarize(verdicts: list[MatchVerdict]) -> dict:
    identificados = sum(1 for v in verdicts if v.ok)
    return {
        "total": len(verdicts),
        "identificados": identificados,
        "com_problema": len(verdicts) - identificados,
    }


def normalize_for_compare(valor: str | None) -> str:
    """Comparação tolerante a caixa e acento, para código impresso."""

    if not valor:
        return ""
    sem_acento = unicodedata.normalize("NFKD", valor)
    return "".join(
        c for c in sem_acento if not unicodedata.combining(c)
    ).strip().upper()
