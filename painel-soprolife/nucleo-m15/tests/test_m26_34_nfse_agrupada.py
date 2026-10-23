"""M26.34 — vários exames numa NFS-e só, com tomador diferente dos pacientes.

Tudo aqui é sintético: nomes inventados, CPFs com verificadores válidos que
não pertencem a ninguém conhecido, telefones não discáveis. Nenhum teste
chega ao provedor — nenhum chama a confirmação de emissão, e o contador de
POST permanece zero por construção.

O que precisa valer:

- dois exames compõem UM documento, com UM tomador escolhido, e o total é a
  SOMA dos itens em Decimal — nunca um número informado por quem clica;
- a emissão não cria receita, e nenhum exame ou receita entra em dois
  documentos;
- qualquer mudança no fato (tomador, composição, valor) deixa a preparação
  anterior STALE, de forma que a conferência humana anterior deixe de valer;
- um grupo incoerente (data, município, política, broncodilatador, fluxo)
  é recusado SEM escrever nada — porque o item ocupa o exame;
- o fluxo 1 exame → 1 NFS-e continua exatamente como era;
- CPF nunca sai inteiro, e a trilha de auditoria não ganha PII.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from app.config import get_settings
from app.models import (AuditLog, FinancialEntry, FiscalAttempt, FiscalDocument,
                        FiscalDocumentItem, FiscalIssuanceRequest, FiscalPreparation,
                        Person, SpirometryExam)
from app.services import nfse
from app.services import nfse_grouped as grouped
from app.services.nfse_national import fiscal_config
from app.services.nfse_national.production_profile import (PRODUCTION_EFFECTIVE_FROM,
                                                           production_configuration,
                                                           production_policies)

API = "/api/v1/fiscal"
SERVICE_DAY = date(2026, 9, 16)
RIO = "3304557"
NITEROI = "3303302"


def synthetic_cpf(seed: int) -> str:
    """Verificadores válidos, distinto por seed, de ninguém que conheçamos."""
    digits = [int(c) for c in f"{(seed * 104729 + 3141592) % 10**9:09d}"]
    for _ in range(2):
        n = len(digits)
        total = sum(digits[i] * ((n + 1) - i) for i in range(n))
        rest = (total * 10) % 11
        digits.append(0 if rest == 10 else rest)
    return "".join(map(str, digits))


@pytest.fixture
def prod_env(monkeypatch):
    monkeypatch.setenv("M15_NFSE_ENABLED", "true")
    monkeypatch.setenv("M15_NFSE_ENVIRONMENT", "production")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def prod_env_worker(monkeypatch, tmp_path):
    """Produção COM o executor one-shot configurado, para exercitar os
    caminhos que o router recusa antes por indisponibilidade. O spool é um
    diretório temporário: nada é executado, nada é enviado."""
    spool = tmp_path / "spool"
    spool.mkdir(mode=0o770)
    monkeypatch.setenv("M15_NFSE_ENABLED", "true")
    monkeypatch.setenv("M15_NFSE_ENVIRONMENT", "production")
    monkeypatch.setenv("M15_NFSE_PRODUCTION_WORKER_SPOOL_DIR", str(spool))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def mundo(db, users):
    """Perfil de produção, duas pacientes domiciliares, uma pagadora e os
    exames auxiliares que cada incoerência exige."""
    admin = users["admin"].id
    for policy in production_policies():
        nfse.create_policy(db, policy, admin)
    fiscal_config.create_version(db, environment="production",
                                 effective_from=PRODUCTION_EFFECTIVE_FROM,
                                 validation_state="validated",
                                 configuration=production_configuration(), actor=admin)
    estado = {"pessoas": {}, "exames": {}, "seed": 0}

    def pessoa(label, code, nome, *, cpf="auto"):
        estado["seed"] += 1
        p = Person(public_code=code, nome_completo=nome, nome_normalizado=nome.lower(),
                   cpf=synthetic_cpf(estado["seed"] + 40) if cpf == "auto" else cpf)
        db.add(p)
        db.flush()
        estado["pessoas"][label] = p
        return p

    def exame(label, code, person, *, dia=SERVICE_DAY, municipio=RIO,
              modalidade="residencial", valor="250.00", status_pagamento="Recebido",
              broncodilatador=True, com_receita=True):
        e = SpirometryExam(public_code=code, person_id=person.id, status="Realizado",
                           data_exame=dia, data_exame_precisao="dia",
                           modalidade=modalidade, broncodilatador=broncodilatador,
                           municipio_atendimento_ibge=municipio)
        db.add(e)
        db.flush()
        if com_receita:
            db.add(FinancialEntry(public_code=f"LAN-{code[4:]}", tipo="receita",
                                  categoria="Espirometria", valor=Decimal(valor),
                                  status=status_pagamento, spirometry_exam_id=e.id,
                                  data_competencia=dia, data_recebimento=dia,
                                  forma_pagamento="Pix"))
        db.commit()
        estado["exames"][label] = e
        return e

    primeira = pessoa("primeira", "PES-034001", "Marina Rocha Albuquerque")
    segunda = pessoa("segunda", "PES-034002", "Nelson Teixeira Barros")
    pessoa("pagadora", "PES-034003", "Helena Prado Vasconcelos")
    pessoa("sem_cpf", "PES-034004", "Beatriz Lemos Antunes", cpf=None)
    terceira = pessoa("terceira", "PES-034005", "Rodrigo Alencar Pontes")

    exame("a", "ESP-034001", primeira)
    exame("b", "ESP-034002", segunda)
    exame("outro_dia", "ESP-034003", terceira, dia=date(2026, 9, 17))
    exame("outro_municipio", "ESP-034004", terceira, municipio=NITEROI)
    exame("sem_bd", "ESP-034005", terceira, broncodilatador=False)
    exame("cowork", "ESP-034006", terceira, modalidade="cowork")
    exame("pastore", "ESP-034007", terceira, modalidade="clinica_parceira")
    exame("sem_receita", "ESP-034008", terceira, com_receita=False)
    exame("nao_recebido", "ESP-034009", terceira, status_pagamento="Pendente")
    exame("c", "ESP-034010", primeira)
    return estado


def _ids(mundo, *labels):
    return [mundo["exames"][label].id for label in labels]


def _pagadora(mundo):
    return mundo["pessoas"]["pagadora"].id


def _preparar(client, auth, mundo, labels=("a", "b"), tomador=None, role="gestor",
              db=None):
    if db is not None:
        _solta(db)
    return client.post(f"{API}/producao/agrupados", headers=auth(role), json={
        "spirometry_exam_ids": _ids(mundo, *labels),
        "recipient_person_id": tomador or _pagadora(mundo),
    })


def _previa(client, auth, mundo, labels=("a", "b"), tomador=None, role="gestor"):
    return client.post(f"{API}/producao/agrupados/previa", headers=auth(role), json={
        "spirometry_exam_ids": _ids(mundo, *labels),
        "recipient_person_id": tomador or _pagadora(mundo),
    })


def _solta(db):
    """Solta o banco antes de uma escrita que vai acontecer por HTTP.

    M47/DPS #9, documentado em `nfse.operate()`: o commit do orquestrador
    escala o SQLite a lock EXCLUSIVO e falha com "database is locked" se
    qualquer outra conexão ainda tiver até um lock COMPARTILHADO — inclusive
    a sessão que o próprio teste abriu para conferir o estado. Ler e depois
    escrever pela API, na mesma sessão, reproduz exatamente aquela falha.
    """
    db.rollback()
    # `rollback()` encerra a transação mas a conexão volta ao pool só no
    # close — e é a conexão que detém o lock do arquivo SQLite.
    db.close()


def _count(db, model, *where):
    _solta(db)
    total = db.scalar(select(func.count()).select_from(model).where(*where))
    _solta(db)
    return total


def _erro(response):
    return response.json()["erro"]["mensagem"]


# ── o caso central ────────────────────────────────────────────────────────


def test_dois_exames_compoem_um_documento_com_total_somado(
    prod_env, client, auth, db, mundo
):
    previa = _previa(client, auth, mundo)
    assert previa.status_code == 200, previa.text
    body = previa.json()
    assert body["can_prepare"] is True
    assert body["blocking_reasons"] == []
    assert body["itens_count"] == 2
    assert [i["exam_code"] for i in body["itens"]] == ["ESP-034001", "ESP-034002"]
    assert [i["amount"] for i in body["itens"]] == ["250.00", "250.00"]
    assert body["total"] == "500.00"
    assert body["municipio_ibge"] == RIO
    assert body["municipio_name"] == "Rio de Janeiro/RJ"
    assert body["service_date"] == SERVICE_DAY.isoformat()
    assert body["amount_source"] == "grouped_items.sum"
    assert "2 espirometrias com broncodilatador" in body["service_description"]

    # A prévia é só leitura: nada foi criado.
    assert _count(db, FiscalDocument) == 0
    assert _count(db, FiscalDocumentItem) == 0

    resposta = _preparar(client, auth, mundo, db=db)
    assert resposta.status_code == 201, resposta.text
    doc = resposta.json()
    assert doc["kind"] == "grouped"
    assert doc["spirometry_exam_id"] is None
    assert doc["recipient_person_id"] == _pagadora(mundo)
    assert doc["state"] == "pending"
    assert doc["eligibility"] == "eligible"
    assert doc["blocking_reasons"] == []
    assert [i["exam_code"] for i in doc["itens"]] == ["ESP-034001", "ESP-034002"]
    assert doc["itens_total"] == "500.00"
    assert doc["monetary_source"] == "Soma dos itens do documento (fiscal_document_items)"
    assert doc["preparation"]["amount_snapshot"] == "500.00"
    assert doc["preparation"]["recipient_person_id"] == _pagadora(mundo)
    assert doc["preparation"]["financial_entry_id"] is None
    assert doc["preparation"]["amount_source"] == "grouped_items.sum"


def test_o_total_e_calculado_dos_itens_nao_informado_por_quem_clica(
    prod_env, client, auth, db, mundo
):
    """O contrato de entrada não tem campo de total; o payload é recusado."""
    resposta = client.post(f"{API}/producao/agrupados", headers=auth("gestor"), json={
        "spirometry_exam_ids": _ids(mundo, "a", "b"),
        "recipient_person_id": _pagadora(mundo),
        "total": "10.00",
    })
    assert resposta.status_code == 422

    _preparar(client, auth, mundo, db=db)
    prep = db.scalars(select(FiscalPreparation)).one()
    itens = db.scalars(select(FiscalDocumentItem)).all()
    assert prep.amount_snapshot == sum((i.amount for i in itens), Decimal("0.00"))
    assert prep.amount_snapshot == Decimal("500.00")
    assert isinstance(prep.amount_snapshot, Decimal)


def test_tomador_e_diferente_dos_pacientes_e_nao_vira_paciente(
    prod_env, client, auth, db, mundo
):
    _preparar(client, auth, mundo, db=db)
    doc = db.scalars(select(FiscalDocument)).one()
    pagadora = mundo["pessoas"]["pagadora"]
    pacientes = {mundo["exames"]["a"].person_id, mundo["exames"]["b"].person_id}
    assert doc.recipient_person_id == pagadora.id
    assert pagadora.id not in pacientes
    # A pagadora não ganhou exame nem receita por ter sido tomadora.
    assert _count(db, SpirometryExam, SpirometryExam.person_id == pagadora.id) == 0


def test_a_emissao_nao_cria_receita_nem_duplica_financeiro(
    prod_env, client, auth, db, mundo
):
    antes = _count(db, FinancialEntry)
    total_antes = db.scalar(select(func.sum(FinancialEntry.valor)))
    _preparar(client, auth, mundo, db=db)
    _preparar(client, auth, mundo, db=db)  # replay
    assert _count(db, FinancialEntry) == antes
    assert db.scalar(select(func.sum(FinancialEntry.valor))) == total_antes


# ── ninguém é cobrado duas vezes ──────────────────────────────────────────


def test_exame_de_um_agrupado_nao_entra_em_outro_agrupado(
    prod_env, client, auth, db, mundo
):
    assert _preparar(client, auth, mundo, ("a", "b"), db=db).status_code == 201
    repetido = _preparar(client, auth, mundo, ("a", "c"), db=db)
    assert repetido.status_code == 409
    assert _erro(repetido)["codigo"] == "grouped_document_blocked"
    assert "group_exam_already_billed" in _erro(repetido)["motivos"]
    # E nada foi escrito pela tentativa recusada.
    assert _count(db, FiscalDocument) == 1
    assert _count(db, FiscalDocumentItem) == 2


def test_exame_de_um_agrupado_nao_recebe_documento_individual(
    prod_env, client, auth, db, mundo, users
):
    _preparar(client, auth, mundo, ("a", "b"), db=db)
    _solta(db)
    with pytest.raises(Exception) as excinfo:
        nfse.prepare(db, mundo["exames"]["a"].id, prod_env, users["admin"].id)
    assert "exam_already_in_fiscal_document" in str(excinfo.value)


def test_exame_com_documento_individual_nao_entra_num_agrupado(
    prod_env, client, auth, db, mundo, users
):
    _solta(db)
    nfse.prepare(db, mundo["exames"]["a"].id, prod_env, users["admin"].id)
    bloqueado = _preparar(client, auth, mundo, ("a", "b"), db=db)
    assert bloqueado.status_code == 409
    assert "group_exam_already_billed" in _erro(bloqueado)["motivos"]


def test_a_mesma_receita_nao_aparece_em_dois_documentos(
    prod_env, client, auth, db, mundo
):
    _preparar(client, auth, mundo, ("a", "b"), db=db)
    entradas = [i.financial_entry_id for i in db.scalars(select(FiscalDocumentItem)).all()]
    assert all(entradas) and len(set(entradas)) == 2
    # A trava é do banco: a mesma receita não cabe num segundo item.
    for entry_id in entradas:
        quantas = _count(db, FiscalDocumentItem,
                         FiscalDocumentItem.financial_entry_id == entry_id)
        assert quantas == 1


# ── qualquer mudança no fato invalida a conferência anterior ──────────────


def _fingerprint_atual(db, doc, environment="production"):
    from app.services.idempotency import payload_fingerprint
    return payload_fingerprint(nfse.evaluate_for_document(db, doc, environment,
                                                          for_update=False))


def test_mudanca_de_tomador_deixa_o_preparo_stale(prod_env, client, auth, db, mundo):
    doc_id = _preparar(client, auth, mundo, db=db).json()["id"]
    novo_tomador = mundo["pessoas"]["terceira"].id
    prep_id = nfse.latest_preparation(db, doc_id).id
    impressao_antes = nfse.latest_preparation(db, doc_id).fingerprint
    assert impressao_antes == _fingerprint_atual(db, db.get(FiscalDocument, doc_id))

    _solta(db)
    troca = client.patch(f"{API}/producao/agrupados/{doc_id}/tomador",
                         headers=auth("gestor"),
                         json={"recipient_person_id": novo_tomador})
    assert troca.status_code == 200, troca.text

    _solta(db)
    doc = db.get(FiscalDocument, doc_id)
    assert doc.recipient_person_id == novo_tomador
    # A preparação anterior continua a MESMA linha (append-only) e agora
    # divergente: é isso que `operate()` lê como `preparation_stale`.
    prep = nfse.latest_preparation(db, doc_id)
    assert prep.id == prep_id
    assert prep.fingerprint == impressao_antes
    assert prep.fingerprint != _fingerprint_atual(db, doc)


def test_mudanca_de_composicao_deixa_o_preparo_stale(prod_env, client, auth, db, mundo):
    doc_id = _preparar(client, auth, mundo, ("a", "b"), db=db).json()["id"]
    antiga = nfse.latest_preparation(db, doc_id)
    antiga_id, antiga_fp = antiga.id, antiga.fingerprint

    _solta(db)
    nova = client.post(f"{API}/producao/agrupados/{doc_id}/repreparar",
                       headers=auth("gestor"), json={
                           "spirometry_exam_ids": _ids(mundo, "a", "c"),
                           "recipient_person_id": _pagadora(mundo)})
    assert nova.status_code == 200, nova.text
    _solta(db)
    assert [i["exam_code"] for i in nova.json()["itens"]] == ["ESP-034001", "ESP-034010"]
    prep_nova = nfse.latest_preparation(db, doc_id)
    assert prep_nova.id != antiga_id
    assert prep_nova.fingerprint != antiga_fp
    # O exame que saiu deixou de estar ocupado e pode compor outro documento.
    assert _count(db, FiscalDocumentItem,
                  FiscalDocumentItem.spirometry_exam_id == mundo["exames"]["b"].id) == 0


def test_recompor_mudando_a_ordem_canonica_nao_colide_posicao(
    prod_env, client, auth, db, mundo
):
    """O caso que a unicidade de posição pune se os itens forem atualizados
    no lugar: a composição muda e um exame que era o item 1 passa a ser o 2,
    enquanto um exame novo reclama a posição 1."""
    doc_id = _preparar(client, auth, mundo, ("b", "c"), db=db).json()["id"]
    assert [i.position for i in grouped.document_items(db, doc_id)] == [1, 2]
    assert [db.get(SpirometryExam, i.spirometry_exam_id).public_code
            for i in grouped.document_items(db, doc_id)] == ["ESP-034002", "ESP-034010"]

    _solta(db)
    nova = client.post(f"{API}/producao/agrupados/{doc_id}/repreparar",
                       headers=auth("gestor"), json={
                           "spirometry_exam_ids": _ids(mundo, "a", "b"),
                           "recipient_person_id": _pagadora(mundo)})
    assert nova.status_code == 200, nova.text
    _solta(db)
    itens = grouped.document_items(db, doc_id)
    assert [i.position for i in itens] == [1, 2]
    assert [db.get(SpirometryExam, i.spirometry_exam_id).public_code
            for i in itens] == ["ESP-034001", "ESP-034002"]
    assert nova.json()["itens_total"] == "500.00"


def test_reconciliacao_recusa_pelo_estado_sem_estourar_por_falta_de_exame(
    prod_env_worker, client, auth, db, mundo
):
    """O documento agrupado não tem `spirometry_exam_id`. O caminho de
    reconciliação tem de recusá-lo pelo ESTADO — e não estourar ao tentar
    ler um exame que não existe."""
    doc_id = _preparar(client, auth, mundo, db=db).json()["id"]
    _solta(db)
    pedido = client.post(
        f"{API}/producao/documentos/{doc_id}/solicitar-reconciliacao",
        headers=auth("gestor"), json={"idempotency_key": "m2634-rec-0001"})
    assert pedido.status_code == 409, pedido.text
    assert _erro(pedido)["codigo"] == "reconciliation_not_required"
    assert _count(db, FiscalIssuanceRequest) == 0


def test_mudanca_de_valor_deixa_o_preparo_stale(prod_env, client, auth, db, mundo):
    doc_id = _preparar(client, auth, mundo, db=db).json()["id"]
    impressao_antes = nfse.latest_preparation(db, doc_id).fingerprint
    assert nfse.latest_preparation(db, doc_id).amount_snapshot == Decimal("500.00")

    entrada = db.scalars(select(FinancialEntry).where(
        FinancialEntry.spirometry_exam_id == mundo["exames"]["a"].id)).one()
    entrada.valor = Decimal("300.00")
    db.commit()

    doc = db.get(FiscalDocument, doc_id)
    atual = nfse.evaluate_for_document(db, doc, "production", for_update=False)
    assert atual["total"] == Decimal("550.00")
    assert impressao_antes != _fingerprint_atual(db, doc)


# ── coerência do grupo: recusa sem escrever nada ──────────────────────────


@pytest.mark.parametrize("labels,motivo", [
    (("a", "outro_dia"), "group_service_date_divergent"),
    (("a", "outro_municipio"), "group_service_location_divergent"),
    (("a", "sem_bd"), "group_bronchodilator_divergent"),
    (("a", "cowork"), "group_flow_divergent"),
    (("a", "pastore"), "group_flow_divergent"),
    (("a", "sem_receita"), "financial_entry_missing"),
    (("a", "nao_recebido"), "financial_revenue_not_received_or_invalid"),
])
def test_grupo_incoerente_e_recusado_sem_escrever_nada(
    prod_env, client, auth, db, mundo, labels, motivo
):
    resposta = _preparar(client, auth, mundo, labels, db=db)
    assert resposta.status_code == 409, resposta.text
    assert motivo in _erro(resposta)["motivos"]
    assert _count(db, FiscalDocument) == 0
    assert _count(db, FiscalDocumentItem) == 0
    assert _count(db, FiscalPreparation) == 0


def test_todo_bloqueio_tem_rotulo_em_portugues(prod_env, db, mundo):
    """Um operador nunca deve ler um código técnico na tela. Vale para os
    motivos de grupo E para os que vêm da avaliação de cada exame."""
    from app.services.nfse import RECIPIENT_FISCAL_REASONS
    faltando = [code for code in (grouped.GROUP_REASONS + RECIPIENT_FISCAL_REASONS)
                if code not in grouped.GROUP_REASON_LABELS]
    assert faltando == [], faltando
    # E todo rótulo é frase, não código.
    for code, label in grouped.GROUP_REASON_LABELS.items():
        assert label != code and " " in label, code


def test_um_dos_pacientes_pode_ser_o_tomador(prod_env, client, auth, db, mundo):
    """Um cônjuge que paga os dois exames é tomador legítimo da nota inteira,
    mesmo sendo beneficiário de um deles — não há regra que o impeça."""
    paciente_a = mundo["exames"]["a"].person_id
    data = grouped.evaluate_group(db, _ids(mundo, "a", "b"), paciente_a,
                                  "production", for_update=False)
    assert data["blocking_reasons"] == []
    assert data["recipient_person_id"] == paciente_a
    assert data["total"] == Decimal("500.00")


def test_um_exame_so_nao_e_um_grupo(prod_env, client, auth, mundo):
    resposta = client.post(f"{API}/producao/agrupados", headers=auth("gestor"), json={
        "spirometry_exam_ids": _ids(mundo, "a"),
        "recipient_person_id": _pagadora(mundo)})
    assert resposta.status_code == 422


def test_tomador_sem_cpf_bloqueia_em_producao(prod_env, client, auth, db, mundo):
    resposta = _preparar(client, auth, mundo, tomador=mundo["pessoas"]["sem_cpf"].id, db=db)
    assert resposta.status_code == 409
    assert "recipient_cpf_missing" in _erro(resposta)["motivos"]
    assert _count(db, FiscalDocument) == 0


def test_paciente_sem_cpf_nao_impede_a_nota_do_tomador(prod_env, db, mundo, users):
    """A identidade fiscal que vai na nota é a do TOMADOR, não a do paciente."""
    paciente = db.get(Person, mundo["exames"]["b"].person_id)
    paciente.cpf = None
    db.commit()
    data = grouped.evaluate_group(db, _ids(mundo, "a", "b"), _pagadora(mundo),
                                  "production", for_update=False)
    assert data["blocking_reasons"] == []
    assert data["total"] == Decimal("500.00")


def test_identidade_fiscal_fraca_do_paciente_nao_impede_a_nota_do_tomador(
    prod_env, db, mundo
):
    """O oposto do fluxo single: ali o nome do paciente VAI na nota e um
    nome-marcador a bloqueia; aqui quem vai é o tomador."""
    paciente = db.get(Person, mundo["exames"]["b"].person_id)
    paciente.nome_completo = "Paciente Teste Silva"
    paciente.nome_normalizado = "paciente teste silva"
    db.commit()

    # No fluxo de UM exame, isto bloqueia — e continua bloqueando.
    individual = nfse.evaluate(db, mundo["exames"]["b"].id, "production",
                               for_update=False)
    assert "recipient_name_looks_like_placeholder" in individual["blocking_reasons"]

    # No agrupado, não: a identidade da nota é a do tomador escolhido.
    grupo = grouped.evaluate_group(db, _ids(mundo, "a", "b"), _pagadora(mundo),
                                   "production", for_update=False)
    assert grupo["blocking_reasons"] == []
    assert grupo["total"] == Decimal("500.00")


def test_paciente_arquivado_ainda_bloqueia(prod_env, db, mundo):
    from datetime import datetime, timezone
    paciente = db.get(Person, mundo["exames"]["b"].person_id)
    paciente.arquivado = True
    paciente.arquivado_em = datetime.now(timezone.utc)
    paciente.arquivado_motivo = "cadastro sintético de teste"
    db.commit()
    data = grouped.evaluate_group(db, _ids(mundo, "a", "b"), _pagadora(mundo),
                                  "production", for_update=False)
    assert "recipient_missing_or_archived" in data["blocking_reasons"]


# ── idempotência, papéis e ausência de emissão ────────────────────────────


def test_preparar_duas_vezes_devolve_o_mesmo_documento(prod_env, client, auth, db, mundo):
    primeiro = _preparar(client, auth, mundo, db=db).json()
    segundo = _preparar(client, auth, mundo, db=db).json()
    assert primeiro["id"] == segundo["id"]
    assert _count(db, FiscalDocument) == 1
    assert _count(db, FiscalDocumentItem) == 2
    # Fato idêntico: a preparação não se multiplica.
    assert _count(db, FiscalPreparation) == 1


def test_a_ordem_dos_exames_nao_cria_documento_diferente(prod_env, client, auth, db, mundo):
    primeiro = _preparar(client, auth, mundo, ("a", "b"), db=db).json()
    invertido = _preparar(client, auth, mundo, ("b", "a"), db=db).json()
    assert invertido["id"] == primeiro["id"]
    assert _count(db, FiscalDocument) == 1


def test_leitura_nao_prepara_nem_troca_tomador(prod_env, client, auth, mundo):
    assert _preparar(client, auth, mundo, role="leitura").status_code == 403
    assert _previa(client, auth, mundo, role="leitura").status_code == 403


def test_nada_foi_emitido_e_o_provedor_nao_recebeu_post(
    prod_env, client, auth, db, mundo
):
    _preparar(client, auth, mundo, db=db)
    assert _count(db, FiscalAttempt) == 0
    assert _count(db, FiscalIssuanceRequest) == 0
    doc = db.scalars(select(FiscalDocument)).one()
    assert doc.state == "pending"
    assert db.scalar(select(func.coalesce(
        func.sum(FiscalIssuanceRequest.provider_post_count), 0))) == 0


def test_o_banco_impede_mais_de_um_post_por_pedido(prod_env, db, mundo):
    """A trava do contador de POST é do banco, não do código do worker."""
    from sqlalchemy import inspect as sa_inspect
    checks = {c["name"] for c in sa_inspect(db.bind).get_check_constraints(
        "fiscal_issuance_requests")}
    assert any("one_post" in name for name in checks)


def test_importar_nota_manual_de_exame_agrupado_e_recusado(
    prod_env, client, auth, db, mundo, users
):
    """O caminho M61 (nota emitida à mão, registrada depois) também cria
    documento por exame. Sem a trava de ocupação ele daria ao exame uma
    segunda nota — uma no grupo, outra importada."""
    from app.services.nfse_external_issuance import (ExternalIssuance,
                                                     register_external_issuance)
    chave = "33045572263544026000110000000000000426099130462655"
    _preparar(client, auth, mundo, ("a", "b"), db=db)
    _solta(db)
    with pytest.raises(Exception) as excinfo:
        register_external_issuance(
            db, ExternalIssuance(mundo["exames"]["a"].id, chave, "2026-09-16"),
            users["admin"].id)
    assert "exam_already_in_fiscal_document" in str(excinfo.value)
    db.rollback()
    # E segue existindo UM documento só: o agrupado.
    assert _count(db, FiscalDocument) == 1


def test_a_fila_individual_para_de_oferecer_o_exame_agrupado(
    prod_env, client, auth, db, mundo
):
    """Sem isto, um exame já dentro da nota agrupada apareceria na fila como
    "pronto para emitir", com botão — e só a trava de ocupação o pararia,
    depois do clique."""
    antes = client.get(f"{API}/producao/fila", headers=auth("gestor")).json()
    por_codigo = {r["exam_code"]: r for r in antes["itens"]}
    assert por_codigo["ESP-034001"]["status"] == "ready"
    assert por_codigo["ESP-034001"]["can_issue"] is True

    _solta(db)
    doc_id = _preparar(client, auth, mundo, ("a", "b"), db=db).json()["id"]

    depois = client.get(f"{API}/producao/fila", headers=auth("gestor")).json()
    por_codigo = {r["exam_code"]: r for r in depois["itens"]}
    for codigo in ("ESP-034001", "ESP-034002"):
        linha = por_codigo[codigo]
        assert linha["status"] == "grouped", linha
        assert linha["status_label"] == "Em nota agrupada — aguardando emissão"
        assert linha["can_issue"] is False
        assert linha["can_reconcile"] is False
        assert linha["grouped_document_id"] == doc_id
    # Um exame fora do grupo segue oferecido normalmente.
    assert por_codigo["ESP-034010"]["status"] == "ready"


def test_o_worker_revalida_um_documento_agrupado_sem_estourar(
    prod_env_worker, client, auth, db, mundo, users
):
    """A revalidação independente do executor one-shot tem de funcionar para
    o agrupado — senão a emissão manual futura morreria no portão, antes de
    qualquer POST. Nada é enviado aqui: só `guards()` é exercitado."""
    import importlib.util

    doc_id = _preparar(client, auth, mundo, db=db).json()["id"]
    _solta(db)
    doc = db.get(FiscalDocument, doc_id)
    prep = nfse.latest_preparation(db, doc_id)

    spec = importlib.util.spec_from_file_location(
        "m2634_worker", Path(__file__).parents[1] / "scripts" / "nfse_production_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    facts = {
        "document_id": doc.id,
        "exam_id": doc.spirometry_exam_id,          # None: é um agrupado
        "preparation_id": prep.id,
        "preparation_fingerprint": prep.fingerprint,
        "recipient_fingerprint": None,              # conferido abaixo
        "amount": Decimal("500.00"),
        "kind": "issue",
    }
    from app.services.nfse_production_issuance import recipient_fingerprint
    facts["recipient_fingerprint"] = recipient_fingerprint(
        db.get(Person, prep.recipient_person_id))

    falhas, esperado = worker.guards(db, facts, prod_env_worker)
    # A única recusa aceitável aqui é AMBIENTAL: não existe certificado A1
    # nesta máquina de teste, e o guard de margem do A1 diz isso. Nenhuma
    # recusa relativa ao documento, aos itens, ao total, ao tomador ou ao
    # município pode aparecer — é justamente isso que se está provando.
    assert falhas == ["certificate_margin_insufficient"], falhas
    assert esperado["vServ"] == "500.00"
    assert esperado["cLocPrestacao"] == RIO
    assert esperado["dCompet"] == SERVICE_DAY.isoformat()


def test_o_worker_recusa_se_o_total_dos_itens_mudar(
    prod_env_worker, client, auth, db, mundo
):
    """O total é reconferido a partir das LINHAS, não só do snapshot."""
    import importlib.util

    doc_id = _preparar(client, auth, mundo, db=db).json()["id"]
    _solta(db)
    doc = db.get(FiscalDocument, doc_id)
    prep = nfse.latest_preparation(db, doc_id)
    from app.services.nfse_production_issuance import recipient_fingerprint
    facts = {
        "document_id": doc.id, "exam_id": None, "preparation_id": prep.id,
        "preparation_fingerprint": prep.fingerprint,
        "recipient_fingerprint": recipient_fingerprint(
            db.get(Person, prep.recipient_person_id)),
        "amount": Decimal("500.00"), "kind": "issue",
    }
    # Um item adulterado por fora: a soma deixa de fechar com o snapshot.
    item = grouped.document_items(db, doc_id)[0]
    item.amount = Decimal("200.00")
    db.commit()

    spec = importlib.util.spec_from_file_location(
        "m2634_worker2", Path(__file__).parents[1] / "scripts" / "nfse_production_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    falhas, _ = worker.guards(db, facts, prod_env_worker)
    assert "grouped_total_mismatch" in falhas, falhas


# ── o fluxo antigo, intacto ───────────────────────────────────────────────


def test_fluxo_individual_continua_funcionando(prod_env, client, auth, db, mundo, users):
    _solta(db)
    doc = nfse.prepare(db, mundo["exames"]["cowork"].id, prod_env, users["admin"].id)
    assert doc.kind == "single"
    assert doc.spirometry_exam_id == mundo["exames"]["cowork"].id
    assert doc.recipient_person_id is None
    assert doc.state == "pending" and doc.eligibility == "eligible"
    prep = nfse.latest_preparation(db, doc.id)
    assert prep.amount_snapshot == Decimal("250.00")
    assert prep.financial_entry_id is not None
    assert prep.amount_source is None
    # Ganhou o item de OCUPAÇÃO, sem valor (a verdade monetária é a preparação).
    item = db.scalars(select(FiscalDocumentItem).where(
        FiscalDocumentItem.document_id == doc.id)).one()
    assert item.amount is None and item.position == 1
    serializado = nfse.serialize_document(db, doc)
    assert serializado["monetary_source"] == "Financeiro_Lancamentos / financial_entries"
    assert serializado["itens_total"] is None


def test_o_resumo_de_confirmacao_mostra_os_itens_e_mascara_o_cpf(
    prod_env, client, auth, db, mundo
):
    from app.services import nfse_production_issuance as production
    doc_id = _preparar(client, auth, mundo, db=db).json()["id"]
    _solta(db)
    resumo = client.get(f"{API}/producao/documentos/{doc_id}/confirmacao",
                        headers=auth("gestor"))
    assert resumo.status_code == 200, resumo.text
    body = resumo.json()
    assert body["kind"] == "grouped"
    assert body["itens_count"] == 2
    assert [i["exam_code"] for i in body["itens"]] == ["ESP-034001", "ESP-034002"]
    assert [i["amount"] for i in body["itens"]] == ["250.00", "250.00"]
    assert body["amount"] == "500.00"
    assert body["can_confirm"] is True
    assert body["cpf_masked"].startswith("***.***.***-")
    pagadora = db.get(Person, _pagadora(mundo))
    assert pagadora.cpf not in resumo.text
    assert production.mask_cpf(pagadora.cpf) == body["cpf_masked"]


# ── privacidade ───────────────────────────────────────────────────────────


def test_nenhum_cpf_ou_nome_na_trilha_nem_nas_respostas(
    prod_env, client, auth, db, mundo
):
    previa = _previa(client, auth, mundo)
    preparado = _preparar(client, auth, mundo, db=db)
    pagadora = db.get(Person, _pagadora(mundo))

    # O CPF inteiro não sai em resposta nenhuma; o nome aparece só na prévia,
    # que é a tela de conferência, e nunca no documento serializado.
    assert pagadora.cpf not in previa.text
    assert pagadora.cpf not in preparado.text
    assert pagadora.nome_completo not in preparado.text

    linhas = db.scalars(select(AuditLog).where(
        AuditLog.acao.like("fiscal.grouped%"))).all()
    assert linhas, "o preparo agrupado tem de deixar trilha"
    for linha in linhas:
        texto = f"{linha.detalhes}"
        assert pagadora.cpf not in texto
        assert pagadora.nome_completo not in texto
        for pessoa in mundo["pessoas"].values():
            if pessoa.cpf:
                assert pessoa.cpf not in texto
    # O que a trilha guarda: contagem e total, em vocabulário fechado.
    detalhes = [l.detalhes for l in linhas if l.acao == "fiscal.grouped_prepared"]
    assert detalhes and detalhes[0].get("itens") == 2
    assert detalhes[0].get("total") == "500.00"


# ── migration ─────────────────────────────────────────────────────────────


def test_migration_e_aditiva_reversivel_e_recusa_apagar_agrupado(tmp_path, monkeypatch):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/m26-34.db"
    root = Path(__file__).parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect, text

    script = ScriptDirectory.from_config(cfg)
    # M26.36 (c3f8a1d7e529, reteste de controle sem cobrança) entra por
    # cima, aditiva: a cadeia fiscal agrupada abaixo segue intacta.
    assert script.get_heads() == ["d7b2c4e86a91"]
    assert script.get_revision("d7b2c4e86a91").down_revision == "c3f8a1d7e529"
    assert script.get_revision("c3f8a1d7e529").down_revision == "b5e2f7a91c38"
    assert script.get_revision("b5e2f7a91c38").down_revision == "a1c7e5d9b204"

    command.upgrade(cfg, "a1c7e5d9b204")
    engine = create_engine(url)
    antes = {t: [c["name"] for c in inspect(engine).get_columns(t)]
             for t in inspect(engine).get_table_names()}
    assert "fiscal_document_items" not in antes

    command.upgrade(cfg, "head")
    command.check(cfg)
    depois = inspect(engine)
    assert "fiscal_document_items" in depois.get_table_names()
    # Os gatilhos que o rebuild derruba foram repostos.
    with engine.connect() as conn:
        triggers = {r[0] for r in conn.execute(text(
            "select name from sqlite_master where type='trigger'"))}
    assert {"fiscal_documents_no_delete", "fiscal_documents_identity",
            "fiscal_preparations_no_update", "fiscal_preparations_no_delete"} <= triggers

    # Vazio: desce e sobe.
    command.downgrade(cfg, "a1c7e5d9b204")
    volta = {t: [c["name"] for c in inspect(engine).get_columns(t)] for t in antes}
    assert volta == antes
    command.upgrade(cfg, "head")
    command.check(cfg)
    engine.dispose()


def test_o_downgrade_se_recusa_quando_existe_documento_agrupado(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/m26-34-cheio.db"
    root = Path(__file__).parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    command.upgrade(cfg, "head")
    engine = create_engine(url)
    from app.models import User
    from app.security import hash_password
    with Session(engine) as session:
        pessoa = Person(public_code="PES-934001", nome_completo="Pessoa Sintetica Migration",
                        nome_normalizado="pessoa sintetica migration")
        usuario = User(email="m2634@teste.local", nome="Teste M26.34",
                       password_hash=hash_password("senha-teste-123"))
        session.add_all([pessoa, usuario])
        session.commit()
        session.add(FiscalDocument(
            spirometry_exam_id=None, kind="grouped", environment="production",
            recipient_person_id=pessoa.id, created_by=usuario.id,
            idempotency_key="k-m2634", idempotency_fingerprint="f-m2634"))
        session.commit()
    with pytest.raises(RuntimeError, match="Abortado"):
        command.downgrade(cfg, "a1c7e5d9b204")
    with engine.connect() as conn:
        assert conn.execute(text(
            "select count(*) from fiscal_documents where kind='grouped'")).scalar() == 1

    # E a natureza do documento é imutável no BANCO: `kind` entra na
    # identidade protegida pelo gatilho. (Só verificável aqui: o banco das
    # outras provas vem de `create_all`, que não cria gatilho nenhum.)
    with engine.begin() as conn:
        with pytest.raises(Exception, match="immutable fiscal identity"):
            conn.execute(text(
                "update fiscal_documents set kind='single' where kind='grouped'"))
    engine.dispose()


def test_o_backfill_cria_item_de_ocupacao_para_documento_historico(
    tmp_path, monkeypatch
):
    """A trava universal tem de valer também sobre o que já existia."""
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/m26-34-backfill.db"
    root = Path(__file__).parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    command.upgrade(cfg, "a1c7e5d9b204")
    engine = create_engine(url)
    from app.models import User
    from app.security import hash_password
    with Session(engine) as session:
        pessoa = Person(public_code="PES-934002", nome_completo="Paciente Sintetico Historico",
                        nome_normalizado="paciente sintetico historico")
        usuario = User(email="hist@teste.local", nome="Teste Historico",
                       password_hash=hash_password("senha-teste-123"))
        session.add_all([pessoa, usuario])
        session.commit()
        # SQL cru pelo MESMO motivo do documento fiscal abaixo: o banco está
        # na revisão anterior à M26.34 e o modelo ORM de `spirometry_exams`
        # já conhece `reteste_sem_cobranca` (M26.36), que ainda não existe
        # ali. Inserir pelo ORM escreveria uma coluna do futuro.
        exam_id = "22222222-2222-4222-8222-222222222222"
        session.execute(text(
            "INSERT INTO spirometry_exams (id, public_code, person_id, status, "
            "data_exame, data_exame_dia_assumido, modalidade, broncodilatador, "
            "created_at, updated_at) VALUES (:id, 'ESP-934001', :pessoa, "
            "'Realizado', :dia, 0, 'residencial', 1, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP)"),
            {"id": exam_id, "pessoa": pessoa.id, "dia": SERVICE_DAY})
        session.commit()
        # SQL cru de propósito: o banco está na revisão ANTERIOR à M26.34 e
        # o modelo ORM já conhece `kind`/`recipient_person_id`, que ainda não
        # existem ali. É assim que se escreve "um documento como os de antes".
        session.execute(text(
            "INSERT INTO fiscal_documents (id, spirometry_exam_id, environment, "
            "state, eligibility, blocking_reasons, created_by, idempotency_key, "
            "idempotency_fingerprint, created_at, updated_at) VALUES "
            "(:id, :exame, 'production', 'issued', 'eligible', '[]', :user, "
            "'k-hist', 'f-hist', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {"id": "11111111-1111-4111-8111-111111111111", "exame": exam_id,
             "user": usuario.id})
        session.commit()

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        linhas = conn.execute(text(
            "select spirometry_exam_id, environment, amount, financial_entry_id, position "
            "from fiscal_document_items")).all()
    assert linhas == [(exam_id, "production", None, None, 1)]
    engine.dispose()
