"""M23.1 — regressões funcionais dos bypasses de receita duplicada."""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Consultation, FinancialEntry, Person, SpirometryExam


VARIANTES_ESPIROMETRIA = [
    "Espirometria",
    "espirometria",
    "ESPIROMETRIA",
    "EsPiRoMeTrIa",
    " \tEspirometria\n ",
    "Ｅｓｐｉｒｏｍｅｔｒｉａ",
    "Espirometria\ufe0f",
    "Espi\u034frometria",
]
VARIANTES_CONSULTA = [
    "Consulta",
    "consulta",
    "CONSULTA",
    "ConSulTa",
    " \tConsulta\n ",
    "Ｃｏｎｓｕｌｔａ",
    "Consulta\u180b",
]


def _atendimento_com_receita(client, auth, person, componente):
    if componente == "exame":
        payload = {
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "10/01/2026",
                "status": "Realizado",
                "modalidade": "residencial",
            },
            "financeiro": {
                "espirometria": {"valor": "220.00", "status": "Pendente"}
            },
        }
        link_field = "spirometry_exam_id"
        result_field = "espirometria"
    else:
        payload = {
            "person_id": person["id"],
            "tipo": "consulta_soprolife",
            "consulta": {
                "data_consulta": "10/01/2026",
                "status": "Realizada",
                "modalidade": "teleconsulta",
                "retorno": "sem_retorno",
            },
            "financeiro": {
                "consulta": {"valor_bruto": "300.00", "status": "Pendente"}
            },
        }
        link_field = "consultation_id"
        result_field = "consulta"
    response = client.post(
        "/api/v1/atendimentos", json=payload, headers=auth("operacional")
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {
        "link_field": link_field,
        "link_id": body[result_field]["id"],
        "existing_code": body["lancamentos"][0]["public_code"],
    }


def _vinculo_sem_receita(client, auth, person, componente):
    if componente == "exame":
        response = client.post(
            "/api/v1/espirometrias",
            json={
                "person_id": person["id"],
                "data_exame": "11/01/2026",
                "status": "Realizado",
            },
            headers=auth("operacional"),
        )
        link_field = "spirometry_exam_id"
    else:
        response = client.post(
            "/api/v1/consultas",
            json={
                "person_id": person["id"],
                "data_consulta": "11/01/2026",
                "status": "Realizada",
            },
            headers=auth("operacional"),
        )
        link_field = "consultation_id"
    assert response.status_code == 201, response.text
    return link_field, response.json()["id"]


def _detalhe_409(response):
    assert response.status_code == 409, response.text
    detalhe = response.json()["erro"]["mensagem"]
    assert detalhe["codigo"] == "receita_ja_existe"
    assert set(detalhe) == {
        "codigo",
        "mensagem",
        "lancamento_existente",
        "lancamento_existente_id",
    }
    return detalhe


@pytest.mark.parametrize("categoria", VARIANTES_ESPIROMETRIA)
def test_post_exame_bloqueia_caixa_espaco_e_unicode(
    client, auth, person, categoria
):
    alvo = _atendimento_com_receita(client, auth, person, "exame")
    response = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": categoria,
            "valor": "220.00",
            alvo["link_field"]: alvo["link_id"],
        },
        headers=auth("gestor"),
    )
    detalhe = _detalhe_409(response)
    assert detalhe["lancamento_existente"] == alvo["existing_code"]


@pytest.mark.parametrize("categoria", VARIANTES_CONSULTA)
def test_post_consulta_bloqueia_caixa_espaco_e_unicode(
    client, auth, person, categoria
):
    alvo = _atendimento_com_receita(client, auth, person, "consulta")
    response = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": categoria,
            "valor": "300.00",
            alvo["link_field"]: alvo["link_id"],
        },
        headers=auth("gestor"),
    )
    detalhe = _detalhe_409(response)
    assert detalhe["lancamento_existente"] == alvo["existing_code"]


