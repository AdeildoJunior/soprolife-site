#!/usr/bin/env python3
"""SoproLife — M14.3A.2 — gerador do manifesto canônico de publicação.

Gera painel-soprolife/core/contracts/apps-script-publication-manifest.json
a partir dos 9 arquivos canônicos de painel-soprolife/apps-script/.

Totalmente offline e determinístico: mesma árvore de arquivos ⇒ mesmos
bytes de saída (sem timestamp, sem ordem dependente de dict/locale).

USO:
  python3 generate-apps-script-publication-manifest.py --write
  python3 generate-apps-script-publication-manifest.py --check
  python3 generate-apps-script-publication-manifest.py --print-summary
  python3 generate-apps-script-publication-manifest.py --write --output CAMINHO

Falha (exit != 0) quando:
  - falta arquivo canônico;
  - existe símbolo global duplicado entre arquivos canônicos;
  - (--check) o manifesto em disco difere do recalculado.
"""

import argparse
import json
import os
import sys

sys.dont_write_bytecode = True  # nenhum bytecode no repositório
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apps_script_parity as parity  # noqa: E402

RAIZ_REPO = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
SAIDA_PADRAO = os.path.join(
    RAIZ_REPO, "painel-soprolife", "core", "contracts",
    "apps-script-publication-manifest.json",
)

# Metadados estáticos por arquivo canônico (revisados no relatório Codex
# 20260712-221016): classificação, papel no Web App e estado operacional.
METADADOS = {
    "command-center-api.gs": {
        "classificacao": "api_web_app",
        "obrigatorio": True,
        "web_app": True,
        "exige_nova_implantacao": True,
        "estado": "operacional",
    },
    "contratos-canonicos.gs": {
        "classificacao": "contratos_fail_closed",
        "obrigatorio": True,
        "web_app": True,
        "exige_nova_implantacao": True,
        "estado": "operacional",
    },
    "pastore-staging.gs": {
        "classificacao": "writer_staging_pastore",
        "obrigatorio": True,
        "web_app": True,
        "exige_nova_implantacao": True,
        "estado": "operacional",
    },
    "converter-lead-em-paciente.gs": {
        "classificacao": "stub_seguranca",
        "obrigatorio": True,
        "web_app": True,
        "exige_nova_implantacao": True,
        "estado": "stub",
    },
    "sync-crm-pacientes.gs": {
        "classificacao": "stub_seguranca",
        "obrigatorio": True,
        "web_app": False,
        "exige_nova_implantacao": False,
        "estado": "stub",
    },
    "organizar-leads-operacionais.gs": {
        "classificacao": "stub_seguranca",
        "obrigatorio": True,
        "web_app": False,
        "exige_nova_implantacao": False,
        "estado": "stub",
    },
    "soprolife-sheets-template.gs": {
        "classificacao": "instalador_e_triggers",
        "obrigatorio": True,
        "web_app": False,
        "exige_nova_implantacao": False,
        "estado": "instalador",
    },
    "manual-das-abas.gs": {
        "classificacao": "documentacao_gerada",
        "obrigatorio": True,
        "web_app": False,
        "exige_nova_implantacao": False,
        "estado": "gerado",
    },
    "limpar-leads-e-manual-abas.gs": {
        "classificacao": "manutencao_manual",
        "obrigatorio": True,
        "web_app": False,
        "exige_nova_implantacao": False,
        "estado": "manutencao_legado_compativel",
    },
}

# Ordem segura de substituição no editor remoto (relatório Codex, seção G).
ORDEM_PUBLICACAO = [
    "contratos-canonicos.gs",
    "command-center-api.gs",
    "pastore-staging.gs",
    "converter-lead-em-paciente.gs",
    "sync-crm-pacientes.gs",
    "organizar-leads-operacionais.gs",
    "soprolife-sheets-template.gs",
    "manual-das-abas.gs",
    "limpar-leads-e-manual-abas.gs",
]


