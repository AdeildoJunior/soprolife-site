#!/usr/bin/env bash
# SoproLife — M14.3A.2 — pacote de publicação manual do Apps Script.
#
# Monta (fora do repositório) um pacote EXATO para publicação manual no
# editor Apps Script: os 9 arquivos canônicos + manifesto + SHA256SUMS +
# checklist + ordem de cópia + inventário de símbolos + rollback.
#
# SEGURANÇA:
#   - dry-run por PADRÃO (nada é escrito sem --execute);
#   - escreve SOMENTE no diretório passado em --output;
#   - recusa --output dentro do repositório;
#   - recusa pacote quando o comparador retorna BLOCKED;
#   - --execute exige --remote-dir com resultado READY (sem prova de
#     paridade remota não há pacote);
#   - nunca acessa Google/rede, nunca executa clasp, nunca publica;
#   - nunca inclui data-private, token, credencial, URL ou ID privado
#     (varredura de segredos no pacote final).
#
# USO:
#   bash prepare-apps-script-publication-pack.sh \
#     --output /caminho/fora/do/repo \
#     --remote-dir /caminho/export-remoto \
#     [--allow-extra NOME]... [--manifest ARQUIVO] [--execute]
#
# Docs: painel-soprolife/docs/m14-3a2-paridade-apps-script.md

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_RAIZ="$(cd "$SCRIPT_DIR/../.." && pwd)"
MANIFESTO="$REPO_RAIZ/painel-soprolife/core/contracts/apps-script-publication-manifest.json"
APPS_DIR="$REPO_RAIZ/painel-soprolife/apps-script"

OUTPUT=""
REMOTE_DIR=""
EXECUTE=0
ALLOW_EXTRA=()

falha() { echo "ERRO: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --output)      OUTPUT="${2:-}"; shift 2 ;;
    --remote-dir)  REMOTE_DIR="${2:-}"; shift 2 ;;
    --manifest)    MANIFESTO="${2:-}"; shift 2 ;;
    --allow-extra) ALLOW_EXTRA+=("${2:-}"); shift 2 ;;
    --execute)     EXECUTE=1; shift ;;
    -h|--help)     grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) falha "argumento desconhecido: $1 (use --help)" ;;
  esac
done

[ -n "$OUTPUT" ] || falha "--output é obrigatório (diretório FORA do repositório)"
[ -f "$MANIFESTO" ] || falha "manifesto não encontrado: $MANIFESTO"

