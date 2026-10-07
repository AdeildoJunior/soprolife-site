# M26.20 — Modernização dos gráficos de laudos

**Data:** 07/10/2026
**Branch de trabalho:** `claude-m26-20-graficos-laudos`
**Base:** `origin/painel-soprolife-v01` (`673b0e6`)
**Worktree:** `/home/fedorasurf/soprolife-worktrees/claude-m26-20-graficos-laudos`
**Escopo tocado:** modal "Gráficos dos laudos" do Command Center.
Nada de NFS-e, portal de resultados, site público, migrations ou dados reais.

---

## 1. Problemas visuais corrigidos

A auditoria achou **três causas-raiz no `css/style.css`**, não no modal. O CSS
global do painel impõe regras a toda tabela e a toda célula, e elas venciam
tudo dentro do card de indicadores:

| # | Causa real | Efeito que você via | Correção |
|---|---|---|---|
| 1 | `css/style.css:486` — `table { min-width: 860px }` | Tabela de "Ver dados do gráfico" com 860px dentro de um card de ~590px: **a tabela ultrapassava o card** | `.report-statistics table { min-width: 0 }` + `.rs-table-wrap` com rolagem própria |
| 2 | `css/style.css:5122` — bloco "Tabelas premium v3" com `th { … !important }` e `td { … !important }` | Padding, borda, cor e tamanho de fonte das tabelas largas de operação aplicados dentro do card; **rótulo de linha em CAIXA ALTA** ("REDUÇÃO DE CVF E VEF1") | Override explícito e localizado, só nas propriedades que o bloco força |
| 3 | `overflow-wrap: anywhere` herdado nas células | Cabeçalho "Laudos" picado em **"LA/UD/OS"** | Quebra só entre palavras no `thead` |

Além dessas, os defeitos de layout propriamente ditos:

- **Alturas inconsistentes.** A grade era `repeat(2, 1fr)` com cards de altura
  livre: abrir a tabela de um card esticava só ele e a linha ficava torta.
  Agora `align-items: stretch`, cada card é uma coluna flex e o `<details>`
  fica ancorado no pé (`margin-top: auto`) — cards da mesma faixa têm sempre
  a mesma altura. **Verificado por teste**, não a olho.
- **Gráfico invadindo card vizinho.** A moldura `.rs-chart` agora tem altura
  explícita que já inclui a faixa de rótulos do eixo, e o teste mede se algum
  filho sai do retângulo do card.
- **Tabela expandida dentro do próprio card.** `.rs-table-wrap` com
  `max-height` + `overflow: auto` + `overscroll-behavior: contain`.
- **Rolagem do modal.** Cabeçalho fixo (`flex: 0 0 auto`), corpo com
  `overflow-y: auto` e `scrollbar-gutter: stable` (sem salto de 15px ao
  aparecer a barra). Botão Fechar e título testados como alcançáveis
  **depois** de rolar até o fim.
- **Alvo de toque.** Os `summary` ("Ver dados do gráfico") tinham 34px de
  altura real; agora 44px.

### Um bug de JavaScript que só o teste pegou

`plotCustom()` declarava `const wrap = …` (o elemento da tabela), que
**sombreava a função `wrap()`** de quebra de linha usada dentro do callback do
tooltip. O Chart.js engole exceções de callback em silêncio: na tela o tooltip
simplesmente aparecia sem a descrição, sem erro no console. A função virou
`wrapLines()` e o teste agora exige as linhas da descrição.

---

## 2. Como ficou no celular

Testado em **360, 390, 430 e 768px**, Chromium e Firefox:

- **Uma coluna** abaixo de 960px (verificado lendo `grid-template-columns`).
- **Zero rolagem horizontal** — da página e do corpo do modal, com todas as
  tabelas abertas.
- Abaixo de 620px o modal vira **tela cheia** (`100dvw`/`100dvh`, sem raio),
  o rótulo "Fechar" some e sobra o × com 44px de alvo.
