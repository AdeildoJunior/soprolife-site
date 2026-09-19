# M26.20 — Pastore Ipanema: demanda gerada pela SoproLife

Bloco da tela **Marketing & SEO** do Command Center que mede quanta demanda a
SoproLife encaminha para a unidade **Pastore Ipanema**.

Objetivo de negócio: poder dizer à Pastore, com número auditável, *"a SoproLife
gerou X visitas, Y cliques em agendamento, Z contatos via WhatsApp e W
solicitações de rota no período"*.

## Fonte de dados

Uma só: o **GA4**, pelo conector que a tela já usa
(`scripts/read-marketing-seo-adc.py`), com a mesma credencial de conta de
serviço somente-leitura, o mesmo cliente e o mesmo período. **Nenhuma
arquitetura paralela foi criada** — o bloco é mais um campo do snapshot
`marketing-seo.local.json`, sob `ga4.pastoreIpanema`.

Origem no site: `https://soprolife.com.br/espirometria-ipanema/`, já
instrumentada em `espirometria-ipanema/index.html` (branch `main`).

### Por que consultas próprias, e não `ga4.topPages` / `ga4.events`

Aquelas duas listas são truncadas por `topLimit` (20 por padrão). Um evento da
parceria fora do top 20 apareceria como **ausente**, e ausência seria lida como
zero. As consultas do bloco são filtradas na origem:

| Consulta | Dimensão | Filtro | Métricas |
|---|---|---|---|
| Página | `pagePath` | `BEGINS_WITH /espirometria-ipanema` | `screenPageViews`, `activeUsers`, `sessions` |
| Eventos | `eventName` | `IN_LIST` dos 3 eventos | `eventCount`, `totalUsers` |

`pagePath` no GA4 já vem sem query string, então o prefixo cobre
`/espirometria-ipanema` e `/espirometria-ipanema/` sem depender de barra final.

### Eventos usados

| Evento | O que mede |
|---|---|
| `click_agendar_pastore` | Clique que **encaminha** ao sistema oficial de agendamento da Pastore |
| `click_whatsapp_ipanema` | Clique no WhatsApp da SoproLife a partir da landing |
| `click_rota_pastore_ipanema` | Clique em rota/Google Maps para a unidade |

> `click_agendar_pastore` **não é** exame realizado nem paciente convertido.
> Mede intenção/encaminhamento. O painel e a nota sob os cards dizem isso
> explicitamente, para que o número nunca seja apresentado como produção
> clínica.

## Período

O mesmo da tela: a janela canônica de **28 datas encerrada ontem**
(`canonical_search_console_window`, timezone `America/Sao_Paulo`). Nenhuma data
é fixada no código do bloco; o painel lê `meta.periodStart` / `meta.periodEnd` /
`meta.lookbackDays` do snapshot e imprime o período consultado.

## Fórmula da conversão (a que está no ar)

```
conversão = click_agendar_pastore (eventCount) ÷ page_view da página Ipanema (screenPageViews) × 100
```

A fórmula, o numerador, o denominador e o nome da métrica de cada lado ficam
gravados no próprio snapshot (`ga4.pastoreIpanema.conversion`) — é auditável
sem ler código.

Divisão homogênea: `screenPageViews` é a contagem do evento `page_view`, então
os dois lados são contagem de evento. **Não se divide evento por usuário.**

### Alternativas consideradas (documentadas, NÃO aplicadas)

As duas exigiriam consultas novas e mudam o significado do número. Ficam
registradas aqui para decisão futura; enquanto não houver decisão, o painel
mostra só a fórmula acima.

1. **Por sessão** — `click_agendar_pastore` (eventCount) ÷ `sessions` da página.
   Responde "quantas visitas terminaram em encaminhamento". Problema: o
   numerador continua sendo evento e uma sessão pode gerar dois cliques, então
   a taxa pode passar de 100%.
2. **Por usuário** — `totalUsers` de `click_agendar_pastore` ÷ `activeUsers` da
   página. É a métrica mais honesta como "taxa de pessoas", e o snapshot já traz
   os dois números (`events[].users` e `page.users`). Problema: `activeUsers`
   com dimensão `pagePath` é deduplicado pelo GA4 por linha, não entre linhas —
   se a landing tiver mais de um caminho, o denominador não é somável. O bloco
   usa o **maior** valor devolvido (piso conhecido), nunca a soma; para virar
   métrica publicável, o correto seria uma consulta sem a dimensão `pagePath`,
   com o filtro aplicado.