OUTPUT_REAL="$(realpath -m "$OUTPUT")"
case "$OUTPUT_REAL" in
  "$REPO_RAIZ"|"$REPO_RAIZ"/*)
    falha "--output está DENTRO do repositório ($REPO_RAIZ) — recusado" ;;
esac

echo "══ Pacote de publicação Apps Script (M14.3A.2) ══"
if [ "$EXECUTE" -eq 0 ]; then
  echo "MODO: dry-run (padrão) — NADA será escrito. Use --execute para montar."
else
  echo "MODO: execute — pacote será montado em $OUTPUT_REAL"
fi

# 1) Manifesto precisa estar em dia com os .gs do Git.
echo
echo "1) Validando manifesto contra os arquivos canônicos do Git…"
if ! PYTHONDONTWRITEBYTECODE=1 python3 \
    "$SCRIPT_DIR/generate-apps-script-publication-manifest.py" \
    --check --output "$MANIFESTO"; then
  falha "manifesto desatualizado — rode o gerador com --write e revise o diff"
fi

# 2) Comparação com a exportação remota (prova de paridade).
COMPARE_JSON=""
if [ -n "$REMOTE_DIR" ]; then
  [ -d "$REMOTE_DIR" ] || falha "--remote-dir não é diretório: $REMOTE_DIR"
  echo
  echo "2) Comparando exportação remota × manifesto…"
  COMPARE_TMP="$(mktemp -d "${TMPDIR:-/tmp}/soprolife-pack.XXXXXX")"
  COMPARE_JSON="$COMPARE_TMP/comparacao.json"
  ALLOW_ARGS=()
  for extra in ${ALLOW_EXTRA[@]+"${ALLOW_EXTRA[@]}"}; do
    ALLOW_ARGS+=(--allow-extra "$extra")
  done
  PYTHONDONTWRITEBYTECODE=1 python3 \
    "$SCRIPT_DIR/compare-apps-script-export.py" \
    --remote-dir "$REMOTE_DIR" --manifest "$MANIFESTO" \
    --json-report "$COMPARE_JSON" \
    ${ALLOW_ARGS[@]+"${ALLOW_ARGS[@]}"} > "$COMPARE_TMP/comparacao.txt" 2>&1
  COMPARE_EXIT=$?
  RESULTADO="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["resultado"])' "$COMPARE_JSON" 2>/dev/null || echo ERRO)"
  echo "   resultado do comparador: $RESULTADO (exit $COMPARE_EXIT)"
  if [ "$RESULTADO" != "READY" ] || [ "$COMPARE_EXIT" -ne 0 ]; then
    echo
    tail -25 "$COMPARE_TMP/comparacao.txt" | sed 's/^/   | /'
    falha "comparação NÃO está READY — pacote recusado (nada foi escrito)"
  fi
else
  echo
  echo "2) SEM --remote-dir: comparação com o remoto ainda pendente."
fi

# 3) --execute exige prova de paridade READY.
if [ "$EXECUTE" -eq 1 ] && [ -z "$REMOTE_DIR" ]; then
  falha "--execute exige --remote-dir com resultado READY (bloqueador Codex: sem inventário/backup remoto não há publicação controlada)"
fi

# Lista exata do conteúdo do pacote (allowlist).
ARQUIVOS_CANONICOS="$(python3 -c '
import json, sys
m = json.load(open(sys.argv[1]))
print("\n".join(a["nome_apps_script"] for a in m["arquivos"]))' "$MANIFESTO")"

echo
echo "3) Conteúdo do pacote:"
echo "   arquivos/  ← os 9 canônicos do Git:"
echo "$ARQUIVOS_CANONICOS" | sed 's/^/     - /'
echo "   apps-script-publication-manifest.json"
echo "   SHA256SUMS"
echo "   INVENTARIO-SIMBOLOS.txt"
echo "   ORDEM-DE-COPIA.txt"
echo "   CHECKLIST-PUBLICACAO.md"
echo "   ROLLBACK.md"
[ -n "$COMPARE_JSON" ] && echo "   evidencia-comparacao.json (+ .txt)"

if [ "$EXECUTE" -eq 0 ]; then
  echo
  echo "DRY-RUN CONCLUÍDO — nada foi escrito. Repita com --execute."
  exit 0
fi

# 4) Montagem real, somente em $OUTPUT_REAL.
echo
echo "4) Montando pacote…"
if [ -e "$OUTPUT_REAL" ] && [ -n "$(ls -A "$OUTPUT_REAL" 2>/dev/null)" ]; then
  falha "--output existe e não está vazio: $OUTPUT_REAL"
fi
mkdir -p "$OUTPUT_REAL/arquivos" || falha "não consegui criar $OUTPUT_REAL"

while IFS= read -r nome; do
  cp "$APPS_DIR/$nome" "$OUTPUT_REAL/arquivos/$nome" \
    || falha "falha copiando $nome"
done <<< "$ARQUIVOS_CANONICOS"

cp "$MANIFESTO" "$OUTPUT_REAL/apps-script-publication-manifest.json"
if [ -n "$COMPARE_JSON" ]; then
  cp "$COMPARE_JSON" "$OUTPUT_REAL/evidencia-comparacao.json"
  cp "$COMPARE_TMP/comparacao.txt" "$OUTPUT_REAL/evidencia-comparacao.txt"
fi

( cd "$OUTPUT_REAL/arquivos" && sha256sum -- *.gs ) > "$OUTPUT_REAL/SHA256SUMS" \
  || falha "falha gerando SHA256SUMS"

PYTHONDONTWRITEBYTECODE=1 python3 - "$MANIFESTO" "$OUTPUT_REAL" <<'PY' || falha "falha gerando inventário/ordem"
import json, sys
manifesto = json.load(open(sys.argv[1], encoding="utf-8"))
saida = sys.argv[2]

with open(f"{saida}/INVENTARIO-SIMBOLOS.txt", "w", encoding="utf-8") as f:
    f.write("Inventário de símbolos globais — pacote de publicação SoproLife\n")
    f.write(f"Total: {manifesto['total_globais']} símbolos em "
            f"{manifesto['total_arquivos']} arquivos.\n")
    f.write("Após publicar, o projeto remoto deve ter EXATAMENTE estes "
            "símbolos,\ncada um definido UMA única vez.\n\n")
    for a in manifesto["arquivos"]:
        f.write(f"== {a['nome_apps_script']} ({len(a['globais'])} globais, "
                f"sha256 {a['sha256']})\n")
        for g in a["globais"]:
            f.write(f"   {g}\n")
        f.write("\n")

ordem = manifesto["regras_publicacao"]["ordem_publicacao"]
detalhes = {a["nome_apps_script"]: a for a in manifesto["arquivos"]}
with open(f"{saida}/ORDEM-DE-COPIA.txt", "w", encoding="utf-8") as f:
    f.write("Ordem de substituição no editor Apps Script (relatório Codex, seção G)\n")
    f.write("Colar o conteúdo de arquivos/<nome> por cima do arquivo remoto\n")
    f.write("de mesmo nome, UM de cada vez, salvando entre cada passo:\n\n")
    for i, nome in enumerate(ordem, 1):
        a = detalhes[nome]
        deploy = "exige NOVA implantação" if a["exige_nova_implantacao"] \
            else "salvar já basta"
        f.write(f"{i}. {nome}  [{a['estado']}; {deploy}]\n")
    f.write("\nDepois: conferir contagem final (9 arquivos, "
            f"{manifesto['total_globais']} globais) com INVENTARIO-SIMBOLOS.txt.\n")
PY

cat > "$OUTPUT_REAL/CHECKLIST-PUBLICACAO.md" <<'FIM'
# Checklist de publicação manual — SoproLife Apps Script (M14.3A.2)

Pré-requisito deste pacote: comparador READY (evidencia-comparacao.json).
NUNCA pular etapas. NUNCA publicar com item em aberto.

## Antes de tocar no editor
- [ ] Backup restaurável do remoto salvo FORA do Git (export completo).
- [ ] remote-inventory.json preenchido: arquivos, triggers, NOMES das
      propriedades (nunca valores), versão ativa da implantação.
- [ ] Acionadores instaláveis antigos/perigosos removidos e anotados.
- [ ] Nenhum dado real de paciente envolvido em nenhuma etapa.

## Publicação (ver ORDEM-DE-COPIA.txt)
- [ ] Substituir os 9 arquivos NA ORDEM indicada, salvando um a um.
- [ ] Remover somente extras já exportados, comparados e aprovados.
- [ ] Contagem final: exatamente 9 arquivos; símbolos conforme
      INVENTARIO-SIMBOLOS.txt (cada global definido UMA vez).
- [ ] Script Properties: API_TOKEN preservado; BUILD_VERSION atualizado
      para o commit publicado (sem expor valores em lugar nenhum).
- [ ] NÃO executar setup, manutenção, _test*, writers nem funções
      bloqueadas durante a publicação.

## Nova implantação (Web App)
- [ ] Implantar → Gerenciar implantações → editar a implantação
      EXISTENTE → Nova versão (mantém deployment ID e URL).
- [ ] Descrição da versão = commit publicado.
- [ ] Executor/acesso INALTERADOS.

## Prova não mutante
- [ ] POST com JSON malformado "{" sem token direto na URL /exec:
      esperado ok=false, code=400, "Corpo da requisição inválido".
      (Falha ANTES de autenticação/planilha — não escreve nada.)

## Evidência
- [ ] Guardar: versão anterior e nova, data/hora, quem publicou,
      SHA256SUMS conferido, resultado da prova não mutante.
- [ ] Em problema: seguir ROLLBACK.md imediatamente.
FIM

cat > "$OUTPUT_REAL/ROLLBACK.md" <<'FIM'
# Rollback — SoproLife Apps Script (M14.3A.2)

Duas camadas INDEPENDENTES. Rollback da implantação NÃO restaura o
código salvo nem os acionadores — são passos separados.

## 1. Implantação (Web App /exec)
1. Implantar → Gerenciar implantações → editar a implantação ativa.
2. Selecionar a VERSÃO ANTERIOR (anotada no checklist) → Implantar.
3. Deployment ID e URL permanecem os mesmos.
4. Restaurar BUILD_VERSION anterior nas Script Properties.

## 2. Código salvo no editor
1. Restaurar cada arquivo a partir do backup completo feito ANTES da
   publicação (export guardado fora do Git).
2. Conferir contagem de arquivos e globals contra o inventário coletado
   antes da mudança.

## 3. Acionadores (triggers)
1. Restaurar/remover acionadores instaláveis conforme o
   remote-inventory.json coletado antes da publicação.
2. onOpen/onEdit simples seguem o código salvo — voltam com o passo 2.

## 4. Verificação pós-rollback
1. Repetir SOMENTE a prova não mutante (POST "{" malformado → 400).
2. NÃO executar writers, setup ou _test* para "testar".
3. Dados no Sheets NÃO são revertidos automaticamente — esta publicação
   não executa writers, então não deve haver dado a reverter.
FIM

# 5) Varredura de segredos no pacote final (defesa em profundidade).
echo "5) Varredura de segredos no pacote…"
PYTHONDONTWRITEBYTECODE=1 python3 - "$SCRIPT_DIR" "$OUTPUT_REAL" <<'PY' || falha "SEGREDO SUSPEITO no pacote — pacote INVÁLIDO, apague $OUTPUT_REAL"
import os, sys
sys.path.insert(0, sys.argv[1])
import apps_script_parity as parity
raiz = sys.argv[2]
problemas = 0
for base, _dirs, arquivos in os.walk(raiz):
    for nome in arquivos:
        caminho = os.path.join(base, nome)
        with open(caminho, encoding="utf-8", errors="replace") as f:
            texto = f.read()
        for padrao in parity.varrer_segredos(texto):
            print(f"SEGREDO SUSPEITO: {os.path.relpath(caminho, raiz)} "
                  f"(padrão {padrao})")
            problemas += 1
raise SystemExit(1 if problemas else 0)
PY

echo
echo "✓ PACOTE MONTADO em $OUTPUT_REAL"
echo "  Verificação independente:  (cd '$OUTPUT_REAL/arquivos' && sha256sum -c ../SHA256SUMS)"
echo "  Próximo passo humano: CHECKLIST-PUBLICACAO.md (nada é publicado por script)."
exit 0
