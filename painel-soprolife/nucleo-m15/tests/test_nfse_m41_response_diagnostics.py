"""M41 — response-SHAPE diagnostics, for a 4xx/5xx that carries no usable
``erros[]`` (DPS #5's exact case: HTTP 400, nothing extractable by the M40
decoder). Proves the shape summary is bounded (counts/hash/key-names only,
never content) and that the flatter ``ResponseErro`` shape is also
supported alongside the nested ``NFSePostResponseErro`` one.
"""
import hashlib
import json

import pytest

from app.services.nfse_national.response_diagnostics import (MAX_CONTENT_TYPE_LEN,
                                                               MAX_KEY_LEN,
                                                               MAX_TOP_LEVEL_KEYS,
                                                               classify_body_kind,
                                                               decode_documented_error_fields,
                                                               safe_top_level_json_keys,
                                                               summarize_response_shape)
from app.services.nfse_national.wire import SefinValidationError

# ============================================================ classify_body_kind


def test_empty_body_is_empty():
    assert classify_body_kind(b"", "application/json") == "empty"


def test_json_object_is_detected():
    assert classify_body_kind(b'{"a": 1}', "application/json") == "json_object"


def test_json_array_is_detected():
    assert classify_body_kind(b'[1, 2, 3]', "application/json") == "json_array"


def test_html_detected_by_content_type():
    assert classify_body_kind(b"<div>oops</div>", "text/html; charset=utf-8") == "html"


def test_html_detected_by_sniffing_without_content_type():
    assert classify_body_kind(b"<!DOCTYPE html><html>...", None) == "html"
    assert classify_body_kind(b"<html><body>err</body></html>", None) == "html"


def test_plain_text_is_detected():
    assert classify_body_kind(b"internal server error", "text/plain") == "text"


def test_binary_garbage_is_detected():
    assert classify_body_kind(bytes(range(0, 40)), None) == "binary"


def test_bare_json_scalar_is_not_object_or_array():
    # valid JSON, but not one of the two documented container shapes
    assert classify_body_kind(b'"just a string"', None) in {"text", "binary"}
    assert classify_body_kind(b"42", None) in {"text", "binary"}


# ============================================================ safe_top_level_json_keys


def test_returns_key_names_only_never_values():
    body = json.dumps({"codigo": "E0001", "descricao": "algo sigiloso aqui"}).encode()
    keys = safe_top_level_json_keys(body)
    assert keys == ("codigo", "descricao")
    assert "algo sigiloso aqui" not in keys
    assert "E0001" not in keys


def test_caps_number_of_keys():
    body = json.dumps({f"k{i}": i for i in range(MAX_TOP_LEVEL_KEYS + 20)}).encode()
    keys = safe_top_level_json_keys(body)
    assert len(keys) == MAX_TOP_LEVEL_KEYS


def test_truncates_oversized_key_names():
    huge_key = "x" * (MAX_KEY_LEN * 5)
    body = json.dumps({huge_key: 1}).encode()
    keys = safe_top_level_json_keys(body)
    assert len(keys[0]) == MAX_KEY_LEN


def test_none_for_non_object_json():
    assert safe_top_level_json_keys(b"[1, 2, 3]") is None
    assert safe_top_level_json_keys(b"not json") is None
    assert safe_top_level_json_keys(b"") is None


# ============================================================ summarize_response_shape


def test_shape_summary_is_bounded_never_content():
    body = b'{"erros": [{"codigo": "E9999", "descricao": "cpf 11144477735 invalido"}]}'
    shape = summarize_response_shape(body, "application/json; charset=utf-8")
    assert shape.body_kind == "json_object"
    assert shape.content_length == len(body)
    assert shape.sha256 == hashlib.sha256(body).hexdigest()
    assert shape.top_level_keys == ("erros",)
    assert shape.content_type == "application/json; charset=utf-8"
    # The hash/length/keys never carry the body's actual sensitive content.
    dumped = repr(shape)
    assert "11144477735" not in dumped
    assert "E9999" not in dumped


def test_content_type_is_truncated():
    huge_ct = "text/plain" + "x" * (MAX_CONTENT_TYPE_LEN * 3)
    shape = summarize_response_shape(b"oops", huge_ct)
    assert len(shape.content_type) == MAX_CONTENT_TYPE_LEN


def test_empty_body_shape():
    shape = summarize_response_shape(b"", None)
    assert shape.body_kind == "empty"
    assert shape.content_length == 0
    assert shape.top_level_keys is None
    assert shape.sha256 == hashlib.sha256(b"").hexdigest()


# ============================================================ decode_documented_error_fields


def test_nested_erros_shape_still_works():
    body = json.dumps({"erros": [{"codigo": "E1", "descricao": "d1"}]}).encode()
    result = decode_documented_error_fields(body)
    assert result == (SefinValidationError("E1", "d1", None),)


def test_flat_response_erro_shape_is_supported():
    """The second documented shape (ResponseErro): fields directly at the
    top level, not nested in an `erros` array."""
    body = json.dumps({"codigo": "E2", "mensagem": "algo deu errado"}).encode()
    result = decode_documented_error_fields(body)
    assert len(result) == 1
    assert result[0].codigo == "E2"
    assert result[0].mensagem == "algo deu errado"
    assert result[0].descricao is None


def test_flat_shape_supports_erro_field_too():
    body = json.dumps({"erro": "falha de validacao"}).encode()
    result = decode_documented_error_fields(body)
    assert result == (SefinValidationError(None, None, None, None, "falha de validacao"),)


def test_undocumented_fields_never_read_in_flat_shape():
    body = json.dumps({"codigo": "E3", "stackTrace": "top secret internals",
                       "internalDebugInfo": {"leak": "me"}}).encode()
    result = decode_documented_error_fields(body)
    assert result == (SefinValidationError("E3", None, None, None, None),)


def test_empty_object_yields_no_entries():
    assert decode_documented_error_fields(b"{}") == ()


def test_no_documented_fields_present_yields_no_entries():
    body = json.dumps({"tipoAmbiente": 2, "dataHoraProcessamento": "2026-09-15"}).encode()
    assert decode_documented_error_fields(body) == ()


@pytest.mark.parametrize("body", [b"", b"not json", b"[1, 2, 3]", b"null"])
def test_malformed_or_unexpected_bodies_never_raise(body):
    assert decode_documented_error_fields(body) == ()


def test_dps5_actual_case_empty_body_yields_nothing_but_never_raises():
    """DPS #5's real shape: HTTP 400 with a body that had no usable
    erros[]. This must never raise, and must correctly report emptiness."""
    for candidate_body in (b"", b"Bad Request", b"<html><body>400</body></html>"):
        assert decode_documented_error_fields(candidate_body) == ()
        shape = summarize_response_shape(candidate_body, None)
        assert shape.body_kind in {"empty", "text", "html"}
