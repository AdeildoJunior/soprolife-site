"""M26.12 — "Retornar para laudadora": devolução administrativa de um laudo
já liberado para correção médica, sem apagar paciente/exame/histórico.

Reaproveita o núcleo de abertura de corretiva já provado em
`test_m25_2_native_report.py` (`_open_corrective_document`, extraído do
antigo `/nova-versao-corretiva`). O que este arquivo prova é a CAMADA NOVA:
quem pode acionar, para quem a autoria clínica vai, o que sobrevive intocado,
a auditoria, e a revogação automática do acesso do paciente ao PDF superado.

Tudo sintético: pacientes "TESTE APAGAR", médicas de teste, CRMs inventados,
PDFs gerados na hora.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import select

from app.models import (
    AuditLog,
    ReportAssignment,
    ReportDocument,
    ReportDocumentVersion,
    SpirometryExam,
    User,
)
from app.security import (
    ensure_roles_exist,
    get_role,
    hash_password,
    issue_token,
)


def _load(nome: str):
    caminho = pathlib.Path(__file__).with_name(nome)
    spec = importlib.util.spec_from_file_location(f"_{nome[:-3]}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_M25_2 = _load("test_m25_2_native_report.py")
_physician = _M25_2._physician
_configure_profile = _M25_2._configure_profile
_make_case = _M25_2._make_case
_preview = _M25_2._preview
_release = _M25_2._release


@pytest.fixture(autouse=True)
def reports_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "m26-12-secret-de-teste-0123456789abcdef0123"
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def portal_ligado(monkeypatch):
    """M15_PORTAL_* — necessários só para o teste de revogação de acesso.

    `_montar_caso`/`_acesso` (de `test_m26_4_portal_resultados.py`) são
    chamados aqui como funções puras, não via fixture: o autouse daquele
    módulo não roda fora da própria coleta de testes dele, então as
    variáveis de ambiente do portal precisam ser religadas aqui também.
    """

    monkeypatch.setenv(
        "M15_REPORTS_VALIDATION_BASE_URL",
        "https://painel-teste.soprolife.local/validar",
    )
    monkeypatch.setenv("M15_PORTAL_ENABLED", "true")
    monkeypatch.setenv(
        "M15_PORTAL_TOKEN_KEY",
        "m26-12-chave-que-deriva-o-link-do-paciente-9876543210abcdef",
    )
    monkeypatch.setenv(
        "M15_PORTAL_SESSION_SECRET",
        "m26-12-segredo-do-cookie-do-portal-publico-fedcba9876543210",
    )
    monkeypatch.setenv(
        "M15_PORTAL_PUBLIC_BASE_URL", "https://soprolife.com.br/resultados"
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def case(client, auth, db, person):
    return _make_case(client, auth, db, person, com_bd=True)


def _liberado(client, case):
    preview = _preview(client, case).json()
    released = _release(client, case, preview)
    assert released.status_code == 200, released.text
    return released.json()


# ---------------------------------------------------------------- caminho feliz


def test_admin_devolve_laudo_liberado_para_a_mesma_medica(client, auth, case, db):
    liberado = _liberado(client, case)

    resposta = client.post(
        f"/api/v1/laudos/{case['document']['id']}/retornar-para-correcao",
        json={"reason_code": "clinical_correction"},
        headers=auth("admin"),
    )
    assert resposta.status_code == 201, resposta.text
    corretiva = resposta.json()

    # Documento novo, não uma reescrita do antigo.
    assert corretiva["id"] != case["document"]["id"]
    assert corretiva["corrects_document_id"] == case["document"]["id"]
    assert corretiva["status"] == "atribuido"
    assert corretiva["predecessor_document_id"] == case["document"]["id"]

    # A autoria clínica continua sendo da médica — nunca do admin.
    assignment = corretiva["assignment"]
    assert assignment["physician_profile_id"] == case["profile"]["id"]

    # O exame é o MESMO (nenhum exame/paciente novo nasceu).
    predecessor = db.get(ReportDocument, case["document"]["id"])
    db.refresh(predecessor)
    assert predecessor.spirometry_exam_id == db.get(
        ReportDocument, corretiva["id"]
    ).spirometry_exam_id
    total_exames = db.execute(select(SpirometryExam)).scalars().all()
    assert len(total_exames) == 1

    # O predecessor NÃO foi tocado: mesmo status, mesma versão corrente,
    # mesmo código de validação.
    assert predecessor.status == "liberado"
    assert predecessor.validation_code == liberado["validation_code"]
    assert predecessor.current_version_id == liberado["released_version_id"]

    # A médica volta a enxergar o exame na fila (nova atribuição ativa).
    nova_atribuicao = db.execute(
        select(ReportAssignment).where(
            ReportAssignment.report_document_id == corretiva["id"],
            ReportAssignment.active.is_(True),
        )
    ).scalar_one()
    assert nova_atribuicao.physician_profile_id == case["profile"]["id"]

    # Auditoria: registra quem, quando, motivo, estado anterior e o
    # documento afetado — no PREDECESSOR (é ele que foi "devolvido").
    evento = db.execute(
        select(AuditLog).where(AuditLog.acao == "laudo_devolvido_para_correcao")
    ).scalar_one()
    assert evento.entidade_id == case["document"]["id"]
    assert evento.detalhes["status_anterior"] == "liberado"
    assert evento.detalhes["reason_code"] == "clinical_correction"
    assert evento.detalhes["corrective_document_id"] == corretiva["id"]
    assert evento.detalhes["retornado_por_admin"] is True


def test_medica_corrige_e_conclui_normalmente_a_corretiva(client, auth, case, db):
    _liberado(client, case)
    corretiva = client.post(
        f"/api/v1/laudos/{case['document']['id']}/retornar-para-correcao",
        json={"reason_code": "clinical_correction"},
        headers=auth("admin"),
    ).json()

    novo_caso = {**case, "document": {"id": corretiva["id"]}}
    preview = _preview(
        client, novo_caso, conclusion_code="NORMAL", bronchodilator_code="BD_NAO_REALIZADO"
    )
    assert preview.status_code == 200, preview.text
    liberado_de_novo = _release(client, novo_caso, preview.json())
    assert liberado_de_novo.status_code == 200, liberado_de_novo.text
    assert liberado_de_novo.json()["status"] == "liberado"


# ------------------------------------------------------------------- RBAC


def test_medica_nao_aciona_a_devolucao_administrativa(client, auth, case):
    _liberado(client, case)
    resposta = client.post(
        f"/api/v1/laudos/{case['document']['id']}/retornar-para-correcao",
        json={"reason_code": "clinical_correction"},
        headers=case["doctor_auth"],
    )
    assert resposta.status_code == 403, resposta.text


def test_operacional_sem_papel_admin_nao_aciona(client, auth, case):
    _liberado(client, case)
    resposta = client.post(
        f"/api/v1/laudos/{case['document']['id']}/retornar-para-correcao",
        json={"reason_code": "clinical_correction"},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 403, resposta.text


def test_outro_admin_tambem_consegue_devolver(client, auth, db, case):
    """Não é uma identidade fixa ("Luiz"): qualquer conta com papel admin."""

    ensure_roles_exist(db)
    outro = User(
        email="outro-admin-m2612@teste.local",
        nome="TESTE APAGAR Outro Admin",
        password_hash=hash_password("senha-admin-sintetica-123"),
    )
    outro.roles.append(get_role(db, "admin"))
    db.add(outro)
    db.commit()
    outro_auth = {"Authorization": f"Bearer {issue_token(outro.id, outro.password_hash)}"}

    _liberado(client, case)
    resposta = client.post(
        f"/api/v1/laudos/{case['document']['id']}/retornar-para-correcao",
        json={"reason_code": "clinical_correction"},
        headers=outro_auth,
    )
    assert resposta.status_code == 201, resposta.text


# --------------------------------------------------------- estados aceitos


def test_recusa_devolver_laudo_ainda_em_elaboracao(client, auth, case):
    _preview(client, case)  # ainda não libera
    resposta = client.post(
        f"/api/v1/laudos/{case['document']['id']}/retornar-para-correcao",
        json={"reason_code": "clinical_correction"},
        headers=auth("admin"),
    )
    assert resposta.status_code == 409, resposta.text
    assert resposta.json()["erro"]["codigo"] == "laudo_nao_fechado"


def test_nao_duplica_corretiva_do_mesmo_predecessor(client, auth, case):
    _liberado(client, case)
    primeira = client.post(
        f"/api/v1/laudos/{case['document']['id']}/retornar-para-correcao",
        json={"reason_code": "clinical_correction"},
        headers=auth("admin"),
    )
    assert primeira.status_code == 201, primeira.text

    segunda = client.post(
        f"/api/v1/laudos/{case['document']['id']}/retornar-para-correcao",
        json={"reason_code": "identification_correction"},
        headers=auth("admin"),
    )
    assert segunda.status_code == 409, segunda.text
    assert segunda.json()["erro"]["codigo"] == "laudo_ja_possui_corretiva"


# ------------------------------------------------ acesso do paciente ao PDF


def test_devolucao_revoga_o_acesso_do_paciente_ao_pdf_anterior(
    client, auth, db, portal_ligado
):
    """O caso mais importante: sem isto, o link já enviado ao paciente
    continuaria servindo o PDF superado, sem nenhum aviso."""

    m29d = _load("test_m25_29d_fluxo_conclusao_assinatura.py")
    m264 = _load("test_m26_4_portal_resultados.py")

    from app.models import RESULTADO_REVOGADO

    caso = m264._montar_caso(
        client, auth, db, nome="TESTE APAGAR Paciente M26.12", suffix="9912"
    )
    acesso_antes = m264._acesso(db, caso["document_id"])
    assert acesso_antes is not None
    assert acesso_antes.status != RESULTADO_REVOGADO

    resposta = client.post(
        f"/api/v1/laudos/{caso['document_id']}/retornar-para-correcao",
        json={"reason_code": "clinical_correction"},
        headers=auth("admin"),
    )
    assert resposta.status_code == 201, resposta.text
    assert "aviso" not in resposta.json()

    acesso_depois = m264._acesso(db, caso["document_id"])
    assert acesso_depois.status == RESULTADO_REVOGADO
    assert acesso_depois.revoked_motivo is not None

    # O PDF assinado em si — versão e registro de assinatura externa —
    # permanece intocado; só o ACESSO do paciente foi fechado.
    assinado_ainda_existe = db.get(
        ReportDocumentVersion, acesso_depois.report_document_version_id
    )
    assert assinado_ainda_existe is not None
