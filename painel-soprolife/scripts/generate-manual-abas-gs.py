#!/usr/bin/env python3
"""
SoproLife — Gerador do Apps Script "Manual das Abas" (M14.3).

Lê core/contracts/abas-manifest.json (fonte única de verdade sobre as abas)
e gera apps-script/manual-das-abas.gs — o Apps Script que cria/atualiza a
aba "Manual das Abas" na planilha privada.

Fluxo:
  core/contracts/abas-manifest.json  ← editar AQUI
        │  python3 scripts/generate-manual-abas-gs.py
        ▼
  apps-script/manual-das-abas.gs     ← colar no editor do Apps Script
        │  executar atualizarManualDasAbasSoproLife()
        ▼
  aba "Manual das Abas" na planilha  ← documentação viva

M14.3A.1 — três conceitos SEPARADOS (nunca confundir):
  1. versão do manifesto (abas-manifest.json);
  2. geração do manual-das-abas.gs (este script — só código local);
  3. execução real no Google Sheets (SEMPRE humana; registrada via
     --mark-published DEPOIS de executar de verdade no editor).

Gerar o .gs NÃO atualiza o Google Sheets. Enquanto o humano não executar
e registrar, o estado remoto é "publication_pending" — honesto, nunca
"atualizado".

Uso:
    python3 painel-soprolife/scripts/generate-manual-abas-gs.py                  # gera
    python3 painel-soprolife/scripts/generate-manual-abas-gs.py --check          # confere (exit 1 se desatualizado)
    python3 painel-soprolife/scripts/generate-manual-abas-gs.py --status         # estados: manifesto/geração/publicação
    python3 painel-soprolife/scripts/generate-manual-abas-gs.py --mark-published # atestado humano pós-execução real

Nunca toca na planilha real — apenas gera o arquivo .gs no repositório.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MANIFEST = RAIZ / "core" / "contracts" / "abas-manifest.json"
DESTINO = RAIZ / "apps-script" / "manual-das-abas.gs"
STATUS = RAIZ / "core" / "contracts" / "manual-abas-status.json"

ROTULOS_TIPO = {
    "cadastro": "Cadastro",
    "fonte_operacional": "Fonte operacional",
    "fonte_financeira": "Fonte financeira",
    "configuracao": "Configuração",
    "resumo": "Resumo",
    "relatorio": "Relatório",
    "log": "Log",
    "staging": "Staging",
    "backup": "Backup",
    "legado": "Legado",
    "arquivo": "Arquivo",
}

ROTULOS_STATUS = {
    "operacional": "Operacional",
    "apoio": "Apoio",
    "transicao": "Transição",
    "legado": "Legado",
    "arquivo": "Arquivo",
    "removida": "REMOVIDA",
}

ROTULOS_RECOMENDACAO = {
    "manter_visivel": "Manter visível",
    "ocultar_usuarios_comuns": "Ocultar para usuários comuns",
    "manter_como_configuracao": "Manter como configuração",
    "manter_como_log": "Manter como log",
    "manter_como_fonte": "Manter como fonte",
    "transformar_em_visao": "Transformar em visão/staging",
    "consolidar": "Consolidar",
    "arquivar": "Arquivar",
    "excluir_futuramente": "Excluir futuramente (decisão humana)",
    "nunca_excluir": "NUNCA excluir",
    "nunca_recriar": "NUNCA recriar",
}

SECOES_PAINEL = {
    "overview": "Painel Geral",
    "crm": "CRM",
    "leads": "Leads e Agendamentos",
    "tarefas": "Tarefas",
    "marketing": "Marketing & SEO",
    "lancamentos": "Entrada de Dados",
    "financeiro": "Financeiro",
    "parcerias-pastore": "Parcerias → Pastore",
    "custos-investimentos": "Custos & Investimentos",
    "documentos": "Documentos",
    "automacoes": "Automações",
}


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_manifesto() -> str:
    return hashlib.sha256(MANIFEST.read_bytes()).hexdigest()


def hash_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def carregar_status() -> dict:
    """Estado conhecido de geração/publicação (commitável, sem segredo)."""
    if not STATUS.exists():
        return {}
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def gravar_status(status: dict) -> None:
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")


def estado_publicacao(status: dict, sha_gs_atual: str) -> str:
    """Estado remoto honesto: sem consulta segura à versão remota, o máximo
    que sabemos vem do atestado humano registrado via --mark-published."""
    pub = status.get("publication") or {}
    if pub.get("publishedSha256") == sha_gs_atual and pub.get("publishedAt"):
        return "published_confirmed_by_human"
    if pub.get("publishedAt"):
        return "publication_pending"  # já publicou antes, mas o código mudou
    return "publication_pending"      # nunca houve atestado de publicação


def verificar_sem_data_fixa(conteudo_gs: str) -> list[str]:
    """Proíbe data fixa antiga na linha 'Atualizado em' do .gs gerado.

    A data de atualização da aba real só pode vir de new Date() no momento
    da execução — nunca de um literal embutido na geração.
    """
    problemas = []
    # A linha de dados "Atualizado em" com literal de data é proibida.
    if re.search(r'"Atualizado em"\s*,\s*"\d{1,2}/\d{1,2}/\d{4}', conteudo_gs):
        problemas.append("linha 'Atualizado em' contém data literal fixa")
    # O bloco que monta a linha deve usar new Date() (data viva na execução).
    idx = conteudo_gs.find('"Atualizado em"')
    if idx == -1:
        problemas.append("linha 'Atualizado em' ausente do .gs")
    elif "new Date()" not in conteudo_gs[idx:idx + 300]:
        problemas.append("'Atualizado em' não usa Utilities.formatDate(new Date(), ...)")
    if "function atualizarManualDasAbasSoproLife()" not in conteudo_gs:
        problemas.append("função real de atualização ausente do .gs")
    return problemas


def _sim_nao(v) -> str:
    return "Sim" if v else "Não"


def _lista(itens) -> str:
    return "; ".join(itens) if itens else "—"


def montar_linhas(manifest: dict) -> list[list[str]]:
    """Monta as linhas da aba Manual das Abas (matriz + detalhes)."""
    linhas: list[list[str]] = []

    # ── Cabeçalho e introdução ─────────────────────────────────────────────
    linhas.append(["MANUAL DAS ABAS — SoproLife Command Center", "", "", "", "", "", "", "", "", ""])
    linhas.append(["Como esta planilha se conecta ao painel:", "", "", "", "", "", "", "", "", ""])
    for passo in manifest.get("cadeia_de_dados", []):
        linhas.append(["", passo, "", "", "", "", "", "", "", ""])
    linhas.append(["", "", "", "", "", "", "", "", "", ""])

    # ── Matriz-resumo ──────────────────────────────────────────────────────
    linhas.append(["▶ MATRIZ DAS ABAS (visão rápida)", "", "", "", "", "", "", "", "", ""])
    linhas.append(["Aba", "Tipo", "Status", "Fonte", "Seção no painel",
                   "Dados pessoais?", "Dados financeiros?", "Pode ocultar?",
                   "Pode excluir?", "Recomendação"])
    for aba in manifest["abas"]:
        linhas.append([
            aba["nome"],
            ROTULOS_TIPO.get(aba["tipo"], aba["tipo"]),
            ROTULOS_STATUS.get(aba["status"], aba["status"]),
            "Oficial" if aba.get("fonte") == "oficial" else "Derivada",
            SECOES_PAINEL.get(aba.get("secao_command_center", ""), aba.get("secao_command_center", "—")),
            _sim_nao(aba.get("dados_pessoais")),
            _sim_nao(aba.get("dados_financeiros")),
            _sim_nao(aba.get("pode_ocultar")),
            _sim_nao(aba.get("pode_excluir")),
            ROTULOS_RECOMENDACAO.get(aba.get("recomendacao", ""), aba.get("recomendacao", "—")),
        ])
    linhas.append(["", "", "", "", "", "", "", "", "", ""])

    # ── Detalhe por aba ────────────────────────────────────────────────────
    for aba in manifest["abas"]:
        def det(rotulo: str, valor: str):
            linhas.append(["  " + rotulo, str(valor), "", "", "", "", "", "", "", ""])

        linhas.append(["▶ " + aba["nome"], aba.get("descricao_simples", ""), "", "", "", "", "", "", "", ""])
        det("Finalidade", aba.get("finalidade", "—"))
        det("Tipo", ROTULOS_TIPO.get(aba["tipo"], aba["tipo"]))
        det("Fonte oficial ou derivada", "Oficial" if aba.get("fonte") == "oficial" else "Derivada")
        det("Status", ROTULOS_STATUS.get(aba["status"], aba["status"]))
        det("Seção no Centro de Comando", SECOES_PAINEL.get(aba.get("secao_command_center", ""),
                                                            aba.get("secao_command_center", "—")))
        det("Página do painel", aba.get("pagina_painel", "—"))
        det("Quem grava (formulário/ação)", _lista(aba.get("quem_grava")))
        det("Apps Script responsável", _lista(aba.get("apps_script")))
        det("Scripts locais que leem", _lista(aba.get("leitores_locais")))
        det("Atualização", aba.get("atualizacao", "—"))
        det("Frequência", aba.get("frequencia", "—"))
        det("Contém dados pessoais?", _sim_nao(aba.get("dados_pessoais")))
        det("Contém dados clínicos?", _sim_nao(aba.get("dados_clinicos")))
        det("Contém dados financeiros?", _sim_nao(aba.get("dados_financeiros")))
        det("Contém apenas agregados?", _sim_nao(aba.get("apenas_agregados")))
        det("Quem pode editar", aba.get("quem_edita", "—"))
        det("Pode ser ocultada?", _sim_nao(aba.get("pode_ocultar")))
        det("Pode ser arquivada?", _sim_nao(aba.get("pode_arquivar")))
        det("Pode ser excluída?", _sim_nao(aba.get("pode_excluir")))
        det("Risco de exclusão", aba.get("risco_exclusao", "—"))
        det("Dependências", _lista(aba.get("dependencias")))
        det("Segurança", aba.get("seguranca", "—"))
        det("Recomendação de uso", ROTULOS_RECOMENDACAO.get(aba.get("recomendacao", ""),
                                                            aba.get("recomendacao", "—")))
        if aba.get("colunas_canonicas"):
            det("Colunas canônicas", ", ".join(aba["colunas_canonicas"]))
        if aba.get("colunas_propostas"):
            det("Colunas propostas (futuras)", ", ".join(aba["colunas_propostas"]))
        if aba.get("observacoes"):
            det("Observações", aba["observacoes"])
        linhas.append(["", "", "", "", "", "", "", "", "", ""])

    linhas.append(["Gerado por", "atualizarManualDasAbasSoproLife()  |  fonte: core/contracts/abas-manifest.json (versão "
                   + str(manifest.get("versao", "?"))
                   + ", manifesto sha256 " + hash_manifesto()[:12] + ")", "", "", "", "", "", "", "", ""])
    return linhas


def gerar_gs(manifest: dict) -> str:
    linhas = montar_linhas(manifest)
    # Uma linha de array por linha da planilha — legível no diff sem inflar o arquivo.
    linhas_js = "[\n  " + ",\n  ".join(json.dumps(l, ensure_ascii=False) for l in linhas) + "\n]"
    return f"""/**
 * manual-das-abas.gs — SoproLife Command Center (M14.3)
 *
 * ═══════════════════════════════════════════════════════════════════
 *  ARQUIVO GERADO — NÃO EDITAR À MÃO.
 *  Fonte de verdade: painel-soprolife/core/contracts/abas-manifest.json
 *  Para atualizar:  python3 painel-soprolife/scripts/generate-manual-abas-gs.py
 *  e cole este arquivo novamente no editor do Apps Script.
 * ═══════════════════════════════════════════════════════════════════
 *
 * Cria/atualiza a aba "Manual das Abas" com a documentação viva da
 * planilha: matriz-resumo de todas as abas + detalhe completo por aba
 * (tipo, status, relação com o Centro de Comando, quem grava, quem lê,
 * dados sensíveis, permissões e recomendação).
 *
 * SEGURANÇA — o que NÃO está neste arquivo:
 *   - ID ou URL da planilha real;
 *   - tokens, senhas ou chaves;
 *   - qualquer dado real de paciente (só metadados estruturais).
 *
 * Este script só escreve na aba "Manual das Abas". Nunca oculta,
 * renomeia ou exclui nenhuma outra aba.
 *
 * COMO EXECUTAR:
 *   1. Abra a planilha "SoproLife - Painel Interno - Dados Privados".
 *   2. Extensões → Apps Script → cole (ou atualize) este arquivo.
 *   3. Selecione atualizarManualDasAbasSoproLife e clique em "Executar".
 */

