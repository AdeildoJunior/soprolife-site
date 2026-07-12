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

Uso:
    python3 painel-soprolife/scripts/generate-manual-abas-gs.py           # gera
    python3 painel-soprolife/scripts/generate-manual-abas-gs.py --check  # só confere se está atualizado

Nunca toca na planilha real — apenas gera o arquivo .gs no repositório.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MANIFEST = RAIZ / "core" / "contracts" / "abas-manifest.json"
DESTINO = RAIZ / "apps-script" / "manual-das-abas.gs"

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
                   + str(manifest.get("versao", "?")) + ")", "", "", "", "", "", "", "", ""])
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera apps-script/manual-das-abas.gs a partir do manifesto")
    parser.add_argument("--check", action="store_true",
                        help="não grava; falha (exit 1) se o .gs estiver desatualizado")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    conteudo = gerar_gs(manifest)

    if args.check:
        atual = DESTINO.read_text(encoding="utf-8") if DESTINO.exists() else ""
        if atual != conteudo:
            print("DESATUALIZADO: apps-script/manual-das-abas.gs não bate com o manifesto.")
            print("Rode: python3 painel-soprolife/scripts/generate-manual-abas-gs.py")
            return 1
        print("OK: manual-das-abas.gs está em dia com abas-manifest.json.")
        return 0

    DESTINO.write_text(conteudo, encoding="utf-8")
    print(f"Gerado: {DESTINO.relative_to(RAIZ.parent)}")
    print("Próximo passo (manual, fora deste repositório): colar no editor do Apps Script")
    print("e executar atualizarManualDasAbasSoproLife(). Nada foi publicado automaticamente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
