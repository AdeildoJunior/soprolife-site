"""M44 — the official ``NFSePostResponseErro.erros[]`` item type is
``MensagemProcessamento``: ``mensagem``, ``parametros``, ``codigo``,
``descricao``, ``complemento`` — ALL FIVE. Until this fix,
``wire.decode_nfse_error_envelope`` only ever read ``codigo``/
``descricao``/``complemento`` per item: an item shaped exactly like
``{"mensagem": "..."}`` (no other key) silently decoded to an all-``None``
entry that STILL counted as "found" (blocking every fallback), so nothing
downstream ever reported it as decoded OR as unrecognized. This is the
exact blind spot behind DPS #6/#7's HTTP 400 with an ``erros[]`` array
present but seemingly empty.
"""
import json

import pytest

from app.services.nfse_national.error_sanitizer import (MAX_PARAMETROS, sanitize_sefin_errors)
from app.services.nfse_national.response_diagnostics import (classify_erros_array,
                                                               decode_documented_error_fields)
from app.services.nfse_national.wire import SefinValidationError, decode_nfse_error_envelope

# ============================================================ the exact DPS #6/#7 blind spot


def test_mensagem_only_item_was_silently_dropped_before_m44():
    """Regression proof: BEFORE this fix, decode_nfse_error_envelope's
    per-item extractor never read "mensagem" — this test locks in that the
    CURRENT (fixed) behavior actually surfaces it."""
    body = json.dumps({"erros": [{"mensagem": "campo X invalido"}]}).encode()
    result = decode_nfse_error_envelope(body)
    assert result == (SefinValidationError(codigo=None, descricao=None, complemento=None,
                                           mensagem="campo X invalido", parametros=None),)


def test_mensagem_only_item_is_now_reported_as_decoded_not_unrecognized():
    body = json.dumps({"erros": [{"mensagem": "campo X invalido"}]}).encode()
    summary = classify_erros_array(body)
    assert summary.status == "erros_nonempty_decoded"
    assert summary.item_count == 1


def test_an_item_with_only_unknown_keys_is_still_reported_unrecognized():
    """The distinction still holds for a genuinely undecodable item — this
    is NOT a regression of the M41 "erros_nonempty_unrecognized" case."""
    body = json.dumps({"erros": [{"someUndocumentedField": "x"}]}).encode()
    summary = classify_erros_array(body)
    assert summary.status == "erros_nonempty_unrecognized"
    assert summary.item_count == 1
    assert summary.item_field_names == ("someUndocumentedField",)


def test_empty_erros_array_is_classified_separately():
    body = json.dumps({"erros": []}).encode()
    summary = classify_erros_array(body)
    assert summary.status == "erros_empty"
    assert summary.item_count == 0


def test_no_erros_key_at_all_is_erros_empty():
    body = json.dumps({"tipoAmbiente": 2}).encode()
    summary = classify_erros_array(body)
    assert summary.status == "erros_empty"


def test_malformed_json_is_erros_empty_never_raises():
    assert classify_erros_array(b"not json").status == "erros_empty"
    assert classify_erros_array(b"").status == "erros_empty"


# ============================================================ full MensagemProcessamento shape


def test_all_five_fields_decoded_from_one_item():
    body = json.dumps({"erros": [{
        "mensagem": "m", "parametros": ["p1", "p2"], "codigo": "E1",
        "descricao": "d", "complemento": "c",
    }]}).encode()
    result = decode_nfse_error_envelope(body)
    assert result == (SefinValidationError(
        codigo="E1", descricao="d", complemento="c", mensagem="m",
        parametros=("p1", "p2"),
    ),)


def test_multiple_items_each_with_partial_fields():
    body = json.dumps({"erros": [
        {"codigo": "E1"},
        {"mensagem": "m2"},
        {"parametros": [1, 2, 3]},
    ]}).encode()
    result = decode_nfse_error_envelope(body)
    assert len(result) == 3
    assert result[0].codigo == "E1"
    assert result[1].mensagem == "m2"
    assert result[2].parametros == (1, 2, 3)


# ============================================================ capitalization variants


