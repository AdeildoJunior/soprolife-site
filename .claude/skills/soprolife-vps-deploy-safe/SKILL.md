---
name: soprolife-vps-deploy-safe
description: Deploy seguro do Painel SoproLife na VPS via Tailscale — conferir branch/commit local e remoto antes, sincronizar dados que não vêm pelo git, e nunca reiniciar serviço ou sobrescrever a VPS sem checar o estado dela antes.
---

# soprolife-vps-deploy-safe

## Objetivo
Padronizar o deploy do painel na VPS via Tailscale de forma segura: sem expor segredos, sem sobrescrever dado real não versionado, e sem derrubar o painel ao vivo sem aviso.

## Quando usar
- Quando o usuário pedir explicitamente para "mandar pra VPS", "atualizar a VPS", "subir pro painel oficial", "sincronizar com a VPS".

## Quando não usar
- Nunca proativamente — deploy só acontece quando solicitado explicitamente.
- Não usar para o site público (GitHub Pages), que tem fluxo de publicação próprio e não passa pela VPS do painel.
- Não usar para editar a planilha real ou o Apps Script — isso é `soprolife-sheets-sync`.

## Arquivos e pastas relevantes
- Repositório remoto na VPS: `/opt/soprolife/soprolife-site` (caminho ilustrativo — confirmar o real antes de assumir).
- `painel-soprolife/scripts/command-center-local-server.py` — servidor local/proxy do painel na VPS.
- Serviço systemd do painel (nome ilustrativo: `soprolife-painel.service`).
- `painel-soprolife/data/*.local.json` — **não versionados**, precisam ser copiados manualmente quando o código depende deles.
- `painel-soprolife/scripts/check-access.sh` — rodar também na VPS depois do deploy.

## Fluxo padrão
1. Local: `git status --short` (deve estar limpo ou só com o que será commitado nesta tarefa).
2. Local: `git log --oneline -5` e confirmar branch `painel-soprolife-v01`.
3. Commit + push **só com aprovação explícita** do usuário.
4. Conectar via Tailscale SSH: `ssh <TAILSCALE_USER>@<TAILSCALE_IP>`.
5. Na VPS: `cd /opt/soprolife/soprolife-site && git status --short && git log --oneline -3` — checar se há mudança local não commitada na VPS antes de mexer.
6. `git fetch origin painel-soprolife-v01 && git log HEAD..origin/painel-soprolife-v01 --oneline` — mostrar exatamente o que vai entrar antes de aplicar.
7. `git pull --ff-only origin painel-soprolife-v01` — nunca `reset --hard` sem checar o status antes.
8. Se o código novo depende de campos novos em `*.local.json` (ex.: mudança em Custos & Investimentos), comparar e copiar os dados também via `scp`, preservando dono/permissão dos arquivos existentes.
9. `curl -s -o /dev/null -w "%{http_code}"` no endpoint servido, pra confirmar HTTP 200.
10. Rodar `check-access.sh` também na VPS.
11. Só reiniciar o serviço systemd se for estritamente necessário, e avisar o impacto (queda momentânea) antes de fazer.

## Comandos seguros
```
ssh -o ConnectTimeout=8 <TAILSCALE_USER>@<TAILSCALE_IP> "cd /opt/soprolife/soprolife-site && git status --short"
git fetch origin painel-soprolife-v01
git log HEAD..origin/painel-soprolife-v01 --oneline
git pull --ff-only origin painel-soprolife-v01
curl -s -o /dev/null -w "%{http_code}\n" http://<TAILSCALE_IP>:<PAINEL_PORT>/painel-soprolife/
scp <arquivo_local> <TAILSCALE_USER>@<TAILSCALE_IP>:<CAMINHO_REMOTO>
```

## Checks obrigatórios
- Branch e commit locais conferidos antes do push.
- `git log HEAD..origin/painel-soprolife-v01 --oneline` mostrado ao usuário antes do `pull` na VPS.
- HTTP 200 confirmado depois do deploy.
- `check-access.sh` rodado na VPS depois do deploy.
- Se o painel depende de `*.local.json`, comparar dado local x dado da VPS antes de considerar o deploy completo.

## Proibições
- Não fazer `git reset --hard` na VPS sem checar `git status --short` antes — pode apagar dado real não versionado.
- Não expor a porta do painel publicamente (bind em `0.0.0.0` sem Tailscale) — o painel deve continuar acessível só via Tailscale.
- Não reiniciar o serviço systemd sem explicar o impacto (o painel fica fora do ar por alguns segundos).
- Não commitar, dar push ou fazer deploy sem autorização explícita.
- Nunca escrever o IP real da VPS, usuário SSH real ou porta real em texto — usar `<TAILSCALE_IP>`, `<TAILSCALE_USER>`, `<PAINEL_PORT>`.
- Não sobrescrever um patch/mudança que já esteja na VPS sem antes conferir `git status`/`git log` de lá.

## Erros já observados
- `app.js` atualizado e enviado via git, mas os arquivos `*.local.json` (gitignored) nunca sincronizados manualmente — o painel na VPS continuou mostrando dado antigo (ou pior, número zerado / "dado não disponível") mesmo depois do "deploy" estar tecnicamente completo do lado do código.
- Cache do navegador servindo uma versão antiga de um JSON local mesmo depois do dado já ter sido corrigido na VPS — resolvido com cache-busting (`?_cb=timestamp` + `cache: "no-store"`) nas leituras de dados locais do painel.

## Exemplos de prompts
- "Suba essa correção pra VPS depois que eu aprovar o commit."
- "Verifique se a VPS está com a mesma versão do GitHub antes de mexer em mais alguma coisa."
- "Não reinicia o serviço sem me avisar antes."

## Comando de revisão após a tarefa
```
ssh <TAILSCALE_USER>@<TAILSCALE_IP> "cd /opt/soprolife/soprolife-site && git log --oneline -3 && git status --short"
```
