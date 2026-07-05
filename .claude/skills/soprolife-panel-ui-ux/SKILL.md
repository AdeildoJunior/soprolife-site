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

## Blocos internos de card (label acima, valor abaixo)
- Quando um card tiver campos com texto longo (ex.: validade/calibração, próxima ação, observações), usar um bloco interno com rótulo pequeno acima (caixa alta, cor secundária/`--muted`) e o valor abaixo (line-height confortável, ex. 1.5+) — nunca label e valor lado a lado tipo tabela (`justify-content: space-between` com valor alinhado à direita), que é o que dá aparência improvisada.
- Todos os blocos equivalentes de um mesmo card — e entre cards irmãos do mesmo grid — devem começar exatamente no mesmo eixo visual esquerdo (`align-items: start`, mesma estrutura de coluna em todos).
- Se o card tiver uma área de chips (documentos, tags) acima dos metadados principais, garantir separação visual clara entre as duas áreas (margin/gap suficiente) — não deixar chips e blocos de metadado "grudados".
- Preferir dar a cada bloco uma leve identidade visual (fundo suave `var(--soft)`, borda `var(--line)`, cantos arredondados, padding generoso) em vez de texto solto direto no card — isso é o que dá o acabamento premium/corporativo. Referência de implementação: `.document-equip-field` / `.document-equip-field-label` / `.document-equip-field-value` em `style.css`.
- Em telas com poucos registros (2-4) e muita informação por item, preferir layout premium por card a uma tabela apertada — mais fácil de manter o alinhamento e a hierarquia do que forçar colunas de tabela.

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
- Antes de aprovar visualmente, checar sobreposição em colunas, badges, chips, tooltips e textos monetários — inclusive em telas menores (testar pelo menos uma largura estreita, não só desktop).
- Em tabelas/cards com chips, validar que textos longos não sobrepõem colunas ou blocos vizinhos (checar `white-space`, `overflow-wrap` e se o container tem `flex-wrap`/`grid` de verdade, não só `max-width` sem quebra de linha interna do chip).
- Em qualquer revisão visual de card/bloco, checar também: alinhamento pelo início da frase (não centralizado nem alinhado à direita escondido), consistência entre colunas/blocos equivalentes (mesmo eixo, mesmo padrão visual), line-height confortável em texto longo, quebra de linha elegante (sem cortar palavra feio), e se o resultado passa a impressão premium/corporativa ou parece improvisado.
- Especificamente: nenhum desalinhamento entre blocos irmãos, nenhuma quebra feia de texto, e nenhuma falta de hierarquia visual (rótulo vs. valor precisa se distinguir por tamanho/peso/cor, não só por posição).

## Proibições
- Não alterar dado, Apps Script ou VPS numa tarefa puramente visual.
- Não esconder informação crítica atrás de um tooltip — o tooltip complementa o texto visível, não substitui.
- Não poluir card com parágrafo longo — priorizar número grande + rótulo curto + tooltip opcional.
- Não deixar texto "solto" sem estrutura visual em campos como validade/calibração, próxima ação ou observações — usar o padrão de bloco (label acima, valor abaixo, mesmo eixo) descrito em "Blocos internos de card".
- O gráfico de "Origem" deve deixar claro que mostra canal de aquisição, **não** etapa comercial.
- O gráfico de "Funil" deve deixar claro que mostra etapa comercial atual.
- A tabela mostra nomes/detalhe individual; o gráfico mostra agregados — não confundir os dois papéis na mesma visualização.

## Erros já observados
- Usuário confundiu o gráfico "Origem dos leads" com "quem já converteu", porque título e subtítulo não deixavam clara a diferença entre canal de aquisição e etapa comercial. Resolvido com título/subtítulo mais explícitos + tooltip específico em cada gráfico explicando o que ele mostra e o que não mostra.
- Painel com `overflow: hidden` em cards/painéis cortando o balão de tooltips customizados que apareciam por cima do elemento — sempre conferir se o container tem overflow visível quando o tooltip está perto da borda.
- Tabela de Documentos → Equipamentos com chips de texto longo (ex.: "Guia de recolhimento tributário (ICMS-ST)") estourando a célula e sobrepondo a coluna vizinha — causa raiz era `.badge` global com `white-space: nowrap`, então um `max-width` no container não bastava (o chip não quebra o próprio texto). Resolvido migrando para cards por equipamento (grid responsivo) com `white-space: normal` + `overflow-wrap: anywhere` só nos chips daquela seção, e rótulo curto no chip (texto completo preservado em `title`/`aria-label`). Preferir cards/grid a tabela larga quando há poucos registros (2-4) e muita informação por item.
- Nos mesmos cards de Equipamentos, depois de virar card, os campos "Validade/Calibração" e "Próxima ação" ainda usavam `.document-meta` (label à esquerda, valor alinhado à direita, `justify-content: space-between`) — com frases longas isso ficava com o texto começando em posições diferentes e sem sensação premium. Resolvido criando um padrão dedicado (`.document-equip-field`) com label pequeno acima e valor abaixo, todos os blocos no mesmo eixo esquerdo, fundo suave e borda sutil por bloco. Importante: `.document-meta` continua em uso pelos cards de "Controle documental" (CNPJ, Licença Sanitária etc.) e não foi alterada — cada card com esse tipo de problema deve ganhar sua própria classe de bloco, não reaproveitar `.document-meta` só porque já existe.

## Exemplos de prompts
- "Deixe esse card mais compacto, só os números principais."
- "Adiciona uma explicação curta nesse gráfico pra não confundir com o outro."
- "Testa visualmente antes de dizer que terminou."

## Comando de revisão após a tarefa
```
node --check painel-soprolife/js/app.js && echo "abrir http://localhost:8000 e testar visualmente"
```
