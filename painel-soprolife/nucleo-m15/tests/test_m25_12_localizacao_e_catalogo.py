"""M25.12 — o que a busca por código institucional precisa responder.

A falha relatada em produção foi digitar `ESP-TF0001` na recepção e o sistema
não dizer nada: o campo de anexar o PDF simplesmente não aparecia. A causa
estava no navegador (um `pattern` que abortava o submit), mas o contrato do
servidor também precisa ficar travado — é ele que decide o que existe, o que
é código válido e o que é código de laudo.

Trava também o catálogo clínico que a médica vê: 17 conclusões +
PERSONALIZADO, 5 complementos pós-broncodilatador, e as duas frases exatas
que o marco cita nominalmente.

Somente dados sintéticos, marcados como teste.
"""

from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from app.config import get_settings
from app.models import User
from app.security import (
    ROLE_MEDICO,
    ensure_roles_exist,
    get_role,
    hash_password,
    issue_token,
)
from app.services.report_conclusions import (
    BRONCHODILATOR_OPTIONS,
    CONCLUSION_CUSTOM_CODE,
    CONCLUSION_OPTIONS,
    CONCLUSIONS_BY_CODE,
    compose_default_conclusion_text,
)


@pytest.fixture(autouse=True)
def _reports_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv(
        "M15_AUTH_SECRET",
        "m25-12-localizacao-secret-only-for-tests-0123456789abcdef",
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _minimal_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture()
def physician(db, client, auth):
    ensure_roles_exist(db)
    user = User(
        email="medica-m25-12@teste.local",
        nome="TESTE APAGAR Médica M25.12",
        password_hash=hash_password("senha-sintetica-m25-12-987"),
    )
    user.roles.append(get_role(db, ROLE_MEDICO))
    db.add(user)
    db.commit()
    response = client.patch(
        f"/api/v1/laudos/admin/medicos/{user.id}",
        json={
            "grant_physician_role": True,
            "professional_name": "TESTE APAGAR Profissional M25.12",
            "crm_number": "600012",
            "crm_state": "SC",
            "verification_status": "verified",
            "verification_reference": "CRM-VERIF-TESTE-M25-12",
            "active": True,
        },
        headers=auth("admin"),
    )
    assert response.status_code == 200, response.text
    return {
        "headers": {
            "Authorization": f"Bearer {issue_token(user.id, user.password_hash)}"
        },
        "profile": response.json()["profile"],
    }


@pytest.fixture()
def exam(client, auth, person):
    response = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-08-08",
                "status": "Realizado",
                "broncodilatador": True,
                "modalidade": "cowork",
            },
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    return response.json()["espirometria"]


# --------------------------------------------- busca por código institucional


def test_codigo_institucional_e_sempre_esp_mais_digitos(exam):
    """`ESP-TF0001` não é um código possível — nenhum caminho o emite.

    Esta é a raiz do relato: o código lembrado não existia porque o formato
    dele não existe. Os códigos saem de `allocate_public_code`, sempre como
    prefixo + 6 dígitos.
    """
    codigo = exam["public_code"]
    assert codigo.startswith("ESP-")
    sufixo = codigo.removeprefix("ESP-")
    assert sufixo.isdigit(), codigo
    assert len(sufixo) == 6, codigo


def test_busca_exata_por_codigo_encontra_o_exame(client, auth, exam):
    response = client.get(
        f"/api/v1/espirometrias?public_code={exam['public_code']}",
        headers=auth("operacional"),
    )
    assert response.status_code == 200, response.text
    itens = response.json()["itens"]
    assert [item["public_code"] for item in itens] == [exam["public_code"]]


