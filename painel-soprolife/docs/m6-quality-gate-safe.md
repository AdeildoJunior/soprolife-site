# M6 — Quality Gate Seguro

## Objetivo

Um comando único, local e offline, que responde "posso commitar?" antes
de qualquer commit/push/deploy:

```bash
bash painel-soprolife/scripts/quality-gate-safe.sh
```

Exit `0` = tudo passou. Exit `1` = corrigir antes de seguir. O script
NÃO usa `set -e` de forma agressiva: acumula falhas, mostra cada uma com
o final da saída e nunca fecha o terminal do usuário.

## O que ele testa (9 seções, ~2s)

1. Sintaxe JS (`app.js`, `operational-actions.js`, `b2b-actions.js`);
2. Suítes JS: M4 (22 casos) e M5 (30 casos);
3. Sintaxe Python (gerador e teste da Saúde Operacional);
4. Suíte Python M3 (19 casos, fixtures sintéticas);
5. Geração real da Saúde Operacional (`--write`, arquivo gitignored);
6. JSON válido: demos commitáveis de `data/` (todos os `*.json` não
   `.local`) + o summary de saúde;
7. `check-access.sh` completo (auditoria anti-PII/segredos);
8. `git diff --check` + **guard rails de staging**: falha se houver
   staged qualquer arquivo de `data-private/`, qualquer `*.local.json`
   (sem exceções por enquanto) ou nome contendo
   `token|secret|credentials|application_default`;
9. Lista informativa dos modificados (não bloqueia).

## O que ele NÃO testa (de propósito)

- Nada que dependa de rede, ADC, Google ou VPS — por isso **não** roda
  `update-local-data.sh`;
- Teste visual do painel (continua manual, skill panel-ui-ux);
- O conteúdo semântico dos dados reais (só estrutura/segurança);
- Estado da VPS (timer, units, HTTP) — isso é a Saúde Operacional em
  produção, não o gate.

## Quando rodar

- SEMPRE antes de `git commit` (é a materialização do passo "checks" do
  fluxo etapa → checks → pacote → revisão → commit);
- depois de resolver conflitos/rebase;
- antes de gerar o pacote de revisão para o GPT.

## Como interpretar

- `✓ PASSOU` → liberado para commit. **Deploy continua exigindo** a
  revisão do GPT e o fluxo da VPS (skills soprolife-vps-safe /
  soprolife-vps-deploy-safe) — o gate é condição NECESSÁRIA, não
  suficiente.
- `✗ FALHOU` → cada check com problema mostra as últimas linhas da
  saída; guard rail de staging mostra o arquivo e o comando
  `git restore --staged` para corrigir.

## Limitações

- Guard rails olham NOMES de arquivos staged, não conteúdo (o conteúdo
  é papel do check-access e do pii_guard);
- A seção 5 sobrescreve o summary local de saúde (comportamento normal
  do gerador);
- Não substitui o teste visual nem a revisão humana do diff.

## Regra

**Deploy só depois do quality gate passar** — sem exceção. Se o gate
falhar na máquina de quem vai commitar, o problema se resolve ANTES de
qualquer push.
