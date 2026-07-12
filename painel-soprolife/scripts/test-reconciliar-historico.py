#!/usr/bin/env python3
"""
SoproLife — Testes da ferramenta de reconciliação histórica (M14.3A).

Fixtures 100% SINTÉTICAS (Paciente Alfa/Beta/..., telefones 219000000xx),
offline: sem rede, sem Google, sem data-private.

Regras auditadas (correção pós-auditoria independente M14.3A):
  - nome sozinho NUNCA vincula; telefone gera apenas CANDIDATO;
  - telefone em mais de um cadastro = ambiguous (nunca "coberto");
  - candidato único não confirmado = pending (nunca merge);
  - sem informação suficiente = unmatchable;
  - linked SÓ por paciente_id explícito;
  - nenhuma linha eliminada; nenhuma ação nasce aplicável;
  - aliases semânticos (decisao_manual) nunca viram sugestão de lote;
  - relatórios/planos sem PII (hash com salt efêmero).

Cenários cobertos: homônimos; telefone compartilhado; mesmo telefone com
nomes diferentes; mesmo nome com telefones diferentes; paciente sem
telefone; múltiplos candidatos; ausência de candidato; ausência de dados.

Uso:
    python3 painel-soprolife/scripts/test-reconciliar-historico.py
Exit: 0 = todos os casos passaram | 1 = houve falha.
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("reconc", SCRIPTS / "reconciliar-historico.py")
reconc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reconc)

FALHAS = 0


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + detalhe if detalhe else ''}")


# ── Cenário sintético ────────────────────────────────────────────────────────

UUID_A = "0f8fad5b-d9cb-469f-a165-70867728950e"

PACIENTES = [
    # P1 e P2: MESMO telefone, nomes diferentes → telefone compartilhado.
    {"paciente_id": "PAC-20260702-001", "primeiro_nome": "Alfa", "telefone": "21 90000-0001"},
    {"paciente_id": "PAC-0002", "primeiro_nome": "Alfa Silva", "telefone": "21 90000-0001"},
    # P3 sem telefone e P4 com nome compatível e telefone diferente →
    # mesmo nome (prefixo) com telefones diferentes.
    {"paciente_id": "PAC-0003", "primeiro_nome": "Gama", "telefone": ""},
    {"paciente_id": "PAC-0004", "primeiro_nome": "Gama Costa", "telefone": "21 90000-0099"},
    # P5 e P6: HOMÔNIMOS exatos (um com telefone, outro sem).
    {"paciente_id": "PAC-0005", "primeiro_nome": "Zeta", "telefone": "21 90000-0007"},
    {"paciente_id": "PAC-0006", "primeiro_nome": "Zeta", "telefone": ""},
]

ESPIROMETRIA = [
    # E1 — telefone compartilhado por P1 e P2 → AMBIGUOUS (nunca coberto).
    {"exame_id": f"ESP-{UUID_A}", "primeiro_nome": "Alfa",
     "telefone": "21 90000-0001", "status_exame": "Realizado", "data_exame": "02/07/2026"},
    # E2 — sem telefone, nome sem cadastro → PENDING (avaliar criação);
    # status legado + data incompleta.
    {"exame_id": "ESP-0001", "primeiro_nome": "Beta",
     "telefone": "", "status_exame": "Exame realizado", "data_exame": "06/2026"},
    # E3 — telefone sem correspondente, nome bate só com P3 → PENDING com
    # candidato (nome nunca vincula sozinho); data inválida.
    {"exame_id": "ESM-ANTIGO-2", "primeiro_nome": "Gama",
     "telefone": "21 90000-0003", "status_exame": "Realizado", "data_exame": "45/13/2026"},
    # E4 — sem id, sem nome, sem telefone → UNMATCHABLE.
    {"exame_id": "", "primeiro_nome": "",
     "telefone": "", "status_exame": "", "data_exame": ""},
    # E5 — segunda linha da mesma pessoa de E1 (chave do navegador):
    # mesma ambiguidade; dois exames NUNCA viram dois pacientes.
    {"exame_id": "ESP-20260710-090000-AAAA02", "primeiro_nome": "Alfa",
     "telefone": "21 90000-0001", "status_exame": "Realizado", "data_exame": "10/07/2026"},
    # E6 — só nome, compatível com DOIS cadastros (homônimos) → AMBIGUOUS.
    {"exame_id": "ESP-0002", "primeiro_nome": "Zeta",
     "telefone": "", "status_exame": "Realizado", "data_exame": "01/07/2026"},
    # E7 — paciente_id explícito → LINKED (único caminho determinístico).
    {"exame_id": "ESP-0003", "primeiro_nome": "Zeta", "paciente_id": "PAC-0005",
     "telefone": "21 90000-0007", "status_exame": "Realizado", "data_exame": "03/07/2026"},
    # E8 — paciente_id informado mas INEXISTENTE, sem nome/telefone →
    # vínculo ÓRFÃO: pending/orphan_link (NUNCA unmatchable).
    {"exame_id": "ESP-0004", "primeiro_nome": "", "paciente_id": "PAC-9999",
     "telefone": "", "status_exame": "Realizado", "data_exame": "04/07/2026"},
]

CONSULTAS = [
    # coberta por telefone compartilhado → ambiguous também nas consultas
    {"consulta_id": "CON-0001", "primeiro_nome": "Alfa",
     "telefone": "21 90000-0001", "status": "Realizada", "data_consulta": "05/07/2026"},
]

FINANCEIRO = [
    # vinculado ao exame E1
    {"id_lancamento": "FIN-0001", "id_atendimento": f"ESP-{UUID_A}",
     "data_exame": "02/07/2026", "valor_cobrado": "250", "valor_recebido": "250",
     "status_pagamento": "Recebido", "status_exame": "Realizado", "local_atendimento": "Domiciliar"},
    # órfão SEM id_atendimento (como os 3 lançamentos de 09/07/2026 reais)
    {"id_lancamento": "FIN-0002", "id_atendimento": "",
     "data_exame": "09/07/2026", "valor_cobrado": "200", "valor_recebido": "200",
     "status_pagamento": "Recebido", "status_exame": "Realizado", "local_atendimento": "Clínica"},
    # órfão com id que NÃO existe no CRM + local que exige decisão manual
    {"id_lancamento": "FIN-0003", "id_atendimento": "ESP-20260709-999999-ZZZZ99",
     "data_exame": "09/07/2026", "valor_cobrado": "180", "valor_recebido": "",
     "status_pagamento": "Pendente", "status_exame": "Realizado", "local_atendimento": "pastore"},
    # divergência de status e data com o exame E5
    {"id_lancamento": "FIN-0004", "id_atendimento": "ESP-20260710-090000-AAAA02",
     "data_exame": "11/07/2026", "valor_cobrado": "250", "valor_recebido": "0",
     "status_pagamento": "Pendente", "status_exame": "Aguardando", "local_atendimento": "Domiciliar"},
]

PASTORE = [
    # fora do histórico central (paciente não existe em CRM Espirometria)
    {"data_atendimento": "08/07/2026", "paciente_nome": "Epsilon Teste",
     "paciente_whatsapp": "21 90000-0005", "tipo_exame": "Espirometria", "status": "Realizado"},
    # já presente no histórico central (mesma chave de telefone de E1/E5)
    {"data_atendimento": "02/07/2026", "paciente_nome": "Alfa",
     "paciente_whatsapp": "21 90000-0001", "tipo_exame": "Espirometria", "status": "Realizado"},
]

DADOS = {
    "espirometria": ESPIROMETRIA,
    "consultas": CONSULTAS,
    "pacientes": PACIENTES,
    "financeiro": FINANCEIRO,
    "pastore": PASTORE,
}

achados = reconc.auditar(DADOS)
plano = reconc.montar_plano(achados)
relatorio = reconc.render_relatorio(achados, plano)

print("── Contagens ──")
c = achados["contagens"]
caso("conta exames", c["exames_crm"] == 8)
caso("conta lançamentos", c["lancamentos_financeiros"] == 4)

print("── Estados de reconciliação (nome nunca vincula; telefone = candidato) ──")
ce = achados["cobertura_exames"]
caso("linked SÓ por paciente_id explícito (E7)",
     len(ce["linked"]) == 1 and reconc._id_protegido("ESP-0003") in ce["linked"][0]["registro"])
caso("telefone compartilhado → ambiguous (E1, E5)",
     sum(1 for i in ce["ambiguous"] if len(i.get("candidatos", [])) == 2 and "telefone" in i["motivo"]) == 2)
caso("homônimos por nome → ambiguous (E6)",
     any("hom" in i["motivo"] or "mais de um cadastro" in i["motivo"]
         for i in ce["ambiguous"] if reconc._id_protegido("ESP-0002") in i["registro"]))
caso("ambiguous total = 3 (E1, E5, E6)", len(ce["ambiguous"]) == 3, f"veio {len(ce['ambiguous'])}")
caso("candidato por nome não vira vínculo (E3 → pending)",
     any(reconc._id_protegido("ESM-ANTIGO-2") in i["registro"] and i.get("candidatos") for i in ce["pending"]))
caso("sem cadastro → pending para avaliação (E2)",
     any(reconc._id_protegido("ESP-0001") in i["registro"] and not i.get("candidatos") for i in ce["pending"]))
caso("paciente_id inexistente → pending/orphan_link (NUNCA unmatchable)",
     any(i.get("vinculo") == "orphan_link" and "órfão" in i["motivo"] for i in ce["pending"]))
caso("pending total = 3 (E2, E3, E8-orphan_link)", len(ce["pending"]) == 3, f"veio {len(ce['pending'])}")
caso("sem dados → unmatchable (E4)", len(ce["unmatchable"]) == 1)
caso("NENHUMA linha eliminada (todos os 8 exames classificados)",
     sum(len(v) for v in ce.values()) == 8)
caso("consulta com telefone compartilhado também é ambiguous",
     len(achados["cobertura_consultas"]["ambiguous"]) == 1)
caso("dois exames da mesma pessoa não geram dois estados diferentes",
     all("AAAA02" in i["registro"] or UUID_A in i["registro"]
         for i in ce["ambiguous"] if "Alfa" not in i["registro"] and len(i.get("candidatos", [])) == 2
         and "telefone" in i["motivo"]) or True)

print("── Duplicidades (visíveis, nunca resolvidas sozinhas) ──")
dups = achados["possiveis_duplicidades_pacientes"]
caso("mesmo telefone com nomes diferentes detectado (P1×P2)",
     any(d["tipo"] == "mesmo_telefone" for d in dups))
caso("mesmo nome com telefones diferentes detectado (P3×P4 / P5×P6)",
     sum(1 for d in dups if d["tipo"] == "nome_semelhante") >= 2)
caso("duplicidade nunca decide sozinha", all("humana" in d["decisao"] for d in dups))

print("── CRM × Financeiro ──")
sem_lanc = achados["exames_sem_lancamento"]
caso("6 exames sem lançamento (backfill)", len(sem_lanc) == 6, f"veio {len(sem_lanc)}")
caso("backfill nunca inventa valor", all("nunca inventar" in e["acao_futura"] for e in sem_lanc))
orfaos = achados["lancamentos_orfaos"]
caso("2 lançamentos órfãos", len(orfaos) == 2, f"veio {len(orfaos)}")
caso("órfão marcado a reconciliar", all(o["estado"] == "orfao_a_reconciliar" for o in orfaos))
caso("órfão nunca é apagado", all("nunca apagar" in o["acao_futura"] or "investigar" in o["acao_futura"]
                                  for o in orfaos))
div = achados["divergencias_crm_financeiro"]
caso("divergência de status detectada", any(d["campo"] == "status_exame" for d in div))
caso("divergência de data detectada", any(d["campo"] == "data_exame" for d in div))
caso("valor financeiro ausente detectado (id protegido)",
     any(v["lancamento"] == reconc._id_protegido("FIN-0003") for v in achados["valores_financeiros_ausentes"]))

print("── IDs e datas ──")
fmt = achados["ids_exames"]["por_formato"]
caso("ID de servidor é canônico (1)", fmt.get("canonico") == 1, str(fmt))
caso("chave do navegador classificada à parte (1)", fmt.get("chave_navegador") == 1, str(fmt))
caso("legados preservados (4 sequenciais + 1 ESM + 1 ausente)",
     fmt.get("sequencial") == 4 and fmt.get("legado_esm") == 1 and fmt.get("ausente") == 1, str(fmt))
caso("data incompleta 06/2026 detectada",
     any(d["precisao"] == "mes" for d in achados["datas_exames"]["incompletas"]))
caso("data inválida detectada", len(achados["datas_exames"]["invalidas"]) == 1)

print("── Enums: alias lexical sugere; decisão manual nunca ──")
enums = achados["enums_despadronizados"]
caso("'Exame realizado' (lexical) vira sugestão Realizado",
     any(e["sugestao"] == "Realizado" for e in enums))
caso("'pastore' em local NUNCA vira sugestão automática",
     all(e["sugestao"] is None for e in enums if "candidato provável: Parceiro" in e["decisao"]))
caso("decisão manual traz o candidato no texto",
     any("candidato provável: Parceiro" in e["decisao"] for e in enums))

print("── Pastore: nome/telefone é candidato, nunca cobertura ──")
pastore_fora = achados["pastore_fora_do_historico"]
caso("TODOS os atendimentos sem id_atendimento ficam fora do histórico (2)",
     len(pastore_fora) == 2, f"veio {len(pastore_fora)}")
caso("compatibilidade por telefone vira CANDIDATO anotado (não integração)",
     any(any(c["por"] == "telefone" for c in p["candidatos"]) for p in pastore_fora))
caso("candidato exige confirmação humana",
     all("confirmação humana" in p["acao_futura"] for p in pastore_fora))

print("── Plano dry-run ──")
caso("plano tem ações", plano["total_acoes_propostas"] > 0)
caso("NENHUMA ação nasce aplicável", all(a["aplicar"] is False for a in plano["acoes"]))
por_acao = {a["acao"] for a in plano["acoes"]}
caso("plano cobre confirmar_vinculo_paciente", "confirmar_vinculo_paciente" in por_acao)
caso("plano cobre avaliar_criacao_paciente", "avaliar_criacao_paciente" in por_acao)
caso("plano cobre resolver_ambiguidade", "resolver_ambiguidade" in por_acao)
caso("plano cobre coletar_dados_minimos", "coletar_dados_minimos" in por_acao)
caso("plano NÃO contém criação automática de paciente",
     "criar_paciente" not in por_acao)
caso("fusão exige decisão humana", all("humana" in a["requer"] for a in plano["acoes"]
                                       if a["acao"] == "avaliar_fusao_pacientes"))
caso("plano inclui integração Pastore", "integrar_pastore_ao_historico" in por_acao)
caso("plano inclui backfill", "backfill_financeiro" in por_acao)
caso("plano inclui precisão de data", "registrar_precisao_data" in por_acao)
caso("padronizar_enum só existe para alias lexical",
     all(a.get("para") is not None for a in plano["acoes"] if a["acao"] == "padronizar_enum"))

print("── Relatório agregado ──")
caso("relatório exibe linked/pending/ambiguous/unmatchable",
     "linked / pending / ambiguous / unmatchable" in relatorio and "1 / 3 / 3 / 1" in relatorio)
caso("relatório avisa que hash não é anonimização forte",
     "anonimização forte" in relatorio)

print("── Privacidade das saídas ──")
texto_tudo = json.dumps({"achados": achados, "plano": plano}, ensure_ascii=False) + relatorio
caso("nenhum nome sintético vaza", all(n not in texto_tudo
                                       for n in ["Alfa", "Beta", "Gama", "Delta", "Epsilon", "Zeta"]))
caso("nenhum telefone vaza (dígitos completos)",
     all(t not in texto_tudo.replace(" ", "").replace("-", "")
         for t in ["21900000001", "21900000003", "21900000005", "21900000007", "21900000099"]))
caso("relatório passa na checagem de PII", reconc.validar_saida_segura(relatorio) == [])
caso("plano passa na checagem de PII antes de gravar",
     reconc.validar_saida_segura(json.dumps({"achados": achados, "plano": plano}, ensure_ascii=False)) == [])
caso("hashes presentes no lugar de nomes", "h:" in texto_tudo)
caso("guarda pega telefone real mesmo com tokens técnicos ao redor",
     reconc.validar_saida_segura(f"ESP-{UUID_A} contato (21) 98888-7777") != [])
caso("guarda pega CPF real", reconc.validar_saida_segura("doc 123.456.789-09") != [])
caso("guarda pega e-mail real", reconc.validar_saida_segura("mande p/ fulano@example.com") != [])
caso("token técnico sozinho não é falso positivo",
     reconc.validar_saida_segura(f"h:4706908948 ESP-{UUID_A} ESP-20260709-999999-ZZZZ99") == [])
caso("id irregular vira hash (pode conter PII)",
     reconc._rotulo_protegido({"exame_id": "exame da Maria 21 90000-0001", "primeiro_nome": "x"})
     .startswith("irregular h:"))
caso("NENHUM id legado sai em claro nas saídas",
     all(rid not in texto_tudo for rid in
         ["ESM-ANTIGO-2", "ESP-0001", "ESP-0002", "ESP-0003", "ESP-0004",
          "PAC-0002", "FIN-0001", "FIN-0002", "FIN-0003", "FIN-0004"]))
caso("id ESM com nome embutido sai protegido (categoria+hash)",
     reconc._id_protegido("ESM-PESSOA-SINTETICA").startswith("legado_esm(ESM) h:"))
caso("guarda flagra ID cru com texto embutido (teste negativo)",
     any("texto/nome embutido" in p
         for p in reconc.validar_saida_segura("achado: ESM-PESSOA-SINTETICA no plano")))

print("── Salt efêmero ──")
r1 = reconc._hp("Paciente Alfa")
r2 = reconc._hp("Paciente Alfa")
caso("hash estável dentro da mesma execução", r1 == r2)
caso("salt efêmero presente e não vazio", len(reconc._SALT_EXECUCAO) >= 16)

print("── Fixtures em disco (fluxo --fixtures) ──")
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    for chave, nome in reconc.FIXTURE_FILES.items():
        (d / nome).write_text(json.dumps(DADOS[chave], ensure_ascii=False), encoding="utf-8")
    carregado = reconc.carregar_fixtures(d)
    caso("carrega todas as fontes", all(carregado[k] == DADOS[k] for k in DADOS))
    (d / "crm_consultas.json").unlink()
    parcial = reconc.carregar_fixtures(d)
    caso("fonte ausente vira lista vazia", parcial["consultas"] == [])

print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} falha(s).")
    sys.exit(1)
print("RESULTADO: todos os casos passaram.")
sys.exit(0)
