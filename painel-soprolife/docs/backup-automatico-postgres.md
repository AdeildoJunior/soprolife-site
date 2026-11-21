# Backup automático do PostgreSQL M15 (systemd)

Automatiza o backup diário do PostgreSQL 16 de produção, eliminando a
dependência exclusiva da execução manual/pré-deploy identificada na auditoria.

## Princípios

- **Um único mecanismo de dump.** Reutiliza o script oficial
  `nucleo-m15/scripts/backup-postgresql-m15.sh`. Nenhum segundo dumper é criado.
- **Separação de pastas** em `/opt/soprolife/backups/m15/`:
  - `daily/`   → backups periódicos (este timer). **Rotacionados (30 dias).**
  - `manual/`  → backups manuais (default do script). **Nunca rotacionados.**
  - `<STAMP>/` → backups pré-deploy (`deploy-producao-vps.sh`). **Nunca rotacionados.**
- **Evidência SHA-256** gerada e conferida junto de cada dump (`*.dump.sha256`).
- **Sem alterar schema nem dados.** Apenas `pg_dump` (leitura) + validação
  estrutural com `pg_restore --list` (sem restaurar).

## Componentes (versionados no Git)

| Arquivo | Papel |
|---|---|
| `nucleo-m15/scripts/backup-postgresql-m15.sh` | Dump oficial. Ganhou gate não-interativo (`SOPROLIFE_BACKUP_ALLOW_NONINTERACTIVE=1`) e geração de `.sha256`. |
| `nucleo-m15/scripts/backup-postgres-rotate.sh` | Retenção de 30 dias, restrita à pasta `daily`. |
| `systemd/soprolife-backup-postgres.service` | oneshot (root): dump + rotação. |
| `systemd/soprolife-backup-postgres.timer` | Diário às 02:00 BRT, `Persistent=true`. |
| `scripts/install-backup-postgres.sh` | Instalador (dry-run por padrão). |
| `scripts/uninstall-backup-postgres.sh` | Rollback (não remove backups). |

## Instalação na VPS (decisão humana)

```bash
cd /opt/soprolife/soprolife-site
# 1. Prévia (não altera nada; lista timers de backup existentes e o fuso):
bash painel-soprolife/scripts/install-backup-postgres.sh
# 2. Instala unidades + cria pasta daily (não habilita o timer):
sudo bash painel-soprolife/scripts/install-backup-postgres.sh --apply
# 3. Habilita o timer:
sudo bash painel-soprolife/scripts/install-backup-postgres.sh --apply --enable-timer
```

## Teste e verificação

```bash
sudo systemctl start soprolife-backup-postgres.service       # 1 rodada manual
systemctl status soprolife-backup-postgres.service --no-pager # status=0/SUCCESS
journalctl -u soprolife-backup-postgres.service -n 80 --no-pager
ls -l /opt/soprolife/backups/m15/daily                        # .dump + .sha256
( cd /opt/soprolife/backups/m15/daily && sudo sha256sum -c *.sha256 )
systemctl list-timers | grep soprolife-backup-postgres        # próxima = 02:00
```

**Não fazer restore em produção.** A validação do dump é apenas estrutural
(`pg_restore --list`), sem carregar dados.

## Observação sobre fuso

`OnCalendar=*-*-* 02:00:00 America/Sao_Paulo` exige systemd ≥ 252. Em systemd
mais antigo, remover o sufixo e garantir `timedatectl` em `America/Sao_Paulo`.
O instalador imprime a versão do systemd e o fuso para conferência.

## Pendências

- Cópia **off-site** encriptada (recomendação da auditoria) — fora do escopo
  desta etapa, a ser tratada separadamente.
