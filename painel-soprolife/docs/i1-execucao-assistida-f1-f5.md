# I1 — Execução assistida F1–F5 (roteiro operacional da janela)

> ⚠️ **NÃO EXECUTAR NADA DESTE DOCUMENTO SEM: checklist GO/NO-GO completo
> (i1-go-no-go-checklist.md) + veredito LIBERADO do GPT + usuário presente.**
> Todos os comandos rodam NA VPS pelo operador humano, um bloco por vez,
> com o rollback da fase aberto ao lado. Placeholders sempre.
>
> **Estratégia de usuário (decidida):** usuário de SISTEMA `soprolife`,
> home `/var/lib/soprolife`, shell `/usr/sbin/nologin`. Nenhum passo
> depende de shell interativo: tudo via `runuser -u soprolife --` ou
> `sudo -u soprolife` com `HOME=/var/lib/soprolife` explícito.
>
> Referências: visão geral em `i1-timer-sem-root-execucao.md`; precheck
> (F0) já coberto por `scripts/i1-precheck-vps-readonly.sh`.

---

## F1 — Usuário e diretórios

**Objetivo:** garantir usuário de sistema `soprolife` seguro e diretórios
de config prontos, sem tocar em units.

**Comandos propostos (⚠️ NÃO EXECUTAR sem GPT):**
```bash
# criar SOMENTE se 'id soprolife' falhar (se já existir, NÃO recriar —
# validar shell/home existentes e levar a divergência ao GPT):
id soprolife 2>/dev/null || \
  useradd --system --home-dir /var/lib/soprolife --create-home \
          --shell /usr/sbin/nologin soprolife
getent passwd soprolife            # conferir home e shell resultantes
install -d -m 700 -o soprolife -g soprolife /var/lib/soprolife/.config
install -d -m 700 -o soprolife -g soprolife /var/lib/soprolife/.config/soprolife/painel
install -d -m 700 -o soprolife -g soprolife /var/lib/soprolife/.config/gcloud
```

**Checks de sucesso:** `getent passwd soprolife` mostra
`/var/lib/soprolife` + `/usr/sbin/nologin`; usuário de sistema sem senha;
`id` sem grupo sudo/wheel/admin; os 3 diretórios com dono soprolife e 700.

**Rollback da fase:** se o usuário foi criado NESTA janela e algo deu
errado: `userdel -r soprolife` (SÓ se criado agora — NUNCA se pré-existia).
Diretórios criados são inertes.

**⏸ PARADA GPT:** relatar `getent passwd` + `id` + `ls -ld` dos diretórios.
Se o usuário pré-existia com home/shell diferentes, PARAR aqui e decidir
com o GPT (migrar vs adotar o existente) antes de qualquer F2.

---

## F2 — Permissões mínimas dos dados do painel

**Objetivo:** dar ao timer posse do que ele escreve SEM quebrar o
`soprolife-painel.service`, que ainda não foi migrado.

**Passo (a) — identificar o usuário REAL do painel (read-only):**
```bash
systemctl show soprolife-painel.service -p User -p Group
ps -o user= -p "$(systemctl show soprolife-painel.service -p MainPID --value)" 2>/dev/null
```

**Passo (b) — decidir com o GPT o modelo de acesso a `data-private/`:**
- **Painel roda como root** (provável hoje): root ignora permissões —
  o timer pode ter posse exclusiva sem quebrar nada.
- **Painel roda como usuário ≠ root e ≠ soprolife:** usar GRUPO
  controlado compartilhado — nunca 700.
- **Painel e timer com o MESMO usuário** (cenário futuro F6): aí sim
  `chmod 700` é a opção correta.