## Ausência de dado ≠ zero

Três estados distintos, tratados separadamente:

| Situação | Snapshot | Painel |
|---|---|---|
| Consulta OK, evento sem ocorrência | `count: 0` | `0` |
| Consulta falhou (rede, permissão, quota) | `null` | `N/D` |
| `pageviews = 0` na conversão | `rate: null` | `N/D` (divisão indefinida, nunca `0%`) |

O GA4 não devolve linha para evento com zero ocorrências, então evento ausente
**numa consulta bem-sucedida** é legitimamente `0`. Só a falha da consulta
inteira vira `null`.

Se o snapshot ainda não tiver o bloco (painel implantado antes do primeiro
ciclo do conector), o painel **esconde** a seção em vez de mostrar zeros. No
modo demonstrativo da tela, a seção também fica oculta: nenhum número fictício
entra nesse bloco.

## Formato no snapshot

```jsonc
"ga4": {
  "pastoreIpanema": {
    "pagePathPrefix": "/espirometria-ipanema",
    "outboundUtm": { "source": "soprolife", "medium": "referral",
                     "campaign": "espirometria_ipanema" },
    "page": { "pageviews": 0, "users": 0, "sessions": 0 },   // ou null
    "events": [                                              // ou null
      { "event": "click_agendar_pastore",      "count": 0, "users": 0 },
      { "event": "click_whatsapp_ipanema",     "count": 0, "users": 0 },
      { "event": "click_rota_pastore_ipanema", "count": 0, "users": 0 }
    ],
    "intentInteractions": { "interactions": 0, "metric": "eventCount",
                            "note": "Soma de interações, não de pessoas únicas." },
    "conversion": { "rate": null, "numerator": 0, "denominator": 0,
                    "numeratorMetric": "eventCount",
                    "denominatorMetric": "screenPageViews",
                    "formula": "…" }                         // ou null
  }
}
```

`events` é **lista**, não mapa por nome de evento: um campo chamado
`click_whatsapp_ipanema` casaria com a lista de chaves de contato proibidas da
guarda de PII (`pii_guard`) e abortaria a gravação do snapshot inteiro. Lista
também é o formato que `ga4.events` já usava.

## UTM de saída

Os links da landing para a Pastore vão marcados com
`utm_source=soprolife`, `utm_medium=referral`,
`utm_campaign=espirometria_ipanema`. Isso fica no snapshot como documentação e
aparece na nota sob os cards — mas **essas sessões são medidas no GA4 da
Pastore, não no nosso**. Nenhuma métrica do bloco é derivada da UTM.

## Interações de intenção

`click_agendar_pastore + click_whatsapp_ipanema + click_rota_pastore_ipanema`,
em `eventCount`. É soma de **interações**, não de pessoas únicas: a mesma
pessoa pode clicar nos três e contar três vezes. O card, a tooltip e a nota
dizem isso.

## Onde está o código

| Camada | Arquivo |
|---|---|
| Leitura GA4 + agregação | `scripts/read-marketing-seo-adc.py` (`montar_bloco_pastore_ipanema`, `_fetch_ga4_pastore_ipanema`) |
| Marcação | `index.html` (`#mktIpanemaPanel`) |
| Renderização | `js/app.js` (`renderMktIpanema`) |
| Estilo | `css/style.css` (`.mkt-ipanema-panel`, `.mkt-ipanema-note`) |

Os cards reusam `.mkt-kpi-card` / `.mkt-kpi-strip`, a mesma anatomia da faixa
de KPIs da tela — sem variação de cor, porque a "régua única" do M17 desliga
`::before` para toda a família.

## Testes

```bash
python3 painel-soprolife/scripts/test-m26-20-ipanema-pastore.py   # agregação + guardas
node    painel-soprolife/scripts/test-m26-20-ipanema-render.js    # renderer real, DOM mínimo
bash    painel-soprolife/scripts/quality-gate-safe.sh             # os dois já registrados
```

Ambos são offline: sem rede, sem credencial, sem `data-private`, sem VPS.
