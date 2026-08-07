"""M25.7 — cifra para segredos de curta duração que precisam ser persistidos.

O `code_verifier` do PKCE precisa sobreviver entre a ida da médica ao VIDaaS
e a volta pelo callback — ou seja, precisa ir para o banco. Guardá-lo em
claro transformaria um acesso de leitura ao banco na capacidade de completar
a assinatura de outra pessoa.

A chave deriva do segredo de autenticação já existente do M15, por HKDF com
rótulo próprio: um vazamento desta cifra não expõe as sessões, e vice-versa.
Cada valor recebe seu próprio nonce aleatório de 96 bits.

Isto NÃO é armazenamento de longo prazo. `forget_transient_secret` existe
para apagar o valor assim que a solicitação chega a um estado terminal — o
segredo que não existe mais não pode vazar.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_INFO = b"soprolife-m15/m25.7/pkce-verifier"
_NONCE_BYTES = 12


class TransientSecretError(ValueError):
    """Falha ao cifrar ou decifrar um segredo transitório."""


def _derive_key(auth_secret: str) -> bytes:
    if not auth_secret:
        raise TransientSecretError(
            "Segredo de autenticação ausente: não é possível proteger o "
            "verificador PKCE."
        )
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO
    ).derive(auth_secret.encode("utf-8"))


def seal_transient_secret(value: str, *, auth_secret: str) -> str:
    """Cifra `value` e devolve texto Base64 pronto para a coluna."""

    key = _derive_key(auth_secret)
    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, value.encode("utf-8"), None)
    return base64.b64encode(nonce + sealed).decode("ascii")


def open_transient_secret(sealed: str | None, *, auth_secret: str) -> str:
    """Decifra o que `seal_transient_secret` produziu. Fail-closed."""

    if not sealed:
        raise TransientSecretError("Segredo transitório ausente ou já apagado.")
    try:
        raw = base64.b64decode(sealed, validate=True)
    except (ValueError, TypeError) as exc:
        raise TransientSecretError("Segredo transitório corrompido.") from exc
    if len(raw) <= _NONCE_BYTES:
        raise TransientSecretError("Segredo transitório truncado.")
    key = _derive_key(auth_secret)
    try:
        opened = AESGCM(key).decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], None)
    except InvalidTag as exc:
        # Chave trocada ou valor adulterado. Nos dois casos a resposta é a
        # mesma: o segredo não é utilizável.
        raise TransientSecretError(
            "Segredo transitório não pôde ser aberto com a chave atual."
        ) from exc
    return opened.decode("utf-8")
