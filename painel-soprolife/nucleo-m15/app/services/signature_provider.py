"""Fronteira de integração de assinatura digital (M24A, item 12).

Define o CONTRATO que um provedor ICP-Brasil real (ex.: um HSM em nuvem, um
serviço de assinatura qualificada) precisa implementar para assinar um
laudo finalizado. NENHUM provedor real está conectado nesta etapa — a
única implementação existente (`UnconfiguredSignatureProvider`) nunca assina nada e
nunca finge sucesso.

Regras que este módulo protege (nunca violar em uma implementação futura):
- nenhuma assinatura baseada em imagem (uma logo ou um "carimbo" desenhado
  no PDF NÃO é uma assinatura digital válida — nunca deve ser tratada
  como tal em `ReportSignature.status`);
- nenhum PDF pode se autodeclarar "assinado digitalmente" sem uma
  assinatura real verificável de um provedor real;
- `request_signature` só pode devolver status "assinada" depois de uma
  confirmação real do provedor — nunca antecipadamente.
- até um provedor real ser configurado (certificado, credenciais, contrato
  jurídico com uma Autoridade Certificadora), a única implementação válida
  em produção é `UnconfiguredSignatureProvider`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

SIGNATURE_STATUS_PENDENTE = "assinatura_pendente"
SIGNATURE_STATUS_ASSINADA = "assinada"
SIGNATURE_STATUS_REJEITADA = "rejeitada"

# ------------------------------------------------------------------ M25.2
#
# Liberação institucional: a via efetivamente disponível hoje. Ela NÃO é
# assinatura qualificada e é registrada com provider e status próprios,
# justamente para nunca ser confundida com o resultado de um provedor
# ICP-Brasil. O que ela realmente prova é:
#
#   - qual médica, autenticada na PRÓPRIA sessão individual, executou a
#     ação consciente "Assinar e liberar laudo";
#   - qual era exatamente o texto liberado (hash do conteúdo);
#   - qual é o hash SHA-256 do PDF final congelado;
#   - quando isso aconteceu (com fuso America/Sao_Paulo registrado).
#
# O caminho qualificado permanece intacto e continua exigindo um provider
# real: `get_signature_provider()` segue devolvendo o provedor nulo, e
# `SIGNATURE_STATUS_ASSINADA` continua inalcançável sem PAdES/ICP-Brasil.
SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL = "liberada_institucional"
PROVIDER_INSTITUTIONAL_RELEASE = "institutional_release"


def institutional_release_evidence(
    *,
    physician_profile_id: str,
    document_sha256: str,
    signed_text_sha256: str,
    signature_asset_sha256: str | None,
) -> dict:
    """Metadados técnicos da liberação institucional.

    Declara explicitamente `qualified_signature=False` para que nenhuma
    verificação futura confunda esta evidência com assinatura qualificada.
    Nunca inclui identidade de paciente, texto clínico ou bytes de imagem.
    """

    return {
        "qualified_signature": False,
        "standard": None,
        "trust_chain": None,
        "release_kind": "institutional_authenticated_action",
        "signer_physician_profile_id": physician_profile_id,
        "document_sha256": document_sha256,
        "signed_text_sha256": signed_text_sha256,
        "handwritten_asset_sha256": signature_asset_sha256,
        # Uma imagem de assinatura manuscrita é elemento visual de
        # identificação; ela não é, e nunca deve ser tratada como,
        # certificado digital.
        "handwritten_image_is_not_a_certificate": True,
    }


@dataclass(frozen=True)
class SignatureRequestResult:
    status: str
    provider: str
    external_reference: str | None
    verification_metadata: dict | None
    error_message: str | None = None


@dataclass(frozen=True)
class SignatureStatusResult:
    status: str
    verification_metadata: dict | None
    completed_at: datetime | None = None


class SignatureProvider(ABC):
    """Contrato que um adapter ICP-Brasil real precisa implementar.

    `document_bytes` é o PDF finalizado (imutável) a assinar;
    `document_sha256` é o hash já calculado (o provedor deve poder
    verificar contra ele); nenhum dado de paciente deve trafegar nos
    metadados de verificação — só referências técnicas.
    """

    name: str

    @abstractmethod
    def request_signature(
        self, *, document_bytes: bytes, document_sha256: str, requested_by_user_id: str
    ) -> SignatureRequestResult:
        raise NotImplementedError

    @abstractmethod
    def check_status(self, *, external_reference: str) -> SignatureStatusResult:
        raise NotImplementedError


class UnconfiguredSignatureProvider(SignatureProvider):
    """Único provedor existente nesta etapa. NUNCA assina — sempre recusa
    a solicitação de forma explícita e auditável, sem exceção nem
    caminho oculto de sucesso simulado."""

    name = "unconfigured"

    def request_signature(
        self, *, document_bytes: bytes, document_sha256: str, requested_by_user_id: str
    ) -> SignatureRequestResult:
        return SignatureRequestResult(
            status=SIGNATURE_STATUS_PENDENTE,
            provider=self.name,
            external_reference=None,
            verification_metadata=None,
            error_message=(
                "Nenhum provedor de assinatura digital ICP-Brasil está configurado. "
                "Assinatura digital pendente."
            ),
        )

    def check_status(self, *, external_reference: str) -> SignatureStatusResult:
        return SignatureStatusResult(
            status=SIGNATURE_STATUS_PENDENTE,
            verification_metadata=None,
            completed_at=None,
        )


def get_signature_provider() -> SignatureProvider:
    """Fábrica única. Devolve sempre o provedor nulo nesta etapa — trocar
    isto por um provedor real é uma decisão de produto/infra em aberto
    (custódia de certificado, contrato com AC, credenciais) e nunca deve
    acontecer sem uma implementação de `SignatureProvider` auditada."""
    return UnconfiguredSignatureProvider()


# Compatibilidade nominal para importações M24A. O alias aponta para a
# implementação fail-closed; não existe provedor de teste com sucesso.
NullSignatureProvider = UnconfiguredSignatureProvider
