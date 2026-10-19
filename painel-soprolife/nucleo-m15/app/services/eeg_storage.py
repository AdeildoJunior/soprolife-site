"""Armazenamento do LAUDO de EEG em PDF.

O arquivo vai para o filesystem privado, como os laudos da espirometria —
nunca para coluna de banco. As primitivas seguras já existem em
`report_storage` (escrita atômica 0600, recusa de symlink, recusa de
sobrescrita, releitura validada); aqui só muda o CAMINHO, que é próprio da
EEGLife para que os dois acervos nunca se misturem no disco.

ATENÇÃO AO QUE ESTE MÓDULO NÃO FAZ
----------------------------------
Não guarda traçado. O arquivo técnico do EEG (EDF, pacote proprietário,
vídeo) continua na fonte externa nesta fase, e `eeg_technical_files` guarda só
metadados. Um traçado tem dezenas a centenas de MB; monitorização prolongada
chega a GB. Este módulo é para o PDF do laudo, que é pequeno.
"""

from __future__ import annotations

from pathlib import Path

from .report_storage import (
    ReportStorageError,
    UnsafeStorageIdError,
    atomic_write_new_file,
    read_and_validate_stored_pdf,
)

__all__ = [
    "ReportStorageError",
    "UnsafeStorageIdError",
    "atomic_write_new_file",
    "read_and_validate_stored_pdf",
    "eeg_report_storage_path",
]

_HEX = set("0123456789abcdef-")


def _assert_safe_id(value: str, *, label: str) -> str:
    """Só UUID interno: nada que venha do usuário entra num caminho."""
    if not value or len(value) > 36 or not set(value.lower()) <= _HEX:
        raise UnsafeStorageIdError(f"{label} inválido para caminho de arquivo.")
    return value


def eeg_report_storage_path(root: Path, *, exam_id: str, document_id: str) -> Path:
    """Caminho determinístico, composto só por UUIDs internos.

    Fica sob `eeg/laudos/`, separado de `laudos/` da espirometria: um acervo
    não encosta no outro nem por engano de varredura.
    """
    _assert_safe_id(exam_id, label="exam_id")
    _assert_safe_id(document_id, label="document_id")
    return root / "eeg" / "laudos" / exam_id / f"{document_id}.pdf"
