"""Indicadores de laudos: leitura agregada, sem interpretar exames ou PDFs.

Uma linha por documento vigente. Prévia, PDF externo e versões anteriores
não viram novos resultados; só a última versão nativa publicada fornece os
códigos escolhidos pela médica. Nenhum identificador sai na resposta.

M26.20 — a fatia "Personalizado" ganhou detalhamento. O texto da conclusão
personalizada é lido para classificar (`custom_conclusion_groups`) e nunca
devolvido: a resposta carrega rótulo, critério e contagem, jamais a redação
da médica.
"""
from collections import Counter
from datetime import date, datetime, timezone

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from ..models import Person, ReportAssignment, ReportDocument, ReportDocumentVersion, SpirometryExam
from . import custom_conclusion_groups as custom_groups
from .report_conclusions import CONCLUSION_CUSTOM_CODE, CONCLUSION_OPTIONS, BRONCHODILATOR_OPTIONS

ORIGINS = {
    "pastore": "Pastore", "coworking": "Consultório / coworking",
    "residencial": "Domiciliar", "clinica_parceira": "Clínica parceira",
    "empresa_pcmso": "Empresa / PCMSO", "outro": "Outro",
}
GROUPS = {
    "normal": "Normal", "obstrutivo": "Obstrutivo",
    "restritivo": "Sugestivo de restritivo", "misto": "Sugestivo de misto",
    "inespecifico": "Inespecífico", "personalizado": "Personalizado",
    "nao_catalogado": "Sem classificação de catálogo",
}
AGES = ("Até 17 anos", "18–39 anos", "40–59 anos", "60–79 anos", "80 anos ou mais", "Não informado")


def _series(counter, labels):
    return [{"chave": key, "rotulo": label, "quantidade": counter[key]}
            for key, label in labels.items()]


