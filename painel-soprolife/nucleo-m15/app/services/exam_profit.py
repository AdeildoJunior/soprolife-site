"""Cálculo monetário puro do lucro por exame; entradas são evidências já resolvidas.

Não cria lançamentos. Valores em centavos evitam perda no rateio. Ausência de
evidência bloqueia lucro fechado em vez de transformar desconhecido em zero.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


DEFAULT_RULES = {
    "material": Decimal("10.00"),
    "card_percent": Decimal("5.00"),
    "tax_percent": Decimal("6.00"),
    "technician": Decimal("0.00"),
    "downtown.room_hour": Decimal("60.00"),
    "downtown.parking": Decimal("25.00"),
    "downtown.fuel": Decimal("10.00"),
    "nova_america.room_hour": Decimal("40.00"),
    "nova_america.parking": Decimal("25.00"),
    "nova_america.fuel": Decimal("10.00"),
    "pastore.parking": Decimal("6.00"),
    "pastore.fuel": Decimal("30.00"),
    "maclin.partner_percent": Decimal("30.00"),
    "maclin.parking": Decimal("10.00"),
    "maclin.fuel": Decimal("0.00"),
    "alergolife.partner_percent": Decimal("30.00"),
    "alergolife.parking": Decimal("10.00"),
    "alergolife.fuel": Decimal("10.00"),
    "home.parking": Decimal("0.00"),
    "home.fuel": Decimal("10.00"),
}


def cents(value: Decimal | str | int) -> int:
    return int((Decimal(value) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money(value: int) -> str:
    return f"{Decimal(value) / 100:.2f}"


def percent(amount: int, rate: Decimal) -> int:
    return cents(Decimal(amount) * Decimal(rate) / 10000)


def allocate(total: int, exam_ids: list[str]) -> dict[str, int]:
    """Centavos restantes vão aos IDs menores; soma é sempre exata."""
    if total < 0 or not exam_ids or len(set(exam_ids)) != len(exam_ids):
        raise ValueError("Rateio exige custo não negativo e IDs distintos.")
    quotient, remainder = divmod(total, len(exam_ids))
    return {exam_id: quotient + (index < remainder)
            for index, exam_id in enumerate(sorted(exam_ids))}


def session_cost(place: str, hours: Decimal | None, rules: dict[str, Decimal],
                 room_override: Decimal | None = None) -> dict[str, int]:
    """Componentes compartilhados de uma única ida confirmada."""
    if place in ("downtown", "nova_america") and room_override is None and (hours is None or hours <= 0):
        raise ValueError("horas_faturaveis_ausentes")
    result = {}
    for component in ("parking", "fuel"):
        result[component] = cents(rules[f"{place}.{component}"])
    result["room"] = (cents(room_override) if room_override is not None else cents(Decimal(hours) * rules[f"{place}.room_hour"])) if place in ("downtown", "nova_america") else 0
    return result


def calculate_exam(*, revenue: int | None, received: int | None,
                   gross: int | None, card_paid: int | None,
                   payment_known: bool, tax_base: int | None,
                   partner_transfer: int | None, physician_transfer: int | None,
                   shared: dict[str, int] | None, rules: dict[str, Decimal],
                   partner_revenue_is_net: bool = False,
                   extra_direct: int = 0, card_fee_override: int | None = None,
                   tax_amount_override: int | None = None) -> dict:
    missing = []
    if revenue is None:
        missing.append("receita_soprolife_ausente")
    if received is None:
        missing.append("valor_recebido_nao_discriminado")
    if not payment_known or card_paid is None:
        missing.append("forma_de_pagamento_nao_classificada")
    if tax_base is None:
        missing.append("base_tributavel_ausente")
    if partner_transfer is None and not partner_revenue_is_net:
        missing.append("repasse_parceiro_nao_conciliado")
    if physician_transfer is None:
        missing.append("repasse_medico_nao_conciliado")
    if shared is None:
        missing.append("sessao_operacional_ausente")
    if gross is None and not partner_revenue_is_net:
        missing.append("preco_bruto_nao_comprovado")
    if revenue is not None and received is not None and received > revenue:
        missing.append("recebido_superior_a_receita")
    if revenue is not None and card_paid is not None and card_paid > revenue:
        missing.append("cartao_superior_a_receita")
    if not {"material", "card_percent", "tax_percent", "technician"}.issubset(rules):
        missing.append("regras_de_custo_sem_vigencia")
    if missing:
        return {"status": "incompleto", "pendencias": missing, "lucro": None, "margem": None}
    costs = {
        "material": cents(rules["material"]),
        "parceiro": 0 if partner_revenue_is_net else partner_transfer,
        "medico": physician_transfer,
        "sala": shared.get("room", 0),
        "estacionamento": shared.get("parking", 0),
        "combustivel": shared.get("fuel", 0),
        "cartao": card_fee_override if card_fee_override is not None else percent(card_paid, rules["card_percent"]),
        "imposto": tax_amount_override if tax_amount_override is not None else percent(tax_base, rules["tax_percent"]),
        "tecnico": cents(rules["technician"]),
        "outros": extra_direct,
    }
    total = sum(costs.values())
    profit = revenue - total
    margin = (Decimal(profit) * 100 / Decimal(revenue)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if revenue else None
    return {
        "status": "completo", "pendencias": [], "receita": money(revenue),
        "bruto": money(gross) if gross is not None else None, "recebido": money(received),
        "pendente": money(revenue - received), "base_imposto": money(tax_base),
        "aliquota_imposto": str(rules["tax_percent"]),
        "imposto_tipo": "contabilizado" if tax_amount_override is not None else "estimado",
        "cartao_fonte": "lancamento" if card_fee_override is not None else "regra",
        "custos": {key: money(value) for key, value in costs.items()},
        "custo_total": money(total), "lucro": money(profit),
        "margem": str(margin) if margin is not None else None,
        "realizado": received == revenue,
    }
