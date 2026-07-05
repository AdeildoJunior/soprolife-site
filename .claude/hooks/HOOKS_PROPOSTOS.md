# Hooks propostos para o Painel SoproLife

**Status: proposta apenas. Nada aqui foi instalado.** Nenhuma entrada foi adicionada a `settings.json` ou `settings.local.json`. Este documento existe para revisão antes de qualquer instalação futura.

Os hooks do Claude Code funcionam como travas automáticas amarradas a eventos (ex.: `PreToolUse` antes de rodar uma ferramenta, `PostToolUse` depois). Abaixo, cada trava é descrita com: o que bloqueia/avisa, o evento sugerido, um matcher de exemplo, e o risco de falso positivo/negativo — para que a decisão de instalar seja informada.

---

## 1. Bloquear commit de `painel-soprolife/data-private/`

- **Evento**: `PreToolUse`, matcher no tool `Bash` com comando casando `git commit` ou `git add`.
- **Ação**: se o comando tentar adicionar/commitar qualquer caminho dentro de `painel-soprolife/data-private/`, bloquear e avisar.
- **Observação**: a pasta já está no `.gitignore` da raiz (`painel-soprolife/data-private/`), então este hook é uma segunda camada de defesa (ex.: contra `git add -f`), não a primeira.
- **Risco**: baixo falso positivo — só dispara em comandos que mencionem literalmente o caminho.

## 2. Bloquear `application_default_credentials.json`

- **Evento**: `PreToolUse` em `Bash` (git add/commit) e também em `Write`/`Edit` se o caminho de destino casar `application_default_credentials.json`.
- **Ação**: bloquear qualquer tentativa de criar, editar ou commitar esse arquivo dentro do repositório.
- **Risco**: baixo. Esse arquivo nunca deveria existir dentro do repo de qualquer forma (deve ficar em `~/.config/gcloud/`).

## 3. Bloquear `command-center-config.local.json` real

- **Evento**: `PreToolUse` em `Bash` (git add/commit).
- **Ação**: bloquear commit de `painel-soprolife/data-private/command-center-config.local.json` (o arquivo real, com token). O `.example.json` correspondente (sem segredo) continua permitido.
- **Risco**: baixo — nome de arquivo específico e já gitignored por padrão.

## 4. Bloquear `.env`, tokens, secrets, credenciais Google

- **Evento**: `PreToolUse` em `Bash` (git add/commit).
- **Ação**: bloquear se o comando referenciar `.env`, `*.env.*`, `*token*`, `*secret*`, `*credential*`, `*service-account*.json` fora da lista de exemplos/templates já aprovados (`*.example.json`).
- **Risco**: médio falso positivo — nomes como "secret" podem aparecer em contexto legítimo (ex.: comentário de código, nome de variável em template). Recomendado revisar a lista de padrões antes de ativar em modo bloqueante; começar em modo "avisar" (warn) antes de "bloquear" (deny).

## 5. Rodar `check-access.sh` antes de commit/deploy

- **Evento**: `PreToolUse` em `Bash`, matcher em `git commit` (e, separadamente, antes de qualquer comando de deploy/`ssh`+`git pull` na VPS).
- **Ação**: rodar `bash painel-soprolife/scripts/check-access.sh` a partir da raiz do repo; se o exit code não for 0, bloquear o commit/deploy e mostrar a saída do script.
- **Risco**: baixo, mas adiciona alguns segundos a cada commit — considerar rodar só quando arquivos relevantes (`data/`, `js/app.js`, `data-private/`) mudaram, não em todo commit.

## 6. Rodar `node --check painel-soprolife/js/app.js` quando `app.js` mudar

- **Evento**: `PreToolUse` em `Bash` (git commit) ou `PostToolUse` em `Edit`/`Write` quando o arquivo editado for `painel-soprolife/js/app.js`.
- **Ação**: rodar `node --check` no arquivo; se falhar, bloquear o commit ou avisar imediatamente depois da edição (mais cedo é melhor).
- **Risco**: baixo. Comando rápido e sem efeito colateral.

## 7. Rodar `python3 -m py_compile` em scripts Python alterados

- **Evento**: `PostToolUse` em `Edit`/`Write` quando o arquivo for `painel-soprolife/scripts/*.py`, ou `PreToolUse` em `Bash` (git commit) verificando todos os `.py` no diff.
- **Ação**: `python3 -m py_compile <arquivo>`; se falhar, avisar/bloquear.
- **Risco**: baixo.

## 8. Avisar se mexer em VPS/deploy/systemd/Apps Script

- **Evento**: `PreToolUse` em `Bash`, matcher em comandos contendo `ssh`, `systemctl`, `scp` para host remoto, ou edição de arquivos `.gs`/`apps-script/`.
- **Ação**: não bloquear (essas ações às vezes são legítimas e aprovadas), mas exibir um aviso destacado lembrando: "Isso afeta ambiente compartilhado/produção — confirme autorização explícita antes de prosseguir." Ideal como hook informativo, não bloqueante.
- **Risco**: baixo, é só um lembrete visual.

## 9. Avisar se mexer em financeiro/custos

- **Evento**: `PreToolUse` ou `PostToolUse` em `Edit`/`Write` quando o caminho casar `custos-investimentos` (dado ou código relacionado).
- **Ação**: aviso lembrando as regras de `soprolife-finance-costs` (não confundir responsável com pagador, não inventar valor, usar "—" quando desconhecido).
- **Risco**: baixo, é um lembrete, não bloqueio.

## 10. Bloquear dados sensíveis (CPF, Pix, dados bancários, comprovantes) em arquivos commitáveis

- **Evento**: `PreToolUse` em `Bash` (git add/commit).
- **Ação**: rodar um grep nos arquivos staged por padrões como CPF (`\d{3}\.\d{3}\.\d{3}-\d{2}`), chave Pix, "comprovante", "id_99pay" (ou equivalente de gateway de pagamento) e bloquear o commit se encontrar, pedindo confirmação manual.
- **Risco**: médio falso positivo em documentação/skill que *fale sobre* esses conceitos sem conter o dado em si (ex.: este próprio arquivo de hooks propostos menciona "CPF" e "Pix" como conceito). Recomendado: o grep deve procurar por *padrões de valor* (regex de CPF, chave Pix no formato de e-mail/telefone/aleatória), não pela palavra "CPF" isolada — para não bloquear documentação legítima que apenas *fala sobre* a regra.

---

## Ordem de instalação sugerida (quando aprovado)

1. Primeiro os hooks 1, 2, 3, 6 e 7 (bloqueios objetivos e de baixo risco de falso positivo).
2. Depois os hooks 5 e 10 (checks mais pesados/com mais chance de falso positivo) — começar em modo "avisar", não "bloquear".
3. Por último os hooks 4, 8 e 9 (avisos informativos) — ajustar os padrões de regex conforme o uso real mostrar falsos positivos.

Nenhum hook foi instalado nesta rodada, conforme solicitado.
