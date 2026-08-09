"""M25.15 — o que precisa ser verdade para a operação real começar.

Três mudanças de comportamento entram aqui, e cada uma tem um jeito próprio
de dar errado em silêncio:

1. **Nome antes dos códigos.** As filas autenticadas passaram a identificar
   o paciente pelo nome. O risco não é o nome aparecer — é ele aparecer
   ONDE não deve. Por isso todo teste que afirma "o nome está aqui" tem um
   par que afirma "o nome não está ali", com atenção especial à rota de
   validação, que é a única superfície pensada para conferência externa.

2. **Seletor dinâmico de médicos.** A lista precisa vir do banco com
   critério de elegibilidade, e não de uma constante com a Dra. Ana. Um
   seletor hardcoded passaria despercebido enquanto houvesse uma médica só;
   quebraria no dia da segunda. Os casos 0/1/N são testados separadamente.

3. **CRM canônico.** `52623075` desenhado como `52.62307-5` sem que o valor
   gravado mude — e sem que uma UF de máscara desconhecida seja formatada
   por analogia.

Somente dados sintéticos, com nomes marcados como teste.
"""

from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog, PhysicianProfile, User
from app.security import (
    ROLE_MEDICO,
    ensure_roles_exist,
    get_role,
    hash_password,
    issue_token,
)
from app.services.crm_display import (
    format_crm_full,
    format_crm_number,
    format_physician_credentials,
)


@pytest.fixture(autouse=True)
def _reports_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv(
        "M15_AUTH_SECRET",
        "m25-15-operacao-real-secret-only-for-tests-0123456789abcd",
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


def _make_physician(
    db,
    client,
    auth,
    *,
    sufixo: str,
    nome: str,
    crm_number: str = "600015",
    crm_state: str = "SC",
    verification_status: str = "verified",
    active: bool = True,
    conta_ativa: bool = True,
    papel_medico: bool = True,
    especialidade: str | None = None,
    rqe: str | None = None,
):
    """Cria conta + perfil médico com elegibilidade controlada.

    Cada eixo (papel, conta ativa, perfil ativo, verificação) é um parâmetro
    próprio porque cada um sozinho já deve bastar para tirar o médico da
    lista. Testá-los juntos esconderia qual deles é que de fato filtra.
    """

    ensure_roles_exist(db)
    user = User(
        email=f"medica-m25-15-{sufixo}@teste.local",
        nome=f"TESTE APAGAR {nome}",
        password_hash=hash_password("senha-sintetica-m25-15-987"),
        ativo=conta_ativa,
    )
    if papel_medico:
        user.roles.append(get_role(db, ROLE_MEDICO))
    db.add(user)
    db.commit()
    payload = {
        "grant_physician_role": papel_medico,
        "professional_name": nome,
        "crm_number": crm_number,
        "crm_state": crm_state,
        "verification_status": verification_status,
        "active": active,
    }
    if verification_status == "verified":
        payload["verification_reference"] = f"CRM-VERIF-M25-15-{sufixo}"
    if especialidade:
        payload["especialidade"] = especialidade
    if rqe:
        payload["rqe"] = rqe
    response = client.patch(
        f"/api/v1/laudos/admin/medicos/{user.id}",
        json=payload,
        headers=auth("admin"),
    )
    assert response.status_code == 200, response.text
    return {
        "user": user,
        "headers": {
            "Authorization": (
                f"Bearer {issue_token(user.id, user.password_hash)}"
            )
        },
        "profile": response.json()["profile"],
    }


@pytest.fixture()
def medica(db, client, auth):
    return _make_physician(
        db,
        client,
        auth,
        sufixo="principal",
        nome="TESTE APAGAR Médica Principal",
        especialidade="Pneumologista",
        rqe="99999",
    )


def _criar_exame(client, auth, person_id, *, data_exame="2026-08-08"):
    response = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person_id,
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": data_exame,
                "status": "Realizado",
                "broncodilatador": True,
                "modalidade": "cowork",
            },
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    return response.json()["espirometria"]


