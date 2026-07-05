---
name: soprolife-safe-dev
description: Constituição prática de desenvolvimento no repositório SoproLife — separação entre site público e painel privado, o que nunca pode ser commitado, e os checks obrigatórios antes de considerar uma mudança pronta.
---

# soprolife-safe-dev

## Objetivo
Ser a referência prática de segurança e organização para qualquer tarefa de código no repositório SoproLife — o que pode e não pode ser tocado, o que nunca pode ser commitado, e quais checks rodar antes de considerar uma tarefa pronta.

## Quando usar
- Qualquer tarefa de código em `~/soprolife-site` — painel ou site público.
- Antes de editar `app.js`, `index.html`, `style.css`, scripts ou dados.

## Quando não usar
- Deploy na VPS → usar `soprolife-vps-deploy-safe`.
- Sincronização com Google Sheets/Apps Script → usar `soprolife-sheets-sync`.
- Regras de negócio de Leads/CRM B2B → usar `soprolife-b2b-pcmso-crm`.
- Regras de Custos & Investimentos → usar `soprolife-finance-costs`.
- Mudança puramente visual → usar `soprolife-panel-ui-ux`.

## Arquivos e pastas relevantes
- `painel-soprolife/js/app.js` — lógica do painel (sem tokens, sem URLs secretas).
- `painel-soprolife/index.html`, `painel-soprolife/css/style.css`.
- `painel-soprolife/data/` — JSON públicos/seguros (alguns `.local.json` aqui também são gitignored de propósito).
- `painel-soprolife/data-private/` — **gitignored inteiro**, nunca commitar.
- `painel-soprolife/scripts/` — scripts Python/bash de leitura, geração e verificação.
- `painel-soprolife/scripts/check-access.sh` — auditoria de segurança do painel (rodar a partir da raiz do repo).
- `painel-soprolife/apps-script/` — templates de Apps Script seguros (sem ID/URL real da planilha).
- `.gitignore` (raiz) — define os padrões `painel-soprolife/data-private/`, `painel-soprolife/**/*.local.json`, `.env`, etc.

## Fluxo padrão
1. `git branch --show-current` — confirmar que está em `painel-soprolife-v01`.
2. `git status --short` — ver o que já está pendente antes de começar.
3. Ler os arquivos relevantes antes de editar.
4. Separar mudança funcional (código) de mudança de dado (JSON) — não misturar as duas no mesmo commit sem necessidade.
5. Editar o mínimo necessário.
6. Se `app.js` mudou: `node --check painel-soprolife/js/app.js`.
7. Rodar `bash painel-soprolife/scripts/check-access.sh` **a partir da raiz do repositório** (o script usa caminhos relativos a `~/soprolife-site`).
8. `git diff --stat` para revisão final.

## Comandos seguros
```
git status --short
git branch --show-current
git diff --stat
node --check painel-soprolife/js/app.js
bash painel-soprolife/scripts/check-access.sh
python3 -m json.tool painel-soprolife/data/<arquivo>.json
```

## Checks obrigatórios
- `node --check` sem erro sempre que `app.js` mudar.
- `check-access.sh` deve terminar com exit code 0.
- Nenhum arquivo de `data-private/` ou `*.local.json` sensível aparecendo em `git status`/`git diff`.
- JSON válido (`python3 -m json.tool`) sempre que um `.json` for editado manualmente.

## Proibições
- Não commitar `painel-soprolife/data-private/` nem qualquer `*.local.json` sensível.
- Não commitar `application_default_credentials.json`, tokens, `.env` ou `command-center-config.local.json` real.
- Não expor URL ou token do Apps Script no `app.js` (o fluxo correto é sempre via proxy local do Command Center).
- Não alterar o proxy do Command Center sem entender que o token nunca pode chegar ao browser.
- Não misturar alteração funcional com alteração de dado no mesmo commit sem necessidade clara.
- Não commitar, dar push ou fazer deploy sem autorização explícita do usuário.

## Erros já observados
- Mudança de dado feita só no arquivo local e nunca sincronizada com a VPS — o painel ao vivo continuou mostrando dado antigo mesmo depois do código novo já estar no ar.
- JSON de resumo (`*-summary.local.json`) faltando campos que uma versão nova do `app.js` já esperava, quebrando o cálculo silenciosamente (mostrando `R$ 0,00` ou "—" onde deveria ter valor real).
- Cache do navegador servindo uma versão antiga de um JSON local mesmo depois do dado já ter sido corrigido (resolvido com cache-busting nas leituras de dados locais).

## Exemplos de prompts
- "Adicione um card novo em Custos & Investimentos sem tocar nos dados."
- "Corrija esse bug visual sem alterar a lógica de negócio."
- "Rode o check-access antes de me dizer que terminou."

## Comando de revisão após a tarefa
```
git status --short && git diff --stat && node --check painel-soprolife/js/app.js
```
