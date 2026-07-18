# M15.2 — primeiro usuário administrativo (procedimento manual)

Este procedimento é separado do deploy. Não execute até API, banco e backup
terem sido validados. O deploy **não cria usuário**.

## Criar

A senha é pedida por `getpass`, com eco desabilitado. Não use
`M15_NOVA_SENHA=...`, argumento, histórico, arquivo ou mensagem. Execute na VPS:

```bash
sudo -u soprolife bash -c '
  set -a
  source /opt/soprolife/secrets/m15.env
  set +a
  cd /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15
  exec /opt/soprolife/venvs/m15/bin/python -m app.cli criar-usuario \
    --email ADMIN-INTERNO --nome "NOME-INTERNO" --papel admin
'
```

Digite a senha forte somente no prompt oculto. O e-mail/nome são argumentos,
mas a senha nunca é. Não reutilize a senha do sistema, banco ou outro serviço.

## Emitir e usar token

```bash
sudo -u soprolife bash -c '
  set -a
  source /opt/soprolife/secrets/m15.env
  set +a
  cd /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15
  exec /opt/soprolife/venvs/m15/bin/python -m app.cli emitir-token \
    --email ADMIN-INTERNO
'
```

O token expira em 60 minutos na configuração produtiva e o frontend o mantém
somente na variável JavaScript da página. Recarregar fecha a sessão. A chave
legada `soproM15Token` é removida; `localStorage`/`sessionStorage` não guardam o
token.

O fluxo básico exibe o token uma vez. Use um terminal privado sem gravação de
sessão, cole diretamente no campo de senha do M15, apague a linha/scrollback
quando suportado e limpe imediatamente o clipboard. Não redirecione stdout,
não use `tee`, não salve em arquivo/notas e não envie por chat. Para evitar até
a passagem transitória pelo clipboard/terminal, um operador local pode
encaminhar stdout diretamente a uma ferramenta de digitação segura já
instalada, com o campo M15 previamente focado; não instale automação só para
isso e nunca passe o token como argumento do processo. Feche o terminal após o
uso.

Não use o token em URL, querystring, `curl -v`, histórico ou DevTools. O
servidor do painel encaminha `Authorization` sem registrá-lo.

## Revogar/inativar

Inativar invalida imediatamente tokens já emitidos porque toda requisição
confirma `users.ativo` no banco:

```bash
sudo -u soprolife bash -c '
  set -a
  source /opt/soprolife/secrets/m15.env
  set +a
  cd /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15
  exec /opt/soprolife/venvs/m15/bin/python -m app.cli desativar-usuario \
    --email ADMIN-INTERNO
'
```

Reativação consciente usa o mesmo formato com `ativar-usuario`. Depois de
reativar, emita um token novo; não tente reutilizar token antigo. Se houver
suspeita de comprometimento global, mantenha `enabled=false`, inative contas
afetadas e rotacione `M15_AUTH_SECRET` por procedimento de mudança — isso
revoga todos os tokens.
