"""Hotfix — DDI explícito: telefone internacional não recebe regra brasileira.

A regressão que este arquivo existe para travar PARA SEMPRE: uma paciente com
telefone do Canadá (+1 647 238-6290) foi cadastrada e o painel reinterpretou o
número como brasileiro. Na tela virava "(16) 47238-6290"; no servidor,
`normalize_phone` presumia "55" e produzia "5516472386290" — a chave de busca,
de duplicidade e de `wa.me` apontando para um número que não existe em
nenhum dos dois países.

O "+" (e o "00" do discado internacional) passa a ser a declaração explícita
de que o DDI já está no valor. Sem ele, a regra brasileira continua idêntica
à de antes: nenhum registro legado muda de chave.

Nenhum telefone real de paciente aparece aqui. O número canadense do caso é
usado SÓ em função pura (`normalize_phone`), sobre string, sem banco e sem
gerar URL. Os registros gravados usam a faixa fictícia 555-01xx do NANP, que
é reservada e não discável — o mesmo princípio do prefixo 0000 do `conftest`.
"""

from app.normalize import normalize_phone
from app.services.followup import build_whatsapp_url
from app.services.patient_results import whatsapp_url

# NANP 555-0100..0199 é faixa reservada para ficção: não discável.
FICTICIO_NANP = "+1 555 555-0100"
FICTICIO_NANP_NORM = "15555550100"


# ------------------------------------------------- a regressão real, pura

def test_canada_nao_recebe_regra_brasileira():
    """O caso que motivou o hotfix, nas formas em que ele chega ao servidor."""

    for entrada in ("+1 647 238-6290", "+16472386290", "+1 (647) 238-6290"):
        normalizado = normalize_phone(entrada)
        assert normalizado == "16472386290", entrada
        # as três formas erradas que o painel produzia ou poderia produzir
        assert normalizado != "5516472386290", entrada
        assert not normalizado.startswith("55"), entrada
        assert normalizado != "1647238629055", entrada


def test_canada_sobrevive_ao_wa_me():
    """O número discável do `wa.me` é o DDI + número, sem "+" nem separador."""

    normalizado = normalize_phone("+1 647 238-6290")
    assert whatsapp_url(normalizado, "oi") == "https://wa.me/16472386290?text=oi"
    assert build_whatsapp_url(normalizado, "oi") == "https://wa.me/16472386290?text=oi"


# --------------------------------------------------- DDI explícito em geral

def test_ddi_explicito_nunca_ganha_55():
    assert normalize_phone("+351912345678") == "351912345678"
    assert normalize_phone("+34 912 345 678") == "34912345678"
    assert normalize_phone("+1 212 555-0100") == "12125550100"
    # o 00 do discado internacional vale como DDI explícito
    assert normalize_phone("0016472386290") == "16472386290"
    assert normalize_phone("00351912345678") == "351912345678"


def test_ddi_explicito_de_brasil_continua_brasil():
    """+55 explícito não duplica o 55 nem perde dígito."""

    assert normalize_phone("+5521999999999") == "5521999999999"
    assert normalize_phone("+55 (21) 99999-9999") == "5521999999999"
    assert normalize_phone("+55 21 99999-9999") == "5521999999999"


# ------------------------------------------------- compatibilidade legada

def test_brasileiro_sem_ddi_mantem_a_regra_de_antes():
    """Dez ou onze dígitos sem "+" seguem sendo lidos como brasileiros.

    Esta é a prova de que a correção NÃO migrou nada: toda chave já gravada
    para telefone brasileiro legado continua sendo calculada igual.
    """

    assert normalize_phone("21999999999") == "5521999999999"
    assert normalize_phone("(21) 99999-9999") == "5521999999999"
    assert normalize_phone("21 99999-9999") == "5521999999999"
    assert normalize_phone("2199999999") == "552199999999"
    # já com 55 na frente, não duplica
    assert normalize_phone("5521999999999") == "5521999999999"
    # fora da faixa 10–11 dígitos nada é presumido, como antes
    assert normalize_phone("999999999") == "999999999"
    assert normalize_phone("(21) 0000-9001") == "552100009001"


