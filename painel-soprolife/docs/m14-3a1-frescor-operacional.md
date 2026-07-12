# M14.3A.1 — Frescor operacional (Marketing/SEO + Manual das Abas)

Camada que faz o painel distinguir com honestidade: **atualizado**, **vencido**,
**indisponível**, **erro de sincronização**, **reautenticação necessária** e
**publicação de Apps Script pendente**.

Contrato: `core/contracts/freshness-contract.json` (estados + exit codes).
Bibliotecas: `scripts/freshness_contract.py` (Python) e
`js/marketing-freshness.js` (painel) — mesmas regras nos dois lados.

## Conceitos que NUNCA se confundem

| Conceito | O que atualiza | O que NÃO atualiza |
|---|---|---|
| Deploy de código (git pull na VPS) | HTML/JS/CSS/scripts | dados `*.local.json`, Apps Script, Google Sheets |
| Regeneração de dados (`update-local-data.sh` / `refresh-marketing`) | snapshots `*.local.json` | código, Apps Script, Sheets |
| Publicação de Apps Script (SEMPRE humana, no editor) | código no Google | a aba real (só a execução atualiza) |
| Execução no Google Sheets (humana) | a aba real (ex.: Manual das Abas) | — |
| Cache do navegador | resolvido por `_cb` de sessão (sem token) | — |

**"Deploy concluído" = as quatro primeiras linhas verificadas**, não só a primeira.

## Estados de frescor

- `fresh` — dentro do limite (`staleAfterHours`, padrão 26h).
- `stale` — snapshot válido porém vencido; painel mostra a última versão
  válida COM selo "Desatualizado" (nunca aparência de atual).
- `authentication_required` — ADC expirado; painel mostra "Reautenticação
  necessária"; **nunca** vira gráfico zerado.
- `unavailable` — fonte não configurada/dependência ausente.
- `error` — falha não classificada; snapshot anterior preservado.
- `publication_pending` — .gs local mais novo que o estado conhecido do Google.
- `unknown` — nunca sincronizado / sem informação.

GA4 e Search Console têm estados independentes; falha em um preserva o outro.

## Comandos

```bash
# Visão geral (offline, sempre seguro)
bash painel-soprolife/scripts/soprolife-operational-refresh.sh status

# Validação com exit codes (0 fresh · 10 stale · 11 auth · 12 schema · 13 indisp.)
bash painel-soprolife/scripts/soprolife-operational-refresh.sh check

# Sincronizar Marketing/SEO (com rede; escrita atômica; preserva snapshot)
bash painel-soprolife/scripts/soprolife-operational-refresh.sh refresh-marketing

# Manual das Abas: regenerar .gs + instruções de publicação (nunca publica)
bash painel-soprolife/scripts/soprolife-operational-refresh.sh prepare-apps-script
```

## Renovar ADC manualmente (quando aparecer "Reautenticação necessária")

```bash
gcloud auth application-default login --no-launch-browser \
  --scopes="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets.readonly,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/webmasters.readonly,https://www.googleapis.com/auth/analytics.readonly"
```

Depois: `refresh-marketing` e conferir o selo "Atualizado" no painel.

## Manual das Abas — publicar de verdade

1. `prepare-apps-script` (regenera o .gs e atualiza `manual-abas-status.json`);
2. colar `apps-script/manual-das-abas.gs` no editor do Apps Script;
3. executar `atualizarManualDasAbasSoproLife()`;
4. conferir a linha "Atualizado em" na aba real (data de hoje);
5. registrar: `python3 painel-soprolife/scripts/generate-manual-abas-gs.py --mark-published`.

Sem o passo 5, o estado continua `publication_pending` — de propósito.

## Timer na VPS (preparado, NÃO instalado)

```bash
# dry-run primeiro, sempre
bash painel-soprolife/scripts/install-operational-refresh.sh
# instalar (não habilita o timer)
sudo bash painel-soprolife/scripts/install-operational-refresh.sh --apply
# habilitar (decisão humana explícita)
sudo systemctl enable --now soprolife-operational-refresh.timer
# verificar
journalctl -u soprolife-operational-refresh.service -n 50 --no-pager
# rollback
sudo bash painel-soprolife/scripts/uninstall-operational-refresh.sh --apply
```

O modo do timer vem de `/etc/soprolife/operational-refresh.env`
(padrão `check`, offline). `refresh-marketing` automático só quando o humano
editar esse arquivo. Nenhum modo publica Apps Script.

## Checklist — Deploy operacional completo

Só declare "deploy concluído" com TODAS as caixas marcadas:

- [ ] Código: `git pull` na VPS no commit esperado (`git log -1`).
- [ ] Dados: `soprolife-operational-refresh.sh status` sem `stale`/`auth` inesperado.
- [ ] Marketing/SEO: selo "Atualizado" no painel, com data de sincronização de hoje.
- [ ] Apps Script (se mudou): colado no editor E "Nova versão" implantada.
- [ ] Manual das Abas (se manifesto mudou): executado no Sheets E `--mark-published`.
- [ ] Aba real confere: "Atualizado em" com a data da execução.
- [ ] Navegador: recarregar o painel e conferir os selos (cache é por sessão).
- [ ] Rollback conhecido: commit anterior anotado + snapshot anterior preservado.
