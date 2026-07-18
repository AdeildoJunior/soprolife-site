"""Seeds do Núcleo M15.

seed_demo: dados 100% SINTÉTICOS (Paciente Demo 00X, Clínica Exemplo) para a
interface experimental. Nunca usa dados reais.

seed_institutional: cadastra parceiros institucionais confirmados a partir de
arquivo privado (data-private/, fora do Git). Idempotente por nome do
parceiro/unidade/contato. NÃO inventa telefone, e-mail, datas ou percentuais —
só grava o que o arquivo privado trouxer.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .dates import parse_incomplete_date
from .ids import allocate_public_code
from .models import (
    Consent,
    Consultation,
    Lead,
    Partner,
    PartnerContact,
    PartnerReferral,
    PartnerUnit,
    Partnership,
    Person,
    PersonContact,
    SpirometryExam,
)
from .normalize import normalize_name, normalize_phone
from .services.followup import due_after_attendance, schedule_followup, today_local

SYNTHETIC_TAG = "[SINTÉTICO]"


def _person(db: Session, nome: str, telefone: str | None = None,
            consent_whatsapp: str | None = None, nao_contatar: bool = False) -> Person:
    existing = db.execute(
        select(Person).where(Person.nome_completo == nome)
    ).scalar_one_or_none()
    if existing:
        return existing
    person = Person(
        public_code=allocate_public_code(db, "people"),
        nome_completo=nome,
        nome_normalizado=normalize_name(nome),
        nao_contatar=nao_contatar,
        observacao=f"{SYNTHETIC_TAG} dado de demonstração",
        legacy_source="seed_demo",
    )
    db.add(person)
    db.flush()
    if telefone:
        # contatos sintéticos SEMPRE não discáveis: nunca geram URL wa.me
        db.add(PersonContact(
            person_id=person.id, tipo="whatsapp", valor=telefone,
            valor_normalizado=normalize_phone(telefone), principal=True,
            nao_discavel=True,
        ))
    if consent_whatsapp:
        db.add(Consent(person_id=person.id, canal="whatsapp",
                       status=consent_whatsapp, origem="seed_demo"))
    db.flush()
    return person


def seed_demo(db: Session) -> dict:
    """Idempotente: reexecutar não duplica (busca por nome sintético)."""
    today = today_local()

    # números deliberadamente INVÁLIDOS para discagem (prefixo local 0000
    # não é atribuído no plano brasileiro) e marcados nao_discavel=True —
    # nunca geram URL de WhatsApp, mas exercitam a normalização.
    p1 = _person(db, "Paciente Demo 001", "(21) 0000-0001", "concedido")
    p2 = _person(db, "Paciente Demo 002", "(21) 0000-0002", "desconhecido")
    p3 = _person(db, "Paciente Demo 003", "(21) 0000-0003", "concedido")
    p4 = _person(db, "Paciente Demo 004", None)
    p5 = _person(db, "Paciente Demo 005", "(21) 0000-0005", "revogado",
                 nao_contatar=True)

    created = {"pessoas": 5, "exames": 0, "consultas": 0, "leads": 0,
               "parceiros": 0, "encaminhamentos": 0, "followups": 0}

    def exam(person: Person, days_ago: int, status: str = "Realizado",
             modalidade: str = "residencial"):
        data = today - timedelta(days=days_ago)
        existing = db.execute(select(SpirometryExam).where(
            SpirometryExam.person_id == person.id,
            SpirometryExam.data_exame == data,
        )).scalars().first()
        if existing:
            return existing
        e = SpirometryExam(
            public_code=allocate_public_code(db, "spirometry_exams"),
            person_id=person.id, data_exame=data,
            data_exame_original=data.isoformat(),
            data_exame_precisao="dia", data_exame_dia_assumido=False,
            modalidade=modalidade, status=status, origem="seed_demo",
            observacao=f"{SYNTHETIC_TAG}",
            legacy_source="seed_demo",
        )
        db.add(e)
        db.flush()
        created["exames"] += 1
        if status == "Realizado":
            _fup, motivo = schedule_followup(
                db, person, "pos_exame", "spirometry_exams", e.id,
                due=due_after_attendance(data),
            )
            if motivo == "criado":
                created["followups"] += 1
        return e

    # exame há ~6 meses -> follow-up vence hoje/esta semana; outro atrasado
    exam(p1, 183)               # follow-up perto de hoje
    exam(p2, 220)               # follow-up atrasado
    exam(p5, 200)               # pessoa não contatar -> nunca aparece na fila

    # consulta realizada há 100 dias -> follow-up futuro (aguardando data/semana)
    data_consulta = today - timedelta(days=100)
    if not db.execute(select(Consultation).where(
        Consultation.person_id == p3.id,
        Consultation.data_consulta == data_consulta,
    )).scalars().first():
        c = Consultation(
            public_code=allocate_public_code(db, "consultations"),
            person_id=p3.id, data_consulta=data_consulta,
            data_consulta_original=data_consulta.isoformat(),
            data_consulta_precisao="dia",
            modalidade="teleconsulta", profissional="Dr. Exemplo",
            status="Realizada", origem="seed_demo",
            observacao=SYNTHETIC_TAG, legacy_source="seed_demo",
        )
        db.add(c)
        db.flush()
        created["consultas"] += 1
        _fup, motivo = schedule_followup(
            db, p3, "pos_consulta", "consultations", c.id,
            due=due_after_attendance(data_consulta),
        )
        if motivo == "criado":
            created["followups"] += 1

    # lead sem atendimento, com data incompleta normalizada (06/2026 -> 01/06/2026)
    if not db.execute(select(Lead).where(Lead.person_id == p4.id)).scalars().first():
        nd = parse_incomplete_date("06/2026")
        lead = Lead(
            public_code=allocate_public_code(db, "leads"),
            person_id=p4.id, origem="instagram", canal_entrada="direct",
            servico_interesse="espirometria", modalidade="residencial",
            etapa="novo",
            data_primeiro_contato=nd.value,
            data_primeiro_contato_original=nd.original,
            data_primeiro_contato_precisao=nd.precision,
            data_primeiro_contato_dia_assumido=nd.day_assumed,
            observacao=SYNTHETIC_TAG, legacy_source="seed_demo",
        )
        db.add(lead)
        db.flush()
        created["leads"] += 1
        _fup, motivo = schedule_followup(
            db, p4, "lead_sem_atendimento", "leads", lead.id, due=nd.value,
        )
        if motivo == "criado":
            created["followups"] += 1

    # clínica parceira sintética com unidade, contato, parceria e encaminhamento
    partner = db.execute(
        select(Partner).where(Partner.nome == "Clínica Exemplo")
    ).scalar_one_or_none()
    if partner is None:
        partner = Partner(
            public_code=allocate_public_code(db, "partners"),
            nome="Clínica Exemplo", tipo="clinica", status="ativa",
            cidade="Rio de Janeiro", observacao=SYNTHETIC_TAG,
            legacy_source="seed_demo",
        )
        db.add(partner)
        db.flush()
        created["parceiros"] += 1
        unit = PartnerUnit(
            public_code=allocate_public_code(db, "partner_units"),
            partner_id=partner.id, nome="Unidade Centro Exemplo",
            bairro="Centro", cidade="Rio de Janeiro", observacao=SYNTHETIC_TAG,
        )
        db.add(unit)
        db.flush()
        db.add(PartnerContact(
            public_code=allocate_public_code(db, "partner_contacts"),
            partner_id=partner.id, partner_unit_id=unit.id,
            nome="Contato Exemplo 001", cargo="Coordenação",
            principal=True, observacao=SYNTHETIC_TAG,
        ))
        db.add(Partnership(
            public_code=allocate_public_code(db, "partnerships"),
            partner_id=partner.id, status="ativa",
            modelo_repasse="indefinido", responsavel_followup="soprolife",
            observacao=SYNTHETIC_TAG,
        ))
        db.flush()
        referral = PartnerReferral(
            public_code=allocate_public_code(db, "partner_referrals"),
            person_id=p2.id, partner_id=partner.id, partner_unit_id=unit.id,
            data_encaminhamento=today - timedelta(days=10),
            data_encaminhamento_original=(today - timedelta(days=10)).isoformat(),
            data_encaminhamento_precisao="dia",
            servico_solicitado="espirometria",
            status="Aguardando contato",
            autorizacao_contato_soprolife=True,
            responsavel_followup="soprolife",
            observacao_operacional=SYNTHETIC_TAG,
        )
        db.add(referral)
        db.flush()
        created["encaminhamentos"] += 1

    return {"status": "ok", "sintetico": True, "criado": created}


def seed_institutional(db: Session, data: dict) -> dict:
    """Cadastro idempotente de parceiros institucionais confirmados.

    Nada é inventado: campos ausentes ficam nulos. Nunca grava PII de paciente.
    """
    from .domain import pcmso_violation

    results = []
    for spec in data.get("parceiros", []):
        nome = (spec.get("nome") or "").strip()
        if not nome:
            results.append({"parceiro": None, "status": "ignorado_sem_nome"})
            continue
        if pcmso_violation({"nome": nome, "tipo": spec.get("tipo") or ""}):
            results.append({"parceiro": nome, "status": "rejeitado_pcmso"})
            continue
        partner = db.execute(
            select(Partner).where(Partner.nome == nome)
        ).scalar_one_or_none()
        if partner is None:
            partner = Partner(
                public_code=allocate_public_code(db, "partners"),
                nome=nome,
                tipo=spec.get("tipo") or "clinica",
                status="ativa" if spec.get("status_parceria") == "ativa" else "prospecto",
                cidade=spec.get("cidade"),
                legacy_source="seed_institucional",
            )
            db.add(partner)
            db.flush()
            status = "criado"
        else:
            status = "ja_existia"
        if spec.get("status_parceria"):
            existing_ps = db.execute(select(Partnership).where(
                Partnership.partner_id == partner.id
            )).scalars().first()
            if existing_ps is None:
                db.add(Partnership(
                    public_code=allocate_public_code(db, "partnerships"),
                    partner_id=partner.id,
                    status=spec["status_parceria"],
                    modelo_repasse="indefinido",
                ))
                db.flush()
        for unit_spec in spec.get("unidades", []):
            unome = (unit_spec.get("nome") or "").strip()
            if not unome:
                continue
            if not db.execute(select(PartnerUnit).where(
                PartnerUnit.partner_id == partner.id, PartnerUnit.nome == unome
            )).scalars().first():
                db.add(PartnerUnit(
                    public_code=allocate_public_code(db, "partner_units"),
                    partner_id=partner.id, nome=unome,
                    bairro=unit_spec.get("bairro"), cidade=unit_spec.get("cidade"),
                ))
                db.flush()
        for contact_spec in spec.get("contatos", []):
            cnome = (contact_spec.get("nome") or "").strip()
            if not cnome:
                continue
            if not db.execute(select(PartnerContact).where(
                PartnerContact.partner_id == partner.id,
                PartnerContact.nome == cnome,
            )).scalars().first():
                unit_id = None
                unit_name = (contact_spec.get("unidade") or "").strip()
                if unit_name:
                    unit = db.execute(select(PartnerUnit).where(
                        PartnerUnit.partner_id == partner.id,
                        PartnerUnit.nome == unit_name,
                    )).scalars().first()
                    unit_id = unit.id if unit else None
                db.add(PartnerContact(
                    public_code=allocate_public_code(db, "partner_contacts"),
                    partner_id=partner.id, partner_unit_id=unit_id,
                    nome=cnome, cargo=contact_spec.get("cargo"),
                    telefone=contact_spec.get("telefone"),
                    email=contact_spec.get("email"),
                    principal=bool(contact_spec.get("principal")),
                ))
                db.flush()
        results.append({"parceiro": nome, "status": status})
    return {"status": "ok", "resultados": results}
