---
name: soprolife-panel-ui-ux
description: Padrões de UI/UX do Painel SoproLife — uma tela, uma função; gráficos acima e detalhe abaixo em telas analíticas; tooltips curtos explicando significado, fonte e interpretação; sempre testar visualmente.
---

# soprolife-panel-ui-ux

## Objetivo
Padronizar decisões de UI/UX do painel: clareza de cada tela, hierarquia visual (resumo → detalhe), e tooltips curtos — sem misturar com mudança de dado ou lógica de negócio.

## Quando usar
- Tarefas de melhoria visual, layout, tooltip, texto ou hierarquia de informação no painel.

## Quando não usar
- Tarefas que exigem mudar cálculo ou dado real — resolver primeiro com `soprolife-finance-costs` ou `soprolife-b2b-pcmso-crm`, depois vir aqui só para a parte visual.
- Mudanças de infraestrutura/deploy → usar `soprolife-vps-deploy-safe`.

## Arquivos e pastas relevantes
- `painel-soprolife/index.html`, `painel-soprolife/css/style.css`.
- `painel-soprolife/js/app.js` — funções `render*`.

## Fluxo padrão
1. Ler o HTML/CSS/JS da tela atual antes de mudar qualquer coisa.
2. Definir a função principal daquela tela — uma tela deve comunicar uma função principal com clareza.
3. Em telas analíticas: gráficos/resumo no topo, detalhe (tabela) embaixo.
4. Tooltips curtos (1 frase): o que é, de onde vem o dado, como interpretar.
5. Testar visualmente (Chrome headless ou navegador real) antes de considerar pronto — ler o código não é suficiente.
6. Se `app.js` mudou: `node --check painel-soprolife/js/app.js`.

## Comandos seguros
```
node --check painel-soprolife/js/app.js
cd painel-soprolife && python3 -m http.server 8000
grep -n "data-tip\|title=" painel-soprolife/js/app.js
```

## Checks obrigatórios
- `node --check` sem erro sempre que `app.js` mudar.
- Teste visual real (não só leitura de código) antes de reportar a tarefa como pronta.
- Tooltip novo em elemento customizado deve ter texto curto (1 frase), `tabindex="0"` e `aria-label` (acessibilidade por teclado).

## Proibições
- Não alterar dado, Apps Script ou VPS numa tarefa puramente visual.
- Não esconder informação crítica atrás de um tooltip — o tooltip complementa o texto visível, não substitui.
- Não poluir card com parágrafo longo — priorizar número grande + rótulo curto + tooltip opcional.
- O gráfico de "Origem" deve deixar claro que mostra canal de aquisição, **não** etapa comercial.
- O gráfico de "Funil" deve deixar claro que mostra etapa comercial atual.
- A tabela mostra nomes/detalhe individual; o gráfico mostra agregados — não confundir os dois papéis na mesma visualização.

## Erros já observados
- Usuário confundiu o gráfico "Origem dos leads" com "quem já converteu", porque título e subtítulo não deixavam clara a diferença entre canal de aquisição e etapa comercial. Resolvido com título/subtítulo mais explícitos + tooltip específico em cada gráfico explicando o que ele mostra e o que não mostra.
- Painel com `overflow: hidden` em cards/painéis cortando o balão de tooltips customizados que apareciam por cima do elemento — sempre conferir se o container tem overflow visível quando o tooltip está perto da borda.

## Exemplos de prompts
- "Deixe esse card mais compacto, só os números principais."
- "Adiciona uma explicação curta nesse gráfico pra não confundir com o outro."
- "Testa visualmente antes de dizer que terminou."

## Comando de revisão após a tarefa
```
node --check painel-soprolife/js/app.js && echo "abrir http://localhost:8000 e testar visualmente"
```