- KPIs em 2 colunas (1 coluna abaixo de 380px); filtros empilhados com
  botões de largura total.
- **A tabela do detalhamento empilha.** Quatro colunas não cabem em 331px de
  card. Em vez de rolagem lateral, a linha vira bloco: categoria + números na
  primeira linha, critério ocupando a largura toda na segunda. O cabeçalho
  sai da vista mas continua no DOM para leitor de tela, e o número ganha
  rótulo inline ("7 laudo(s)") para não ficar órfão.
- **Tooltips por toque.** `touchstart`/`touchmove` entraram na lista de
  eventos do Chart.js (sem isso o tooltip só existia com mouse). Tocar a
  fatia **ou a legenda** mostra a mesma informação; tocar a legenda **não
  esconde a fatia** (o comportamento padrão do Chart.js foi substituído).

---

## 3. Como funciona o detalhamento de personalizados

Card novo: **"Detalhamento dos laudos personalizados"**, rosca colorida.

- A classificação acontece **no servidor**
  (`app/services/custom_conclusion_groups.py`). O navegador recebe apenas
  rótulo, critério, quantidade e percentual — **nenhuma redação clínica
  trafega**, e isso é testado nos dois lados (pytest e Playwright).
- Passar o mouse (ou tocar) numa fatia mostra: nome da categoria, quantidade,
  percentual e a **descrição do que está sendo contado** — frase que vem
  pronta do servidor, nunca montada no navegador.
- Botão **"Ver conclusões"** abre a tabela com categoria, quantidade,
  percentual, **critério de agrupamento** e o total de laudos considerados,
  mais a nota do que foi deliberadamente ignorado na classificação.
- O card respeita os **mesmos filtros** de período e origem do resto do
  modal (um único bloco de filtros acima de tudo).

---

## 4. Categorias encontradas nos dados reais

Auditoria em produção (consulta somente-leitura, 07/10/2026). **37 laudos
efetivos**, todos com conclusão publicada; distribuição por código:

| Código | Laudos |
|---|---|
| NORMAL | 23 |
| **PERSONALIZADO** | **8** |
| DVO_GRAVE | 2 |
| DVO_LEVE | 2 |
| DVO_MODERADO | 1 |
| DVR_SUG_MODERADO | 1 |

Os 8 personalizados têm 8 redações distintas, mas são variações de escrita de
**duas** conclusões. Nenhum tem `observations_snapshot` preenchido — ou seja,
**não existe campo estruturado** além do código: só o texto livre.

Sete deles registram redução de CVF **e** de VEF1 (com variações de grafia:
"Redução de CVF e VEF1 isolados.", "Redução de vef1 e Cvf isolado",
"Redução isolada de VEF1 e CVF…"). Um registra apenas CVF
("Redução discreta de CVF"). Todos têm complemento BD `RBD_NEGATIVO`, que
**não participa** da classificação.

---

## 5. Quantidades e critérios de classificação

Regra **aprovada por você antes da implementação**, em 07/10/2026.

Texto normalizado: minúsculas, sem acento, espaços colapsados. Nada é
truncado nem reescrito.

**Pré-requisito para qualquer categoria:**
1. contém termo de redução como palavra inteira —
   `reducao | reducoes | reduzido | reduzida | reduzidos | reduzidas | diminuicao`;
2. **e não** contém negação — `sem reducao`, `sem reducoes`, `sem diminuicao`,
   `ausencia de reducao`, `nao houve reducao`. A negação em qualquer ponto do
   texto derruba a classificação inteira (mais conservador do que procurá-la
   só antes do termo).

| Categoria | Critério | Laudos | % |
|---|---|---|---|
| Redução de CVF e VEF1 | termo de redução + `cvf` + `vef1` (`vef1`/`vef 1`/`vef-1`) | **7** | 87,5% |
| Redução isolada de CVF | termo de redução + `cvf`, sem `vef1` | **1** | 12,5% |
| Redução isolada de VEF1 | termo de redução + `vef1`, sem `cvf` | 0 | 0% |
| Não classificável com segurança | nenhuma correspondência explícita, ou negação | 0 | 0% |
| **Total** | | **8** | 100% |

