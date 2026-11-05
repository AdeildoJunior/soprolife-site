#!/usr/bin/env python3
"""Dry-run por padrão. --execute exige hash do plano e backup já verificado."""
import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import get_engine
from app.services.profit_history import append_history, plan_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--first-date-policy", choices=("exact", "month_boundary", "first_exact"), default="exact")
    parser.add_argument("--minimum-room-hours", type=Decimal)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    args = parser.parse_args()
    with Session(get_engine()) as db:
        if db.bind.dialect.name == "postgresql":
            if args.execute:
                db.execute(text("SELECT pg_advisory_xact_lock(20261105)"))
                db.execute(text("LOCK TABLE profit_cost_rules IN SHARE ROW EXCLUSIVE MODE"))
            else:
                db.execute(text("SET TRANSACTION READ ONLY"))
        plan = plan_history(db, as_of=args.as_of, first_date_policy=args.first_date_policy,
                            minimum_room_hours=args.minimum_room_hours)
        if args.execute:
            if not args.expected_plan_sha256:
                parser.error("--execute exige --expected-plan-sha256")
            ids = append_history(db, plan, args.expected_plan_sha256)
            db.commit()
            print(json.dumps({"created_ids": ids, "plan": plan}, ensure_ascii=False))
        else:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            db.rollback()


if __name__ == "__main__":
    main()
