"""Correção telefone compartilhado entre pacientes (2026-11-21).

Problema real em produção: duas crianças diferentes usam o mesmo WhatsApp da
mãe. Ao cadastrar a segunda, o sistema bloqueava por "possível duplicado"
porque o telefone já pertencia à primeira. Telefone NÃO é identificador de
paciente.

Regra nova:

* telefone igual SOZINHO nunca bloqueia — vira aviso informativo;
* CPF idêntico continua sendo prova forte (bloqueio próprio, intocado);
* nome + nascimento fortemente coincidentes continuam exigindo decisão humana;
* nome igual com nascimento DIFERENTE prova pessoas distintas e não bloqueia.

Só dados sintéticos; banco isolado por teste. Prefixo 0000 não é discável.
"""

from __future__ import annotations

API = "/api/v1"
EEG = "/api/v1/eeg"

# Telefones sintéticos (não discáveis) e CPFs válidos nos verificadores, sem dono.
FONE_MAE = "(21) 0000-7701"
FONE_MAE_2 = "(21) 0000-7702"
CPF_A = "529.982.247-25"
CPF_B = "111.444.777-35"


def _espirometria(**campos):
    bloco = {"data_exame": "12/08/2026", "status": "Realizado"}
    bloco.update(campos)
    return bloco


def _novo_paciente(nome, telefone=None, nascimento=None, cpf=None, confirmar=False):
    pessoa = {"nome_completo": nome, "contatos": []}
    if telefone:
        pessoa["contatos"].append(
            {"tipo": "whatsapp", "valor": telefone, "principal": True}
        )
    if nascimento:
        pessoa["data_nascimento"] = nascimento
    if cpf:
        pessoa["cpf"] = cpf
    corpo = {
        "pessoa": pessoa,
        "tipo": "espirometria_soprolife",
        "espirometria": _espirometria(),
    }
    if confirmar:
        corpo["confirmar_duplicado"] = True
    return corpo


def _criar_pessoa(client, auth, nome, telefone=None, nascimento=None):
    pessoa = {"nome_completo": nome, "contatos": []}
    if telefone:
        pessoa["contatos"].append(
            {"tipo": "whatsapp", "valor": telefone, "principal": True}
        )
    if nascimento:
        pessoa["data_nascimento"] = nascimento
    r = client.post(f"{API}/pessoas", json=pessoa, headers=auth("operacional"))
    assert r.status_code == 201, r.text
    return r.json()


# ══════════════ Novo Atendimento (fluxo atômico pessoa + exame) ══════════════


def test_dois_irmaos_mesmo_telefone_ambos_salvam(client, auth):
    """Caso real: irmãos no WhatsApp da mãe. Ambos nascem, sem confirmação."""
    r1 = client.post(
        f"{API}/atendimentos/novo-paciente",
        json=_novo_paciente("Crianca A Sintetica", FONE_MAE, "2018-03-10"),
        headers=auth("operacional"),
    )
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        f"{API}/atendimentos/novo-paciente",
        json=_novo_paciente("Crianca B Sintetica", FONE_MAE, "2020-07-22"),
        headers=auth("operacional"),
    )
    assert r2.status_code == 201, "telefone igual sozinho não pode bloquear"


def test_telefone_igual_cpf_diferente_permitido(client, auth):
    assert (
        client.post(
            f"{API}/atendimentos/novo-paciente",
            json=_novo_paciente("Irmao Um Sintetico", FONE_MAE_2, "2017-01-01", CPF_A),
            headers=auth("operacional"),
        ).status_code
        == 201
    )
    r = client.post(
        f"{API}/atendimentos/novo-paciente",
        json=_novo_paciente("Irmao Dois Sintetico", FONE_MAE_2, "2019-02-02", CPF_B),
        headers=auth("operacional"),
    )
    assert r.status_code == 201, "telefone igual + CPF diferente não pode bloquear"


