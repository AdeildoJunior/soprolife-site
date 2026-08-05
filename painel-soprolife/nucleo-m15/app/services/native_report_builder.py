"""M25.2 — montagem do conteúdo do laudo nativo a partir do banco.

Camada fina entre o domínio (documento, exame, paciente, unidade, perfil
médico) e o gerador de PDF. Ela existe para que o gerador continue puro:
ele desenha o que recebe e nunca consulta o banco.

Nada aqui infere conclusão, grau, resposta a broncodilatador ou endereço:
todos os campos vêm de decisão humana registrada ou de cadastro existente.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    Person,
    PhysicianProfile,
    PhysicianSignatureAsset,
    ReportAddendum,
    ReportDocument,
    SpirometryExam,
)
from .report_locations import ReportLocation, resolve_report_location
from .report_native_pdf import (
    AddendumBlock,
    ExamBlock,
    LocationBlock,
    NativeReportContent,
    PatientBlock,
    PhysicianBlock,
    SignatureImage,
)
from .report_storage import ReportStorageError, StoredPdfIntegrityError
from .signature_asset import (
    SignatureAssetError,
    read_and_validate_signature_asset,
    signature_asset_storage_path,
)

# Alfabeto sem caracteres ambíguos (0/O, 1/I/L): o código é lido em voz
# alta e digitado por pessoas a partir do papel.
_VALIDATION_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
VALIDATION_CODE_LENGTH = 12


class NativeReportBuildError(ValueError):
    """Erro de montagem com `codigo` estável para a resposta."""

    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class ResolvedSignatureAsset:
    """Ativo manuscrito já revalidado, pronto para o desenho."""

    asset: PhysicianSignatureAsset
    image: SignatureImage


def generate_validation_code() -> str:
    """Código opaco de verificação — não derivado de dado do paciente."""

    return "".join(
        secrets.choice(_VALIDATION_ALPHABET)
        for _ in range(VALIDATION_CODE_LENGTH)
    )


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validation_url(code: str | None) -> str | None:
    """URL de validação — só existe quando explicitamente configurada."""

    if not code:
        return None
    base = get_settings().reports_validation_base_url
    if not base:
        return None
    return f"{base}/{code}"


def to_display_timezone(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(get_settings().display_timezone))


def active_signature_asset(
    db: Session, profile_id: str
) -> PhysicianSignatureAsset | None:
    return db.execute(
        select(PhysicianSignatureAsset).where(
            PhysicianSignatureAsset.physician_profile_id == profile_id,
            PhysicianSignatureAsset.active.is_(True),
        )
    ).scalar_one_or_none()


def resolve_signature_asset(
    db: Session, profile_id: str, *, storage_root
) -> ResolvedSignatureAsset | None:
    """Lê e revalida o ativo manuscrito ativo, quando existir.

    Ausência de ativo é um estado NORMAL: o laudo é liberado apenas com o
    bloco identificador da médica. Já um ativo cadastrado que não pode ser
    lido ou cujo hash divergiu é falha fechada — nunca é ignorado em
    silêncio, porque significaria liberar um documento diferente do
    esperado.
    """

    asset = active_signature_asset(db, profile_id)
    if asset is None:
        return None
    path = signature_asset_storage_path(
        storage_root,
        physician_profile_id=asset.physician_profile_id,
        asset_id=asset.id,
    )
    try:
        validated = read_and_validate_signature_asset(
            path,
            root=storage_root,
            expected_sha256=asset.sha256,
            expected_size_bytes=asset.size_bytes,
            expected_width=asset.image_width,
            expected_height=asset.image_height,
        )
    except StoredPdfIntegrityError as exc:
        raise NativeReportBuildError(exc.codigo, exc.mensagem) from None
    except SignatureAssetError as exc:
        raise NativeReportBuildError(exc.codigo, exc.mensagem) from None
    except (ReportStorageError, OSError, ValueError):
        raise NativeReportBuildError(
            "assinatura_armazenada_indisponivel",
            "O ativo de assinatura cadastrado não pôde ser lido.",
        ) from None
    return ResolvedSignatureAsset(
        asset=asset,
        image=SignatureImage(
            data=validated.data,
            width=validated.width,
            height=validated.height,
        ),
    )


def load_addenda(db: Session, document_id: str) -> tuple[AddendumBlock, ...]:
    rows = db.execute(
        select(ReportAddendum)
        .where(ReportAddendum.report_document_id == document_id)
        .order_by(ReportAddendum.sequence)
    ).scalars()
    return tuple(
        AddendumBlock(
            sequence=row.sequence,
            body_text=row.body_text,
            created_at=to_display_timezone(row.created_at),
        )
        for row in rows
    )


def physician_block(profile: PhysicianProfile) -> PhysicianBlock:
    """Bloco médico impresso.

    `crm_display` é opcional: sem ele, imprime-se o CRM normalizado. Nada é
    formatado por adivinhação.
    """

    return PhysicianBlock(
        professional_name=profile.professional_name,
        specialty=profile.especialidade,
        crm_display=(profile.crm_display or profile.crm_number),
        crm_state=profile.crm_state,
        rqe=profile.rqe,
    )


def build_native_content(
    db: Session,
    *,
    document: ReportDocument,
    exam: SpirometryExam,
    person: Person,
    profile: PhysicianProfile,
    location: ReportLocation,
    version_number: int,
    conclusion_text: str,
    observations: str | None,
    issued_at: datetime,
    released: bool,
    released_at: datetime | None = None,
    validation_code: str | None = None,
    signature_image: SignatureImage | None = None,
    addenda: tuple[AddendumBlock, ...] = (),
    pilot_warning: str | None = None,
) -> NativeReportContent:
    """Conteúdo completo do laudo nativo, já no fuso de apresentação."""

    settings = get_settings()
    return NativeReportContent(
        document_code=document.public_code,
        version_number=version_number,
        patient=PatientBlock(
            full_name=person.nome_completo,
            birth_date=person.data_nascimento,
            sex=person.sexo,
            public_code=person.public_code,
        ),
        exam=ExamBlock(
            public_code=exam.public_code,
            exam_date=exam.data_exame,
            exam_time=exam.hora_exame,
            date_precision=exam.data_exame_precisao,
            has_post_bd=exam.broncodilatador,
            clinical_indication=exam.indicacao_clinica,
        ),
        location=LocationBlock(
            name=location.name,
            address_line=location.address_line,
            contact_line=location.contact_line,
        ),
        physician=physician_block(profile),
        conclusion_text=conclusion_text,
        observations=observations,
        issued_at_local=to_display_timezone(issued_at),
        released=released,
        released_at_local=to_display_timezone(released_at),
        validation_code=validation_code,
        validation_url=validation_url(validation_code),
        signature_image=signature_image,
        addenda=addenda,
        pilot_warning=pilot_warning,
        timezone_label=settings.display_timezone,
    )


def resolve_document_context(
    db: Session, document: ReportDocument
) -> tuple[SpirometryExam, Person, ReportLocation]:
    """Exame, paciente e local — tudo obrigatório para emitir o laudo."""

    exam = db.get(SpirometryExam, document.spirometry_exam_id)
    if exam is None:
        raise NativeReportBuildError(
            "exame_nao_encontrado", "Exame do laudo não encontrado."
        )
    person = db.get(Person, exam.person_id)
    if person is None:
        raise NativeReportBuildError(
            "paciente_nao_encontrado",
            "O paciente vinculado ao exame não está disponível.",
        )
    location = resolve_report_location(db, document=document, exam=exam)
    return exam, person, location
