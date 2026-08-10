"""M25.19 — o catálogo técnico sai da tela, não do sistema.

A tela de "Laudos de espirometria" exibia, dentro da administração restrita,
um bloco "Catálogo versionado / Templates clínicos" com um card por template
do servidor — inclusive os seis placeholders `*_PROVISORIO` carimbados como
impróprios para produção. Aquilo nunca foi operação: a médica conclui pelas
siglas clínicas definitivas da bancada (M25.2), e o catálogo de templates só
alimenta o fluxo legado de anotação sobre o PDF da MIR (M24C).

Estes testes separam as duas metades da mudança, porque confundi-las seria o
erro caro:

1. **A vitrine sumiu.** Nenhuma string, classe, handler ou chamada do bloco
   administrativo de catálogo sobrou no painel.
2. **O catálogo NÃO sumiu.** Os seis provisórios continuam no banco, a
   leitura administrativa continua restrita a admin e a criação de nova
   revisão imutável continua funcionando pela API.

O terceiro grupo é regressão: a bancada clínica, a M25.17 e a M25.18 não
podem ter sido tocadas por uma limpeza de UX.

Somente dados sintéticos.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.config import get_settings
from app.services.report_catalog import PROVISIONAL_CODES

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_JS = (PANEL_ROOT / "js" / "report-workflow.js").read_text()
WORKFLOW_CSS = (PANEL_ROOT / "css" / "report-workflow.css").read_text()


@pytest.fixture(autouse=True)
def _reports_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv(
        "M15_AUTH_SECRET",
        "m25-19-limpeza-catalogo-secret-only-for-tests-0123456789",
    )
    monkeypatch.delenv(
        "M15_REPORTS_TEST_ALLOW_PROVISIONAL_TEMPLATES", raising=False
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --------------------------------------------------------- 1. a vitrine sumiu


def test_catalogo_versionado_nao_aparece_mais_na_interface():
    assert "Catálogo versionado" not in WORKFLOW_JS
    assert "Templates clínicos" not in WORKFLOW_JS
    assert "templateAdminTitle" not in WORKFLOW_JS
    assert "renderTemplateAdmin" not in WORKFLOW_JS


def test_nenhum_card_provisorio_e_renderizado():
    # O carimbo vermelho que aparecia em cada card do catálogo.
    assert "PROVISÓRIO — NÃO UTILIZAR EM PRODUÇÃO" not in WORKFLOW_JS
    assert "NÃO UTILIZAR EM PRODUÇÃO" not in WORKFLOW_JS
    assert "data-report-admin-template" not in WORKFLOW_JS
    assert "report-admin-template" not in WORKFLOW_JS
    # Sem o `_PROVISORIO` na tela, os códigos dos placeholders não têm por
    # onde chegar ao DOM da página operacional.
    assert "_PROVISORIO" not in WORKFLOW_JS


def test_edicao_de_template_nao_tem_mais_formulario_na_tela():
    assert "reportTemplateRevisionForm" not in WORKFLOW_JS
    assert "saveTemplateRevision" not in WORKFLOW_JS
    assert "Criar nova revisão" not in WORKFLOW_JS
    assert "adminTemplates" not in WORKFLOW_JS
    assert "selectedAdminTemplateId" not in WORKFLOW_JS
    # A carga do catálogo administrativo saiu junto: sem tela que o consuma,
    # a chamada era tráfego para ninguém ver.
    assert "catalog=admin" not in WORKFLOW_JS


def test_css_do_catalogo_saiu_sem_deixar_regra_orfa():
    assert ".report-admin-template" not in WORKFLOW_CSS
    assert ".report-template-revision-form" not in WORKFLOW_CSS
    assert ".report-template-state" not in WORKFLOW_CSS


def test_administracao_restrita_nao_deixa_coluna_vazia():
    # Duas colunas com um painel só = metade da faixa em branco, que é
    # exatamente o que a remoção deveria eliminar.
    bloco = re.search(
        r"\.report-admin-shell\s*\{(.*?)\}", WORKFLOW_CSS, re.S
    )
    assert bloco, "regra .report-admin-shell sumiu do CSS"
    assert "repeat(2," not in bloco.group(1)
    assert "minmax(0, 1fr)" in bloco.group(1)
    assert ".report-admin-shell > .report-panel" in WORKFLOW_CSS


# ----------------------------------------- 2. contas médicas continuam de pé


def test_contas_medicas_continuam_na_administracao_restrita():
    assert "Administração restrita — contas médicas" in WORKFLOW_JS
    assert "Administração restrita — contas médicas e catálogo técnico" \
        not in WORKFLOW_JS
    assert "renderProfileAdmin()" in WORKFLOW_JS
    assert 'id="physicianAdminTitle">Contas médicas' in WORKFLOW_JS
    assert 'for="reportAdminUser">Usuário existente' in WORKFLOW_JS
    assert "reportPhysicianAdminForm" in WORKFLOW_JS
    # A rubrica (M25.4) mora dentro de contas médicas e não pode ter ido
    # embora junto com o catálogo.
    assert "renderSignatureAssetAdmin(profile)" in WORKFLOW_JS


def test_administracao_restrita_continua_exclusiva_de_admin():
    assert 'if (can("admin")) blocks.push(renderAdminWorkspace());' \
        in WORKFLOW_JS
    workspace = re.search(
        r"function renderAdminWorkspace\(\)\s*\{(.*?)\n  \}", WORKFLOW_JS, re.S
    )
    assert workspace
    # Nenhuma senha e nenhum caminho de criação de usuário nesta tela.
    assert "senha" not in workspace.group(1).lower()


def test_catalogo_administrativo_continua_restrito_no_servidor(client, auth):
    negado = client.get(
        "/api/v1/laudos/templates?catalog=admin", headers=auth("operacional")
    )
    assert negado.status_code == 403
    assert negado.json()["erro"]["codigo"] == "permissao_insuficiente"


def test_templates_provisorios_continuam_no_banco(client, auth):
    catalogo = client.get(
        "/api/v1/laudos/templates?catalog=admin", headers=auth("admin")
    )
    assert catalogo.status_code == 200, catalogo.text
    codigos = {item["codigo"] for item in catalogo.json()}
    assert PROVISIONAL_CODES <= codigos
    provisorios = [
        item for item in catalogo.json() if item["codigo"] in PROVISIONAL_CODES
    ]
    assert len(provisorios) == 6
    for item in provisorios:
        assert item["status"] == "draft"
        assert item["clinically_approved"] is False


def test_nova_revisao_imutavel_continua_possivel_pela_api(client, auth):
    catalogo = client.get(
        "/api/v1/laudos/templates?catalog=admin", headers=auth("admin")
    ).json()
    alvo = next(
        item for item in catalogo if item["codigo"] == "NORMAL_PROVISORIO"
    )
    revisao = client.patch(
        f"/api/v1/laudos/templates/{alvo['id']}",
        json={
            "titulo": alvo["titulo"],
            "texto_tooltip": "TESTE - APAGAR tooltip sintético",
            "texto_completo": (
                "TESTE - APAGAR: texto controlado sintético sem validade "
                "clínica."
            ),
            "ativo": True,
            "status": "draft",
            "clinically_approved": False,
        },
        headers=auth("admin"),
    )
    assert revisao.status_code == 201, revisao.text
    assert revisao.json()["versao"] == alvo["versao"] + 1
    assert revisao.json()["id"] != alvo["id"]


# ------------------------------------ 3. a bancada clínica não foi arranhada


def test_conclusoes_da_medica_continuam_intactas():
    assert "renderConclusionPicker()" in WORKFLOW_JS
    assert "catalogo-conclusoes" in WORKFLOW_JS
    assert "data-report-conclusion" in WORKFLOW_JS
    assert "PERSONALIZADO" in WORKFLOW_JS
    assert "data-report-bd" in WORKFLOW_JS
    assert "complementos_bd" in WORKFLOW_JS
    # A bancada lado a lado (PDF da MIR à esquerda, laudo à direita).
    assert "report-clinical-split" in WORKFLOW_JS
    assert "reportNativeForm" in WORKFLOW_JS
    assert "previewNativeReport" in WORKFLOW_JS


def test_catalogo_clinico_de_conclusoes_permanece_completo():
    from app.services.report_conclusions import catalog_payload

    catalogo = catalog_payload(has_post_bd=True)
    codigos = {item["codigo"] for item in catalogo["conclusoes"]}
    # 17 conclusões clínicas + PERSONALIZADO, que é o escape para texto livre
    # e não uma conclusão do catálogo.
    clinicas = [
        item for item in catalogo["conclusoes"] if not item["personalizado"]
    ]
    assert len(clinicas) == 17
    assert len(catalogo["conclusoes"]) == 18
    assert "DVO_LEVE" in codigos
    assert "PERSONALIZADO" in codigos
    rotulos = {item["rotulo"] for item in catalogo["conclusoes"]}
    assert "DVO Leve" in rotulos
    bd = {item["rotulo"] for item in catalogo["complementos_bd"]}
    assert len(catalogo["complementos_bd"]) == 5
    assert "RBD+" in bd


def test_m25_17_e_m25_18_nao_regridem():
    # M25.17 — fila e download pelo nome do paciente, origem derivada.
    assert "report-item-name" in WORKFLOW_JS
    assert 'anchor.download = blob.nomeSugerido || ""' in WORKFLOW_JS
    assert "origem_derivada" in WORKFLOW_JS
    # M25.18 — conclusão do laudo com assinatura externa, sem faixa de piloto.
    assert "Concluir laudo" in WORKFLOW_JS
    assert "Assinar e liberar laudo" not in WORKFLOW_JS
    assert 'class="report-pilot-warning"' not in WORKFLOW_JS
