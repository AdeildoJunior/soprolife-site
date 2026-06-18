# Conector Google Search Console — Painel SoproLife

Status: não implementado. Este arquivo é documentação de contrato.

## Objetivo

Exportar métricas agregadas de SEO do site SoproLife para o painel: cliques, impressões, CTR e posição média.

## Dados permitidos exportar para o painel

- total de cliques (período configurável);
- total de impressões;
- CTR médio;
- posição média;
- top 5 páginas por cliques (apenas URL relativa, sem query de usuário);
- top 5 termos de busca por cliques (termos genéricos, sem dados de usuário).

## Dados proibidos de exportar

- qualquer dado que identifique um usuário individual;
- IP de visitante;
- histórico de navegação.

## Configuração esperada

Configuração real: manter somente em arquivo local privado fora do repositório.

Salvar em: `~/.config/soprolife/painel/search-console.local.json`

## Referência

Documentação da API: https://developers.google.com/webmaster-tools/search-console-api-original/v3/searchanalytics
