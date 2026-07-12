#!/usr/bin/env python3
"""
SoproLife — Testes da biblioteca de normalização canônica (M14.3).

100% offline e sintético: sem rede, sem Google, sem data-private, sem dado
real. Cobre datas com precisão, classificação de IDs, enums canônicos com
aliases, chave de paciente e hash protegido.

Uso:
    python3 painel-soprolife/scripts/test-normalizacao.py
Exit: 0 = todos os casos passaram | 1 = houve falha.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from soprolife_normalizacao import (
    DataFlex,
    chave_paciente,
    classificar_id,
    formatar_data_br,
    hash_protegido,
    nomes_compativeis,
    normalizar_enum,
    parse_data_flex,
)

FALHAS = 0


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + detalhe if detalhe else ''}")


print("── Datas com precisão ──")
caso("DD/MM/AAAA vira dia", parse_data_flex("02/07/2026") == DataFlex("2026-07-02", "dia", True))
caso("ISO simples vira dia", parse_data_flex("2026-07-02") == DataFlex("2026-07-02", "dia", True))
caso("ISO com hora vira dia", parse_data_flex("2026-07-02T14:30:55Z").iso == "2026-07-02")
caso("BR com hora vira dia", parse_data_flex("02/07/2026 14:30").iso == "2026-07-02")
caso("DD-MM-AAAA aceito", parse_data_flex("02-07-2026").iso == "2026-07-02")
caso("DD/MM/AA século 2000", parse_data_flex("02/07/26") == DataFlex("2026-07-02", "dia", True))
caso("MM/AAAA NUNCA inventa dia", parse_data_flex("06/2026") == DataFlex("2026-06-01", "mes", True))
caso("M/AAAA aceito", parse_data_flex("6/2026").precisao == "mes")
caso("AAAA/MM aceito", parse_data_flex("2026/06") == DataFlex("2026-06-01", "mes", True))
caso("mês PT com barra", parse_data_flex("junho/2026") == DataFlex("2026-06-01", "mes", True))
caso("mês PT com espaço e acento", parse_data_flex("Março 2026") == DataFlex("2026-03-01", "mes", True))
caso("só o ano vira precisão ano", parse_data_flex("2026") == DataFlex("2026-01-01", "ano", True))
caso("serial do Sheets vira dia", parse_data_flex("46203").precisao == "dia")
caso("data impossível é inválida", parse_data_flex("45/13/2026") == DataFlex(None, "desconhecida", False))
caso("vazio é desconhecida", parse_data_flex("") == DataFlex(None, "desconhecida", False))
caso("texto livre é desconhecida", parse_data_flex("na semana passada").valida is False)
caso("exibição respeita precisão mês", formatar_data_br("2026-06-01", "mes") == "06/2026")
caso("exibição respeita precisão ano", formatar_data_br("2026-01-01", "ano") == "2026")
caso("exibição dia completo", formatar_data_br("2026-07-02", "dia") == "02/07/2026")

print("── IDs ──")
UUID_EX = "0f8fad5b-d9cb-469f-a165-70867728950e"
caso("ID de servidor (prefixo+UUID) é canônico",
     classificar_id(f"PAC-{UUID_EX}") == (f"PAC-{UUID_EX}".split('-')[0], ) or
     classificar_id(f"PAC-{UUID_EX}").formato == "canonico")
caso("canônico marca canonico=True", classificar_id(f"ESP-{UUID_EX}").canonico is True)
caso("id do navegador vira chave_navegador (idempotency key legada)",
     classificar_id("ESP-20260709-143055-ABC123").formato == "chave_navegador")
caso("chave do navegador NÃO é canônica", classificar_id("ESP-20260709-143055-ABC123").canonico is False)
caso("sequencial _nextId é legado", classificar_id("ESP-0001").formato == "sequencial")
caso("ESM antigo é legado_esm", classificar_id("ESM-jun-01").formato == "legado_esm")
caso("PAC-AAAAMMDD-NNN é data_seq", classificar_id("PAC-20260615-001").formato == "data_seq")
caso("vazio é ausente", classificar_id("").formato == "ausente")
caso("texto solto é irregular", classificar_id("exame do dia").formato == "irregular")
caso("nenhum formato legado vira canônico", classificar_id("ESM-x").canonico is False)

print("── Enums canônicos ──")
caso("valor exato reconhecido", normalizar_enum("status_exame", "Realizado").via == "exato")
caso("exato ignora caixa/acento", normalizar_enum("status_exame", "realizado").via == "exato")
caso("'Exame realizado' é alias lexical de Realizado",
     normalizar_enum("status_exame", "Exame realizado") == ("Realizado", "alias"))
caso("'Confirmado' exige decisão manual (nunca alias automático)",
     normalizar_enum("status_exame", "Confirmado").via == "decisao_manual")
caso("'agendado' exige decisão manual (agendar ≠ aguardar exame)",
     normalizar_enum("status_exame", "agendado").via == "decisao_manual")
caso("'concluído' exige decisão manual",
     normalizar_enum("status_exame", "Concluído").via == "decisao_manual")
caso("'Não confirmado' exige decisão manual (consentimento nunca promovido)",
     normalizar_enum("consentimento_whatsapp", "Não confirmado").via == "decisao_manual")
caso("consentimento 'sim' → Sim (lexical)", normalizar_enum("consentimento_whatsapp", "SIM").canonico == "Sim")
caso("valor desconhecido não é inventado", normalizar_enum("status_exame", "talvez") == (None, None))
caso("'pastore' em local exige decisão manual (parceiro ≠ sinônimo de local)",
     normalizar_enum("local_atendimento", "Pastore").via == "decisao_manual")
caso("'coworking' em local exige decisão manual",
     normalizar_enum("local_atendimento", "coworking").via == "decisao_manual")
caso("'Convertido em paciente' exige decisão manual (serviço não inferível)",
     normalizar_enum("etapa_lead", "Convertido em paciente").via == "decisao_manual")
caso("decisao_manual carrega o candidato provável",
     normalizar_enum("status_exame", "Confirmado").canonico == "Realizado")
caso("'clinica' sem acento casa como exato (normalização remove acentos)",
     normalizar_enum("local_atendimento", "clinica") == ("Clínica", "exato"))
try:
    normalizar_enum("dominio_inexistente", "x")
    caso("domínio inexistente falha", False)
except KeyError:
    caso("domínio inexistente falha", True)

print("── Chave de paciente e hash ──")
caso("telefone tem prioridade", chave_paciente("(21) 90000-0001", "Alfa") == "tel:21900000001")
caso("sem telefone usa nome", chave_paciente("", "Paciente Alfa") == "nome:paciente alfa")
caso("nome normaliza acento", chave_paciente(None, "José") == "nome:jose")
caso("sem nada retorna None", chave_paciente("", "") is None)
caso("hash é estável", hash_protegido("Paciente Alfa") == hash_protegido("paciente ALFA"))
caso("hash não expõe o valor", "alfa" not in hash_protegido("Paciente Alfa"))
caso("hash de vazio é marcado", hash_protegido("") == "h:vazio")
caso("hashes de valores diferentes diferem", hash_protegido("a") != hash_protegido("b"))

print("── Similaridade de nomes ──")
caso("iguais são compatíveis", nomes_compativeis("Maria", "maria"))
caso("prefixo de tokens é compatível", nomes_compativeis("Maria", "Maria Silva"))
caso("nomes distintos não são", not nomes_compativeis("Maria", "Joana"))
caso("vazio nunca é compatível", not nomes_compativeis("", "Maria"))

print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} falha(s).")
    sys.exit(1)
print("RESULTADO: todos os casos passaram.")
sys.exit(0)
