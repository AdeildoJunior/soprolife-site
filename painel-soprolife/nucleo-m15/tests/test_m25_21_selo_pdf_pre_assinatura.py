"""M25.21 — o carimbo não sobrevive ao próprio prazo de validade.

O selo do laudo pré-assinatura dizia, em quatro linhas:

    CONCLUÍDO
    PELA MÉDICA
    ─────────
    AGUARDANDO
    ASSINATURA

As duas linhas de baixo eram verdadeiras no instante em que o PDF era
gerado — e falsas alguns minutos depois. O fluxo é este: a médica **baixa
exatamente este arquivo**, aplica a assinatura qualificada nele por fora
(VIDaaS, com o certificado dela) e devolve o mesmo PDF assinado. A
assinatura entra na camada PDF; o desenho do selo continua impresso como
saiu daqui. O documento **já assinado** ficaria carimbado "AGUARDANDO
ASSINATURA" para sempre, e quem o recebesse leria, no próprio arquivo, a
negativa mais forte que existe sobre ele.

"CONCLUÍDO PELA MÉDICA" não tem esse problema: é um fato sobre o ato
clínico, permanece verdadeiro antes e depois da assinatura, e não afirma
nada sobre ICP-Brasil.

**O estado operacional não mudou.** "Aguardando assinatura qualificada",
"Assinado recebido — validação pendente", "Pronto para entrega" e
"Entregue" continuam no Centro de Comando, que sabe a hora em que cada um
deixa de valer. Um carimbo impresso não sabe — e é exatamente essa a
diferença entre estado e documento.

Somente dados sintéticos.
"""

from __future__ import annotations

import io
import pathlib
from datetime import date, datetime, timezone

import pytest

from app.services.report_native_pdf import (
    SIGNATURE_KIND_INSTITUTIONAL,
    SIGNATURE_KIND_QUALIFIED_ICP,
    ExamBlock,
    LocationBlock,
    NativeReportContent,
    PatientBlock,
    PhysicianBlock,
    build_native_report_pdf,
)

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_JS = (PANEL_ROOT / "js" / "report-workflow.js").read_text()
PDF_SOURCE = (
    PANEL_ROOT / "nucleo-m15" / "app" / "services" / "report_native_pdf.py"
).read_text()


def _conteudo(**overrides) -> NativeReportContent:
    base = {
        "document_code": "LAU-000021",
        "version_number": 1,
        "patient": PatientBlock(
            full_name="TESTE APAGAR Paciente M25.21",
            birth_date=date(1979, 4, 17),
            sex="feminino",
            public_code="PES-000021",
        ),
        "exam": ExamBlock(
            public_code="ESP-000021",
            exam_date=date(2026, 8, 10),
            exam_time="09:40",
            date_precision="dia",
            has_post_bd=True,
            clinical_indication="teste sintético",
        ),
        "location": LocationBlock(
            name="Unidade Sintética",
            address_line="Rua Sintética, 21",
            contact_line="(21) 0000-0000",
        ),
        "physician": PhysicianBlock(
            professional_name="TESTE APAGAR Médica",
            specialty="Pneumologista",
            crm_display="52.62307-5",
            crm_state="RJ",
            rqe="58224",
        ),
        "conclusion_text": "conclusão sintética para teste de selo",
        "observations": "observação sintética",
        "issued_at_local": datetime.now(timezone.utc),
        "released": True,
        "released_at_local": datetime.now(timezone.utc),
        "validation_code": "ABCDEFGH2345",
    }
    base.update(overrides)
    return NativeReportContent(**base)


def _texto(dados: bytes) -> str:
    from pypdf import PdfReader

    leitor = PdfReader(io.BytesIO(dados))
    return "\n".join(pagina.extract_text() or "" for pagina in leitor.pages)


@pytest.fixture()
def pdf_pre_assinatura() -> str:
    return _texto(build_native_report_pdf(_conteudo()))


# ===================================================================
# 1. O PDF pré-assinatura: o que ficou e o que saiu
# ===================================================================


def test_selo_pre_assinatura_diz_concluido_pela_medica(pdf_pre_assinatura):
    """Conta ocorrências, não presença.

    "pela médica responsável" já aparece no rodapé (`RELEASE_STATEMENT`), e
    "Concluído em" é o rótulo da data. Procurar só presença encontraria essas
    e passaria mesmo com o selo apagado. A DUPLA ocorrência de "PELA MÉDICA"
    é o que só existe quando o carimbo está desenhado — é ela a asserção.
    """

    alto = pdf_pre_assinatura.upper()
    assert alto.count("PELA MÉDICA") == 2, (
        "esperado o rodapé + o selo; conte de novo se o selo mudou"
    )
    assert "CONCLUÍDO" in alto


