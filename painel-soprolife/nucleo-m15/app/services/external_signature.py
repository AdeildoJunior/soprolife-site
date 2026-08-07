"""M25.8 — assinatura externa em lote pelo certificado que a médica já tem.

Fluxo gratuito, sem API comercial: o Centro de Comando congela os laudos
revisados, entrega os PDFs num ZIP, a médica assina fora com o VIDaaS dela, e
devolve os assinados — também em lote. O sistema reconhece cada arquivo,
valida um por um e libera SOMENTE os que passarem.

O que este módulo garante:

- o laudo é identificado por metadados CARIMBADOS dentro do PDF, nunca pelo
  nome do arquivo (que a médica pode renomear sem querer);
- o PDF assinado precisa CONTER o preparado byte a byte — assinar é anexar,
  não reescrever, então o preparado é prefixo do assinado;
- uma falha em um arquivo não derruba o lote;
- o PDF técnico da MIR nunca entra no pacote de assinatura.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime

from pypdf import PdfReader, PdfWriter

from .report_pades import PadesError, read_signature_fields, validate_pades

# Marca legível por máquina gravada em /Keywords do PDF preparado. É o que
# amarra um arquivo devolvido ao laudo de origem sem depender do nome.
MARKER_PREFIX = "soprolife-laudo"
_MARKER_RE = re.compile(
    rf"{MARKER_PREFIX}:(?P<code>[A-Z0-9\-]+):v(?P<version>\d+):"
    r"(?P<sha>[0-9a-f]{64})"
)

MANIFEST_JSON = "manifesto.json"
MANIFEST_CSV = "manifesto.csv"
INSTRUCTIONS = "COMO-ASSINAR.txt"


class ExternalSignatureError(ValueError):
    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class SigningMarker:
    """Identificação carimbada dentro do PDF.

    `content_sha256` é o hash do laudo ANTES do carimbo — o hash do conteúdo
    clínico renderizado. Não é, e não pode ser, o hash do arquivo entregue:
    um arquivo não consegue conter o próprio hash. O hash do arquivo
    entregue (o que a médica assina) vive no banco e no manifesto, e é ele
    que a reimportação compara byte a byte.
    """

    document_code: str
    version_number: int
    content_sha256: str

    def render(self) -> str:
        return (
            f"{MARKER_PREFIX}:{self.document_code}:"
            f"v{self.version_number}:{self.content_sha256}"
        )


def stamp_signing_metadata(
    pdf: bytes,
    *,
    document_code: str,
    version_number: int,
    physician_name: str,
    crm: str,
) -> bytes:
    """Carimba a identificação do laudo nos metadados do PDF.

    Chamado ANTES de calcular o hash do preparado: o hash precisa ser do
    arquivo que a médica realmente vai assinar, com carimbo e tudo.

    Nenhum dado de paciente entra aqui. O carimbo tem código do laudo,
    versão e — depois de calculado — o próprio hash, o suficiente para
    reencontrar o laudo na volta.
    """

    marcador = SigningMarker(
        document_code=document_code,
        version_number=version_number,
        # Hash do conteúdo renderizado, calculado ANTES de carimbar.
        content_sha256=hashlib.sha256(pdf).hexdigest(),
    )
    try:
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
        writer.add_metadata({
            "/Title": f"Laudo de Espirometria {document_code}",
            "/Author": physician_name,
            "/Subject": f"Laudo {document_code} versao {version_number} - {crm}",
            "/Keywords": marcador.render(),
        })
        buffer = io.BytesIO()
        writer.write(buffer)
    except Exception as exc:
        raise ExternalSignatureError(
            "carimbo_nao_aplicado",
            "Não foi possível carimbar a identificação no PDF do laudo.",
        ) from exc
    marcado = buffer.getvalue()
    if read_signing_marker(marcado) != marcador:
        # Confere que o carimbo é RELIDO igual ao gravado. O pypdf escapa
        # strings literais (`-` vira \055, `:` vira \072); sem esta prova, um
        # carimbo ilegível só apareceria na reimportação, semanas depois.
        raise ExternalSignatureError(
            "carimbo_nao_relido",
            "O carimbo gravado no PDF não pôde ser relido.",
        )
    return marcado


def read_signing_marker(pdf: bytes) -> SigningMarker | None:
    """Lê o carimbo de um PDF (preparado ou assinado). None se não houver.

    Lê pelos METADADOS, com o parser: é ele que desfaz os escapes octais que
    o pypdf aplica ao gravar. A varredura em bytes crus fica só como reserva,
    para o caso de o assinador externo reescrever o dicionário de outra
    forma — nunca como caminho principal.
    """

    try:
        metadados = PdfReader(io.BytesIO(pdf)).metadata or {}
        bruto = str(metadados.get("/Keywords") or "")
    except Exception:
        bruto = ""
    achado = _MARKER_RE.search(bruto)
    if achado is None:
        achado = _MARKER_RE.search(pdf.decode("latin-1", errors="ignore"))
    if achado is None:
        return None
    return SigningMarker(
        document_code=achado.group("code"),
        version_number=int(achado.group("version")),
        content_sha256=achado.group("sha"),
    )


def safe_filename(
    *, document_code: str, version_number: int, patient_reference: str
) -> str:
    """Nome de arquivo inequívoco e sem depender do nome do paciente.

    Usa o código do laudo (único), a versão e a referência interna do
    paciente — nunca o nome civil, que se repete, tem acento e vaza dado
    pessoal para a pasta de downloads da médica.
    """

    def limpar(valor: str) -> str:
        normalizado = unicodedata.normalize("NFKD", valor)
        somente_ascii = normalizado.encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^A-Za-z0-9\-]+", "-", somente_ascii).strip("-")

    return (
        f"{limpar(document_code)}_v{version_number}"
        f"_{limpar(patient_reference)}.pdf"
    )


@dataclass
class BatchEntry:
    """Um laudo dentro do pacote de assinatura."""

    document_code: str
    version_number: int
    patient_reference: str
    prepared_sha256: str
    exam_code: str
    physician_name: str
    crm: str
    rqe: str | None
    prepared_at: datetime | None
    pdf: bytes
    mir_pdf: bytes | None = None

    @property
    def filename(self) -> str:
        return safe_filename(
            document_code=self.document_code,
            version_number=self.version_number,
            patient_reference=self.patient_reference,
        )


def build_signing_package(
    entries: list[BatchEntry],
    *,
    include_mir: bool = False,
    validation_base_url: str | None = None,
) -> bytes:
    """Monta o ZIP com os laudos prontos para assinatura.

    O PDF técnico da MIR fica FORA por padrão. Ele só entra quando pedido
    explicitamente e, mesmo assim, em pasta separada e claramente nomeada —
    nunca misturado com o que vai ser assinado.
    """

    if not entries:
        raise ExternalSignatureError(
            "lote_vazio",
            "Nenhum laudo revisado foi selecionado para assinatura.",
        )

    buffer = io.BytesIO()
    manifesto: list[dict] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as pacote:
        vistos: set[str] = set()
        for entrada in entries:
            nome = entrada.filename
            if nome in vistos:
                nome = f"{entrada.document_code}_{entrada.prepared_sha256[:8]}.pdf"
            vistos.add(nome)
            pacote.writestr(f"assinar/{nome}", entrada.pdf)
            manifesto.append({
                "arquivo": nome,
                "codigo_laudo": entrada.document_code,
                "versao": entrada.version_number,
                "hash_preparado_sha256": entrada.prepared_sha256,
                "codigo_exame": entrada.exam_code,
                "referencia_paciente": entrada.patient_reference,
                "medica": entrada.physician_name,
                "crm": entrada.crm,
                "rqe": entrada.rqe,
                "preparado_em": (
                    entrada.prepared_at.isoformat()
                    if entrada.prepared_at else None
                ),
            })
            if include_mir and entrada.mir_pdf:
                pacote.writestr(
                    f"exame-tecnico-mir-NAO-ASSINAR/{entrada.document_code}"
                    f"_MIR.pdf",
                    entrada.mir_pdf,
                )

        pacote.writestr(
            MANIFEST_JSON,
            json.dumps(
                {
                    "gerado_por": "SoproLife Command Center",
                    "total": len(manifesto),
                    "observacao": (
                        "Assine somente os arquivos da pasta 'assinar'. "
                        "Nenhum CPF ou dado clínico consta deste manifesto."
                    ),
                    "laudos": manifesto,
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )

        csv_buffer = io.StringIO()
        escritor = csv.DictWriter(
            csv_buffer,
            fieldnames=[
                "arquivo", "codigo_laudo", "versao", "hash_preparado_sha256",
                "codigo_exame", "referencia_paciente", "medica", "crm", "rqe",
                "preparado_em",
            ],
        )
        escritor.writeheader()
        for linha in manifesto:
            escritor.writerow(linha)
        pacote.writestr(MANIFEST_CSV, csv_buffer.getvalue().encode("utf-8"))
        pacote.writestr(
            INSTRUCTIONS,
            _instructions(len(manifesto), validation_base_url).encode("utf-8"),
        )
    return buffer.getvalue()


def _instructions(total: int, validation_base_url: str | None) -> str:
    """Instruções honestas: não prometem lote onde ele não foi comprovado."""

    linhas = [
        "COMO ASSINAR ESTES LAUDOS",
        "=" * 40,
        "",
        f"Este pacote contém {total} laudo(s) na pasta 'assinar'.",
        "",
        "1. Assine os arquivos da pasta 'assinar' com o seu certificado",
        "   ICP-Brasil no VIDaaS.",
        "2. NAO altere, renomeie o conteudo nem reimprima os PDFs. Assinar",
        "   ANEXA a assinatura ao arquivo; qualquer reescrita invalida.",
        "3. Devolva os arquivos assinados pelo Centro de Comando, em",
        "   'Enviar laudos assinados'. Pode enviar varios de uma vez, ou um",
        "   ZIP com todos.",
        "",
        "SOBRE ASSINAR VARIOS DE UMA VEZ",
        "-" * 40,
        "Nao foi possivel confirmar que a ferramenta oficial do VIDaaS",
        "assina varios PDFs numa unica sessao. Ate que isso seja confirmado",
        "com a Valid, considere a assinatura arquivo por arquivo.",
        "O download e o reenvio pelo Centro de Comando continuam em lote.",
        "",
    ]
    if validation_base_url:
        linhas += [
            "VALIDACAO",
            "-" * 40,
            f"Cada laudo traz o endereco de conferencia: {validation_base_url}",
            "",
        ]
    return "\n".join(linhas)


# ------------------------------------------------------- reimportação


VALIDATION_OK = "validado_e_liberado"
VALIDATION_DUPLICADO = "arquivo_duplicado"
VALIDATION_NAO_ENCONTRADO = "laudo_nao_encontrado"
VALIDATION_VERSAO_DIVERGENTE = "versao_divergente"
VALIDATION_SEM_ASSINATURA = "assinatura_ausente"
VALIDATION_ASSINATURA_INVALIDA = "assinatura_invalida"
VALIDATION_OUTRO_CERTIFICADO = "certificado_de_outra_pessoa"
VALIDATION_ALTERADO = "documento_alterado"
VALIDATION_FALHA_TECNICA = "falha_tecnica_recuperavel"


@dataclass
class FileVerdict:
    """Resultado de UM arquivo. O lote é uma lista disto, nunca um booleano."""

    filename: str
    outcome: str
    document_code: str | None = None
    version_number: int | None = None
    message: str = ""
    signer_subject: str | None = None
    signed_sha256: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == VALIDATION_OK


def extract_pdfs(uploads: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Achata a entrada: aceita PDFs soltos e ZIPs contendo PDFs."""

    saida: list[tuple[str, bytes]] = []
    for nome, dados in uploads:
        if dados[:4] == b"PK\x03\x04":
            try:
                with zipfile.ZipFile(io.BytesIO(dados)) as pacote:
                    for membro in pacote.namelist():
                        if membro.endswith("/") or not membro.lower().endswith(
                            ".pdf"
                        ):
                            continue
                        # Nunca confiar no caminho de dentro do ZIP.
                        interno = membro.rsplit("/", 1)[-1]
                        with pacote.open(membro) as handle:
                            saida.append((interno, handle.read()))
            except zipfile.BadZipFile:
                saida.append((nome, dados))
        else:
            saida.append((nome, dados))
    return saida