def _criar_pessoa(client, auth, nome):
    response = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": nome},
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload(client, auth, *, exam_code, profile_id):
    return client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exam_code,
            "physician_profile_id": profile_id,
            "origin_type": "coworking",
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )


@pytest.fixture()
def caso(client, auth, person, medica):
    """Um exame com laudo atribuído — o cenário mínimo da operação real."""

    exame = _criar_exame(client, auth, person["id"])
    response = _upload(
        client,
        auth,
        exam_code=exame["public_code"],
        profile_id=medica["profile"]["id"],
    )
    assert response.status_code == 201, response.text
    return {"exame": exame, "laudo": response.json(), "pessoa": person}


# ------------------------------------------------ 1. nome antes dos códigos


def test_fila_da_medica_identifica_pelo_nome_e_preserva_os_codigos(
    client, medica, caso, person
):
    response = client.get("/api/v1/laudos/meus", headers=medica["headers"])
    assert response.status_code == 200, response.text
    linha = response.json()[0]
    assert linha["patient"]["full_name"] == person["nome_completo"]
    # Os códigos NÃO sumiram: continuam na linha, como rastreabilidade.
    assert linha["exam_code"] == caso["exame"]["public_code"]
    assert linha["report_code"] == caso["laudo"]["public_code"]
    assert linha["exam_date"] == caso["exame"]["data_exame"]


def test_acompanhamento_operacional_identifica_pelo_nome(
    client, auth, caso, person
):
    response = client.get("/api/v1/laudos", headers=auth("operacional"))
    assert response.status_code == 200, response.text
    linha = response.json()[0]
    assert linha["patient"]["full_name"] == person["nome_completo"]
    assert linha["report_code"] == caso["laudo"]["public_code"]
    # Unidade resolvida também na fila operacional: é o que separa homônimos
    # sem precisar abrir cada laudo.
    assert linha["location_name"]


def test_bloco_de_paciente_na_fila_e_fechado(client, medica, caso):
    """Só nome e código do cadastro.

    A fila é a superfície mais exposta do fluxo (fica aberta na tela o dia
    inteiro). Um bloco fechado impede que um campo sensível entre depois por
    conveniência — nascimento, telefone ou observação.
    """

    response = client.get("/api/v1/laudos/meus", headers=medica["headers"])
    assert set(response.json()[0]["patient"]) == {"full_name", "public_code"}


def test_documentos_para_download_dizem_de_quem_sao(client, medica, caso):
    """M25.14 continua entregando dois documentos separados — agora nomeados."""

    response = client.get(
        f"/api/v1/laudos/{caso['laudo']['id']}/documentos",
        headers=medica["headers"],
    )
    assert response.status_code == 200, response.text
    corpo = response.json()
    assert corpo["patient"]["full_name"] == caso["pessoa"]["nome_completo"]
    assert corpo["exam_code"] == caso["exame"]["public_code"]
    # Os dois documentos continuam separados, como na M25.14.
    assert corpo["tecnico_mir"] is not None
    assert "laudo_soprolife" in corpo


# -------------------------------------------------------- 2. privacidade


def test_rota_publica_de_validacao_nunca_devolve_nome_de_paciente(
    client, auth, db, medica, caso, person
):
    """A única superfície de conferência externa não pode ganhar identidade.

    Este é o teste que protege a mudança inteira: ao espalhar o nome pelas
    filas, o erro fácil seria incluí-lo também aqui "por simetria". Quem
    confere um laudo precisa saber que o documento é autêntico, não de quem
    ele é.
    """

    from datetime import datetime, timezone

    from app.models import ReportDocument

    # O estado "liberado" é protegido por CHECK: só existe com TODOS os
    # carimbos de liberação. Montá-lo por inteiro aqui é de propósito — um
    # atalho que burlasse a constraint testaria um estado que a produção
    # jamais alcança.
    agora = datetime.now(timezone.utc)
    documento = db.execute(
        select(ReportDocument).where(
            ReportDocument.public_code == caso["laudo"]["public_code"]
        )
    ).scalar_one()
    documento.status = "liberado"
    documento.clinical_started_at = agora
    documento.ready_for_signature_at = agora
    documento.released_at = agora
    documento.released_by_user_id = medica["user"].id
    documento.released_physician_profile_id = medica["profile"]["id"]
    documento.validation_code = "ABCDEFGH2345"
    documento.signature_status = "liberada_institucional"
    db.commit()

    response = client.get(
        "/api/v1/laudos/validacao/ABCDEFGH2345", headers=auth("operacional")
    )
    assert response.status_code == 200, response.text
    corpo = response.json()
    assert "patient" not in corpo
    assert person["nome_completo"] not in response.text
    assert person["public_code"] not in response.text
    # O que ela DEVE responder continua lá: identidade do médico e do laudo.
    assert corpo["physician_name"] == medica["profile"]["professional_name"]
    assert corpo["qualified_signature"] is False


