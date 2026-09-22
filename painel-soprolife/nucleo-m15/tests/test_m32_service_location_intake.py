"""M32 — expõe o município de atendimento estruturado (M31) na intake normal
de exame: criação (`POST /espirometrias`, `POST /atendimentos`), leitura e
edição (`PATCH /espirometrias/{id}`). Nenhuma chamada de rede, nenhum dado de
paciente real — só fixtures sintéticas, como o resto da suíte M15.
"""

from app.models import Partner, PartnerUnit

API = "/api/v1"

RIO_IBGE = "3304557"
NITEROI_IBGE = "3303302"


# --------------------------------------------------------- POST /espirometrias


def test_criar_exame_rio_persiste_e_retorna_codigo(client, auth, person):
    resp = client.post(
        f"{API}/espirometrias",
        json={
            "person_id": person["id"],
            "data_exame": "10/08/2026",
            "status": "Realizado",
            "municipio_atendimento_ibge": RIO_IBGE,
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["municipio_atendimento_ibge"] == RIO_IBGE


def test_criar_exame_niteroi_persiste_e_retorna_codigo(client, auth, person):
    resp = client.post(
        f"{API}/espirometrias",
        json={
            "person_id": person["id"],
            "data_exame": "10/08/2026",
            "status": "Realizado",
            "municipio_atendimento_ibge": NITEROI_IBGE,
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["municipio_atendimento_ibge"] == NITEROI_IBGE


def test_criar_exame_sem_municipio_fica_nulo_nunca_rio(client, auth, person):
    """Campo omitido nunca vira Rio por padrão — fica exatamente nulo."""
    resp = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "data_exame": "10/08/2026", "status": "Realizado"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["municipio_atendimento_ibge"] is None
    assert body["municipio_atendimento_ibge"] != RIO_IBGE


def test_exame_historico_nulo_continua_nulo_na_leitura(client, auth, person):
    """Registro 'histórico' (criado sem o campo, como os pré-M31) continua
    em branco na leitura — nunca preenchido/adivinhado depois."""
    created = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "data_exame": "05/01/2026", "status": "Aguardando"},
        headers=auth("operacional"),
    ).json()
    lido = client.get(f"{API}/espirometrias?public_code={created['public_code']}",
                       headers=auth("leitura")).json()
    assert lido["itens"][0]["municipio_atendimento_ibge"] is None


def test_local_atendimento_texto_livre_nao_alimenta_municipio(client, auth, person):
    """`local_atendimento` (texto livre, ex.: nome do coworking) nunca vira
    fonte do município estruturado — são campos independentes."""
    resp = client.post(
        f"{API}/espirometrias",
        json={
            "person_id": person["id"],
            "data_exame": "10/08/2026",
            "status": "Realizado",
            "modalidade": "cowork",
            "local_atendimento": "Coworking Rio de Janeiro Centro",
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["local_atendimento"] == "Coworking Rio de Janeiro Centro"
    assert body["municipio_atendimento_ibge"] is None


def test_codigo_com_seis_digitos_e_rejeitado(client, auth, person):
    resp = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "municipio_atendimento_ibge": "330455"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422


def test_codigo_com_oito_digitos_e_rejeitado(client, auth, person):
    resp = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "municipio_atendimento_ibge": "33045570"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422


def test_codigo_nao_numerico_e_rejeitado(client, auth, person):
    resp = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "municipio_atendimento_ibge": "RIODEJAN"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422


def test_codigo_nunca_e_aceito_como_inteiro(client, auth, person):
    """Enviar o valor como número JSON (não string) é rejeitado — o contrato
    é textual, nunca numérico (perderia zero à esquerda em outros IBGE)."""
    resp = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "municipio_atendimento_ibge": 3304557},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422


def test_codigo_sintaticamente_valido_mas_nao_suportado_e_aceito_na_intake(client, auth, person):
    """A M32 não duplica a lista de municípios suportados da M31: um código
    de 7 dígitos sintaticamente válido é aceito na intake normal, mesmo que
    não seja Rio/Niterói. Quem recusa (fail-closed) é o gate fiscal restrito
    (M31, services/nfse_national/service_location.py), nunca este contrato."""
    resp = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "municipio_atendimento_ibge": "9999999"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["municipio_atendimento_ibge"] == "9999999"


# ---------------------------------------------------- PATCH /espirometrias/{id}


def test_update_omitindo_campo_preserva_valor_existente(client, auth, person):
    created = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "municipio_atendimento_ibge": RIO_IBGE},
        headers=auth("operacional"),
    ).json()
    resp = client.patch(
        f"{API}/espirometrias/{created['id']}",
        json={"responsavel": "Outro técnico"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["municipio_atendimento_ibge"] == RIO_IBGE


def test_update_muda_de_rio_para_niteroi(client, auth, person):
    created = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "municipio_atendimento_ibge": RIO_IBGE},
        headers=auth("operacional"),
    ).json()
    resp = client.patch(
        f"{API}/espirometrias/{created['id']}",
        json={"municipio_atendimento_ibge": NITEROI_IBGE},
        headers=auth("operacional"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["municipio_atendimento_ibge"] == NITEROI_IBGE


def test_update_string_vazia_limpa_campo(client, auth, person):
    created = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "municipio_atendimento_ibge": RIO_IBGE},
        headers=auth("operacional"),
    ).json()
    resp = client.patch(
        f"{API}/espirometrias/{created['id']}",
        json={"municipio_atendimento_ibge": ""},
        headers=auth("operacional"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["municipio_atendimento_ibge"] is None


def test_update_codigo_malformado_e_rejeitado_preserva_valor_no_banco(client, auth, person):
    created = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "municipio_atendimento_ibge": RIO_IBGE},
        headers=auth("operacional"),
    ).json()
    resp = client.patch(
        f"{API}/espirometrias/{created['id']}",
        json={"municipio_atendimento_ibge": "abc"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    lido = client.get(f"{API}/espirometrias?public_code={created['public_code']}",
                       headers=auth("leitura")).json()
    assert lido["itens"][0]["municipio_atendimento_ibge"] == RIO_IBGE


# ------------------------------------------- GET /espirometrias/municipios-atendimento


def test_endpoint_municipios_retorna_rio_e_niteroi_sem_segredo(client, auth):
    resp = client.get(f"{API}/espirometrias/municipios-atendimento", headers=auth("leitura"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"codigo": RIO_IBGE, "rotulo": "Rio de Janeiro/RJ"} in body
    assert {"codigo": NITEROI_IBGE, "rotulo": "Niterói/RJ"} in body
    # Só código + rótulo — nenhuma outra chave (nada de config fiscal/PII).
    for item in body:
        assert set(item.keys()) == {"codigo", "rotulo"}


# ------------------------------------------------------------ POST /atendimentos


def test_atendimento_soprolife_combinado_aceita_municipio(client, auth, person):
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "10/08/2026",
                "status": "Realizado",
                "modalidade": "residencial",
                "municipio_atendimento_ibge": NITEROI_IBGE,
            },
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["espirometria"]["municipio_atendimento_ibge"] == NITEROI_IBGE


def test_atendimento_pastore_aceita_municipio_sem_alterar_fluxo_de_parceria(
    client, auth, person, db
):
    """Pastore/SPLIT continua intocado: parceiro/unidade canônicos, sem
    lançamento financeiro direto — o município só se soma a isso."""
    partner = Partner(public_code="CLI-M32PASTORE", nome="Pastore",
                       tipo="clinica", status="ativa", arquivado=False)
    db.add(partner)
    db.flush()
    unit = PartnerUnit(public_code="UNI-M32PASTORE", partner_id=partner.id,
                        nome="Pastore M32 Unidade", ativo=True)
    db.add(unit)
    db.commit()

    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_pastore",
            "espirometria": {
                "data_exame": "10/08/2026",
                "status": "Realizado",
                "partner_id": partner.id,
                "partner_unit_id": unit.id,
                "municipio_atendimento_ibge": RIO_IBGE,
            },
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    exame = resp.json()["espirometria"]
    assert exame["municipio_atendimento_ibge"] == RIO_IBGE
    assert exame["partner_id"] == partner.id
    assert exame["partner_unit_id"] == unit.id
    assert exame["modalidade"] == "clinica_parceira"
