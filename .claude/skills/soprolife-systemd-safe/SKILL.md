---
name: soprolife-systemd-safe
description: Procedimento seguro para criar/alterar services e timers systemd do painel (local ou VPS) — backup datado antes, daemon-reload, verificação por journal e rollback pronto. Nunca alterar permissões em massa sem revisão.
---

# soprolife-systemd-safe

## Quando usar
Criar ou alterar qualquer `soprolife-*.service` / `soprolife-*.timer`,
local ou na VPS (na VPS, somente com aprovação — ver soprolife-vps-safe).

## Antes de mexer
1. Ler o unit atual: `systemctl cat <unit>`.
2. Backup datado (padrão já usado na VPS):
   `cp /etc/systemd/system/<unit>{,.bak.$(date +%Y%m%d-%H%M%S)}`
3. Anotar o estado atual: `systemctl is-enabled <unit>` e último
   `status=` no journal.

## Ao alterar
- Editar/instalar o unit → `systemctl daemon-reload` (obrigatório; sem
  isso o systemd usa a versão antiga).
- Preferir 1 execução manual antes de confiar no timer:
  `systemctl start <service>` → `systemctl status <service> --no-pager`
  → `journalctl -u <service> -n 80 --no-pager`.
- Critério de sucesso: `status=0/SUCCESS` + efeitos esperados verificados
  (ex.: 13/13 etapas do update, summaries regenerados, HTTP 200).
- Timer: após o start manual OK, aguardar ao menos 1 ciclo real e conferir
  `systemctl list-timers | grep sopro`.

## Rollback (escrito antes de mexer, sempre)
1. Restaurar o `.bak` mais recente sobre o unit.
2. `systemctl daemon-reload`.
3. `systemctl restart <timer|service>` e confirmar 1 execução `0/SUCCESS`
   no estado antigo.
4. Registrar a causa da falha antes de nova tentativa.

## Proibições
- Nunca `chmod`/`chown` em massa (`-R` em diretórios largos) sem revisão
  linha a linha do que será atingido — permissão errada em `data-private/`
  ou nos summaries quebra privacidade ou o painel.
- Nunca apagar `.bak` de unit — são o rollback.
- Nunca editar unit em produção sem backup datado feito NESSA sessão.
- Restart de serviço que derruba o painel: avisar o impacto antes.