def test_auditoria_da_atribuicao_nao_grava_nome_de_paciente(
    db, caso, person
):
    """A atribuição continua auditada — sem virar um índice de pacientes."""

    registros = db.execute(
        select(AuditLog).where(AuditLog.acao == "laudo_original_atribuido")
    ).scalars().all()
    assert len(registros) == 1
    detalhes = registros[0].detalhes
    assert detalhes["report_code"] == caso["laudo"]["public_code"]
    assert detalhes["exam_code"] == caso["exame"]["public_code"]
    # A prova de QUEM recebeu o laudo continua gravada.
    assert detalhes["physician_profile_id"]
    assert detalhes["assignment_id"]
    assert person["nome_completo"] not in str(detalhes)


def test_isolamento_medico_continua_valendo_com_nome_na_fila(
    db, client, auth, caso, medica, person
):
    """Outra médica não vê o laudo — nem o nome que agora viaja com ele."""

    outra = _make_physician(
        db,
        client,
        auth,
        sufixo="outra",
        nome="TESTE APAGAR Médica Sem Atribuicao",
        crm_number="600016",
    )
    fila = client.get("/api/v1/laudos/meus", headers=outra["headers"])
    assert fila.status_code == 200, fila.text
    assert fila.json() == []
    assert person["nome_completo"] not in fila.text

    detalhe = client.get(
        f"/api/v1/laudos/{caso['laudo']['id']}", headers=outra["headers"]
    )
    assert detalhe.status_code == 404
    assert person["nome_completo"] not in detalhe.text


# ----------------------------------------------------- 3. busca por nome


def test_busca_por_nome_encontra_o_exame(client, auth, caso, person):
    response = client.get(
        "/api/v1/laudos/exames",
        params={"q": person["nome_completo"][:10]},
        headers=auth("operacional"),
    )
    assert response.status_code == 200, response.text
    encontrados = [item["exam_code"] for item in response.json()]
    assert caso["exame"]["public_code"] in encontrados


def test_busca_por_codigo_de_exame_e_de_laudo_continua_funcionando(
    client, auth, caso
):
    """Busca por código não foi removida — ganhou companhia."""

    por_esp = client.get(
        "/api/v1/laudos/exames",
        params={"q": caso["exame"]["public_code"]},
        headers=auth("operacional"),
    )
    assert [item["exam_code"] for item in por_esp.json()] == [
        caso["exame"]["public_code"]
    ]
    por_lau = client.get(
        "/api/v1/laudos/exames",
        params={"q": caso["laudo"]["public_code"]},
        headers=auth("operacional"),
    )
    assert [item["report_code"] for item in por_lau.json()] == [
        caso["laudo"]["public_code"]
    ]


