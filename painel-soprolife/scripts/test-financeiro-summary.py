#!/usr/bin/env python3
"""
SoproLife — Testes da fonte financeira única (M14.2).

Valida parse_records + build_summary de read-financeiro-lancamentos-adc.py
com fixtures 100% SINTÉTICAS, offline: sem rede, sem Google, sem VPS, sem
data-private, sem credenciais e sem qualquer dado identificável de paciente.

Cobre: recebido, pendente, parcial, cortesia, cancelado, desconto,
duplicidade por id_atendimento, valores ausentes/inválidos, ausência da
aba, chaves de compatibilidade e segurança do summary (pii_guard).

Uso:
    python3 painel-soprolife/scripts/test-financeiro-summary.py
Exit: 0 = todos os casos passaram | 1 = houve falha.
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "fin_gen", SCRIPTS / "read-financeiro-lancamentos-adc.py")
fin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fin)

FALHAS = 0
HOJE = date(2026, 7, 11)  # "agora" fixo dos testes — não depende do relógio


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + detalhe if detalhe else ''}")


HEADER = ["id_lancamento", "id_atendimento", "criado_em", "data_exame",
          "tipo_movimento", "servico", "local_atendimento", "valor_tabela",
          "valor_cobrado", "valor_recebido", "desconto", "status_exame",
          "status_pagamento", "forma_pagamento", "origem_preco",
          "observacao_financeira", "fonte"]


def linha(**kv):
    """Linha sintética no shape da aba oficial (valores padrão seguros)."""
    base = {
        "id_lancamento": "FIN-TESTE-001", "id_atendimento": "ESP-TESTE-001",
        "criado_em": "2026-07-01T10:00:00Z", "data_exame": "2026-07-01",
        "tipo_movimento": "receita", "servico": "Espirometria",
        "local_atendimento": "Domiciliar", "valor_tabela": "250",
        "valor_cobrado": "250", "valor_recebido": "250", "desconto": "0",
        "status_exame": "Realizado", "status_pagamento": "Recebido",
        "forma_pagamento": "Pix", "origem_preco": "Tabela",
        "observacao_financeira": "", "fonte": "nova_espirometria",
    }
    base.update(kv)
    return [base[h] for h in HEADER]


def summary_de(linhas):
    registros, _ = fin.parse_records([HEADER] + linhas)
    return fin.build_summary(registros, hoje=HOJE)


print("── Recebido / soma básica " + "─" * 40)
# Espelho sintético do cenário confirmado em produção: dois recebidos
# (219 + 220) somam 439 sem duplicidade.
s = summary_de([
    linha(id_lancamento="F1", id_atendimento="A1", data_exame="2026-07-03",
          valor_cobrado="219", valor_recebido="219", desconto="31"),
    linha(id_lancamento="F2", id_atendimento="A2", data_exame="2026-07-10",
          valor_cobrado="220", valor_recebido="220", desconto="30"),
])
caso("dois recebidos somam 439.00", s["totais"]["receita_recebida"] == 439.0,
     str(s["totais"]["receita_recebida"]))
caso("exames_pagos = 2", s["exames_pagos"] == 2)
caso("ticket médio = 219.50", s["ticket_medio_real"] == 219.5)
caso("descontos somados do campo gravado (31+30)", s["totais"]["descontos_concedidos"] == 61.0)
caso("período de/até do próprio dado", s["periodo"]["de"] == "2026-07-03" and s["periodo"]["ate"] == "2026-07-10")
caso("mês atual soma as duas entradas", s["total_entradas_mes_atual"] == 439.0)
caso("valor_base_exame = moda do valor_tabela", s["valor_base_exame"] == 250.0)

print("── Pendente / Parcial " + "─" * 44)
s = summary_de([
    linha(id_atendimento="A1", valor_cobrado="250", valor_recebido="250"),
    # Pendente com célula de recebido preenchida: defensivo → não vira receita
    linha(id_atendimento="A2", status_pagamento="Pendente", valor_recebido="250"),
    # Parcial: 100 entram, 150 ficam a receber
    linha(id_atendimento="A3", status_pagamento="Parcial", valor_recebido="100"),
])
caso("pendente não conta como receita (mesmo com célula preenchida)",
     s["totais"]["receita_recebida"] == 350.0, str(s["totais"]["receita_recebida"]))
caso("pendente soma valor_cobrado em receita_pendente",
     s["totais"]["receita_pendente"] == 400.0, str(s["totais"]["receita_pendente"]))
caso("parcial conta como exame pago", s["exames_pagos"] == 2)
caso("por_status registra os 3 status", len(s["por_status"]) == 3)

print("── Cortesia / Cancelado " + "─" * 42)
s = summary_de([
    linha(id_atendimento="A1", status_pagamento="Cortesia", valor_cobrado="0",
          valor_recebido="0", desconto="", origem_preco="Cortesia"),
    linha(id_atendimento="A2", status_pagamento="Cancelado", valor_recebido="250"),
    linha(id_atendimento="A3", status_exame="Cancelado", status_pagamento="Recebido",
          valor_recebido="250"),
])
caso("cortesia/cancelados não geram receita", s["totais"]["receita_recebida"] == 0.0)
caso("1 cortesia contada", s["totais"]["cortesias"] == 1)
caso("2 cancelados (por pagamento e por exame)", s["totais"]["cancelados"] == 2)
caso("cortesia sem campo desconto deriva de tabela-cobrado (250)",
     s["totais"]["descontos_concedidos"] == 250.0, str(s["totais"]["descontos_concedidos"]))
caso("cancelado não gera desconto nem pendência",
     s["totais"]["receita_pendente"] == 0.0)

print("── Desconto derivado " + "─" * 45)
s = summary_de([
    linha(id_atendimento="A1", valor_cobrado="200", valor_recebido="200", desconto=""),
])
caso("desconto ausente derivado (250-200=50)", s["totais"]["descontos_concedidos"] == 50.0)
caso("tabela menor que cobrado nunca dá desconto negativo",
     summary_de([linha(valor_tabela="200", valor_cobrado="250", desconto="")])
     ["totais"]["descontos_concedidos"] == 0.0)

print("── Duplicidade (upsert por id_atendimento) " + "─" * 24)
s = summary_de([
    linha(id_lancamento="F1", id_atendimento="A1", valor_cobrado="219", valor_recebido="219"),
    linha(id_lancamento="F2", id_atendimento="A1", valor_cobrado="220", valor_recebido="220"),
])
caso("última linha vence no dedupe", s["totais"]["receita_recebida"] == 220.0,
     str(s["totais"]["receita_recebida"]))
caso("duplicado contado", s["totais"]["duplicados_ignorados"] == 1)
caso("só 1 lançamento válido", s["totais"]["lancamentos_validos"] == 1)
s = summary_de([
    linha(id_lancamento="F1", id_atendimento="", valor_recebido="100", valor_cobrado="100"),
    linha(id_lancamento="F1", id_atendimento="", valor_recebido="150", valor_cobrado="150"),
])
caso("sem id_atendimento, dedupe cai para id_lancamento",
     s["totais"]["receita_recebida"] == 150.0 and s["totais"]["duplicados_ignorados"] == 1)

print("── Valores ausentes/inválidos " + "─" * 36)
s = summary_de([
    linha(id_atendimento="A1"),
    linha(id_atendimento="A2", valor_cobrado=""),        # sem valor
    linha(id_atendimento="A3", valor_cobrado="abc"),     # lixo
    linha(id_atendimento="A4", valor_cobrado="-50"),     # negativo
    linha(id_atendimento="A5", status_pagamento="Pago"), # fora do enum
])
caso("linhas inválidas fora das somas", s["totais"]["receita_recebida"] == 250.0)
caso("4 linhas inválidas contadas", s["totais"]["linhas_invalidas"] == 4,
     str(s["totais"]["linhas_invalidas"]))
s = summary_de([
    linha(id_atendimento="A1", status_pagamento="Recebido",
          valor_cobrado="250", valor_recebido="200"),
])
caso("Recebido com recebido≠cobrado usa o valor real e marca inconsistência",
     s["totais"]["receita_recebida"] == 200.0 and s["totais"]["linhas_inconsistentes"] == 1)

print("── Aba ausente / vazia " + "─" * 43)
registros, avisos = fin.parse_records([])
s = fin.build_summary(registros, hoje=HOJE)
caso("aba ausente → summary zerado sem erro",
     s["totais"]["receita_recebida"] == 0.0 and s["totais"]["lancamentos_validos"] == 0)
caso("aba ausente → ticket e valor_base ficam null (nunca 0 inventado)",
     s["ticket_medio_real"] is None and s["valor_base_exame"] is None)
caso("saldo_operacional é sempre null (não derivável da fonte)",
     s["saldo_operacional"] is None)

print("── Compatibilidade com consumidores " + "─" * 30)
s = summary_de([
    linha(id_atendimento="A1", data_exame="2026-07-03", valor_cobrado="219", valor_recebido="219"),
    linha(id_atendimento="A2", data_exame="2026-06-15", valor_cobrado="250", valor_recebido="250"),
])
caso("receita_exames (compat) = receita recebida total", s["receita_exames"] == 469.0)
caso("espirometrias_pagas (compat)", s["espirometrias_pagas"] == 2)
caso("total_lancamentos (compat)", s["total_lancamentos"] == 2)
caso("total_entradas_mes_atual só conta o mês corrente",
     s["total_entradas_mes_atual"] == 219.0, str(s["total_entradas_mes_atual"]))
caso("por_mes separa jun e jul", len(s["por_mes"]) == 2)
caso("fonte oficial declarada no source",
     s["source"]["official_source"] == "Financeiro_Lancamentos")

print("── Lançamentos agregados (template, sem texto livre) " + "─" * 13)
s = summary_de([
    linha(id_atendimento="A1", observacao_financeira="paciente pediu recibo",
          local_atendimento="Domiciliar"),
    linha(id_atendimento="A2", status_pagamento="Pendente", valor_cobrado="180",
          valor_recebido="0"),
])
descrs = [l["descricao"] for l in s["lancamentos_agregados"]]
caso("descrição é template Serviço — Local", "Espirometria — Domiciliar" in descrs)
caso("pendente aparece com valor cobrado e status",
     any(l["valor"] == 180.0 and l["status"] == "Pendente" for l in s["lancamentos_agregados"]))
caso("observacao_financeira NUNCA aparece no summary",
     "recibo" not in str(s))

print("── Segurança (pii_guard + coluna bloqueada) " + "─" * 22)
s = summary_de([
    linha(id_atendimento="A1"),
    linha(id_atendimento="A2", status_pagamento="Parcial", valor_recebido="100"),
    linha(id_atendimento="A3", status_pagamento="Cortesia", valor_cobrado="0", desconto=""),
])
problemas = fin.validate_summary(s)
caso("summary completo passa na validação de segurança", problemas == [],
     "; ".join(problemas))

header_com_pii = HEADER + ["paciente_nome"]
rows = [header_com_pii, linha() + ["Fulano de Tal"]]
registros, avisos = fin.parse_records(rows)
caso("coluna de pessoa é BLOQUEADA no parse",
     all("paciente_nome" not in r for r in registros))
caso("bloqueio gera aviso", any("BLOQUEADA" in a for a in avisos))
caso("nenhum registro carrega o valor bloqueado",
     "Fulano" not in str(registros))

print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} caso(s) FALHARAM.")
    sys.exit(1)
print("RESULTADO: todos os casos passaram.")
sys.exit(0)
