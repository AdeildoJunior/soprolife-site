"""Closed fiscal inputs: technical configuration, never patient/clinical free text."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Environment = Literal['mock', 'restricted', 'production']
Flow = Literal['DIRECT', 'HOME', 'PASTORE_A', 'PASTORE_B', 'SPLIT']


class FiscalInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class TaxConfiguration(FiscalInput):
    national_service_code: str | None = Field(None, min_length=1, max_length=40)
    municipal_service_code: str | None = Field(None, min_length=1, max_length=40)
    service_list_item: str | None = Field(None, min_length=1, max_length=40)
    tax_rate: Decimal | None = Field(None, ge=0, le=100, max_digits=8, decimal_places=5)
    tax_regime: str | None = Field(None, min_length=1, max_length=40)
    withholding: bool | None = None
    enforceability: str | None = Field(None, min_length=1, max_length=40)
    incidence: str | None = Field(None, min_length=1, max_length=40)
    municipality: str | None = Field(None, min_length=1, max_length=40)
    ibs_cbs_treatment: str | None = Field(None, min_length=1, max_length=40)
    # These are explicit policy decisions, not runtime assumptions.
    amount_basis: Literal['financial_entry.valor'] | None = None
    competence_rule: Literal['service_date'] | None = None
    issuer: Literal['SOPROLIFE'] | None = None
    recipient: Literal['service_person'] | None = None
    own_revenue_confirmed: Literal[True] | None = None
    validation_reference: str | None = Field(None, min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.:-]+$')

    def missing_fields(self) -> list[str]:
        return sorted(key for key, value in self.model_dump().items() if value is None)


class PolicyCreate(FiscalInput):
    version: str = Field(min_length=1, max_length=60, pattern=r'^[A-Za-z0-9_.-]+$')
    environment: Environment
    flow: Flow
    service: Literal['spirometry'] = 'spirometry'
    effective_from: date
    effective_to: date
    validation_state: Literal['draft', 'validated'] = 'draft'
    configuration: TaxConfiguration

    @model_validator(mode='after')
    def valid_policy(self):
        if self.effective_to < self.effective_from:
            raise ValueError('Vigência invertida.')
        if self.validation_state == 'validated' and self.configuration.missing_fields():
            raise ValueError('Política incompleta: ' + ', '.join(self.configuration.missing_fields()))
        return self


class PrepareRequest(FiscalInput):
    spirometry_exam_id: str = Field(min_length=36, max_length=36)


class OperationRequest(FiscalInput):
    idempotency_key: str = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_.:-]+$')


class BatchRequest(OperationRequest):
    document_ids: list[str] = Field(min_length=1, max_length=100)


class ProductionIssueConfirmation(OperationRequest):
    """M66 — the second click. Echoes back exactly what the modal showed; the
    server refuses unless every field still matches the facts."""
    preparation_id: str = Field(min_length=36, max_length=36)
    amount: str = Field(min_length=1, max_length=20, pattern=r'^\d+\.\d{2}$')
    confirmation: str = Field(min_length=1, max_length=80)
