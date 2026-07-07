# I1 F0 — Pré-check read-only da VPS

> Documentação de `painel-soprolife/scripts/i1-precheck-vps-readonly.sh`.
> É a fase F0 do roteiro `i1-timer-sem-root-execucao.md` em forma
> executável: diagnóstico permitido pela skill soprolife-vps-safe, sem
> nenhuma escrita remota e com relatório sanitizado para o GPT.

## Como funciona

O script roda **LOCALMENTE na estação de trabalho** e recebe o alvo SSH
como argumento único:

```bash
bash painel-soprolife/scripts/i1-precheck-vps-readonly.sh root@<VPS_TAILSCALE>
```

Fluxo: SSH read-only → coleta remota → **sanitização local** → análise
local (FAIL/WARN/veredito) → relatório salvo em:

```
~/Documents/SoproLife/_REVISOES_GPT/i1-precheck-vps-readonly-YYYYMMDD-HHMMSS.txt
```

O caminho do relatório é impresso ao final. Exit: `0` relatório gerado;
`1` falha de SSH (mensagem clara); `2` uso incorreto (argumento ausente).

## Garantias

- **Remoto 100% leitura**: somente `whoami/hostname/pwd/date`,
  `systemctl status|cat|show|list-timers|list-unit-files`, `find/id/
  getent/passwd -S`, `git status|log`, `ls` e `curl` GET no painel local
  da VPS. **Sem sudo. Sem rm/cp/mv/chmod/chown/useradd/mkdir/touch, sem
  systemctl de ação, sem apt/dnf/pip/npm.**
- **Análise no lado local**: o remoto é um coletor burro; FAIL/WARN e o
  scan de `/root/` fixo são computados na estação (o scan usa a cópia
  local dos scripts — conferir que o commit remoto, seção [7], bate com o
  local).
- **Sanitização antes de gravar/exibir**: URLs de Apps Script,
  deployment IDs (`AKfycb…`), `Bearer …`, `ya29…`, `AIza…`,
  `/spreadsheets/d/<id>`, valores de `token/api_key/secret/password`,
  e-mails, telefones brasileiros, CPF e IPs Tailscale `100.x.x.x` são
  substituídos por marcadores `[…-REDACTED]`. A saída bruta nunca toca o
  disco nem o terminal.
- Relatório gravado com `chmod 600`.

## O que o relatório contém

1. Cabeçalho (data, `ssh_exit`; o alvo é redigido).
2. **Análise local**: lista OK/INFO/WARN/FAIL + veredito
   (`GO condicional` sem FAILs / `NO-GO` com FAILs).
3. **Coleta remota sanitizada**, em 11 seções: identidade; units
   instalados; unit do update (`User=`/`ExecStart=` — confirma o motivo
   do I1); unit do painel; timers e última execução; usuário `soprolife`
   (existe? shell? `passwd -S`? `~/.ssh`?); repositório (status/log);
   permissões de `data/` e `data-private/`; configs+ADC do root
   (só existência/dono/permissão — nunca conteúdo); venvs; HTTP do painel.

## Interpretação pós-precheck real (estado confirmado da VPS)

O precheck real já rodou e fixou a realidade que a análise local agora
reconhece:

- usuário `soprolife` EXISTE (uid 1000, home `/home/soprolife`, shell
  `/bin/bash`) — home/shell atuais são **válidos**; ausência do usuário
  vira WARN "inesperado" (nada de criar sem GPT); `/var/lib/soprolife`
  NÃO é verificado — não é a realidade atual;
- painel `User=soprolife` → OK (mesmo usuário pós-migração; `chmod 700`
  em `data-private/` é seguro); painel com outro usuário → WARN;
- update `User=root` → INFO (motivo do I1); `User=soprolife` → I1 feito;
- timer enabled → INFO "pausar (`systemctl stop`) antes da troca na F4";
- ADC/venv presentes no root → INFO (F3 cria os do soprolife; nunca
  copiar ADC sem GPT); venv do soprolife ausente → INFO (esperado);
- arquivos `root:root` em `data/` → **WARN com contagem** — se 600, o
  painel (soprolife) não consegue lê-los; corrigir de forma DIRIGIDA na
  F2, com listagem aprovada;
- HTTP `000` no localhost → **WARN, não FAIL** — esperado quando o
  painel binda só no IP Tailscale; conferir manualmente em
  `http://<VPS_TAILSCALE>:8765/painel-soprolife/`.

## Critérios de NO-GO (análise local)

- usuário `soprolife` em grupo administrativo (sudo/wheel/admin);
- senha do usuário existente não travada (`NP` no `passwd -S`);
- repo não encontrado na VPS;
- caminho `/root/` fixo nos scripts.

WARNs (repo sujo, `~/.ssh` presente, usuário ausente, arquivos root em
`data/`, painel sem 200 no localhost) são decisão caso a caso na revisão.

## Como anexar ao GPT

O arquivo `.txt` gerado já é seguro por construção (sanitizado). Gerar o
pacote padrão com `scripts/i1-generate-review-pack.sh` (mesma pasta
`_REVISOES_GPT`) e anexar os dois juntos. O GPT responde GO/NO-GO da
F1–F5 com base no veredito + WARNs + checklist
`i1-go-no-go-checklist.md`.

## O que este script NÃO decide

- Não cria usuário, não corrige nada, não prepara ambiente (isso é F1+,
  com aprovação).
- A revisão de `~soprolife/.ssh` e do conteúdo de sudoers é manual — o
  script apenas sinaliza.
