#!/usr/bin/env python3
"""M70 — write the PASTORE production fiscal policy into a database.

ONE ROW: the validated ``FiscalPolicy`` ``SOPROLIFE-PRODUCTION-PASTORE-v1``.
Its values are not arguments — they come from
``app.services.nfse_national.production_profile.pastore_production_policy``,
which builds them from the same tax body as DIRECT/HOME and differs only in
``amount_basis`` (the partnership rule) and provenance.

It does not issue anything, allocate a DPS, create a document, touch a
FinancialEntry or a PartnerSettlement, or open a socket.

ACTIVATION IS PROSPECTIVE: ``effective_from`` is the M70 deploy date. Pastore
exams performed before it stay blocked (``partner_before_fiscal_activation``).

Idempotent and fail-closed exactly like the M60 script: ``nfse.create_policy``
returns the existing row for an identical payload and refuses a different one
under the same version. Dry run by default; ``--apply`` needs
``M15_DATABASE_URL`` named explicitly and ``--actor``.

Usage:
    M15_DATABASE_URL=... python scripts/nfse_m70_pastore_policy_apply.py [--apply --actor <user-id>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import nfse                                    # noqa: E402
from app.services.nfse_national.production_profile import (      # noqa: E402
    PASTORE_DIFFERS_FROM_DIRECT,
    pastore_production_policy,
    production_policy,
)


def _describe() -> dict:
    policy = pastore_production_policy()
    pastore = policy.configuration.model_dump(mode="json")
    direct = production_policy("DIRECT").configuration.model_dump(mode="json")
    return {
        "version": policy.version,
        "environment": policy.environment,
        "flow": policy.flow,
        "effective_from": policy.effective_from.isoformat(),
        "effective_to": policy.effective_to.isoformat(),
        "validation_state": policy.validation_state,
        "configuration": pastore,
        "differs_from_direct": sorted(k for k in pastore if pastore[k] != direct[k]),
        "allowed_differences": sorted(PASTORE_DIFFERS_FROM_DIRECT),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually write")
    parser.add_argument("--actor", default=None, help="user id (required with --apply)")
    args = parser.parse_args()

    plan = _describe()
    print(json.dumps(plan, indent=2, ensure_ascii=False))
    if set(plan["differs_from_direct"]) - set(plan["allowed_differences"]):
        print("ERRO: a política PASTORE diverge da DIRECT fora do permitido.", file=sys.stderr)
        return 3
    if not args.apply:
        print("\nDRY RUN — nada foi escrito.", file=sys.stderr)
        return 0
    if not os.environ.get("M15_DATABASE_URL"):
        print("ERRO: M15_DATABASE_URL não definido.", file=sys.stderr)
        return 2
    if not args.actor:
        print("ERRO: --actor é obrigatório com --apply.", file=sys.stderr)
        return 2

    from app.db import get_sessionmaker

    with get_sessionmaker()() as db:
        created = nfse.create_policy(db, pastore_production_policy(), args.actor)
        print(f"fiscal_policy: {created.version} flow={created.flow} "
              f"effective_from={created.effective_from} (id={created.id})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
