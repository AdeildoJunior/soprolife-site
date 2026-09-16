# M26.15 — Painel mostrava a versão superada do PDF técnico

16/09/2026. Worktree `claude-m26-15-corrigir-versao-pdf-vigente`, base `painel-soprolife-v01` (`469ef1a`).
Entregue: commit `68d7231`, integrado por fast-forward, deploy na VPS confirmado (`git status` limpo em
`68d7231` em oficial e produção), health `200`/`200`. Sem migration, sem restart de serviço (mudança
100% de assets estáticos servidos pelo Nginx — `soprolife-m15-api.service` não carrega nenhum arquivo
alterado nesta etapa).

## Contexto real

Depois da M26.14 (troca do PDF técnico de uma corretiva ainda não iniciada, aplicada ao caso real de
LAU-000038/Claudia), o usuário reportou que o painel continuava mostrando o PDF ANTIGO (sem a anotação
do motivo do exame) na tela "Exame técnico (MIR)", mesmo depois da troca confirmada.

## Investigação

Eliminação sistemática, sem supor nada:

1. **Arquivo errado?** SHA256 do PDF na pasta indicada pelo usuário batia exatamente com o que já
   havia sido enviado — não era isso.
2. **Conteúdo realmente sem a anotação?** O PDF (lido diretamente) contém "Cansaço a esclarecer" —
   não era isso.
3. **Backend guardou o arquivo errado?** Script de leitura direta no banco (produção, somente leitura)
   confirmou: a versão CORRENTE de LAU-000038 contém a anotação no texto extraído; só a versão antiga
   (superada) não contém. O backend estava certo.
4. **Causa real, no frontend:** `loadDocument()` em `report-workflow.js` escolhia a versão a mostrar
   com `detail.versoes.find((item) => item.kind === "original")` — isso pega o PRIMEIRO elemento do
   array (a versão mais antiga), nunca a vigente. O bug é antigo mas nunca havia se manifestado: antes
   da M26.14, todo documento tinha no máximo UMA versão `original`, então "primeira" e "vigente" eram
   sempre a mesma. A M26.14 passou a permitir uma segunda versão `original` — e a tela continuou presa
   na primeira.

## Correção

Reaproveitado o helper já existente `versionByKind(kind)` (usado corretamente em
`renderPhysicianDetail()`), que percorre o array de trás para frente e pega a última versão daquele
`kind` — ou seja, a mais recente. Sem mudança nenhuma de backend: os bytes armazenados já estavam
corretos.

## Testes

- Novo teste Playwright dedicado (`test-m26-15-versao-pdf-vigente.js`): documento sintético com DUAS
  versões `kind=original`; confirma que o painel busca o conteúdo (`apiBlob`) da versão mais recente,
  nunca da mais antiga.
- **Confirmação de que o teste realmente pega o bug**: código revertido temporariamente para a versão
  quebrada → teste falha exatamente como esperado (`esperava a versão vigente ... pegou: .../versao-original-antiga/...`);
  código restaurado → teste volta a passar.
- Suítes existentes sem regressão: M24C estrutural (`test-m24a-report-workflow.js`) e M26.12 Playwright
  (16 cenários) — ambas passam limpas depois do bump de versão.
- `quality-gate-safe.sh`: as duas únicas falhas (M21, M25.26) são pré-existentes e ambientais — confirmado
  rodando os mesmos dois testes contra o commit-base `469ef1a` (antes de qualquer mudança desta etapa),
  com o resultado idêntico.
- Suíte completa de backend (pytest) não foi executada nesta etapa: nenhum arquivo de código de backend
  foi alterado (só o valor fixado de uma string em um teste de cache-busting já existente); o risco de
  regressão de backend é nulo.

Bump do carimbo de cache-busting de `report-workflow.css/js` (`2026091602 → 2026091603`), com o teste
de regressão correspondente atualizado junto.

## Resultado para o caso real

LAU-000038 (Claudia): a versão vigente do PDF técnico (a com a anotação do motivo do exame, aplicada
na M26.14) é, por construção, a de maior `version_number` entre as `kind=original` — exatamente a que
`versionByKind` agora seleciona. O painel deve exibir o PDF corrigido a partir deste deploy.
