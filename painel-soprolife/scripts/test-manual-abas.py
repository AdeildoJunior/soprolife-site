#!/usr/bin/env python3
"""
SoproLife — Testes do manifesto das abas e do gerador do Manual (M14.3).

Valida:
  - integridade do core/contracts/abas-manifest.json (campos, enums, seções);
  - referências a Apps Scripts e scripts locais existem no repositório;
  - apps-script/manual-das-abas.gs está em dia com o manifesto;
  - abas críticas presentes com as regras certas (nunca excluir / nunca recriar);
  - nenhum padrão de PII no manifesto (telefone/CPF/e-mail).

Offline, sem rede, sem data-private.

Uso:
    python3 painel-soprolife/scripts/test-manual-abas.py
Exit: 0 = todos os casos passaram | 1 = houve falha.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
RAIZ = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("gen_ma", SCRIPTS / "generate-manual-abas-gs.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

FALHAS = 0


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + detalhe if detalhe else ''}")


manifest = json.loads(gen.MANIFEST.read_text(encoding="utf-8"))
abas = manifest["abas"]
nomes = [a["nome"] for a in abas]

print("── Integridade do manifesto ──")
caso("manifesto tem versão", bool(manifest.get("versao")))
caso("cadeia de dados explicada (Sheets → painel)", len(manifest.get("cadeia_de_dados", [])) >= 5)
caso("sem abas duplicadas", len(nomes) == len(set(nomes)))

OBRIGATORIOS = ["nome", "descricao_simples", "finalidade", "tipo", "fonte", "status",
                "secao_command_center", "pagina_painel", "quem_grava", "apps_script",
                "leitores_locais", "atualizacao", "frequencia", "dados_pessoais",
                "dados_clinicos", "dados_financeiros", "apenas_agregados", "quem_edita",
                "pode_ocultar", "pode_arquivar", "pode_excluir", "risco_exclusao",
                "dependencias", "seguranca", "recomendacao"]
faltando = [(a["nome"], c) for a in abas for c in OBRIGATORIOS if c not in a]
caso("todas as abas têm os campos obrigatórios", not faltando, str(faltando[:4]))

tipos_ok = all(a["tipo"] in manifest["tipos_validos"] for a in abas)
caso("tipos dentro do vocabulário", tipos_ok)
status_ok = all(a["status"] in manifest["status_validos"] for a in abas)
caso("status dentro do vocabulário", status_ok)
rec_ok = all(a["recomendacao"] in manifest["recomendacoes_validas"] for a in abas)
caso("recomendações dentro do vocabulário", rec_ok)
secoes_ok = [a["nome"] for a in abas if a["secao_command_center"] not in gen.SECOES_PAINEL]
caso("seções do painel conhecidas", not secoes_ok, str(secoes_ok))
caso("fonte é oficial ou derivada", all(a["fonte"] in ("oficial", "derivada") for a in abas))

print("── Referências a arquivos do repositório ──")
refs_gs, refs_py = set(), set()
for a in abas:
    for ref in a.get("apps_script", []):
        m = re.match(r"^([\w./-]+\.gs)", ref)
        if m:
            refs_gs.add(m.group(1))
    for ref in a.get("leitores_locais", []):
        m = re.match(r"^(scripts/[\w./-]+\.py)", ref)
        if m:
            refs_py.add(m.group(1))
gs_faltando = [g for g in refs_gs
               if not (RAIZ / "apps-script" / g).exists() and not (RAIZ / g).exists()
               and not (RAIZ / "core" / g).exists()]
caso("todos os .gs citados existem", not gs_faltando, str(gs_faltando))
py_faltando = [p for p in refs_py if not (RAIZ / p).exists()]
caso("todos os scripts locais citados existem", not py_faltando, str(py_faltando))

print("── Abas críticas e regras de proteção ──")
por_nome = {a["nome"]: a for a in abas}
for critica in ["CRM Pacientes", "CRM Espirometria", "CRM Consultas", "Financeiro_Lancamentos"]:
    a = por_nome.get(critica)
    caso(f"{critica}: presente e nunca_excluir",
         a is not None and a["recomendacao"] == "nunca_excluir" and a["pode_excluir"] is False)
caso("aba antiga Financeiro marcada nunca_recriar",
     por_nome.get("Financeiro", {}).get("recomendacao") == "nunca_recriar"
     and por_nome.get("Financeiro", {}).get("status") == "removida")
caso("Pastore-Atendimentos é staging em transição",
     por_nome.get("Parceria Pastore - Atendimentos", {}).get("tipo") == "staging")
caso("backups _Backup_Leads_* documentados",
     "_Backup_Leads_Demo_*" in por_nome and "_Backup_Leads_Operacional_*" in por_nome)
caso("nenhum backup é excluído sem decisão humana",
     all("humana" in por_nome[b]["risco_exclusao"] for b in
         ["_Backup_Leads_Demo_*", "_Backup_Leads_Operacional_*"]))
caso("Financeiro_Lancamentos é a única fonte financeira oficial",
     [a["nome"] for a in abas if a["tipo"] == "fonte_financeira" and a["fonte"] == "oficial"
      and a["status"] == "operacional" and a["secao_command_center"] == "financeiro"]
     == ["Financeiro_Lancamentos"])
caso("CRM Espirometria não é fonte financeira",
     por_nome["CRM Espirometria"]["dados_financeiros"] is False)

print("── Privacidade do manifesto ──")
texto = json.dumps(manifest, ensure_ascii=False)
caso("sem padrão de telefone", not re.search(r"\(?\d{2}\)?\s?\d{4,5}-\d{4}", texto))
caso("sem padrão de CPF", not re.search(r"\d{3}\.\d{3}\.\d{3}-\d{2}", texto))
caso("sem e-mail", not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", texto))
caso("sem token/secret", not re.search(r"(api[_-]?key|token\s*[:=]|secret\s*[:=])", texto, re.I))

print("── Arquivo gerado em dia ──")
conteudo_esperado = gen.gerar_gs(manifest)
atual = gen.DESTINO.read_text(encoding="utf-8") if gen.DESTINO.exists() else ""
caso("manual-das-abas.gs bate com o manifesto", atual == conteudo_esperado,
     "rode: python3 painel-soprolife/scripts/generate-manual-abas-gs.py")
caso("gerado marca 'NÃO EDITAR À MÃO'", "NÃO EDITAR À MÃO" in atual)
caso("gerado só escreve na aba Manual das Abas",
     'var _MA_ABA = "Manual das Abas"' in atual
     and "deleteSheet" not in atual and "hideSheet" not in atual and "setName" not in atual)
linhas = gen.montar_linhas(manifest)
caso("todas as linhas com a mesma largura", len({len(l) for l in linhas}) == 1)
caso("matriz cita todas as abas", all(any(l[0] == a["nome"] for l in linhas) for a in abas))
caso("detalhe cobre risco de exclusão", sum(1 for l in linhas if l[0].strip() == "Risco de exclusão") == len(abas))

print("── M14.3A.1 — geração vs publicação (nunca confundir) ──")
caso("nenhuma data fixa proibida no .gs gerado",
     gen.verificar_sem_data_fixa(conteudo_esperado) == [])
caso("data fixa literal é detectada (guarda funciona)",
     gen.verificar_sem_data_fixa(
         conteudo_esperado.replace('"Atualizado em",',
                                   '"Atualizado em", "23/06/2026",', 1)) != [])
caso("guarda exige a função real de atualização",
     any("função real" in p for p in
         gen.verificar_sem_data_fixa(conteudo_esperado.replace(
             "function atualizarManualDasAbasSoproLife()", "function outraCoisa()"))))
caso("'Atualizado em' usa new Date() (data viva na execução)",
     "new Date()" in atual[atual.find('"Atualizado em"'):atual.find('"Atualizado em"') + 300])

status_ma = gen.carregar_status()
caso("manual-abas-status.json existe e é JSON válido", bool(status_ma))
caso("status registra a versão do manifesto atual",
     status_ma.get("manifestVersion") == str(manifest.get("versao")))
caso("status registra o hash do manifesto atual",
     status_ma.get("manifestSha256") == gen.hash_manifesto())
caso("status registra o hash da geração atual do .gs",
     status_ma.get("generationSha256") == gen.hash_texto(atual))

pub = status_ma.get("publication") or {}
caso("publicação tem estado explícito",
     pub.get("state") in ("publication_pending", "published_confirmed_by_human"))
caso("estado 'publicado' só com atestado humano (publishedAt + sha idêntico)",
     pub.get("state") != "published_confirmed_by_human"
     or (pub.get("publishedAt") and pub.get("publishedSha256") == gen.hash_texto(atual)))
caso("código novo sem atestado → publication_pending (honesto)",
     gen.estado_publicacao({"publication": {"publishedSha256": "outro-sha",
                                            "publishedAt": "2026-06-23T00:00:00+00:00"}},
                           gen.hash_texto(atual)) == "publication_pending")
caso("sem nenhum atestado → publication_pending",
     gen.estado_publicacao({}, gen.hash_texto(atual)) == "publication_pending")
caso("atestado do sha atual → published_confirmed_by_human",
     gen.estado_publicacao({"publication": {"publishedSha256": gen.hash_texto(atual),
                                            "publishedAt": "2026-07-12T00:00:00+00:00"}},
                           gen.hash_texto(atual)) == "published_confirmed_by_human")

texto_status = json.dumps(status_ma, ensure_ascii=False)
caso("status sem segredo/PII",
     not re.search(r"(?i)(token|secret|password|credential|/home/|"
                   r"\(?\d{2}\)?\s?\d{4,5}-\d{4})", texto_status))

print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} falha(s).")
    sys.exit(1)
print("RESULTADO: todos os casos passaram.")
sys.exit(0)
