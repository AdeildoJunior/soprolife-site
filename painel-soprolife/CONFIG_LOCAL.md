# Configuração local privada do Painel SoproLife

Este documento explica onde ficam configurações privadas locais que não devem ser servidas pelo painel nem enviadas ao GitHub.

## Regra

Arquivos com URL, ID de planilha, tokens, senhas ou chaves não devem ficar dentro da pasta servida pelo painel.

Evitar guardar segredos em:

painel-soprolife/data-private/

Mesmo ignorada pelo Git, essa pasta pode ser servida localmente se o painel for aberto por um servidor estático.

## Local recomendado

Usar:

~/.config/soprolife/painel/

Exemplo:

~/.config/soprolife/painel/google-sheets.local.json

## Por quê?

Quando o painel é mostrado via Tailscale, o servidor estático pode permitir acesso a arquivos locais dentro da pasta do projeto.

Por isso, dados privados devem ficar fora do repositório e fora da pasta servida.

## O que nunca colocar no GitHub

- URL privada de planilha;
- ID real da planilha;
- token;
- chave de API;
- senha;
- CPF;
- telefone real de paciente;
- pedido médico;
- laudo;
- resultado de exame;
- dado clínico identificável.
