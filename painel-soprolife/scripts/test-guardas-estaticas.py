#!/usr/bin/env python3
"""
SoproLife — Guardas estáticas M14.3A (anti-regressão dos bloqueadores).

Falha se qualquer padrão proibido pela auditoria independente voltar ao
código ativo:

  - clearContents/reescrita integral no cadastro mestre (CRM Pacientes);
  - deduplicação/merge por primeiro nome;
  - lastRow/contador como autoridade de ID;
  - fallback legado do Manual das Abas (conteúdo desatualizado);
  - exclusão automática de backup;
  - alias semântico perigoso tratado como automático;
  - ausência dos contratos versionados executáveis;
  - data mensal/ausente promovida a dia/hoje;
  - falso sucesso com cabeçalho incompatível (writers sem fail-closed);
  - upsert destrutivo (reescrita da linha inteira);
  - setup destrutivo (clear/exclusão de abas no instalador);
  - base paralela de pessoas Pastore dentro da API principal.

Offline, sem rede, sem data-private. Roda no quality gate.

Uso:
    python3 painel-soprolife/scripts/test-guardas-estaticas.py
Exit: 0 = todas as guardas OK | 1 = regressão detectada.
"""

import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FALHAS = 0


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + detalhe if detalhe else ''}")


def ler(rel):
    p = RAIZ / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


sync = ler("apps-script/sync-crm-pacientes.gs")
api = ler("apps-script/command-center-api.gs")
conv = ler("apps-script/converter-lead-em-paciente.gs")
manual = ler("apps-script/limpar-leads-e-manual-abas.gs")
organizar = ler("apps-script/organizar-leads-operacionais.gs")
template = ler("apps-script/soprolife-sheets-template.gs")
contratos_gs = ler("apps-script/contratos-canonicos.gs")
pastore_gs = ler("apps-script/pastore-staging.gs")
reconciliar = ler("scripts/reconciliar-historico.py")

print("── Cadastro mestre (CRM Pacientes) ──")
# Padrão EXECUTÁVEL (chamada), não menção em comentário de documentação.
caso("sync não chama .clearContents()", not re.search(r"\.clearContents\(\)", sync))
caso("sync não reescreve a aba (_reescreverAbaPacientes removido)",
     "_reescreverAbaPacientes" not in sync)
caso("sync está explicitamente BLOQUEADO e lança erro",
     "BLOQUEADO" in sync and "throw new Error" in sync)
caso("menu do sync removido (function onOpen_syncCRM)",
     "function onOpen_syncCRM" not in sync)
caso("api nunca chama .clearContents()", not re.search(r"\.clearContents\(\)", api))

print("── Deduplicação por nome (proibida) ──")
caso("sync sem chave por nome", '"nome:" +' not in sync and "nome:" not in sync.replace("nome sozinho", ""))
caso("sync sem chave por telefone absoluto", '"tel:" +' not in sync)
caso("reconciliar: nome nunca vincula (só pending/ambiguous)",
     "nome nunca vincula" in reconciliar.lower() or "nome sozinho" in reconciliar.lower())
caso("reconciliar usa listas de candidatos (não sets de cobertura)",
     "por_tel: dict" in reconciliar and "tels_pacientes: set" not in reconciliar)
caso("reconciliar emite os 4 estados",
     all(f'"{e}"' in reconciliar for e in ("linked", "pending", "ambiguous", "unmatchable")))

print("── IDs: autoridade do servidor ──")
caso("api sem _nextId ativo", "_nextId(" not in api)
caso("api usa ctNovoIdServidor", "ctNovoIdServidor(" in api)
caso("converter sem ID por lastRow/padStart",
     not re.search(r"getLastRow\(\)[^\n]*padStart", conv))
caso("converter BLOQUEADO (2ª rodada): lança erro e não converte",
     "BLOQUEADA" in conv and "throw new Error" in conv and "_cvCriarCRMPacientes" not in conv)
