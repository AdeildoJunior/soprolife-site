"""M25.18 — o documento vai ser assinado FORA daqui, e precisa dizer isso.

A operação clínica passou a acontecer com paciente real, e o piloto virou
fluxo. Três coisas mudaram de natureza:

1. **O PDF não é um protótipo nem um documento assinado.** É o laudo
   concluído pela médica, destinado à assinatura qualificada que ela aplica
   fora do sistema. A faixa "PILOTO INTERNO" saiu; o que entra no lugar não
   é silêncio, é a declaração correta — inclusive no selo, que dizia
   "ASSINADO ELETRONICAMENTE" num carimbo redondo.

2. **O nome do arquivo.** A M25.17 mandou o cabeçalho certo e mesmo assim o
   navegador salvou `UWNAUiEo.pdf`. Havia DUAS causas, e os testes aqui
   travam as duas — a do proxy e a do visualizador de PDF.

3. **CPF.** Passou a existir como campo opcional e validado. Existir não
   pode significar vazar: fila, busca e rota pública continuam sem ele.

Somente dados sintéticos.
"""

from __future__ import annotations

import importlib.util
import io
import pathlib
import re

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.config import get_settings
from app.models import Partner, PartnerUnit, Person, SpirometryExam, User
from app.security import (
    ROLE_MEDICO,
    ensure_roles_exist,
    get_role,
    hash_password,
    issue_token,
)
from app.services.cpf import (
    CPFInvalidoError,
    cpf_valido,
    formatar_cpf,
    mascarar_cpf,
    normalizar_cpf,
)
from app.services.download_names import report_download_filename

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_JS = (PANEL_ROOT / "js" / "report-workflow.js").read_text()
NUCLEO_JS = (PANEL_ROOT / "js" / "m15-nucleo.js").read_text()