**Passo (c) — comandos por cenário (⚠️ NÃO EXECUTAR sem GPT; executar SÓ
o bloco do cenário decidido):**
```bash
# CENÁRIO grupo compartilhado (painel com usuário próprio ≠ soprolife):
groupadd --system soprolife-data 2>/dev/null || true
usermod -aG soprolife-data <USUARIO_DO_PAINEL>
chown -R soprolife:soprolife-data /opt/soprolife/soprolife-site/painel-soprolife/data-private
chmod 750 /opt/soprolife/soprolife-site/painel-soprolife/data-private
find /opt/soprolife/soprolife-site/painel-soprolife/data-private -maxdepth 1 -type f -name '*.json' -exec chmod 640 {} +

# CENÁRIO painel como root (root lê independente de permissão):
chown -R soprolife:soprolife /opt/soprolife/soprolife-site/painel-soprolife/data-private
# chmod 700 SÓ neste cenário ou quando painel+timer forem o mesmo usuário:
chmod 700 /opt/soprolife/soprolife-site/painel-soprolife/data-private

# summaries públicos (qualquer cenário — servidos ao navegador):
chown soprolife:soprolife /opt/soprolife/soprolife-site/painel-soprolife/data/*.local.json 2>/dev/null || true
chmod 644 /opt/soprolife/soprolife-site/painel-soprolife/data/*.local.json 2>/dev/null || true
chown soprolife:soprolife /opt/soprolife/soprolife-site/painel-soprolife/data
```

**Posse do repo:** o caminho recomendado é
`git config --system --add safe.directory /opt/soprolife/soprolife-site`
(repo segue do dono atual; deploy inalterado).
`chown -R soprolife:soprolife /opt/soprolife/soprolife-site` no repo
INTEIRO é **NÃO RECOMENDADO** — muda o modelo de deploy e a posse de tudo;
só com aprovação EXPLÍCITA do GPT registrada no checklist.

**Checks de sucesso:** `ls -ld data data-private` conforme o cenário;
painel continua HTTP 200 **imediatamente após cada chown/chmod**
(testar antes de prosseguir).

**Rollback da fase:** reverter donos/permissões para a foto do precheck
(seção [8] do relatório é o "antes").

**⏸ PARADA GPT:** relatar saída do passo (a) + `ls -ld` antes/depois +
HTTP do painel.

---

## F3 — Credenciais ADC e configs (sem cópia cega)

**Objetivo:** o usuário `soprolife` autentica nas APIs Google por conta
própria; configs copiadas UMA A UMA, só as aprovadas.

**Passo (a) — listar o que existe (nomes/permissões, nunca conteúdo):**
```bash
ls -l /root/.config/soprolife/painel/
```

**Passo (b) — GPT aprova a lista de arquivos a copiar** (esperados:
`google-sheets.local.json`, `resumo-dashboard.json`/CSV se houver —
qualquer arquivo inesperado fica para trás até ser explicado).

**Passo (c) — copiar SOMENTE os aprovados, arquivo por arquivo
(⚠️ NÃO EXECUTAR sem GPT):**
```bash
install -m 600 -o soprolife -g soprolife \
  /root/.config/soprolife/painel/<ARQUIVO_APROVADO> \
  /var/lib/soprolife/.config/soprolife/painel/<ARQUIVO_APROVADO>
# (repetir por arquivo aprovado — sem curingas, sem cp -a, sem cópia cega)
```

**Passo (d) — venv recriado (nunca copiado) e ADC próprio:**
```bash
sudo -u soprolife HOME=/var/lib/soprolife bash -c '
  python3 -m venv /var/lib/soprolife/.local/share/soprolife/venvs/google-sheets &&
  /var/lib/soprolife/.local/share/soprolife/venvs/google-sheets/bin/pip install \
    -r /opt/soprolife/soprolife-site/painel-soprolife/requirements-google.txt'
# ADC device flow (interativo no navegador da estação; sem shell de login):
sudo -u soprolife HOME=/var/lib/soprolife gcloud auth application-default login \
  --no-launch-browser --scopes=<ESCOPOS_DECIDIDOS_NO_CHECKLIST>
sudo -u soprolife HOME=/var/lib/soprolife gcloud auth application-default \
  set-quota-project <PROJECT_ID>
```

