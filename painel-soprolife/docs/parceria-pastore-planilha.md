# Estrutura de Planilha — Parceria Pastore

Este documento define o modelo de Google Sheets que vai alimentar o módulo **Parcerias → Pastore**
do Painel SoproLife. É a etapa anterior a qualquer automação (Apps Script) ou botão no painel —
primeiro a estrutura de dados, depois a leitura, só depois a escrita.

Contexto operacional (ver também `soprolife-b2b-pcmso-crm` para o vocabulário do funil comercial):
- Unidade: **Pastore Ipanema**.
- Agenda: **terças-feiras e sábados, 08h às 12h**.
- A Pastore gera a demanda; a SoproLife executa os exames na unidade.
- Serviço: espirometria com ou sem broncodilatador.
- Futuramente pode haver expansão para outras unidades, dias e horários — por isso os campos
  `unidade`, `dia_semana` e `horario_*` existem em todas as abas, mesmo com um único valor hoje.

## Regra principal (a mesma de `PLANILHAS_PRIVADAS.md`)

A planilha real fica **fora do GitHub**. O repositório contém apenas:
- modelos vazios (só cabeçalho) em `painel-soprolife/templates/`;
- esta documentação;
- exemplos fictícios, se necessário — nunca dado real de paciente.

Nunca commitar: nome/telefone real de paciente, CPF, valor cobrado individual, observação clínica,
comprovante, chave Pix, token ou credencial.

## As três abas

### 1. Parceria Pastore - Atendimentos

Uma linha por atendimento/exame realizado (ou tentado) via parceria Pastore.
Template: `painel-soprolife/templates/parceria-pastore-atendimentos-template.csv`

| Campo | Privacidade | Observação |
|---|---|---|
| `data_atendimento` | Agregável | Vira "produção por data" no summary |
| `unidade` | Agregável | Hoje sempre "Pastore Ipanema" |
| `dia_semana` | Agregável | Terça-feira / Sábado |
| `horario_inicio` / `horario_fim` | Agregável | Janela do turno (08h/12h hoje) |
| `origem` | Agregável | Sempre "Pastore" nesta planilha — existe para permitir reaproveitar o mesmo template em outras parcerias no futuro |
| `paciente_nome` | **Privado — nunca sai da planilha/`data-private/`** | Nome completo ou como for identificado |
| `paciente_whatsapp` | **Privado — nunca sai da planilha/`data-private/`** | Mesmo tratamento do campo `telefone_whatsapp` de Leads |
| `tipo_exame` | Agregável | Espirometria (rótulo livre, mas recomenda-se padronizar) |
| `broncodilatador` | Agregável | Sim / Não — vira `distribuicao_tipo_exame` no summary |
| `valor_cobrado` | **Privado por linha** — só agregado (soma) pode virar summary | Valor do exame específico |
| `forma_pagamento` | **Privado por linha** — agregável por categoria (Pix, cartão, dinheiro, faturado) | Não expor forma de pagamento vinculada a um paciente específico |
| `recebido_por` | Interno (não é PII de paciente) — pode virar agregado nomeado (ex.: total recebido por pessoa da equipe), nunca inferido por texto livre | Quem recebeu o valor do exame — mesma lógica de `pago_por` em Custos & Investimentos: campo estruturado, não `responsavel` |
| `repasse_pastore` | **Privado por linha** — só soma no summary | Valor ou percentual repassado à Pastore neste atendimento |
| `custo_insumo` | **Privado por linha** — só soma no summary | Custo de insumo daquele exame |
| `custo_deslocamento` | **Privado por linha** — só soma no summary | Custo de deslocamento do profissional |
| `custo_profissional` | **Privado por linha** — só soma no summary | Custo do profissional que executou o exame |
| `outros_custos` | **Privado por linha** — só soma no summary | Qualquer custo residual |
| `receita_bruta` | Calculado — ver "Cálculos automáticos" | Não preencher manualmente se possível |
| `custo_total` | Calculado | Não preencher manualmente se possível |
| `resultado_liquido` | Calculado | Não preencher manualmente se possível |
| `status` | Agregável | Ex.: Realizado, Cancelado, Reagendado, Não compareceu |
| `followup_status` | Agregável em contagem — nunca por paciente | Ex.: pendente, em dia, concluído |
| `consentimento_contato_futuro` | **Privado — nunca por linha, só contagem agregada** | Sim / Não / Não perguntado |
| `observacao_privada_minima` | **Sempre privado, nunca resumido** | Texto livre curto — nunca CPF, laudo, diagnóstico ou pedido médico (mesma regra de Leads/CRM Pacientes) |

Dropdowns sugeridos:
- `status`: Realizado, Cancelado, Reagendado, Não compareceu
- `followup_status`: pendente, em dia, concluído, não se aplica
- `broncodilatador`: Sim, Não
- `consentimento_contato_futuro`: Sim, Não, Não perguntado

