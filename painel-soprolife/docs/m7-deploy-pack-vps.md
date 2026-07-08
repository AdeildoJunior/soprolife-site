# M7 — Deploy Pack Seguro: M3–M6 para a VPS

> ⚠️ **NENHUM comando deste documento deve ser executado sem revisão do
> ChatGPT** e sem o usuário presente. Placeholders sempre
> (`<VPS_TAILSCALE>`). Rollback é MANUAL e deliberado — nunca automático.
> Skills que regem a janela: soprolife-vps-safe, soprolife-vps-deploy-safe.

## Objetivo do deploy

Levar à VPS o pacote M3–M6: Saúde Operacional (tela + gerador real +
testes), Central de Ações Operacionais, Próximas Ações B2B/PCMSO e o
Quality Gate — transformando o painel em cockpit operacional/comercial
com validação única pré-commit.

## Commits incluídos (3815704 → c64c35e)

| Commit | Tag | Conteúdo |
|---|---|---|
| bfa6754 | checkpoint-m3-saude-operacional-v01 | Tela Saúde Operacional + demo |
| c104473 | …-gerador-v02 | Gerador real (`generate-saude-operacional.py`) + etapa 13/14 do update |
| 44a93ac | …-testes-v03 | Suíte M3 (19 casos) + `--data-dir/--out` |
| e7494e4 | checkpoint-m4-acoes-operacionais-v01 | Central de Ações Operacionais (UI) |
| e38e127 | …-complemento-v01 | Helper puro + suíte M4 (22 casos) |
| 5d4b696 | checkpoint-m5-acoes-b2b-pcmso-v01 | Próximas Ações B2B/PCMSO + suíte (30 casos) |
| c64c35e | checkpoint-m6-quality-gate-safe-v01 | Quality Gate Seguro |

**Mudanças com efeito operacional na VPS:**
- `update-local-data.sh`: 12→**14 etapas** — check-access com exit
  capturado (12/14) alimenta o gerador de saúde (13/14); falha do check
  ainda derruba o update no final; falha do gerador é só AVISO.
- Novo arquivo gerado a cada ciclo:
  `data/saude-operacional-summary.local.json` (644, sem PII).
- Frontend: 2 arquivos JS novos + `app.js`/`index.html`/`style.css`
  maiores; painéis novos no Painel Geral e em Automações.
- `check-access.sh`: +validador de Saúde Operacional.
- **Nenhuma mudança de systemd** neste pacote: os units da I1 ficam como
  estão (sem daemon-reload; o timer usa o script novo no próximo ciclo).

## Riscos

1. **Cache do navegador (BLOQUEADOR já identificado):**
   `index.html` referencia `app.js?v=2026062202` — desatualizado para o
   app.js do M3–M5. Sem bump, os sócios verão o painel antigo/quebrado.
   **Pré-requisito de deploy: commit de 1 linha atualizando o `?v=` do
   app.js** (e conferir os `?v=` dos JS novos), aprovado pelo GPT.
2. Primeiro ciclo do timer com as 14 etapas: se o gerador de saúde
   falhar na VPS, o update NÃO quebra (AVISO) e o painel cai no demo —
   verificar o journal do 1º ciclo mesmo assim.
3. `*.local.json` não vêm pelo git: o painel pode mostrar seções novas
   vazias/demo até o 1º ciclo regenerar (esperado, ~10 min).
4. Pastore: a etapa "Parceira" já foi corrigida na planilha para
   "Parceiro ativo" via Command Center — o 1º ciclo deve refletir isso
   nos painéis B2B (Convertidas=2, sem ação "Revisar etapa").

## Pré-check LOCAL (executável agora, sem VPS)

```bash
bash painel-soprolife/scripts/m7-local-deploy-readiness.sh
# (roda o quality gate + branch + tree limpo + tags + push pendente)
```

Critério: **GO local** = gate verde, branch certa, tree limpo, HEAD
tagueado e sem commits à frente do origin.

## Pré-check REMOTO read-only  ⚠️ NÃO EXECUTAR SEM REVISÃO DO CHATGPT

