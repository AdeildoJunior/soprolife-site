#!/usr/bin/env python3
"""M60 — read-only certificate margin check. Offline, no network, no writes.

WHY A HUMAN HAS TO RUN THIS

The PKCS#12 password is entered at the terminal and nowhere else: not in an
environment variable, not in a file, not in the database, not in this
repository. That is deliberate (M50) and it means an agent cannot open the
bundle on its own — so an agent cannot tell you, first-hand, what is inside
it. This script is how a human gets that answer without the password
leaving the terminal.

WHAT IT PRINTS

Safe metadata only: subject common name, validity window, whether it has
expired, days remaining, and the verdict of the production renewal margin
(``M15_NFSE_PRODUCTION_CERTIFICATE_MIN_DAYS``, default 30). Never the
password, never the private key, never the raw bytes.

WHAT IT DOES NOT DO

No network, no renewal, no download, no write of any kind. It opens one
file for reading and prints a verdict.

Usage:
    python scripts/nfse_m60_certificate_margin_check.py \\
        [--path ~/.local/share/soprolife/secrets/nfse/soprolife-nfse.pfx] \\
        [--min-days 30]
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.nfse_national.certificate_guard import (            # noqa: E402
    DEFAULT_PRODUCTION_MIN_DAYS_REMAINING, evaluate_certificate_margin)
from app.services.nfse_national.readiness import CertificateSummary   # noqa: E402
from app.services.nfse_national.signer import (                       # noqa: E402
    SignatureError, load_pkcs12_certificate)

DEFAULT_PATH = "~/.local/share/soprolife/secrets/nfse/soprolife-nfse.pfx"


def _read_password_from_tty() -> str:
    """Only from the controlling terminal. A piped or redirected stdin is
    refused rather than silently accepted — the same discipline M50 used."""
    if not sys.stdin.isatty():
        print("ERRO: a senha só é aceite a partir do terminal. Execute "
              "interativamente.", file=sys.stderr)
        raise SystemExit(2)
    password = getpass.getpass("Senha do certificado (não é exibida, não é guardada): ")
    if not password:
        print("ERRO: senha vazia — abortando.", file=sys.stderr)
        raise SystemExit(2)
    return password


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=DEFAULT_PATH)
    parser.add_argument("--min-days", type=int,
                        default=int(os.environ.get(
                            "M15_NFSE_PRODUCTION_CERTIFICATE_MIN_DAYS",
                            DEFAULT_PRODUCTION_MIN_DAYS_REMAINING)))
    args = parser.parse_args()

    path = Path(args.path).expanduser()
    if not path.is_file():
        print(f"ERRO: certificado não encontrado em {path}", file=sys.stderr)
        return 2

    mode = oct(path.stat().st_mode & 0o777)
    print(f"arquivo            : {path}")
    print(f"permissões         : {mode}  {'(ok)' if mode == '0o600' else '(ATENÇÃO: esperado 0o600)'}")

    password = _read_password_from_tty()
    try:
        loaded = load_pkcs12_certificate(path.read_bytes(), password)
    except SignatureError as exc:
        print(f"ERRO ao abrir o bundle: {exc}", file=sys.stderr)
        return 1
    finally:
        password = "\0" * len(password)
        del password

    certificate = loaded.certificate
    not_before = certificate.not_valid_before_utc
    not_after = certificate.not_valid_after_utc
    now = datetime.now(timezone.utc)
    summary = CertificateSummary(
        subject_common_name=certificate.subject.rfc4514_string(),
        not_before=not_before, not_after=not_after, expired=now > not_after)

    print(f"titular            : {summary.subject_common_name}")
    print(f"válido de          : {not_before.isoformat()}")
    print(f"válido até         : {not_after.isoformat()}")
    print(f"agora (UTC)        : {now.isoformat(timespec='seconds')}")
    print(f"dias restantes     : {(not_after - now).days}")

    margin = evaluate_certificate_margin(summary, min_days_remaining=args.min_days, now=now)
    print()
    print(f"margem exigida     : {args.min_days} dias")
    print(f"veredito           : {'PASSA' if margin.valid else 'RECUSA'}  ({margin.reason})")
    if margin.valid:
        from datetime import timedelta
        closes = not_after - timedelta(days=args.min_days)
        print(f"janela fecha em    : {closes.date()}  "
              f"({(closes - now).days} dias a partir de hoje)")
        print()
        print("Nota: a janela útil termina QUANDO A MARGEM FECHA, não na data de "
              "validade. Renove antes disso.")
    return 0 if margin.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
