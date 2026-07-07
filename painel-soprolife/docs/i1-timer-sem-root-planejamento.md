# I1 — Timer/serviço sem root na VPS — Planejamento

> Documento de planejamento (nenhuma ação executada). Gerado em 07/07/2026.
> Sem IP, usuário SSH, porta, token ou ID de planilha — placeholders sempre:
> `<VPS_TAILSCALE>`, `<TAILSCALE_IP>`, `<PAINEL_PORT>`.

## Objetivo

Migrar as rotinas automáticas do painel na VPS (atualização de dados e,
em segunda fase, o serviço do painel) de `root` para um usuário de sistema
sem privilégio, eliminando o caminho de escalonamento mais barato da VPS
sem quebrar o pipeline que hoje funciona.

## Estado atual conhecido

- VPS Ubuntu 24.04 LTS, acesso somente via Tailscale.
- Repositório em `/opt/soprolife/soprolife-site`, branch `painel-soprolife-v01`.
- Units existentes em `/etc/systemd/system/`:
  - `soprolife-painel.service` (enabled) — proxy/servidor do painel
    (`command-center-local-server.py`);
  - `soprolife-update-data.service` (disparado por timer) — roda
    `update-local-data.sh` (13 etapas);
  - `soprolife-update-data.timer` (enabled) — a cada 10 min, `Persistent=true`.
- O unit de update versionado no repo roda como **`User=root` / `HOME=/root`**;
  o usuário do serviço do painel precisa ser confirmado na janela (F0).
- Existência/estado do usuário `soprolife` na VPS: **confirmar no
  precheck (F0)** — não presumir; criação só ocorre na fase aprovada se
  `id soprolife` falhar.
- Dependências que hoje moram no HOME do root (a confirmar na F0):
  - ADC: `~/.config/gcloud/application_default_credentials.json`;
  - configs: `~/.config/soprolife/painel/*.json` (chmod 600);
  - venv: `~/.local/share/soprolife/venvs/google-sheets/`.
- Config do Command Center (token do Apps Script) em
  `painel-soprolife/data-private/command-center-config.local.json` (600).

## Risco de rodar rotinas como root

1. `update-local-data.sh` consome APIs externas com dependências pip:
   qualquer comprometimento de dependência executa como root.
2. O proxy do painel escuta rede (Tailscale) como root: um bug no handler
   HTTP vira execução privilegiada.
3. Um erro de script como root pode sobrescrever qualquer arquivo do
   sistema; sem root, o dano fica confinado ao escopo do painel.

## Usuário recomendado

Usuário de SISTEMA `soprolife`: home `/var/lib/soprolife`, shell
`/usr/sbin/nologin`, sem senha, sem sudo. **Criado SOMENTE se
`id soprolife` falhar** (se já existir, validar shell/home/grupos no
precheck e decidir com o GPT antes de adotar). Comandos que precisem
rodar como esse usuário usam `sudo -u soprolife HOME=/var/lib/soprolife`
ou `runuser -u soprolife --` — nunca shell interativo/de login.

## Diretórios necessários e permissões recomendadas

| Caminho | Dono | Permissão | Uso |
|---|---|---|---|
| `/opt/soprolife/soprolife-site` (repo) | `soprolife:soprolife` | padrão git | `git pull` pelo fluxo de deploy; `safe.directory` se o pull continuar sendo feito por outro usuário |
| `painel-soprolife/data-private/` | `soprolife` | 700 (dir), 600 (arquivos) | dados reais + config do Command Center |
| `painel-soprolife/data/*.local.json` | `soprolife` | **644** | summaries sem PII (M2) servidos ao navegador |
| `/var/lib/soprolife/.config/gcloud/` | `soprolife` | 700/600 | ADC reautenticado como `soprolife` |
| `/var/lib/soprolife/.config/soprolife/painel/` | `soprolife` | 700/600 | configs privadas dos conectores |
| `/var/lib/soprolife/.local/share/soprolife/venvs/` | `soprolife` | padrão | venv recriado (não copiar o do root — caminhos absolutos no venv) |

## Scripts candidatos ao timer (os mesmos de hoje)

- `update-local-data.sh` (orquestrador — único ExecStart do timer);
- indiretamente: `read-*-adc.py`, `generate-*.py`, `sync-*.sh`,
  `check-access.sh` (etapa 12/13) e `pii_guard` (M2).
- Fora do timer, mas na F2: `command-center-local-server.py`
  (soprolife-painel.service).

## Proposta inicial — service (substitui o atual, mesma função)

```ini
[Unit]
Description=SoproLife — Atualização automática dos dados do painel
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=soprolife
Group=soprolife
Environment=HOME=/var/lib/soprolife
WorkingDirectory=/opt/soprolife/soprolife-site
ExecStart=/bin/bash /opt/soprolife/soprolife-site/painel-soprolife/scripts/update-local-data.sh
StandardOutput=journal
StandardError=journal
# Endurecimento mínimo (não exagerar na 1ª migração):
PrivateTmp=true
NoNewPrivileges=true
```