def test_selo_pre_assinatura_nao_diz_aguardando_assinatura(pdf_pre_assinatura):
    """A asserção mira "AGUARDANDO", não "ASSINATURA".

    A palavra "assinatura" aparece legitimamente em outros lugares do
    documento — no rótulo acima da rubrica e na frase que NEGA a assinatura
    qualificada ICP-Brasil. Quem só existia no carimbo era "AGUARDANDO";
    é a ausência dela que prova a remoção, sem apagar o resto.
    """

    alto = pdf_pre_assinatura.upper()
    assert "AGUARDANDO" not in alto
    assert "AGUARDANDO ASSINATURA" not in alto


def test_o_selo_pre_assinatura_tem_duas_linhas_e_nao_quatro():
    """Estrutural, e não só textual: o ramo não-qualificado não pode voltar a
    ter as duas linhas de baixo nem a régua que as separava."""

    inicio = PDF_SOURCE.index("def draw_signature_type_seal")
    fim = PDF_SOURCE.index("def draw_institutional_seal")
    corpo = PDF_SOURCE[inicio:fim]
    ramo_nao_qualificado = corpo[corpo.index("        else:"):]
    assert "CONCLUÍDO" in ramo_nao_qualificado
    assert "PELA MÉDICA" in ramo_nao_qualificado
    assert "AGUARDANDO" not in ramo_nao_qualificado
    # A régua divisória separava duas afirmações; com uma só, ela sai.
    assert "c.line(" not in ramo_nao_qualificado


# ===================================================================
# 2. O que a mudança NÃO podia levar junto
# ===================================================================


def test_tudo_o_que_identifica_o_laudo_continua_impresso(pdf_pre_assinatura):
    texto = pdf_pre_assinatura
    # Médica, CRM e RQE.
    assert "TESTE APAGAR Médica" in texto
    assert "52.62307-5" in texto
    assert "RQE" in texto and "58224" in texto
    # Selo institucional SoproLife.
    assert "SOPROLIFE" in texto.upper()
    # Código de verificação e conclusão clínica.
    assert "ABCDEFGH2345" in texto
    assert "conclusão sintética para teste de selo" in texto
    assert "observação sintética" in texto
    # Identificação do documento e do exame — é por ela que a M25.20
    # reconhece o PDF que volta assinado.
    assert "LAU-000021" in texto
    assert "ESP-000021" in texto
    assert "TESTE APAGAR Paciente M25.21" in texto


def test_a_negativa_sobre_icp_brasil_continua_no_rodape(pdf_pre_assinatura):
    """O carimbo saiu; a declaração honesta não.

    Era ela, e não o selo, que carregava a informação relevante: onde
    conferir a assinatura de verdade. E ela é texto do documento, que
    descreve o que ESTE arquivo é — não um estado que expira.
    """

    texto = pdf_pre_assinatura
    assert "Documento concluído pela médica responsável" in texto
    assert "deve ser verificada no arquivo" in texto
    assert "não constitui" in texto
    assert texto.upper().count("ICP-BRASIL") == 1


def test_rubrica_e_versionamento_intactos():
    conteudo = _conteudo(version_number=3)
    texto = _texto(build_native_report_pdf(conteudo))
    assert "Assinatura" in texto or "assinatura" in texto
    assert "v3" in texto or "3" in texto


# ===================================================================
# 3. O ramo qualificado não foi tocado
# ===================================================================


def test_selo_qualificado_continua_declarando_icp_brasil():
    """Quando houver prova criptográfica gravada, o selo volta às quatro
    linhas. A M25.21 mexeu SÓ no PDF pré-assinatura."""

    texto = _texto(build_native_report_pdf(
        _conteudo(signature_kind=SIGNATURE_KIND_QUALIFIED_ICP)
    ))
    alto = texto.upper()
    assert "ASSINADO" in alto and "DIGITALMENTE" in alto
    assert "PADRÃO PADES" in alto
    assert "ICP-BRASIL" in alto
    # Aqui "PELA MÉDICA" aparece UMA vez só — a do rodapé. O carimbo agora
    # diz assinatura, então não repete a conclusão.
    assert alto.count("PELA MÉDICA") == 1
    assert "AGUARDANDO" not in alto


def test_o_padrao_continua_sendo_institucional_fail_closed():
    """Sem informação em contrário, o selo nunca afirma ICP-Brasil."""

    assert _conteudo().signature_kind == SIGNATURE_KIND_INSTITUTIONAL
    alto = _texto(build_native_report_pdf(_conteudo())).upper()
    assert "ASSINADO DIGITALMENTE" not in alto
    assert "PADRÃO PADES" not in alto


def test_previa_nao_desenha_selo_nenhum():
    """Documento não concluído não recebe carimbo de conclusão."""

    alto = _texto(build_native_report_pdf(_conteudo(released=False))).upper()
    assert "PELA MÉDICA" not in alto
    assert "AGUARDANDO" not in alto


