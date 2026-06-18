# Conector Gmail — Painel SoproLife

Status: não implementado. Este arquivo é documentação de contrato.

## Objetivo

Contar e-mails por label (ex: propostas enviadas, retornos pendentes, parcerias) e exportar somente contagens para o painel.

## Dados permitidos exportar para o painel

- contagem de mensagens por label;
- data do e-mail mais recente por label;
- contagem de não lidos por label.

## Dados proibidos de exportar

- assunto real de e-mail;
- remetente ou destinatário;
- corpo do e-mail;
- anexos;
- qualquer conteúdo identificável.

## Labels sugeridas para controle operacional

- `SoproLife/Proposta enviada`
- `SoproLife/Aguardando retorno`
- `SoproLife/Parceria ativa`
- `SoproLife/Pendente`

## Configuração esperada

Configuração real: manter somente em arquivo local privado fora do repositório.

Salvar em: `~/.config/soprolife/painel/gmail.local.json`