@pytest.mark.parametrize("componente", ["exame", "consulta"])
def test_post_categoria_ausente_bloqueia_receita_existente(
    client, auth, person, componente
):
    alvo = _atendimento_com_receita(client, auth, person, componente)
    response = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "valor": "100.00",
            alvo["link_field"]: alvo["link_id"],
        },
        headers=auth("gestor"),
    )
    _detalhe_409(response)


@pytest.mark.parametrize(
    ("componente", "categoria"),
    [("exame", "Espirometria"), ("consulta", "Consulta")],
)
def test_post_categoria_ausente_e_canonizada_quando_vinculo_e_inequivoco(
    client, auth, person, componente, categoria
):
    link_field, link_id = _vinculo_sem_receita(client, auth, person, componente)
    first = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": "100.00", link_field: link_id},
        headers=auth("gestor"),
    )
    assert first.status_code == 201, first.text
    assert first.json()["categoria"] == categoria

    second = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": "100.00", link_field: link_id},
        headers=auth("gestor"),
    )
    _detalhe_409(second)


@pytest.mark.parametrize(
    ("componente", "primeira_categoria", "replay_categoria"),
    [
        ("exame", "espirometria", "Ｅｓｐｉｒｏｍｅｔｒｉａ"),
        ("consulta", " consulta ", "CONSULTA"),
    ],
)
def test_replay_idempotente_equivalente_de_receita_continua_funcional(
    client,
    auth,
    person,
    componente,
    primeira_categoria,
    replay_categoria,
):
    link_field, link_id = _vinculo_sem_receita(client, auth, person, componente)
    payload = {
        "tipo": "receita",
        "categoria": primeira_categoria,
        "valor": "100.00",
        link_field: link_id,
        "idempotency_key": f"m23-replay-{componente}",
    }
    first = client.post(
        "/api/v1/lancamentos", json=payload, headers=auth("gestor")
    )
    assert first.status_code == 201, first.text

    payload["categoria"] = replay_categoria
    replay = client.post(
        "/api/v1/lancamentos", json=payload, headers=auth("gestor")
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["idempotente"] is True


@pytest.mark.parametrize(
    ("componente", "categoria_patch"),
    [("exame", " \tESPIROMETRIA "), ("consulta", "Ｃｏｎｓｕｌｔａ")],
)
def test_patch_outro_para_receita_propria_duplicada_e_bloqueado(
    client, auth, person, componente, categoria_patch
):
    alvo = _atendimento_com_receita(client, auth, person, componente)
    outro = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": "Outro",
            "valor": "15.00",
            alvo["link_field"]: alvo["link_id"],
        },
        headers=auth("gestor"),
    )
    assert outro.status_code == 201, outro.text

    response = client.patch(
        f"/api/v1/lancamentos/{outro.json()['id']}",
        json={"categoria": categoria_patch},
        headers=auth("gestor"),
    )
    detalhe = _detalhe_409(response)
    assert detalhe["lancamento_existente"] == alvo["existing_code"]

    persisted = client.post(
        "/api/v1/lancamentos/busca",
        json={"q": outro.json()["public_code"]},
        headers=auth("leitura"),
    ).json()["itens"][0]
    assert persisted["categoria"] == "Outro"


