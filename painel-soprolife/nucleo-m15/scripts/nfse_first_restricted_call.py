#!/usr/bin/env python3
"""M29 — human-controlled launcher for the FIRST real restricted NFS-e call.

BUILT, NEVER EXECUTED, as part of the M29 mission. This script does not run
itself: it exists so the NEXT explicitly-authorized human session knows
exactly where the buttons are and what to check before pressing them.

What this script does NOT do:
- it never enables ``M15_NFSE_RESTRICTED_NETWORK_ENABLED`` itself — that
  variable lives only in the API process's own environment (systemd
  EnvironmentFile), and this script has no remote way to flip it. It prints
  the exact command and WAITS for a human to confirm they ran it themselves;
- it never reads, logs, or persists the PFX password anywhere except a
  single local variable for the duration of one local certificate-open
  call, discarded immediately after;
- it never sends the password over HTTP — the live API process needs its
  OWN copy of the password in ITS OWN environment (configured out-of-band
  by the human, exactly as documented in
  painel-soprolife/docs/m28-nfse-restrito-primeira-chamada.md), independent
  of anything this script does;
- it never performs more than ONE issue operation per invocation, and it
  closes the network gate reminder immediately after that one call,
  regardless of outcome.

Usage (when a human is explicitly ready — see the mission's ABSOLUTE STOP
LINE first):

    python3 scripts/nfse_first_restricted_call.py \\
        --api-base https://<host>/painel-soprolife/api/m15 \\
        --document-id <fiscal_document_id> \\
        --pfx-path /home/fedorasurf/.local/share/soprolife/secrets/nfse/soprolife-nfse.pfx

The script will:
  1. ask for the PFX password on /dev/tty (hidden, never echoed);
  2. open the certificate LOCALLY (no network) and print a sanitized summary
     (subject CN, validity dates, expired?) — never the password or the key;
  3. discard the password from memory;
  4. call the live API's read-only ``/fiscal/status`` and run the OFFLINE
     preflight for --document-id, printing the sanitized result;
  5. STOP and require the exact confirmation phrase before proceeding;
  6. print the exact command to open the restricted network gate, and wait
     for the human to confirm they ran it AND restarted the API process;
  7. re-check ``/fiscal/status`` to confirm the gate is now reported open;
  8. perform EXACTLY ONE issue call for --document-id;
  9. immediately print the exact command to close the gate again;
 10. fetch and print the resulting document state; if UNCERTAIN, print the
     exact reconciliation command instead of retrying automatically;
 11. write one sanitized JSON artifact (document id, request fingerprint,
     final state, timestamps — never XML content, never secrets) under
     ~/soprolife-relatorios/nfse-first-restricted-call-<timestamp>.json.

Abort at any prompt with Ctrl+C or a non-matching answer: nothing mutates
until step 8, and step 8 only ever runs once per invocation.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CONFIRMATION_PHRASE = "AUTORIZO A PRIMEIRA CHAMADA RESTRITA REAL"
GATE_OPEN_CONFIRMATION_PHRASE = "PORTAO_ABERTO_E_API_REINICIADA"


class AbortedByHuman(RuntimeError):
    """Raised whenever a confirmation step is declined — never a bug."""


@dataclass(frozen=True)
class CertificateSummary:
    subject_common_name: str | None
    not_before: str
    not_after: str
    expired: bool


def read_password_from_tty(prompt: str) -> str:
    """Reads a hidden secret directly from the controlling terminal
    (``/dev/tty``), NEVER from stdin — so this still works even if stdin is
    piped (e.g. from a wrapper script), and a shell history/log can never
    capture it. Raises if no controlling terminal is available: this
    launcher refuses to accept the password any other way.
    """
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as tty:
            return getpass.getpass(prompt, stream=tty)
    except OSError as exc:
        raise RuntimeError(
            "Nenhum terminal de controle (/dev/tty) disponível — este "
            "launcher recusa aceitar a senha por qualquer outro canal "
            "(nunca argumento de linha de comando, nunca variável de "
            "ambiente, nunca stdin redirecionado)."
        ) from exc


def summarize_certificate(pfx_path: Path, password: str) -> CertificateSummary:
    """Opens the PFX exactly once, locally, and returns ONLY safe metadata.

    Imports the application's own certificate loader so this launcher never
    duplicates (and never diverges from) the fail-closed PKCS#12 handling
    already reviewed in ``app/services/nfse_national/signer.py``.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.services.nfse_national.signer import load_pkcs12_certificate  # noqa: E402

    raw = pfx_path.read_bytes()
    loaded = load_pkcs12_certificate(raw, password)
    cert = loaded.certificate
    not_before = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before
    not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
    now = datetime.now(timezone.utc)
    expired = now > not_after.replace(tzinfo=timezone.utc) if not_after.tzinfo is None else now > not_after
    cn = None
    try:
        from cryptography.x509.oid import NameOID

        attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        cn = attrs[0].value if attrs else None
    except Exception:
        cn = None
    return CertificateSummary(
        subject_common_name=cn, not_before=not_before.isoformat(),
        not_after=not_after.isoformat(), expired=bool(expired),
    )


