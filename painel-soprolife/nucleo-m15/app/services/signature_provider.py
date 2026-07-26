"""Fronteira de integração de assinatura digital (M24A, item 12).

Define o CONTRATO que um provedor ICP-Brasil real (ex.: um HSM em nuvem, um
serviço de assinatura qualificada) precisa implementar para assinar um
laudo finalizado. NENHUM provedor real está conectado nesta etapa — a
única implementação existente (`NullSignatureProvider`) nunca assina nada e
nunca finge sucesso.

Regras que este módulo protege (nunca violar em uma implementação futura):
- nenhuma assinatura baseada em imagem (uma logo ou um "carimbo" desenhado
  no PDF NÃO é uma assinatura digital válida — nunca deve ser tratada
  como tal em `ReportSignature.status`);
- nenhum PDF pode se autodeclarar "assinado digitalmente" sem uma
  assinatura real verificável de um provedor real;
- `request_signature` só pode devolver status "assinada" depois de uma
  confirmação real do provedor — nunca antimicipadamente.
- até um provedor real ser configurado (certificado, credenciais, contrato
  jurídico com uma Autoridade Certificadora), a única implementação válida
  em produção é `NullSignatureProvider`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

SIGNATURE_STATUS_PENDENTE = "assinatura_pendente"
SIGNATURE_STATUS_ASSINADA = "assinada"
SIGNATURE_STATUS_REJEITADA = "rejeitada"


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


class NullSignatureProvider(SignatureProvider):
    """Único provedor existente nesta etapa. NUNCA assina — sempre recusa
    a solicitação de forma explícita e auditável, sem exceção nem
    caminho oculto de sucesso simulado."""

    name = "nenhum_configurado"

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
    return NullSignatureProvider()
