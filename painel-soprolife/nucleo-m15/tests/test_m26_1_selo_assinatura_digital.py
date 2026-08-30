"""M26.1 — o modelo do laudo parou de nomear o aparelho e passou a nomear o ato.

Duas correções editoriais no template, sem retroatividade.

**A frase do PDF técnico.** Ela dizia "PDF técnico do equipamento (MIR)".
A frota deixou de ser de um fabricante só — há exames feitos no KOKO —, e o
nome do aparelho nunca foi o que a frase precisa afirmar: ela existe para
dizer que o traçado está em OUTRO arquivo, intacto, com download próprio. O
modelo, quando importa, consta do próprio PDF técnico, que é o documento que
sabe qual é.

**O selo esquerdo.** Dizia "CONCLUÍDO / PELA MÉDICA" — o que já está escrito
duas vezes logo abaixo, no rótulo da data e na declaração do rodapé. Agora
diz "ASSINATURA / DIGITAL" sobre a marca do provedor, na composição de um
selo de certificação: dizeres em cima, marca embaixo, tudo contido pelo anel.

A restrição da M25.21 continua sendo o que decide o texto: **um carimbo
impresso não pode afirmar estado que expira.** A médica baixa este mesmo
arquivo, assina por fora e devolve o PDF assinado; o desenho do selo continua
como saiu daqui. "ASSINATURA DIGITAL" descreve a natureza do documento e
permanece verdadeiro depois que a assinatura entra na camada PDF — ao
contrário de "AGUARDANDO ASSINATURA", que a M25.21 teve de remover. E nada
aqui afirma ICP-Brasil: essa afirmação continua exclusiva do ramo qualificado,
escolhido só com prova criptográfica gravada.

Somente dados sintéticos.
"""

from __future__ import annotations

import io
import pathlib
from datetime import date, datetime, timezone

import pytest

from app.services import report_native_pdf as gerador
from app.services.report_native_pdf import (
    SEAL_RADIUS,
    SIGNATURE_KIND_QUALIFIED_ICP,
    ExamBlock,
    LocationBlock,
    NativeReportContent,
    PatientBlock,
    PhysicianBlock,
    build_native_report_pdf,
)

# O anel interno, que é o limite real: nada pode cruzá-lo.
INNER_RADIUS = SEAL_RADIUS - 4.2