@pytest.mark.parametrize("key", ["Mensagem", "Codigo", "Descricao", "Complemento", "Parametros"])
def test_capitalized_field_variant_is_recognized(key):
    """Only the exact-lowercase-then-Capitalized casing is ever tried —
    justified by the CNC consulta API (a different but also-official SEFIN
    endpoint, M42/M43) already observed using PascalCase for every field."""
    value = ["x"] if key == "Parametros" else "valor"
    body = json.dumps({"erros": [{key: value}]}).encode()
    result = decode_nfse_error_envelope(body)
    field = key.lower()
    got = getattr(result[0], field)
    if field == "parametros":
        assert got == ("x",)
    else:
        assert got == "valor"


def test_lowercase_and_capitalized_do_not_double_up():
    """If somehow both casings were present, lowercase (the documented
    form) wins — never silently merges/overwrites unpredictably."""
    body = json.dumps({"erros": [{"codigo": "lower", "Codigo": "upper"}]}).encode()
    result = decode_nfse_error_envelope(body)
    assert result[0].codigo == "lower"


def test_unknown_other_casings_are_never_guessed():
    body = json.dumps({"erros": [{"CODIGO": "all-caps-never-read"}]}).encode()
    result = decode_nfse_error_envelope(body)
    assert result[0].codigo is None


# ============================================================ parametros sanitization


def test_parametros_capped_in_length():
    values = list(range(MAX_PARAMETROS + 10))
    body = json.dumps({"erros": [{"codigo": "E1", "parametros": values}]}).encode()
    decoded = decode_nfse_error_envelope(body)
    sanitized = sanitize_sefin_errors(decoded)
    assert len(sanitized[0].parametros) == MAX_PARAMETROS
    assert list(sanitized[0].parametros) == values[:MAX_PARAMETROS]


def test_parametros_string_items_are_masked_for_pii():
    body = json.dumps({"erros": [
        {"codigo": "E1", "parametros": ["cpf 11144477735 invalido"]},
    ]}).encode()
    decoded = decode_nfse_error_envelope(body)
    sanitized = sanitize_sefin_errors(decoded)
    assert "11144477735" not in sanitized[0].parametros[0]
    assert "[CPF_MASCARADO]" in sanitized[0].parametros[0]


def test_parametros_non_primitive_items_are_dropped_not_coerced():
    body = json.dumps({"erros": [
        {"codigo": "E1", "parametros": [{"nested": "object"}, [1, 2], "ok", 5]},
    ]}).encode()
    decoded = decode_nfse_error_envelope(body)
    assert decoded[0].parametros == ("ok", 5)


def test_parametros_absent_is_none_not_empty_tuple():
    body = json.dumps({"erros": [{"codigo": "E1"}]}).encode()
    decoded = decode_nfse_error_envelope(body)
    assert decoded[0].parametros is None


def test_parametros_non_list_value_is_none():
    body = json.dumps({"erros": [{"codigo": "E1", "parametros": "not-a-list"}]}).encode()
    decoded = decode_nfse_error_envelope(body)
    assert decoded[0].parametros is None


# ============================================================ never leaks raw body/XML/certificate


def test_never_carries_raw_body_xml_or_certificate_content():
    fake_leak = json.dumps({"erros": [
        {"mensagem": "<DPS>conteudo xml</DPS>", "parametros": ["QUJDREVGRw==base64-look-alike"]},
    ]}).encode()
    decoded = decode_nfse_error_envelope(fake_leak)
    sanitized = sanitize_sefin_errors(decoded)
    # The text survives (it's documented free text) but only truncated/
    # masked for PII-shaped substrings — never specially flagged or
    # separately exposed as "this was XML/Base64".
    assert sanitized[0].mensagem == "<DPS>conteudo xml</DPS>"
    assert sanitized[0].parametros == ("QUJDREVGRw==base64-look-alike",)
    # The function signatures involved never accept a certificate, request
    # body, or anything beyond the response bytes — structural guarantee.
    import inspect
    assert list(inspect.signature(decode_nfse_error_envelope).parameters) == ["body"]


# ============================================================ integration via decode_documented_error_fields


def test_decode_documented_error_fields_now_surfaces_mensagem_only_items():
    body = json.dumps({"erros": [{"mensagem": "algo especifico"}]}).encode()
    result = decode_documented_error_fields(body)
    assert result[0].mensagem == "algo especifico"
