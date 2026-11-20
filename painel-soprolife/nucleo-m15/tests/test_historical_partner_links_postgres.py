"""Same structural-only regression against disposable PostgreSQL 16."""
import os
import pytest
from tests.test_command_center_postgres import postgres_url, config, engine
from tests.test_historical_partner_links import (canonical,
    test_legacy_cents_exact_only_structural_columns,
    test_existing_partner_only_fills_unit_and_alergolife_untouched,
    test_conflict_and_alergolife_fail_closed,
    test_stale_batch_and_bad_hash_no_partial_writes,
    test_existing_confirmed_financial_projection_unchanged)
pytestmark = pytest.mark.skipif(not os.environ.get('M15_TEST_POSTGRES_URL'), reason='Disposable PostgreSQL required')


def test_atomic_production_fingerprint_guard(db, canonical, users):
    import importlib.util
    from pathlib import Path
    from copy import deepcopy
    from app.services.historical_partner_links import make_plan, apply_plan, digest
    from tests.test_historical_partner_links import legacy
    path = Path(__file__).resolve().parents[1]/'scripts/link-historical-partner-exams.py'
    spec = importlib.util.spec_from_file_location('historical_link_cli',path)
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    e = legacy(db, canonical, users)
    plan = make_plan(db,[{'exam_code':e.public_code,'partner_code':'maclin'}])
    before = cli.fingerprint(db)
    results = apply_plan(db,plan,digest(plan),users['admin'].id)
    after = cli.fingerprint(db)
    cli.verify_changes(before,after,results,plan)
    changed = deepcopy(after); changed['financial_entries']=['unexpected_change']
    with pytest.raises(ValueError,match='financial_entries'):
        cli.verify_changes(before,changed,results,plan)
    changed = deepcopy(after); changed['_protected_exams']=['unexpected_date_change']
    with pytest.raises(ValueError,match='_protected_exams'):
        cli.verify_changes(before,changed,results,plan)
