# M26.30 — Ordenação dos "Termos de busca" (Marketing & SEO)

**Data:** 11/10/2026
**Branch de trabalho:** `claude-m26-30-ordenacao-termos-busca`
**Base:** `origin/painel-soprolife-v01` (`4ca2fe3`)
**Commit:** `0961fdc` — empurrado fast-forward para `origin/painel-soprolife-v01`
**Worktree:** `/home/fedorasurf/soprolife-worktrees/m26-30-ordenacao-termos-busca`
**Escopo tocado:** apenas o painel "Termos de busca" da seção Marketing & SEO
do Command Center. Nada de NFS-e, laudos, portal de resultados, site público,
migrations, backend ou dados reais.

---

## 1. O que mudou

A lista "Termos de busca" exibia as consultas **na ordem em que o Search
Console as devolve** — cliques decrescentes. Para análise SEO a medida de
visibilidade é a **impressão**: é ela que revela o termo muito exibido e pouco
clicado, que é exatamente o que se quer caçar.

O painel passa a abrir em **impressões ↓**, com desempate por **cliques** e
depois por **CTR**, conforme pedido.

No efeito prático, com o formato real do snapshot (107 consultas na conta
SoproLife, segundo o M25.28): o termo de maior exposição e CTR baixo sobe para
o topo em vez de ficar em 6º lugar atrás de termos com poucos cliques a mais.

## 2. O seletor

No cabeçalho do painel, uma linha discreta:

```
Ordenar por:  [ Impressões ↓ ] [ Cliques ] [ CTR ] [ Posição ]
```

- **Padrão ao abrir:** Impressões ↓.
- **Ativo marcado em teal** (preenchimento + borda + peso de fonte), com a
  seta de sentido no próprio chip e `aria-pressed` correto.
- **Ascendente/descendente sem poluir a UI:** clicar no critério **já ativo**
  inverte o sentido. Não há um quinto controle no desktop.
- **Posição abre em ascendente** — 1 é o primeiro lugar no Google, então
  "melhor primeiro" ali é o menor número. Trocar de critério **não herda** o
  sentido invertido: cada critério recomeça no padrão dele.
- **Tela estreita (≤560px):** os quatro chips viram um `<select>` compacto mais
  um botão redondo de seta (28×28, alvo de toque decente). O `select` não sabe
  expressar sentido — por isso o botão ao lado existe só nesse modo.

## 3. O que explicitamente NÃO mudou

| Requisito | Como foi garantido |
|---|---|
| Não mudar os dados vindos do Search Console | A lista é **copiada** antes de ordenar (`[...queries].sort(...)`). Teste prova que `topQueries` continua sendo o mesmo array, na ordem original, e que os totais ficam intactos. |
| Apenas ordenar na apresentação | Nenhuma chamada de rede no trecho novo (teste faz a guarda estática contra `fetch`/`XMLHttpRequest`). |
| Manter filtros/período atuais | O trecho novo não toca em `mktPeriod`, `mktRefresh`, `periodStart` nem `lookbackDays` (teste faz a guarda estática). |
| Não alterar outras áreas do Marketing & SEO | Mudança isolada em `renderMktSeoList()` + CSS novo com classes próprias (`.mkt-sort*`) e uma regra escopada em `#seoList`. KPIs, tendência, páginas, funil, Pastore Ipanema e alertas intocados. |

## 4. Duas decisões que o pedido não previa

**a) O chip de posição.** Ordenar por um número que a linha não mostra deixaria
a lista inexplicável. Então, **e só quando "Posição" é o critério ativo**, a
linha ganha um 4º chip `pos. 1,4`. Na ordenação padrão a linha continua
exatamente como era — três chips.

**b) A largura mínima do termo.** Com o 4º chip, na coluna estreita do grid
`2fr/1fr` do Marketing, os chips comiam o termo inteiro: sobrava `e…` ou nada
(`.mkt-mini-chips` tem `flex-shrink: 0` e `.mkt-mini-term` tem `flex: 1`).
Correção escopada em `#seoList.is-sorted-by-position`: 12ch de largura mínima
para o termo e os chips passam a ceder espaço e quebrar para a 2ª linha. As
outras mini-listas do Marketing (Páginas mais acessadas, Páginas no Google,
Funil) ficam exatamente como estão.