def report_statistics(db: Session, *, physician_profile_id: str | None = None,
                      inicio: date | None = None, fim: date | None = None,
                      origem: str | None = None) -> dict:
    successor = aliased(ReportDocument)
    published = aliased(ReportDocumentVersion)
    latest_published = (
        select(published.id)
        .where(published.report_document_id == ReportDocument.id,
               published.kind.in_(("laudo_liberado", "laudo_adendo")))
        .order_by(published.version_number.desc()).limit(1)
        .correlate(ReportDocument).scalar_subquery()
    )
    statement = (
        select(ReportDocument.origin_type, ReportDocument.status,
               SpirometryExam.data_exame, SpirometryExam.data_exame_precisao,
               SpirometryExam.data_exame_dia_assumido,
               Person.sexo, Person.data_nascimento,
               ReportDocumentVersion.conclusion_code_snapshot,
               ReportDocumentVersion.bronchodilator_code_snapshot,
               # M26.20 — lido só para classificar em memória; o texto nunca
               # entra na resposta nem em log algum.
               ReportDocumentVersion.conclusion_text_snapshot)
        .select_from(ReportDocument)
        .join(SpirometryExam, SpirometryExam.id == ReportDocument.spirometry_exam_id)
        .join(Person, Person.id == SpirometryExam.person_id)
        .outerjoin(ReportDocumentVersion, ReportDocumentVersion.id == latest_published)
        .where(Person.arquivado.is_(False),
               ReportDocument.superseded_by_id.is_(None),
               ~exists().where(successor.corrects_document_id == ReportDocument.id))
    )
    if physician_profile_id is not None:
        statement = statement.where(exists().where(
            ReportAssignment.report_document_id == ReportDocument.id,
            ReportAssignment.physician_profile_id == physician_profile_id,
            ReportAssignment.active.is_(True),
        ), SpirometryExam.encerramento_motivo.is_(None))
    # Datas parciais não recebem um dia inventado para entrar num filtro diário.
    complete_date = (
        SpirometryExam.data_exame.is_not(None)
        & (SpirometryExam.data_exame_precisao.is_(None) | (SpirometryExam.data_exame_precisao == "dia"))
        & SpirometryExam.data_exame_dia_assumido.is_not(True)
    )
    if inicio is not None:
        statement = statement.where(complete_date, SpirometryExam.data_exame >= inicio)
    if fim is not None:
        statement = statement.where(complete_date, SpirometryExam.data_exame <= fim)
    if origem is not None:
        statement = statement.where(ReportDocument.origin_type == origem)

    conclusions = {x.code: x for x in CONCLUSION_OPTIONS}
    bd_labels = {x.code: x.short_label for x in BRONCHODILATOR_OPTIONS}
    bd_labels["nao_informado"] = "Não registrado"
    groups, codes, bd, sexes, ages, origins, months = (Counter() for _ in range(7))
    custom = Counter()
    total = concluded = classified = no_date = 0
    # Consulta única sem LIMIT da fila operacional (200). Campos mínimos:
    # nenhum nome, CPF, contato ou PDF é lido. O único texto livre lido é a
    # conclusão personalizada, e ela não sai daqui — é consumida em memória
    # por `custom_conclusion_groups.classify` e descartada; a resposta leva
    # apenas rótulo de categoria e contagem.
    for (origin, status, day, precision, assumed, sex, birth, code, bd_code,
         custom_text) in db.execute(statement):
        total += 1
        exact = day is not None and precision in (None, "dia") and not assumed
        if exact:
            months[day.strftime("%Y-%m")] += 1
        else:
            no_date += 1
        origins[origin if origin in ORIGINS else "nao_informado"] += 1
        sexes[sex if sex in ("masculino", "feminino", "outro") else "nao_informado"] += 1
        age = None
        if exact and birth is not None and birth <= day:
            age = day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))
        band = "Não informado" if age is None else AGES[0 if age < 18 else 1 if age < 40 else 2 if age < 60 else 3 if age < 80 else 4]
        ages[band] += 1
        # Ausência de resultado publicado não vira "normal" nem "sem resposta".
        if code and status in ("liberado", "assinado", "finalizado"):
            concluded += 1
            if code in conclusions:
                classified += 1
                groups[conclusions[code].group] += 1
                codes[code] += 1
                # Uma categoria por laudo: o texto cai em exatamente um
                # balde, nunca em dois, para que a soma reconcilie com a
                # fatia "Personalizado" do gráfico de resultados.
                if code == CONCLUSION_CUSTOM_CODE:
                    custom[custom_groups.classify(custom_text)] += 1
            else:
                groups["nao_catalogado"] += 1
                codes["nao_catalogado"] += 1
            bd[bd_code if bd_code in bd_labels else "nao_informado"] += 1
    # Meses intermediários sem produção aparecem como zero, não uma linha
    # contínua que salta silenciosamente de janeiro para abril.
    timeline = []
    if months:
        year, month = map(int, min(months).split("-"))
        end = max(months)
        while f"{year:04d}-{month:02d}" <= end:
            key = f"{year:04d}-{month:02d}"
            timeline.append({"mes": key, "quantidade": months[key]})
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "escopo": "meus_laudos" if physician_profile_id is not None else "institucional",
        "filtros": {"inicio": inicio.isoformat() if inicio else None,
                    "fim": fim.isoformat() if fim else None, "origem": origem},
        "totais": {"laudos": total, "com_conclusao": concluded,
                   "sem_conclusao_publicada": total - concluded,
                   "classificados": classified, "normais": groups["normal"],
                   "sem_data_completa": no_date},
        "evolucao": timeline,
        "resultados": _series(groups, GROUPS),
        "conclusoes": _series(codes, {**{x.code: x.short_label for x in CONCLUSION_OPTIONS},
                                      "nao_catalogado": GROUPS["nao_catalogado"]}),
        "broncodilatador": _series(bd, bd_labels),
        "personalizados": custom_groups.payload(custom, total=groups["personalizado"]),
        "sexo": _series(sexes, {"feminino": "Feminino", "masculino": "Masculino",
                                 "outro": "Outro", "nao_informado": "Não informado"}),
        "faixa_etaria": _series(ages, {x: x for x in AGES}),
        "origens": _series(origins, {**ORIGINS, "nao_informado": "Não informada"}),
        "opcoes_origem": [{"chave": k, "rotulo": v} for k, v in ORIGINS.items()],
    }
