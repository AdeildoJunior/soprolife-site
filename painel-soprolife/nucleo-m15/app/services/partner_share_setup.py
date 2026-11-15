"""Configuração explícita das duas parcerias confirmadas; sem cadastro de pacientes."""
from decimal import Decimal
import re
from fastapi import HTTPException
from sqlalchemy import select
from ..audit import audit
from ..ids import allocate_public_code
from ..models import Partner, PartnerUnit, Partnership, PartnerCommercialRule


def configure(db, codigo, effective_from, user_id):
    # Único catálogo de bootstrap. Operação e cálculo consultam a regra no banco.
    names = {'maclin': ('Maclin', 'Maclin / Norte Shopping', 'DIRECT'),
             'alergocare': ('Alergocare', 'Alergocare', None)}
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
        if (len(rules) != 1 or rules[0].partnership_id != agreement.id or rules[0].partner_unit_id != unit.id
                or rules[0].vigencia_inicio != effective_from or rules[0].percentual_parceiro != Decimal('30')
                or rules[0].percentual_soprolife != Decimal('70') or rules[0].fiscal_flow != fiscal_flow):
            raise HTTPException(409, 'Regra já configurada com outros fatos; não sobrescrever.')
        return rules[0]
    rule = PartnerCommercialRule(codigo=codigo, partnership_id=agreement.id, partner_unit_id=unit.id,
        modalidade='clinica_parceira', vigencia_inicio=effective_from,
        percentual_parceiro=Decimal('30'), percentual_soprolife=Decimal('70'), fiscal_flow=fiscal_flow,
        created_by_user_id=user_id)
    db.add(rule); db.flush()
    audit(db, 'parceria.regra_criada', 'partner_commercial_rules', rule.id, user_id,
          detalhes={'tipo': codigo, 'origin_partner_unit_id': unit.id})
    return rule