@pytest.mark.parametrize(
    ("componente", "categoria_input", "categoria_canonica"),
    [
        ("exame", "espirometria", "Espirometria"),
        ("consulta", " consulta ", "Consulta"),
    ],
)
def test_patch_do_proprio_lancamento_e_canonico_e_permitido(
    client, auth, person, componente, categoria_input, categoria_canonica
):
    alvo = _atendimento_com_receita(client, auth, person, componente)
    existente = client.post(
        "/api/v1/lancamentos/busca",
        json={"q": alvo["existing_code"]},
        headers=auth("leitura"),
    ).json()["itens"][0]

    response = client.patch(
        f"/api/v1/lancamentos/{existente['id']}",
        json={"categoria": categoria_input, "forma_pagamento": "Pix"},
        headers=auth("gestor"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["categoria"] == categoria_canonica
    assert response.json()["forma_pagamento"] == "Pix"


@pytest.mark.parametrize(
    ("componente", "categoria"),
    [("exame", "Ｅｓｐｉｒｏｍｅｔｒｉａ"), ("consulta", "CONSULTA")],
)
def test_patch_outro_para_receita_propria_sem_duplicata_e_permitido(
    client, auth, person, componente, categoria
):
    link_field, link_id = _vinculo_sem_receita(client, auth, person, componente)
    outro = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": "Outro",
            "valor": "25.00",
            link_field: link_id,
        },
        headers=auth("gestor"),
    )
    assert outro.status_code == 201, outro.text
    response = client.patch(
        f"/api/v1/lancamentos/{outro.json()['id']}",
        json={"categoria": categoria},
        headers=auth("gestor"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["categoria"] == (
        "Espirometria" if componente == "exame" else "Consulta"
    )


@pytest.mark.parametrize("componente", ["exame", "consulta"])
def test_ajuste_despesa_repasse_e_categoria_outro_continuam_permitidos(
    client, auth, person, componente
):
    alvo = _atendimento_com_receita(client, auth, person, componente)
    comum = {alvo["link_field"]: alvo["link_id"]}
    outro = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": "Outro",
            "valor": "10.00",
            **comum,
        },
        headers=auth("gestor"),
    )
    assert outro.status_code == 201, outro.text
    despesa = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "despesa",
            "categoria": "Ajuste",
            "valor": "5.00",
            **comum,
        },
        headers=auth("gestor"),
    )
    assert despesa.status_code == 201, despesa.text
    repasse = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "repasse",
            "categoria": "Repasse ao médico",
            "valor": "3.00",
            **comum,
        },
        headers=auth("gestor"),
    )
    assert repasse.status_code == 201, repasse.text


def test_vinculo_combinado_nao_contorna_categoria_explicita_ou_ausente(
    client, auth, person
):
    response = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_consulta_soprolife",
            "espirometria": {
                "data_exame": "10/01/2026",
                "status": "Realizado",
                "modalidade": "residencial",
            },
            "consulta": {
                "data_consulta": "10/01/2026",
                "status": "Realizada",
                "modalidade": "teleconsulta",
                "retorno": "sem_retorno",
            },
            "financeiro": {
                "espirometria": {"valor": "220.00"},
                "consulta": {"valor_bruto": "300.00"},
            },
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    links = {
        "spirometry_exam_id": body["espirometria"]["id"],
        "consultation_id": body["consulta"]["id"],
    }
    for categoria in ("Espirometria", "Consulta"):
        duplicate = client.post(
            "/api/v1/lancamentos",
            json={
                "tipo": "receita",
                "categoria": categoria,
                "valor": "10.00",
                **links,
            },
            headers=auth("gestor"),
        )
        _detalhe_409(duplicate)
    ambiguous = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": "10.00", **links},
        headers=auth("gestor"),
    )
    assert ambiguous.status_code == 422
    assert (
        ambiguous.json()["erro"]["mensagem"]["codigo"]
        == "categoria_obrigatoria_vinculo_ambiguo"
    )


@pytest.mark.parametrize(
    ("componente", "categoria"),
    [("exame", "Espirometria"), ("consulta", "Consulta")],
)
def test_linha_historica_ambigua_sem_categoria_retorna_409_seguro(
    client, auth, person, db, componente, categoria
):
    exam_field, exam_id = _vinculo_sem_receita(client, auth, person, "exame")
    consultation_field, consultation_id = _vinculo_sem_receita(
        client, auth, person, "consulta"
    )
    historica = _linha(
        "LAN-909090",
        tipo="receita",
        categoria=None,
        exam_id=exam_id,
        consultation_id=consultation_id,
    )
    db.add(historica)
    db.commit()

    link_field, link_id = (
        (exam_field, exam_id)
        if componente == "exame"
        else (consultation_field, consultation_id)
    )
    response = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": categoria,
            "valor": "10.00",
            link_field: link_id,
        },
        headers=auth("gestor"),
    )
    detalhe = _detalhe_409(response)
    assert detalhe["lancamento_existente"] == "LAN-909090"


