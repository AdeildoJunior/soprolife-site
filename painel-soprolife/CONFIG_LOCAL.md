# Configuração local privada do Painel SoproLife

Este documento explica onde ficam configurações privadas locais que não devem ser servidas pelo painel nem enviadas ao GitHub.

## Configuração vigente

O painel usa M15/PostgreSQL e o servidor autenticado. Não copiar `.env`,
credenciais ou dados reais para worktrees de desenvolvimento. Configuração
privada existente deve ser preservada no ambiente de origem; não ler ou
reescrever segredos como parte de trabalho documental.

O servidor oficial bloqueia `data-private/`, código e arquivos internos mesmo
com sessão. Um servidor estático genérico ignora essa proteção. Os launchers
seguros e o fluxo local estão no [README](README.md).

Integrações futuras por marca terão configuração privada própria. A criação
documental de EEGLife não cria credencial, propriedade de analytics ou conexão.
Veja [arquitetura Life](../docs/CENTRO_COMANDO_LIFE_ARCHITECTURE.md).

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

## Importação CSV — procedimento histórico, fora do runtime

O procedimento abaixo é legado. Desde M23, o fluxo operacional normal gera
snapshots a partir do PostgreSQL; não executar importação de planilha para
atualizar o painel. Uso forense/migração exige escopo separado e explícito.
O registro histórico do procedimento é:

1. No Google Sheets, abra a aba **Resumo Dashboard**.
2. Exporte somente essa aba como CSV.
3. Salve o arquivo em:

~/.config/soprolife/painel/resumo-dashboard.csv

4. Rode:

painel-soprolife/scripts/import-summary-csv.sh

O script aceita somente indicadores agregados permitidos e bloqueia palavras associadas a dados sensíveis, como CPF, telefone, paciente, laudo e pedido médico.

O arquivo final usado pelo painel continua sendo local e ignorado pelo Git:

painel-soprolife/data/resumo-dashboard.local.json
