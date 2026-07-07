# I1 — Timer sem root — Roteiro de EXECUÇÃO (janela na VPS)

> Complementa `i1-timer-sem-root-planejamento.md`. Nada aqui foi executado.
> **TODOS os comandos abaixo são sugestões — NÃO EXECUTAR sem revisão GPT**
> e sem o usuário presente na janela. Placeholders sempre (`<VPS_TAILSCALE>`).
> Templates dos units: `painel-soprolife/systemd/*.example`.

## Visão geral das fases

| Fase | O quê | Executor | Reversível? |
|---|---|---|---|
| F0 | Pré-checks read-only | IA (permitido) | n/a |
| F1 | Usuário + diretórios | usuário/root na janela | sim (nada ativo usa) |
| F2 | Estratégia ADC + configs + venv | usuário na janela | sim |
| F3 | Teste manual como soprolife (2x) | usuário na janela | n/a (só leitura de estado) |
| F4 | Instalar units + 1 start manual | usuário na janela | **rollback abaixo** |
| F5 | Ativação do timer + monitoramento | usuário | rollback abaixo |

## F0 — Pré-checks (read-only, sem aprovação extra)

```
systemctl cat soprolife-update-data.service soprolife-update-data.timer
systemctl cat soprolife-painel.service          # confirmar o User= atual
systemctl list-timers --all --no-pager | grep -i sopro
id soprolife || echo "usuario nao existe — criar na F1 (fase aprovada)"
sudo -l -U soprolife 2>/dev/null                 # se existir: NÃO pode ter sudo
ls -la /home/soprolife/ /home/soprolife/.ssh/ 2>/dev/null   # chaves inesperadas?
ls -la /opt/soprolife/soprolife-site/painel-soprolife/data-private/
ls -la /opt/soprolife/soprolife-site/painel-soprolife/data/*.local.json
ls -la /root/.config/soprolife/painel/ /root/.config/gcloud/ 2>/dev/null  # só nomes
git -C /opt/soprolife/soprolife-site status --short && git -C /opt/soprolife/soprolife-site log --oneline -3
```

Critério para prosseguir: confirmado se o usuário `soprolife` existe (e,
existindo, sem sudo e sem chave estranha); repo limpo e no commit esperado;
localização de ADC/configs/venv confirmada.

## F1 — Usuário e diretórios  ⚠️ NÃO EXECUTAR SEM REVISÃO GPT

Confirmar se o usuário existe; **criar SOMENTE se `id soprolife` falhar**
(nesta fase aprovada, nunca na F0):

```
# criar apenas se não existir — sem senha (locked), sem sudo; shell bash é
# necessário para os "sudo -u soprolife -i ..." das fases F2/F3:
id soprolife 2>/dev/null || \
  useradd --create-home --shell /bin/bash soprolife
# conferir shell/estado resultante (senha deve constar como travada):
getent passwd soprolife && passwd -S soprolife
install -d -m 700 -o soprolife -g soprolife /home/soprolife/.config
install -d -m 700 -o soprolife -g soprolife /home/soprolife/.config/soprolife/painel
install -d -m 700 -o soprolife -g soprolife /home/soprolife/.config/gcloud
```

Posse dos dados do painel (cirúrgico, NUNCA `-R` no repo inteiro sem revisão):

```
chown -R soprolife:soprolife /opt/soprolife/soprolife-site/painel-soprolife/data-private
chmod 700 /opt/soprolife/soprolife-site/painel-soprolife/data-private
chown soprolife:soprolife /opt/soprolife/soprolife-site/painel-soprolife/data/*.local.json
chmod 644 /opt/soprolife/soprolife-site/painel-soprolife/data/*.local.json
```

**Decisão pendente (GPT):** posse do repo — `chown` do repo para soprolife
(e deploy passa a fazer pull como soprolife) OU repo continua de root com
`git config --system safe.directory` e o pull segue como hoje.

## F2 — Estratégia ADC, configs e venv  ⚠️ NÃO EXECUTAR SEM REVISÃO GPT

```
# copiar configs privadas preservando modo (600):
cp -a /root/.config/soprolife/painel/. /home/soprolife/.config/soprolife/painel/
chown -R soprolife:soprolife /home/soprolife/.config/soprolife

# venv: RECRIAR (não copiar — venv tem caminhos absolutos do root):
sudo -u soprolife bash -c '
  python3 -m venv /home/soprolife/.local/share/soprolife/venvs/google-sheets &&
  /home/soprolife/.local/share/soprolife/venvs/google-sheets/bin/pip install \
    -r /opt/soprolife/soprolife-site/painel-soprolife/requirements-google.txt'

# ADC como soprolife — fluxo sem navegador (device flow), interativo:
sudo -u soprolife -i gcloud auth application-default login --no-launch-browser \
  --scopes=<ESCOPOS: sheets.readonly, drive, webmasters.readonly, analytics.readonly>
sudo -u soprolife -i gcloud auth application-default set-quota-project <PROJECT_ID>
```