### 2. Parceria Pastore - Custos

Uma linha por custo operacional da parceria que não está amarrado a um atendimento específico
(ex.: custo fixo mensal, deslocamento de um dia inteiro, compra de insumo em lote).
Template: `painel-soprolife/templates/parceria-pastore-custos-template.csv`

| Campo | Privacidade | Observação |
|---|---|---|
| `data` | Agregável | Data do custo |
| `unidade` | Agregável | Pastore Ipanema |
| `tipo_custo` | Agregável | Insumo, Deslocamento, Profissional, Outro |
| `descricao` | Interno — recomenda-se manter fora do summary público | Texto livre curto |
| `valor` | **Privado por linha** — só soma/categoria no summary | Nunca expor linha a linha publicamente |
| `pago_por` | Interno — campo estruturado, nunca inferido por texto livre (mesma regra do `soprolife-finance-costs`: nunca confundir "responsável" com "quem pagou") | Quem desembolsou de fato |
| `recorrente` | Agregável | Sim / Não |
| `observacao_privada_minima` | **Sempre privado** | Texto livre curto, sem dado sensível |

### 3. Parceria Pastore - Config

Uma linha por combinação unidade + dia da semana (ou uma linha por unidade, se o horário for igual
em todos os dias). É a fonte de verdade da agenda e dos parâmetros comerciais.
Template: `painel-soprolife/templates/parceria-pastore-config-template.csv`

| Campo | Privacidade | Observação |
|---|---|---|
| `unidade` | Público (já aparece no painel) | Pastore Ipanema |
| `status` | Público | planejada / ativa / pausada |
| `dia_semana` | Público | Terça-feira, Sábado |
| `horario_inicio` / `horario_fim` | Público | 08h / 12h |
| `capacidade_estimada_por_turno` | Público quando definido | Usado para calcular ocupação da agenda — hoje "A definir" |
| `valor_exame_sem_bd` | **Sensível comercial — manter privado até decisão** | Preço acordado com a Pastore |
| `valor_exame_com_bd` | **Sensível comercial — manter privado até decisão** | Preço acordado com a Pastore |
| `repasse_pastore_tipo` | **Sensível comercial** | "percentual" ou "valor_fixo" |
| `repasse_pastore_valor` | **Sensível comercial** | Número correspondente ao tipo acima |
| `custo_insumo_padrao` | **Sensível comercial** | Custo padrão por exame, quando não há custo real lançado |
| `custo_deslocamento_padrao` | **Sensível comercial** | Idem |
| `custo_profissional_padrao` | **Sensível comercial** | Idem |
| `observacao` | Interno | Texto livre curto |

> "Sensível comercial" aqui não é dado pessoal de paciente — é informação de acordo comercial com a
> Pastore. Enquanto não houver decisão explícita de exibi-la (mesmo que só para os sócios, dentro do
> painel local via Tailscale), tratar como privada, na mesma lógica de "nunca inventar valor" do
> `soprolife-finance-costs`.

## O que pode virar summary público (`data/parcerias-pastore-summary.local.json` / `.json`)

Só agregados, nunca linha a linha e nunca com nome/telefone:
- contagens (`exames_realizados`, `total_atendidos`, `followup_pendente`, `recorrentes`)
- somas (`receita_estimada`, `custo_total_periodo`, `resultado_liquido_estimado`)
- percentuais calculados (`ocupacao_agenda_pct`, `margem_estimada_pct`)
- séries agregadas por data/período (`producao_por_data`, `financeiro_por_periodo`)
- a própria Config (agenda, status) — não é PII e já é exibida hoje

Nunca no summary: `paciente_nome`, `paciente_whatsapp`, `observacao_privada_minima`,
`valor_cobrado` linha a linha, `consentimento_contato_futuro` linha a linha.

## Cálculos automáticos propostos (a serem feitos no gerador, nunca no navegador)

Estes cálculos devem rodar num script gerador (ex.: `generate-parceria-pastore-summary.py`, a ser
criado numa etapa futura — ver `soprolife-sheets-sync`), lendo a planilha privada via ADC e
escrevendo primeiro `data-private/parcerias-pastore.local.json` (detalhado) e só depois o summary
seguro. Nunca calcular a partir de dado bruto com PII diretamente no `app.js`.

Por linha (aba Atendimentos), quando não vier já preenchido:
- `receita_bruta` = `valor_cobrado`
- `custo_total` = `custo_insumo` + `custo_deslocamento` + `custo_profissional` + `outros_custos` + (`repasse_pastore`, se tratado como custo — ver pergunta em aberto nº 2)
- `resultado_liquido` = `receita_bruta` − `custo_total`

