# M15.3B — Hardening operacional do deploy

Data: 18/07/2026 · Base: M15.3A (commit 14da85b) ·
Branch: `fable-m15-3b-hardening-operacional`

Esta etapa versiona as duas correções identificadas durante o deploy
produtivo da M15.3A (relatório do Sol de 18/07/2026). Não altera CRM,
pacientes, financeiro, parceiros nem a feature flag (`enabled` continua
`false`). Nenhum deploy foi executado nesta etapa; a VPS não foi tocada.

## Problema 1 — corrida de inicialização da API

`soprolife-m15-api.service` é `Type=simple`: o `systemctl` retorna assim que
o processo nasce, antes de Python, Pydantic, FastAPI e Uvicorn terminarem de
carregar. O deploy testava o health imediatamente, recebia "conexão
recusada" e o trap interrompia um serviço que estava subindo normalmente.

Correção: `painel-soprolife/nucleo-m15/scripts/lib-deploy-hardening.sh`
define `soprolife_wait_health_ok`, usada pelo deploy após iniciar/reiniciar
a API, o proxy loopback e (quando reiniciado) o proxy Tailscale:

- tentativas finitas (padrão 30) com intervalo curto (padrão 2 s);
- timeout total explícito (padrão 90 s);
- aceita somente HTTP 200 com body JSON `status=ok`;
- falha fechada ao esgotar os limites, com diagnóstico (tentativas, tempo,
  URL de health local — sem segredos) e orientação para `journalctl`;
- não usa sleep fixo como única solução e não mascara erro persistente: um
  serviço que realmente não sobe continua derrubando o deploy.

O deploy agora usa `systemctl enable` + `systemctl restart` (em vez de
`enable --now`) para que uma reexecução com a API já ativa passe a rodar o
código do commit implantado.

## Problema 2 — as duas interfaces do painel na porta 8765

O painel tem **duas interfaces**, ambas na porta 8765, servidas por
`painel-soprolife/scripts/command-center-local-server.py`:

| Interface | Unit | Papel |
|---|---|---|
| `IP-TAILSCALE:8765` | `soprolife-painel.service` | acesso real dos usuários via Tailscale |
| `127.0.0.1:8765` | `soprolife-painel-loopback.service` | health checks e validação local do deploy |

Durante o deploy produtivo, `127.0.0.1:8765` era atendido por um processo
manual órfão (iniciado em 10/07, fora do systemd, com código anterior à rota
M15) e devolvia 404 para o proxy. A unit loopback foi criada
operacionalmente na VPS; a M15.3B a versiona em
`painel-soprolife/systemd/soprolife-painel-loopback.service`:

- `User/Group=soprolife`, `WorkingDirectory=/opt/soprolife/soprolife-site`;
- bind exclusivo em `127.0.0.1:8765` (imposto por
  `SOPROLIFE_PANEL_HOST/PORT`; nunca `0.0.0.0`);
- mesmo hardening da unit da API (`ProtectSystem=strict`, repositório
  somente leitura, `NoNewPrivileges`, capabilities zeradas);
- **sem** `EnvironmentFile`: não acessa `/opt/soprolife/secrets`;
- não substitui nem conflita com a unit Tailscale existente (sem `Alias=`,
  sem `Conflicts=`).

## O que o deploy faz agora com a porta 8765

1. instala a unit versionada e executa `daemon-reload`;
2. inspeciona o listener em `127.0.0.1:8765`
   (`soprolife_garantir_porta_loopback_livre`):
   - porta livre → segue;
   - PID pertence à própria unit loopback → segue (o restart recarrega o
     código novo);
   - processo legado **validado** (usuário `soprolife`, comando de servidor
     do painel conhecido, fora de cgroup de outra unit systemd, escutando
     somente em loopback) → SIGTERM e espera finita pela liberação;
   - qualquer outra combinação (usuário, comando, cgroup ou listener
     inesperados; PID não identificável; porta não liberada após SIGTERM) →
     **falha fechada, sem matar nada**;
3. `systemctl enable` + `restart` da unit loopback — garante que o proxy
   local sirva o código do commit implantado;
4. espera de health com retry no proxy local e, se preciso reiniciar a unit
   Tailscale, também no proxy Tailscale.

## Reexecução e idempotência

O deploy é seguro em: primeira instalação; reexecução no mesmo commit; API
ativa ou inativa; unit loopback já instalada; processo manual antigo
conhecido; porta ocupada por processo desconhecido (aborta); proxy
temporariamente indisponível (retry); e após falha parcial anterior. Ele não
apaga backups, não recria segredos incompatíveis (valida e reutiliza o
`m15.env` existente), não recria administrador e não importa dados. Criação
de administrador (`m15-2-primeiro-usuario.md`) e importação continuam etapas
humanas separadas; `enabled=false` permanece após o deploy.

## Rollback

Igual ao M15.2 (`m15-2-proxy-seguro-deploy-vps.md`), com um item a mais: o
deploy grava `soprolife-painel-loopback.service.before` no diretório de
backup quando a unit já existia. Para reverter somente a unit loopback,
restaure o `.before`, rode `systemctl daemon-reload` e reinicie apenas
`soprolife-painel-loopback.service`. Em falha, o trap preserva banco e
backups e captura o journal da API e da unit loopback
(`failure-journal-*.log`).

## Verificação de serviços e portas

```bash
sudo systemctl status soprolife-m15-api.service --no-pager --full
sudo systemctl status soprolife-painel.service --no-pager --full
sudo systemctl status soprolife-painel-loopback.service --no-pager --full
sudo ss -ltnp | grep -E ':(5432|8015|8765)\b'
curl --fail --silent http://127.0.0.1:8015/api/v1/health
curl --fail --silent http://127.0.0.1:8765/painel-soprolife/api/m15/health
sudo journalctl -u soprolife-painel-loopback.service --since today --no-pager
```

Esperado: PostgreSQL somente em loopback; API somente em `127.0.0.1:8015`;
porta 8765 com um listener no IP Tailscale (unit Tailscale) e um em
`127.0.0.1` (unit loopback); nenhum listener em `0.0.0.0`.

## Testes desta etapa

```bash
bash painel-soprolife/nucleo-m15/scripts/test-deploy-hardening.sh
python3 painel-soprolife/scripts/test_command_center_m15_proxy.py
bash -n painel-soprolife/nucleo-m15/scripts/deploy-producao-vps.sh
```

O teste de shell usa dublês para provar retry/fail-closed/validação de
processo legado e um servidor HTTP efêmero em loopback para o probe real.
Nenhum teste toca a VPS, systemd real ou dados reais.