caso("converter sem dedupe por telefone", "_cvConjuntoTelefones" not in conv)
caso("converter sem datas por regex própria",
     "\\d{2}\\/\\d{2}" not in conv and "\\d{2}/\\d{2}" not in conv)
caso("contrato de ID rejeita contador/linha",
     "uuid inválido" in contratos_gs)
caso("chave do navegador documentada como idempotency key (nunca data do exame)",
     "nunca a data do exame" in contratos_gs)

print("── Fluxos destrutivos globais (allowlist só para saídas derivadas) ──")
# M14.3A (correção final) — descoberta AUTOMÁTICA de todo .gs em apps-script/
# (nunca uma lista fixa que possa esquecer um arquivo novo). deleteRow(/
# deleteSheet( são proibidos em TODO arquivo, sem exceção nenhuma — nenhum
# writer operacional desta base tem motivo legítimo para excluir linha/aba.
# .clear()/.clearContents() têm allowlist EXPLÍCITA e MÍNIMA, por arquivo E
# pela aba derivada/regenerável específica que ele tem permissão de limpar —
# qualquer outro arquivo (inclusive um novo, ainda não listado aqui) é
# tratado como operacional e reprovado se contiver esses padrões.
GS_DIR = RAIZ / "apps-script"
GS_TODOS = {p.relative_to(RAIZ).as_posix(): p.read_text(encoding="utf-8") for p in sorted(GS_DIR.glob("*.gs"))}
caso("descoberta automática encontrou os .gs esperados (nenhum a menos)",
     len(GS_TODOS) >= 9)

# aba derivada/regenerável que cada arquivo tem permissão de limpar — nunca
# "o arquivo inteiro está isento", só a chamada .clear()/.clearContents()
# específica documentada abaixo.
GS_CLEAR_ALLOWLIST = {
    "apps-script/manual-das-abas.gs": 'var _MA_ABA = "Manual das Abas"',
    "apps-script/soprolife-sheets-template.gs": '"Resumo Dashboard"',
}
for rel, texto in GS_TODOS.items():
    caso(f"{rel}: sem deleteRow( (proibido sem exceção)", "deleteRow(" not in texto)
    caso(f"{rel}: sem deleteSheet( (proibido sem exceção)", "deleteSheet(" not in texto)
    if rel in GS_CLEAR_ALLOWLIST:
        caso(f"{rel}: allowlist de .clear() aponta para a aba derivada esperada",
             GS_CLEAR_ALLOWLIST[rel] in texto)
    else:
        caso(f"{rel}: sem .clearContents()", not re.search(r"\.clearContents\(\)", texto))
        caso(f"{rel}: sem .clear() de aba", not re.search(r"\.clear\(\)", texto))
caso("organizar-leads BLOQUEADO com erro explícito",
     "BLOQUEADO" in organizar and "throw new Error" in organizar)
caso("limpar-leads é aditivo (sem remoção de linhas demo)",
     "_removerLinhasDemonstrativas" not in manual and "remoção automática BLOQUEADA" in manual)
caso("dedupLeadsConvertidos: aplicação bloqueada (só relatório)",
     "BLOQUEADO pela M14.3A" in api and "exclusão está bloqueada" in api)
caso("allowlist: manual-das-abas só limpa a própria aba",
     ler("apps-script/manual-das-abas.gs").count(".clear()") == 1
     and 'var _MA_ABA = "Manual das Abas"' in ler("apps-script/manual-das-abas.gs"))

print("── Writers: patch por presença e idempotência (2ª rodada) ──")
caso("writers usam ctCamposPresentes (fail-closed p/ TODO campo)",
     api.count("ctCamposPresentes(") >= 2)
caso("eventos clínicos: chave separada do ID (coluna técnica)",
     "campos.idempotency_key = chave" in api and "ctNovoIdServidor(contrato.prefixo" in api)