**Decisões pendentes (GPT):** (a) escopos exatos — copiar a linha validada na
skill soprolife-sheets-sync; (b) o `update-local-data.sh` usa
`$HOME/.local/share/...` para o venv — com `HOME=/home/soprolife` no unit os
caminhos resolvem sozinhos; confirmar que NENHUM script tem `/root/` fixo:
`grep -rn "/root/" painel-soprolife/scripts/` (esperado: nada).

## F3 — Teste manual como soprolife (DUAS execuções)

```
sudo -u soprolife HOME=/home/soprolife \
  /opt/soprolife/soprolife-site/painel-soprolife/scripts/update-local-data.sh
```

Critérios (as duas vezes): 13/13 etapas; `check-access.sh` exit 0;
summaries regenerados com dono soprolife e 644; painel (ainda servido pelo
serviço atual) mostrando dado fresco após refresh; nenhuma escrita nova em
`/root/`.

## F4 — Instalar units + start manual  ⚠️ NÃO EXECUTAR SEM REVISÃO GPT

Seguir soprolife-systemd-safe (backup datado ANTES, sempre):

```
# Backups defensivos — não falham se o unit ainda não existir:
[ -f /etc/systemd/system/soprolife-update-data.service ] && \
  cp /etc/systemd/system/soprolife-update-data.service{,.bak.$(date +%Y%m%d-%H%M%S)}
[ -f /etc/systemd/system/soprolife-update-data.timer ] && \
  cp /etc/systemd/system/soprolife-update-data.timer{,.bak.$(date +%Y%m%d-%H%M%S)}
# instalar a partir do repo (fonte versionada = *.example):
install -m 644 /opt/soprolife/soprolife-site/painel-soprolife/systemd/soprolife-update-data.service.example \
  /etc/systemd/system/soprolife-update-data.service
install -m 644 /opt/soprolife/soprolife-site/painel-soprolife/systemd/soprolife-update-data.timer.example \
  /etc/systemd/system/soprolife-update-data.timer
systemctl daemon-reload
systemctl start soprolife-update-data.service
systemctl status soprolife-update-data.service --no-pager
journalctl -u soprolife-update-data.service -n 80 --no-pager
ps -o user= -C python3   # durante execução: NÃO pode aparecer root
```

Critério: `status=0/SUCCESS` + mesmos critérios da F3.

## F5 — Ativação do timer e monitoramento

```
systemctl restart soprolife-update-data.timer
systemctl list-timers --no-pager | grep -i sopro
# aguardar 2 ciclos (≈20 min) e conferir:
journalctl -u soprolife-update-data.service --since "-25 min" --no-pager | tail -40
curl -s -o /dev/null -w "%{http_code}\n" http://<VPS_TAILSCALE>:8765/painel-soprolife/
```

Monitoramento nas 24h seguintes: 1 olhada no journal + timestamp de um
summary (`generatedAt`) + card "Últimas alterações"/fontes no painel.
Só depois de estável: considerar F6 (painel.service sem root — etapa
separada) e limpeza das cópias em `/root/.config` (não antes).

## Rollback (escrever aberto ao lado durante a F4/F5)

```
cp /etc/systemd/system/soprolife-update-data.service.bak.<TIMESTAMP> \
   /etc/systemd/system/soprolife-update-data.service
cp /etc/systemd/system/soprolife-update-data.timer.bak.<TIMESTAMP> \
   /etc/systemd/system/soprolife-update-data.timer
systemctl daemon-reload
systemctl restart soprolife-update-data.timer
journalctl -u soprolife-update-data.service -n 40 --no-pager   # 0/SUCCESS como root
```

Não apagar nada do ambiente soprolife preparado (fica para a próxima
tentativa). Se os `chown` da F1 atrapalharem o unit antigo (root lê tudo —
não deveriam), reverter posse só de `data-private/` para root é o único
ajuste eventualmente necessário. Registrar a causa antes de tentar de novo.

## Pontos que exigem confirmação do GPT antes da execução

1. Posse do repo: `chown` vs `safe.directory` (F1) — impacta o deploy.
2. Linha exata de escopos do ADC + quota project (F2).
3. Resultado do `grep -rn "/root/"` nos scripts (F2) — precisa vir vazio.
4. `sudo -l -U soprolife` vazio e `~/.ssh` sem surpresas (F0).
5. Janela única (F1→F5) ou F1–F3 num dia e F4–F5 no seguinte.
6. `ProtectSystem=full` agora, `strict` só no endurecimento pós-estável.
