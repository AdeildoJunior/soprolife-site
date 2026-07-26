"""M23.1 — contrato puro de normalização de categorias financeiras."""

import pytest

from app.finance_categories import (
    CATEGORIA_CONSULTA,
    CATEGORIA_ESPIROMETRIA,
    canonizar_categoria,
    categoria_efetiva_de_receita,
    e_receita_propria_do_componente,
    limpar_categoria,
    mesma_categoria,
)


@pytest.mark.parametrize(
    ("entrada", "esperada"),
    [
        ("espirometria", CATEGORIA_ESPIROMETRIA),
        ("ESPIROMETRIA", CATEGORIA_ESPIROMETRIA),
        ("EsPiRoMeTrIa", CATEGORIA_ESPIROMETRIA),
        (" \t Espirometria \n", CATEGORIA_ESPIROMETRIA),
        ("Ｅｓｐｉｒｏｍｅｔｒｉａ", CATEGORIA_ESPIROMETRIA),
        ("Espirometria\ufe0f", CATEGORIA_ESPIROMETRIA),
        ("Espi\u034frometria", CATEGORIA_ESPIROMETRIA),
        ("ConSulta", CATEGORIA_CONSULTA),
        ("\u200bConsulta\u2060", CATEGORIA_CONSULTA),
        ("Consulta\u180b", CATEGORIA_CONSULTA),
    ],
)
def test_variantes_seguras_recebem_grafia_canonica_exata(entrada, esperada):
    assert canonizar_categoria(entrada) == esperada


def test_categoria_livre_e_preservada_apenas_limpa():
    assert canonizar_categoria("  Receita   avulsa  ") == "Receita avulsa"
    assert canonizar_categoria("Espirométria") == "Espirométria"
    assert not mesma_categoria("Espirométria", CATEGORIA_ESPIROMETRIA)


@pytest.mark.parametrize(
    "entrada", [None, "", " \t\n", "\u200b\u2060", "\ufe0f\u034f\u180b"]
)
def test_ausencia_e_vazio_normalizado_sao_none(entrada):
    assert limpar_categoria(entrada) is None
    assert canonizar_categoria(entrada) is None


@pytest.mark.parametrize(
    ("exam_id", "consultation_id", "esperada"),
    [
        ("exam-1", None, CATEGORIA_ESPIROMETRIA),
        (None, "con-1", CATEGORIA_CONSULTA),
        (None, None, None),
        ("exam-1", "con-1", None),
    ],
)
def test_receita_sem_categoria_so_e_inferida_com_um_vinculo(
    exam_id, consultation_id, esperada
):
    assert categoria_efetiva_de_receita(
        tipo="receita",
        categoria=None,
        spirometry_exam_id=exam_id,
        consultation_id=consultation_id,
    ) == esperada


def test_categoria_explicita_classifica_vinculo_combinado_sem_bypass():
    assert e_receita_propria_do_componente(
        tipo="receita",
        categoria=" espirometria ",
        spirometry_exam_id="exam-1",
        consultation_id="con-1",
    ) == (CATEGORIA_ESPIROMETRIA, "exame")
    assert e_receita_propria_do_componente(
        tipo="receita",
        categoria="CONSULTA",
        spirometry_exam_id="exam-1",
        consultation_id="con-1",
    ) == (CATEGORIA_CONSULTA, "consulta")


def test_ajuste_repasse_e_categoria_nao_equivalente_ficam_fora_do_predicado():
    assert e_receita_propria_do_componente(
        tipo="despesa",
        categoria=CATEGORIA_ESPIROMETRIA,
        spirometry_exam_id="exam-1",
        consultation_id=None,
    ) == (None, None)
    assert e_receita_propria_do_componente(
        tipo="receita",
        categoria="Outro",
        spirometry_exam_id="exam-1",
        consultation_id=None,
    ) == (None, None)