caso("mesma chave + payload diferente = 409", "409" in api and "fingerprint" in api)
caso("pré-validação ANTES de criar colunas técnicas (zero mutação em erro)",
     api.index("var preCheck = ctPlanejarEscrita") < api.index("_ensureExtraColumns(sheet, contrato.colunas_tecnicas)"))
caso("lock nos writers (busca+validação+escrita)", api.count("_lockScriptOuErro()") >= 3)
caso("update por presença em interação de clínica",
     "patch POR PRESENÇA" in api)
caso("financeiro: id_atendimento obrigatório", "id_atendimento é obrigatório" in api)
caso("financeiro: sem 250/Tabela/0 inventados",
     "fallback: _EF_VALOR_TABELA_PADRAO" not in api and ': "Tabela"' not in api
     and "REGRA FORMAL DE ZERO" in api)
caso("cliente financeiro sem defaults silenciosos",
     "fallback: EF_VALOR_TABELA_PADRAO" not in ler("js/espirometria-financeiro.js")
     and 'fallback: "Tabela"' not in ler("js/espirometria-financeiro.js"))

print("── UI: datas clínicas e chave persistente ──")
appjs = ler("js/app.js")
caso("data clínica não nasce com hoje (sem value: hojeISO())",
     "value: hojeISO()" not in appjs)
caso("chave de idempotência sobrevive a refresh (sessionStorage)",
     "sessionStorage" in appjs and "obterIdempotencyKeyEspi" in appjs)
caso("modal Pastore sem custos pré-preenchidos com 0",
     not re.search(r'id: "repasse_pastore"[^}]*value: 0', appjs))

print("── Pastore staging: contrato de data e sem defaults ──")
caso("staging usa ctValidarDataComPrecisao", "ctValidarDataComPrecisao" in pastore_gs)
caso("staging: status obrigatório (sem default Realizado)",
     '"status"]' in pastore_gs and 'data.status                       || "Realizado"' not in pastore_gs)
caso("staging: ausência de custo/repasse não vira 0",
     "_numOuVazio" in pastore_gs and ': 0,' not in pastore_gs)

print("── Privacidade: IDs protegidos nas saídas ──")
caso("relatório/plano nunca emitem ID completo (_id_protegido)",
     "_id_protegido" in reconciliar and reconciliar.count("_id_protegido(") >= 6)
caso("scanner flagra texto embutido em ID", "_ID_COM_TEXTO_RE" in reconciliar)
caso("paciente_id órfão vira pending/orphan_link", "orphan_link" in reconciliar)
caso("Pastore por nome/telefone é candidato, nunca cobertura",
     "nunca prova" in reconciliar and "candidatos" in reconciliar)

print("── Manual das Abas ──")
caso("fallback legado removido (sem conteúdo ▶ Financeiro)", '"▶ Financeiro"' not in manual)
caso("sem gerador legado (_conteudoManualAbas)", "_conteudoManualAbas" not in manual)
caso("ausência do gerado falha com instrução clara",
     "throw new Error" in manual and "manual-das-abas.gs" in manual)

print("── Backups ──")
caso("limpar-leads não exclui backup", "deleteSheet" not in manual)
caso("organizar-leads não exclui backup", "deleteSheet" not in organizar)
caso("backup demo com sufixo único (segundos+UUID)",
     "HHmmss" in manual and "getUuid" in manual)
caso("organizar-leads (migração destrutiva) segue bloqueado",
     "throw new Error(_OP_LEADS_BLOQUEADO)" in organizar)

print("── Instalador (fronteira segura) ──")
caso("template não chama .clearContents()", not re.search(r"\.clearContents\(\)", template))
caso("template sem exclusão de abas", "ss.deleteSheet(" not in template)
caso("template pula aba com conteúdo", "pulada" in template)

