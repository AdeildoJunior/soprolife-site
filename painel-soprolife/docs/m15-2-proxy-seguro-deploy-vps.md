# M15.2 — proxy seguro de mesma origem e implantação produtiva

Status: kit versionado e testado; **produção não foi implantada** e a feature
flag continua desligada.

## Arquitetura

Antes, o frontend tentava alcançar `127.0.0.1:8015`; em um navegador remoto,
esse endereço é a máquina do usuário. Depois do M15.2:

```text
navegador (Tailscale)
  http://IP-TAILSCALE:8765/painel-soprolife/
        │ mesma origem: /painel-soprolife/api/m15/...
        ▼
command-center-local-server.py (:8765)
        │ upstream fixo, HTTP loopback
        ▼
FastAPI M15 (127.0.0.1:8015 /api/v1/...)
        │ conexão PostgreSQL local
        ▼
PostgreSQL 16 (5432, não exposto)
```

Apps Script continua em `/painel-soprolife/api/command-center`; arquivos
estáticos e módulos históricos continuam no mesmo servidor. Marketing, SEO,
CRM, Financeiro legado, Documentos, Equipamentos, Auditoria e Automações não
são redirecionados pelo proxy M15.

## Contrato e proteções do proxy

- prefixo público: `/painel-soprolife/api/m15`;
- base privada padrão: `http://127.0.0.1:8015/api/v1`;
- métodos: somente `GET`, `POST` e `PATCH`, exatamente os métodos declarados
  pelos routers M15; busca de pessoas permanece `POST /pessoas/busca` com JSON;
- upstream opcional somente pela variável server-side
  `SOPROLIFE_M15_UPSTREAM`; aceita apenas `http` com endereço IP loopback,
  sem credenciais, query ou fragmento. Host, URL ou header enviados pelo
  cliente nunca escolhem o upstream;
- headers encaminhados: `Authorization`, `Content-Type`, `Idempotency-Key`,
  `X-Request-ID` válido (`[A-Za-z0-9._-]`, máximo 64) e `Accept`;
- `Cookie`, `Host` do cliente, `Connection`, `Proxy-*`, hop-by-hop e quaisquer
  outros headers não são encaminhados. O cliente HTTP cria apenas o `Host`
  protocolar do upstream validado;
- respostas preservam status, `Content-Type` e `X-Request-ID` válidos;
  conflitos `401`, `403`, `409` e `422` não são convertidos em sucesso;
- corpo de entrada: máximo 1 MiB; resposta: máximo 4 MiB;
- timeout de conexão: 3 s; timeout de resposta: 15 s;
- timeout gera `504`; recusa/indisponibilidade/resposta inválida ou excessiva
  geram `502`; configuração insegura gera `503`; corpo excessivo gera `413`;
- traversal, path codificado/ambíguo, barra invertida e caminho fora do
  prefixo são rejeitados ou resultam em `404`;
- log M15 contém somente operação genérica, método, status, request ID
  sanitizado e duração aproximada. Não contém URL, query, corpo ou headers.

O frontend produtivo usa apenas `/painel-soprolife/api/m15`. Para desenvolver
localmente, suba a API loopback e execute, na raiz do repositório:

```bash
python3 painel-soprolife/scripts/command-center-local-server.py
```

Não use `python3 -m http.server` para testar a interface M15: ele não possui o
proxy. A flag permanece `false`; ativação local continua sendo uma ação manual
e consciente conforme o README do núcleo.

## Serviço da API

O unit file versionado é
`painel-soprolife/systemd/soprolife-m15-api.service`. Ele usa:

- `User/Group=soprolife`;
- repositório somente leitura e escrita apenas em `nucleo-m15/var`;
- ambiente privado `/opt/soprolife/secrets/m15.env` (`root:soprolife`, `0640`);
- venv `/opt/soprolife/venvs/m15`;
- bind imposto em `127.0.0.1:8015` no próprio unit e novamente validado pela
  aplicação;
- restart apenas em falha, `UMask=0077`, `NoNewPrivileges`, `PrivateTmp`,
  filesystem/kernel/proc protegidos e famílias de sockets limitadas;
- dependência de `postgresql.service` e `network-online.target`.

`PrivateTmp` mantém uploads temporários privados. O diretório `var` permanece
gravável para relatórios/importações locais explicitamente executados; código,
fixtures e repositório são somente leitura para o serviço.

## Instalação manual futura

Pré-condições: código já atualizado em `/opt/soprolife/soprolife-site`, terminal
interativo na VPS, branch produtiva no commit aprovado e worktree limpo. O
script não faz pull/checkout e não recebe senha por argumento.

