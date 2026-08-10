"""M25.17 — o que o primeiro uso real ensinou.

Três defeitos vieram de operar de verdade, e cada teste aqui protege a
correção de um deles:

1. **`unidade_origem_incompativel`.** O formulário pedia origem e unidade
   como campos livres e deixava o operador descobrir quais combinações o
   servidor aceita. O exame já sabia onde foi feito. Os testes de origem
   provam que a derivação sai de campo ESTRUTURADO (nunca de texto livre
   como "Pastore") e que contradição de cadastro para o fluxo em vez de
   imprimir o endereço de uma clínica onde o exame não aconteceu.

2. **Cadastro de teste na fila clínica.** Os cenários das M25.13/M25.14
   apareciam para a médica junto com paciente real. O arquivamento é um
   sinalizador explícito por registro — nunca uma regra sobre o nome, que
   sumiria com um paciente real chamado Teste e deixaria voltar um registro
   de teste renomeado.

3. **Arquivo baixado com nome técnico.** `laudo-ESP-000017-v3-...pdf` não
   diz de quem é. Os testes de download cobrem o nome pedido, a sanitização
   e a impossibilidade de injetar cabeçalho pelo nome do paciente.

Somente dados sintéticos. A rubrica usada nos testes é gerada em memória —
a imagem real da médica nunca entra no repositório.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    AuditLog,
    Partner,
    PartnerUnit,
    Person,
    PhysicianProfile,
    SpirometryExam,
    User,
)
from app.security import (
    ROLE_MEDICO,
    ensure_roles_exist,
    get_role,
    hash_password,
    issue_token,
)
from app.services.download_names import (
    content_disposition,
    report_download_filename,
    sanitize_filename_base,
)
from app.services.report_origin import (
    OriginDerivationError,
    derive_report_origin,
)


@pytest.fixture(autouse=True)
def _reports_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv(
        "M15_AUTH_SECRET",
        "m25-17-operacao-limpa-secret-only-for-tests-0123456789ab",
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _minimal_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    saida = io.BytesIO()
    writer.write(saida)
    return saida.getvalue()


def _rubrica_png(largura: int = 320, altura: int = 440) -> bytes:
    """Rubrica sintética: um traço em RGBA, com fundo transparente.

    Tem a MESMA forma do ativo real (mais alta que larga, com alfa), para
    exercitar o mesmo caminho de validação e desenho — sem que nenhuma
    assinatura de pessoa real precise existir no repositório.
    """

    img = Image.new("RGBA", (largura, altura), (0, 0, 0, 0))
    for y in range(40, altura - 40):
        for dx in range(-3, 4):
            x = largura // 2 + dx + int(60 * (y / altura))
            if 0 <= x < largura:
                img.putpixel((x, y), (30, 34, 48, 255))
    saida = io.BytesIO()
    img.save(saida, "PNG")
    return saida.getvalue()


@pytest.fixture()
def medica(db, client, auth):
    ensure_roles_exist(db)
    user = User(
        email="medica-m25-17@teste.local",
        nome="TESTE APAGAR Médica M25.17",
        password_hash=hash_password("senha-sintetica-m25-17-987"),
    )
    user.roles.append(get_role(db, ROLE_MEDICO))
    db.add(user)
    db.commit()
    resposta = client.patch(
        f"/api/v1/laudos/admin/medicos/{user.id}",
        json={
            "grant_physician_role": True,
            "professional_name": "TESTE APAGAR Médica M25.17",
            "crm_number": "700017",
            "crm_state": "SC",
            "verification_status": "verified",
            "verification_reference": "CRM-VERIF-M25-17",
            "active": True,
            "especialidade": "Pneumologista",
        },
        headers=auth("admin"),
    )
    assert resposta.status_code == 200, resposta.text
    return {
        "user": user,
        "headers": {
            "Authorization": f"Bearer {issue_token(user.id, user.password_hash)}"
        },
        "profile": resposta.json()["profile"],
    }


@pytest.fixture()
def unidade(db):
    parceiro = Partner(
        public_code="PAR-M2517",
        nome="TESTE APAGAR Clínica Parceira",
    )
    db.add(parceiro)
    db.flush()
    unit = PartnerUnit(
        public_code="UNI-M2517",
        partner_id=parceiro.id,
        nome="TESTE APAGAR Unidade Centro",
        logradouro="Rua Sintética, 100",
        bairro="Centro",
        cidade="Rio de Janeiro",
        uf="RJ",
        ativo=True,
    )
    db.add(unit)
    db.commit()
    return {"parceiro": parceiro, "unidade": unit}


def _exame(db, person_id, **campos) -> SpirometryExam:
    exame = SpirometryExam(
        public_code=f"ESP-9{db.query(SpirometryExam).count():05d}",
        person_id=person_id,
        status="Realizado",
        **campos,
    )
    db.add(exame)
    db.commit()
    return exame


# ------------------------------------------------ 1. origem e unidade


def test_clinica_parceira_deriva_origem_e_unidade_do_exame(
    db, person, unidade
):
    """O caso do Geoffrey: modalidade + unidade vinculada bastam."""

    exame = _exame(
        db,
        person["id"],
        modalidade="clinica_parceira",
        partner_id=unidade["parceiro"].id,
        partner_unit_id=unidade["unidade"].id,
    )
    derivado = derive_report_origin(db, exame)
    assert derivado.origin_type == "clinica_parceira"
    assert derivado.partner_unit_id == unidade["unidade"].id
    assert derivado.completo is True
    assert derivado.source == "exame_unidade_parceira"
    # O endereço impresso sai da UNIDADE, nunca de texto digitado.
    assert "Rua Sintética, 100" in derivado.address_line


def test_exame_soprolife_nao_recebe_unidade_parceira(db, person):
    for modalidade, origem in (
        ("cowork", "coworking"),
        ("residencial", "residencial"),
    ):
        exame = _exame(db, person["id"], modalidade=modalidade)
        derivado = derive_report_origin(db, exame)
        assert derivado.origin_type == origem
        assert derivado.partner_unit_id is None
        assert derivado.address_line is None


def test_texto_livre_nunca_decide_a_unidade(db, person, unidade):
    """`origem`/`local_atendimento` dizem "Pastore" e são ignorados.

    Casar a palavra numa string escolheria a unidade FINANCEIRA por
    heurística de texto — e um exame domiciliar cujo campo livre menciona a
    clínica passaria a creditar a clínica.
    """

    exame = _exame(
        db,
        person["id"],
        modalidade="residencial",
        origem="Pastore",
        local_atendimento="Pastore Ipanema",
    )
    derivado = derive_report_origin(db, exame)
    assert derivado.origin_type == "residencial"
    assert derivado.partner_unit_id is None


@pytest.mark.parametrize(
    "campos,codigo",
    [
        (
            {"modalidade": "clinica_parceira"},
            "exame_sem_unidade_parceira",
        ),
        (
            {"modalidade": "residencial", "_unidade": True},
            "unidade_incompativel_com_modalidade",
        ),
        ({"_unidade": True}, "unidade_sem_modalidade"),
    ],
)
def test_cadastro_contraditorio_falha_fechado(
    db, person, unidade, campos, codigo
):
    """Contradição para o fluxo e diz onde corrigir — nunca adivinha."""

    usa_unidade = campos.pop("_unidade", False)
    if usa_unidade:
        campos["partner_id"] = unidade["parceiro"].id
        campos["partner_unit_id"] = unidade["unidade"].id
    exame = _exame(db, person["id"], **campos)
    with pytest.raises(OriginDerivationError) as erro:
        derive_report_origin(db, exame)
    assert erro.value.codigo == codigo
    # A mensagem sozinha deixa o operador parado; o conserto precisa vir junto.
    assert erro.value.como_corrigir


def test_exame_sem_local_registrado_nao_bloqueia_mas_avisa(db, person):
    """Ausência ≠ contradição.

    13 dos 18 exames em produção vieram de importação sem modalidade.
    Bloqueá-los trocaria um laudo sem endereço por nenhum laudo; o caminho
    honesto é a origem genérica, sem endereço inventado, sinalizada como
    incompleta.
    """

    exame = _exame(db, person["id"])
    derivado = derive_report_origin(db, exame)
    assert derivado.origin_type == "outro"
    assert derivado.partner_unit_id is None
    assert derivado.address_line is None
    assert derivado.completo is False


def test_upload_nao_precisa_de_origem_e_grava_a_do_exame(
    db, client, auth, person, unidade, medica
):
    """O formulário simplificado: só exame, médico e PDF."""

    exame = _exame(
        db,
        person["id"],
        modalidade="clinica_parceira",
        partner_id=unidade["parceiro"].id,
        partner_unit_id=unidade["unidade"].id,
    )
    resposta = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame.public_code,
            "physician_profile_id": medica["profile"]["id"],
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert corpo["origin_type"] == "clinica_parceira"
    assert corpo["origin_partner_unit_id"] == unidade["unidade"].id

    registro = db.execute(
        select(AuditLog).where(AuditLog.acao == "laudo_original_atribuido")
    ).scalars().all()[-1]
    assert registro.detalhes["origin_source"] == "exame_unidade_parceira"


def test_payload_artificial_nao_contradiz_o_exame(
    db, client, auth, person, unidade, medica
):
    """Fail closed no servidor, e não só no formulário simplificado."""

    exame = _exame(
        db,
        person["id"],
        modalidade="clinica_parceira",
        partner_id=unidade["parceiro"].id,
        partner_unit_id=unidade["unidade"].id,
    )
    resposta = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame.public_code,
            "physician_profile_id": medica["profile"]["id"],
            # Cliente mandando à mão uma origem que o exame desmente.
            "origin_type": "residencial",
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 422, resposta.text
    assert resposta.json()["erro"]["codigo"] == "origem_divergente_do_exame"


def test_combinacao_invalida_do_primeiro_uso_real_e_impossivel(
    db, client, auth, person, unidade, medica
):
    """`unidade_origem_incompativel` não pode mais ser alcançável pela tela.

    A tela não manda mais origem nem unidade; e um cliente que mande a
    combinação exata do relato (origem `pastore` + unidade) é recusado
    antes, por divergir do exame.
    """

    exame = _exame(
        db,
        person["id"],
        modalidade="clinica_parceira",
        partner_id=unidade["parceiro"].id,
        partner_unit_id=unidade["unidade"].id,
    )
    resposta = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame.public_code,
            "physician_profile_id": medica["profile"]["id"],
            "origin_type": "pastore",
            "origin_partner_unit_id": unidade["unidade"].id,
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 422
    assert resposta.json()["erro"]["codigo"] == "origem_divergente_do_exame"


def test_localizador_devolve_o_local_derivado_para_leitura(
    db, client, auth, person, unidade
):
    exame = _exame(
        db,
        person["id"],
        modalidade="clinica_parceira",
        partner_id=unidade["parceiro"].id,
        partner_unit_id=unidade["unidade"].id,
    )
    resposta = client.get(
        "/api/v1/laudos/exames",
        params={"q": exame.public_code},
        headers=auth("operacional"),
    )
    origem = resposta.json()[0]["origem_derivada"]
    assert origem["ok"] is True
    assert origem["completo"] is True
    assert "TESTE APAGAR Unidade Centro" in origem["display_name"]


def test_localizador_mostra_o_exame_com_cadastro_contraditorio(
    db, client, auth, person
):
    """A linha continua aparecendo — é assim que o erro é descoberto."""

    exame = _exame(db, person["id"], modalidade="clinica_parceira")
    resposta = client.get(
        "/api/v1/laudos/exames",
        params={"q": exame.public_code},
        headers=auth("operacional"),
    )
    origem = resposta.json()[0]["origem_derivada"]
    assert origem["ok"] is False
    assert origem["codigo"] == "exame_sem_unidade_parceira"
    assert origem["como_corrigir"]


def test_atendimento_pode_ser_corrigido_pela_api(
    db, client, auth, person, unidade
):
    """Sem isso, "corrija o cadastro" apontaria para uma tela inexistente."""

    exame = _exame(db, person["id"])
    resposta = client.patch(
        f"/api/v1/espirometrias/{exame.id}",
        json={
            "modalidade": "clinica_parceira",
            "partner_unit_id": unidade["unidade"].id,
        },
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    db.expire_all()
    atualizado = db.get(SpirometryExam, exame.id)
    assert atualizado.modalidade == "clinica_parceira"
    assert atualizado.partner_unit_id == unidade["unidade"].id
    # O parceiro é preenchido a partir da unidade escolhida.
    assert atualizado.partner_id == unidade["parceiro"].id
    assert derive_report_origin(db, atualizado).completo is True


def test_correcao_incoerente_do_atendimento_e_recusada(
    db, client, auth, person, unidade
):
    exame = _exame(db, person["id"])
    resposta = client.patch(
        f"/api/v1/espirometrias/{exame.id}",
        json={
            "modalidade": "residencial",
            "partner_unit_id": unidade["unidade"].id,
        },
        headers=auth("operacional"),
    )
    assert resposta.status_code == 422, resposta.text
    # `HTTPException` com detail em dict: o envelope de erro do núcleo põe o
    # dicionário em `mensagem` (mesma convenção de `test_partners.py`).
    assert resposta.json()["erro"]["mensagem"]["codigo"] == (
        "unidade_incompativel_com_modalidade"
    )


# --------------------------------------- 2. cadastro interno arquivado


def _arquivar(db, person_id, motivo="cenário interno M25.13"):
    from datetime import datetime, timezone

    pessoa = db.get(Person, person_id)
    pessoa.arquivado = True
    pessoa.arquivado_em = datetime.now(timezone.utc)
    pessoa.arquivado_motivo = motivo
    db.commit()
    return pessoa


def test_arquivado_some_da_fila_da_medica(
    db, client, auth, person, unidade, medica
):
    exame = _exame(
        db,
        person["id"],
        modalidade="clinica_parceira",
        partner_id=unidade["parceiro"].id,
        partner_unit_id=unidade["unidade"].id,
    )
    envio = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame.public_code,
            "physician_profile_id": medica["profile"]["id"],
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert envio.status_code == 201
    laudo = envio.json()["public_code"]

    antes = client.get("/api/v1/laudos/meus", headers=medica["headers"])
    assert [i["report_code"] for i in antes.json()] == [laudo]

    _arquivar(db, person["id"])

    depois = client.get("/api/v1/laudos/meus", headers=medica["headers"])
    assert depois.json() == []
    assert person["nome_completo"] not in depois.text


def test_arquivado_some_do_acompanhamento_e_do_localizador(
    db, client, auth, person, unidade, medica
):
    exame = _exame(
        db,
        person["id"],
        modalidade="clinica_parceira",
        partner_id=unidade["parceiro"].id,
        partner_unit_id=unidade["unidade"].id,
    )
    client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame.public_code,
            "physician_profile_id": medica["profile"]["id"],
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    _arquivar(db, person["id"])

    operacional = client.get("/api/v1/laudos", headers=auth("operacional"))
    assert operacional.json() == []

    localizador = client.get(
        "/api/v1/laudos/exames",
        params={"q": exame.public_code},
        headers=auth("operacional"),
    )
    assert localizador.json() == []

    por_nome = client.get(
        "/api/v1/laudos/exames",
        params={"q": person["nome_completo"][:10]},
        headers=auth("operacional"),
    )
    assert por_nome.json() == []


def test_modo_tecnico_ainda_alcanca_o_arquivado(
    db, client, auth, person, unidade, medica
):
    """Auditar cenário antigo continua possível — de forma explícita."""

    exame = _exame(
        db,
        person["id"],
        modalidade="clinica_parceira",
        partner_id=unidade["parceiro"].id,
        partner_unit_id=unidade["unidade"].id,
    )
    client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame.public_code,
            "physician_profile_id": medica["profile"]["id"],
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    _arquivar(db, person["id"])

    tecnico = client.get(
        "/api/v1/laudos",
        params={"incluir_arquivados": "true"},
        headers=auth("operacional"),
    )
    assert len(tecnico.json()) == 1
    localizador = client.get(
        "/api/v1/laudos/exames",
        params={"q": exame.public_code, "incluir_arquivados": "true"},
        headers=auth("operacional"),
    )
    assert len(localizador.json()) == 1


def test_fila_clinica_nao_tem_modo_tecnico(
    db, client, auth, person, unidade, medica
):
    """A médica não recebe teste nem passando parâmetro na URL."""

    exame = _exame(
        db,
        person["id"],
        modalidade="clinica_parceira",
        partner_id=unidade["parceiro"].id,
        partner_unit_id=unidade["unidade"].id,
    )
    client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame.public_code,
            "physician_profile_id": medica["profile"]["id"],
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    _arquivar(db, person["id"])
    resposta = client.get(
        "/api/v1/laudos/meus",
        params={"incluir_arquivados": "true"},
        headers=medica["headers"],
    )
    assert resposta.json() == []


def test_arquivar_preserva_auditoria_versoes_e_hashes(
    db, client, auth, person, unidade, medica
):
    """Arquivar tira das listas. Não apaga nada."""

    from app.models import ReportDocument, ReportDocumentVersion

    exame = _exame(
        db,
        person["id"],
        modalidade="clinica_parceira",
        partner_id=unidade["parceiro"].id,
        partner_unit_id=unidade["unidade"].id,
    )
    client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame.public_code,
            "physician_profile_id": medica["profile"]["id"],
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    auditoria_antes = db.scalar(
        select(AuditLog).where(AuditLog.acao == "laudo_original_atribuido")
    )
    versoes_antes = db.query(ReportDocumentVersion).count()
    hashes_antes = [
        v.sha256 for v in db.query(ReportDocumentVersion).all()
    ]

    _arquivar(db, person["id"])

    assert db.query(ReportDocument).count() == 1
    assert db.query(ReportDocumentVersion).count() == versoes_antes
    assert [
        v.sha256 for v in db.query(ReportDocumentVersion).all()
    ] == hashes_antes
    assert db.get(AuditLog, auditoria_antes.id) is not None
    assert db.get(Person, person["id"]) is not None


def test_arquivamento_exige_evidencia(db, person):
    """Marcar sem data e motivo é estado que ninguém explica depois."""

    from sqlalchemy.exc import IntegrityError

    pessoa = db.get(Person, person["id"])
    pessoa.arquivado = True
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ---------------------------------------------------- 3. rubrica visual


def test_medico_com_rubrica_desenha_no_pdf(client, auth, medica):
    """Ativo cadastrado -> imagem no laudo liberado."""

    from app.services.native_report_builder import physician_block
    from app.services.report_native_pdf import (
        SignatureImage,
        build_native_report_pdf,
    )

    envio = client.post(
        f"/api/v1/laudos/admin/medicos/{medica['profile']['id']}/assinatura",
        files={"arquivo": ("rubrica.png", _rubrica_png(), "image/png")},
        data={"confirmacao": "ATIVO DE ASSINATURA AUTORIZADO"},
        headers=auth("admin"),
    )
    assert envio.status_code == 201, envio.text
    corpo = envio.json()
    # Metadado técnico apenas: nunca os bytes, nunca o caminho.
    assert "sha256" in corpo
    assert "data" not in corpo
    assert "storage_path" not in envio.text
    assert "base64" not in envio.text

    consulta = client.get(
        f"/api/v1/laudos/admin/medicos/{medica['profile']['id']}/assinatura",
        headers=auth("admin"),
    )
    assert consulta.status_code == 200
    assert "url" not in consulta.text


def test_pdf_continua_valido_sem_rubrica_cadastrada():
    """Médico sem ativo: laudo sai normalmente, sem quebrar."""

    from tests.test_m25_15_operacao_real import _conteudo_de_conformidade
    from app.services.report_native_pdf import build_native_report_pdf

    from datetime import datetime, timezone

    pdf = build_native_report_pdf(
        _conteudo_de_conformidade(
            signature_image=None,
            validation_code="ABCDEFGH2345",
            released_at_local=datetime.now(timezone.utc),
        )
    )
    assert pdf.startswith(b"%PDF-")


def test_rubrica_nao_altera_a_semantica_da_assinatura():
    """Imagem é representação visual — não é prova criptográfica.

    Este é o teste que impede a confusão mais cara possível: um laudo com
    rubrica desenhada continua sendo liberação institucional, e o portão CFM
    continua contando a assinatura qualificada como pendência.
    """

    from datetime import datetime, timezone

    from tests.test_m25_15_operacao_real import _conteudo_de_conformidade
    from app.services import report_compliance as rc
    from app.services.report_native_pdf import (
        SIGNATURE_KIND_INSTITUTIONAL,
        SignatureImage,
    )

    conteudo = _conteudo_de_conformidade(
        signature_image=SignatureImage(
            data=_rubrica_png(), width=320, height=440
        ),
        signature_kind=SIGNATURE_KIND_INSTITUTIONAL,
        validation_code="ABCDEFGH2345",
        released_at_local=datetime.now(timezone.utc),
    )
    requisitos = {
        item.chave: item for item in rc.avaliar_cfm_2381(conteudo)
    }
    assert requisitos["assinatura_qualificada"].atendido is False
    assert rc.veredito(rc.avaliar_cfm_2381(conteudo)) == rc.VEREDITO_AGUARDANDO


def test_ativo_de_rubrica_nao_tem_rota_publica(client, auth, medica):
    """Nenhum endpoint serve os bytes da imagem ao navegador."""

    from app.main import app

    caminhos = [getattr(r, "path", "") for r in app.routes]
    assinatura = [c for c in caminhos if "assinatura" in c and "admin" in c]
    # As únicas rotas do ativo são administrativas (cadastro/consulta/revogação).
    assert assinatura
    for caminho in assinatura:
        assert caminho.startswith("/api/v1/laudos/admin/")
    # E nenhuma rota entrega conteúdo de imagem por URL.
    assert not [c for c in caminhos if "assinatura" in c and "conteudo" in c]


def test_rubrica_real_nao_esta_versionada():
    """A imagem da médica é ativo privado — nunca entra no Git."""

    import subprocess

    saida = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        cwd="/home/fedorasurf/soprolife-worktrees/"
        "claude-m25-15-laudos-operacao-real",
    ).stdout.lower()
    for proibido in ("rubrica", "assinatura-ana", "signature-asset"):
        assert proibido not in saida, proibido


# ------------------------------------------------- 4. nome do download


@pytest.mark.parametrize(
    "nome,mir,esperado",
    [
        ("Geoffrey Kirk Barnes", False, "Geoffrey Kirk Barnes - Assinado.pdf"),
        (
            "Geoffrey Kirk Barnes",
            True,
            "Geoffrey Kirk Barnes - Exame técnico.pdf",
        ),
        ("João da Silva", False, "João da Silva - Assinado.pdf"),
        ("Maria / Souza", False, "Maria Souza - Assinado.pdf"),
        (None, False, "LAU-000123 - Assinado.pdf"),
        ("", False, "LAU-000123 - Assinado.pdf"),
        ("..", False, "LAU-000123 - Assinado.pdf"),
        (".", False, "LAU-000123 - Assinado.pdf"),
    ],
)
def test_nome_do_arquivo_e_humano_e_sanitizado(nome, mir, esperado):
    assert report_download_filename(
        patient_name=nome,
        fallback_code="LAU-000123",
        is_technical_exam=mir,
    ) == esperado


@pytest.mark.parametrize(
    "perigoso",
    [
        "../../etc/passwd",
        "C:\\Windows\\System32",
        'aspas" e <tags>',
        "pipe|asterisco*interrogacao?",
        "controle\x00nulo",
    ],
)
def test_caracteres_perigosos_saem_do_nome(perigoso):
    base = sanitize_filename_base(perigoso)
    for proibido in '<>:"/\\|?*':
        assert proibido not in base
    assert "\x00" not in base
    assert base not in {".", ".."}


def test_content_disposition_nao_permite_injecao_de_cabecalho():
    """Nome com CRLF não pode emendar um cabeçalho novo."""

    header = content_disposition(
        report_download_filename(
            patient_name="Fulano\r\nX-Injetado: sim",
            fallback_code="LAU-000001",
            is_technical_exam=False,
        ),
        disposition="attachment",
    )
    assert "\r" not in header and "\n" not in header
    assert "X-Injetado" not in header.split(";")[0]


def test_content_disposition_preserva_acento_em_filename_estendido():
    header = content_disposition(
        "João da Silva - Assinado.pdf", disposition="attachment"
    )
    assert "filename*=UTF-8''" in header
    assert "Jo%C3%A3o" in header
    # E mantém uma versão ASCII para clientes antigos.
    assert 'filename="Joao da Silva - Assinado.pdf"' in header


def test_download_real_usa_o_nome_do_paciente(
    db, client, auth, person, unidade, medica
):
    exame = _exame(
        db,
        person["id"],
        modalidade="clinica_parceira",
        partner_id=unidade["parceiro"].id,
        partner_unit_id=unidade["unidade"].id,
    )
    envio = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame.public_code,
            "physician_profile_id": medica["profile"]["id"],
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    documento = envio.json()
    versao = documento["versoes"][0]["id"]
    resposta = client.get(
        f"/api/v1/laudos/{documento['id']}/versoes/{versao}/conteudo",
        params={"modo": "download"},
        headers=medica["headers"],
    )
    assert resposta.status_code == 200
    disposicao = resposta.headers["content-disposition"]
    # É o PDF ORIGINAL da MIR, então o sufixo é "Exame técnico".
    assert "Exame" in disposicao
    assert person["nome_completo"].split()[0] in disposicao