def _conteudo(**overrides) -> NativeReportContent:
    base = {
        "document_code": "LAU-000021",
        "version_number": 1,
        "patient": PatientBlock(
            full_name="TESTE APAGAR Paciente M26.1",
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


def _imagens(dados: bytes) -> list:
    from pypdf import PdfReader

    leitor = PdfReader(io.BytesIO(dados))
    return [imagem for pagina in leitor.pages for imagem in pagina.images]


@pytest.fixture()
def laudo() -> bytes:
    return build_native_report_pdf(_conteudo())


# ===================================================================
# 1. A frase do PDF técnico não nomeia mais o aparelho
# ===================================================================


def test_a_frase_sai_exatamente_como_especificada(laudo):
    esperado = (
        "Traçado e medidas originais constam do PDF técnico do equipamento — "
        "documento SEPARADO deste laudo, inalterado, com download próprio."
    )
    assert gerador.EQUIPMENT_SEPARATE_NOTICE == esperado
    # E chega ao papel. O texto é quebrado em linhas pelo compositor, então a
    # asserção é sobre os pedaços que sobrevivem à extração.
    texto = _texto(laudo)
    assert "PDF técnico do equipamento" in texto
    assert "documento SEPARADO deste laudo" in texto
    assert "com download próprio" in texto


def test_nenhum_fabricante_e_nomeado_na_frase(laudo):
    """Nem o antigo nem o novo.

    Trocar "(MIR)" por "(MIR/KOKO)" seria criar a mesma dívida com um item a
    mais: a frase voltaria a quebrar na próxima marca que entrar na frota.
    """

    alto = _texto(laudo).upper()
    assert "MIR" not in alto
    assert "KOKO" not in alto


def test_a_frase_nao_nomeia_aparelho_nem_no_codigo():
    """A constante é a única fonte da frase, e ela não cita fabricante."""

    assert "MIR" not in gerador.EQUIPMENT_SEPARATE_NOTICE.upper()
    assert "KOKO" not in gerador.EQUIPMENT_SEPARATE_NOTICE.upper()


# ===================================================================
# 2. O selo esquerdo: dizeres em cima, marca embaixo
# ===================================================================


def test_o_selo_diz_assinatura_digital(laudo):
    """As duas linhas GRUDADAS são a assinatura do carimbo.

    "assinatura digital" aparece duas vezes no rodapé (onde conferir a
    assinatura de verdade, e a negativa sobre ICP-Brasil). Só de dentro do
    anel a extração devolve "ASSINATURA" seguida imediatamente de "DIGITAL".
    """

    assert "ASSINATURA\nDIGITAL" in _texto(laudo).upper()


def test_o_texto_antigo_saiu_do_anel(laudo):
    """"PELA MÉDICA" sobrou uma vez só — a do rodapé, que é texto do
    documento e não carimbo."""

    assert _texto(laudo).upper().count("PELA MÉDICA") == 1


def test_a_marca_vidas_entra_no_pdf(laudo):
    """O laudo liberado embute DUAS imagens: a marca SoproLife do cabeçalho
    e a marca do provedor dentro do selo. A prévia embute uma só."""

    assert gerador.DIGITAL_SIGNATURE_LOGO_PATH.is_file(), (
        "o asset da marca precisa estar versionado no projeto"
    )
    assert len(_imagens(laudo)) == 2
    previa = build_native_report_pdf(_conteudo(released=False))
    assert len(_imagens(previa)) == 1


def test_a_marca_cabe_inteira_dentro_do_anel(monkeypatch):
    """Geométrico, e não visual: os QUATRO cantos do retângulo desenhado
    ficam dentro do anel interno.

    Um retângulo inscrito num círculo não é limitado pelo diâmetro, e sim
    pela corda da altura em que está. Estimar isso à mão foi o que já fez
    texto vazar do selo antes (M25.5). Aqui a conta é conferida no desenho de
    verdade: interceptam-se `circle` e `drawImage` para pegar o centro real
    do anel e o retângulo real da marca, e mede-se a distância entre eles.
    """

    aneis: list[tuple[float, float, float]] = []
    desenhos: list[tuple[float, float, float, float]] = []
    circulo_original = gerador.canvas.Canvas.circle
    imagem_original = gerador.canvas.Canvas.drawImage

    def espiao_circulo(self, x, y, r, **kwargs):
        aneis.append((x, y, r))
        return circulo_original(self, x, y, r, **kwargs)

    def espiao_imagem(self, image, x, y, width=None, height=None, **kwargs):
        desenhos.append((x, y, width, height))
        return imagem_original(self, image, x, y, width=width, height=height,
                               **kwargs)

    monkeypatch.setattr(gerador.canvas.Canvas, "circle", espiao_circulo)
    monkeypatch.setattr(gerador.canvas.Canvas, "drawImage", espiao_imagem)
    build_native_report_pdf(_conteudo())

    # O anel externo do selo esquerdo: raio SEAL_RADIUS, na coluna esquerda.
    centro_x = gerador.MARGIN_X + gerador.SEAL_CELL_WIDTH / 2
    selo = [a for a in aneis
            if abs(a[2] - SEAL_RADIUS) < 0.01 and abs(a[0] - centro_x) < 0.01]
    assert len(selo) == 1, f"esperado 1 selo esquerdo, veio {len(selo)}"
    cx, cy, _ = selo[0]

    # A marca do selo é a única imagem mais estreita que o anel — a do
    # cabeçalho ocupa uma faixa muito maior.
    do_selo = [d for d in desenhos
               if d[2] is not None and d[2] <= SEAL_RADIUS * 2]
    assert len(do_selo) == 1, f"esperado 1 marca no selo, veio {len(do_selo)}"
    x, y, largura, altura = do_selo[0]

    # A marca está de fato DENTRO deste selo, e não em outro lugar da página.
    assert abs((x + largura / 2) - cx) < 0.01
    assert abs((y + altura / 2) - cy) < SEAL_RADIUS

    for canto_x in (x, x + largura):
        for canto_y in (y, y + altura):
            distancia = ((canto_x - cx) ** 2 + (canto_y - cy) ** 2) ** 0.5
            assert distancia < INNER_RADIUS, (
                f"canto ({canto_x:.1f}, {canto_y:.1f}) a {distancia:.1f}pt do "
                f"centro do selo, além do anel interno ({INNER_RADIUS:.1f}pt)"
            )
    # E a folga é real, não marginal: sobram pelo menos 2pt entre o canto
    # mais distante e o anel interno.
    mais_longe = max(
        ((cx_ - cx) ** 2 + (cy_ - cy) ** 2) ** 0.5
        for cx_ in (x, x + largura) for cy_ in (y, y + altura)
    )
    assert INNER_RADIUS - mais_longe >= 2.0


def test_a_marca_preserva_a_proporcao(monkeypatch):
    """A marca não pode ser esticada para preencher a faixa."""

    from PIL import Image

    with Image.open(gerador.DIGITAL_SIGNATURE_LOGO_PATH) as arquivo:
        proporcao = arquivo.width / arquivo.height

    desenhos: list[tuple] = []
    original = gerador.canvas.Canvas.drawImage

    def espiao(self, image, x, y, width=None, height=None, **kwargs):
        desenhos.append((width, height))
        return original(self, image, x, y, width=width, height=height,
                        **kwargs)

    monkeypatch.setattr(gerador.canvas.Canvas, "drawImage", espiao)
    build_native_report_pdf(_conteudo())

    largura, altura = [d for d in desenhos
                       if d[0] is not None and d[0] <= SEAL_RADIUS * 2][0]
    assert altura > 0
    assert abs(largura / altura - proporcao) < 0.05


# ===================================================================
# 3. O que a mudança não podia levar junto
# ===================================================================


def test_o_selo_continua_sem_afirmar_icp_brasil(laudo):
    """A única menção a ICP-Brasil é a NEGATIVA do rodapé."""

    alto = _texto(laudo).upper()
    assert alto.count("ICP-BRASIL") == 1
    assert "não constitui" in _texto(laudo)
    assert "PADRÃO PADES" not in alto
    assert "ASSINADO DIGITALMENTE" not in alto


def test_o_selo_continua_sem_carimbar_estado(laudo):
    """M25.21 — a razão de "AGUARDANDO ASSINATURA" ter saído vale igual para
    o texto novo: ele sobreviveria à assinatura aplicada sobre este arquivo."""

    assert "AGUARDANDO" not in _texto(laudo).upper()


def test_o_ramo_qualificado_nao_foi_tocado():
    """M26.1 mexeu só no selo institucional. Quando houver prova gravada, o
    carimbo volta a declarar ICP-Brasil, sem marca de provedor."""

    dados = build_native_report_pdf(
        _conteudo(signature_kind=SIGNATURE_KIND_QUALIFIED_ICP)
    )
    alto = _texto(dados).upper()
    assert "ASSINADO" in alto and "DIGITALMENTE" in alto
    assert "PADRÃO PADES" in alto
    assert "ICP-BRASIL" in alto
    # Sem as duas linhas do selo institucional e sem a marca do provedor.
    assert "ASSINATURA\nDIGITAL" not in alto
    assert len(_imagens(dados)) == 1


def test_a_identificacao_do_laudo_continua_impressa(laudo):
    """Os identificadores pelos quais a M25.20 reconhece o PDF que volta
    assinado não dependem do selo — e continuam onde estavam."""

    texto = _texto(laudo)
    assert "LAU-000021" in texto
    assert "ESP-000021" in texto
    assert "TESTE APAGAR Paciente M26.1" in texto
    assert "ABCDEFGH2345" in texto
    assert "SOPROLIFE" in texto.upper()


# ===================================================================
# 4. O asset é opcional por construção
# ===================================================================


def test_sem_o_asset_o_laudo_continua_saindo(monkeypatch, tmp_path):
    """Nenhuma parte do fluxo clínico pode depender de um PNG estar no lugar.

    Sem a marca, o selo se recompõe com as duas linhas centralizadas no anel
    — o laudo sai completo, e só o desenho muda.
    """

    ausente = tmp_path / "nao-existe.png"
    monkeypatch.setattr(gerador, "DIGITAL_SIGNATURE_LOGO_PATH", ausente)
    monkeypatch.setattr(gerador, "_logo_cache", {})

    dados = build_native_report_pdf(_conteudo())
    alto = _texto(dados).upper()
    assert "ASSINATURA\nDIGITAL" in alto
    assert "LAU-000021" in _texto(dados)
    # Só a marca do cabeçalho sobrou.
    assert len(_imagens(dados)) == 1


def test_o_asset_e_pequeno_e_versionado():
    """Ele entra em TODO laudo emitido; um PNG gordo vira peso em cada PDF."""

    caminho = gerador.DIGITAL_SIGNATURE_LOGO_PATH
    assert caminho.is_file()
    assert caminho.stat().st_size < 40_000, "asset grande demais para o selo"
    # E vive dentro do projeto, não num caminho absoluto de máquina.
    raiz = pathlib.Path(__file__).resolve().parents[3]
    assert caminho.resolve().is_relative_to(raiz)
