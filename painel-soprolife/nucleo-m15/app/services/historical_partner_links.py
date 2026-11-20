"""Reviewed structural repair only. Never invokes exam/settlement/share workflows."""
import hashlib
import json
import re
import unicodedata

from sqlalchemy import select, text

from ..audit import audit
from ..models import SpirometryExam, User
from .partnership_center import catalog

LINK_FIELDS = ('partner_id', 'partner_unit_id')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
        separators=(',', ':')).encode()).hexdigest()


def _row(db, code, lock=False):
    query = select(SpirometryExam.__table__).where(SpirometryExam.public_code == code)
    if lock:
        query = query.with_for_update()
    row = db.execute(query).mappings().one_or_none()
    if row is None:
        raise ValueError('Exame não encontrado.')
    return dict(row)


def _protected(row):
    return digest({k: v for k, v in row.items() if k not in LINK_FIELDS})


def _canonical(db, code):
    card = next((c for c in catalog(db) if c['codigo'] == code), None)
    if not card or card['estado'] != 'ready' or len(card['unidades']) != 1:
        raise ValueError('Parceiro/unidade canônicos ausentes ou ambíguos.')
    return {'partner_id': card['partner_id'], 'partner_unit_id': card['unidades'][0]['id']}


def _evidence(row, code):
    value = unicodedata.normalize('NFKD', ' '.join(
        str(row.get(k) or '') for k in ('local_atendimento', 'origem')))
    value = re.sub('[^a-z]', '', value.encode('ascii', 'ignore').decode().lower())
    markers = {c for c, names in {
        'maclin': ('maclin', 'norteshopping'), 'alergocare': ('alergocare',),
        'pastore': ('pastore',), 'alergolife': ('alergolife',)
    }.items() if any(n in value for n in names)}
    if markers != {code}:
        raise ValueError('Evidência de local/origem ausente ou conflitante; revisão humana necessária.')


def make_plan(db, requests):
    if not isinstance(requests, list) or not 1 <= len(requests) <= 100:
        raise ValueError('Informe de 1 a 100 vínculos explicitamente revisados.')
    links = []
    seen = set()
    for request in sorted(requests, key=lambda r: r['exam_code']):
        code, partner = request['exam_code'], request['partner_code']
        if code in seen:
            raise ValueError('Exame repetido no plano.')
        seen.add(code)
        row = _row(db, code)
        target = _canonical(db, partner)
        _evidence(row, partner)
        if any(row[k] not in (None, target[k]) for k in LINK_FIELDS):
            raise ValueError('Vínculo existente conflitante; não será sobrescrito.')
        links.append({'exam_code': code, 'exam_id': row['id'], 'partner_code': partner,
            'before': {k: row[k] for k in LINK_FIELDS}, 'after': target,
            'protected_sha256': _protected(row)})
    return {'version': 1, 'links': links}


def apply_plan(db, plan, expected_sha256, actor_id):
    if digest(plan) != expected_sha256 or plan.get('version') != 1:
        raise ValueError('Hash/versão do plano inválido.')
    actor = db.get(User, actor_id)
    if not actor or not actor.ativo or not any(r.name == 'admin' for r in actor.roles):
        raise ValueError('Operador administrador ativo obrigatório.')
    links = plan.get('links', [])
    if not 1 <= len(links) <= 100 or len({i['exam_code'] for i in links}) != len(links):
        raise ValueError('Plano vazio, repetido ou excedido.')
    results = []
    # Validate/lock the complete batch before writing; any error rolls it all back.
    with db.begin_nested():
        checked = []
        for link in sorted(links, key=lambda i: i['exam_code']):
            row = _row(db, link['exam_code'], lock=True)
            target = _canonical(db, link['partner_code'])
            if row['id'] != link['exam_id'] or _protected(row) != link['protected_sha256']:
                raise ValueError('Exame mudou desde o dry-run; refaça a auditoria.')
            _evidence(row, link['partner_code'])
            if target != link['after'] or any(link['before'][k] not in (None, target[k]) for k in LINK_FIELDS):
                raise ValueError('Configuração/vínculo do plano conflitante.')
            current = {k: row[k] for k in LINK_FIELDS}
            if current not in (link['before'], target):
                raise ValueError('Vínculo mudou desde o dry-run.')
            checked.append((link, current, target))
        for link, current, target in checked:
            changed = current != target
            if changed:
                # Deliberate parameterized SQL: only these two columns, including
                # no updated_at/onupdate, fiscal preparation or settlement hooks.
                db.execute(text('UPDATE spirometry_exams SET partner_id=:partner_id, '
                    'partner_unit_id=:partner_unit_id WHERE id=:id'),
                    {**target, 'id': link['exam_id']})
                audit(db, 'espirometria.vinculo_historico_corrigido', 'spirometry_exams',
                    link['exam_id'], actor_id, expected_sha256[:64], {
                        'exam_code': link['exam_code'], 'reason_code': 'historical_partner_link',
                        'sha256': expected_sha256, 'campos': list(LINK_FIELDS),
                        'previous_partner_id': current['partner_id'],
                        'previous_partner_unit_id': current['partner_unit_id'], **target})
            results.append({'exam_code': link['exam_code'], 'changed': changed,
                'before': current, 'after': target})
        db.flush()
    db.expire_all()
    return results
