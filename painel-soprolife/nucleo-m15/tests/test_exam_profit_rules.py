"""Regras de custo independentes do banco e de dados clínicos reais."""
import importlib.util
from decimal import Decimal
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "app/services/exam_profit.py"
spec = importlib.util.spec_from_file_location("exam_profit_rules", MODULE)
profit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profit)


def check_session(place, count, hours, expected):
    component = profit.session_cost(place, hours, profit.DEFAULT_RULES)
    assert sum(component.values()) == profit.cents(expected)
    ids = [f"ESP-{n:06d}" for n in range(count)]
    allocated = {key: profit.allocate(value, ids) for key, value in component.items()}
    assert sum(sum(v.values()) for v in allocated.values()) == profit.cents(expected)
    return allocated


def test_downtown_room_is_once_per_hour():
    check_session("downtown", 1, Decimal("1"), "95")
    shares = check_session("downtown", 2, Decimal("1"), "95")
    assert list(shares["room"].values()) == [3000, 3000]
    check_session("downtown", 2, Decimal("2"), "155")


def test_nova_america_room_is_once_per_hour():
    check_session("nova_america", 1, Decimal("1"), "75")
    shares = check_session("nova_america", 2, Decimal("1"), "75")
    assert list(shares["room"].values()) == [2000, 2000]
    check_session("nova_america", 2, Decimal("2"), "115")


def test_pastore_once_per_day_and_exact_rounding():
    check_session("pastore", 1, None, "36")
    shares = check_session("pastore", 5, None, "36")
    assert all(v == 600 for v in shares["fuel"].values())
    assert all(v == 120 for v in shares["parking"].values())
    assert sum(profit.allocate(1001, [str(i) for i in range(3)]).values()) == 1001


def test_maclin_variable_transfer_and_one_parking():
    for price, expected in [("200", "60"), ("350", "105")]:
        assert profit.percent(profit.cents(price), Decimal("30")) == profit.cents(expected)
    check_session("maclin", 3, None, "10")


def test_alergolife_and_home_share_visit():
    check_session("alergolife", 2, None, "20")
    check_session("home", 3, None, "10")


def test_card_proven_portion_and_tax_on_recognized_revenue():
    rules = profit.DEFAULT_RULES
    result = profit.calculate_exam(
        revenue=20000, received=20000, gross=20000, card_paid=8000,
        payment_known=True, tax_base=15000, partner_transfer=0,
        physician_transfer=0, shared={"room":0,"parking":0,"fuel":0}, rules=rules)
    assert result["custos"]["cartao"] == "4.00"
    assert result["custos"]["imposto"] == "12.00"
    assert result["custos"]["material"] == "10.00"
    assert result["custos"]["tecnico"] == "0.00"
    pix = profit.calculate_exam(
        revenue=20000, received=20000, gross=20000, card_paid=0,
        payment_known=True, tax_base=20000, partner_transfer=0,
        physician_transfer=0, shared={"room":0,"parking":0,"fuel":0}, rules=rules)
    assert pix["custos"]["cartao"] == "0.00"


def test_existing_medical_transfer_once_and_net_partner_once():
    base = dict(revenue=20000, received=20000, gross=20000, card_paid=0,
                payment_known=True, tax_base=20000, partner_transfer=6000,
                physician_transfer=2500, shared={"room":0,"parking":0,"fuel":0},
                rules=profit.DEFAULT_RULES)
    result = profit.calculate_exam(**base)
    assert result["custos"]["parceiro"] == "60.00"
    assert result["custos"]["medico"] == "25.00"
    net = profit.calculate_exam(**{**base,"revenue":14000,"received":14000,
                                    "gross":None,"partner_revenue_is_net":True})
    assert net["custos"]["parceiro"] == "0.00"


def test_missing_evidence_never_creates_profit():
    result = profit.calculate_exam(
        revenue=20000, received=None, gross=None, card_paid=None,
        payment_known=False, tax_base=None, partner_transfer=None,
        physician_transfer=None, shared=None, rules=profit.DEFAULT_RULES)
    assert result["status"] == "incompleto"
    assert result["lucro"] is None
    assert "forma_de_pagamento_nao_classificada" in result["pendencias"]
