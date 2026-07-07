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

## v2 — Gerador real local (IMPLEMENTADO)

`scripts/generate-saude-operacional.py` produz
`data/saude-operacional-summary.local.json` (gitignored, `dadosReais=true`,
chmod 644) usando SOMENTE metadados dos 12 summaries seguros de `data/`:
existência, JSON válido, flags `safeToDisplay`/`containsPersonalData`,
frescor (mtime) e contagens agregadas (ex.: `stats.erros` da auditoria,
`meta.sources` do marketing). **Nunca abre data-private/, ~/.config, ADC
ou rede; nunca copia conteúdo.** Payload validado por allowlist estrita +
`pii_guard` antes de gravar.

Regras: fonte com PII flag errada ou JSON inválido → alerta CRÍTICO;
pipeline sem atualização > 24h → CRÍTICO; > 30 min → ATENÇÃO; SC/GA4
sem dados → ATENÇÃO; check-access só entra via `--check-access-exit N`
(o update passa o exit real; sem o argumento fica "desconhecido" — o
script nunca inventa resultado de segurança). "Pós-I1" é inferido de
evidência local (docs I1 + pipeline vivo), **sem systemctl**.
`status_geral` = pior nível entre ok/atenção/crítico; "desconhecido" não
rebaixa o geral (é ausência de informação, visível nos próprios cards).

### Como rodar / testar

```bash
python3 painel-soprolife/scripts/generate-saude-operacional.py            # dry-run
python3 painel-soprolife/scripts/generate-saude-operacional.py --write
python3 -m json.tool painel-soprolife/data/saude-operacional-summary.local.json
bash painel-soprolife/scripts/check-access.sh   # valida demo E real (dadosReais true/false)
```

Integração: etapa **13/14** do `update-local-data.sh` — o check-access
(12/14) tem o exit capturado e repassado ao gerador; falha do check
continua derrubando o update ao final (semântica preservada); falha do
gerador vira AVISO (painel cai no demo).

### Limitações da v2

- "Painel no ar" fica **desconhecido** (sem teste de rede por decisão);
  na VPS, a alternativa futura é o próprio proxy gravar um heartbeat.
- Frescor usa mtime local — depende do relógio da máquina que gera.
- Rodado na estação (fora do ciclo), o pipeline aparecerá "atenção/
  crítico" por idade — comportamento honesto, não bug.

### Antes do deploy (revisão)

Diff completo + este doc pelo GPT; na VPS, após o deploy, 1 ciclo do
timer deve gerar o arquivo real e o card mudar para "Fonte: Pipeline
real" — conferir também permissão 644 e o check-access remoto.

## Segurança

Sem segredos, sem endpoint privado, sem PII: o schema só admite os
campos da allowlist (`id/label/status/detalhe/tip` e
`id/nivel/titulo/mensagem/proximo_passo`), validados pelo
`check-access.sh` no demo e no futuro arquivo real (que já nasce
gitignored pelo padrão `**/*.local.json`).