**c) A barra acompanha a métrica ordenada.** Presa às impressões, uma lista
ordenada por cliques pareceria fora de ordem. Em Posição a barra volta às
impressões — posição não tem comprimento que signifique alguma coisa.

**d) "Ver mais" guardado no state.** Antes o estado de expansão vivia num
`let` dentro do render; trocar a ordenação recolheria a lista que o operador
acabou de abrir.

**e) Sem consultas reais o seletor some.** O modelo demonstrativo
(`state.marketing.seo`) não tem CTR; oferecer "ordenar por CTR" ali seria
prometer uma métrica inexistente.

## 5. Arquivos

| Arquivo | Mudança |
|---|---|
| `painel-soprolife/index.html` | Marcação do seletor no `.panel-header`; cache-buster de `style.css` e `app.js` → `v=2026101101` |
| `painel-soprolife/js/app.js` | Bloco M26.30 (`MKT_SEO_SORTS`, `mktSeoOrdenar`, controles) + `renderMktSeoList()` reescrito; `state.mktSeoSort` e `state.mktSeoExpandido` |
| `painel-soprolife/css/style.css` | Bloco `.mkt-sort*` + media query ≤560px + regra escopada `#seoList.is-sorted-by-position` |
| `painel-soprolife/scripts/test-m26-30-ordenacao-termos-busca.js` | Novo (486 linhas) |
| `painel-soprolife/scripts/quality-gate-safe.sh` | Registra o teste novo (sintaxe + execução) |

**Cache-buster:** obrigatório por contrato (seção 4 do contexto mestre) — toda
mudança de JS/CSS exige bump no `index.html`, senão o deploy não chega ao
navegador. Feito.

## 6. Testes

### 6.1 Teste do painel (offline, no quality gate)

`painel-soprolife/scripts/test-m26-30-ordenacao-termos-busca.js` — **66 casos,
todos passando**. Executa o renderizador **real**, recortado de `js/app.js`
(se o recorte falhar o teste falha; nunca valida uma cópia desatualizada),
contra um DOM mínimo com listeners de verdade.

Fixture no formato que `read-marketing-seo-adc.py` grava
(`{query, impressions, clicks, ctr, avgPosition}`, ctr em 4 casas,
avgPosition em 1), entregue na ordem do Search Console (cliques ↓), com a
cauda longa de 1 impressão / 0 clique que o relatório do M25.28 registrou na
conta real.

Cobre: padrão de abertura; desempate impressões → cliques → CTR → posição →
alfabético; snapshot não alterado; os quatro critérios monotônicos;
asc/desc por clique no ativo e pelo botão de seta; troca de critério não
herdando sentido; o `select` compacto (inclusive valor fora do contrato);
"Ver mais" sobrevivendo à reordenação; métrica ausente indo para o fim nos
dois sentidos e saindo como `N/D`; o modelo demonstrativo escondendo o
seletor; e as guardas estáticas de escopo.

### 6.2 Conferência responsiva em navegador

Chromium headless (Playwright 1.61.1), `index.html` + CSS + `app.js` reais,
**todas as rotas abortadas** (sem rede), 107 consultas no formato real.
Larguras **1920, 1440, 1280, 1101, 1024, 768, 560, 430, 390, 320**:

- sem estouro horizontal da página e sem estouro do painel em nenhuma largura;
- nenhum erro de JavaScript;
- chips e `select` nunca coexistem nem somem juntos; o botão de seta sempre
  acompanha o `select`;
- os quatro chips cabem **em uma única linha** em toda largura em que
  aparecem (em ≤1280 o rótulo "Ordenar por:" é que desce para a linha de cima);
- ordem padrão correta em todas as larguras e Posição ordenando ascendente.

Screenshots (20 PNG, 2 por largura — padrão e Posição) em
`~/SoproLife_Programacao/RELATORIOS/screenshots-m26-30-20261011/`.

