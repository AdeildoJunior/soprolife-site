#!/usr/bin/env python3
"""Private reviewed mapping; dry-run by default. No API endpoint or auto-backfill."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from app.db import get_engine
from app.services.historical_partner_links import apply_plan, digest, make_plan


def verify_backup(folder):
    root = Path(folder).resolve(strict=True)
    files = []
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split(maxsplit=1)
        path = Path(name.lstrip('*')).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Arquivo de backup fora do diretório revisado.')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                raise ValueError('Checksum de backup inválido.')
        files.append(path.suffix)
    if '.dump' not in files or '.bundle' not in files:
        raise ValueError('Backup PostgreSQL e bundle Git verificados obrigatórios.')


def fingerprint(db):
    # Only irreversible hashes leave the database; snapshot includes every table.
    result = {}
    for table in inspect(db.connection()).get_table_names():
        quoted = db.bind.dialect.identifier_preparer.quote(table)
        result[table] = db.scalars(text(
            f'SELECT md5(to_jsonb(t)::text) FROM {quoted} t ORDER BY 1')).all()
    result['_protected_exams'] = db.scalars(text("SELECT md5((to_jsonb(t)-'partner_id'-'partner_unit_id')::text) FROM spirometry_exams t ORDER BY 1")).all()
    result['_exam_links'] = [dict(r) for r in db.execute(text(
        'SELECT id,partner_id,partner_unit_id FROM spirometry_exams ORDER BY id')).mappings()]
    return result


def verify_changes(before, after, results, plan):
    if before.keys() != after.keys():
        raise ValueError('Conjunto de tabelas alterado.')
    for table in before:
        if table == 'spirometry_exams' or table == '_exam_links':
            continue
        if table == 'audit_logs':
            if Counter(before[table]) - Counter(after[table]):
                raise ValueError('Auditoria preexistente alterada.')
            if len(after[table])-len(before[table]) != sum(r['changed'] for r in results):
                raise ValueError('Quantidade inesperada de eventos de auditoria.')
        elif before[table] != after[table]:
            raise ValueError('Fatos protegidos alterados: ' + table)
    expected = {r['exam_id']: r['after'] for r in plan['links']}
    old = {r['id']: r for r in before['_exam_links']}
    for row in after['_exam_links']:
        required = {'id': row['id'], **expected[row['id']]} if row['id'] in expected else old[row['id']]
        if row != required:
            raise ValueError('Vínculo fora do plano alterado.')


def private_json(path, data):
    # New exclusive file; never replace a prior audit or follow a symlink.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--requests', help='Private reviewed exam_code/partner_code JSON')
    source.add_argument('--plan', help='Exact reviewed dry-run plan')
    parser.add_argument('--output', required=True, help='New private artifact outside Git')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--actor-id')
    parser.add_argument('--backup-dir')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    repo = Path(__file__).resolve().parents[3]
    if not output.is_absolute() or output.is_relative_to(repo) or output.exists():
        parser.error('Use novo artefato privado fora do repositório.')
    if args.apply:
        if not all((args.plan, args.expected_sha256, args.actor_id, args.backup_dir)):
            parser.error('Apply exige plano/hash, administrador e backup verificado.')
        verify_backup(args.backup_dir)
    with Session(get_engine()) as db, db.begin():
        if args.apply and db.bind.dialect.name != 'postgresql':
            raise ValueError('Apply exige PostgreSQL e verificação integral de fatos.')
        if db.bind.dialect.name == 'postgresql':
            db.execute(text('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE' if args.apply
                else 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            db.execute(text("SET LOCAL lock_timeout='5s'"))
        plan = (make_plan(db, json.loads(Path(args.requests).read_text())['requests'])
                if args.requests else json.loads(Path(args.plan).read_text()))
        if args.apply:
            before = fingerprint(db)
            results = apply_plan(db, plan, args.expected_sha256, args.actor_id)
            after = fingerprint(db)
            verify_changes(before, after, results, plan)
            artifact = {'mode': 'apply', 'plan_sha256': digest(plan), 'results': results,
                'protected_tables': len(before)-2,
                'only_approved_links_and_audit_changed': True}
        else:
            artifact = plan
        # Reserve/write artifact before committing; a failed commit is not success.
        private_json(output, artifact)
    print(json.dumps({'mode': 'apply' if args.apply else 'dry-run',
        'plan_sha256': digest(plan), 'links': len(plan['links']), 'committed': args.apply}))


if __name__ == '__main__':
    main()
