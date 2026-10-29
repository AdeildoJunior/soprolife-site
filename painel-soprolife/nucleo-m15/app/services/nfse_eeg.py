"""Adaptação EEG para a fila fiscal corporativa. Não contém provider ou emissor.

Sem política EEG validada e perfil tributário explicitamente revisado, nenhum
preparo fica elegível. Credenciais, certificados, allocator e SEFIN permanecem
nos módulos corporativos existentes. Nenhuma política é instalada aqui.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from ..models import (EegExam, EegEnrollment, Person, FinancialEntry, FiscalPolicy,
                      FiscalDocument, FiscalPreparation, NationalDpsConfigurationVersion)
from ..fiscal_schemas import PolicyCreate
from . import nfse
from .idempotency import idempotent_create, payload_fingerprint
from .eeg_flow import EXAME_JA_REALIZADO
from .eeg_modalidades import obter
from .nfse_national.config import NationalDpsConfiguration
from .nfse_national.service_description import ServiceDescriptionUndetermined

ISSUER_CNPJ = '63544026000110'
ISSUER_NAME = 'SOPRO LIFE DIAGNÓSTICO E SOLUÇÕES EM SAÚDE LTDA'
DESCRIPTIONS = {
    'vigilia': 'Eletroencefalograma (EEG) de rotina',
    'fotoestimulacao': 'Eletroencefalograma (EEG) com fotoestimulação',
    'prolongado-30-60': 'Eletroencefalograma (EEG) prolongado de 30 minutos a 1 hora',
    'prolongado-2h': 'Eletroencefalograma (EEG) prolongado de 2 horas',
    'digital-por-hora': 'Eletroencefalograma (EEG) digital prolongado por hora',
    'mapeamento': 'Eletroencefalograma (EEG) com mapeamento cerebral',
    'mapeamento-fotoestimulacao': 'Eletroencefalograma (EEG) com mapeamento cerebral e fotoestimulação',
    'infantil': 'Eletroencefalograma (EEG) infantil',
    'com-sono': 'Eletroencefalograma (EEG) com sono',
    'privacao-sono': 'Eletroencefalograma (EEG) com privação de sono',
    'ambulatorial': 'Eletroencefalograma (EEG) ambulatorial',
    'hospitalar-beira-leito': 'Eletroencefalograma (EEG) hospitalar à beira do leito',
    'video-eeg': 'Vídeo-eletroencefalograma (vídeo-EEG)',
}


def service_description(code):
    if code not in DESCRIPTIONS:
        raise ServiceDescriptionUndetermined('Modalidade EEG sem nomenclatura fiscal definida.')
    return DESCRIPTIONS[code]


def _policy_input(policy):
    return PolicyCreate.model_validate({k: getattr(policy, k) for k in (
        'version', 'environment', 'flow', 'service', 'effective_from', 'effective_to',
        'validation_state', 'configuration')})


def national_configuration(db, policy_id, environment, competence):
    """Perfil corporativo REFERENCIADO, classificação EEG explicitamente aprovada.

    Reaproveita identidade jurídica e tributos do perfil apontado pela política.
    Os códigos do serviço vêm exclusivamente da aprovação EEG. O fingerprint
    referencia a política imutável; trocar perfil/classificação exige nova versão.
    """
    policy = db.get(FiscalPolicy, policy_id) if policy_id else None
    if not policy or policy.service != 'eeg' or policy.flow != 'EEG' or policy.validation_state != 'validated':
        nfse.fail('eeg_tax_classification_pending')
    try:
        cfg = _policy_input(policy).configuration
        if policy.environment != environment or not competence or not policy.effective_from <= competence <= policy.effective_to:
            nfse.fail('eeg_tax_classification_invalid')
        row = db.get(NationalDpsConfigurationVersion, cfg.eeg_national_configuration_id)
        expected_env = 'restricted' if environment == 'mock' else environment
        if not row or row.environment != expected_env or row.validation_state != 'validated' or row.effective_from > competence:
            nfse.fail('eeg_tax_classification_invalid')
        base = dict(row.configuration)
        if base.get('issuer_cnpj') != ISSUER_CNPJ:
            nfse.fail('eeg_issuer_mismatch')
        # Não rebatiza o prestador como EEGLife; os dados jurídicos vêm da
        # mesma versão corporativa. O rótulo legal é informado pela interface.
        base.update(codigo_tributacao_nacional=cfg.national_service_code,
                    codigo_tributacao_municipal=cfg.municipal_service_code,
                    codigo_nbs=cfg.eeg_nbs_code or None)
        return NationalDpsConfiguration.model_validate(base)
    except (ValidationError, TypeError):
        nfse.fail('eeg_tax_classification_invalid')


def evaluate(db, exam_id, environment, *, for_update=True):
    stmt = select(EegExam).where(EegExam.id == exam_id)
    exam = db.scalar(stmt.with_for_update() if for_update else stmt)
    if not exam:
        nfse.fail('missing_stable_exam_link', 422)
    enrollment = db.get(EegEnrollment, exam.enrollment_id)
    person = db.get(Person, enrollment.person_id) if enrollment else None
    reasons = []
    if exam.status not in EXAME_JA_REALIZADO:
        reasons.append('service_not_performed')
    service_date = (exam.realizado_em.replace(tzinfo=ZoneInfo('UTC')) if exam.realizado_em and exam.realizado_em.tzinfo is None
                    else exam.realizado_em)
    service_date = service_date.astimezone(ZoneInfo('America/Sao_Paulo')).date() if service_date else None
    if not service_date:
        reasons.append('service_date_missing_or_imprecise')
    elif service_date > datetime.now(ZoneInfo('America/Sao_Paulo')).date():
        reasons.append('service_date_in_future')
    if not enrollment or enrollment.status != 'ativo' or not person or person.arquivado:
        reasons.append('recipient_missing_or_archived')
    # Local é estruturado e segue o mesmo limite comprovado pelo motor atual.
    reasons.extend(nfse.production_fact_blockers(person, exam.municipio_atendimento_ibge)
                   if environment == 'production' else [])
    from .nfse_national.service_location import spirometry_service_municipio_ibge
    try:
        spirometry_service_municipio_ibge(exam.municipio_atendimento_ibge)
    except ValueError:
        reasons.append('service_location_missing')
    stmt = select(FinancialEntry).where(FinancialEntry.eeg_exam_id == exam.id)
    entry = db.scalar(stmt.with_for_update() if for_update else stmt)
    if not entry:
        reasons.append('financial_entry_missing')
    elif entry.tipo != 'receita' or entry.categoria != 'EEG' or entry.status != 'Recebido' or entry.moeda != 'BRL' or entry.valor <= 0:
        reasons.append('financial_revenue_not_received_or_invalid')
    policies = list(db.scalars(select(FiscalPolicy).where(
        FiscalPolicy.environment == environment, FiscalPolicy.service == 'eeg',
        FiscalPolicy.flow == 'EEG', FiscalPolicy.validation_state == 'validated')).all())
    candidates = [p for p in policies if service_date and p.effective_from <= service_date <= p.effective_to]
    policy = candidates[0] if len(candidates) == 1 else None
    if len(candidates) > 1:
        reasons.append('policy_ambiguous')
    if not policy:
        reasons.append('eeg_tax_classification_pending')
    else:
        try:
            cfg = _policy_input(policy).configuration
            if cfg.amount_basis != 'financial_entry.valor':
                reasons.append('policy_amount_basis_mismatch')
            if exam.modalidade_codigo not in (cfg.eeg_modalidades or []):
                reasons.append('eeg_modality_not_validated')
            national_configuration(db, policy.id, environment, service_date)
        except (ValidationError, HTTPException):
            reasons.append('eeg_tax_classification_invalid')
    try:
        description = service_description(exam.modalidade_codigo)
    except ServiceDescriptionUndetermined:
        description = None
        reasons.append('service_description_undetermined')
    modalidade = obter(exam.modalidade_codigo)
    if not modalidade or not modalidade.ativo:
        reasons.append('eeg_modality_not_validated')
    return dict(financial_entry_id=entry.id if entry else None,
        policy_id=policy.id if policy else None, recipient_person_id=person.id if person else None,
        flow='EEG', service_date=service_date, competence=service_date,
        service_municipio_ibge=exam.municipio_atendimento_ibge,
        amount_snapshot=entry.valor if entry and policy and not reasons else None,
        description=description, blocking_reasons=sorted(set(reasons)))


def prepare(db, exam_id, settings, actor, request_id=None):
    if not settings.nfse_enabled:
        nfse.fail('nfse_disabled', 503)
    if not db.get(EegExam, exam_id):
        nfse.fail('missing_stable_exam_link', 422)
    identity = {'eeg_exam_id': exam_id, 'environment': settings.nfse_environment}
    key = payload_fingerprint(identity)
    def factory(key, fingerprint):
        doc = FiscalDocument(eeg_exam_id=exam_id, kind='eeg', environment=settings.nfse_environment,
            created_by=actor, idempotency_key=key, idempotency_fingerprint=fingerprint)
        db.add(doc)
        db.flush()
        return doc
    doc, _ = idempotent_create(db, FiscalDocument, key, identity, factory)
    doc = nfse.get_document(db, doc.id, lock=True)
    if doc.state not in {'pending', 'blocked'}:
        nfse.fail('document_frozen_after_attempt')
    data = evaluate(db, exam_id, settings.nfse_environment)
    fingerprint = payload_fingerprint(data)
    latest = nfse.latest_preparation(db, doc.id)
    if not latest or latest.fingerprint != fingerprint:
        db.add(FiscalPreparation(document_id=doc.id, created_by=actor, fingerprint=fingerprint, **data))
        nfse.record(db, 'prepared', 'fiscal_document', doc.id, actor, request_id, contexto='eeglife', motivo=data['blocking_reasons'])
    doc.blocking_reasons = data['blocking_reasons']
    doc.eligibility = 'blocked' if doc.blocking_reasons else 'eligible'
    doc.state = 'blocked' if doc.blocking_reasons else 'pending'
    db.commit()
    return doc
