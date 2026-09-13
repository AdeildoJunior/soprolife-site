"""XSD validation against the vendored official restricted schema package.

Schemas come from ``esquemas-nfse-rtc-prodrest-v1-01-20260727.zip`` (official
Produção Restrita documentation page, retrieved 2026-09-13; SHA-256 recorded
in OFFICIAL_SOURCES_USED.md). Validation must never be weakened to make a
payload pass — a schema-invalid DPS is a bug in the builder, not the schema.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import xmlschema
from lxml import etree

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas" / "restricted"
DPS_SCHEMA_PATH = SCHEMA_DIR / "DPS_v1.01.xsd"
NFSE_SCHEMA_PATH = SCHEMA_DIR / "NFSe_v1.01.xsd"


class XsdValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors) or "Documento não conforme ao XSD.")


@dataclass(frozen=True)
class ValidatedXml:
    root: etree._Element


@lru_cache(maxsize=None)
def _schema(path: Path) -> xmlschema.XMLSchema:
    if not path.is_file():
        raise FileNotFoundError(
            f"Schema oficial ausente em {path}. O pacote de schemas restritos "
            "precisa estar vendorizado antes de qualquer validação."
        )
    return xmlschema.XMLSchema(str(path))


def validate_dps_xml(data: bytes) -> ValidatedXml:
    """Validate raw DPS XML bytes against DPS_v1.01.xsd. Fail-closed: any
    schema violation raises, listing every error found (not just the first)."""
    schema = _schema(DPS_SCHEMA_PATH)
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError as exc:
        raise XsdValidationError([f"XML malformado: {exc}"]) from exc
    errors = [str(err) for err in schema.iter_errors(data)]
    if errors:
        raise XsdValidationError(errors)
    return ValidatedXml(root=root)


def validate_nfse_xml(data: bytes) -> ValidatedXml:
    """Validate a returned NFS-e XML against NFSe_v1.01.xsd (reconciliation path)."""
    schema = _schema(NFSE_SCHEMA_PATH)
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError as exc:
        raise XsdValidationError([f"XML malformado: {exc}"]) from exc
    errors = [str(err) for err in schema.iter_errors(data)]
    if errors:
        raise XsdValidationError(errors)
    return ValidatedXml(root=root)