def test_homonimos_vem_com_o_que_os_diferencia(client, auth):
    """Mesmo nome, exames diferentes: data, unidade e ESP separam."""

    nome = "TESTE APAGAR Homonimo Silva"
    primeiro = _criar_pessoa(client, auth, nome)
    segundo = _criar_pessoa(client, auth, nome)
    exame_um = _criar_exame(
        client, auth, primeiro["id"], data_exame="2026-08-01"
    )
    exame_dois = _criar_exame(
        client, auth, segundo["id"], data_exame="2026-08-05"
    )

    response = client.get(
        "/api/v1/laudos/exames",
        params={"q": "Homonimo Silva"},
        headers=auth("operacional"),
    )
    assert response.status_code == 200, response.text
    itens = {item["exam_code"]: item for item in response.json()}
    assert {exame_um["public_code"], exame_dois["public_code"]} <= set(itens)
    # Os dois têm o mesmo nome; o que os distingue precisa vir preenchido.
    assert itens[exame_um["public_code"]]["exam_date"] == "2026-08-01"
    assert itens[exame_dois["public_code"]]["exam_date"] == "2026-08-05"
    assert itens[exame_um["public_code"]]["patient"]["public_code"] != (
        itens[exame_dois["public_code"]]["patient"]["public_code"]
    )


def test_busca_por_nome_curto_demais_e_recusada_com_motivo(client, auth):
    """Duas letras casariam com meia base — 422 explicado, nunca lista."""

    response = client.get(
        "/api/v1/laudos/exames",
        params={"q": "ab"},
        headers=auth("operacional"),
    )
    assert response.status_code == 422, response.text
    assert response.json()["erro"]["codigo"] == "termo_de_busca_curto"


def test_localizador_de_exames_exige_papel_operacional(client, auth, medica):
    """Buscar paciente por nome não pode ser acessível a qualquer sessão."""

    response = client.get(
        "/api/v1/laudos/exames",
        params={"q": "Pessoa"},
        headers=auth("leitura"),
    )
    assert response.status_code == 403, response.text


# ------------------------------------------- 4. seletor dinâmico de médicos


def test_medico_verificado_e_ativo_aparece(client, auth, medica):
    response = client.get(
        "/api/v1/laudos/medicos-disponiveis", headers=auth("operacional")
    )
    assert response.status_code == 200, response.text
    ids = [item["id"] for item in response.json()]
    assert medica["profile"]["id"] in ids


def test_dois_medicos_validos_aparecem_os_dois(db, client, auth, medica):
    """A lista NÃO é hardcoded para uma médica: cresce sozinha."""

    segunda = _make_physician(
        db,
        client,
        auth,
        sufixo="segunda",
        nome="TESTE APAGAR Médica Segunda",
        crm_number="600017",
    )
    response = client.get(
        "/api/v1/laudos/medicos-disponiveis", headers=auth("operacional")
    )
    ids = [item["id"] for item in response.json()]
    assert medica["profile"]["id"] in ids
    assert segunda["profile"]["id"] in ids
    assert len(ids) == 2


def _tornar_inelegivel(db, medico, eixo: str) -> None:
    """Degrada UM eixo de elegibilidade de um médico já cadastrado.

    A degradação é feita direto no banco de propósito: a API administrativa
    (corretamente) recusa CRIAR um perfil ativo sem verificação
    (`ativacao_medica_insegura`), então não existe caminho para nascer
    inelegível. O que a produção produz é o contrário — um perfil que era
    válido e foi suspenso, teve a verificação revogada ou perdeu a conta. É
    esse estado que o seletor precisa filtrar.
    """

    perfil = db.execute(
        select(PhysicianProfile).where(
            PhysicianProfile.id == medico["profile"]["id"]
        )
    ).scalar_one()
    if eixo == "perfil-inativo":
        perfil.active = False
    elif eixo == "verificacao-pendente":
        # Revogar a verificação apaga junto a EVIDÊNCIA dela: um CHECK
        # garante que só um perfil `verified` carrega data, verificador e
        # referência da consulta ao conselho. Deixar a evidência para trás
        # descreveria um perfil "não verificado, mas com prova de
        # verificação" — estado que o banco nem permite existir.
        perfil.verification_status = "pending"
        perfil.verified_at = None
        perfil.verified_by_user_id = None
        perfil.verification_reference = None
    elif eixo == "conta-inativa":
        db.get(User, perfil.user_id).ativo = False
    else:  # pragma: no cover - guarda de programação
        raise AssertionError(f"eixo desconhecido: {eixo}")
    db.commit()


