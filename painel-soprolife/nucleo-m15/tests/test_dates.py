"""Datas incompletas: normalização com preservação de metadados."""

from datetime import date

from app.dates import add_months, parse_incomplete_date


def test_data_completa_ddmmaaaa():
    nd = parse_incomplete_date("09/07/2026")
    assert nd.value == date(2026, 7, 9)
    assert nd.precision == "dia"
    assert nd.day_assumed is False
    assert nd.original == "09/07/2026"


def test_data_completa_iso():
    nd = parse_incomplete_date("2026-07-09")
    assert nd.value == date(2026, 7, 9)
    assert nd.precision == "dia"


def test_mes_ano_numerico_assume_dia_1():
    nd = parse_incomplete_date("06/2026")
    assert nd.value == date(2026, 6, 1)
    assert nd.precision == "mes"
    assert nd.day_assumed is True
    assert nd.original == "06/2026"


def test_mes_ano_iso_parcial():
    nd = parse_incomplete_date("2026-08")
    assert nd.value == date(2026, 8, 1)
    assert nd.precision == "mes"
    assert nd.day_assumed is True


def test_nome_do_mes():
    nd = parse_incomplete_date("dezembro/2026")
    assert nd.value == date(2026, 12, 1)
    assert nd.precision == "mes"
    assert nd.day_assumed is True


def test_nome_do_mes_com_de():
    nd = parse_incomplete_date("março de 2026")
    assert nd.value == date(2026, 3, 1)
    assert nd.precision == "mes"


def test_so_ano():
    nd = parse_incomplete_date("2026")
    assert nd.value == date(2026, 1, 1)
    assert nd.precision == "ano"
    assert nd.day_assumed is True


def test_vazio_e_invalido():
    assert parse_incomplete_date("").value is None
    assert parse_incomplete_date(None).value is None
    nd = parse_incomplete_date("não sei")
    assert nd.value is None
    assert nd.precision == "desconhecida"
    assert nd.original == "não sei"


def test_data_invalida_31_fevereiro():
    nd = parse_incomplete_date("31/02/2026")
    assert nd.value is None
    assert nd.precision == "desconhecida"


def test_add_months_simples():
    assert add_months(date(2026, 1, 15), 6) == date(2026, 7, 15)


def test_add_months_estouro_de_dia():
    # 31/08 + 6 meses -> 28/02 (2027 não é bissexto)
    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)


def test_add_months_virada_de_ano():
    assert add_months(date(2026, 10, 1), 6) == date(2027, 4, 1)
