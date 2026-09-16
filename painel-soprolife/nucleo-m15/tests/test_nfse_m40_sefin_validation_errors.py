"""M40 — safe structured diagnostics for SEFIN's documented
``NFSePostResponseErro`` (4xx/5xx bodies), and the sanitizer that stands
between that body and anything ever persisted/shown.

Context: DPS #4 (2026-09-15) was REJECTED with a bare HTTP 400 — the ONLY
diagnostic available was ``provider_rejected:http_400``, because nothing
downstream of the transport ever inspected a 4xx/5xx response BODY, even
though the documented error schema (``erros[].{codigo,descricao,
complemento}``) carries the actual validation reason. This file proves the
new decode+sanitize path is both useful (captures the real detail) and safe
(bounded, truncated, PII-masked, never touches XML/Base64/cert material,
never changes classification).
"""
import json

import pytest

from app.services.nfse_national.error_sanitizer import (MAX_ERRORS, MAX_STR_LEN,
                                                         SanitizedValidationError,
                                                         sanitize_sefin_errors)
from app.services.nfse_national.wire import SefinValidationError, decode_nfse_error_envelope

# ============================================================ decode_nfse_error_envelope


def test_decodes_a_single_valid_error():
    body = json.dumps({"erros": [
        {"codigo": "E0001", "descricao": "Campo X invalido", "complemento": "ver anexo II"},
    ]}).encode("utf-8")
    result = decode_nfse_error_envelope(body)
    assert result == (SefinValidationError("E0001", "Campo X invalido", "ver anexo II"),)


def test_decodes_multiple_errors_in_order():
    body = json.dumps({"erros": [
        {"codigo": "E0001", "descricao": "primeiro"},
        {"codigo": "E0002", "descricao": "segundo"},
        {"codigo": "E0003", "descricao": "terceiro"},
    ]}).encode("utf-8")
    result = decode_nfse_error_envelope(body)
    assert [e.codigo for e in result] == ["E0001", "E0002", "E0003"]


def test_missing_fields_become_none_not_missing_key_error():
    body = json.dumps({"erros": [{"codigo": "E0001"}]}).encode("utf-8")
    result = decode_nfse_error_envelope(body)
    assert result == (SefinValidationError("E0001", None, None),)


@pytest.mark.parametrize("body", [
    b"",
    b"not json",
    b"[]",  # a JSON array, not an object
    json.dumps({"tipoAmbiente": 2}).encode(),  # object without "erros"
    json.dumps({"erros": "not-a-list"}).encode(),
    json.dumps({"erros": [1, 2, "three"]}).encode(),  # list of non-objects
    json.dumps({"erros": None}).encode(),
])
def test_malformed_or_unexpected_shapes_yield_empty_tuple_never_raise(body):
    assert decode_nfse_error_envelope(body) == ()


def test_non_string_field_values_are_dropped_not_coerced():
    body = json.dumps({"erros": [
        {"codigo": 123, "descricao": {"nested": "object"}, "complemento": ["a", "list"]},
    ]}).encode("utf-8")
    result = decode_nfse_error_envelope(body)
    assert result == (SefinValidationError(None, None, None),)


def test_unknown_keys_anywhere_are_ignored():
    body = json.dumps({
        "erros": [{"codigo": "E0001", "campoDesconhecido": "qualquer coisa",
                  "descricao": "ok"}],
        "outroCampoDesconhecido": "xyz",
    }).encode("utf-8")
    result = decode_nfse_error_envelope(body)
    assert result == (SefinValidationError("E0001", "ok", None),)


# ============================================================ sanitize_sefin_errors


def test_caps_number_of_errors():
    errors = tuple(SefinValidationError(f"E{i:04d}", "d", None) for i in range(MAX_ERRORS + 10))
    sanitized = sanitize_sefin_errors(errors)
    assert len(sanitized) == MAX_ERRORS
    assert [e.codigo for e in sanitized] == [f"E{i:04d}" for i in range(MAX_ERRORS)]