Agregado (para o summary), sempre filtrando `status = "Realizado"` (nunca somar linha cancelada/reagendada como se fosse produção real):
- `exames_realizados` = contagem de linhas com `status = "Realizado"` no período
- `receita_estimada` = soma de `receita_bruta` das linhas realizadas
- `resultado_liquido_estimado` = soma de `resultado_liquido` das linhas realizadas
- `margem_estimada_pct` = `resultado_liquido_estimado / receita_estimada * 100`, só se `receita_estimada > 0` — senão `null` ("A definir")
- `ocupacao_agenda_pct` = `exames_realizados_no_período / (capacidade_estimada_por_turno × nº de turnos no período) * 100` — só se `capacidade_estimada_por_turno` estiver definida na Config; senão `null`
- `producao_por_data` = contagem de exames realizados agrupada por `data_atendimento`
- `financeiro_por_periodo` = soma de receita/custo/resultado agrupada por semana ou mês
- `pacientes_pastore.total_atendidos` = pacientes **distintos** para fins de AGREGADO estatístico do summary (aproximação por telefone normalizado). ATENÇÃO (M14.3A): telefone/nome NUNCA é prova de identidade — no modelo canônico o vínculo de pessoa é por `paciente_id` com decisão humana; a antiga regra do sincronizador foi BLOQUEADA.
- `pacientes_pastore.recorrentes` = pacientes distintos com mais de um atendimento
- `pacientes_pastore.followup_pendente` = contagem de `followup_status = "pendente"`
- `pacientes_pastore.distribuicao_tipo_exame` = contagem por `broncodilatador` (sem/com)

## KPIs que devem aparecer no painel

O painel já tem exatamente 4 KPIs no topo da seção (visual limpo, sem poluir — ver
`soprolife-panel-ui-ux`); este modelo de dados só precisa **alimentar** os que já existem, não
criar novos cards soltos:
1. **Exames realizados** — `kpis.exames_realizados`
2. **Receita estimada** — `kpis.receita_estimada` ("—" se ainda não houver produção)
3. **Resultado líquido estimado** — `kpis.resultado_liquido_estimado` ("—" se ainda não houver produção)
4. **Ocupação da agenda** — `kpis.ocupacao_agenda_pct` (0% até haver capacidade definida e produção)

Nas abas internas (Agenda / Financeiro / Pacientes), manter os blocos já existentes no painel —
este documento não propõe nenhum indicador novo além do que já está implementado.

## O que ainda precisa ser decidido com a Pastore

1. **Tipo de repasse**: percentual sobre o valor cobrado, valor fixo por exame, ou valor fixo por turno/mês?
2. **O repasse é um desconto na receita da SoproLife ou um custo pago à parte?** Isso muda a fórmula de `custo_total`/`resultado_liquido` acima.
3. **Quem emite cobrança/nota ao paciente ou à empresa** — SoproLife ou Pastore? Define o fluxo real de `recebido_por` e `forma_pagamento`.
4. **Capacidade real por turno** (quantos exames cabem nas 4h de terça/sábado) — essencial para `ocupacao_agenda_pct` deixar de ser "A definir".
5. **Formas de pagamento aceitas** (Pix, cartão, dinheiro, faturamento mensal consolidado).
6. **Política de cancelamento/não comparecimento** — gera custo ou reembolso?
7. **Expansão futura**: outras unidades/dias/horários já têm previsão, ou é só uma possibilidade em aberto? Isso define se a Config já nasce preparada para múltiplas linhas.
8. **Variação de preço por convênio/particular** — o modelo atual assume um valor fixo sem BD e um com BD, sem diferenciação por convênio.

## Próxima etapa: do Sheets para o painel escrever direto

Ordem recomendada (mesma lógica de `PLANILHAS_PRIVADAS.md` → "Integração futura" e da skill
`soprolife-sheets-sync`), **cada etapa só começa depois da anterior estar validada**:

1. Criar a planilha real no Google Sheets com as 3 abas, usando os cabeçalhos exatos destes templates.
2. Script de leitura via ADC (`read-parceria-pastore-adc.py`), em modo dry-run primeiro — só leitura, sem gravar nada.
3. Gerador de summary (`generate-parceria-pastore-summary.py`) aplicando os cálculos acima, escrevendo primeiro `data-private/parcerias-pastore.local.json` e, validado visualmente, o summary seguro.
4. Rodar o grep de sanity check (nome, telefone, CPF, Pix) no summary antes de considerar pronto.
5. Testar o painel local com o novo dado (o fallback em 3 níveis já implementado continua funcionando: `.local.json` → `.json` committável → objeto interno).
6. Sincronizar a VPS (mesma cadeia do `update-local-data.sh` / `soprolife-update-data.service`).
7. **Só então**, se for necessário registrar atendimento direto pela UI do painel (escrita, não só leitura), criar um Apps Script Web App dedicado seguindo o mesmo padrão de proxy local do Command Center — token nunca chega ao browser. Isso é uma etapa avançada e exige autorização explícita antes de qualquer mudança em Apps Script ou produção.

Nada disso foi implementado nesta tarefa — apenas o modelo de dados e a documentação.
