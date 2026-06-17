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

## Importação segura do Resumo Dashboard por CSV

Para atualizar os indicadores locais do painel a partir da planilha privada:

1. No Google Sheets, abra a aba **Resumo Dashboard**.
2. Exporte somente essa aba como CSV.
3. Salve o arquivo em:

~/.config/soprolife/painel/resumo-dashboard.csv

4. Rode:

painel-soprolife/scripts/import-summary-csv.sh

O script aceita somente indicadores agregados permitidos e bloqueia palavras associadas a dados sensíveis, como CPF, telefone, paciente, laudo e pedido médico.

O arquivo final usado pelo painel continua sendo local e ignorado pelo Git:

painel-soprolife/data/resumo-dashboard.local.json