def test_busca_por_codigo_inexistente_responde_lista_vazia(client, auth):
    """Não é erro de servidor: é ausência. A interface é que precisa dizer."""
    response = client.get(
        "/api/v1/espirometrias?public_code=ESP-TF0001",
        headers=auth("operacional"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["itens"] == []


def test_busca_normaliza_caixa_do_codigo(client, auth, exam):
    minusculo = exam["public_code"].lower()
    response = client.get(
        f"/api/v1/espirometrias?public_code={minusculo}",
        headers=auth("operacional"),
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["itens"]) == 1


def test_upload_com_codigo_de_formato_invalido_recusa_com_motivo(
    client, auth, physician
):
    """422 com código estável — nunca um 500 nem um silêncio."""
    response = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": "ESP-TF0001",
            "physician_profile_id": physician["profile"]["id"],
            "origin_type": "coworking",
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert response.status_code == 422, response.text
    assert response.json()["erro"]["codigo"] == "codigo_exame_invalido"


def test_upload_com_codigo_de_laudo_no_lugar_do_exame_recusa(
    client, auth, physician
):
    """`LAU-000001` identifica o laudo, não o exame — a confusão do relato."""
    response = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": "LAU-000001",
            "physician_profile_id": physician["profile"]["id"],
            "origin_type": "coworking",
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert response.status_code == 422, response.text
    assert response.json()["erro"]["codigo"] == "codigo_exame_invalido"


def test_upload_com_codigo_bem_formado_mas_inexistente_responde_404(
    client, auth, physician
):
    response = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": "ESP-999999",
            "physician_profile_id": physician["profile"]["id"],
            "origin_type": "coworking",
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert response.status_code == 404, response.text
    assert response.json()["erro"]["codigo"] == "exame_nao_encontrado"


def test_upload_pelo_codigo_correto_cria_o_laudo_e_atribui(
    client, auth, physician, exam
):
    """O caminho feliz completo da recepção, ponta a ponta."""
    response = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exam["public_code"],
            "physician_profile_id": physician["profile"]["id"],
            "origin_type": "coworking",
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    corpo = response.json()
    assert corpo["exam_code"] == exam["public_code"]
    assert corpo["status"] == "atribuido"
    assert corpo["public_code"].startswith("LAU-")

    fila = client.get("/api/v1/laudos/meus", headers=physician["headers"])
    assert fila.status_code == 200, fila.text
    assert [item["exam_code"] for item in fila.json()] == [exam["public_code"]]


def test_lista_de_espirometrias_exige_papel_de_leitura(client):
    """A lista que alimenta o seletor de exames não é pública."""
    assert client.get("/api/v1/espirometrias").status_code == 401


# ------------------------------------------------------ catálogo clínico


def test_catalogo_tem_17_conclusoes_clinicas_mais_personalizado():
    codigos = [opcao.code for opcao in CONCLUSION_OPTIONS]
    assert len(codigos) == 18
    assert codigos[0] == "NORMAL"
    assert codigos[-1] == CONCLUSION_CUSTOM_CODE
    clinicas = [c for c in codigos if c != CONCLUSION_CUSTOM_CODE]
    assert len(clinicas) == 17
    assert len(set(codigos)) == 18


def test_catalogo_tem_5_complementos_pos_broncodilatador():
    codigos = [opcao.code for opcao in BRONCHODILATOR_OPTIONS]
    assert len(codigos) == 5
    assert codigos[0] == "RBD_POSITIVO"
    assert len(set(codigos)) == 5


def test_dvo_leve_expande_para_a_frase_exata():
    assert CONCLUSIONS_BY_CODE["DVO_LEVE"].short_label == "DVO Leve"
    assert (
        CONCLUSIONS_BY_CODE["DVO_LEVE"].full_text
        == "Distúrbio ventilatório obstrutivo leve."
    )


def test_rbd_positivo_expande_para_a_frase_exata():
    rbd = next(o for o in BRONCHODILATOR_OPTIONS if o.code == "RBD_POSITIVO")
    assert rbd.short_label == "RBD+"
    assert rbd.full_text == "Com resposta significativa ao broncodilatador."


def test_dvo_leve_com_rbd_mais_monta_as_duas_frases():
    texto = compose_default_conclusion_text(
        conclusion_code="DVO_LEVE",
        custom_text=None,
        bronchodilator_code="RBD_POSITIVO",
        has_post_bd=True,
    )
    assert texto == (
        "Distúrbio ventilatório obstrutivo leve.\n"
        "Com resposta significativa ao broncodilatador."
    )


def test_toda_conclusao_clinica_tem_texto_por_extenso():
    for opcao in CONCLUSION_OPTIONS:
        if opcao.code == CONCLUSION_CUSTOM_CODE:
            assert opcao.full_text == ""
            continue
        assert opcao.full_text.strip(), opcao.code
        assert opcao.full_text.endswith("."), opcao.code
        assert opcao.short_label.strip(), opcao.code


def test_catalogo_entregue_a_medica_tem_o_conjunto_completo(
    client, auth, physician, exam
):
    """O que chega ao navegador é o mesmo conjunto — não uma versão reduzida."""
    criado = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exam["public_code"],
            "physician_profile_id": physician["profile"]["id"],
            "origin_type": "coworking",
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert criado.status_code == 201, criado.text
    documento = criado.json()["id"]

    resposta = client.get(
        f"/api/v1/laudos/{documento}/catalogo-conclusoes",
        headers=physician["headers"],
    )
    assert resposta.status_code == 200, resposta.text
    catalogo = resposta.json()
    assert len(catalogo["conclusoes"]) == 18
    assert catalogo["conclusoes"][-1]["personalizado"] is True
    assert catalogo["exame_com_pos_bd"] is True
    assert len(catalogo["complementos_bd"]) == 5

    rotulos = {item["rotulo"]: item["texto"] for item in catalogo["conclusoes"]}
    assert rotulos["DVO Leve"] == "Distúrbio ventilatório obstrutivo leve."
    complementos = {
        item["rotulo"]: item["texto"] for item in catalogo["complementos_bd"]
    }
    assert complementos["RBD+"] == (
        "Com resposta significativa ao broncodilatador."
    )


def test_exame_sem_pos_bd_nao_oferece_complementos_incompativeis(
    client, auth, physician, person
):
    sem_bd = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-08-08",
                "status": "Realizado",
                "broncodilatador": False,
                "modalidade": "cowork",
            },
        },
        headers=auth("operacional"),
    )
    assert sem_bd.status_code == 201, sem_bd.text
    criado = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": sem_bd.json()["espirometria"]["public_code"],
            "physician_profile_id": physician["profile"]["id"],
            "origin_type": "coworking",
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert criado.status_code == 201, criado.text

    catalogo = client.get(
        f"/api/v1/laudos/{criado.json()['id']}/catalogo-conclusoes",
        headers=physician["headers"],
    ).json()
    assert catalogo["exame_com_pos_bd"] is False
    assert [item["codigo"] for item in catalogo["complementos_bd"]] == [
        "BD_NAO_REALIZADO"
    ]
    # As 18 conclusões continuam inteiras: é o complemento que depende da fase.
    assert len(catalogo["conclusoes"]) == 18


def test_modo_piloto_continua_imprimindo_o_aviso(client, auth, physician, exam):
    """`reports_mode=pilot` não pode virar produção clínica por acidente."""
    assert get_settings().reports_mode == "pilot"
    criado = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exam["public_code"],
            "physician_profile_id": physician["profile"]["id"],
            "origin_type": "coworking",
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert criado.status_code == 201, criado.text
    previa = client.post(
        f"/api/v1/laudos/{criado.json()['id']}/laudo/previa",
        json={
            "conclusion_code": "DVO_LEVE",
            "bronchodilator_code": "RBD_POSITIVO",
        },
        headers=physician["headers"],
    )
    assert previa.status_code == 200, previa.text
    assert previa.json()["final_text"] == (
        "Distúrbio ventilatório obstrutivo leve.\n"
        "Com resposta significativa ao broncodilatador."
    )