```bash
ssh <TAILSCALE_USER>@<VPS_TAILSCALE> '
  git -C /opt/soprolife/soprolife-site status --short
  git -C /opt/soprolife/soprolife-site log --oneline -3
  systemctl list-timers "soprolife-*" --no-pager
  systemctl show soprolife-update-data.service -p Result -p ExecMainStatus
'
```

Critério: repo da VPS limpo e no commit anterior esperado (3815704);
timer ativo com última execução `success/0`.

## Plano de atualização  ⚠️ NÃO EXECUTAR SEM REVISÃO DO CHATGPT

```bash
# 1. (local) push já revisado/aprovado:
git push origin painel-soprolife-v01 --tags

# 2. (VPS) mostrar o que entra ANTES de aplicar:
ssh <TAILSCALE_USER>@<VPS_TAILSCALE> '
  cd /opt/soprolife/soprolife-site &&
  git fetch origin painel-soprolife-v01 &&
  git log HEAD..origin/painel-soprolife-v01 --oneline
'
# ⏸ PARADA: a lista acima deve bater EXATAMENTE com a tabela de commits.

# 3. (VPS) aplicar fast-forward only (nunca reset --hard no deploy):
ssh <TAILSCALE_USER>@<VPS_TAILSCALE> \
  'git -C /opt/soprolife/soprolife-site pull --ff-only origin painel-soprolife-v01'
```

## Validação pós-deploy (imediata)  ⚠️ NÃO EXECUTAR SEM REVISÃO

```bash
ssh <TAILSCALE_USER>@<VPS_TAILSCALE> '
  git -C /opt/soprolife/soprolife-site log --oneline -1
  bash /opt/soprolife/soprolife-site/painel-soprolife/scripts/check-access.sh | tail -8
'
curl -s -o /dev/null -w "%{http_code}\n" http://<VPS_TAILSCALE>:8765/painel-soprolife/
# navegador: hard-refresh (Ctrl+Shift+R) e conferir que os painéis novos
# aparecem (Saúde Operacional com "Dados demonstrativos" até o 1º ciclo).
```

## Validação após o 1º ciclo do timer (~10 min)

```bash
ssh <TAILSCALE_USER>@<VPS_TAILSCALE> '
  journalctl -u soprolife-update-data.service -n 60 --no-pager | tail -25
  ls -l /opt/soprolife/soprolife-site/painel-soprolife/data/saude-operacional-summary.local.json
'
```

Critérios: journal com **14/14 etapas** e `status=0/SUCCESS`; summary de
saúde criado com dono soprolife e 644; no navegador, card mostra
**"Fonte: Pipeline real"**, check de segurança OK, e os painéis
B2B/Ações com números reais (Pastore como Parceiro ativo).

## Rollback conceitual (MANUAL, com aprovação — nunca automático)

1. Só considerar rollback se: painel quebrado no navegador após
   hard-refresh, OU 1º ciclo com falha estrutural (não-AVISO), OU
   check-access da VPS falhando.
2. Caminho: `git -C /opt/soprolife/soprolife-site reset --hard 3815704`
   **somente** com `git status` limpo conferido antes e aprovação
   explícita (regra da skill) — units não mudaram, nada de systemd.
3. Após rollback: 1 ciclo do timer (13 etapas antigas) verde + HTTP 200;
   os summaries se regeneram sozinhos.
4. Registrar a causa antes de qualquer nova tentativa.

## Critérios de PARADA (abortar a janela)

- Pré-check remoto: repo da VPS sujo ou em commit inesperado → parar e
  entender ANTES do pull.
- `git log HEAD..origin/...` mostrando commits fora da tabela → parar.
- `pull --ff-only` recusando (histórico divergente) → parar; nunca forçar.
- Pós-deploy: check-access da VPS falhando → rollback é o caminho, não
  "consertar na mão em produção".

## O que NÃO fazer

- Nada de `reset --hard` como primeira opção; nada de `--force`.
- Não editar arquivos na VPS à mão; não tocar em units/systemd (o
  pacote não os altera); não reiniciar `soprolife-painel.service`
  (não é necessário — conteúdo estático + o proxy não mudou... conferir:
  `command-center-local-server.py` NÃO está no pacote M3–M6 ✔).
- Não deployar sem o bump do `?v=` do app.js commitado.
- Não pular a parada de conferência da lista de commits.