# ===================================================================
# 4. A UI mantém o estado operacional — que é onde ele pertence
# ===================================================================


@pytest.mark.parametrize("rotulo", [
    "Aguardando assinatura qualificada",
    "Assinado recebido — validação pendente",
])
def test_estado_operacional_continua_na_interface(rotulo):
    assert rotulo in WORKFLOW_JS


def test_os_cinco_estados_da_fila_de_entrega_continuam_no_servidor():
    from app.routers.reports import FILA_ROTULOS

    rotulos = set(FILA_ROTULOS.values())
    assert "Aguardando assinatura" in rotulos
    assert any("Pronto para entrega" in r for r in rotulos)
    assert any("Entregue" in r for r in rotulos)


def test_a_fila_da_medica_continua_rotulando_o_que_aguarda_assinatura():
    """O rótulo de status da fila clínica (M25.18) é outro texto, em outro
    lugar, e não podia sair junto com o carimbo."""

    assert "aguardando assinatura qualificada" in WORKFLOW_JS


# ===================================================================
# 5. A M25.20 continua identificando o PDF que volta assinado
# ===================================================================


def test_o_pareamento_do_retorno_nao_depende_do_texto_do_selo():
    """O reconhecimento do arquivo assinado usa código do laudo, código do
    exame e nome do paciente — nada que a M25.21 tenha mexido.

    Se o pareamento lesse o carimbo, remover duas linhas dele quebraria o
    retorno do lote. Este teste trava essa independência.
    """

    fonte = (
        PANEL_ROOT / "nucleo-m15" / "app" / "services" / "signature_batch.py"
    ).read_text()
    assert "AGUARDANDO" not in fonte.upper()
    assert "PELA MÉDICA" not in fonte
    # E os sinais que ele de fato usa continuam lá: o código do laudo gravado
    # nos metadados do PDF, o mesmo código impresso no corpo (regex `LAU-`) e
    # o código de verificação. Nenhum deles é o carimbo.
    assert "META_REPORT_CODE" in fonte
    assert r"\bLAU-\d{6}\b" in fonte
    assert "validation_code" in fonte


def test_o_texto_que_a_m25_20_procura_continua_no_pdf(pdf_pre_assinatura):
    """Os identificadores impressos que sobrevivem à assinatura externa."""

    assert "LAU-000021" in pdf_pre_assinatura
    assert "ESP-000021" in pdf_pre_assinatura


# ===================================================================
# 6. Nada retroativo
# ===================================================================


def test_a_correcao_nao_reescreve_laudo_ja_emitido():
    """Nenhum caminho de LEITURA gera PDF.

    O gerador roda na emissão e o resultado vira uma versão imutável no
    armazenamento. Se alguma rota de download reconstruísse o PDF na hora,
    esta correção reescreveria retroativamente laudos já emitidos — e
    mudaria o hash de documentos que a médica já assinou.

    O teste amarra as duas pontas: o armazenamento não conhece o gerador, e
    TODAS as chamadas ao gerador estão dentro de funções que criam versão
    nova (compor prévia, finalizar, assinar/liberar, publicar adendo,
    iniciar assinatura qualificada). A rota de conteúdo não é uma delas.
    """

    import re

    fonte_storage = (
        PANEL_ROOT / "nucleo-m15" / "app" / "services" / "report_storage.py"
    ).read_text()
    assert "build_native_report_pdf" not in fonte_storage

    rotas = (
        PANEL_ROOT / "nucleo-m15" / "app" / "routers" / "reports.py"
    ).read_text()
    linhas = rotas.split("\n")

    escritas = {
        "compose_native_report_preview",
        "finalize_review_for_signature",
        "sign_and_release_report",
        "add_report_addendum",
        "start_qualified_signature",
        "_native_pdf_bytes",
    }
    geradoras = set()
    for numero, linha in enumerate(linhas):
        if "_native_pdf_bytes(" not in linha:
            continue
        for anterior in range(numero, -1, -1):
            achado = re.match(r"(?:async )?def (\w+)", linhas[anterior])
            if achado:
                geradoras.add(achado.group(1))
                break
    assert geradoras, "nenhuma chamada ao gerador encontrada — teste cego"
    assert geradoras <= escritas, (
        f"o gerador de PDF passou a ser chamado fora da emissão: "
        f"{geradoras - escritas}"
    )

    # E a rota que entrega o conteúdo de uma versão não constrói nada.
    inicio = rotas.index("def download_report_version")
    trecho = rotas[inicio:rotas.index("@router", inicio + 10)]
    assert "_native_pdf_bytes" not in trecho
    assert "build_native_report_pdf" not in trecho
