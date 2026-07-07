# I1 — Execução assistida F1–F5 (migração do timer EXISTENTE root → soprolife)

> ⚠️ **NÃO EXECUTAR NADA DESTE DOCUMENTO SEM: checklist GO/NO-GO completo
> (i1-go-no-go-checklist.md) + veredito LIBERADO do GPT + usuário presente.**
> Todos os comandos rodam NA VPS pelo operador humano, um bloco por vez,
> com o rollback da fase aberto ao lado. Placeholders sempre.
>
> **Realidade confirmada pelo precheck (norte deste roteiro):**
> - o `soprolife-painel.service` JÁ roda como `User=soprolife` — este I1
>   migra APENAS o `soprolife-update-data.service`, que ainda roda como
>   root; após a F4, timer e painel usam o MESMO usuário;
> - o usuário `soprolife` JÁ EXISTE (uid 1000, home `/home/soprolife`,
>   shell `/bin/bash`) — **nada de useradd/usermod/chsh nesta I1**;
>   `/var/lib/soprolife` fica como alternativa FUTURA, se algum dia
>   houver justificativa (não há hoje);
> - o timer JÁ está enabled — será PAUSADO durante a troca;
> - ADC e venv Google estão no root; `data/`/`data-private/` têm donos
>   misturados, incluindo summaries `root:root 600` que o painel
>   (rodando como soprolife) não consegue ler.

---

## F1 — Confirmar usuário existente e preparar diretórios de config

**Objetivo:** validar o usuário que JÁ existe (sem criar, sem alterar
home/shell) e preparar seus diretórios de config.

**Comandos propostos (⚠️ NÃO EXECUTAR sem GPT):**
```bash
id soprolife && getent passwd soprolife       # deve existir; NÃO criar nada
passwd -S soprolife                           # senha deve estar travada (L/LK)
# NÃO rodar useradd/usermod/chsh — usuário atual é adotado como está.
install -d -m 700 -o soprolife -g soprolife /home/soprolife/.config
install -d -m 700 -o soprolife -g soprolife /home/soprolife/.config/soprolife/painel
install -d -m 700 -o soprolife -g soprolife /home/soprolife/.config/gcloud
```

**Checks de sucesso:** usuário existe, sem grupo sudo/wheel/admin, senha
travada, `~/.ssh` já revisado manualmente (precheck); 3 diretórios com
dono soprolife e 700.

**Rollback da fase:** nenhum (nada foi criado além de diretórios inertes).

**⏸ PARADA GPT:** `id` + `passwd -S` + `ls -ld` dos diretórios. Se a
senha NÃO estiver travada ou houver grupo administrativo: PARAR — decisão
GPT antes de qualquer F2 (travar senha é 1 comando, mas é mudança de
estado do usuário e precisa de aprovação explícita).

---

## F2 — Permissões mínimas e DIRIGIDAS (listar antes, corrigir só o listado)

**Objetivo:** corrigir os donos misturados que o precheck encontrou —
somente arquivos gerados do painel, um conjunto listado e aprovado.
Como painel e (pós-F4) timer usam o MESMO usuário, `700` em
`data-private/` é seguro.

**Passo (a) — LISTAR o estado atual (read-only; nada muda):**
```bash
ls -ld /opt/soprolife/soprolife-site/painel-soprolife/data \
       /opt/soprolife/soprolife-site/painel-soprolife/data-private
ls -l  /opt/soprolife/soprolife-site/painel-soprolife/data/*.local.json
ls -lR /opt/soprolife/soprolife-site/painel-soprolife/data-private | head -40
```

**Passo (b) — ⏸ GPT aprova a lista exata de arquivos a corrigir**
(esperados: summaries `root:root`/600 em `data/` — que hoje o painel nem
consegue ler — e itens de `data-private/` fora de soprolife).

**Passo (c) — corrigir SÓ o aprovado (⚠️ NÃO EXECUTAR sem GPT):**
```bash
# somente dados gerados do painel — NUNCA chown -R no repo inteiro:
chown -R soprolife:soprolife /opt/soprolife/soprolife-site/painel-soprolife/data-private
chmod 700 /opt/soprolife/soprolife-site/painel-soprolife/data-private
chown soprolife:soprolife /opt/soprolife/soprolife-site/painel-soprolife/data
chown soprolife:soprolife /opt/soprolife/soprolife-site/painel-soprolife/data/*.local.json
chmod 644 /opt/soprolife/soprolife-site/painel-soprolife/data/*.local.json
```
`chown -R` do REPO INTEIRO segue **NÃO RECOMENDADO** (o deploy hoje faz
pull como root); se o pull acusar dono, usar
`git config --system --add safe.directory /opt/soprolife/soprolife-site`.

**Checks de sucesso:** re-rodar o passo (a) — tudo soprolife; summaries
644; painel continua respondendo no IP Tailscale
(`http://<VPS_TAILSCALE>:8765/painel-soprolife/`) **imediatamente após**
as mudanças, e as seções que dependiam de summaries root:600 passam a
carregar.

**Rollback da fase:** a listagem do passo (a) é a foto do "antes" —
reverter dono/permissão só do que foi tocado.

**⏸ PARADA GPT:** listagem antes/depois + HTTP do painel.

---

## F3 — ADC e venv do usuário soprolife (sem cópia cega)

**Objetivo:** ADC e venv próprios em `/home/soprolife` — hoje ambos só
existem no root.

**Passo (a) — listar configs do root (nomes/permissões, nunca conteúdo):**
```bash
ls -l /root/.config/soprolife/painel/
```

