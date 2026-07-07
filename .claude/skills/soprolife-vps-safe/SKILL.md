---
name: soprolife-vps-safe
description: Regras de acesso à VPS do painel (via Tailscale) — o que pode rodar sem pedir (read-only), o que exige aprovação explícita, checks de deploy e rollback. Complementa soprolife-vps-deploy-safe com a lista curta operacional.
---

# soprolife-vps-safe

## Quando usar
Qualquer sessão que toque a VPS (`/opt/soprolife/soprolife-site`), mesmo
que só para diagnóstico. Para o fluxo completo de deploy, ver também
soprolife-vps-deploy-safe.

## Identidade
Nunca escrever IP real, usuário SSH ou porta em arquivo/output — usar
`<TAILSCALE_IP>`, `<TAILSCALE_USER>`, `<PAINEL_PORT>` ou o nome tailnet.

## Read-only — permitido sem aprovação (diagnóstico)
```
whoami · hostname · id <user>
systemctl status/cat <unit> --no-pager
systemctl list-timers / list-unit-files | grep -i sopro
journalctl -u <unit> -n <N> --no-pager
ls -la <paths do painel>
git -C /opt/soprolife/soprolife-site status --short | log --oneline -5
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:<PAINEL_PORT>/...
```

## Proibido sem aprovação explícita NA tarefa atual
- `git pull/reset/checkout` na VPS; qualquer escrita em arquivo.
- `systemctl start/stop/restart/enable/disable/daemon-reload`.
- criar/alterar usuário, permissão, cron, unit.
- instalar/atualizar pacote; `pip install`.
- deletar QUALQUER coisa (backups `.bak` inclusive).

## Deploy (quando aprovado)
1. Local: branch/commit conferidos; commit já no remoto.
2. VPS: `git status --short` ANTES (pode haver mudança local lá).
3. `git fetch` + `git log HEAD..origin/<branch> --oneline` — mostrar o que
   entra ANTES do `git pull --ff-only`.
4. `*.local.json` não vêm pelo git — conferir se o código novo depende de
   campo novo e deixar o timer regenerar (ou sincronizar manualmente).
5. Depois: HTTP 200 no painel, `check-access.sh` na VPS, 1 ciclo do timer
   com `status=0/SUCCESS` no journal.

## Rollback padrão
`git -C /opt/soprolife/soprolife-site reset --hard <commit-anterior>` SÓ
com aprovação e SÓ após `git status` limpo; para units, restaurar o `.bak`
datado + `daemon-reload` (ver soprolife-systemd-safe).