var _MA_ABA = "Manual das Abas";

// Conteúdo gerado a partir do manifesto (1 array por linha da aba).
var _MA_LINHAS = {linhas_js};

/**
 * Ponto de entrada: recria a aba "Manual das Abas" a partir do manifesto.
 */
function atualizarManualDasAbasSoproLife() {{
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(_MA_ABA);
  if (!sheet) {{
    sheet = ss.insertSheet(_MA_ABA);
  }} else {{
    sheet.clear();
  }}

  var nCols = _MA_LINHAS[0].length;
  var linhas = _MA_LINHAS.concat([[
    "Atualizado em",
    Utilities.formatDate(new Date(), "America/Sao_Paulo", "dd/MM/yyyy HH:mm"),
    "", "", "", "", "", "", "", ""
  ]]);

  sheet.getRange(1, 1, linhas.length, nCols).setValues(linhas);

  // Título principal.
  sheet.getRange(1, 1, 1, nCols)
    .setFontWeight("bold").setFontSize(13)
    .setFontColor("#ffffff").setBackground("#08243d");

  // Seções (coluna A começa com "▶") e cabeçalho da matriz.
  for (var i = 0; i < linhas.length; i++) {{
    var celA = String(linhas[i][0]);
    if (celA.indexOf("▶") === 0) {{
      sheet.getRange(i + 1, 1, 1, nCols)
        .setFontWeight("bold").setFontColor("#ffffff").setBackground("#0b6e9e");
    }}
    if (celA === "Aba") {{ // cabeçalho da matriz-resumo
      sheet.getRange(i + 1, 1, 1, nCols)
        .setFontWeight("bold").setBackground("#e8f1f5");
    }}
  }}

  sheet.setColumnWidth(1, 250);
  sheet.setColumnWidth(2, 560);
  for (var c = 3; c <= nCols; c++) sheet.setColumnWidth(c, 130);
  sheet.setFrozenRows(1);

  SpreadsheetApp.flush();
  Logger.log("Aba '" + _MA_ABA + "' atualizada com " + linhas.length + " linha(s).");
  _maAlertaSeguro("Manual das Abas atualizado (" + linhas.length + " linhas).");
}}