**Passo (b) — ⏸ GPT aprova a lista de arquivos de config a copiar.**

**Passo (c) — copiar SÓ os aprovados, arquivo por arquivo:**
```bash
install -m 600 -o soprolife -g soprolife \
  /root/.config/soprolife/painel/<ARQUIVO_APROVADO> \
  /home/soprolife/.config/soprolife/painel/<ARQUIVO_APROVADO>
# (repetir por arquivo — sem curingas, sem cp -a, sem cópia cega)
```

**Passo (d) — venv recriado (nunca copiado) e ADC próprio:**
```bash
sudo -u soprolife HOME=/home/soprolife bash -c '
  python3 -m venv /home/soprolife/.local/share/soprolife/venvs/google-sheets &&
  /home/soprolife/.local/share/soprolife/venvs/google-sheets/bin/pip install \
    -r /opt/soprolife/soprolife-site/painel-soprolife/requirements-google.txt'
sudo -u soprolife HOME=/home/soprolife gcloud auth application-default login \
  --no-launch-browser --scopes=<ESCOPOS_DECIDIDOS_NO_CHECKLIST>
sudo -u soprolife HOME=/home/soprolife gcloud auth application-default \
  set-quota-project <PROJECT_ID>
```

**REGRA DURA:** o ADC do root NUNCA é copiado — nem como atalho, nem
"temporariamente" — sem aprovação EXPLÍCITA do GPT registrada por
escrito. Se o device flow travar: adiar F3 e encerrar a janela SEM tocar
nos units (o timer root atual segue funcionando; nada quebrou).

**Checks de sucesso:**
`sudo -u soprolife HOME=/home/soprolife gcloud auth application-default print-access-token >/dev/null && echo ADC-OK`
(nunca exibir o token); venv do soprolife existente com dependências.

**Rollback da fase:** nenhum efeito em produção — artefatos ficam no home
para a próxima tentativa.

**⏸ PARADA GPT:** lista do passo (a), o que foi copiado, exit codes.

---

## F4 — PAUSAR o timer e trocar o service root → soprolife

**Objetivo:** trocar o unit existente com o timer parado, com backup, sem
janela de disparo no meio da troca.

**Comandos propostos (⚠️ NÃO EXECUTAR sem GPT):**
```bash
# 1. PAUSAR o timer (pré-condição do GO — sem disparo durante a troca):
systemctl stop soprolife-update-data.timer
systemctl list-timers 'soprolife-*' --no-pager   # não deve listar próximo disparo

# 2. Backups datados (units EXISTEM — confirmado no precheck):
cp /etc/systemd/system/soprolife-update-data.service{,.bak.$(date +%Y%m%d-%H%M%S)}
cp /etc/systemd/system/soprolife-update-data.timer{,.bak.$(date +%Y%m%d-%H%M%S)}

# 3. Instalar a partir da fonte versionada (.example) e recarregar:
install -m 644 /opt/soprolife/soprolife-site/painel-soprolife/systemd/soprolife-update-data.service.example \
  /etc/systemd/system/soprolife-update-data.service
install -m 644 /opt/soprolife/soprolife-site/painel-soprolife/systemd/soprolife-update-data.timer.example \
  /etc/systemd/system/soprolife-update-data.timer
systemctl daemon-reload
```

**Checks de sucesso:** `systemctl cat soprolife-update-data.service`
mostra `User=soprolife`/`HOME=/home/soprolife`/proteções; `.bak` datados
existem; timer parado (sem próximo disparo).

**Rollback da fase:** restaurar os `.bak` + `systemctl daemon-reload` +
`systemctl start soprolife-update-data.timer` → confirmar 1 execução
`0/SUCCESS` como root (estado antigo de volta).

**⏸ PARADA GPT:** colar o `systemctl cat` + estado do timer.

---

## F5 — DUAS execuções manuais e SÓ DEPOIS reativar o timer

**Objetivo:** provar duas execuções completas e idempotentes como
soprolife antes de devolver o pipeline ao timer.

**Passo (a) — teste manual, duas vezes (⚠️ NÃO EXECUTAR sem GPT):**
```bash
systemctl start soprolife-update-data.service
systemctl status soprolife-update-data.service --no-pager
journalctl -u soprolife-update-data.service -n 80 --no-pager
# repetir o bloco acima uma 2ª vez (idempotência)
```

**Checks de sucesso (cada execução):** `status=0/SUCCESS`; 13/13 etapas
no journal; `check-access.sh` exit 0 na VPS; summaries regenerados com
dono soprolife e 644; painel com dado fresco no IP Tailscale; nenhuma
escrita nova em `/root/`.

**⏸ PARADA GPT (obrigatória):** journal das 2 execuções. **O timer só é
reativado com as 2/2 verdes E aprovação explícita do GPT.**

**Passo (b) — reativar o timer (só após a parada acima):**
```bash
systemctl enable --now soprolife-update-data.timer
systemctl list-timers 'soprolife-*' --no-pager
```

**Depois:** 2 ciclos reais (≈20 min) verdes no journal; monitorar 24h
(journal + `generatedAt` de um summary + painel). F6 — o painel já roda
como soprolife, então NÃO há F6 de painel; o próximo endurecimento
opcional é `ProtectSystem=strict` no service, etapa separada.

**Rollback da fase:** rollback do F4 (units `.bak` + daemon-reload +
start do timer restaurado) → confirmar 1 execução `0/SUCCESS` como root.