**O que a regra não faz, por decisão explícita:**

- **Não nomeia distúrbio nem padrão.** "Distúrbio obstrutivo" e "padrão
  sugestivo de restrição" têm código próprio no catálogo fechado; deduzi-los
  de texto livre seria produzir diagnóstico novo. Um teste varre os rótulos
  das categorias e falha se qualquer termo clínico desse tipo aparecer.
- **Não transforma sugestão em diagnóstico.** "Sugiro complementar com
  volumes pulmonares" é conduta, e é ignorado.
- **Não confunde resposta ao broncodilatador com tipo de achado.** O
  complemento pós-BD tem código estruturado e gráfico próprios.
- **Não conta achados, conta laudos.** Um texto que cita CVF e VEF1 cai em
  **uma** categoria, nunca em duas. A soma reconcilia com a fatia
  "Personalizado" do gráfico de resultados — testado.
- Casos ambíguos ficam em "Não classificável com segurança". O balde nunca é
  esvaziado por palpite.

### Preservação de contagens (item 7 do briefing)

A regra de laudo efetivo **não foi alterada**. O serviço de estatísticas já
conta uma linha por cadeia de documento (documento vigente, sem sucessor
corretivo, paciente não arquivado) — 1 exame = no máximo 1 laudo, a mesma
garantia da M26.13. Um teste cobre o ciclo completo: libera personalizado →
abre corretiva → libera de novo, e verifica que o total continua 1 e que a
categoria migra em vez de somar. Competências históricas não foram tocadas.

---

## 6. Testes

**Backend — `tests/test_m26_20_personalizados_detalhados.py` (novo):**

- as **8 redações reais** caem na categoria certa (parametrizado);
- a distribuição bate com a auditoria (7 / 1 / 0 / 0);
- 15 casos ambíguos vão para "Não classificável": sem termo de redução, com
  negação, com valores numéricos, texto de conduta, exame insatisfatório;
- texto livre nunca produz categoria de distúrbio ou padrão;
- variações de grafia e acento não criam categoria nova;
- `cvf`/`vef1` só contam como termo inteiro (`PCVFX`, `VEF12` não contam);
- payload reconcilia, não divide por zero e **não carrega redação alguma**;
- endpoint: detalhe presente, total igual à fatia "Personalizado", nenhum
  fragmento do texto da médica na resposta (marcador sintético);
- corretiva não duplica; conclusão de catálogo não entra no detalhe;
- filtro de período e recorte da médica aplicados ao detalhe;
- RBAC preservado (403 para leitura/operacional/gestor, 401 sem sessão).

**Frontend — `scripts/test-m26-20-graficos-laudos.js` (novo):**
Chromium + Firefox × **1920, 1440, 1024, 768, 430, 390, 360** = 14 execuções.

- 7 gráficos, 4 KPIs, rosca presente;
- **zero overflow horizontal** da página e do corpo — com todas as tabelas
  abertas;
- nenhum filho fora do retângulo do card; nenhuma tabela mais larga que o card;
- cards da mesma faixa com altura idêntica;
- 2 colunas acima de 960px, 1 abaixo;
- alvos de toque ≥ 40px; Fechar e título alcançáveis após rolar ao fim;
- tabela "Ver conclusões": 4 categorias, percentuais `87,5%`/`12,5%`, critério
  em cada linha, rodapé com o total, soma reconciliando com 8;
- **nenhuma redação clínica nem rótulo de distúrbio** no DOM da tabela;
- tooltip por mouse e **por toque**: título, `7 laudo(s) · 87,5%` e descrição
  quebrada em linhas;
- tocar a legenda mostra a mesma informação e **não** esconde a fatia;
- barras de série única em **uma** cor; faixa etária na rampa ordinal com
  cinza reservado para "Não informado";
- rótulo de linha não herda caixa alta; "Laudos" em uma linha;
- no celular, linha empilhada sem borda de célula sobrando;
- recorte vazio sem canvas órfão; Escape devolve o foco; zero `pageerror`.