def construir_manifesto(dir_apps_script):
    """Monta o dict do manifesto. Levanta SystemExit em erro canônico."""
    erros = []
    analises = {}
    for nome in parity.ARQUIVOS_CANONICOS:
        caminho = os.path.join(dir_apps_script, nome)
        if not os.path.isfile(caminho):
            erros.append(f"arquivo canônico ausente: {caminho}")
            continue
        with open(caminho, "rb") as f:
            analises[nome] = parity.analisar_fonte(f.read())

    if erros:
        for e in erros:
            print(f"ERRO: {e}", file=sys.stderr)
        raise SystemExit(1)

    # Globals duplicados entre arquivos canônicos = erro fatal.
    dono = {}
    duplicados = []
    for nome in parity.ARQUIVOS_CANONICOS:
        for simbolo in analises[nome]["globais"]:
            s = simbolo["nome"]
            if s in dono:
                duplicados.append(f"{s} (em {dono[s]} e {nome})")
            else:
                dono[s] = nome
    if duplicados:
        for d in sorted(duplicados):
            print(f"ERRO: global duplicado entre canônicos: {d}", file=sys.stderr)
        raise SystemExit(1)

    arquivos = []
    total_globais = 0
    for nome in parity.ARQUIVOS_CANONICOS:
        a = analises[nome]
        meta = METADADOS[nome]
        globais_nomes = set(s["nome"] for s in a["globais"])
        # Dependências: identificadores deste arquivo definidos em OUTRO.
        dependencias = {}
        for outro in parity.ARQUIVOS_CANONICOS:
            if outro == nome:
                continue
            fornecidos = set(s["nome"] for s in analises[outro]["globais"])
            usados = sorted(
                (a["identificadores"] & fornecidos) - globais_nomes
            )
            if usados:
                dependencias[outro] = usados
        total_globais += len(a["globais"])
        arquivos.append({
            "nome_apps_script": nome,
            "caminho_local": f"{parity.DIR_APPS_SCRIPT}/{nome}",
            "classificacao": meta["classificacao"],
            "obrigatorio": meta["obrigatorio"],
            "web_app": meta["web_app"],
            "exige_nova_implantacao": meta["exige_nova_implantacao"],
            "estado": meta["estado"],
            "bytes": a["bytes"],
            "linhas": a["linhas"],
            "sha256": a["sha256"],
            "globais": sorted(globais_nomes),
            "entrypoints": sorted(
                a["entrypoints"], key=lambda e: (e["tipo"], e["nome"])
            ),
            "dependencias": {
                k: dependencias[k] for k in sorted(dependencias)
            },
            "operacoes_escrita": {
                k: a["operacoes_escrita"][k]
                for k in sorted(a["operacoes_escrita"])
            },
        })

    return {
        "manifesto": "apps-script-publication-manifest",
        "versao": 1,
        "descricao": (
            "Manifesto canônico dos 9 arquivos .gs aprovados no Git para o "
            "projeto Apps Script do SoproLife Command Center. Fonte única "
            "para comparação com exportação remota e para o pacote de "
            "publicação manual. Determinístico: sem timestamp."
        ),
        "gerado_por": "painel-soprolife/scripts/generate-apps-script-publication-manifest.py",
        "total_arquivos": len(arquivos),
        "total_globais": total_globais,
        "regras_publicacao": {
            "ordem_publicacao": ORDEM_PUBLICACAO,
            "entrypoints_unicos": parity.ENTRYPOINTS_UNICOS,
            "simbolos_proibidos": parity.SIMBOLOS_PROIBIDOS,
            "funcoes_bloqueadas": parity.FUNCOES_BLOQUEADAS,
            "operacoes_destrutivas": parity.OPERACOES_DESTRUTIVAS,
            "operacoes_monitoradas": parity.OPERACOES_ESCRITA,
            "padroes_nome_proibido": parity.PADROES_NOME_PROIBIDO,
        },
        "arquivos": arquivos,
    }


def serializar(manifesto):
    """JSON determinístico, UTF-8, newline final."""
    return json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n"


def imprimir_resumo(manifesto):
    print("Manifesto de publicação Apps Script — resumo")
    print(f"  arquivos canônicos: {manifesto['total_arquivos']}")
    print(f"  símbolos globais:   {manifesto['total_globais']}")
    for arq in manifesto["arquivos"]:
        eps = ", ".join(e["nome"] for e in arq["entrypoints"]) or "-"
        print(
            f"  {arq['nome_apps_script']:35s} {arq['linhas']:5d} linhas  "
            f"{len(arq['globais']):3d} globais  sha256 {arq['sha256'][:12]}…"
        )
        print(f"    estado={arq['estado']}  entrypoints: {eps}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help="grava o manifesto no destino")
    ap.add_argument("--check", action="store_true",
                    help="falha se o manifesto em disco difere do recalculado")
    ap.add_argument("--print-summary", action="store_true",
                    help="imprime resumo legível")
    ap.add_argument("--output", default=SAIDA_PADRAO,
                    help="caminho de saída (padrão: core/contracts)")
    ap.add_argument("--apps-script-dir",
                    default=os.path.join(RAIZ_REPO, parity.DIR_APPS_SCRIPT),
                    help="diretório dos .gs canônicos (para testes)")
    args = ap.parse_args()

    if not (args.write or args.check or args.print_summary):
        ap.error("escolha ao menos um modo: --write, --check ou --print-summary")

    manifesto = construir_manifesto(args.apps_script_dir)
    texto = serializar(manifesto)

    if args.print_summary:
        imprimir_resumo(manifesto)

    if args.check:
        if not os.path.isfile(args.output):
            print(f"ERRO --check: manifesto inexistente: {args.output}",
                  file=sys.stderr)
            return 1
        with open(args.output, "r", encoding="utf-8") as f:
            atual = f.read()
        if atual != texto:
            print("ERRO --check: manifesto em disco difere do recalculado.",
                  file=sys.stderr)
            print("Rode com --write para atualizar (e revise o diff).",
                  file=sys.stderr)
            return 1
        print(f"OK --check: manifesto em dia ({args.output}).")

    if args.write:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(texto)
        print(f"OK --write: manifesto gravado em {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
