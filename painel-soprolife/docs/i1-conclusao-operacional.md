# I1 — Conclusão operacional: timer/update sem root

Data da conclusão operacional: 2026-07-07  
Projeto: SoproLife Command Center / Painel SoproLife  
Branch: `painel-soprolife-v01`  
Commit de referência: `cff9df9` — `Ajusta I1 à realidade do timer existente`

## Objetivo

Migrar a rotina automática de atualização dos dados do painel para execução sem `root`, usando o usuário operacional `soprolife`.

## Estado antes da I1

- `soprolife-painel.service` já rodava como `soprolife`;
- `soprolife-update-data.service` ainda rodava como `root`;
- `soprolife-update-data.timer` já existia;
- havia histórico de arquivos locais gerados por `root`.

## Estado final validado

- `soprolife-painel.service` roda como `soprolife`;
- `soprolife-update-data.service` roda como `soprolife`;
- `soprolife-update-data.timer` está ativo;
- atualização automática executou com `Result=success`;
- atualização automática executou com `ExecMainStatus=0`;
- painel respondeu `HTTP 200` via Tailscale;
- `check-access.sh` passou;
- nenhum `.local.json` ou `*-summary.local.json` novo ficou como `root`;
- repo da VPS ficou em `cff9df9`.

## Service esperado

O service real da VPS deve manter:

- `User=soprolife`;
- `Group=soprolife`;
- `HOME=/home/soprolife`;
- `CLOUDSDK_CONFIG=/home/soprolife/.config/gcloud`;
- `ExecStart=/bin/bash /opt/soprolife/soprolife-site/painel-soprolife/scripts/update-local-data.sh`.

## Permissões esperadas

Arquivos privados locais:

- ficam em `painel-soprolife/data-private/`;
- devem ser `gitignored`;
- devem pertencer a `soprolife:soprolife`;
- normalmente devem usar permissão `600`.

Summaries locais:

- ficam em `painel-soprolife/data/`;
- devem pertencer a `soprolife:soprolife`;
- arquivos servidos ao navegador podem ser legíveis pelo painel;
- arquivos `.local.json` devem permanecer fora do Git.

## Riscos residuais

Monitorar:

- falhas intermitentes do timer;
- expiração ou reautenticação do ADC Google;
- mudança acidental de ownership para `root`;
- criação de arquivo privado fora de `data-private`;
- alterações futuras no `update-local-data.sh` que dependam de ambiente interativo;
- divergência entre templates systemd e service real da VPS.

## Checklist pós-I1

Em 24 horas:

- confirmar `Result=success`;
- confirmar `ExecMainStatus=0`;
- confirmar próxima execução do timer;
- confirmar painel `HTTP 200`;
- rodar `check-access.sh`;
- confirmar que nenhum `.local.json` foi criado como `root`.

Em 7 dias:

- revisar histórico do timer;
- revisar logs do `soprolife-update-data.service`;
- confirmar que o ADC continua funcionando;
- confirmar que `SC=True` e `GA4=True` continuam quando aplicável;
- verificar se nenhum arquivo sensível foi criado em local indevido.

## Rollback conceitual

Backups dos units foram criados durante a I1 em `/root/soprolife-i1-backup/`.

Rollback só deve ser considerado se houver falha real de operação, com revisão prévia. Não executar rollback preventivo se o timer estiver saudável.

## Conclusão

I1 concluída: a atualização automática da VPS foi migrada de `root` para `soprolife`, mantendo o painel online e a rotina de atualização funcionando.