## Proposta inicial — timer (inalterado exceto revisão)

```ini
[Timer]
OnBootSec=2min
OnActiveSec=1min
OnUnitActiveSec=10min
Persistent=true

[Install]
WantedBy=timers.target
```

## Plano de migração em fases

> Numeração HISTÓRICA deste planejamento. A numeração operacional
> canônica (e os comandos finais) é a de `i1-execucao-assistida-f1-f5.md`.

**F0 — Inventário fino (read-only, janela curta):** confirmar usuário do
`soprolife-painel.service`; listar donos/permissões atuais de
`data-private/` e `data/*.local.json`; confirmar onde estão ADC/venv/configs
e se o `soprolife` já tem algo no HOME.

**F1 — Preparar ambiente do usuário (sem tocar nos units):** copiar
SOMENTE configs aprovadas, arquivo por arquivo (`install -m 600`, sem
`cp -a` cego), de `/root/.config/soprolife/painel/` para
`/var/lib/soprolife/.config/soprolife/painel/`;
recriar venv como `soprolife` (`pip install -r requirements-google.txt`);
**reautenticar ADC como `soprolife`** com escopos explícitos (Sheets, Drive,
Search Console, GA4) + quota project — fluxo já validado na skill
`soprolife-sheets-sync`; `chown` de `data-private/` e dos `*.local.json`.

**F2 — Teste manual como usuário:** `sudo -u soprolife` rodando
`update-local-data.sh` completo; critério: 13/13 etapas, `check-access.sh`
exit 0, summaries regenerados com 644 e painel servindo dado fresco.
Rodar DUAS vezes (idempotência).

**F3 — Trocar o unit:** backup do unit atual (`.bak` datado, padrão já usado
na VPS), instalar unit novo, `daemon-reload`, disparar 1 execução manual
(`systemctl start`), verificar `status=0/SUCCESS` e journal; então aguardar
2 ciclos do timer.

**F4 — (Etapa separada, mesma receita) `soprolife-painel.service`** para o
usuário `soprolife` — só depois de F3 estável por alguns dias; envolve
queda momentânea do painel (avisar antes).

**F5 — Fechamento:** remover cópias de config do HOME do root (só após F3/F4
estáveis); registrar aprendizado na skill `soprolife-vps-deploy-safe`.

## Rollback (escrever antes, executar sem pensar)

1. Restaurar o unit `.bak` → `systemctl daemon-reload` →
   `systemctl restart soprolife-update-data.timer`.
2. Confirmar 1 execução `0/SUCCESS` como root (estado antigo funcional).
3. NÃO apagar o ambiente do `soprolife` preparado em F1 — fica pronto para a
   próxima tentativa; registrar a causa da falha antes de tentar de novo.
4. Se o painel ficou sem dado fresco: os summaries anteriores continuam no
   disco; nenhuma perda, só staleness — comunicar e corrigir com calma.

## Comandos sugeridos (NÃO executados — referência para a janela)

```bash
# F0
systemctl cat soprolife-painel.service
ls -la /opt/soprolife/soprolife-site/painel-soprolife/data-private/
ls -la /opt/soprolife/soprolife-site/painel-soprolife/data/*.local.json
# F1 (como root, preparando o usuário)
install -d -m 700 -o soprolife -g soprolife /var/lib/soprolife/.config/soprolife/painel
# ... cópias arquivo a arquivo com install -m 600; reauth ADC como soprolife
# F2
sudo -u soprolife HOME=/var/lib/soprolife /opt/soprolife/soprolife-site/painel-soprolife/scripts/update-local-data.sh
# F3
cp /etc/systemd/system/soprolife-update-data.service{,.bak.$(date +%Y%m%d-%H%M%S)}
systemctl daemon-reload && systemctl start soprolife-update-data.service
journalctl -u soprolife-update-data.service -n 80 --no-pager
```

## Pontos que exigem confirmação do GPT antes da execução

1. **ADC**: reautenticar como `soprolife` exige navegador/fluxo interativo —
   confirmar como será feito na VPS (device flow) e quem estará presente.
2. **Posse do repo**: `chown -R soprolife` no repo inteiro vs. manter root e
   usar `safe.directory` — impacta o fluxo de deploy atual (`git pull` como
   quem?). Decidir antes da F1.
3. **Se o usuário já existir na VPS** (verificar no precheck): confirmar
   que não tem sudo nem chaves autorizadas inesperadas, e decidir com o
   GPT se adota o existente ou migra para o desenho de sistema (F0).
4. **soprolife-painel.service (F4)**: janela de queda momentânea do painel —
   agendar com os sócios.
5. Ordem F1→F3 numa janela só, ou F1 num dia e F3 no seguinte (mais seguro).
