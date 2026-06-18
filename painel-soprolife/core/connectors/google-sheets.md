# Conector Google Sheets — Painel SoproLife

Status: não implementado. Este arquivo é documentação de contrato.

## Objetivo

Ler indicadores agregados de planilhas privadas do Google Workspace e gerar arquivos `*.local.json` seguros para o painel, sem expor dados identificáveis ou segredos.

## Pré-requisitos

1. Projeto no Google Cloud Console.
2. Google Sheets API v4 habilitada.
3. Conta de serviço criada.
4. Arquivo JSON da conta de serviço salvo em:
   `~/.config/soprolife/painel/service-account.json`
   Permissão: `chmod 600 ~/.config/soprolife/painel/service-account.json`
5. Planilha privada compartilhada com o e-mail da conta de serviço (somente leitura).

## O que o conector deve fazer

1. Ler somente a aba `Resumo Dashboard` da planilha privada.
2. Extrair somente indicadores numéricos agregados (lista em `schema-resumo.json`).
3. Bloquear exportação de qualquer campo proibido (CPF, telefone, nome de paciente, etc.).
4. Salvar resultado em `painel-soprolife/data/resumo-dashboard.local.json`.
5. Não salvar URL, ID de planilha nem token em nenhum arquivo dentro do repositório.

## O que o conector nunca deve fazer

- Ler abas que contenham dados individuais de pacientes.
- Exportar CPF, telefone, nome completo, pedido médico ou laudo.
- Salvar credenciais dentro da pasta do repositório.
- Commitar `*.local.json`.

## Configuração esperada em `~/.config/soprolife/painel/google-sheets.local.json`

Configuração real: manter somente em arquivo local privado fora do repositório.

Este arquivo fica fora do repositório e fora da pasta servida pelo painel.

## Referência de implementação

Biblioteca Python recomendada: `google-auth` + `google-api-python-client`
Documentação: https://developers.google.com/sheets/api/guides/concepts