def test_mesmo_nome_nascimento_diferente_nao_bloqueia(client, auth):
    assert (
        client.post(
            f"{API}/atendimentos/novo-paciente",
            json=_novo_paciente("Xara Sintetica Dupla", nascimento="2010-01-01"),
            headers=auth("operacional"),
        ).status_code
        == 201
    )
    r = client.post(
        f"{API}/atendimentos/novo-paciente",
        json=_novo_paciente("Xara Sintetica Dupla", nascimento="2012-02-02"),
        headers=auth("operacional"),
    )
    assert r.status_code == 201, "nascimento diferente prova pessoas distintas"


def test_mesmo_nome_e_nascimento_continua_bloqueando(client, auth):
    corpo = _novo_paciente("Homonimo Sintetico Forte", nascimento="2015-05-05")
    assert (
        client.post(
            f"{API}/atendimentos/novo-paciente", json=corpo, headers=auth("operacional")
        ).status_code
        == 201
    )
    rep = client.post(
        f"{API}/atendimentos/novo-paciente", json=corpo, headers=auth("operacional")
    )
    assert rep.status_code == 409
    assert rep.json()["erro"]["mensagem"]["codigo"] == "possivel_duplicado"
    # Com confirmação humana, o segundo nasce.
    corpo["confirmar_duplicado"] = True
    assert (
        client.post(
            f"{API}/atendimentos/novo-paciente", json=corpo, headers=auth("operacional")
        ).status_code
        == 201
    )


def test_cpf_igual_continua_sendo_duplicidade_forte(client, auth):
    assert (
        client.post(
            f"{API}/atendimentos/novo-paciente",
            json=_novo_paciente("Pessoa CPF Sintetica", nascimento="1990-01-01", cpf=CPF_A),
            headers=auth("operacional"),
        ).status_code
        == 201
    )
    rep = client.post(
        f"{API}/atendimentos/novo-paciente",
        json=_novo_paciente("Nome Totalmente Diferente Sintetico", cpf=CPF_A),
        headers=auth("operacional"),
    )
    assert rep.status_code == 409
    assert rep.json()["erro"]["mensagem"]["codigo"] == "cpf_ja_cadastrado"


# ══════════════ Pré-checagem /pessoas/verificar-duplicados ══════════════


def test_verificar_duplicados_telefone_e_aviso_nao_bloqueio(client, auth):
    _criar_pessoa(client, auth, "Mae Sintetica Verif", telefone=FONE_MAE, nascimento="1985-01-01")
    r = client.post(
        f"{API}/pessoas/verificar-duplicados",
        json={
            "nome_completo": "Filho Diferente Verif",
            "telefones": [FONE_MAE],
            "data_nascimento": "2019-09-09",
        },
        headers=auth("operacional"),
    )
    body = r.json()
    assert body["total"] == 1
    assert body["bloqueia"] is False
    cand = body["candidatos"][0]
    assert cand["forte"] is False
    assert cand["motivo"] == "telefone_igual"
    assert cand["aviso"], "a tela precisa do texto informativo do telefone compartilhado"


def test_verificar_duplicados_nome_e_nascimento_bloqueia(client, auth):
    _criar_pessoa(client, auth, "Alvo Sintetico Verif", nascimento="2000-01-01")
    r = client.post(
        f"{API}/pessoas/verificar-duplicados",
        json={
            "nome_completo": "Alvo Sintetico Verif",
            "telefones": [],
            "data_nascimento": "2000-01-01",
        },
        headers=auth("operacional"),
    )
    body = r.json()
    assert body["bloqueia"] is True
    assert body["candidatos"][0]["forte"] is True


# ══════════════ Busca por telefone retorna múltiplas pessoas ══════════════


