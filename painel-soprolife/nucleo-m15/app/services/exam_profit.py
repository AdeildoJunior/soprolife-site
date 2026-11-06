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
    "downtown.minimum_hours": Decimal("1.00"),
    "nova_america.minimum_hours": Decimal("1.00"),
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
    "alergocare.partner_percent": Decimal("30.00"),
    "alergocare.parking": Decimal("10.00"),
    "alergocare.fuel": Decimal("10.00"),
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
                 room_override: Decimal | None = None, *,
                 parking_override: Decimal | None = None,
                 fuel_override: Decimal | None = None,
                 partial: bool = False) -> dict[str, int]:
    """Componentes compartilhados de uma única ida confirmada."""
    if place in ("downtown", "nova_america") and room_override is None and (hours is None or hours <= 0):
        if not partial:
            raise ValueError("horas_faturaveis_ausentes")
    result = {}
    for component, override in (("parking", parking_override), ("fuel", fuel_override)):
        if override is not None:
            result[component] = cents(override)
        elif f"{place}.{component}" in rules:
            result[component] = cents(rules[f"{place}.{component}"])
        elif not partial:
            raise KeyError(f"{place}.{component}")
    if room_override is not None:
        result["room"] = cents(room_override)
    elif place not in ("downtown", "nova_america"):
        result["room"] = 0
    elif hours is not None and hours > 0 and f"{place}.room_hour" in rules:
        result["room"] = cents(Decimal(hours) * rules[f"{place}.room_hour"])
    elif not partial:
        raise KeyError(f"{place}.room_hour")
    return result


def calculate_exam(*, revenue: int | None, received: int | None,
                   gross: int | None, card_paid: int | None,
                   payment_known: bool, tax_base: int | None,
                   partner_transfer: int | None, physician_transfer: int | None,
                   shared: dict[str, int] | None, rules: dict[str, Decimal],
                   partner_revenue_is_net: bool = False,
                   extra_direct: int = 0, card_fee_override: int | None = None,
                   tax_amount_override: int | None = None,
                   contractual_partner: bool = False) -> dict:
    missing = []
    if revenue is None:
        missing.append("receita_soprolife_ausente")
    if (not payment_known or card_paid is None) and not (contractual_partner and not payment_known) and card_fee_override is None:
        missing.append("forma_de_pagamento_nao_classificada")
    # Base gerencial é a receita reconhecida; não afirma emissão fiscal.
    # O argumento legado não permite trocar a base gerencial pela fiscal.
    tax_base = revenue
    if partner_transfer is None and not partner_revenue_is_net:
        missing.append("repasse_parceiro_nao_conciliado")
    if physician_transfer is None:
        missing.append("repasse_medico_nao_conciliado")
    if shared is None:
        missing.append("sessao_operacional_ausente")
    elif not {"room", "parking", "fuel"}.issubset(shared):
        missing.append("custos_da_sessao_incompletos")
    if revenue is not None and received is not None and received > revenue:
        missing.append("recebido_superior_a_receita")
    card_limit = gross if gross is not None else revenue
    if card_limit is not None and card_paid is not None and card_paid > card_limit:
        missing.append("cartao_superior_a_receita")
    required_rules = {"material", "technician"}
    if card_fee_override is None and card_paid != 0 and not (contractual_partner and not payment_known):
        required_rules.add("card_percent")
    if tax_amount_override is None:
        required_rules.add("tax_percent")
    if not required_rules.issubset(rules):
        missing.append("regras_de_custo_sem_vigencia")
    # Cada componente conserva sua própria evidência, mesmo sem fechamento.
    # None é desconhecido; zero só decorre de regra, pagamento ou não aplicação.
    shared = shared or {}
    card_cost = card_fee_override
    if card_cost is None and contractual_partner and not payment_known:
        # Fechamento contratual: nenhuma taxa sem evidência de cartão.
        card_cost = 0
    if card_cost is None and payment_known and card_paid is not None:
        if card_paid == 0:
            card_cost = 0
        elif "card_percent" in rules:
            card_cost = percent(card_paid, rules["card_percent"])
    tax_cost = tax_amount_override
    if tax_cost is None and tax_base is not None and "tax_percent" in rules:
        tax_cost = percent(tax_base, rules["tax_percent"])
    costs = {
        "material": cents(rules["material"]) if "material" in rules else None,
        "parceiro": 0 if partner_revenue_is_net else partner_transfer,
        "medico": physician_transfer,
        "sala": shared.get("room"),
        "estacionamento": shared.get("parking"),
        "combustivel": shared.get("fuel"),
        "cartao": card_cost,
        "imposto": tax_cost,
        "tecnico": cents(rules["technician"]) if "technician" in rules else None,
    }
    if extra_direct:
        costs["outros"] = extra_direct
    known_costs = [v for v in costs.values() if v is not None]
    total = sum(known_costs) if known_costs else None
    profit = revenue - total if not missing else None
    margin = (Decimal(profit) * 100 / Decimal(revenue)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if profit is not None and revenue else None
    return {
        "lucro_competencia": money(profit) if profit is not None else None,
        "lucro_realizado": money(profit) if profit is not None and received is not None and received == revenue else None,
        "pendencias_caixa": ["recebimento_nao_comprovado"] if received is None else ["recebimento_nao_integral"] if received != revenue else [],
        "status": "incompleto" if missing else "completo", "pendencias": missing,
        "receita": money(revenue) if revenue is not None else None,
        "bruto": money(gross) if gross is not None else None,
        "recebido": money(received) if received is not None else None,
        "pendente": money(revenue - received) if revenue is not None and received is not None and received <= revenue else None,
        "base_imposto": money(tax_base) if tax_base is not None else None,
        "aliquota_imposto": str(rules["tax_percent"]) if "tax_percent" in rules else None,
        "imposto_tipo": "contabilizado" if tax_amount_override is not None else "estimado",
        "imposto_descricao": "Imposto contabilizado" if tax_amount_override is not None else f"Imposto gerencial estimado — {format(rules['tax_percent'].normalize(), 'f') if 'tax_percent' in rules else '—'}%",
        "cartao_fonte": "lancamento" if card_fee_override is not None else "sem_evidencia_cartao_fechamento" if contractual_partner and not payment_known else "regra",
        "componentes_pendentes": [key for key, value in costs.items() if value is None],
        "regras_pendentes": sorted(required_rules.difference(rules)),
        "custos": {key: money(value) if value is not None else None for key, value in costs.items()},
        "custos_comprovados": money(total) if total is not None else None,
        "custo_total": money(total) if not missing else None,
        "lucro": money(profit) if profit is not None else None,
        "margem": str(margin) if margin is not None else None,
        "realizado": received == revenue if received is not None and revenue is not None else None,
    }
