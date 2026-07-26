#!/usr/bin/env bash
# M23.1 — Regressão do mecanismo de detecção de arquivos privados usado por
# check-access.sh (git check-ignore -q + caminho relativo à raiz do repo).
#
# Contexto: o terceiro deploy do M23 reportou "ERRO CRÍTICO ... NÃO está
# gitignored" para vários arquivos em painel-soprolife/data-private/ na VPS,
# mesmo com a regra ampla `painel-soprolife/data-private/` no .gitignore.
# Esta bateria prova, com repositórios Git sintéticos e efêmeros (sem tocar
# no repositório real nem na VPS), que o mecanismo em si:
#   1) funciona a partir da raiz do repositório;
#   2) funciona dentro de um worktree Git vinculado (git worktree add);
#   3) funciona quando o diretório de trabalho é acessado por um symlink;
#   4) falha fechado (relata corretamente como "NÃO ignorado") quando um
#      arquivo já está rastreado no índice, mesmo que combine com um padrão
#      do .gitignore — ou seja, arquivos rastreados NUNCA são silenciosamente
#      tratados como seguros.
#
# Isto isola a causa do aviso de produção: o mecanismo git check-ignore não
# está quebrado por raiz/worktree/symlink, e nenhum arquivo de
# data-private/ jamais foi rastreado neste repositório (confirmado à parte
# por `git log --all --diff-filter=A -- painel-soprolife/data-private/`).
# Ler o relatório oficial para a conclusão completa desta investigação.
#
# 100% offline, sem sudo, sem tocar no repositório real. Usa repositórios
# Git temporários descartáveis em um diretório efêmero.
#
# Uso: bash painel-soprolife/scripts/test-check-access-gitignore.sh
# Exit: 0 = todos os casos passaram | 1 = houve falha.
set -u

FALHAS=0
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TMP_ROOT"' EXIT

caso() {
  local nome="$1" esperado="$2" obtido="$3"
  if [[ "$esperado" == "$obtido" ]]; then
    echo "  PASS: $nome"
  else
    FALHAS=$((FALHAS + 1))
    echo "  FAIL: $nome — esperado '$esperado', obtido '$obtido'"
  fi
}

novo_repo_sintetico() {
  # Cria um repositório Git isolado com a MESMA regra de .gitignore usada no
  # repositório real para painel-soprolife/data-private/.
  local dir="$1"
  mkdir -p "$dir/painel-soprolife/data-private"
  (
    cd "$dir" || exit 1
    git init -q
    git config user.email "teste@example.com"
    git config user.name "Teste"
    printf 'painel-soprolife/data-private/\n' > .gitignore
    git add .gitignore
    git commit -q -m "init sintético"
  )
}

echo "── Cenário 1: raiz do repositório ──"
REPO1="$TMP_ROOT/repo-raiz"
novo_repo_sintetico "$REPO1"
echo '{"exemplo":true}' > "$REPO1/painel-soprolife/data-private/leads.local.json"
(
  cd "$REPO1" || exit 1
  git check-ignore -q painel-soprolife/data-private/leads.local.json
)
caso "arquivo não rastreado é reportado como ignorado (rc 0)" 0 $?

echo "── Cenário 2: worktree Git vinculado ──"
REPO2="$TMP_ROOT/repo-worktree-base"
novo_repo_sintetico "$REPO2"
WORKTREE2="$TMP_ROOT/repo-worktree-linked"
(cd "$REPO2" && git worktree add -q --detach "$WORKTREE2" HEAD) >/dev/null 2>&1
mkdir -p "$WORKTREE2/painel-soprolife/data-private"
echo '{"exemplo":true}' > "$WORKTREE2/painel-soprolife/data-private/financeiro-lancamentos.local.json"
(
  cd "$WORKTREE2" || exit 1
  git check-ignore -q painel-soprolife/data-private/financeiro-lancamentos.local.json
)
caso "mesmo arquivo, dentro de um worktree vinculado, continua ignorado (rc 0)" 0 $?

echo "── Cenário 3: diretório de trabalho acessado por symlink ──"
REPO3="$TMP_ROOT/repo-symlink-alvo"
novo_repo_sintetico "$REPO3"
LINK3="$TMP_ROOT/repo-symlink-acesso"
ln -s "$REPO3" "$LINK3"
echo '{"exemplo":true}' > "$LINK3/painel-soprolife/data-private/crm-contatos-b2b.local.json"
(
  cd "$LINK3" || exit 1
  git check-ignore -q painel-soprolife/data-private/crm-contatos-b2b.local.json
)
caso "mesmo arquivo, acessado via symlink do diretório, continua ignorado (rc 0)" 0 $?

echo "── Cenário 4: contraexemplo — arquivo JÁ rastreado (risco genuíno) ──"
REPO4="$TMP_ROOT/repo-rastreado"
novo_repo_sintetico "$REPO4"
(
  cd "$REPO4" || exit 1
  echo '{"nunca deveria ter sido commitado":true}' \
    > painel-soprolife/data-private/tracked-por-acidente.local.json
  git add -f painel-soprolife/data-private/tracked-por-acidente.local.json
  git commit -q -m "acidentalmente commitado (fixture de teste)"
)
(
  cd "$REPO4" || exit 1
  git check-ignore -q painel-soprolife/data-private/tracked-por-acidente.local.json
)
caso "arquivo JÁ rastreado nunca é relatado como seguro (rc != 0)" 1 $?
(
  cd "$REPO4" || exit 1
  git ls-files --error-unmatch \
    painel-soprolife/data-private/tracked-por-acidente.local.json >/dev/null 2>&1
)
caso "confirmação independente: o contraexemplo está mesmo rastreado (rc 0)" 0 $?

echo "── Confirmação: nenhum arquivo de data-private/ jamais foi rastreado no repositório real ──"
REAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
(
  cd "$REAL_ROOT" || exit 1
  [[ -z "$(git log --all --diff-filter=A --name-only --format= \
            -- painel-soprolife/data-private/ 2>/dev/null)" ]]
)
caso "histórico completo (todos os branches) nunca adicionou arquivo em data-private/" 0 $?

echo
if (( FALHAS )); then
  echo "RESULTADO: $FALHAS falha(s)."
  exit 1
fi
echo "RESULTADO: todos os casos passaram."