@pytest.mark.parametrize(
    "eixo",
    ["perfil-inativo", "verificacao-pendente", "conta-inativa"],
)
def test_medico_nao_elegivel_nao_aparece(db, client, auth, medica, eixo):
    """Cada eixo de elegibilidade, sozinho, já tira o médico da lista."""

    excluido = _make_physician(
        db,
        client,
        auth,
        sufixo=eixo,
        nome=f"TESTE APAGAR Médica {eixo}",
        crm_number="600018",
    )
    _tornar_inelegivel(db, excluido, eixo)

    response = client.get(
        "/api/v1/laudos/medicos-disponiveis", headers=auth("operacional")
    )
    ids = [item["id"] for item in response.json()]
    assert excluido["profile"]["id"] not in ids
    # A médica elegível continua lá: o filtro é seletivo, não um apagão.
    assert medica["profile"]["id"] in ids


def test_atribuir_a_medico_nao_elegivel_falha_fechado(
    db, client, auth, person
):
    """Fail closed no SERVIDOR, não só no botão desabilitado da tela."""

    inelegivel = _make_physician(
        db,
        client,
        auth,
        sufixo="bloqueado",
        nome="TESTE APAGAR Médica Bloqueada",
        crm_number="600019",
    )
    _tornar_inelegivel(db, inelegivel, "verificacao-pendente")
    exame = _criar_exame(client, auth, person["id"])
    response = _upload(
        client,
        auth,
        exam_code=exame["public_code"],
        profile_id=inelegivel["profile"]["id"],
    )
    assert response.status_code == 409, response.text
    assert response.json()["erro"]["codigo"] == "medico_nao_elegivel"


def test_upload_continua_funcionando_e_cria_atribuicao(client, auth, caso):
    """A regressão mais cara possível: parar de receber PDF."""

    assert caso["laudo"]["status"] == "atribuido"
    assert caso["laudo"]["assignment"]["active"] is True
    assert caso["laudo"]["exam_code"] == caso["exame"]["public_code"]


def test_rotulo_do_medico_traz_credenciais_prontas_para_a_tela(
    client, auth, medica
):
    """O seletor não deve remontar CRM/RQE por conta própria no navegador."""

    response = client.get(
        "/api/v1/laudos/medicos-disponiveis", headers=auth("operacional")
    )
    perfil = response.json()[0]
    assert perfil["credentials_label"].startswith(
        medica["profile"]["professional_name"]
    )
    assert "Pneumologista" in perfil["credentials_label"]
    assert "RQE 99999" in perfil["credentials_label"]


# ---------------------------------------------------- 5. CRM canônico


def test_crm_rj_da_dra_ana_e_desenhado_com_ponto_e_hifen():
    """O caso exato da missão: 52623075 -> 52.62307-5."""

    assert format_crm_number("52623075", "RJ") == "52.62307-5"
    assert format_crm_full("52623075", "RJ") == "CRM-RJ 52.62307-5"
    # O `crm_display` gravado hoje (5262307-5) chega ao mesmo desenho: a
    # máscara sai dos DÍGITOS, não do texto já formatado.
    assert (
        format_crm_number("52623075", "RJ", crm_display="5262307-5")
        == "52.62307-5"
    )


@pytest.mark.parametrize(
    "numero,uf",
    [
        ("52623075", "SP"),   # mesma quantidade de dígitos, UF sem máscara
        ("123456", "RJ"),     # RJ, mas fora do formato de 8 dígitos
        ("526230751", "RJ"),  # nove dígitos: não é o padrão conferido
        ("1234567", "MG"),
    ],
)
def test_outros_crm_nao_sao_mascarados_por_analogia(numero, uf):
    """Sem prova do formato, o número sai como está — nunca "parecido"."""

    assert format_crm_number(numero, uf) == numero
    assert "." not in format_crm_number(numero, uf)


