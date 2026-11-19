"""Configuração explícita das duas parcerias confirmadas; sem cadastro de pacientes."""
from decimal import Decimal
from datetime import timedelta, timezone
import re
from fastapi import HTTPException
from sqlalchemy import select
from ..audit import audit
from ..ids import allocate_public_code
from ..models import Partner, PartnerUnit, Partnership, PartnerCommercialRule, utcnow
from .partner_shares import rule_version_key


def configure(db, codigo, effective_from, user_id):
    # Único catálogo de bootstrap. Operação e cálculo consultam a regra no banco.
    names = {'maclin': ('Maclin', 'Maclin / Norte Shopping', 'DIRECT'),
             'alergocare': ('Alergocare', 'Alergocare', 'DIRECT')}
    name, unit_name, fiscal_flow = names[codigo]
    matches = [p for p in db.scalars(select(Partner)).all()
               if not p.arquivado and re.fullmatch(re.escape(codigo) + r'(?:\s*\([^)]*\))?', p.nome.strip(), re.I)]
    if len(matches) > 1:
        raise HTTPException(409, 'Parceiro ambíguo: indique cadastro canônico antes de configurar.')
    if matches:
        partner = matches[0]
    else:
        partner = Partner(public_code=allocate_public_code(db, 'partners'), nome=name, status='ativa')
        db.add(partner); db.flush()
        audit(db, 'parceiro.criado', 'partners', partner.id, user_id, detalhes={'public_code': partner.public_code})
    units = db.scalars(select(PartnerUnit).where(PartnerUnit.partner_id == partner.id)).all()
    if len(units) > 1:
        preferred = [u for u in units if u.ativo and (u.nome.strip().casefold() == unit_name.casefold()
                     or (codigo == 'maclin' and 'norte shopping' in u.nome.casefold()))]
        if len(preferred) != 1:
            raise HTTPException(409, 'Unidade ambígua: não criar duplicata.')
        unit = preferred[0]
    elif units:
        unit = units[0]
        if not unit.ativo:
            raise HTTPException(409, 'Unidade existente inativa; revisão necessária.')
    else:
        # Sem endereço conhecido, conserva NULL. Não inventa rua/CEP.
        unit = PartnerUnit(public_code=allocate_public_code(db, 'partner_units'), partner_id=partner.id, nome=unit_name, ativo=True)
        db.add(unit); db.flush()
        audit(db, 'unidade.criada', 'partner_units', unit.id, user_id, detalhes={'public_code': unit.public_code})
    agreements = db.scalars(select(Partnership).where(Partnership.partner_id == partner.id)).all()
    if len(agreements) > 1:
        raise HTTPException(409, 'Parceria ambígua; não criar duplicata.')
    if agreements:
        agreement = agreements[0]
        if agreement.status != 'ativa' or agreement.modelo_repasse != 'percentual' or agreement.percentual_repasse != Decimal('30'):
            raise HTTPException(409, 'Parceria existente diverge; preservar e revisar vigência.')
    else:
        agreement = Partnership(public_code=allocate_public_code(db, 'partnerships'), partner_id=partner.id,
            status='ativa', modelo_repasse='percentual', percentual_repasse=Decimal('30'), vigencia_inicio=effective_from)
        db.add(agreement); db.flush()
        audit(db, 'parceria.criada', 'partnerships', agreement.id, user_id, detalhes={'public_code': agreement.public_code})
    rules = db.scalars(select(PartnerCommercialRule).where(PartnerCommercialRule.codigo == codigo)).all()
    if rules:
        latest = max(rules, key=rule_version_key)
        if (latest.partnership_id != agreement.id or latest.partner_unit_id != unit.id
                or latest.vigencia_inicio != effective_from or latest.percentual_parceiro != Decimal('30')
                or latest.percentual_soprolife != Decimal('70') or latest.fiscal_flow != fiscal_flow):
            raise HTTPException(409, 'Regra já configurada com outros fatos; não sobrescrever.')
        return latest
    rule = PartnerCommercialRule(codigo=codigo, partnership_id=agreement.id, partner_unit_id=unit.id,
        modalidade='clinica_parceira', vigencia_inicio=effective_from,
        percentual_parceiro=Decimal('30'), percentual_soprolife=Decimal('70'), fiscal_flow=fiscal_flow,
        created_by_user_id=user_id)
    db.add(rule); db.flush()
    audit(db, 'parceria.regra_criada', 'partner_commercial_rules', rule.id, user_id,
          detalhes={'tipo': codigo, 'origin_partner_unit_id': unit.id})
    return rule


def approve_alergocare_direct(db, user_id):
    """Autorização fiscal explícita: nova versão, sem mudar IDs/economia/fatos."""
    agreement_id = db.scalar(select(PartnerCommercialRule.partnership_id).where(
        PartnerCommercialRule.codigo == 'alergocare').limit(1))
    if agreement_id is None:
        raise HTTPException(409, 'Configure a parceria Alergocare existente antes da autorização fiscal.')
    # Mesmo lock da criação administrativa de versões: dois admins não duplicam aprovação.
    agreement = db.scalar(select(Partnership).where(Partnership.id == agreement_id).with_for_update())
    previous = db.scalar(select(PartnerCommercialRule).where(
        PartnerCommercialRule.codigo == 'alergocare').order_by(
        PartnerCommercialRule.vigencia_inicio.desc(), PartnerCommercialRule.created_at.desc()))
    if previous.partnership_id != agreement.id:
        raise HTTPException(409, 'Código comercial com parceria divergente; revisão necessária.')
    unit = db.get(PartnerUnit, previous.partner_unit_id)
    partner = db.get(Partner, agreement.partner_id)
    if (agreement.status != 'ativa' or partner.arquivado or not unit.ativo
            or unit.partner_id != partner.id or previous.percentual_parceiro != Decimal('30')
            or previous.percentual_soprolife != Decimal('70')):
        raise HTTPException(409, 'Contexto comercial divergente; nenhuma alteração aplicada.')
    result = {'codigo': previous.codigo, 'regra_anterior': previous.id,
              'parceria': agreement.id, 'partner_id': partner.id, 'unidade': unit.id,
              'vigencia': previous.vigencia_inicio.isoformat(), 'fiscal_anterior': previous.fiscal_flow,
              'fiscal_flow': 'DIRECT', 'clinica': str(previous.percentual_parceiro),
              'soprolife': str(previous.percentual_soprolife)}
    if previous.fiscal_flow == 'DIRECT':
        return result
    created = previous.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    values = {key: getattr(previous, key) for key in (
        'codigo', 'partnership_id', 'partner_unit_id', 'modalidade', 'vigencia_inicio',
        'vigencia_fim', 'percentual_parceiro', 'percentual_soprolife')}
    row = PartnerCommercialRule(**values, fiscal_flow='DIRECT', created_by_user_id=user_id,
        created_at=max(utcnow(), created + timedelta(microseconds=1)))
    db.add(row); db.flush()
    audit(db, 'parceria.regra_criada', 'partner_commercial_rules', row.id, user_id,
          detalhes={'tipo': 'alergocare', 'origin_partner_unit_id': unit.id,
                    'motivo': 'autorizacao_fiscal_direct'})
    return result
