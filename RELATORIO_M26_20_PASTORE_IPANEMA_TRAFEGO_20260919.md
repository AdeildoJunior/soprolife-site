# M26.20 — Pastore Ipanema: tráfego gerado pela SoproLife

19/09/2026. Branch `painel-soprolife-v01`, base `2aea7ea`.

Entregue: commit `c885412`, push para `origin/painel-soprolife-v01` concluído.
**`main` não foi tocada.** Deploy na VPS **não executado** (motivo na seção
"Deploy").

## Pedido

Bloco na tela **Marketing & SEO** do Command Center medindo quanta demanda a
SoproLife encaminha para a unidade **Pastore Ipanema**, a partir da landing
`https://soprolife.com.br/espirometria-ipanema/`, com dado real do GA4.

## O que foi inspecionado antes de editar

1. **Conector de Marketing**: `scripts/read-marketing-seo-adc.py` — já consulta
   Search Console e GA4 com conta de serviço somente-leitura, grava o snapshot
   `marketing-seo.local.json` (privado em `~/.config/...`, público gitignored) e
   passa por `pii_guard` + contrato de frescor antes de gravar.
2. **Período**: `canonical_search_console_window()` — janela canônica de **28
   datas encerrada ontem**, timezone `America/Sao_Paulo`. Nenhuma data fixa.
3. **Renderização**: `js/app.js` → `renderMarketingSection()` chama os
   renderers da tela; cards usam `.mkt-kpi-card` dentro de `.mkt-kpi-strip`.
4. **Consulta genérica reaproveitável**: existe `ga4_query(...)` dentro de
   `_fetch_ga4`, e os blocos `ga4.topPages` (por `pagePath`) e `ga4.events`
   (por `eventName`). **Foram reaproveitados como padrão, mas não como fonte**
   — ver decisão abaixo.
5. **Instrumentação real do site**: confirmada em
   `origin/main:espirometria-ipanema/index.html` — os três eventos existem e
   são disparados por `data-sl-wa`, `data-sl-rota` e `data-sl-pastore`.

## Decisão: consultas próprias, não garimpo em topPages/events

`ga4.topPages` e `ga4.events` são truncados por `topLimit` (20 por padrão). Um
evento da parceria fora do top 20 apareceria **ausente**, e ausência seria lida
como zero — exatamente o erro que o pedido proíbe. O bloco faz duas consultas
filtradas na origem, pelo **mesmo** `ga4_query`, mesmo cliente, mesma
credencial e mesmo período:

| Consulta | Dimensão | Filtro | Métricas |
|---|---|---|---|
| Página | `pagePath` | `BEGINS_WITH /espirometria-ipanema` | `screenPageViews`, `activeUsers`, `sessions` |
| Eventos | `eventName` | `IN_LIST` dos 3 eventos | `eventCount`, `totalUsers` |

Para isso, `ga4_query` ganhou um parâmetro opcional `dimension_filter` — é
extensão do helper existente, não um segundo caminho de leitura.

## 1. Arquivos alterados

| Arquivo | O quê |
|---|---|
| `painel-soprolife/scripts/read-marketing-seo-adc.py` | constantes da parceria, `montar_bloco_pastore_ipanema` (pura), `_fetch_ga4_pastore_ipanema`, `dimension_filter` em `ga4_query`, `ga4.pastoreIpanema` no snapshot |
| `painel-soprolife/index.html` | painel `#mktIpanemaPanel` na seção Marketing; cache-buster de `app.js` e `style.css` → `2026091901` |
| `painel-soprolife/js/app.js` | `renderMktIpanema()`; chamada no ramo real de `renderMarketingSection()`; ocultação no ramo demonstrativo |
| `painel-soprolife/css/style.css` | `.mkt-ipanema-panel`, `.mkt-ipanema-note` |
| `painel-soprolife/scripts/test-m26-20-ipanema-pastore.py` | **novo** — agregação, filtros, guardas |
| `painel-soprolife/scripts/test-m26-20-ipanema-render.js` | **novo** — renderer real contra DOM mínimo |
| `painel-soprolife/scripts/quality-gate-safe.sh` | registra os dois testes |
| `painel-soprolife/docs/m26-20-pastore-ipanema-demanda.md` | **novo** — contrato do bloco |

Nenhum arquivo `.backup` foi criado; o histórico é o Git.