def test_crm_formatado_nao_altera_o_valor_persistido(db, client, auth):
    """Correção é de FACHADA: identidade e verificação seguem intactas."""

    ana = _make_physician(
        db,
        client,
        auth,
        sufixo="ana-crm",
        nome="TESTE APAGAR Médica CRM RJ",
        crm_number="52623075",
        crm_state="RJ",
        especialidade="Pneumologista",
        rqe="58224",
    )
    perfil = db.execute(
        select(PhysicianProfile).where(
            PhysicianProfile.id == ana["profile"]["id"]
        )
    ).scalar_one()
    assert perfil.crm_number == "52623075"
    assert perfil.verification_status == "verified"
    assert perfil.verification_reference == "CRM-VERIF-M25-15-ana-crm"

    response = client.get(
        "/api/v1/laudos/medicos-disponiveis", headers=auth("operacional")
    )
    devolvido = [
        item for item in response.json() if item["id"] == perfil.id
    ][0]
    # O número gravado continua sendo devolvido como está…
    assert devolvido["crm_number"] == "52623075"
    # …e a apresentação canônica vem em campo PRÓPRIO.
    assert devolvido["crm_formatted"] == "52.62307-5"
    assert devolvido["crm_full"] == "CRM-RJ 52.62307-5"
    assert devolvido["verification_status"] == "verified"


def test_rotulo_completo_da_dra_ana():
    assert format_physician_credentials(
        "Dra. Ana Cristina do Nascimento Cunha",
        crm_number="52623075",
        crm_state="RJ",
        crm_display="5262307-5",
        rqe="58224",
        especialidade="Pneumologista",
    ) == (
        "Dra. Ana Cristina do Nascimento Cunha • Pneumologista • "
        "CRM-RJ 52.62307-5 • RQE 58224"
    )


# ------------------------------- 6. prontidão do PDF (CFM 2.381/2024)


def _conteudo_de_conformidade(**overrides):
    """Monta um `NativeReportContent` mínimo para conferir a estrutura."""

    from datetime import date, datetime, timezone

    from app.services.report_native_pdf import (
        ExamBlock,
        LocationBlock,
        NativeReportContent,
        PatientBlock,
        PhysicianBlock,
    )

    base = {
        "document_code": "LAU-000001",
        "version_number": 1,
        "patient": PatientBlock(
            full_name="TESTE APAGAR Paciente",
            birth_date=date(1980, 1, 1),
            sex="feminino",
            public_code="PES-000001",
        ),
        "exam": ExamBlock(
            public_code="ESP-000001",
            exam_date=date(2026, 8, 8),
            exam_time="09:00",
            date_precision="dia",
            has_post_bd=True,
            clinical_indication="teste",
        ),
        "location": LocationBlock(
            name="Unidade Teste",
            address_line="Rua Sintética, 1 — Rio de Janeiro/RJ",
            contact_line="(21) 0000-0000",
        ),
        "physician": PhysicianBlock(
            professional_name="TESTE APAGAR Médica",
            specialty="Pneumologista",
            crm_display="52.62307-5",
            crm_state="RJ",
            rqe="58224",
        ),
        "conclusion_text": "conclusão sintética",
        "observations": None,
        "issued_at_local": datetime.now(timezone.utc),
        "released": True,
    }
    base.update(overrides)
    return NativeReportContent(**base)


def test_pdf_reune_os_campos_obrigatorios_que_o_sistema_possui():
    """Tudo o que o núcleo sabe guardar precisa estar impresso.

    A missão manda ADICIONAR campo obrigatório que exista no sistema e não
    esteja no papel. Este teste é a trava contra o inverso: um campo
    disponível deixar de ser impresso numa mudança futura de layout.
    """

    from app.services.report_compliance import avaliar_cfm_2381

    requisitos = {
        item.chave: item
        for item in avaliar_cfm_2381(_conteudo_de_conformidade())
    }
    for chave in (
        "identificacao_medico",
        "crm_uf",
        "rqe",
        "identificacao_paciente",
        "data_emissao",
        "data_realizacao_exame",
        "endereco_profissional",
        "contato_profissional",
    ):
        assert requisitos[chave].atendido, requisitos[chave].detalhe
    assert requisitos["crm_uf"].detalhe == "CRM-RJ 52.62307-5"