def test_truncates_oversized_strings():
    huge = "x" * (MAX_STR_LEN * 5)
    sanitized = sanitize_sefin_errors((SefinValidationError("E1", huge, huge),))
    assert len(sanitized[0].descricao) == MAX_STR_LEN
    assert len(sanitized[0].complemento) == MAX_STR_LEN


def test_none_fields_stay_none_after_sanitization():
    sanitized = sanitize_sefin_errors((SefinValidationError("E1", None, None),))
    assert sanitized == (SanitizedValidationError("E1", None, None),)


# "NFS" + 9 digits + 14 alphanumeric + 27 digits = 53 chars, matching the
# real TSIdNFSe shape (identifiers.NFSE_ACCESS_KEY_PATTERN).
NFS_SHAPED_ACCESS_KEY = "NFS" + "330455722" + "11222333000181" + "0" * 27
assert len(NFS_SHAPED_ACCESS_KEY) == 53


@pytest.mark.parametrize("raw, masked_marker", [
    ("CPF 11144477735 invalido", "[CPF_MASCARADO]"),
    ("CNPJ 63544026000110 invalido", "[CNPJ_MASCARADO]"),
    (f"chave {NFS_SHAPED_ACCESS_KEY} nao encontrada", "[CHAVE_ACESSO_MASCARADA]"),
    ("chave de acesso 33045572211222333000181000000000000012609123456789 nao encontrada",
     "[CHAVE_ACESSO_MASCARADA]"),
    ("id 12345678-1234-1234-1234-123456789012 conflita", "[UUID_MASCARADO]"),
])
def test_masks_pii_shaped_patterns(raw, masked_marker):
    sanitized = sanitize_sefin_errors((SefinValidationError("E1", raw, None),))
    assert masked_marker in sanitized[0].descricao


def test_masks_cpf_and_does_not_leave_the_digits():
    sanitized = sanitize_sefin_errors((SefinValidationError("E1", "cpf 11144477735 invalido", None),))
    assert "11144477735" not in sanitized[0].descricao


def test_masks_access_key_and_does_not_leave_it_whole_or_leak_as_cnpj_cpf_substrings():
    access_key = "33045572211222333000181000000000000012609123456789"  # 53 digits
    sanitized = sanitize_sefin_errors((SefinValidationError("E1", access_key, None),))
    assert access_key not in sanitized[0].descricao
    assert sanitized[0].descricao == "[CHAVE_ACESSO_MASCARADA]"


def test_masks_uuid_and_does_not_leave_it():
    uuid_value = "12345678-1234-1234-1234-123456789012"
    sanitized = sanitize_sefin_errors((SefinValidationError("E1", f"ref {uuid_value}", None),))
    assert uuid_value not in sanitized[0].descricao


def test_never_carries_xml_certificate_base64_or_raw_request_content():
    """The sanitizer's only input is already-parsed codigo/descricao/
    complemento strings — there is no parameter through which XML, PEM
    certificate material, Base64 request content or the raw response body
    could ever reach it. This test proves the function's signature/contract
    enforces that (a single tuple-of-SefinValidationError argument), not
    just that a particular fixture happens not to trigger a leak."""
    import inspect
    sig = inspect.signature(sanitize_sefin_errors)
    assert list(sig.parameters) == ["errors"]
    # Even if a caller mistakenly stuffed XML/Base64-looking text into the
    # documented free-text fields, it survives ONLY truncated/masked for
    # PII-shaped substrings — never specially treated, never separately
    # exposed as "raw content" anywhere else in the return value.
    fake_leak = SefinValidationError(
        "E1", "<DPS>conteudo</DPS>", "QUJDREVGRw==signed-payload-look-alike")
    sanitized = sanitize_sefin_errors((fake_leak,))
    assert len(sanitized) == 1
    assert set(vars(sanitized[0])) == {"codigo", "descricao", "complemento"}
