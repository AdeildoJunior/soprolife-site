"""Armazenamento de PDFs de laudo — fora do Git, IDs internos, sem overwrite.

Regras impostas aqui (M24A):
- nunca usa o nome de arquivo enviado pelo cliente para montar um caminho;
- todo componente de caminho é um UUID gerado pelo servidor e validado por
  regex antes de tocar o sistema de arquivos (defesa em profundidade contra
  path traversal, mesmo que os IDs já nasçam seguros);
- escrita atômica com `os.link` (falha com FileExistsError se o destino já
  existir — nunca sobrescreve) e permissão restritiva (0600 arquivo,
  0700 diretório);
- a raiz vem de `Settings.resolved_reports_storage_dir()`, que já falha
  fechado se ausente, relativa, symlink ou dentro do repositório Git.
"""

import os
import re
import uuid
from pathlib import Path

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


class UnsafeStorageIdError(ValueError):
    pass


def _assert_safe_id(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not _UUID_RE.fullmatch(value):
        raise UnsafeStorageIdError(f"{label} não é um UUID válido: recusado.")
    return value


def version_storage_path(root: Path, *, exam_id: str, document_id: str, version_id: str) -> Path:
    """Caminho interno determinístico para uma versão de PDF.

    Nunca deriva de nome de arquivo enviado pelo usuário. Os três
    componentes são UUIDs internos validados antes de montar o caminho.
    """
    _assert_safe_id(exam_id, label="exam_id")
    _assert_safe_id(document_id, label="document_id")
    _assert_safe_id(version_id, label="version_id")
    path = root / "laudos" / exam_id / document_id / f"{version_id}.pdf"
    # Defesa final: o caminho resolvido (lexicamente — não exige existência)
    # tem que continuar dentro da raiz. Nenhum componente aqui pode ser
    # "..", pois os três já foram validados como UUID puro, mas a checagem
    # fica barata e remove qualquer dúvida sobre normalização de caminho.
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise UnsafeStorageIdError("Caminho de armazenamento escapou da raiz configurada.") from exc
    return path


def atomic_write_new_file(path: Path, data: bytes) -> None:
    """Escreve `data` em `path` de forma atômica, recusando sobrescrita.

    - cria diretórios pais com 0700;
    - escreve num arquivo temporário exclusivo (O_CREAT|O_EXCL) com 0600;
    - fsync antes de publicar;
    - publica com `os.link` (atômico, falha se `path` já existir) e remove
      o temporário — nunca há uma janela em que `path` existe com conteúdo
      parcial, e nunca há overwrite silencioso.
    """
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Recusado: {path} já existe (overwrite proibido).")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for ancestor in [path.parent, *path.parent.parents]:
        if ancestor.is_symlink():
            raise UnsafeStorageIdError(f"Diretório de armazenamento é um symlink: {ancestor}")

    tmp_path = path.parent / f".tmp-{uuid.uuid4().hex}"
    fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp_path, path)
        except FileExistsError:
            raise FileExistsError(f"Recusado: {path} já existe (overwrite proibido).") from None
        os.chmod(path, 0o600)
    finally:
        tmp_path.unlink(missing_ok=True)


def read_stored_pdf(path: Path, *, root: Path) -> bytes:
    """Lê um PDF já armazenado, com a mesma defesa de que o caminho
    resolvido continua dentro da raiz configurada."""
    if path.is_symlink():
        raise UnsafeStorageIdError("Arquivo armazenado é um symlink — recusado.")
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise UnsafeStorageIdError("Caminho fora da raiz de armazenamento configurada.") from exc
    return resolved_path.read_bytes()