def test_patch_metadado_em_receita_historica_ambigua_e_permitido(
    client, auth, person, db
):
    """M-1 (revisão pós-M23.1): PATCH de metadado numa linha histórica
    ambígua (categoria NULL, ambos os vínculos) não deve mais ficar
    permanentemente travado — a linha só não pode ser RECLASSIFICADA
    implicitamente."""
    _, exam_id = _vinculo_sem_receita(client, auth, person, "exame")
    _, consultation_id = _vinculo_sem_receita(client, auth, person, "consulta")
    historica = _linha(
        "LAN-909092",
        tipo="receita",
        categoria=None,
        exam_id=exam_id,
        consultation_id=consultation_id,
    )
    db.add(historica)
    db.commit()
    entry_id = historica.id

    response = client.patch(
        f"/api/v1/lancamentos/{entry_id}",
        json={
            "status": "Recebido",
            "data_recebimento": "2026-07-20",
            "forma_pagamento": "Pix",
        },
        headers=auth("gestor"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "Recebido"
    assert body["forma_pagamento"] == "Pix"
    # Categoria NÃO foi inferida nem alterada — segue exatamente como estava.
    assert body["categoria"] is None


def test_patch_reclassificacao_ambigua_explicita_continua_bloqueada(
    client, auth, person, db
):
    """M-1: uma vez que o caller TOCA em categoria (mesmo enviando None de
    volta), a validação completa de ambiguidade continua rodando — só o
    PATCH que não menciona categoria é isento."""
    _, exam_id = _vinculo_sem_receita(client, auth, person, "exame")
    _, consultation_id = _vinculo_sem_receita(client, auth, person, "consulta")
    historica = _linha(
        "LAN-909093",
        tipo="receita",
        categoria=None,
        exam_id=exam_id,
        consultation_id=consultation_id,
    )
    db.add(historica)
    db.commit()
    entry_id = historica.id

    resposta_null = client.patch(
        f"/api/v1/lancamentos/{entry_id}",
        json={"categoria": None, "status": "Recebido"},
        headers=auth("gestor"),
    )
    assert resposta_null.status_code == 422, resposta_null.text
    assert (
        resposta_null.json()["erro"]["mensagem"]["codigo"]
        == "categoria_obrigatoria_vinculo_ambiguo"
    )

    resposta_ambigua = client.patch(
        f"/api/v1/lancamentos/{entry_id}",
        json={"categoria": "  ", "status": "Recebido"},
        headers=auth("gestor"),
    )
    assert resposta_ambigua.status_code == 422, resposta_ambigua.text
    assert (
        resposta_ambigua.json()["erro"]["mensagem"]["codigo"]
        == "categoria_obrigatoria_vinculo_ambiguo"
    )

    # Categoria explícita e não ambígua resolve normalmente.
    resposta_explicita = client.patch(
        f"/api/v1/lancamentos/{entry_id}",
        json={
            "categoria": "Espirometria",
            "status": "Recebido",
            "data_recebimento": "2026-07-20",
        },
        headers=auth("gestor"),
    )
    assert resposta_explicita.status_code == 200, resposta_explicita.text
    assert resposta_explicita.json()["categoria"] == "Espirometria"


def test_payload_409_nao_expoe_pii(client, auth, person):
    alvo = _atendimento_com_receita(client, auth, person, "exame")
    response = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": "espirometria",
            "valor": "220.00",
            alvo["link_field"]: alvo["link_id"],
        },
        headers=auth("gestor"),
    )
    _detalhe_409(response)
    texto = response.text
    assert "Pessoa Teste 001" not in texto
    assert "0000-9001" not in texto
    assert "nome_completo" not in texto
    assert "telefone" not in texto