def test_cpf_e_pendencia_declarada_e_nunca_fabricada():
    """O cadastro não tem CPF; a conferência diz isso em vez de disfarçar."""

    from app.services import report_compliance as rc

    requisitos = {
        item.chave: item
        for item in rc.avaliar_cfm_2381(_conteudo_de_conformidade())
    }
    cpf = requisitos["cpf_paciente"]
    assert cpf.atendido is False
    assert cpf.bloqueia_entrega_oficial is True
    assert cpf.detalhe == rc.PENDENCIA_CPF


def test_unidade_sem_endereco_vira_pendencia_e_nao_endereco_inventado():
    from app.services.report_compliance import avaliar_cfm_2381
    from app.services.report_native_pdf import LocationBlock

    requisitos = {
        item.chave: item
        for item in avaliar_cfm_2381(
            _conteudo_de_conformidade(
                location=LocationBlock(
                    name="Atendimento domiciliar",
                    address_line=None,
                    contact_line=None,
                )
            )
        )
    }
    assert requisitos["endereco_profissional"].atendido is False
    assert requisitos["contato_profissional"].atendido is False


def test_veredito_so_e_pronto_quando_nada_bloqueante_falta():
    """A conclusão é CALCULADA — não existe caminho para declará-la."""

    from app.services import report_compliance as rc

    requisitos = rc.avaliar_cfm_2381(_conteudo_de_conformidade())
    # Hoje faltam CPF e assinatura qualificada: veredito de espera.
    assert rc.veredito(requisitos) == rc.VEREDITO_AGUARDANDO
    assert {"cpf_paciente", "assinatura_qualificada"} <= {
        item.chave for item in rc.pendencias_bloqueantes(requisitos)
    }

    # Com TODOS os bloqueantes atendidos, e só então, a conclusão vira PRONTO.
    todos_ok = [
        rc.Requisito(
            chave=item.chave,
            exigencia=item.exigencia,
            atendido=True,
            bloqueia_entrega_oficial=item.bloqueia_entrega_oficial,
            detalhe=item.detalhe,
        )
        for item in requisitos
    ]
    assert rc.veredito(todos_ok) == rc.VEREDITO_PRONTO


# -------------------------- 7. assinatura: semântica honesta


def test_liberacao_institucional_nunca_se_declara_icp_brasil():
    """O PDF liberado diz o que é — e o que NÃO é."""

    from datetime import datetime, timezone

    from app.services.report_native_pdf import (
        RELEASE_STATEMENT,
        SIGNATURE_KIND_INSTITUTIONAL,
        build_native_report_pdf,
    )

    assert "não constitui" in RELEASE_STATEMENT
    assert "assinatura digital qualificada ICP-Brasil" in RELEASE_STATEMENT
    pdf = build_native_report_pdf(
        _conteudo_de_conformidade(
            signature_kind=SIGNATURE_KIND_INSTITUTIONAL,
            # O gerador recusa laudo liberado sem código de verificação e
            # sem data de liberação — falhas fechadas que a M25.15 não
            # afrouxa, e que por isso precisam ser satisfeitas aqui.
            validation_code="ABCDEFGH2345",
            released_at_local=datetime.now(timezone.utc),
        )
    )
    assert pdf.startswith(b"%PDF-")


def test_conformidade_reconhece_assinatura_qualificada_quando_houver():
    """Não é ceticismo cego: com prova gravada, o requisito é atendido."""

    from app.services.report_compliance import avaliar_cfm_2381
    from app.services.report_native_pdf import SIGNATURE_KIND_QUALIFIED_ICP

    requisitos = {
        item.chave: item
        for item in avaliar_cfm_2381(
            _conteudo_de_conformidade(
                signature_kind=SIGNATURE_KIND_QUALIFIED_ICP
            )
        )
    }
    assert requisitos["assinatura_qualificada"].atendido is True


