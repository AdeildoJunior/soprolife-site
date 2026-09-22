#!/usr/bin/env python3
"""M60 — write the production fiscal profile and both flow policies into a database.

WHAT THIS DOES, AND WHAT IT CANNOT DO

It writes three rows: the versioned ``NationalDpsConfiguration`` for
``environment='production'`` (tpAmb=1) and the validated ``FiscalPolicy`` for
each of HOME and DIRECT. The values are not arguments — they come from
``app.services.nfse_national.production_profile``, which states them once,
with provenance per field, under code review. Nothing here can invent a tax
parameter.

It cannot issue anything, and it does not bring an issuance any closer than
the configuration does. Writing these rows satisfies items 2-3 of the M59
blocker list. It does not touch the production network gate, it does not
touch the human authorization (which has no configuration path at all), and
it opens no socket.

IDEMPOTENT AND FAIL-CLOSED

``fiscal_config.create_version`` and ``nfse.create_policy`` both refuse to
mutate a version that already exists with a different payload — fiscal
history that may already have backed a signed DPS is immutable. Re-running
this script with the same profile is therefore a no-op; re-running it after
editing the profile module fails loudly instead of rewriting history. Bump
the version instead.

DRY RUN BY DEFAULT

With no flags it prints what it WOULD write and exits without opening a
write transaction. ``--apply`` is required to write, and the target database
must be named explicitly through ``M15_DATABASE_URL`` — there is no default,
so this can never quietly hit whatever database happened to be configured.

M60 ran this in dry-run only. No live database was written.

Usage:
    M15_DATABASE_URL=sqlite:////path/to/target.db \\
        python scripts/nfse_m60_production_profile_apply.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import nfse                                    # noqa: E402
from app.services.nfse_national import fiscal_config             # noqa: E402
from app.services.nfse_national.production_profile import (      # noqa: E402
    DERIVED_FROM_POLICY_VERSIONS,
    DERIVED_FROM_VERSION,
    PRODUCTION_EFFECTIVE_FROM,
    production_configuration,
    production_policies,
)

ENVIRONMENT = "production"


def _describe() -> dict:
    configuration = production_configuration()
    return {
        "environment": ENVIRONMENT,
        "national_dps_configuration": {
            "version": configuration.version,
            "effective_from": PRODUCTION_EFFECTIVE_FROM.isoformat(),
            "validation_state": "validated",
            "tp_amb": configuration.tp_amb,
            "derived_from": DERIVED_FROM_VERSION,
            "configuration": configuration.model_dump(mode="json"),
        },
        "fiscal_policies": [
            {
                "version": policy.version,
                "flow": policy.flow,
                "effective_from": policy.effective_from.isoformat(),
                "effective_to": policy.effective_to.isoformat(),
                "validation_state": policy.validation_state,
                "configuration": policy.configuration.model_dump(mode="json"),
            }
            for policy in production_policies()
        ],
        "derived_from_policies": list(DERIVED_FROM_POLICY_VERSIONS),
        "writes_nothing_else": [
            "no network gate is touched",
            "no human authorization is granted (it has no configuration path)",
            "no document, DPS or attempt is created",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="actually write. Without it, this is a dry run.")
    parser.add_argument("--actor", default=None,
                        help="user id to record as the author of the rows "
                             "(required with --apply)")
    args = parser.parse_args()

    plan = _describe()
    print(json.dumps(plan, indent=2, ensure_ascii=False))

    if not args.apply:
        print("\nDRY RUN — nada foi escrito. Use --apply --actor <user-id> para gravar.",
              file=sys.stderr)
        return 0

    database_url = os.environ.get("M15_DATABASE_URL")
    if not database_url:
        print("ERRO: M15_DATABASE_URL não definido. O banco de destino tem de ser "
              "nomeado explicitamente — este script nunca escolhe um por si.",
              file=sys.stderr)
        return 2
    if not args.actor:
        print("ERRO: --actor é obrigatório com --apply (as linhas fiscais registam "
              "quem as criou).", file=sys.stderr)
        return 2

    from app.db import get_sessionmaker

    with get_sessionmaker()() as db:
        row = fiscal_config.create_version(
            db, environment=ENVIRONMENT, effective_from=PRODUCTION_EFFECTIVE_FROM,
            validation_state="validated", configuration=production_configuration(),
            actor=args.actor)
        print(f"national_dps_configuration: {row.version} (id={row.id})", file=sys.stderr)
        for policy in production_policies():
            created = nfse.create_policy(db, policy, args.actor)
            print(f"fiscal_policy: {created.version} flow={created.flow} "
                  f"(id={created.id})", file=sys.stderr)

    print("\nEscrito. Produção continua FECHADA: gate de rede e autorização humana "
          "não foram tocados.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