print("── Datas ──")
caso("nenhum .gs promove mês a dia 01", all('return "01/"' not in t and '"01/" +' not in t
                                            for t in (sync, api, conv, manual, organizar, template)))
caso("nenhum fallback de data para hoje no sync", "|| _hoje()" not in sync)
caso("api valida data+precisão (ctValidarDataComPrecisao)",
     api.count("ctValidarDataComPrecisao(") >= 3)
caso("conversor bloqueado não grava data nenhuma (sem caminho executável)",
     "appendRow" not in conv and "setValue" not in conv)
caso("contrato recusa precisão incoerente", "precisão incoerente" in contratos_gs)

print("── Contratos versionados fail-closed ──")
schemas = ler("core/contracts/registros-schemas.json")
caso("registros-schemas.json existe e é JSON", bool(schemas) and bool(json.loads(schemas)))
caso("contratos-canonicos.gs existe", bool(contratos_gs))
caso("versões coincidem",
     bool(schemas) and json.loads(schemas).get("schema_version", "") in contratos_gs)
caso("api é fail-closed sem contratos instalados", "_ERRO_CONTRATOS_AUSENTES" in api)
caso("api planeja escrita pelo contrato", api.count("ctPlanejarUpsert(") >= 3)
caso("resposta expõe campos persistidos", "campos_persistidos" in api)
caso("erro lista colunas ausentes (nada gravado)", "nada foi gravado" in contratos_gs)

print("── Upsert seletivo ──")
caso("api não reescreve linha inteira no update",
     not re.search(r"row\.forEach\(function\(val, i\)", api))
caso("patch preserva ausências (contrato)", "NUNCA vira limpeza" in contratos_gs)

print("── Aliases perigosos (decisão manual) ──")
enums = json.loads(ler("core/contracts/enums-canonicos.json"))
dominios = enums["dominios"]
PERIGOSOS = [
    ("status_exame", "confirmado"),
    ("status_exame", "agendado"),
    ("status_exame", "concluido"),
    ("consentimento_whatsapp", "nao confirmado"),
    ("local_atendimento", "pastore"),
    ("local_atendimento", "coworking"),
    ("etapa_lead", "convertido em paciente"),
]
for dominio, valor in PERIGOSOS:
    dom = dominios[dominio]
    caso(f"'{valor}' em {dominio} exige decisão manual",
         valor not in dom.get("aliases", {}) and valor in dom.get("decisao_manual", {}))
caso("todo domínio declara decisao_manual",
     all("decisao_manual" in d for d in dominios.values()))

print("── Financeiro: cardinalidade e órfãos ──")
caso("contrato declara 1:N (não impõe 1:1)",
     "N movimentos" in schemas and "receita_principal" in schemas)
caso("duplicata é conflito visível (não 'última vence' silencioso)",
     "CONFLITO" in schemas)
caso("órfãos nunca apagados (reconciliar)", "nunca apagar" in reconciliar)

print("── Pastore: sem base paralela na API principal ──")
caso("api não carrega campos de pessoa da Pastore",
     "paciente_nome" not in api and "paciente_whatsapp" not in api)
caso("writer Pastore isolado e marcado STAGING",
     "STAGING" in pastore_gs and "_registrarAtendimentoPastore" in pastore_gs)
caso("api guarda a ação quando staging não instalado",
     "pastore-staging.gs não está instalado" in api)

print("── Privacidade da ferramenta ──")
caso("plano é validado ANTES de gravar", "problemas_plano" in reconciliar)
caso("salt efêmero por execução", "_SALT_EXECUCAO" in reconciliar)
caso("hash de nome declarado como não-anonimização forte",
     "anonimização forte" in reconciliar)
caso("todo ID sai protegido (categoria+hash), nunca em claro",
     "def _id_protegido" in reconciliar)

print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} regressão(ões) detectada(s).")
    sys.exit(1)
print("RESULTADO: todas as guardas passaram.")
sys.exit(0)
