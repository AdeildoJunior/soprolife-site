---
name: soprolife-finance-costs
description: Regras de cálculo e exibição de Custos & Investimentos / rateio entre sócios no painel SoproLife — nunca confundir responsável com pagador, nunca inventar valor, sempre usar campos estruturados em vez de texto livre.
---

# soprolife-finance-costs

## Objetivo
Evitar os erros já cometidos em Custos & Investimentos: confundir "responsável" com "quem pagou", tratar desconto como desembolso, inferir rateio por texto livre, e mostrar `R$ 0,00` como se fosse dado real quando na verdade é desconhecido.

## Quando usar
- Qualquer tarefa em Custos & Investimentos, rateio entre sócios, pagamentos ou parcelas.

## Quando não usar
- Leads/CRM B2B → usar `soprolife-b2b-pcmso-crm`.
- Mudança puramente visual sem tocar em número/cálculo → usar `soprolife-panel-ui-ux`.

## Arquivos e pastas relevantes
- `painel-soprolife/data-private/custos-investimentos.local.json` — fonte privada detalhada.
- `painel-soprolife/data/custos-investimentos-summary.local.json` — resumo seguro que o painel realmente lê.
- Seção "Custos & Investimentos" em `painel-soprolife/js/app.js` (`buildSociosPane`, `renderCiSociosCharts`, `getRateioSocios`, `getRateioItens`).

## Fluxo padrão
1. Ler o JSON privado **e** o summary antes de mudar qualquer cálculo.
2. Confirmar se já existem os campos estruturados de rateio (`rateio_socios`, `rateio_itens`) — **nunca** inferir pagador pelo campo `responsavel` nem por texto de `observacao`.
3. Separar sempre os campos:
   - `valor_total`
   - `valor_mensal`
   - `parcelas_total`
   - `parcelas_pagas`
   - `pago_adeildo`
   - `pago_faustino`
   - `pendente_adeildo`
   - `pendente_faustino`
   - `pendente_sem_pagador`
   - `desconto_ou_baixa_sem_saida_caixa`
   - `observacao_curta`
4. Se um valor é desconhecido, usar `null` no JSON e "—" na UI — nunca `R$ 0,00` inventado.
5. Somar manualmente os itens e conferir que bate com o agregado por sócio antes de considerar pronto.
6. Rodar `check-access.sh` para confirmar que o summary não tem dado sensível.

## Comandos seguros
```
python3 -m json.tool painel-soprolife/data/custos-investimentos-summary.local.json
grep -n "rateio_socios\|rateio_itens" painel-soprolife/js/app.js
bash painel-soprolife/scripts/check-access.sh
```

## Checks obrigatórios
- Todo valor `pago_X`/`pendente_X` vem de um campo estruturado — nunca de regex sobre `responsavel` ou parsing de `observacao`.
- Soma dos itens (`rateio_itens`) bate com o agregado por sócio (`rateio_socios`).
- Nenhum CPF, CNPJ, chave Pix, dado bancário, comprovante ou ID de pagamento no summary público.

## Proibições
- Não confundir "responsável" (quem administra/é o dono do item) com "quem pagou de fato" (desembolso real).
- Não tratar desconto ou baixa como se fosse dinheiro efetivamente pago — usar o campo próprio `desconto_ou_baixa_sem_saida_caixa`.
- Não calcular rateio fazendo parsing de texto livre de `observacao`.
- Não inventar um valor numérico quando o dado real é desconhecido.
- Não expor CPF, CNPJ, Pix, dados bancários, comprovante ou ID de pagamento em nenhum arquivo `data/` (mesmo os "summary" e mesmo sendo gitignored — o hábito importa).

## Erros já observados
1. **Inferência por `responsavel` quebrou em item de responsabilidade mista.** Uma versão inicial do cálculo tentava adivinhar quem pagou usando o texto do campo `responsavel` — funcionava para itens de um único responsável, mas quebrava (mostrando "—" ou `R$ 0,00` errado) em itens pagos por duas pessoas diferentes. A correção foi parar de inferir e criar campos estruturados explícitos (`pago_adeildo`, `pago_faustino`, etc.).
2. **`app.js` esperava `rateio_socios`/`rateio_itens`, mas o JSON da VPS não tinha essas estruturas.** O código novo foi enviado antes do dado correspondente ser sincronizado — o painel na VPS mostrou números quebrados/zerados até o `.local.json` da VPS ser atualizado manualmente. **Sempre validar o JSON local E o JSON da VPS** antes de considerar um deploy financeiro completo (ver `soprolife-vps-deploy-safe`).

## Exemplos de prompts
- "Adicione um novo custo ao rateio sem inventar quem pagou."
- "Confira se o resumo público de custos não vaza nome nem CPF."
- "Antes de mudar o cálculo, me mostra os campos que já existem no JSON."

## Comando de revisão após a tarefa
```
python3 -c "import json; d=json.load(open('painel-soprolife/data/custos-investimentos-summary.local.json')); print('rateio_socios' in d, 'rateio_itens' in d)"
```

## Aprendizado — Parcerias com resultado líquido

Para parceria operacional como Pastore:
- receita bruta vem do valor cobrado;
- custo total soma repasse, insumo, deslocamento, profissional e outros custos;
- resultado líquido = receita bruta - custo total;
- o painel deve mostrar agregados, nunca paciente/telefone/observação.

Validação usada:
- valor cobrado: 150;
- custo insumo: 10;
- resultado esperado: 140;
- teste sempre com paciente fictício `TESTE - APAGAR`.