```bash
cd /opt/soprolife/soprolife-site
bash painel-soprolife/nucleo-m15/scripts/deploy-producao-vps.sh \
  COMMIT-APROVADO-DE-40-HEXADECIMAIS painel-soprolife-v01 100.87.98.100
```

O script confirma usuário/host/branch/commit/flag, pede a frase interativa,
cria backup antes de mutações, instala somente PostgreSQL 16/client/venv e
dependências apt decorrentes, cria role/banco dedicados, gera segredos sem
imprimi-los, instala estritamente `requirements.lock`, executa `pip check`,
`alembic upgrade/current/check`, instala/inicia o unit e testa API direta,
proxy, painel localhost e painel Tailscale. Ele também falha se API ou
PostgreSQL aparecerem expostos.

Reexecução é segura: pacotes/migrações/banco são idempotentes; segredos
existentes são validados e reutilizados para manter rollback consistente. Uma
nova cópia de backup é criada antes. Rotação é mudança separada. O deploy não
cria usuário, seed, demo ou importação e não muda Apps Script/planilhas. A flag
não é ativada.

## Atualização futura

1. mantenha `enabled=false` durante a janela;
2. atualize o repositório por processo Git previamente aprovado;
3. confirme branch, commit e worktree limpo;
4. execute o mesmo deploy com o novo commit esperado;
5. revise os healthchecks, listeners e journal;
6. só considere ativação futura em mudança separada e aprovada.

Não execute o script a partir de outro caminho e não edite o unit instalado
diretamente: altere a versão Git, revise e reinstale pelo procedimento.

## Operação e logs

```bash
sudo systemctl status soprolife-m15-api.service --no-pager --full
sudo journalctl -u soprolife-m15-api.service --since today --no-pager
sudo journalctl -u soprolife-painel.service --since today --no-pager
sudo ss -ltnp | grep -E ':(5432|8015|8765)\b'
curl --fail --silent http://127.0.0.1:8015/api/v1/health
curl --fail --silent http://127.0.0.1:8765/painel-soprolife/api/m15/health
```

Não aumente o nível de access log da API em produção: request lines podem
conter querystrings. Nunca cole token em `curl -v`, unit, journal ou arquivo.

## Backup

Backup manual privado e verificado:

```bash
bash /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15/scripts/backup-postgresql-m15.sh
```

O script usa `pg_dump -Fc`, valida com `pg_restore --list`, grava sob
`/opt/soprolife/backups/m15/manual` com acesso root e não apaga nada. Rotação é
inicialmente manual: inventarie data/tamanho, mantenha ao menos o último dump
pré-mudança aprovado e só remova um arquivo após autorização explícita. Teste
periodicamente a restauração em banco isolado; nunca sobre o banco produtivo.

## Rollback

Primeiro mecanismo visual: mantenha/retorne
`painel-soprolife/data/m15-config.json` para `enabled=false`. Depois:

```bash
sudo systemctl stop soprolife-m15-api.service
sudo journalctl -u soprolife-m15-api.service --since today --no-pager
cd /opt/soprolife/soprolife-site
git status --short
git switch painel-soprolife-v01
git reset --keep COMMIT-ANTERIOR-APROVADO
sudo systemctl restart soprolife-painel.service
```

`git reset --keep` acima é um comando futuro destrutivo: confirme commit e
worktree limpo antes de executá-lo. Alternativamente, faça um novo commit de
reversão pelo fluxo Git normal. Para rollback apenas do unit/proxy, restaure os
arquivos `.before` do diretório privado informado pelo deploy, rode
`systemctl daemon-reload` e reinicie somente o serviço afetado.

O banco **não é apagado automaticamente**. Se rollback de schema/dados for
realmente necessário, pare a API, crie outro dump, restaure o dump verificado
em banco novo e troque a conexão apenas após validação. Não use downgrade ou
drop improvisado em produção.

Em falha, o deploy para a API nova, preserva banco/backups, captura o journal e
indica este runbook. A investigação e o rollback são manuais para evitar perda.

## Riscos residuais

- porta 8765 continua sendo o perímetro do painel; acesso Tailscale e regras de
  host/firewall permanecem controles externos a este kit;
- o proxy não implementa rate limit próprio; autenticação/autorização e limite
  de login permanecem na API;
- rotação de backup é manual e restauração precisa ser ensaiada;
- ativação da interface e migração/importação de dados reais continuam fora do
  escopo e exigem aprovação humana separada.

Criação do primeiro administrador: `m15-2-primeiro-usuario.md`.
