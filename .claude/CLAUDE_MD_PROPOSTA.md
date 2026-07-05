# Proposta de atualização do CLAUDE.md

**Status: proposta apenas. O `CLAUDE.md` real na raiz do repositório não foi alterado.**

O `CLAUDE.md` atual documenta bem o contexto da empresa e o MVP inicial, mas ficou desatualizado em relação ao que o painel realmente é hoje. Esta proposta preenche as lacunas sem remover nada do conteúdo original — é para ser mesclada por quem revisar, não copiada às cegas.

## O que está faltando no CLAUDE.md atual

1. **Branch de trabalho** — não menciona `painel-soprolife-v01` como branch principal do painel.
2. **Estrutura real de pastas** — só lista os arquivos do MVP inicial; não menciona `painel-soprolife/data-private/`, `painel-soprolife/scripts/`, `painel-soprolife/apps-script/`, nem `check-access.sh`.
3. **Modelo de segurança de dados** — não explica a separação entre `data/` (público, alguns seguros) e `data-private/` (gitignored, nunca commitado), nem o padrão `*.local.json`.
4. **Regra de conversão canônica** — não menciona `isLeadConvertido()` como fonte única de verdade para o que conta como "convertido" no funil de leads.
5. **Regras de Custos & Investimentos** — não existe nenhuma menção a como calcular/exibir rateio entre sócios (campos estruturados, nunca inferir por texto).
6. **Deploy/VPS** — não existe nenhuma seção sobre como (e quando) o painel é publicado na VPS via Tailscale.
7. **Skills disponíveis** — agora que existem skills específicas do projeto, vale referenciá-las no CLAUDE.md para quem chegar depois.
8. **Regra de trabalho desatualizada** — a seção final ("O ChatGPT está conduzindo a arquitetura...") pode não refletir mais o fluxo atual, em que o próprio usuário conduz diretamente via Claude Code.

## Trechos sugeridos para adicionar

### Nova seção: "Branch e ambiente"
```md
## Branch e ambiente

Branch principal de trabalho do painel: `painel-soprolife-v01`.

Antes de qualquer alteração, confirme a branch atual com `git branch --show-current`
e o estado do repositório com `git status --short`.
```

### Nova seção: "Estrutura real do painel"
```md
## Estrutura real do painel (atualizada)

- painel-soprolife/index.html
- painel-soprolife/css/style.css
- painel-soprolife/js/app.js
- painel-soprolife/data/               — JSON públicos/seguros (alguns .local.json aqui também são gitignored)
- painel-soprolife/data-private/       — gitignored inteiro, NUNCA commitar
- painel-soprolife/scripts/            — scripts Python/bash de leitura, geração e verificação
- painel-soprolife/scripts/check-access.sh — auditoria de segurança, rodar antes de considerar uma tarefa pronta
- painel-soprolife/apps-script/        — templates de Apps Script seguros (sem URL/ID real da planilha)
```

### Nova seção: "Modelo de segurança de dados"
```md
## Modelo de segurança de dados

- `painel-soprolife/data-private/` é gitignored inteiro. Nunca commitar, nunca copiar conteúdo
  para skills, documentação ou exemplos.
- Qualquer `*.local.json` em qualquer pasta é gitignored por padrão (`painel-soprolife/**/*.local.json`).
- `app.js` nunca pode conter URL do Apps Script nem token — a escrita na planilha passa sempre
  pelo proxy local do Command Center, que injeta o token no servidor, nunca no browser.
- Use placeholders (`<TAILSCALE_IP>`, `<PAINEL_PORT>`, `<APPS_SCRIPT_URL>`, `<SHEETS_ID>`,
  `<TOKEN_LOCAL>`) em qualquer documentação, commit message ou skill — nunca o valor real.
- Antes de commitar, rodar `bash painel-soprolife/scripts/check-access.sh` a partir da raiz do repo.
```

### Nova seção: "Regra de conversão de leads"
```md
## Regra de conversão de leads

A única fonte de verdade sobre "o que conta como lead convertido" é a função
`isLeadConvertido()` em `painel-soprolife/js/app.js`. Qualquer gráfico, card ou
filtro que precise saber se um lead está convertido deve chamar essa função —
nunca reimplementar a regra separadamente (isso já causou divergência de números
na mesma tela).

"Convertido" inclui: paciente atendido, exame realizado, consulta realizada, e
parceria B2B fechada ("Parceiro ativo"). Origem (canal de aquisição) e etapa
(fase comercial) são conceitos independentes — um lead pode ter qualquer origem
e estar em qualquer etapa.
```

### Nova seção: "Regras de Custos & Investimentos"
```md
## Regras de Custos & Investimentos

Nunca confundir "responsável" (quem administra o item) com "quem pagou de fato"
(desembolso real). Nunca inferir rateio entre sócios por texto livre de
observação ou pelo campo `responsavel` — usar sempre campos estruturados:
`pago_adeildo`, `pago_faustino`, `pendente_adeildo`, `pendente_faustino`,
`pendente_sem_pagador`, `desconto_ou_baixa_sem_saida_caixa`. Se um valor é
desconhecido, mostrar "—" na interface — nunca `R$ 0,00` como se fosse dado real.
```

### Nova seção: "Skills disponíveis"
```md
## Skills disponíveis

Este projeto tem skills operacionais em `.claude/skills/`:

- `soprolife-safe-dev` — regras gerais de segurança e organização do código.
- `soprolife-vps-deploy-safe` — deploy seguro na VPS via Tailscale.
- `soprolife-sheets-sync` — integração com Google Sheets/Apps Script/ADC.
- `soprolife-b2b-pcmso-crm` — fluxo comercial de Leads e CRM B2B.
- `soprolife-finance-costs` — regras de Custos & Investimentos e rateio.
- `soprolife-panel-ui-ux` — padrões de UI/UX do painel.

Use a skill correspondente ao tipo de tarefa antes de editar código nessas áreas.
```

### Revisão sugerida para "Regra de trabalho"
A seção atual diz que "o ChatGPT está conduzindo a arquitetura". Sugerimos substituir por algo
neutro quanto à ferramenta, focado no processo:

```md
## Regra de trabalho

Antes de alterar arquivos funcionais (app.js, index.html, style.css, scripts,
Apps Script ou dados), aguarde uma solicitação explícita do usuário. Siga o
fluxo: diagnóstico → plano → alteração → revisão → teste → aprovação → commit
(ver skill `terminal-first-safe-agent`). Nunca commitar, dar push ou fazer
deploy sem autorização explícita na própria tarefa.
```

## O que NÃO propomos mudar
- O contexto da empresa (seção "Contexto da empresa") — continua correto e não precisa de mudança.
- O "Estilo visual" — continua válido.
- A regra de "não inserir dados reais de pacientes" — continua válida e deve ser reforçada, não substituída.

## Como aplicar esta proposta (quando aprovado)
1. Revisar cada seção proposta acima.
2. Copiar manualmente para `CLAUDE.md` real (ou pedir para o agente aplicar depois de aprovação explícita).
3. Não aplicar via commit automático — esta proposta é só para leitura humana nesta rodada.
