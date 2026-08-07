"""M25.7 — cliente HTTP da API IntegraICP (certificado em nuvem VIDaaS).

O que este módulo pode ver e o que ele NUNCA pode ver:

- PODE: o digest SHA-256 do conteúdo assinável, o identificador do canal, o
  identificador da credencial devolvido pelo callback, e os parâmetros PKCE.
- NUNCA: o PDF, o nome do paciente, a conclusão clínica, o CPF completo da
  médica, ou qualquer coisa que identifique um paciente. Nada disso é
  necessário para assinar um digest, e o que não é necessário não trafega.

Nenhum endereço, canal ou callback está escrito aqui. Tudo chega por
configuração (`app/config.py`), e sem os três a integração é considerada
indisponível — nunca há um valor "de exemplo" que possa vazar para produção.

Os quatro passos do protocolo, na ordem:

1. ``build_authentication_url``  — GET /authentications (clearance + PKCE)
2. o navegador da médica volta ao callback com o CredentialId
3. ``fetch_credential``          — GET /credentials/{credentialId}
4. ``request_signature``         — POST /signatures (envia SÓ o digest)
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

# RFC 7636 §4.1: o verifier tem entre 43 e 128 caracteres do alfabeto
# unreserved. 64 bytes aleatórios em base64url dão 86 caracteres.
_PKCE_VERIFIER_BYTES = 64
PKCE_METHOD = "S256"


class IntegraICPError(RuntimeError):
    """Falha de integração com `codigo` estável para a resposta da API.

    A mensagem é sempre segura para exibir: nunca carrega corpo de resposta
    da AC, cabeçalho, token, verifier ou CPF.
    """

    def __init__(self, codigo: str, mensagem: str, *, recuperavel: bool = False):
        self.codigo = codigo
        self.mensagem = mensagem
        # Distingue "tente de novo" de "esta solicitação morreu". A tela da
        # médica só oferece o botão de repetir quando isto é verdadeiro.
        self.recuperavel = recuperavel
        super().__init__(mensagem)


@dataclass(frozen=True)
class PkcePair:
    """Par PKCE. O `verifier` é segredo de uso único e nunca é registrado."""

    verifier: str
    challenge: str
    method: str = PKCE_METHOD


@dataclass(frozen=True)
class CredentialInfo:
    """Dados mínimos da credencial devolvida pela AC."""

    credential_id: str
    subject_name: str | None
    certificate_der: bytes | None
    raw_status: str | None


@dataclass(frozen=True)
class SignatureResult:
    """CMS devolvido pela AC, já decodificado."""

    cms_der: bytes
    external_reference: str | None


def generate_pkce() -> PkcePair:
    """Gera verifier e challenge conforme RFC 7636, método S256."""

    verifier = _b64url_nopad(secrets.token_bytes(_PKCE_VERIFIER_BYTES))
    challenge = _b64url_nopad(hashlib.sha256(verifier.encode("ascii")).digest())
    return PkcePair(verifier=verifier, challenge=challenge)


def _b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def digest_to_base64(digest_hex: str) -> str:
    """Converte o digest hexadecimal para Base64 PADRÃO.

    A API espera Base64 padrão (RFC 4648 §4), com "+" e "/" — NÃO a variante
    URL-safe. Enviar a variante errada produz uma assinatura sobre bytes
    diferentes, que só falha lá na frente, na validação do PDF.
    """

    try:
        raw = bytes.fromhex(digest_hex)
    except ValueError as exc:
        raise IntegraICPError(
            "digest_invalido", "O digest do laudo não é hexadecimal válido."
        ) from exc
    if len(raw) != 32:
        raise IntegraICPError(
            "digest_invalido", "O digest do laudo não tem 32 bytes (SHA-256)."
        )
    return base64.b64encode(raw).decode("ascii")


class IntegraICPClient:
    """Cliente fino sobre a API. Sem estado entre chamadas, sem retry cego."""

    def __init__(
        self,
        *,
        base_url: str,
        channel_id: str,
        callback_url: str,
        signature_policy: str | None = None,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not base_url or not channel_id or not callback_url:
            raise IntegraICPError(
                "integracao_incompleta",
                "A integração de assinatura qualificada não está configurada.",
            )
        self._base_url = base_url.rstrip("/")
        self._channel_id = channel_id
        self._callback_url = callback_url
        self._policy = signature_policy
        self._timeout = timeout_seconds
        # Injetável só para teste: a suíte monta um servidor falso em
        # memória. Em produção fica None e o httpx usa a rede de verdade.
        self._transport = transport

    # ------------------------------------------------------------ passo 1

    def build_authentication_url(
        self, *, pkce: PkcePair, state: str, nonce: str
    ) -> str:
        """Monta a URL para a qual a médica é enviada, com PKCE e state.

        Não faz chamada HTTP: o navegador dela é que vai a este endereço. O
        `state` amarra a volta à solicitação, e o `nonce` protege contra
        reaproveitamento do callback.
        """

        query = urllib.parse.urlencode({
            "channel": self._channel_id,
            "redirect_uri": self._callback_url,
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce.challenge,
            "code_challenge_method": pkce.method,
        })
        return f"{self._base_url}/authentications?{query}"

    # ------------------------------------------------------------ passo 3

    def fetch_credential(
        self, *, credential_id: str, pkce_verifier: str
    ) -> CredentialInfo:
        """Troca o CredentialId pela credencial, provando posse do verifier."""

        if not credential_id:
            raise IntegraICPError(
                "credencial_ausente", "A autoridade não devolveu credencial."
            )
        payload = self._request(
            "GET",
            f"/credentials/{urllib.parse.quote(credential_id, safe='')}",
            params={"code_verifier": pkce_verifier},
        )
        certificate = None
        raw_cert = payload.get("certificate") or payload.get("certificado")
        if isinstance(raw_cert, str) and raw_cert.strip():
            try:
                certificate = base64.b64decode(raw_cert, validate=True)
            except (ValueError, TypeError) as exc:
                raise IntegraICPError(
                    "certificado_ilegivel",
                    "O certificado devolvido pela autoridade é inválido.",
                ) from exc
        subject = payload.get("subjectName") or payload.get("titular")
        return CredentialInfo(
            credential_id=credential_id,
            subject_name=subject if isinstance(subject, str) else None,
            certificate_der=certificate,
            raw_status=(
                payload.get("status") if isinstance(payload.get("status"), str)
                else None
            ),
        )

    # ------------------------------------------------------------ passo 4

    def request_signature(
        self, *, credential_id: str, digest_hex: str, pkce_verifier: str
    ) -> SignatureResult:
        """Envia SOMENTE o digest e recebe o CMS destacado."""

        body: dict[str, Any] = {
            "credentialId": credential_id,
            "hashes": [digest_to_base64(digest_hex)],
            "hashAlgorithm": "SHA256",
            "signatureFormat": "CMS",
            "code_verifier": pkce_verifier,
        }
        if self._policy:
            body["signaturePolicy"] = self._policy
        payload = self._request("POST", "/signatures", json_body=body)

        raw = payload.get("signatures") or payload.get("assinaturas")
        if isinstance(raw, list) and raw:
            candidate = raw[0]
        else:
            candidate = payload.get("signature") or payload.get("assinatura")
        if not isinstance(candidate, str) or not candidate.strip():
            raise IntegraICPError(
                "assinatura_ausente",
                "A autoridade não devolveu a assinatura do documento.",
            )
        try:
            cms_der = base64.b64decode(candidate, validate=True)
        except (ValueError, TypeError) as exc:
            raise IntegraICPError(
                "assinatura_ilegivel",
                "A assinatura devolvida pela autoridade não é Base64 válida.",
            ) from exc
        reference = payload.get("transactionId") or payload.get("id")
        return SignatureResult(
            cms_der=cms_der,
            external_reference=reference if isinstance(reference, str) else None,
        )

    # ------------------------------------------------------------- comum

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict:
        url = f"{self._base_url}{path}"
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers={"Accept": "application/json"},
                )
        except httpx.TimeoutException as exc:
            raise IntegraICPError(
                "tempo_esgotado",
                "A autoridade certificadora não respondeu a tempo.",
                recuperavel=True,
            ) from exc
        except httpx.HTTPError as exc:
            # Nunca propague o texto da exceção: ele costuma conter a URL
            # completa, e a URL carrega o canal e o verifier.
            raise IntegraICPError(
                "falha_de_comunicacao",
                "Não foi possível falar com a autoridade certificadora.",
                recuperavel=True,
            ) from exc

        if response.status_code in (408, 429, 502, 503, 504):
            raise IntegraICPError(
                "autoridade_indisponivel",
                "A autoridade certificadora está temporariamente indisponível.",
                recuperavel=True,
            )
        if response.status_code == 401 or response.status_code == 403:
            raise IntegraICPError(
                "autorizacao_recusada",
                "A autoridade certificadora recusou a autorização.",
            )
        if response.status_code >= 400:
            raise IntegraICPError(
                "resposta_de_erro",
                "A autoridade certificadora recusou a solicitação.",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise IntegraICPError(
                "resposta_malformada",
                "A autoridade certificadora devolveu uma resposta ilegível.",
            ) from exc
        if not isinstance(payload, dict):
            raise IntegraICPError(
                "resposta_malformada",
                "A autoridade certificadora devolveu uma resposta inesperada.",
            )
        return payload
