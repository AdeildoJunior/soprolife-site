# Conector WhatsApp Business — Painel SoproLife

Status: não implementado. Este arquivo é documentação de contrato.

## Objetivo

Contar mensagens por status (novas, respondidas, encerradas) e exportar somente contagens para o painel, sem armazenar conversas, números ou nomes de contatos.

## Dados permitidos exportar para o painel

- contagem de conversas por status (nova, em andamento, encerrada);
- contagem de mensagens recebidas no dia;
- contagem de mensagens pendentes de resposta;
- origem do contato quando disponível como dado agregado (ex: "Indicação", "Instagram").

## Dados proibidos de exportar

- número de telefone de qualquer contato;
- nome de contato;
- conteúdo de mensagem;
- histórico de conversa;
- qualquer dado que identifique o remetente.

## Opções de integração

| Opção | Observação |
|---|---|
| Meta Cloud API (oficial) | exige número verificado Meta Business |
| Z-API (intermediário) | webhook local, sem hospedagem pública necessária |
| Twilio WhatsApp | sandbox ou produção, custo por mensagem |

## Webhook local

O conector receberá eventos via webhook e os processará localmente. A credencial do webhook fica em `~/.config/soprolife/painel/whatsapp.local.json` e nunca entra no repositório.

## Configuração esperada

```json
{
  "provider": "z-api",
  "webhook_secret": "SEGREDO_DO_WEBHOOK_AQUI",
  "status_labels": ["nova", "em_andamento", "encerrada"]
}
```

Salvar em: `~/.config/soprolife/painel/whatsapp.local.json`
Permissão: `chmod 600 ~/.config/soprolife/painel/whatsapp.local.json`
