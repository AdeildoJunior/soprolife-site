---
name: soprolife-sheets-sync
description: Integração segura entre Google Sheets, Apps Script, ADC e os JSONs locais do painel (privado e summary) — inspecionar antes de automatizar, usar dry-run antes de escrever, e nunca vazar dado pessoal no summary público.
---

# soprolife-sheets-sync

## Objetivo
Padronizar a integração entre a planilha real do Google Sheets, Apps Script, ADC (Application Default Credentials) e os JSONs que alimentam o painel — tanto o privado (`data-private/`) quanto o summary seguro (`data/*-summary.local.json`).

## Quando usar
- Ler ou escrever na planilha real.
- Gerar ou atualizar um summary a partir de dados do Sheets.
- Mexer em Apps Script (`.gs`) ou em scripts de leitura (`read-*-adc.py`, `generate-*.py`).

## Quando não usar
- Tarefas puramente visuais no painel que já têm dado local suficiente.
- Editar a planilha real sem ADC configurado e sem autorização explícita do usuário.

## Arquivos e pastas relevantes
- `painel-soprolife/scripts/read-*-adc.py` — leitura via ADC (Google API).
- `painel-soprolife/scripts/generate-*.py` — geradores de resumo/local a partir de dados já lidos.
- `painel-soprolife/apps-script/*.gs` — templates seguros, **sem** URL ou ID real da planilha.
- `painel-soprolife/data-private/*.local.json` — saída privada (nome, telefone, etc.), nunca commitada.
- `painel-soprolife/data/*-summary.local.json` — saída segura pro painel (agregados, sem PII), também gitignored por convenção mesmo sendo "segura".
- `painel-soprolife/data-private/command-center-config.local.json` — token real do Apps Script, nunca versionado.

## Fluxo padrão
1. Identificar a cadeia real: **Planilha (Sheets)** → Apps Script (só para escrita) → ADC (leitura via API) → `data-private/` (saída privada) → `data/*-summary.local.json` (saída segura pro painel).
2. Antes de automatizar qualquer coisa, **inspecionar os cabeçalhos** da aba real (nome exato das colunas, ordem).
3. Rodar em **modo leitura/dry-run** antes de qualquer escrita real, sempre que o script suportar.
4. Gerar o JSON privado primeiro, validar visualmente, e só depois gerar o summary seguro (sem PII).
5. Validar que o summary não tem nome, telefone, CPF, endereço ou dado clínico antes de considerar pronto.
6. Só então atualizar/testar o painel local com o novo dado.

## Comandos seguros
```
python3 painel-soprolife/scripts/read-sheets-summary-dry-run.py
python3 -m json.tool painel-soprolife/data/<arquivo>-summary.local.json
grep -inE "cpf|telefone|pix|cnpj" painel-soprolife/data/<arquivo>-summary.local.json
```
Antes de rodar qualquer script com `--write` ou equivalente, ler o código do script primeiro para confirmar que ele não escreve em produção sem confirmação.

## Checks obrigatórios
- Cabeçalho da aba real confere com o que o script espera (nomes e ordem das colunas).
- Dry-run OK antes de qualquer flag de escrita real.
- Summary final sem PII — rodar o grep de sanity check acima.
- Reprocessar o mesmo dado não deve duplicar linhas/registros (idempotência).

## Proibições
- Não expor URL do Apps Script real ou token no frontend/`app.js` — o app.js só fala com o proxy local.
- Não commitar `application_default_credentials.json` (ADC).
- Não tratar um log de conversões como fonte primária de verdade sem deduplicar primeiro.
- Não expor nome, telefone ou dado clínico em summary público — só agregados.
- Não inserir CNPJ, chave Pix ou ID de transação real em `data-private/` sem necessidade genuína de auditoria.

## Erros já observados
- **ADC expirado confundido com "senha errada"** — o sintoma parece um problema de autenticação simples, mas geralmente precisa de `gcloud auth application-default login` de novo, não é questão de digitar senha certa. Diagnosticar a mensagem de erro exata antes de insistir.
- **Apps Script responde "ok" mas a planilha não muda** — geralmente é deployment antigo (versão do Web App não republicada), URL de deployment errada, ID de projeto errado, ou a função errada sendo chamada. Conferir a versão do deployment antes de insistir em "tentar de novo".

## Exemplos de prompts
- "Gere o summary de Custos & Investimentos a partir da planilha, mas primeiro me mostra os headers."
- "Rode em dry-run antes de escrever de verdade."
- "Confirma que o novo summary não tem telefone nem nome antes de eu aprovar."

## Comando de revisão após a tarefa
```
python3 -m json.tool painel-soprolife/data/<arquivo>-summary.local.json > /dev/null && echo "JSON válido"
```