def test_telefone_ausente_ou_sem_digito():
    assert normalize_phone(None) == ""
    assert normalize_phone("") == ""
    assert normalize_phone("   ") == ""
    assert normalize_phone("sem número") == ""
    assert normalize_phone("+") == ""


# ------------------------------------------- cadastro e edição pela API

def test_cadastro_internacional_preserva_o_valor_e_a_chave(client, auth, db):
    """O que o operador digitou é o que fica em `valor`; a chave é E.164."""

    from sqlalchemy import select

    from app.models import PersonContact

    resp = client.post(
        "/api/v1/pessoas",
        json={
            "nome_completo": "Paciente Sintetico Nanp",
            "contatos": [
                {"tipo": "whatsapp", "valor": FICTICIO_NANP, "principal": True}
            ],
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    person_id = resp.json()["id"]

    contato = db.execute(
        select(PersonContact).where(PersonContact.person_id == person_id)
    ).scalars().one()
    assert contato.valor == FICTICIO_NANP
    assert contato.valor_normalizado == FICTICIO_NANP_NORM
    assert not contato.valor_normalizado.startswith("55")


def test_edicao_aceita_ddi_internacional(client, auth, person, db):
    """O PATCH de cadastro aceita DDI estrangeiro — era aqui que travava.

    A validação pedia dez dígitos "com DDD" para TODO telefone. Para um
    número com DDI explícito o painel não conhece o plano de numeração do
    país e não inventa um: exige apenas que haja número depois do código.
    """

    from sqlalchemy import select

    from app.models import PersonContact

    resp = client.patch(
        f"/api/v1/pessoas/{person['id']}/cadastro",
        json={"telefone": FICTICIO_NANP},
        headers=auth("admin"),
    )
    assert resp.status_code == 200, resp.text

    contato = db.execute(
        select(PersonContact).where(
            PersonContact.person_id == person["id"],
            PersonContact.ativo.is_(True),
            PersonContact.tipo.in_(("whatsapp", "telefone")),
        )
    ).scalars().one()
    assert contato.valor == FICTICIO_NANP
    assert contato.valor_normalizado == FICTICIO_NANP_NORM


def test_edicao_brasileira_continua_exigindo_ddd(client, auth, person):
    """Sem DDI explícito, "99999-9999" continua sendo erro de DDD faltando."""

    resp = client.patch(
        f"/api/v1/pessoas/{person['id']}/cadastro",
        json={"telefone": "99999-9999"},
        headers=auth("admin"),
    )
    assert resp.status_code == 422, resp.text
    detalhe = resp.json()["erro"]["mensagem"]
    assert detalhe["codigo"] == "telefone_invalido"
    assert "DDD" in detalhe["mensagem"]


def test_edicao_recusa_ddi_sem_numero(client, auth, person):
    """"+1" sozinho não é telefone: DDI sem número continua recusado."""

    resp = client.patch(
        f"/api/v1/pessoas/{person['id']}/cadastro",
        json={"telefone": "+1 555"},
        headers=auth("admin"),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["erro"]["mensagem"]["codigo"] == "telefone_invalido"


def test_busca_por_telefone_internacional(client, auth):
    """Quem foi cadastrado em E.164 é encontrado pelo próprio número."""

    client.post(
        "/api/v1/pessoas",
        json={
            "nome_completo": "Paciente Sintetico Busca",
            "contatos": [
                {"tipo": "whatsapp", "valor": FICTICIO_NANP, "principal": True}
            ],
        },
        headers=auth("operacional"),
    )
    resp = client.post(
        "/api/v1/pessoas/busca",
        json={"q": FICTICIO_NANP},
        headers=auth("leitura"),
    )
    assert resp.status_code == 200, resp.text
    nomes = [p["nome_completo"] for p in resp.json()["itens"]]
    assert "Paciente Sintetico Busca" in nomes
