# M3 — Alertas Operacionais / Saúde Operacional

## Objetivo

Dar ao operador (não técnico) uma resposta imediata para "a operação do
painel está saudável?": estado do pipeline de dados (timer pós-I1,
check de segurança, Sheets, Search Console, GA4, arquivos locais,
auditoria, painel no ar) + lista de alertas com mensagem clara e
**próximo passo** — e um banner no Painel Geral quando houver alerta
CRÍTICO.

## Arquivos criados/alterados (v1 — mock local)

| Arquivo | Mudança |
|---|---|
| `data/saude-operacional.json` | **novo** — demo commitável, fictício, `source.dadosReais=false` |
| `index.html` | painel `#saudePanel` na seção Automações + `#saudeBannerCritico` no overview |
| `js/app.js` | estado `saudeOperacional`, carregamento em cascata, `renderSaudeOperacional()` |
| `css/style.css` | classes `.saude-*` (níveis, grid, alertas, banner) |
| `scripts/check-access.sh` | `validate_saude_operacional()` (allowlist de campos + flags + anti-PII) |
| `docs/m3-alertas-operacionais.md` | este documento |

## Como funciona

1. O painel tenta `data/saude-operacional-summary.local.json` (REAL,
   gerado na VPS — ainda não existe) com as guardas
   `safeToDisplay=true` + `containsPersonalData=false`.
2. Sem ele, usa o demo commitável `data/saude-operacional.json`
   (rotulado "Dados demonstrativos" na tela).
3. Sem nenhum: o painel fica **oculto** — fallback elegante, nada quebra.

Níveis: `ok` · `atencao` · `critico` · `desconhecido` (status fora da
lista vira Desconhecido na tela e ERRO no check-access).

Banner no Painel Geral aparece SOMENTE com alerta `critico` — discrição
por padrão.

## Como testar localmente

```bash
python3 -m http.server 8765          # na raiz do repo
# abrir http://127.0.0.1:8765/painel-soprolife/ → seção Automações
node --check painel-soprolife/js/app.js
python3 -m json.tool painel-soprolife/data/saude-operacional.json
bash painel-soprolife/scripts/check-access.sh
```

Para ver o banner crítico: editar temporariamente um alerta do demo para
`"nivel": "critico"` e recarregar o Painel Geral (reverter depois).
Para testar o fallback: renomear temporariamente o JSON demo.

## Limites da v1

- **Os dados são mock**: o retrato de saúde é fictício e estático.
- Nenhum alerta é calculado — a lista vem pronta do JSON.
- O banner crítico não tem link clicável (aponta em texto para a seção).

## O que falta para ligar com dados reais da VPS (v2)

1. **Gerador** `scripts/generate-saude-operacional.py` rodando na VPS ao
   fim do `update-local-data.sh` (etapa 14), produzindo
   `data/saude-operacional-summary.local.json` com `dadosReais=true` a
   partir de fontes já disponíveis lá: exit code do próprio update,
   resultado do check-access, timestamps `generatedAt` dos summaries,
   flags do marketing-seo (`sources.searchConsole/ga4`), stats da
   auditoria — **sem systemctl/journal** (o script roda dentro do
   próprio timer; "timer ok" = o arquivo acabou de ser gerado).
2. Passar o payload pelo `pii_guard` antes de gravar (padrão M2).
3. Regras de alerta calculadas (ex.: `ultima_atualizacao` > 30 min →
   crítico "painel com dado velho"; check-access ≠ 0 → crítico).
4. Deploy normal via git + 1 ciclo do timer; conferir permissão 644.

## Segurança

Sem segredos, sem endpoint privado, sem PII: o schema só admite os
campos da allowlist (`id/label/status/detalhe/tip` e
`id/nivel/titulo/mensagem/proximo_passo`), validados pelo
`check-access.sh` no demo e no futuro arquivo real (que já nasce
gitignored pelo padrão `**/*.local.json`).