## 2. Arquitetura / fonte de dados

Uma só fonte: **GA4**, pelo conector que a tela já usava. O bloco é mais um
campo do snapshot, sob `ga4.pastoreIpanema` — sem segunda arquitetura, sem
segundo mecanismo de cache/atualização. Vale o mesmo ciclo
(`update-local-data.sh` / `soprolife-update-data.timer`) e o mesmo botão
"Atualizar dados" da tela.

`events` é **lista** `{event, count, users}`, não mapa por nome: uma chave
`click_whatsapp_ipanema` casa com a lista de chaves de contato proibidas do
`pii_guard` e abortaria a gravação do snapshot inteiro. Isso foi descoberto por
teste, não em produção.

## 3. Valores GA4 encontrados para Ipanema

**Nenhum ainda — e nenhum foi inventado.**

Esta estação não tem a configuração de Marketing
(`painel-soprolife/data-private/marketing-seo-config.local.json` não existe) e
o snapshot local está `configured: false`, `NOT_CONFIGURED` para GA4 e Search
Console. A credencial real de leitura vive só na VPS
(`/opt/soprolife/secrets/marketing-readonly.json`).

Os números reais aparecem no painel após o deploy + o primeiro ciclo do
conector na VPS. Até lá, o bloco fica **oculto** (snapshot sem
`ga4.pastoreIpanema`) — não mostra zeros nem valores de demonstração.

## 4. Fórmula da conversão

```
conversão = click_agendar_pastore (eventCount) ÷ page_view da página Ipanema (screenPageViews) × 100
```

Divisão homogênea: `screenPageViews` é a contagem do evento `page_view`, então
os dois lados são contagem de evento. Fórmula, numerador, denominador e o nome
da métrica de cada lado ficam gravados em `ga4.pastoreIpanema.conversion` — é
auditável sem ler código.

`pageviews = 0` → `rate: null` → painel mostra **N/D**, não `0%`: divisão por
zero é indefinida.

### Alternativas documentadas e NÃO aplicadas

Conforme pedido, ficam registradas antes de qualquer aplicação
(detalhe em `docs/m26-20-pastore-ipanema-demanda.md`):

1. **Por sessão** — `eventCount ÷ sessions`. Numerador continua sendo evento e
   uma sessão pode gerar dois cliques: a taxa pode passar de 100%.
2. **Por usuário** — `totalUsers` do evento ÷ `activeUsers` da página. É a
   métrica mais honesta como "taxa de pessoas", e o snapshot **já traz os dois
   números**. Mas `activeUsers` com dimensão `pagePath` é deduplicado por
   linha, não entre linhas; para virar métrica publicável seria preciso uma
   consulta sem a dimensão `pagePath`. Não foi aplicada.

Nada de "usuários" foi misturado com "event count": cada card diz a métrica de
origem (`GA4 · page_view`, `GA4 · eventCount`, `GA4 · activeUsers`,
`GA4 · calculado`).

## 5. Honestidade dos rótulos

- **"Agendamentos → Pastore"** = `click_agendar_pastore`. Card, tooltip e nota
  dizem que são **cliques enviados ao sistema da Pastore — encaminhamento e
  intenção, não exames concluídos nem pacientes convertidos**.
- **"Interações de intenção"** = soma dos 3 eventos em `eventCount`, declarada
  como **interações, não pessoas únicas**, no card, na tooltip e na nota.
- **UTM** (`utm_source=soprolife`, `utm_medium=referral`,
  `utm_campaign=espirometria_ipanema`) aparece na nota como documentação do
  encaminhamento, com o aviso de que **essas sessões são medidas no GA4 da
  Pastore, não no nosso**. Nenhuma métrica do bloco vem da UTM.

## 6. Ausência de dado

| Situação | Snapshot | Painel |
|---|---|---|
| Consulta OK, evento sem ocorrência | `count: 0` | `0` |
| Consulta falhou | `null` | `N/D` |
| `pageviews = 0` na conversão | `rate: null` | `N/D` |
| Snapshot sem o bloco / modo demonstrativo | — | painel oculto |

## 7. Testes executados

Quality gate completo (`bash painel-soprolife/scripts/quality-gate-safe.sh`),
66s, com os dois testes novos registrados:

- `test-m26-20-ipanema-pastore (bloco Pastore Ipanema)` — **OK** (47 casos)
- `test-m26-20-ipanema-render (painel JS)` — **OK** (33 casos)
- `node --check js/app.js`, `py_compile read-marketing-seo-adc` — **OK**
- `check-access.sh` (auditoria de segurança completa) — **OK**
- `git diff --check` — **OK**

Validação visual extra: o bloco foi renderizado em Chrome headless com um
harness descartável no scratchpad (fora do repositório), sem erro de
JavaScript. Layout idêntico ao resto da tela.

### 3 falhas do gate que **já existiam** antes desta etapa

Confirmado por `git stash`: saída **byte a byte idêntica** antes e depois da
mudança. Nenhuma foi causada nem agravada aqui, e nenhuma foi "corrigida de
passagem" (fora do escopo pedido):

| Check | Falha | Natureza |
|---|---|---|
| `test-m21-auth-crm-nav (M21)` | "scripts de Marketing usam cache-buster da reconciliação" | O teste fixa `app.js?v=2026080101`; o `index.html` já estava em `2026090101` desde antes. Asserção desatualizada por milestone anterior. |
| `test-m25-26-fluxo-espirometria (M25.26)` | 2 casos sobre correção de pendências | Não toca Marketing. |
| `test-marketing credencial durável (M21)` | "chave inválida vira credential_pending" → `DEPENDENCY_MISSING` | Esperado nesta estação: as bibliotecas do Google não estão instaladas aqui (o venv dedicado é criado pelo deploy na VPS). |

> Observação: como `app.js` mudou, o cache-buster **precisava** subir
> (`2026091901`), senão os sócios veriam o painel antigo depois do deploy. Isso
> mantém a asserção do M21 desalinhada — ela já estava, e alinhá-la exigiria
> mexer num teste de outra milestone.

## 8. Segredos

Nenhum. O diff foi lido inteiro: só código, marcação, estilo, testes e
documentação. Nada de `data-private/`, `*.local.json`, token, chave ou dado de
paciente. `check-access.sh` passou.

## 9. Git

- Commit: **`c885412`** — `feat(m26.20): bloco Pastore Ipanema em Marketing & SEO`
- Push: **`2aea7ea..c885412 painel-soprolife-v01 -> painel-soprolife-v01`**, OK
- **`main` intocada** — nenhum merge, nenhum commit.

## 10. Deploy

**Não executado.** Estado verificado (só leitura):

- VPS `soprolife-painel-01` (Tailscale) **online**, ping OK.
- Painel **no ar**: `https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/`
  responde **200** com a tela de login (5.596 bytes = `login.html`).
- Assets do painel respondem **401** sem sessão — o gate de autenticação está
  ativo, como esperado.

Por que não foi feito daqui: esta estação **não tem chave SSH** (`~/.ssh`
contém só `known_hosts`), e o mecanismo do projeto
(`nucleo-m15/scripts/deploy-producao-vps.sh`) é, por desenho, executado **no
terminal da própria VPS**, com operador presente, secrets e variáveis de
go-live. As skills `soprolife-vps-safe` / `soprolife-vps-deploy-safe` também
exigem aprovação explícita e separada para deploy.

Sequência para quando a janela de deploy abrir, no terminal da VPS:

```bash
cd /opt/soprolife/soprolife-site
git fetch origin painel-soprolife-v01
git log --oneline -1 origin/painel-soprolife-v01     # deve mostrar c885412
# ... deploy pelo mecanismo do projeto, com as variáveis de go-live ...
sudo bash painel-soprolife/nucleo-m15/scripts/deploy-producao-vps.sh c885412 painel-soprolife-v01 <TAILSCALE_IP>
```

Depois do deploy, o bloco só mostra número no **primeiro ciclo do conector de
Marketing** (`soprolife-update-data.timer`) ou ao clicar **"Atualizar dados"**
na própria tela. Antes disso ele fica oculto — por desenho.

## 11. Onde conferir visualmente

`https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/`
→ login → menu lateral **Marketing & SEO** → rolar até o painel
**"Pastore Ipanema — tráfego gerado pela SoproLife"**, logo abaixo de
"Páginas no Google" / "Funil do site" e acima de "Prioridades de busca local".

Se o painel não aparecer: o snapshot ainda não tem o bloco. Clicar em
"Atualizar dados" no cabeçalho da tela e aguardar o ciclo.