### 6.3 Quality gate

`bash painel-soprolife/scripts/quality-gate-safe.sh` — **2 falhas, ambas
pré-existentes e provadas na base limpa `4ca2fe3`** (com as mudanças
guardadas em stash):

| Check | Falha | Situação |
|---|---|---|
| `test-m21-auth-crm-nav` | "scripts de Marketing usam cache-buster da reconciliação" — o teste fixa `app.js?v=2026080101`, enquanto o `index.html` já estava em `v=2026100403` antes desta missão | Pré-existente. **Não corrigido de propósito**: o conserto exige decidir a intenção da asserção (fixar a versão da reconciliação ou só exigir que exista cache-buster) e isso é outra missão. |
| `test-m25-26-fluxo-espirometria` | "a correção só oferece os campos realmente pendentes" e "após salvar, as pendências são RELIDAS do servidor" | Pré-existente, já registrado no M26.20. |

Todo o resto passou, incluindo `check-access.sh` (auditoria de segurança) e o
teste novo.

## 7. Deploy — **PENDENTE**

Commit `0961fdc` está em `origin/painel-soprolife-v01` (push fast-forward,
direto por cima de `4ca2fe3`).

**O deploy na VPS não foi executado: a VPS está inacessível desta máquina.**
`100.87.98.100` responde a ping (19ms, 0% de perda) mas **toda porta TCP está
filtrada** — SSH (22), HTTP (80) e HTTPS (443) dão timeout. É o check do
Tailscale expirado, que o contexto mestre descreve: "o check do Tailscale
expira em ~30 min — peça o clique antes".

Quando o acesso voltar, o deploy é **de front puro** (seção 11 do contexto
mestre): **não precisa de restart** da `soprolife-m15-api`, basta o pull e o
cache-buster, que já foi bumpado.

```bash
ssh root@100.87.98.100
cd /opt/soprolife/soprolife-site
git status --short                      # tem de estar limpo
git rev-parse --short HEAD              # anotar para rollback
git pull --ff-only origin painel-soprolife-v01
git rev-parse --short HEAD              # deve dar 0961fdc
```

Smoke depois do pull (o `.js` dá 401 sem sessão, então o alvo servível é o CSS
e o arquivo em disco):

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8015/api/v1/health
curl -s http://127.0.0.1:8765/css/style.css | grep -c 'mkt-sort-btn'     # > 0
grep -c 'data-mkt-sort' /opt/soprolife/soprolife-site/painel-soprolife/index.html  # 4
journalctl -u soprolife-m15-api --since '-5 min' --no-pager | tail -20
```

No navegador: Marketing & SEO → "Termos de busca" deve abrir com
**Impressões ↓** marcado e o primeiro termo sendo o de maior impressão.

**Rollback** (front puro, sem migration): `git reset --hard <HEAD anotado>` no
checkout da VPS, ou `git revert 0961fdc` na branch e novo pull.

## 8. Pendências

1. **Deploy + smoke na VPS** — bloqueado pelo acesso Tailscale (seção 7).
2. **`test-m21-auth-crm-nav`** continua reprovando pelo cache-buster fixado em
   `v=2026080101`. Vale uma missão curta para decidir se a asserção fixa a
   versão ou só exige a presença do cache-buster.
3. **Dados reais não foram vistos nesta máquina.** Os dois snapshots locais
   (`painel-soprolife/data/marketing-seo.local.json` e
   `~/.config/soprolife/painel/marketing-seo.json`) têm `topQueries` vazio —
   o ADC do Search Console não roda aqui. A conferência usou o **formato real**
   do leitor e o volume real (107 consultas), não os valores reais. A
   verificação com os números de produção acontece no smoke do navegador.

## 9. Caminho do relatório e screenshots

- Relatório versionado no Git (cópia canônica):
  `docs/archive/reports/RELATORIO_M26_30_ORDENACAO_TERMOS_DE_BUSCA_20261011.md`
- Screenshots (fora do Git):
  `~/SoproLife_Programacao/RELATORIOS/screenshots-m26-30-20261011/` (20 PNG)