@pytest.fixture(autouse=True)
def _reports_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv(
        "M15_AUTH_SECRET",
        "m25-18-assinatura-externa-secret-only-for-tests-01234567",
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


@pytest.fixture()
def medica(db, client, auth):
    ensure_roles_exist(db)
    user = User(
        email="medica-m25-18@teste.local",
        nome="TESTE APAGAR Médica M25.18",
        password_hash=hash_password("senha-sintetica-m25-18-987"),
    )
    user.roles.append(get_role(db, ROLE_MEDICO))
    db.add(user)
    db.commit()
    resposta = client.patch(
        f"/api/v1/laudos/admin/medicos/{user.id}",
        json={
            "grant_physician_role": True,
            "professional_name": "TESTE APAGAR Médica M25.18",
            "crm_number": "700018",
            "crm_state": "SC",
            "verification_status": "verified",
            "verification_reference": "CRM-VERIF-M25-18",
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
    parceiro = Partner(public_code="PAR-M2518", nome="TESTE APAGAR Parceira")
    db.add(parceiro)
    db.flush()
    unit = PartnerUnit(
        public_code="UNI-M2518",
        partner_id=parceiro.id,
        nome="TESTE APAGAR Unidade M25.18",
        logradouro="Rua Sintética, 18",
        bairro="Centro",
        cidade="Rio de Janeiro",
        uf="RJ",
        telefone_central="(21) 0000-0000",
        ativo=True,
    )
    db.add(unit)
    db.commit()
    return {"parceiro": parceiro, "unidade": unit}


@pytest.fixture()
def caso(db, client, auth, person, unidade, medica):
    exame = SpirometryExam(
        public_code="ESP-918018",
        person_id=person["id"],
        status="Realizado",
        modalidade="clinica_parceira",
        partner_id=unidade["parceiro"].id,
        partner_unit_id=unidade["unidade"].id,
    )
    db.add(exame)
    db.commit()
    envio = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame.public_code,
            "physician_profile_id": medica["profile"]["id"],
        },
        files={"file": ("tecnico.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert envio.status_code == 201, envio.text
    return {"exame": exame, "laudo": envio.json(), "pessoa": person}


# ------------------------------------ 1. nome do arquivo: as DUAS causas


def _proxy_module():
    """Carrega o proxy do painel como módulo, sem subir servidor."""

    caminho = PANEL_ROOT / "scripts" / "command-center-local-server.py"
    spec = importlib.util.spec_from_file_location("_ccls_m2518", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_proxy_deixa_passar_o_nome_humano():
    """CAUSA 1 — o proxy descartava o cabeçalho inteiro.

    A allowlist aceitava só `[A-Za-z0-9._-]` e exigia `$` logo após as
    aspas. O nome humano tem ESPAÇOS e vem acompanhado de `filename*`, então
    os dois eram recusados e o `Content-Disposition` sumia no caminho. O
    navegador ficava sem nome e o Chrome gerava `UWNAUiEo.pdf`.

    O nome técnico antigo passava — por isso a regressão só apareceu depois
    da melhoria da M25.17.
    """

    validar = _proxy_module()._safe_content_disposition
    aceitos = [
        'attachment; filename="ANTONIO LOPES DA SILVA - Para assinatura.pdf"',
        (
            'attachment; filename="ANTONIO LOPES DA SILVA - Para assinatura.pdf"'
            "; filename*=UTF-8''ANTONIO%20LOPES%20DA%20SILVA%20-%20Para"
            "%20assinatura.pdf"
        ),
        'inline; filename="Geoffrey Kirk Barnes - Exame tecnico.pdf"',
        # O formato técnico anterior continua válido.
        'attachment; filename="laudo-ESP-000017-v3-laudo_liberado.pdf"',
    ]
    for valor in aceitos:
        assert validar(valor) == valor, valor


@pytest.mark.parametrize(
    "perigoso",
    [
        'attachment; filename="a";X-Injetado: 1"',
        'attachment; filename="../../etc/passwd"',
        'attachment; filename="a\r\nX-Injetado: 1"',
        'form-data; filename="x.pdf"',
        'attachment; filename="C:\\Windows\\x.pdf"',
    ],
)
def test_proxy_continua_recusando_cabecalho_perigoso(perigoso):
    """Abrir para espaço não pode abrir para injeção."""

    assert _proxy_module()._safe_content_disposition(perigoso) is None


def test_frontend_nao_deixa_o_visualizador_sem_nome():
    """CAUSA 2 — o <iframe> apontava para uma object URL.

    O visualizador de PDF do Chrome tem o PRÓPRIO botão de download, que é o
    botão à mão de quem está lendo o laudo. Baixando de um `blob:` ele não
    tem nome para herdar. Nenhum ajuste no botão "Baixar" do painel
    alcançava esse caminho.
    """

    assert "function pdfViewerSource(" in WORKFLOW_JS
    assert "c.hasSession()" in WORKFLOW_JS
    assert "apiUrl: function (path)" in NUCLEO_JS
    # E o caminho do blob continua existindo para o token da CLI, que não
    # consegue autenticar um iframe.
    assert "URL.createObjectURL(blob)" in WORKFLOW_JS


def test_frontend_usa_o_nome_do_servidor_no_download_explicito():
    """O botão "Baixar" respeita o `Content-Disposition`."""

    assert "function nomeDoContentDisposition(" in NUCLEO_JS
    assert "blob.nomeSugerido = nomeDoContentDisposition(disposicao)" in (
        NUCLEO_JS
    )
    assert 'anchor.download = blob.nomeSugerido || ""' in WORKFLOW_JS


def test_nome_do_laudo_ainda_nao_e_assinado():
    """O arquivo que sai daqui é o que a médica leva para assinar."""

    assert report_download_filename(
        patient_name="ANTONIO LOPES DA SILVA",
        fallback_code="LAU-000003",
        is_technical_exam=False,
    ) == "ANTONIO LOPES DA SILVA - Para assinatura.pdf"
    assert report_download_filename(
        patient_name="ANTONIO LOPES DA SILVA",
        fallback_code="LAU-000003",
        is_technical_exam=True,
    ) == "ANTONIO LOPES DA SILVA - Exame técnico.pdf"


def test_download_real_entrega_para_assinatura(client, medica, caso):
    documento = caso["laudo"]
    versao = documento["versoes"][0]["id"]
    resposta = client.get(
        f"/api/v1/laudos/{documento['id']}/versoes/{versao}/conteudo",
        params={"modo": "download"},
        headers=medica["headers"],
    )
    assert resposta.status_code == 200
    disposicao = resposta.headers["content-disposition"]
    assert "Exame" in disposicao  # é o PDF original da MIR
    assert caso["pessoa"]["nome_completo"].split()[0] in disposicao
    # E o cabeçalho atravessa a allowlist do proxy — que é onde ele morria.
    assert _proxy_module()._safe_content_disposition(disposicao) == disposicao


def test_disposicao_do_laudo_atravessa_o_proxy(client, medica, caso, db):
    """O caminho completo: nome do laudo + allowlist do proxy."""

    from app.services.download_names import content_disposition

    header = content_disposition(
        report_download_filename(
            patient_name="ANTONIO LOPES DA SILVA",
            fallback_code="LAU-000003",
            is_technical_exam=False,
        ),
        disposition="attachment",
    )
    assert "ANTONIO LOPES DA SILVA - Para assinatura.pdf" in header
    assert _proxy_module()._safe_content_disposition(header) == header


# ----------------------------- 2. piloto fora, semântica correta no lugar


def test_ui_nao_tem_mais_faixa_de_piloto():
    # A checagem é pelo CÓDIGO que desenhava, e não pela frase: o texto da
    # faixa continua no arquivo, dentro do comentário que explica por que ela
    # saiu. Procurar a frase acusaria justamente a documentação da remoção.
    assert "const PILOT_WARNING =" not in WORKFLOW_JS
    assert 'class="report-pilot-warning"' not in WORKFLOW_JS
    # A função ainda existe (o chamador não precisou mudar), mas não desenha
    # mais nada. Comparar sem depender de indentação exata.
    corpo = re.search(
        r"function renderPilotWarning\(\)\s*\{(.*?)\}", WORKFLOW_JS, re.S
    )
    assert corpo is not None
    assert corpo.group(1).strip() == 'return "";' 


def test_ui_fala_em_concluir_e_nao_em_assinar():
    assert "Concluir laudo" in WORKFLOW_JS
    assert "Confirmar conclusão do laudo" in WORKFLOW_JS
    assert "Sim, concluir laudo" in WORKFLOW_JS
    assert (
        "O conteúdo será congelado e o PDF ficará disponível para assinatura "
        "digital qualificada externa." in WORKFLOW_JS
    )
    # A linguagem antiga não pode voltar como rótulo de botão.
    assert "Assinar e liberar laudo" not in WORKFLOW_JS
    assert "Sim, assinar e liberar laudo" not in WORKFLOW_JS


def test_status_diz_o_que_falta():
    assert 'liberado: "Concluído — aguardando assinatura qualificada"' in (
        WORKFLOW_JS
    )


def _conteudo(**overrides):
    from datetime import date, datetime, timezone

    from app.services.report_native_pdf import (
        ExamBlock,
        LocationBlock,
        NativeReportContent,
        PatientBlock,
        PhysicianBlock,
    )

    base = {
        "document_code": "LAU-000003",
        "version_number": 1,
        "patient": PatientBlock(
            full_name="TESTE APAGAR Paciente M25.18",
            birth_date=date(1980, 1, 1),
            sex="masculino",
            public_code="PES-000001",
        ),
        "exam": ExamBlock(
            public_code="ESP-000018",
            exam_date=date(2026, 8, 9),
            exam_time="09:00",
            date_precision="dia",
            has_post_bd=True,
            clinical_indication="teste",
        ),
        "location": LocationBlock(
            name="Unidade Sintética",
            address_line="Rua Sintética, 18",
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
        "released_at_local": datetime.now(timezone.utc),
        "validation_code": "ABCDEFGH2345",
    }
    base.update(overrides)
    return NativeReportContent(**base)


def _texto_pdf(dados: bytes) -> str:
    from pypdf import PdfReader

    leitor = PdfReader(io.BytesIO(dados))
    return "\n".join(pagina.extract_text() or "" for pagina in leitor.pages)


def test_pdf_novo_nao_tem_faixa_de_piloto():
    from app.services.report_native_pdf import build_native_report_pdf

    texto = _texto_pdf(build_native_report_pdf(_conteudo())).upper()
    assert "PILOTO INTERNO" not in texto
    assert "NÃO LIBERAR AO PACIENTE" not in texto


def test_pdf_novo_declara_conclusao_e_nao_assinatura():
    from app.services.report_native_pdf import build_native_report_pdf

    texto = _texto_pdf(build_native_report_pdf(_conteudo()))
    alto = texto.upper()
    # Selo semanticamente inequívoco.
    assert "CONCLUÍDO" in alto and "PELA MÉDICA" in alto
    assert "AGUARDANDO" in alto and "ASSINATURA" in alto
    # Nunca as marcas reservadas à assinatura qualificada.
    assert "ASSINADO DIGITALMENTE" not in alto
    assert "PADRÃO PADES" not in alto
    # E o texto discreto que diz ONDE conferir a assinatura de verdade.
    assert "Documento concluído pela médica responsável" in texto
    assert "deve ser verificada no arquivo" in texto
    # A data acima da assinatura também descreve conclusão, não liberação
    # para entrega — o documento ainda vai ser assinado fora do sistema.
    assert "Concluído em" in texto
    assert "Liberado em" not in texto
    # ICP-Brasil aparece UMA vez — na frase que NEGA a assinatura.
    assert alto.count("ICP-BRASIL") == 1
    assert "não constitui" in texto


def test_pdf_novo_preserva_rubrica_crm_e_rqe():
    from app.services.report_native_pdf import (
        SignatureImage,
        build_native_report_pdf,
    )
    from tests.test_m25_17_operacao_limpa import _rubrica_png

    pdf = build_native_report_pdf(
        _conteudo(
            signature_image=SignatureImage(
                data=_rubrica_png(), width=320, height=440
            )
        )
    )
    texto = _texto_pdf(pdf)
    assert "CRM-RJ 52.62307-5" in texto
    assert "RQE 58224" in texto
    assert "TESTE APAGAR Médica" in texto
    # A rubrica é imagem: o que se prova é que ela foi embutida.
    assert b"/Image" in pdf or b"/XObject" in pdf


def test_rubrica_continua_sem_valer_como_assinatura_qualificada():
    from app.services import report_compliance as rc
    from app.services.report_native_pdf import SignatureImage
    from tests.test_m25_17_operacao_limpa import _rubrica_png

    conteudo = _conteudo(
        signature_image=SignatureImage(
            data=_rubrica_png(), width=320, height=440
        )
    )
    requisitos = {i.chave: i for i in rc.avaliar_cfm_2381(conteudo)}
    assert requisitos["assinatura_qualificada"].atendido is False


# ------------------------------------------------ 3. CPF opcional


@pytest.mark.parametrize(
    "entrada", ["529.982.247-25", "52998224725", " 529 982 247 25 "]
)
def test_cpf_valido_e_normalizado_para_digitos(entrada):
    assert normalizar_cpf(entrada) == "52998224725"
    assert cpf_valido(entrada) is True


@pytest.mark.parametrize(
    "entrada,codigo",
    [
        ("111.111.111-11", "cpf_invalido"),
        ("12345678900", "cpf_invalido"),
        ("123", "cpf_formato_invalido"),
        ("abcdefghijk", "cpf_formato_invalido"),
    ],
)
def test_cpf_invalido_e_recusado(entrada, codigo):
    with pytest.raises(CPFInvalidoError) as erro:
        normalizar_cpf(entrada)
    assert erro.value.codigo == codigo


@pytest.mark.parametrize("vazio", [None, "", "   "])
def test_cpf_ausente_e_ausencia_legitima(vazio):
    """Paciente sem CPF aplicável existe; obrigar produziria CPF inventado."""

    assert normalizar_cpf(vazio) is None


def test_cpf_opcional_no_cadastro(client, auth, db):
    sem = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "TESTE APAGAR Sem CPF"},
        headers=auth("operacional"),
    )
    assert sem.status_code == 201, sem.text
    assert sem.json()["tem_cpf"] is False
    assert sem.json()["cpf_mascarado"] is None

    com = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "TESTE APAGAR Com CPF", "cpf": "529.982.247-25"},
        headers=auth("operacional"),
    )
    assert com.status_code == 201, com.text
    assert com.json()["tem_cpf"] is True
    # Mascarado na resposta de cadastro — nunca o número inteiro.
    assert com.json()["cpf_mascarado"] == "***.982.247-**"
    assert "52998224725" not in com.text
    # E gravado só em dígitos.
    assert db.get(Person, com.json()["id"]).cpf == "52998224725"


def test_cpf_pode_ser_preenchido_depois_e_removido(client, auth, person, db):
    """Cadastro existente não é alterado sozinho, mas aceita correção."""

    assert db.get(Person, person["id"]).cpf is None
    # Encerra a transação ORM de leitura antes de voltar à API: no SQLite ela
    # deixaria o PATCH falhar com "database is locked".
    db.rollback()
    preenche = client.patch(
        f"/api/v1/pessoas/{person['id']}",
        json={"cpf": "111.444.777-35"},
        headers=auth("operacional"),
    )
    assert preenche.status_code == 200, preenche.text
    db.expire_all()
    assert db.get(Person, person["id"]).cpf == "11144477735"
    db.rollback()

    # String vazia DESVINCULA — é como se corrige um CPF errado.
    remove = client.patch(
        f"/api/v1/pessoas/{person['id']}",
        json={"cpf": ""},
        headers=auth("operacional"),
    )
    assert remove.status_code == 200, remove.text
    db.expire_all()
    assert db.get(Person, person["id"]).cpf is None


def test_cpf_nao_vaza_na_fila_nem_na_rota_publica(
    db, client, auth, medica, caso, person
):
    from datetime import datetime, timezone

    from app.models import ReportDocument

    db.get(Person, person["id"]).cpf = "52998224725"
    db.commit()

    for resposta in (
        client.get("/api/v1/laudos", headers=auth("operacional")),
        client.get("/api/v1/laudos/meus", headers=medica["headers"]),
        client.get(
            "/api/v1/laudos/exames",
            params={"q": caso["exame"].public_code},
            headers=auth("operacional"),
        ),
    ):
        assert resposta.status_code == 200
        assert "52998224725" not in resposta.text
        assert "529.982.247-25" not in resposta.text
        # A chave também não aparece: nem mascarada, nem como "tem_cpf".
        assert "cpf" not in resposta.text.lower()

    # Rota de validação: nem CPF, nem qualquer dado de paciente.
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
    documento.validation_code = "VALIDA234567"
    documento.signature_status = "liberada_institucional"
    db.commit()

    validacao = client.get(
        "/api/v1/laudos/validacao/VALIDA234567", headers=auth("operacional")
    )
    assert validacao.status_code == 200
    assert "52998224725" not in validacao.text
    assert "529.982.247-25" not in validacao.text
    assert '"cpf' not in validacao.text.lower()


def test_cpf_nao_entra_na_auditoria(db, client, auth):
    from app.models import AuditLog

    client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "TESTE APAGAR Auditoria CPF", "cpf": "529.982.247-25"},
        headers=auth("operacional"),
    )
    despejo = " ".join(
        str(a.detalhes) for a in db.execute(select(AuditLog)).scalars()
    )
    assert "52998224725" not in despejo
    assert "529.982.247-25" not in despejo


def test_cpf_entra_no_laudo_quando_existe():
    """Contexto clínico autorizado é o ÚNICO lugar com o número completo."""

    from app.services.report_native_pdf import (
        PatientBlock,
        build_native_report_pdf,
    )

    com_cpf = _conteudo(
        patient=PatientBlock(
            full_name="TESTE APAGAR Paciente M25.18",
            birth_date=None,
            sex="masculino",
            public_code="PES-000001",
            cpf=formatar_cpf("52998224725"),
        )
    )
    assert "529.982.247-25" in _texto_pdf(build_native_report_pdf(com_cpf))

    # Sem CPF cadastrado a linha simplesmente não existe — nunca um
    # "não informado" no lugar de um documento de identidade.
    texto = _texto_pdf(build_native_report_pdf(_conteudo()))
    assert "CPF" not in texto.upper()


def test_mascara_mostra_que_existe_sem_revelar():
    assert mascarar_cpf("52998224725") == "***.982.247-**"
    assert mascarar_cpf(None) is None