def test_gate_de_conformidade_e_admin_only(client, auth, medica, caso):
    """Quem opera não emite parecer de conformidade sobre o próprio trabalho."""

    for papel in ("operacional", "leitura"):
        resposta = client.get(
            f"/api/v1/laudos/{caso['laudo']['id']}/conformidade-cfm",
            headers=auth(papel),
        )
        assert resposta.status_code == 403, resposta.text


def test_gate_de_conformidade_responde_veredito_de_espera(
    client, auth, caso
):
    from app.services import report_compliance as rc

    resposta = client.get(
        f"/api/v1/laudos/{caso['laudo']['id']}/conformidade-cfm",
        headers=auth("admin"),
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["norma"] == "Resolução CFM 2.381/2024"
    assert corpo["veredito"] == rc.VEREDITO_AGUARDANDO
    assert "assinatura_qualificada" in corpo["pendencias_bloqueantes"]
    assert corpo["reports_mode"] == "pilot"


# ---------------------------- 8. contrato do frontend (arquivo estático)
#
# O painel é servido como arquivo estático: não há build, e portanto nada
# quebraria em compilação se a tela voltasse a mostrar código no lugar do
# nome, ou se alguém recolocasse a lista de médicos fixa no JS. Estas
# asserções são a única trava automática que esse arquivo tem.

from pathlib import Path  # noqa: E402

PANEL_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_JS = (PANEL_ROOT / "js" / "report-workflow.js").read_text()


def test_frontend_usa_nome_como_referencia_das_filas():
    # As duas filas e o localizador passam pelos mesmos helpers de
    # identidade; se alguém voltar a imprimir `item.report_code` como título,
    # o helper deixa de ser chamado.
    assert "function patientName(" in WORKFLOW_JS
    assert "function codeTrail(" in WORKFLOW_JS
    assert WORKFLOW_JS.count("patientName(item)") >= 2
    # O cabeçalho da bancada mostra o nome do paciente como título.
    assert "esc(detail.patient.full_name)" in WORKFLOW_JS


def test_frontend_preserva_os_codigos_como_metadado():
    """Nome primário NÃO pode significar código escondido."""

    assert "report-code-trail" in WORKFLOW_JS
    assert "item.exam_code, item.report_code" in WORKFLOW_JS


def test_frontend_busca_medicos_no_backend_e_nao_em_lista_fixa():
    assert '"/laudos/medicos-disponiveis"' in WORKFLOW_JS
    # Nenhum nome de médico real pode estar escrito no código da tela.
    assert "Ana Cristina" not in WORKFLOW_JS
    assert "52623075" not in WORKFLOW_JS
    # Os três casos (0/1/N) existem explicitamente.
    assert "function renderPhysicianChooser(" in WORKFLOW_JS
    assert "Nenhum médico elegível para receber o laudo" in WORKFLOW_JS
    assert "state.busy || semMedico" in WORKFLOW_JS


def test_frontend_busca_exame_por_nome_ou_codigo():
    assert "/laudos/exames?q=${encodeURIComponent(busca)}" in WORKFLOW_JS
    assert '"/laudos/exames?somente_sem_laudo=true"' in WORKFLOW_JS
    assert "REPORT_CODE_RE" in WORKFLOW_JS
    # Ambiguidade nunca é resolvida sozinha pelo navegador.
    assert "state.locateMatches = items;" in WORKFLOW_JS


def test_frontend_nao_promete_assinatura_qualificada_na_liberacao():
    """O rótulo de "liberado" precisa dizer o que ainda falta."""

    assert 'liberado: "Liberado — aguardando assinatura qualificada"' in (
        WORKFLOW_JS
    )
    # O rótulo ANTIGO não pode voltar. A checagem mira a linha do mapa de
    # status, e não a expressão solta: ela também aparece no comentário que
    # explica por que foi trocada.
    assert 'liberado: "Liberado (assinatura eletrônica interna)"' not in (
        WORKFLOW_JS
    )