def verify_signed_pdf(
    *,
    filename: str,
    signed: bytes,
    prepared: bytes,
    expected_marker: SigningMarker,
    expected_signer_subject: str | None,
) -> FileVerdict:
    """Confere UM PDF assinado contra o preparado que o originou.

    Ordem deliberada: primeiro o que é barato e decisivo (é o documento
    certo? não foi reescrito?), depois o que é caro (criptografia).
    """

    marcador = read_signing_marker(signed)
    if marcador is None:
        return FileVerdict(
            filename=filename,
            outcome=VALIDATION_NAO_ENCONTRADO,
            message=(
                "O arquivo não traz a identificação da SoproLife. Ele foi "
                "gerado por este painel?"
            ),
        )
    if marcador.document_code != expected_marker.document_code:
        return FileVerdict(
            filename=filename,
            outcome=VALIDATION_NAO_ENCONTRADO,
            document_code=marcador.document_code,
            message="O arquivo pertence a outro laudo.",
        )
    if marcador.version_number != expected_marker.version_number:
        return FileVerdict(
            filename=filename,
            outcome=VALIDATION_VERSAO_DIVERGENTE,
            document_code=marcador.document_code,
            version_number=marcador.version_number,
            message=(
                "Este arquivo é de uma versão anterior do laudo. Baixe a "
                "versão atual e assine novamente."
            ),
        )
    if marcador.content_sha256 != expected_marker.content_sha256:
        return FileVerdict(
            filename=filename,
            outcome=VALIDATION_ALTERADO,
            document_code=marcador.document_code,
            message="O conteúdo do laudo não confere com o que foi preparado.",
        )

    # Assinar ANEXA: o preparado tem de continuar sendo prefixo exato.
    if not signed.startswith(prepared):
        return FileVerdict(
            filename=filename,
            outcome=VALIDATION_ALTERADO,
            document_code=marcador.document_code,
            message=(
                "O arquivo foi reescrito, e não apenas assinado. Assine o "
                "PDF baixado sem reimprimir nem exportar de novo."
            ),
        )
    if len(signed) == len(prepared):
        return FileVerdict(
            filename=filename,
            outcome=VALIDATION_SEM_ASSINATURA,
            document_code=marcador.document_code,
            message="O arquivo não foi assinado — é o mesmo PDF baixado.",
        )

    try:
        read_signature_fields(signed)
    except PadesError:
        return FileVerdict(
            filename=filename,
            outcome=VALIDATION_SEM_ASSINATURA,
            document_code=marcador.document_code,
            message=(
                "Não há assinatura digital no arquivo. Uma imagem de "
                "assinatura não vale como assinatura digital."
            ),
        )

    try:
        validacao = validate_pades(signed)
    except PadesError as exc:
        outcome = (
            VALIDATION_ALTERADO
            if exc.codigo in ("digest_assinado_divergente", "digest_divergente")
            else VALIDATION_ASSINATURA_INVALIDA
        )
        return FileVerdict(
            filename=filename,
            outcome=outcome,
            document_code=marcador.document_code,
            message=exc.mensagem,
        )

    if (
        expected_signer_subject
        and validacao.signer_subject != expected_signer_subject
    ):
        return FileVerdict(
            filename=filename,
            outcome=VALIDATION_OUTRO_CERTIFICADO,
            document_code=marcador.document_code,
            signer_subject=validacao.signer_subject,
            message=(
                "O certificado que assinou este laudo não é o mesmo já "
                "vinculado a esta médica."
            ),
        )

    return FileVerdict(
        filename=filename,
        outcome=VALIDATION_OK,
        document_code=marcador.document_code,
        version_number=marcador.version_number,
        signer_subject=validacao.signer_subject,
        signed_sha256=validacao.final_sha256,
        message="Assinatura ICP-Brasil validada.",
    )


def summarize(verdicts: list[FileVerdict]) -> dict:
    """Contadores para a tela: validados, com erro, total."""

    validados = sum(1 for v in verdicts if v.ok)
    return {
        "total": len(verdicts),
        "validados": validados,
        "com_erro": len(verdicts) - validados,
    }
