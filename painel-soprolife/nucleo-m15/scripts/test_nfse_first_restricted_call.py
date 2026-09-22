#!/usr/bin/env python3
"""M29 — deterministic tests for the first-restricted-call launcher's PURE
logic. No real certificate, no real password, no real network call, and the
launcher's own ``main()`` is never invoked here — only its individual
functions, each with a fake/synthetic input.

Uso: python3 painel-soprolife/nucleo-m15/scripts/test_nfse_first_restricted_call.py
"""
import io
import json
import pathlib
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import nfse_first_restricted_call as launcher  # noqa: E402


class TestConfirm(unittest.TestCase):
    def test_exact_phrase_accepted(self):
        with mock.patch("builtins.input", return_value="FRASE-X"):
            launcher.confirm("pergunta", "FRASE-X")  # does not raise

    def test_wrong_phrase_aborts(self):
        with mock.patch("builtins.input", return_value="qualquer coisa"):
            with self.assertRaises(launcher.AbortedByHuman):
                launcher.confirm("pergunta", "FRASE-X")

    def test_empty_answer_aborts(self):
        with mock.patch("builtins.input", return_value=""):
            with self.assertRaises(launcher.AbortedByHuman):
                launcher.confirm("pergunta", "FRASE-X")


class TestCertificateSummarySynthetic(unittest.TestCase):
    """Uses the SAME synthetic-certificate generator M27/M28/M29 already use
    in the application test suite — never anything real."""

    def test_summarizes_synthetic_certificate_without_leaking_secrets(self):
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        from app.services.nfse_national.signer import generate_synthetic_test_certificate

        p12_bytes, password = generate_synthetic_test_certificate()
        with tempfile.TemporaryDirectory() as tmp:
            pfx_path = pathlib.Path(tmp) / "synthetic.pfx"
            pfx_path.write_bytes(p12_bytes)
            summary = launcher.summarize_certificate(pfx_path, password)
        self.assertIn("Synthetic Test", summary.subject_common_name)
        self.assertFalse(summary.expired)
        # The returned object must never carry the password or key material —
        # only the four documented, safe fields.
        self.assertEqual(
            set(summary.__dataclass_fields__.keys()),
            {"subject_common_name", "not_before", "not_after", "expired"},
        )

    def test_wrong_password_fails_closed(self):
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        from app.services.nfse_national.signer import generate_synthetic_test_certificate

        p12_bytes, _password = generate_synthetic_test_certificate()
        with tempfile.TemporaryDirectory() as tmp:
            pfx_path = pathlib.Path(tmp) / "synthetic.pfx"
            pfx_path.write_bytes(p12_bytes)
            with self.assertRaises(Exception):
                launcher.summarize_certificate(pfx_path, "senha-errada-de-proposito")


class TestHttpJson(unittest.TestCase):
    def test_get_request_parses_json_body(self):
        fake_response = io.BytesIO(json.dumps({"ok": True}).encode("utf-8"))
        fake_response.__enter__ = lambda self=fake_response: self
        fake_response.__exit__ = lambda self, *a: False
        with mock.patch("urllib.request.urlopen", return_value=fake_response):
            result = launcher._http_json("GET", "https://example.invalid/x", "tok-fake")
        self.assertEqual(result, {"ok": True})

    def test_http_error_body_is_still_parsed_as_json(self):
        import urllib.error

        error_body = io.BytesIO(json.dumps({"erro": {"codigo": "x"}}).encode("utf-8"))
        exc = urllib.error.HTTPError("https://example.invalid/x", 503, "erro", {}, error_body)
        with mock.patch("urllib.request.urlopen", side_effect=exc):
            result = launcher._http_json("GET", "https://example.invalid/x", "tok-fake")
        self.assertEqual(result["erro"]["codigo"], "x")


class TestSanitizedArtifact(unittest.TestCase):
    def test_artifact_never_contains_secrets_or_xml(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("pathlib.Path.home",
                                                              return_value=pathlib.Path(tmp)):
            path = launcher.write_sanitized_artifact(
                document_id="doc-fake-1",
                preflight_result={"status": "ready_to_send", "request_fingerprint": "abc123",
                                  "staged_artifacts": [{"kind": "dps_signed_xml"}]},
                issue_result={"state": "issuing", "id": "doc-fake-1"},
                final_state={"state": "uncertain", "reconciliation_required": True},
            )
            body = json.loads(path.read_text(encoding="utf-8"))
        flat = json.dumps(body).lower()
        for forbidden in ("senha", "password", "pfx", "p12", "<dps", "xml", "private_key"):
            self.assertNotIn(forbidden, flat)
        self.assertEqual(body["document_id"], "doc-fake-1")
        self.assertEqual(body["preflight_request_fingerprint"], "abc123")
        self.assertEqual(body["final_document_state"], "uncertain")
        self.assertTrue(body["final_document_reconciliation_required"])


class TestReadPasswordFromTtyFailsClosedWithoutTerminal(unittest.TestCase):
    def test_raises_when_no_controlling_terminal(self):
        with mock.patch("builtins.open", side_effect=OSError("no tty")):
            with self.assertRaises(RuntimeError):
                launcher.read_password_from_tty("senha: ")


if __name__ == "__main__":
    unittest.main()
