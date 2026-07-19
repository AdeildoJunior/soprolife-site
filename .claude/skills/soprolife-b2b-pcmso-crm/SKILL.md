---
name: soprolife-b2b-pcmso-crm
description: Fluxo comercial B2B/PCMSO do painel SoproLife (Leads → CRM Clínicas → Contatos B2B) e os conceitos que já geraram confusão — origem vs etapa, lead ativo vs convertido, pessoa vs clínica.
---

# soprolife-b2b-pcmso-crm

## Objetivo
Documentar o fluxo comercial B2B/PCMSO do painel (Leads → CRM Clínicas → CRM Contatos B2B) e os conceitos que já causaram confusão real, para que qualquer mudança de lógica ou de texto respeite as mesmas regras.

## Quando usar
- Qualquer tarefa envolvendo Leads, CRM Clínicas, CRM Contatos B2B, funil comercial ou conversão de lead em parceiro/paciente.

## Quando não usar
- Custos & Investimentos / rateio entre sócios → usar `soprolife-finance-costs`.
- Mudança puramente visual sem tocar em lógica de negócio → usar `soprolife-ux-premium`.

## Arquivos e pastas relevantes
- `painel-soprolife/js/app.js` — funções `isLeadConvertido()`, `isB2BLead()`, constantes `LEAD_ETAPA_OPTIONS`, `LEAD_ETAPAS_TERMINAIS`.
- `painel-soprolife/data/leads*.json`, `painel-soprolife/data/crm-clinicas*.json`, `painel-soprolife/data/crm-contatos-b2b*.json`.
- `painel-soprolife/apps-script/converter-lead-em-paciente.gs`, `painel-soprolife/apps-script/organizar-leads-operacionais.gs`.

## Fluxo padrão (conceitual)
```
Leads → (conversão) → Leads Convertidos / Log de Conversões → CRM Clínicas (se B2B) → CRM Contatos B2B (pessoa vinculada à clínica)
```
Regras centrais:
- **Pessoa ≠ clínica**: um contato B2B é uma pessoa vinculada a uma clínica, não a clínica em si.
- **Origem ≠ etapa**: canal de aquisição (Google, indicação, site) não é a fase comercial do lead.
- **Lead ativo ≠ convertido**: `isLeadConvertido()` é a única fonte de verdade sobre o que conta como convertido — nunca reimplementar essa regra em outro lugar.
- **"Parceiro ativo"** sai da lista de "Ativos" e passa a aparecer em "Convertidos"/"Todos".
- O funil/label "Convertido" deve deixar claro que inclui pacientes, exames, consultas **e** parceria B2B — não é só "virou paciente".

## Comandos seguros
```
grep -n "isLeadConvertido\|isB2BLead\|LEAD_ETAPAS_TERMINAIS" painel-soprolife/js/app.js
node --check painel-soprolife/js/app.js
```

## Checks obrigatórios
- Qualquer nova lógica de "convertido" deve chamar `isLeadConvertido()`, nunca reimplementar a regra em outro lugar (evita divergência de números na mesma tela).
- Tooltips/labels de "Convertido" devem mencionar que inclui pacientes **e** parceria B2B.
- Operações de conversão devem ser idempotentes — rodar a mesma conversão duas vezes não deve duplicar registro.

## Proibições
- Não usar nomes ou dados reais de pacientes/leads como exemplo em documentação, skill ou commit — usar dado genérico ("Lead Exemplo", "Clínica Exemplo").
- O caso "Juan/Pastore" só pode ser citado como **referência conceitual de padrão de fluxo** (ex.: "um contato B2B convertido aparece como parceiro ativo"), nunca associado a dado sensível real.
- Não apagar log de conversões ou registros sem confirmar explicitamente com o usuário antes.

## Erros já observados
- O gráfico de funil contava "Convertido" com uma fórmula própria (soma de duas etapas específicas via `countLeadEtapa`), divergente de `isLeadConvertido()` — isso deixava de contar leads com etapa "Parceiro ativo", fazendo o número do gráfico ficar menor que o número real de convertidos mostrado em outro card na mesma tela. Corrigido trocando para a função canônica `isLeadConvertido()`.

## Exemplos de prompts
- "Explique por que esse lead não aparece como convertido."
- "Adicione uma nova etapa terminal ao funil B2B."
- "Confirme que a contagem de convertidos bate em todos os lugares da tela antes de eu aprovar."

## Comando de revisão após a tarefa
```
grep -n "isLeadConvertido" painel-soprolife/js/app.js | wc -l
```
Use esse comando para conferir os pontos onde a regra de conversão é usada — todos devem chamar a mesma função, nunca reimplementá-la.