**Regressão — `scripts/test-report-statistics-ui.js`** (o harness que já
existia) atualizado para 7 gráficos: 8 execuções OK. Ele continua cobrindo
sessão/logout, preservação do formulário clínico e recuperação de erro.

**Paleta:** validada por script (`validate_palette.js`), não a olho —
faixa de luminosidade, piso de croma, separação sob daltonismo
(protan/deutan/tritan), piso de visão normal e contraste contra o fundo
branco do card. A paleta anterior **falhava** em luminosidade e croma.

---

### Falhas pré-existentes do quality gate

O gate acusa 2 falhas, **as mesmas num worktree limpo do `673b0e6`** — não
foram introduzidas aqui:

- `test-m21-auth-crm-nav`: "scripts de Marketing usam cache-buster da
  reconciliação";
- `test-m25-26-fluxo-espirometria`: "a correção só oferece os campos realmente
  pendentes" e "após salvar, as pendências são RELIDAS do servidor".

Suíte backend completa: **3068 passed, 47 skipped, 0 falhas** (16min25s).

---

## 7. HEAD oficial

`7993bea` em `origin/painel-soprolife-v01` — integração **fast-forward**
(`673b0e6..7993bea`), sem merge commit.
Branch de trabalho: `origin/claude-m26-20-graficos-laudos`, mesmo commit.

## 8. HEAD produção

`7993bea` em `/opt/soprolife/soprolife-site` (`git merge --ff-only`).
`soprolife-m15-api` reiniciado e `active`.
`/opt/soprolife/secrets/m15.env` **intacto** — `52ea81cbba2efda6` antes e
depois (a armadilha da M26.4, em que o deploy apagava `M15_PORTAL_*`, foi
conferida explicitamente).

## 9. Health

```
GET http://127.0.0.1:8015/api/v1/health → HTTP 200
{"status":"ok","versao":"0.1.0","ambiente":"prod","banco":"ok",
 "agora_local":"2026-10-07T02:16:53-03:00"}
```

- `GET /api/v1/laudos/estatisticas` sem token → **401** (RBAC preservado).
- `css/report-statistics.css?v=2026100701` → 200.
- `js/report-statistics.js?v=2026100701` → 401, que é o **portão esperado**
  dos `.js` do painel (conhecido desde a M26.10), não uma falha do deploy.

**Smoke somente-leitura contra os dados reais, já em produção:**

```
laudos vigentes: 37
fatia "Personalizado" do gráfico de resultados: 8
detalhe.total: 8
  Redução de CVF e VEF1 .......... 7  (87,5%)
  Redução isolada de CVF ......... 1  (12,5%)
  Redução isolada de VEF1 ........ 0  (0%)
  Não classificável com segurança  0  (0%)
soma das categorias: 8 — reconcilia com a fatia e com o total: True
texto clínico na resposta: False
```

Nenhuma escrita: o smoke e a auditoria usaram apenas leitura.

## 10. Caminho do relatório e screenshots

- **Relatório (canônico, versionado no Git):**
  `docs/archive/reports/RELATORIO_M26_20_GRAFICOS_LAUDOS_20261007.md`
  Ler sem checkout:
  `git -C ~/soprolife-site show origin/painel-soprolife-v01:docs/archive/reports/RELATORIO_M26_20_GRAFICOS_LAUDOS_20261007.md`
- **Screenshots sintéticos** (4 por combinação: topo, tabelas abertas,
  tooltip, fim da rolagem — 2 engines × 7 larguras = 56 imagens):
  `~/SoproLife_Programacao/RELATORIOS/screenshots-m26-20-20261007/` (22 PNG,
  4,0 MB — as 7 larguras em topo, tabelas abertas e tooltip)
  Regeneráveis a qualquer momento, sem dado real:
  `cd painel-soprolife && STATISTICS_SHOTS=<dir> node scripts/test-m26-20-graficos-laudos.js`
