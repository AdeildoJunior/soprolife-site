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

## Aprendizado — Google ADC, quota project e dados automáticos do painel

Quando o painel SoproLife não atualizar Google Sheets, Search Console ou GA4 e aparecer erro de reautenticação/quota:

1. Diferenciar duas autenticações:
   - `gcloud auth application-default login` cria/renova o ADC usado pelos scripts Python.
   - `gcloud auth login` autentica o CLI do gcloud para listar projetos, habilitar APIs e configurar quota project.

2. ADC válido não basta se o projeto ativo estiver `(unset)` ou se aparecer:
   - `requires a quota project`
   - `Cannot find a quota project to add to ADC`
   - `The sheets.googleapis.com API requires a quota project`
   - `The searchconsole.googleapis.com API requires a quota project`

3. Fluxo correto:
   - `gcloud auth login --no-launch-browser`
   - `gcloud projects list`
   - `gcloud config set project <PROJECT_ID>`
   - habilitar APIs necessárias:
     - `serviceusage.googleapis.com`
     - `sheets.googleapis.com`
     - `searchconsole.googleapis.com`
     - `analyticsdata.googleapis.com`
   - `gcloud auth application-default set-quota-project <PROJECT_ID>`

4. Depois testar com:
   - `gcloud auth application-default print-access-token`
   - `bash painel-soprolife/scripts/update-local-data.sh`
   - procurar erros com `grep` por `Reauthentication`, `quota`, `permission`, `acesso negado`, `Search Console`, `GA4`.

5. Indicadores de sucesso:
   - painel mostra `Dados reais`
   - painel mostra `Search Console`
   - painel mostra `GA4`
   - `marketing-seo.local.json seguro — configured=True, SC=True, GA4=True`
   - `soprolife-update-data.service` termina com `status=0/SUCCESS`.

6. Segurança:
   - nunca gravar token, URL secreta, client secret ou credential no frontend;
   - configs privadas ficam em `painel-soprolife/data-private/`;
   - resumos exibidos no painel devem ser agregados e seguros.

## Aprendizado — fórmulas Google Sheets, Apps Script e localidade

Ao criar fórmulas em Google Sheets via Apps Script, não presumir que a localidade visual da planilha define o idioma das funções.

Caso apareça:
- `#ERROR!`
- `#NAME?`
- `Função desconhecida: SE`

Testar combinações entre:
- funções em inglês: `IF`, `SUM`, `COUNTA`;
- separador `;` em vez de `,`.

Na planilha SoproLife/Pastore, a combinação validada foi:
- funções em inglês;
- separador `;`.

Exemplos validados:
- `=IF(K2="";"";K2)`
- `=IF(COUNTA(N2:R2)=0;"";SUM(N2:R2))`
- `=IF(S2="";"";S2-IF(T2="";0;T2))`

Para summaries servidos pelo painel:
- arquivos privados em `data-private/` devem ficar protegidos, preferencialmente `600`;
- summaries seguros em `painel-soprolife/data/*.local.json`, quando precisam ser lidos pelo navegador, devem estar legíveis pelo serviço web, normalmente `644`;
- se a VPS gera o JSON certo mas o navegador não atualiza, testar o JSON por HTTP com `curl`.

Regra de teste:
usar linha fictícia evidente, como `TESTE - APAGAR`, validar o fluxo completo e remover a linha após confirmar no painel.