def _http_json(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 — operator-supplied HTTPS host
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def fetch_status(api_base: str, token: str) -> dict:
    return _http_json("GET", api_base.rstrip("/") + "/fiscal/status", token)


def run_preflight(api_base: str, token: str, document_id: str) -> dict:
    url = api_base.rstrip("/") + f"/fiscal/documentos/{document_id}/preflight"
    return _http_json("POST", url, token)


def issue_once(api_base: str, token: str, document_id: str, idempotency_key: str) -> dict:
    url = api_base.rstrip("/") + f"/fiscal/documentos/{document_id}/emitir-mock"
    return _http_json("POST", url, token, {"idempotency_key": idempotency_key})


def inspect_document(api_base: str, token: str, document_id: str) -> dict:
    return _http_json("GET", api_base.rstrip("/") + f"/fiscal/documentos/{document_id}", token)


def confirm(prompt: str, expected_phrase: str) -> None:
    answer = input(f"{prompt}\nDigite exatamente: {expected_phrase!r}\n> ")
    if answer.strip() != expected_phrase:
        raise AbortedByHuman(f"Confirmação não recebida para: {prompt!r}")


def write_sanitized_artifact(*, document_id: str, preflight_result: dict, issue_result: dict,
                             final_state: dict) -> Path:
    """Never the signed/unsigned XML, never a secret — only identifiers,
    the request fingerprint, and final state, matching the same evidence
    discipline as ``FiscalArtifact``/``FiscalAttempt`` in the database."""
    out_dir = Path.home() / "soprolife-relatorios"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"nfse-first-restricted-call-{stamp}.json"
    payload = {
        "document_id": document_id,
        "preflight_request_fingerprint": preflight_result.get("request_fingerprint"),
        "preflight_status": preflight_result.get("status"),
        "issue_http_result": {k: v for k, v in issue_result.items() if k != "preparation"},
        "final_document_state": final_state.get("state"),
        "final_document_reconciliation_required": final_state.get("reconciliation_required"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    out_path.chmod(0o600)
    return out_path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-base", required=True,
                        help="Ex.: https://<host>/painel-soprolife/api/m15")
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--pfx-path", required=True, type=Path)
    parser.add_argument("--token", help="Token do gestor/admin. Se ausente, é pedido no /dev/tty.")
    args = parser.parse_args(argv)

    print("=" * 70)
    print("NFS-e — PRIMEIRA CHAMADA RESTRITA REAL — launcher humano-controlado")
    print("=" * 70)
    print("Leia painel-soprolife/docs/m28-nfse-restrito-primeira-chamada.md")
    print("do início ao fim ANTES de continuar, se ainda não leu.")
    print()

    token = args.token or read_password_from_tty("Token Bearer (gestor/admin), oculto: ")

    print("\n[1/6] Abrindo o certificado LOCALMENTE (sem rede) para validar a senha…")
    password = read_password_from_tty(f"Senha do PKCS#12 em {args.pfx_path} (oculta): ")
    try:
        summary = summarize_certificate(args.pfx_path, password)
    finally:
        password = None  # noqa: F841 — descarta a referência explicitamente, nunca reaproveitada
    print(f"  Titular (CN): {summary.subject_common_name}")
    print(f"  Validade: {summary.not_before} a {summary.not_after}")
    print(f"  Expirado: {summary.expired}")
    if summary.expired:
        print("BLOQUEADO: certificado expirado. Abortando.")
        return 1

    print("\n[2/6] Consultando /fiscal/status (somente leitura)…")
    status = fetch_status(args.api_base, token)
    block = status.get("restricted_provider_foundation", {})
    print(json.dumps(block.get("readiness", {}), indent=2, ensure_ascii=False))
    if block.get("readiness", {}).get("blockers"):
        print("BLOQUEADO: ainda há bloqueios de prontidão. Resolva-os antes de continuar.")
        return 1

    print("\n[3/6] Rodando o preflight OFFLINE (nenhuma chamada de rede) "
         f"para o documento {args.document_id}…")
    preflight_result = run_preflight(args.api_base, token, args.document_id)
    print(json.dumps(preflight_result, indent=2, ensure_ascii=False))
    if preflight_result.get("status") != "ready_to_send":
        print("BLOQUEADO: preflight não chegou a READY_TO_SEND. Abortando.")
        return 1

    try:
        confirm(
            "\n[4/6] Confirme que revisou o preflight acima e AUTORIZA prosseguir "
            "para abrir o portão de rede restrito por UMA chamada.",
            CONFIRMATION_PHRASE,
        )

        print("\n[5/6] Ação exata para abrir o portão (editar o EnvironmentFile PRIVADO "
             "do systemd, NUNCA o .env do worktree):")
        print("      M15_NFSE_RESTRICTED_NETWORK_ENABLED=true")
        print("      systemctl restart <unidade-da-api-m15>")
        confirm("Confirme que já fez isso e que /fiscal/status mostra o portão aberto.",
               GATE_OPEN_CONFIRMATION_PHRASE)

        status_after = fetch_status(args.api_base, token)
        if not status_after.get("restricted_provider_foundation", {}).get("network_gate_enabled"):
            print("BLOQUEADO: /fiscal/status ainda reporta o portão fechado. Abortando "
                 "sem tentar emitir.")
            return 1

        print("\n[6/6] Enviando A ÚNICA chamada de emissão autorizada…")
        idempotency_key = f"m29-first-restricted-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        issue_result = issue_once(args.api_base, token, args.document_id, idempotency_key)
        print(json.dumps(issue_result, indent=2, ensure_ascii=False))
    finally:
        print("\nAção exata para FECHAR o portão de novo, AGORA:")
        print("      M15_NFSE_RESTRICTED_NETWORK_ENABLED=false")
        print("      systemctl restart <unidade-da-api-m15>")

    final_state = inspect_document(args.api_base, token, args.document_id)
    print("\nEstado final do documento:")
    print(json.dumps({"state": final_state.get("state"),
                      "reconciliation_required": final_state.get("reconciliation_required")},
                     indent=2, ensure_ascii=False))
    if final_state.get("reconciliation_required"):
        print("\nEstado UNCERTAIN/reconciling: NÃO reenvie. Use a reconciliação explícita:")
        print(f"  POST {args.api_base}/fiscal/documentos/{args.document_id}/reconciliar")

    artifact_path = write_sanitized_artifact(
        document_id=args.document_id, preflight_result=preflight_result,
        issue_result=issue_result, final_state=final_state,
    )
    print(f"\nEvidência sanitizada preservada em: {artifact_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except AbortedByHuman as exc:
        print(f"\nABORTADO PELO OPERADOR: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nABORTADO (Ctrl+C).", file=sys.stderr)
        raise SystemExit(130)