def test_normalizacao_nao_abre_bypass_de_pii_na_categoria(client, auth):
    response = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": "CPF 123.\u200b456.789-00",
            "valor": "10.00",
        },
        headers=auth("gestor"),
    )
    assert response.status_code == 422, response.text
    numeric = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "categoria": 123, "valor": "10.00"},
        headers=auth("gestor"),
    )
    assert numeric.status_code == 422, numeric.text


def test_patch_continua_sem_permitir_valor_tipo_ou_vinculos(client, auth, person):
    alvo = _atendimento_com_receita(client, auth, person, "exame")
    existente = client.post(
        "/api/v1/lancamentos/busca",
        json={"q": alvo["existing_code"]},
        headers=auth("leitura"),
    ).json()["itens"][0]
    for payload in (
        {"valor": "999.00"},
        {"tipo": "despesa"},
        {"spirometry_exam_id": "outro-id"},
    ):
        response = client.patch(
            f"/api/v1/lancamentos/{existente['id']}",
            json=payload,
            headers=auth("gestor"),
        )
        assert response.status_code == 422, response.text


def _linha(
    codigo,
    *,
    tipo,
    categoria,
    exam_id=None,
    consultation_id=None,
):
    return FinancialEntry(
        public_code=codigo,
        tipo=tipo,
        categoria=categoria,
        valor=Decimal("10.00"),
        moeda="BRL",
        status="Pendente",
        data_competencia_dia_assumido=False,
        spirometry_exam_id=exam_id,
        consultation_id=consultation_id,
    )


@pytest.mark.parametrize(
    ("componente", "segunda_categoria"),
    [
        ("exame", " \tESPIROMETRIA\n"),
        ("exame", None),
        ("consulta", " consulta "),
        ("consulta", None),
    ],
)
def test_sqlite_indice_unico_bloqueia_variantes_e_categoria_ausente(
    db, componente, segunda_categoria
):
    pessoa = Person(
        public_code="PES-900001",
        nome_completo="Pessoa Sintetica",
        nome_normalizado="pessoa sintetica",
    )
    db.add(pessoa)
    db.flush()
    exam = SpirometryExam(public_code="ESP-900001", person_id=pessoa.id)
    consultation = Consultation(public_code="CON-900001", person_id=pessoa.id)
    db.add_all([exam, consultation])
    db.flush()
    if componente == "exame":
        kwargs = {"exam_id": exam.id}
        categoria = "Espirometria"
    else:
        kwargs = {"consultation_id": consultation.id}
        categoria = "Consulta"
    db.add(_linha("LAN-900001", tipo="receita", categoria=categoria, **kwargs))
    db.commit()

    db.add(
        _linha(
            "LAN-900002",
            tipo="receita",
            categoria=segunda_categoria,
            **kwargs,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_sqlite_indice_exclui_outro_despesa_e_repasse(db):
    pessoa = Person(
        public_code="PES-900002",
        nome_completo="Pessoa Sintetica Dois",
        nome_normalizado="pessoa sintetica dois",
    )
    db.add(pessoa)
    db.flush()
    exam = SpirometryExam(public_code="ESP-900002", person_id=pessoa.id)
    db.add(exam)
    db.flush()
    db.add_all(
        [
            _linha(
                "LAN-900010",
                tipo="receita",
                categoria="Espirometria",
                exam_id=exam.id,
            ),
            _linha(
                "LAN-900011",
                tipo="receita",
                categoria="Outro",
                exam_id=exam.id,
            ),
            _linha(
                "LAN-900012",
                tipo="despesa",
                categoria="Espirometria",
                exam_id=exam.id,
            ),
            _linha(
                "LAN-900013",
                tipo="repasse",
                categoria="Espirometria",
                exam_id=exam.id,
            ),
        ]
    )
    db.commit()