def test_busca_por_telefone_retorna_ambos(client, auth):
    _criar_pessoa(client, auth, "Busca Irmao Um", telefone=FONE_MAE)
    _criar_pessoa(client, auth, "Busca Irmao Dois", telefone=FONE_MAE)
    r = client.post(
        f"{API}/pessoas/busca", json={"q": FONE_MAE}, headers=auth("leitura")
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2, "telefone → pode mapear mais de um paciente"


def test_mesmo_telefone_nao_cria_erro_de_unicidade(client, auth):
    _criar_pessoa(client, auth, "Unicidade Irmao Um", telefone=FONE_MAE)
    # segundo cadastro com o mesmo telefone não pode falhar por unicidade
    _criar_pessoa(client, auth, "Unicidade Irmao Dois", telefone=FONE_MAE)
    total = client.get("/api/v1/pessoas", headers=auth("leitura")).json()["total"]
    assert total == 2


# ══════════════ Funil (reusa a mesma função de matching) ══════════════


def _contato_funil(client, auth, nome, telefone=None, nascimento=None):
    pessoa = {"nome_completo": nome, "contatos": []}
    if telefone:
        pessoa["contatos"].append(
            {"tipo": "whatsapp", "valor": telefone, "principal": True}
        )
    if nascimento:
        pessoa["data_nascimento"] = nascimento
    import uuid

    return client.post(
        f"{API}/funil/contatos",
        json={
            "idempotency_key": uuid.uuid4().hex,
            "origem_aquisicao": "google_maps",
            "canal_entrada": "whatsapp",
            "unidade_pretendida": "barra",
            "pessoa": pessoa,
        },
        headers=auth("operacional"),
    )


def test_funil_telefone_compartilhado_nao_bloqueia(client, auth):
    assert _contato_funil(
        client, auth, "Funil Mae Sintetica", FONE_MAE, "1986-06-06"
    ).status_code == 201
    r = _contato_funil(client, auth, "Funil Filho Sintetico", FONE_MAE, "2015-05-05")
    assert r.status_code == 201, "funil não pode bloquear por telefone compartilhado"


# ══════════════ EEGLife (fluxo e endpoints próprios, mesma regra) ══════════════


def test_eeg_telefone_compartilhado_nao_bloqueia(client, auth):
    r1 = client.post(
        f"{EEG}/pessoas",
        json={
            "nome_completo": "Eeg Irmao Um Sintetico",
            "telefone": FONE_MAE,
            "data_nascimento": "2016-01-01",
        },
        headers=auth(),
    )
    assert r1.status_code == 201, r1.text
    ver = client.post(
        f"{EEG}/pessoas/verificar",
        json={
            "nome_completo": "Eeg Irmao Dois Sintetico",
            "telefone": FONE_MAE,
            "data_nascimento": "2018-02-02",
        },
        headers=auth(),
    )
    assert ver.json()["bloqueia"] is False
    # cadastro da segunda pessoa sem confirmar_nova_pessoa: não pode bloquear
    r2 = client.post(
        f"{EEG}/pessoas",
        json={
            "nome_completo": "Eeg Irmao Dois Sintetico",
            "telefone": FONE_MAE,
            "data_nascimento": "2018-02-02",
        },
        headers=auth(),
    )
    assert r2.status_code == 201, "EEG não pode bloquear por telefone compartilhado"


def test_eeg_mesmo_nome_e_nascimento_continua_exigindo_confirmacao(client, auth):
    base = {
        "nome_completo": "Eeg Homonimo Forte Sintetico",
        "data_nascimento": "2001-03-03",
    }
    assert client.post(f"{EEG}/pessoas", json=base, headers=auth()).status_code == 201
    ver = client.post(f"{EEG}/pessoas/verificar", json=base, headers=auth())
    assert ver.json()["bloqueia"] is True
    assert client.post(f"{EEG}/pessoas", json=base, headers=auth()).status_code == 409
    # Com confirmação explícita, nasce.
    assert (
        client.post(
            f"{EEG}/pessoas",
            json={**base, "confirmar_nova_pessoa": True},
            headers=auth(),
        ).status_code
        == 201
    )
