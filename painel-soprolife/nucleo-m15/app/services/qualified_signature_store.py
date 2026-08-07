"""M25.7 — guarda o PDF PREPARADO entre a ida ao VIDaaS e a volta.

O fluxo qualificado é interrompido por natureza: a médica sai do painel,
autoriza no aplicativo dela e volta em OUTRA requisição HTTP. Os bytes
exatos que geraram o digest precisam sobreviver a esse intervalo.

Regenerar o PDF na volta não serve: a geração carrega data e hora, então os
bytes sairiam diferentes e o digest deixaria de bater — a assinatura viraria
inválida sem que ninguém soubesse por quê.

O arquivo vive na MESMA raiz privada dos laudos, em subárvore própria, com
permissão 0600 e nome derivado só do UUID da solicitação. É descartado assim
que a solicitação chega a um estado terminal.
"""

from __future__ import annotations

import os
from pathlib import Path

from .report_storage import (
    ReportStorageError,
    _assert_safe_id,
    _relative_to_root,
    atomic_write_new_file,
)

_SUBTREE = "assinaturas-preparadas"


def prepared_path(root: Path, *, request_id: str) -> Path:
    """Caminho determinístico, composto apenas pelo UUID da solicitação."""

    _assert_safe_id(request_id, label="request_id")
    path = root / _SUBTREE / f"{request_id}.pdf"
    _relative_to_root(path, root)
    return path


def store_prepared_pdf(root: Path, *, request_id: str, data: bytes) -> Path:
    """Grava o PDF preparado. Nunca sobrescreve — cada solicitação é única."""

    path = prepared_path(root, request_id=request_id)
    atomic_write_new_file(path, data, root=root)
    return path


def read_prepared_pdf(root: Path, *, request_id: str) -> bytes:
    """Lê os bytes preparados. Ausência é erro, nunca silêncio."""

    path = prepared_path(root, request_id=request_id)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError as exc:
        raise ReportStorageError(
            "O PDF preparado desta solicitação não está mais disponível."
        ) from exc


def discard_prepared_pdf(root: Path, *, request_id: str) -> bool:
    """Apaga o preparado ao fim do fluxo. Falha aqui nunca derruba o laudo."""

    path = prepared_path(root, request_id=request_id)
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        # O laudo já está assinado e validado; um preparado órfão é lixo,
        # não risco de correção. Nunca transformar isso em erro do usuário.
        return False
