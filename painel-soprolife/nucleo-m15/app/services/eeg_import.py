"""Importação manual Nano/iCelera; nenhum transporte/API do fabricante.

Adapter futuro recebe bytes exportados e devolve metadados técnicos de arquivo.
Não lê nem interpreta o conteúdo clínico. ZIP nunca é extraído ou executado.
"""
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Protocol
from zipfile import ZipFile, BadZipFile

from ..errors import ReportDomainError
from .pdf_validation import validate_pdf_bytes, InvalidPdfError
from .report_storage import read_private_file_bytes, atomic_write_new_file, assert_safe_storage_id

MAX_TECHNICAL_BYTES = 64 * 1024 * 1024
TYPES = {'pdf': 'application/pdf', 'edf': 'application/octet-stream', 'zip': 'application/zip'}


class ExportAdapter(Protocol):
    """Contrato local de arquivo, sem afirmar API remota disponível."""
    def validate_export(self, data: bytes, extension: str, declared_mime: str) -> dict: ...


class ManualNanoAdapter:
    def validate_export(self, data, extension, declared_mime):
        ext = extension.lower().lstrip('.')
        if ext not in TYPES or not data or len(data) > MAX_TECHNICAL_BYTES:
            raise ReportDomainError(422, 'arquivo_invalido', 'Use PDF, EDF ou ZIP de até 64 MiB. Vídeo permanece inativo.')
        allowed = {TYPES[ext], 'application/octet-stream'}
        if ext == 'zip': allowed.add('application/x-zip-compressed')
        if declared_mime not in allowed:
            raise ReportDomainError(422, 'mime_invalido', 'Tipo declarado incompatível com o formato.')
        try:
            if ext == 'pdf':
                validate_pdf_bytes(data, max_size_bytes=MAX_TECHNICAL_BYTES, declared_content_type='application/pdf')
            elif ext == 'edf':
                # Somente envelope EDF: assinatura, tamanho do cabeçalho e canais.
                if len(data) < 256 or data[:8] != b'0       ': raise ValueError()
                header = int(data[184:192]); channels = int(data[252:256])
                if channels < 1 or channels > 512 or header != 256 + channels * 256 or header > len(data): raise ValueError()
            else:
                if data[:4] != b'PK\x03\x04': raise ValueError()
                with ZipFile(BytesIO(data)) as archive:
                    entries = archive.infolist()
                    if not entries or len(entries) > 2048: raise ValueError()
                    total = 0
                    for item in entries:
                        name = item.filename
                        path = PurePosixPath(name)
                        if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or '\x00' in name: raise ValueError()
                        if (item.external_attr >> 16) & 0o170000 == 0o120000 or item.flag_bits & 1: raise ValueError()
                        if path.suffix.lower() in {'.exe','.dll','.js','.html','.sh','.bat','.cmd','.ps1','.py','.php','.svg'}: raise ValueError()
                        total += item.file_size
                        if total > 512 * 1024 * 1024 or item.file_size > max(item.compress_size,1) * 200: raise ValueError()
        except (ValueError, BadZipFile, InvalidPdfError, OverflowError):
            raise ReportDomainError(422, 'arquivo_invalido', 'Arquivo inválido ou pacote fora da política de segurança.') from None
        return {'extensao': ext, 'mime_type': TYPES[ext], 'sha256': sha256(data).hexdigest(), 'size_bytes': len(data)}


def storage_path(root, exam_id, file_id, ext, *, documents=False):
    assert_safe_storage_id(exam_id, label='exam_id')
    assert_safe_storage_id(file_id, label='file_id')
    if ext not in TYPES: raise ValueError('Formato interno inválido.')
    return root / 'eeg' / ('documentos' if documents else 'tracados') / exam_id / (file_id + '.' + ext)


def read_checked(root, relative, expected_hash, expected_size):
    data = read_private_file_bytes(root / relative, root=root)
    if len(data) != expected_size or sha256(data).hexdigest() != expected_hash:
        raise ReportDomainError(409, 'integridade_divergente', 'Arquivo indisponível por divergência de integridade.')
    return data