**REGRA DURA:** o ADC do root NUNCA é copiado — nem como atalho, nem
"temporariamente" — sem aprovação EXPLÍCITA do GPT registrada por
escrito. Se o device flow travar: adiar F3, encerrar a janela SEM
instalar units (o timer root atual segue funcionando; nada quebrou).

**Checks de sucesso:**
`sudo -u soprolife HOME=/var/lib/soprolife gcloud auth application-default print-access-token >/dev/null && echo ADC-OK`
(nunca exibir o token); venv com dependências instaladas.

**Rollback da fase:** nenhum efeito em produção — artefatos ficam no home
do soprolife para a próxima tentativa.

**⏸ PARADA GPT:** relatar a lista do passo (a), o que foi copiado e os
exit codes (nunca tokens).

---

## F4 — Instalar service/timer (ainda sem habilitar)

**Objetivo:** trocar os units pela versão sem root, com backup, SEM
entregar ao timer ainda.

**Comandos propostos (⚠️ NÃO EXECUTAR sem GPT):**
```bash
[ -f /etc/systemd/system/soprolife-update-data.service ] && \
  cp /etc/systemd/system/soprolife-update-data.service{,.bak.$(date +%Y%m%d-%H%M%S)}
[ -f /etc/systemd/system/soprolife-update-data.timer ] && \
  cp /etc/systemd/system/soprolife-update-data.timer{,.bak.$(date +%Y%m%d-%H%M%S)}
install -m 644 /opt/soprolife/soprolife-site/painel-soprolife/systemd/soprolife-update-data.service.example \
  /etc/systemd/system/soprolife-update-data.service
install -m 644 /opt/soprolife/soprolife-site/painel-soprolife/systemd/soprolife-update-data.timer.example \
  /etc/systemd/system/soprolife-update-data.timer
systemctl daemon-reload
```

**Checks de sucesso:** `systemctl cat soprolife-update-data.service`
mostra `User=soprolife`, `HOME=/var/lib/soprolife` e as proteções;
`.bak` datados existem.

**Rollback da fase:** restaurar os `.bak` + `systemctl daemon-reload`.

**⏸ PARADA GPT:** colar o `systemctl cat` (units não têm segredo).

---

## F5 — DUAS execuções manuais e SÓ DEPOIS habilitar o timer

**Objetivo:** provar duas execuções completas e idempotentes sem root
antes de entregar ao timer.

**Passo (a) — teste manual, duas vezes (⚠️ NÃO EXECUTAR sem GPT):**
```bash
systemctl start soprolife-update-data.service
systemctl status soprolife-update-data.service --no-pager
journalctl -u soprolife-update-data.service -n 80 --no-pager
# repetir o bloco acima uma 2ª vez (idempotência)
```

**Checks de sucesso (cada execução):** `status=0/SUCCESS`; 13/13 etapas
no journal; `check-access.sh` exit 0 na VPS; summaries regenerados com
dono soprolife e 644; painel com dado fresco
(`http://<VPS_TAILSCALE>:8765/painel-soprolife/`); nenhuma escrita nova
em `/root/`.

**⏸ PARADA GPT (obrigatória):** journal das 2 execuções. **O timer só é
habilitado com as 2/2 verdes E aprovação explícita do GPT.**

**Passo (b) — habilitar o timer (só após a parada acima):**
```bash
systemctl enable --now soprolife-update-data.timer
systemctl list-timers 'soprolife-*' --no-pager
```

**Depois:** 2 ciclos reais do timer (≈20 min) verdes no journal;
monitorar 24h (journal + `generatedAt` de um summary + painel) antes de
considerar o I1 fechado. F6 (painel.service sem root) é etapa separada.

**Rollback da fase:** rollback do F4 (units `.bak` + daemon-reload) +
`systemctl enable --now soprolife-update-data.timer` no unit restaurado →
confirmar 1 execução `0/SUCCESS` como root.