/** Alerta via UI quando disponível; em execução pelo editor, só loga. */
function _maAlertaSeguro(mensagem) {{
  try {{
    SpreadsheetApp.getUi().alert(mensagem);
  }} catch (e) {{
    Logger.log("(alerta via UI indisponível neste contexto)");
  }}
}}

/** Item de menu opcional. */
function onOpen_manualAbas() {{
  SpreadsheetApp.getUi()
    .createMenu("SoproLife")
    .addItem("Atualizar Manual das Abas", "atualizarManualDasAbasSoproLife")
    .addToUi();
}}
"""


def _imprimir_status(manifest: dict, status: dict, conteudo_esperado: str) -> None:
    atual = DESTINO.read_text(encoding="utf-8") if DESTINO.exists() else ""
    sha_atual = hash_texto(atual) if atual else None
    gs_em_dia = atual == conteudo_esperado
    pub = status.get("publication") or {}
    estado = estado_publicacao(status, sha_atual) if sha_atual else "unknown"

    print("Manual das Abas — três estados separados:")
    print(f"  1. Manifesto local:      v{manifest.get('versao', '?')} "
          f"(sha256 {hash_manifesto()[:12]})")
    print(f"  2. Código .gs gerado:    {'em dia com o manifesto' if gs_em_dia else 'DESATUALIZADO — regenerar'}")
    if estado == "published_confirmed_by_human":
        print(f"  3. Google Sheets real:   publicado (atestado humano em {pub.get('publishedAt')})")
    else:
        print("  3. Google Sheets real:   publicação pendente / estado desconhecido")
        print("     (gerar o .gs NÃO atualiza a planilha; execute no editor do")
        print("      Apps Script e registre com --mark-published)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera apps-script/manual-das-abas.gs a partir do manifesto")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--check", action="store_true",
                       help="não grava; falha (exit 1) se o .gs estiver desatualizado")
    grupo.add_argument("--status", action="store_true",
                       help="mostra manifesto/geração/publicação (sem gravar)")
    grupo.add_argument("--mark-published", action="store_true",
                       help="registra que o humano EXECUTOU o .gs atual no Google Sheets")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    conteudo = gerar_gs(manifest)
    status = carregar_status()

    if args.status:
        _imprimir_status(manifest, status, conteudo)
        return 0

    if args.mark_published:
        atual = DESTINO.read_text(encoding="utf-8") if DESTINO.exists() else ""
        if atual != conteudo:
            print("ERRO: o .gs está desatualizado em relação ao manifesto.")
            print("  Regenere, execute no Apps Script e só então marque como publicado.")
            return 1
        status.setdefault("schemaVersion", 1)
        status["manifestVersion"] = str(manifest.get("versao", "?"))
        status["manifestSha256"] = hash_manifesto()
        status["generationSha256"] = hash_texto(atual)
        status["publication"] = {
            "state": "published_confirmed_by_human",
            "publishedAt": _agora_iso(),
            "publishedSha256": hash_texto(atual),
            "note": "Atestado HUMANO: registrado após execução real de "
                    "atualizarManualDasAbasSoproLife() no editor do Apps Script. "
                    "Nenhuma verificação remota automática foi feita.",
        }
        gravar_status(status)
        print("Registrado: publicação atestada pelo humano para o .gs atual.")
        print(f"Status: {STATUS.relative_to(RAIZ.parent)}")
        return 0

    if args.check:
        falhas = []
        atual = DESTINO.read_text(encoding="utf-8") if DESTINO.exists() else ""
        if atual != conteudo:
            falhas.append("apps-script/manual-das-abas.gs não bate com o manifesto — "
                          "rode: python3 painel-soprolife/scripts/generate-manual-abas-gs.py")
        falhas.extend(verificar_sem_data_fixa(conteudo))
        if STATUS.exists():
            st_manifest = status.get("manifestVersion")
            if st_manifest and st_manifest != str(manifest.get("versao", "?")):
                falhas.append(f"manual-abas-status.json registra manifesto v{st_manifest}, "
                              f"mas o manifesto atual é v{manifest.get('versao')}")
            sha_gs = hash_texto(atual) if atual else None
            if sha_gs and status.get("generationSha256") not in (None, sha_gs):
                falhas.append("manual-abas-status.json não corresponde ao .gs atual — regenerar")
        if falhas:
            for f in falhas:
                print(f"FALHOU: {f}")
            return 1
        print("OK: manual-das-abas.gs está em dia com abas-manifest.json.")
        _imprimir_status(manifest, status, conteudo)
        return 0

    DESTINO.write_text(conteudo, encoding="utf-8")
    sha_novo = hash_texto(conteudo)
    pub_anterior = status.get("publication") or {}
    status.setdefault("schemaVersion", 1)
    status["manifestVersion"] = str(manifest.get("versao", "?"))
    status["manifestSha256"] = hash_manifesto()
    status["generationSha256"] = sha_novo
    status["generatedAt"] = _agora_iso()
    if pub_anterior.get("publishedSha256") != sha_novo:
        status["publication"] = {
            "state": "publication_pending",
            "publishedAt": pub_anterior.get("publishedAt"),
            "publishedSha256": pub_anterior.get("publishedSha256"),
            "note": "Código local mais novo que o último estado conhecido do "
                    "Google Sheets. Executar no Apps Script e registrar com "
                    "--mark-published.",
        }
    gravar_status(status)

    print(f"Gerado: {DESTINO.relative_to(RAIZ.parent)}")
    print(f"Status: {STATUS.relative_to(RAIZ.parent)}")
    print()
    _imprimir_status(manifest, status, conteudo)
    print()
    print("Próximo passo (manual, fora deste repositório): colar no editor do Apps Script,")
    print("executar atualizarManualDasAbasSoproLife() e registrar com --mark-published.")
    print("Nada foi publicado automaticamente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
