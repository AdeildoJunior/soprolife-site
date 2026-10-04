# M25.22 — Verificação dirigida: financeiro real dos atendimentos

**Data:** 2026-08-11
**Natureza:** verificação **somente leitura**. Nenhum arquivo de código alterado,
nenhum paciente/exame/lançamento/fechamento criado, nenhuma migration executada,
nenhum deploy, nenhum status alterado.
**Branch local de trabalho:** `claude-m25-22-verificacao-financeiro-integrado`
**Branch oficial:** `painel-soprolife-v01`
**VPS:** `root@soprolife-painel-01` — `/opt/soprolife/soprolife-site`, HEAD `075528b`,
working tree limpo, mesmo commit da branch oficial local.
**Banco lido:** `soprolife_m15` (PostgreSQL na VPS), acesso via `su - postgres`,
exclusivamente `SELECT`.
**PII:** nenhuma coluna de nome, telefone, CPF, endereço residencial ou dado
clínico foi projetada em nenhuma consulta. A identificação é por código
institucional (`ESP-…`, `LAN-…`, `CLI-…`, `UNI-…`, `PAR-…`, `CON-…`).

---

## VEREDITO

| domínio | veredito |
| --- | --- |
| **SOPROLIFE** | **PARCIAL** |
| **PASTORE** | **PARCIAL** |

Os dois vereditos são "parcial" por razões **opostas**, e essa diferença é o
ponto central deste relatório:

- **SoproLife é parcial por cobertura.** O caminho automático existe, está
  correto e está provado em produção — mas ele é **opcional**, e existe um
  segundo caminho de criação de exame que não passa pelo financeiro.
- **Pastore é parcial por ausência de regra monetária.** A estrutura de
  fechamento está completa e a proteção contra receita indevida está provada
  como **eficaz** (0 violações) — mas **nenhuma competência jamais foi fechada**
  e **não existe, em lugar nenhum do sistema, um valor esperado**. Em dinheiro
  reconhecido, a Pastore está em **R$ 0,00** desde o primeiro exame.

Detalhamento por eixo, no fim do documento (seção 7).

---

# 1. O CONTRATO ATUAL — provado no código

## A) Espirometria SoproLife

### Onde o valor é recebido no "Novo atendimento"

**Interface** — `painel-soprolife/js/central-cadastros.js:911-913`:

```js
${fld("Valor da espirometria (R$)", inp("esp_valor", "",
  'inputmode="decimal" placeholder="220,00"'),
  { span: 4, help: "Opcional. Nenhum valor é inferido — em branco, nenhum lançamento." })}
```

Acompanhado de `esp_pgto_status` (Recebido/Pendente/Parcial/Cortesia),
`esp_pgto_data` e `esp_pgto_forma` (Pix/Dinheiro/Cartão/Outro).

O payload é montado em `montarFinanceiro()` —
`painel-soprolife/js/central-cadastros.js:1208-1226`. Regras impostas **no
cliente**:

- se `esp_valor` estiver vazio, `financeiro.espirometria` **não é enviado** e
  nenhum lançamento nasce;
- `data_competencia` recebe a **data do exame** (`esp_data`), não a data de hoje;
- status "Recebido" sem data de recebimento é recusado antes do envio.

**Contrato de API** — `AtendimentoFinanceiroExame`,
`nucleo-m15/app/schemas.py:601-609`: `valor: Money` é **obrigatório dentro do
bloco**; o bloco inteiro é que é opcional. Não existe valor default, tabela de
preço nem inferência.

### Quando o `FinancialEntry` é criado

Numa **única transação atômica**, dentro do `POST /atendimentos` —
`nucleo-m15/app/routers/attendances.py:283-305`. A atomicidade é explícita
(`attendances.py:110-121`): qualquer falha em qualquer etapa faz `db.rollback()`
e não sobrevive paciente, exame, consulta nem lançamento parcial.

### Vínculo ESP → LAN

Chave estrangeira técnica `financial_entries.spirometry_exam_id →
spirometry_exams.id` (`nucleo-m15/app/models.py:748-750`), gravada em
`attendances.py:303` (`spirometry_exam_id=exam.id`).

O vínculo é **exclusivamente técnico**. `FinancialEntry` está documentado como
*"Fonte financeira canônica. Vinculada só por IDs técnicos — sem PII"*
(`models.py:727-728`). O nome do paciente nunca é gravado no lançamento; quando
a tela precisa exibi-lo, ele é lido da pessoa vinculada em tempo de exibição
(`nucleo-m15/app/routers/finance.py:_entry_context`, linhas 85-118).

### Categoria

`CATEGORIA_ESPIROMETRIA = "Espirometria"` —
`nucleo-m15/app/finance_categories.py:39`, aplicada em `attendances.py:291`.

A categoria tem um contrato de normalização próprio (M23.1,
`finance_categories.py`): a **chave de comparação** usa NFKC + remoção de
invisíveis + colapso de espaços + `casefold`, e a **forma persistida** é a grafia
canônica exata. Isso é o que impede `"espirometria"`, `"ESPIROMETRIA"`,
`" Espirometria "` e a categoria ausente de virarem categorias diferentes e
contornarem o bloqueio de duplicidade.

### Receita

`tipo="receita"` — `attendances.py:289`.

Descrição gerada pelo sistema, sem PII: `f"Espirometria {exam.public_code}"`
(`attendances.py:292`).

### Status

Vem do operador: `status=bloco.status` (`attendances.py:294`), default `"Recebido"`
no schema (`schemas.py:603`). Domínio fechado —
`StatusPagamento = Literal["Recebido","Pendente","Parcial","Cortesia","Cancelado"]`
(`schemas.py:41`).

### Competência

`data_competencia` passa por `parse_incomplete_date()` e grava **quatro** colunas
(`attendances.py:_apply_date`, linhas 61-66):
`data_competencia`, `_original`, `_precisao`, `_dia_assumido`.

Data parcial nunca vira data exata: o metadado de precisão acompanha o registro.

### Forma de pagamento

`forma_pagamento` — domínio fechado `Literal["Pix","Dinheiro","Cartão","Outro"]`
(`schemas.py:42`), mais `origem_preco`
`Literal["Tabela","Promoção","Parceria","Negociação","Cortesia"]` (`schemas.py:43`).

### Proteção contra duplicidade

Três camadas, em profundidade:

1. **Índice parcial único no banco** — `uq_financial_entries_receita_espirometria`
   sobre `spirometry_exam_id`, com predicado
   (`models.py:706-712`, `models.py:773-779`):
   ```sql
   tipo = 'receita' AND spirometry_exam_id IS NOT NULL AND
   (categoria IS NULL OR btrim(categoria, …) = '' OR lower(btrim(categoria, …)) = 'espirometria')
   ```
   Categoria ausente ou vazia cai **dentro** do predicado — era exatamente o
   contorno explorado na revisão crítica da M23.1.

2. **Guarda de aplicação** — `_bloquear_receita_duplicada()` roda dentro do
   `factory` do `POST /lancamentos` (`finance.py:481-487`), no `PATCH
   /lancamentos/{id}` sobre o **estado final** da linha (`finance.py:735-750`) e
   no lote de conciliação extra-Pastore (`finance.py:684-690`).

3. **Idempotência** — `idempotency_key` único + `idempotency_fingerprint`
   (`models.py:766-767`). No `POST /atendimentos` a chave é derivada por
   componente (`f"{payload.idempotency_key}:esp"`, `attendances.py:236`) e um
   replay devolve **409 `atendimento_repetido`**, não uma segunda cópia.

A corrida `SELECT`-então-`INSERT` é reconhecida no próprio código
(`finance.py:398-424`): o índice do banco é o backstop real e o perdedor da
corrida recebe `receita_ja_existe` com o código público do vencedor, em vez de
um 500.

---

## B) Espirometria Pastore

### Criação do exame

Mesmo endpoint `POST /atendimentos`, `tipo="espirometria_pastore"`
(`TIPO_PASTORE`, `schemas.py:547`).

Em `_criar_exame()` (`attendances.py:196-217`), quatro campos deixam de ser
escolha do cliente e viram **domínio derivado**:

```python
modalidade        = "clinica_parceira"
local_atendimento = unit.nome
partner_id        = partner.id
partner_unit_id   = unit.id
origem            = partner.nome
```

### `partner_id`

Resolvido por `canonical_pastore()` — `nucleo-m15/app/services/pastore.py:21-35`.
É **fail-closed**: exige **exatamente um** parceiro não arquivado cujo nome, em
minúsculas e sem espaços nas pontas, seja `"pastore"`. Zero ou dois → 409
`pastore_canonica_ambigua`.

Se o cliente mandar outro `partner_id`, o pedido é recusado com
`parceiro_pastore_invalido` (`attendances.py:199-203`).

### `partner_unit_id`

`pastore_unit()` (`services/pastore.py:46-55`) exige que a unidade exista,
**pertença ao parceiro canônico** e esteja **ativa**. Caso contrário: 422
`unidade_pastore_invalida`.

Na interface, quando existe uma única unidade ativa ela é exibida como campo
**somente leitura** (`central-cadastros.js:1083-1086`); com várias, vira um
`select` obrigatório restrito às unidades ativas.

### Broncodilatador

`spirometry_exams.broncodilatador: Boolean | None` — `models.py:329-330`, com
comentário explícito no modelo:

```
# None = não informado (registros históricos); True/False = com/sem BD
```

Coletado no formulário para **todos** os tipos de exame, inclusive Pastore
(`central-cadastros.js:894-896`, bloco `commonStart`).

### Por que NÃO nasce `FinancialEntry` individual

Duas travas independentes:

1. **No atendimento** — `_criar_financeiro()`, `attendances.py:262-270`:
   ```python
   if payload.tipo == TIPO_PASTORE:
       if fin is not None:
           raise _erro("pagamento_direto_pastore_proibido",
                       "Espirometria Pastore não aceita pagamento direto do paciente.")
       return []
   ```
   A interface sequer envia controles de pagamento nesse tipo
   (`central-cadastros.js:1211`: `if (tipo === TIPO_PASTORE) return null;`).

2. **No lançamento avulso** — `POST /lancamentos`, `finance.py:437-448`: se o
   `spirometry_exam_id` informado pertence a um exame cujo parceiro se chama
   "pastore", o pedido é recusado com 422 `pagamento_direto_pastore_proibido` e
   a mensagem *"use o fechamento mensal da parceria"*. Esta trava é avaliada
   **antes** da normalização de categoria, de propósito.

A regra de negócio está no cabeçalho do próprio router
(`nucleo-m15/app/routers/pastore.py:1-6`):

> O exame não é um recebimento. O único lançamento financeiro possível neste
> domínio é o recibo agregado do fechamento, criado depois que gestor confirma
> valor, data e forma do pagamento efetivamente recebido.

### Como o exame entra no `PartnerSettlement`

Pela tabela de ligação `partner_settlement_items` (`models.py:665-687`), cujo
docstring define o contrato:

> O vínculo é **não monetário**. A unicidade global do exame impede que ele
> participe de dois fechamentos.

`spirometry_exam_id` é `unique=True` **na tabela inteira**, não por fechamento —
um exame não pode estar em dois fechamentos, nem por engano nem por corrida.

Elegibilidade — `_eligible_exams()` (`pastore.py:155-169`) + `is_completed_pastore_exam()`
(`services/pastore.py:58-70`). O filtro é uma **lista de bloqueio**, não de
permissão: o exame é elegível se tiver `data_exame` e o status **não** for
vazio/`aguardando`/`agendada`/`cancelado`/`remarcado`/`não compareceu`.

### Competência mensal

`competency_month()` (`services/pastore.py:73-75`) normaliza `"YYYY-MM"` para o
**primeiro dia do mês**. `month_end()` (`services/pastore.py:78-83`) calcula o
último dia.

Comentário no modelo (`models.py:641-643`):

> Primeiro dia do mês de competência. O valor é derivado do mês dos exames
> incluídos, **nunca da data do recebimento**.

Unicidade garantida por `UniqueConstraint(partner_id, partner_unit_id,
competencia)` — `models.py:656-661`. Ou seja: **um fechamento por unidade por
mês**, imposto pelo banco.

### Quando nasce o lançamento agregado

Só em `POST /pastore/fechamentos/{id}/receber` — `pastore.py:394-483`, papel
**gestor** obrigatório. O `FinancialEntry` criado (`pastore.py:432-457`):

| campo | valor |
| --- | --- |
| `tipo` | `receita` |
| `categoria` | `"Recebimento de parceiro"` (literal local, **não** é uma das canônicas de `finance_categories.py`) |
| `descricao` | `f"Fechamento Pastore {competencia:%Y-%m} — {unit.public_code}"` |
| `valor` | `payload.valor_confirmado`, quantizado a 2 casas `ROUND_HALF_UP` |
| `data_competencia` | `settlement.competencia`, precisão `"dia"`, `dia_assumido=False` |
| `data_recebimento` | `payload.data_recebimento` |
| `status` | `"Recebido"` |
| `origem_preco` | `"Parceria"` |
| `partner_settlement_id` | `settlement.id` |

Travas do recebimento:

- **um recibo por fechamento**, imposto pelo banco: `partner_settlement_id` é
  `unique=True` em `financial_entries` (`models.py:753-757`), com o comentário
  *"Nunca se liga aos pacientes/exames individualmente"*;
- fechamento `cancelado` não pode ser recebido (409);
- se `valor_total` já estava confirmado e o valor recebido diverge → 409
  `valor_recebido_diverge_fechamento`;
- fechamento `recebido` é **imutável**: o `PATCH` recusa com 409
  `fechamento_ja_recebido` e manda corrigir por lançamento auditável separado
  (`pastore.py:352-357`);
- idempotência por chave, com verificação de que a chave não pertence a outro
  fechamento (`pastore.py:459-470`).

### Quem é o pagador

A **Pastore**, não o paciente. A interface é explícita
(`central-cadastros.js:1087-1089`):

> "O paciente não paga a SoproLife. Este exame entra no fechamento mensal da
> Pastore sem criar recebimento ou valor individual."

### Como o fechamento aparece no Financeiro

O recibo agregado é um `FinancialEntry` comum e entra na listagem padrão de
`/lancamentos`, categoria `"Recebimento de parceiro"`. A tela dedicada é
`GET /pastore/fechamentos` (`pastore.py:180-262`), que devolve indicadores
(`aguardando_fechamento`, `fechamento_em_aberto`, `a_receber`, `recebido`,
`valor_a_receber_confirmado`, `valor_recebido`), a lista de exames elegíveis
agrupados por unidade+competência, e os fechamentos serializados. O campo final
da resposta é literalmente:

```python
"regra_valor": "Não inferido; exige confirmação do gestor."
```

Estados possíveis (`pastore.py:47-54`): `incluido` → `enviado` → `a_receber` →
`recebido`, mais `cancelado`.

---

# 2. PRODUÇÃO REAL — exames Pastore

## Parceiro e unidades

| código | nome | tipo | status | arquivado |
| --- | --- | --- | --- | --- |
| **CLI-000002** | Pastore | clinica | ativa | não ← **canônico** |
| CLI-000001 | Pastore | Consultório | encerrada | **sim** (consolidado na M20) |

| unidade | nome | cidade | ativa |
| --- | --- | --- | --- |
| UNI-000001 | Pastore | Zona Sul | não |
| **UNI-000002** | Pastore Ipanema | Zona Sul | **sim** |

A resolução `canonical_pastore()` está **correta**: existe exatamente um parceiro
Pastore não arquivado. A duplicata histórica foi arquivada, não apagada — e
nenhum exame ficou preso nela (conferido: os 5 exames apontam para CLI-000002).

## Todos os exames Pastore reais

| ESP | data | BD | status exame | parceiro | unidade | competência | fechamento | status settlement |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ESP-000013 | 2026-07-14 | **não informado** | Liberado | Pastore | Pastore Ipanema | 2026-07 | **SEM FECHAMENTO** | — |
| ESP-000014 | 2026-07-18 | **não informado** | Liberado | Pastore | Pastore Ipanema | 2026-07 | **SEM FECHAMENTO** | — |
| ESP-000019 | 2026-08-01 | sim | Realizado | Pastore | Pastore Ipanema | 2026-08 | **SEM FECHAMENTO** | — |
| ESP-000017 | 2026-08-04 | sim | Realizado | Pastore | Pastore Ipanema | 2026-08 | **SEM FECHAMENTO** | — |
| ESP-000018 | 2026-08-04 | sim | Realizado | Pastore | Pastore Ipanema | 2026-08 | **SEM FECHAMENTO** | — |

## Respostas pedidas

| pergunta | resposta |
| --- | --- |
| quantidade total de exames Pastore | **5** |
| quantidade **com** BD | **3** (ESP-000017, ESP-000018, ESP-000019) |
| quantidade **sem** BD | **0** |
| BD **não informado** | **2** (ESP-000013, ESP-000014) |
| quantos estão **sem fechamento** | **5** (100%) |
| quantos estão **em fechamento** | **0** |
| quantos já foram **recebidos** | **0** |

**Observação sobre "sem BD".** A pergunta assumia a partição com/sem. Em produção
ela é **com BD (3) / não informado (2)** — nenhum exame Pastore foi marcado
explicitamente como `broncodilatador = false`. `NULL` significa "não informado"
por contrato do modelo, não "sem broncodilatador". Se uma futura regra de preço
tratar `NULL` como "sem BD", ela vai precificar 2 exames com base numa suposição.
Não assumo essa equivalência aqui.

**Observação sobre status.** Os dois exames de julho estão como `"Liberado"`, que
**não** pertence ao domínio canônico `StatusExame`
(`schemas.py:34` — `"Aguardando" | "Realizado" | "Laudo Liberado" | "Cancelado" |
"Remarcado"`). É um valor deliberadamente preservado, declarado fora de escopo em
`nucleo-m15/app/status_display.py:9-13`. **Não bloqueia o fechamento**: o filtro
de elegibilidade é lista de bloqueio, e `"Liberado"` não está nela. Verificado
diretamente: os **5** exames passam no filtro de elegibilidade, com unidade
ativa. Ou seja, os fechamentos de **2026-07** (2 exames) e **2026-08** (3 exames)
poderiam ser criados hoje, e não foram.

---

# 3. FINANCEIRO PASTORE — provado no banco

| verificação | consulta | resultado |
| --- | --- | --- |
| exame Pastore com **receita individual indevida** | `financial_entries` ⋈ `spirometry_exams` ⋈ `partners` onde nome = pastore | **0 linhas** ✅ |
| **lançamento ligado diretamente a ESP Pastore** | idem | **0 linhas** ✅ |
| lançamento ligado a **encaminhamento** Pastore | `financial_entries` ⋈ `partner_referrals` ⋈ `partners` | **0 linhas** ✅ |
| encaminhamento Pastore com campo monetário preenchido | `valor_cobrado`/`valor_recebido`/`valor_repasse`/`percentual_repasse` não nulos | **0 linhas** ✅ |
| **settlements existentes** | `partner_settlements` | **0 linhas** |
| **itens por settlement** | `partner_settlement_items` | **0 linhas** |
| **valor_total de cada settlement** | — | **não existe nenhum** |
| **status** | — | **não existe nenhum** |
| **lançamento financeiro agregado** | `financial_entries` com `partner_settlement_id` não nulo | **0 linhas** |
| **valor recebido** | — | **R$ 0,00** |
| repasses a parceiro | `partner_transfers` | **0 linhas** |
| eventos de auditoria `pastore.*` / `*fechamento*` | `audit_logs` | **0 linhas** |

**Conclusão da seção 3.** A metade **protetiva** do contrato Pastore está
**provada como eficaz em produção**: nenhum centavo indevido foi reconhecido por
exame individual, nem pelo atendimento, nem por lançamento avulso, nem por
encaminhamento. A metade **produtiva** — transformar exame em dinheiro — **nunca
foi exercida uma única vez**. A tabela `audit_logs` confirma: nenhum fechamento
Pastore foi jamais criado, atualizado ou recebido no sistema.

---

# 4. SOPROLIFE — exames e lançamentos

## Exames não-Pastore e seus lançamentos

| ESP | data | BD | status exame | modalidade | LAN | valor | status fin. | forma | comp. | origem preço |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ESP-000016 | 2026-08-09 | sim | Realizado | clinica_parceira | **— (nenhum)** | — | — | — | — | — |
| ESP-000015 | 2026-07-15 | não inf. | Realizado | cowork | LAN-000003 | 220,00 | Recebido | Pix | 2026-07 | Tabela |
| ESP-000012 | 2026-07-10 | não inf. | Realizado | — | LAN-000001 | 220,00 | Recebido | Pix | 2026-07 | Promoção |
| ESP-000011 | 2026-07-02 | não inf. | Exame realizado | — | LAN-000002 | 219,00 | Recebido | Pix | 2026-07 | Negociação |
| ESP-000010 | 2026-07-01 | não inf. | Exame realizado | — | LAN-000013 | 238,57 | Recebido | — | **—** | — |
| ESP-000001 | 2026-06-01 | não inf. | Exame realizado | — | LAN-000004 | 238,58 | Recebido | — | **—** | — |
| ESP-000002 | 2026-06-01 | não inf. | Exame realizado | — | LAN-000005 | 238,58 | Recebido | — | **—** | — |
| ESP-000003 | 2026-06-01 | não inf. | Exame realizado | — | LAN-000006 | 238,58 | Recebido | — | **—** | — |
| ESP-000004 | 2026-06-01 | não inf. | Exame realizado | — | LAN-000007 | 238,58 | Recebido | — | **—** | — |
| ESP-000005 | 2026-06-01 | não inf. | Exame realizado | — | LAN-000008 | 238,58 | Recebido | — | **—** | — |
| ESP-000006 | 2026-06-01 | não inf. | Exame realizado | — | LAN-000009 | 238,58 | Recebido | — | **—** | — |
| ESP-000007 | 2026-06-01 | não inf. | Exame realizado | — | LAN-000010 | 238,58 | Recebido | — | **—** | — |
| ESP-000008 | 2026-06-01 | não inf. | Exame realizado | — | LAN-000011 | 238,58 | Recebido | — | **—** | — |
| ESP-000009 | 2026-06-01 | não inf. | Exame realizado | — | LAN-000012 | 238,58 | Recebido | — | **—** | — |
| ESP-TF0001 | **—** | sim | Realizado | — | **— (nenhum)** | — | — | — | — | — |

Panorama total dos lançamentos do banco — **uma única linha**:

| tipo | categoria | status | qtd | soma |
| --- | --- | --- | --- | --- |
| receita | Espirometria | Recebido | **13** | **R$ 3.044,79** |

## Identificações pedidas

### ✅ Exame SoproLife com receita automática correta

**ESP-000012 → LAN-000001** e **ESP-000011 → LAN-000002** (origem
`financeiro_lancamentos`, importados da planilha na M15.8) e **ESP-000015 →
LAN-000003** (origem `m18_reconciliacao_bancaria`).

São os três lançamentos **completos**: valor individual real, categoria
`Espirometria` canônica, `origem_preco` preenchida, forma de pagamento Pix e
`data_competencia` batendo com a data do exame.

### ⚠️ Os 10 lançamentos de rateio — não são receita individual real

**LAN-000004 … LAN-000013** (ESP-000001 … ESP-000010) merecem atenção separada.
A aritmética é conclusiva:

```
238,58 × 9  = 2.147,22
           +   238,57
           = 2.385,79
220,00 + 219,00 + 220,00 =   659,00
                          ─────────
TOTAL                      3.044,79
```

**R$ 3.044,79** é exatamente `TOTAL_ALVO_EXTRA_PASTORE`, a constante fixa em
`nucleo-m15/app/routers/finance.py:534`, rotulada *"Total histórico informado —
exames próprios fora da Pastore"*.

Ou seja: esses 10 valores são uma **divisão igualitária de um total agregado**
(238,579 arredondado, com o centavo residual jogado no último), não o preço
efetivamente cobrado de cada paciente. Coerente com isso, os 10 não têm
`origem_preco`, não têm `data_competencia` e não têm `data_recebimento`.

Isso **não é um bug do código** — `conciliar_lote_extra_pastore()` valida que a
soma bata com o pendente recalculado no servidor, e o próprio docstring diz
*"nunca preenche valores por média — cada item precisa vir com o valor exato
evidenciado"* (`finance.py:602-607`). A validação é sobre a **soma**, e a soma
fecha. Foi o operador que informou o rateio. O efeito prático: **o total
financeiro está certo; a atribuição por exame não é verdade individual.**

### ⚠️ Exame SoproLife sem LAN

| ESP | data | status | contexto |
| --- | --- | --- | --- |
| ESP-000016 | 2026-08-09 | Realizado | `origem` / `responsavel` / `local_atendimento` = **"TESTE M25.13"** — registro de teste da validação visual de laudos, criado em 2026-08-09 |
| ESP-TF0001 | **sem data** | Realizado | seed de teste, criado em 2026-08-08 (código `TF` fora da sequência `ESP-0000NN`) |

**Os dois são artefatos de teste, não atendimentos reais perdidos.** Nenhum
atendimento SoproLife genuíno está sem lançamento. Mas eles **poluem o
indicador**: ESP-000016 tem `modalidade = clinica_parceira` sem `partner_id`, o
que é um estado incoerente, e ambos aparecem em qualquer relatório de "exame sem
receita".

### ✅ LAN duplicado

**0 linhas.** Nenhum exame tem mais de um lançamento. O índice parcial único e o
guarda de aplicação estão fazendo o trabalho.

### ✅ LAN sem exame correspondente

**0 órfãos** (nenhum `spirometry_exam_id` apontando para exame inexistente) e
**0 lançamentos sem nenhum vínculo técnico** (todos os 13 têm
`spirometry_exam_id`).

### ⚠️ Achados adicionais não solicitados, mas relevantes

1. **Os 13 lançamentos estão `Recebido` e nenhum tem `data_recebimento`.**
   Verificado individualmente: a coluna está vazia nos 13. O caixa não tem data.
2. **10 dos 13 não têm `data_competencia`.** Não entram em nenhum recorte mensal
   — some do DRE por competência.
3. **CON-000001 (consulta de 2026-06-01, "Consulta realizada") não tem nenhum
   lançamento.** É a única consulta do banco, e ela é receita SoproLife não
   reconhecida.
4. **Três grafias de status de exame convivem**: `"Exame realizado"` (11),
   `"Realizado"` (7), `"Liberado"` (2). Só a segunda é canônica; a primeira tem
   normalização de exibição, a terceira é explicitamente fora de escopo.

---

# 5. PREÇO: Pastore com BD **versus** sem BD

## Resposta direta

# **NÃO.**

O sistema **não** calcula — hoje, em lugar nenhum — um valor diferente para
Pastore sem broncodilatador versus Pastore com broncodilatador. Ele não calcula
valor **nenhum** para Pastore. O único número monetário que existe no domínio
Pastore é **digitado manualmente pelo gestor**.

## Onde o dado de broncodilatador existe

| local | referência | uso |
| --- | --- | --- |
| **Coluna do banco** | `spirometry_exams.broncodilatador`, `Boolean` nullable — `models.py:329-330` | armazenamento |
| Captura na API | `AtendimentoEspirometria.broncodilatador`, `ExamCreate`, `ExamUpdate` — `schemas.py:174, 184, 528` | entrada |
| Captura na tela | `esp_bd` — `central-cadastros.js:894-896` | entrada |
| Gravação | `attendances.py:221`, `operations.py:237`, `operations.py:298-300` | escrita |
| Serialização | `serializers.py:169` | exibição |
| **Contagem** no resumo | `snapshots.py:520-522, 581-582` — `sem_broncodilatador` / `com_broncodilatador` | estatística |
| **Laudo médico** | `reports.py:2221` (`post_bronchodilator`), `reports.py:4966`, `reports.py:5014`; `native_report_builder.py:254`; `report_conclusions.py` | clínico |

**Nenhum desses pontos é uma trilha monetária.** Uma busca por
`broncodilatador` em todo o núcleo devolve exatamente essas ocorrências: uma
coluna, três schemas, três escritas, uma serialização, uma contagem estatística e
o pipeline de laudo. **Zero** dentro de qualquer código que produza `Decimal`,
`valor`, `FinancialEntry` ou `valor_total`.

## Por que o BD ainda não vira valor esperado

Porque **não existe nenhuma entidade "valor esperado"** no sistema. A cadeia é
literalmente esta:

```
broncodilatador (bool)  →  laudo, contagem estatística
                        ↛  (nada)  →  valor
```

O único campo monetário de fechamento é `partner_settlements.valor_total`, e ele
tem exatamente **três** pontos de escrita em todo o código:

| local | valor gravado |
| --- | --- |
| `pastore.py:313` | `valor_total=None` na criação do fechamento |
| `pastore.py:374-375` | `updates["valor_total"]` — **digitado pelo gestor** no `PATCH`, apenas quantizado |
| `pastore.py:472` | `settlement.valor_total = confirmed` — **digitado pelo gestor** no `/receber` |

Nenhum deles deriva, multiplica, consulta tabela de preço ou olha para
broncodilatador, quantidade de exames ou tipo de exame. O único cruzamento é
**valor confirmado × valor confirmado** (`pastore.py:423`): o `/receber` compara
o que o gestor digita agora com o que o gestor digitou antes. Não existe
comparação **esperado × confirmado**, porque não existe esperado.

A resposta da API declara isso de forma literal — `pastore.py:261`:

```python
"regra_valor": "Não inferido; exige confirmação do gestor."
```

E o resumo publicado carrega os campos como `null` explícito —
`snapshots.py:585-590`:

```python
"financeiro_parametros": {
    "valor_exame_sem_broncodilatador": None,
    "valor_exame_com_broncodilatador": None,
    "repasse_percentual_pastore": None,
    "nota": "Valores comerciais não definidos; o sistema não infere.",
},
```

Confirmado em produção: `painel-soprolife/data/parcerias-pastore-summary.json`
linhas 49-51 trazem os três como `null`.

## Configuração histórica de percentual/valor que NÃO é verdade monetária atual

**Existe, sim — e é substancial.** Foram encontradas três camadas:

### (a) Colunas da planilha Pastore, importadas como referência bloqueada

`nucleo-m15/app/migration/adapters.py:37`:

```python
PAPEL_REF_MONETARIA_BLOQUEADA = "ref_monetaria_bloqueada"  # Pastore: nunca vira dinheiro
```

**Adapter `pastore_config`** (`adapters.py:334-342`) — comentado no código como
*"Config Pastore NUNCA cria verdade monetária (fonte única M14.2)"*:

- `valor_exame_sem_bd` ← **a regra de preço sem BD que você procura**
- `valor_exame_com_bd` ← **a regra de preço com BD que você procura**
- `repasse_pastore_tipo`
- `repasse_pastore_valor`
- `custo_insumo_padrao`, `custo_deslocamento_padrao`, `custo_profissional_padrao`

**Adapter `pastore_atendimentos`** (`adapters.py:364-374`) — *"CRM Pastore NUNCA
cria verdade monetária"*:

- `valor_cobrado`, `repasse_pastore`, `receita_bruta`
- `custo_insumo`, `custo_deslocamento`, `custo_profissional`, `outros_custos`
- `custo_total`, `resultado_liquido`

Essas colunas são apenas **contadas** durante o staging
(`migration/staging.py:336-339`, `refs_monetarias_bloqueadas`) e nunca mapeadas
para nenhuma coluna do banco.

**Provado no banco de produção:** consultei `information_schema.columns` nas
tabelas de parceria procurando qualquer coluna cujo nome case
`valor|preco|percentual|repasse|custo|receita`. O modelo `PartnerUnitConfig`
(destino do `pastore_config`) tem **zero** colunas monetárias. Os dois registros
de configuração que existem em produção carregam apenas dia da semana, horário e
status:

| unidade | status | dia | horário |
| --- | --- | --- | --- |
| Pastore Ipanema | Planejada | Terça-feira | 08:00–12:00 |
| Pastore Ipanema | Planejada | Sábado | 08:00–12:00 |

O docstring do modelo já dizia (`models.py:505-510`): *"NUNCA carrega valor
monetário: colunas de preço/repasse/custo da fonte são referência bloqueada
(M14.2) e ficam fora do modelo por contrato."* Isso é **confirmado**, não
suposto.

### (b) `partnerships` — colunas comerciais que existem, mas ninguém lê

A tabela `partnerships` **tem** as colunas que suportariam uma regra comercial:

| tabela | coluna | tipo |
| --- | --- | --- |
| `partnerships` | `modelo_repasse` | varchar |
| `partnerships` | `percentual_repasse` | numeric |
| `partnerships` | `valor_repasse_fixo` | numeric |
| `partner_settlements` | `valor_total` | numeric |

Estado em produção — a **única** parceria cadastrada é a da Pastore:

| PAR | parceiro | status | modelo_repasse | percentual_repasse | valor_repasse_fixo |
| --- | --- | --- | --- | --- | --- |
| PAR-000001 | CLI-000002 (Pastore) | `em_negociacao` | **`indefinido`** | **NULL** | **NULL** |

Dois pontos importantes:

1. Está **vazia** — `modelo_repasse = "indefinido"`, parceria `em_negociacao`.
2. **Mesmo se fosse preenchida, nada aconteceria.** Verifiquei todos os usos de
   `percentual_repasse` / `valor_repasse_fixo` / `modelo_repasse` no núcleo: são
   escritos em `POST /parcerias` (`routers/partners.py:367-369`), lidos em
   `serializers.py:248-250` para exibição, e **em nenhum outro lugar**. O router
   `pastore.py` **não importa e não consulta `Partnership`** para calcular
   valor. É um campo de anotação, não uma regra ativa.

### (c) `PartnerReferral` — campos de repasse por encaminhamento, não usados

`models.py:601-607`: `valor_cobrado`, `valor_recebido`, `tipo_repasse`,
`valor_repasse`, `percentual_repasse`, `status_repasse`. Em produção, **0
encaminhamentos Pastore têm qualquer um deles preenchido**, e o fluxo Pastore
canônico não passa por `PartnerReferral`.

### Resumo da seção 5

| pergunta | resposta |
| --- | --- |
| BD altera o valor calculado? | **NÃO** — não há valor calculado |
| Onde o BD existe? | `spirometry_exams.broncodilatador` — usado em laudo e estatística, **nunca** em dinheiro |
| Existe tabela de preço Pastore? | **NÃO**, em nenhuma tabela do banco |
| Existe valor esperado do fechamento? | **NÃO** |
| Existe comparação esperado × confirmado? | **NÃO** — só confirmado × confirmado |
| Existe config histórica de valor/percentual? | **SIM** — `valor_exame_sem_bd`, `valor_exame_com_bd`, `repasse_pastore_tipo/valor` na planilha Pastore, importados como `PAPEL_REF_MONETARIA_BLOQUEADA` e **descartados por contrato** (M14.2). E `partnerships.percentual_repasse` / `valor_repasse_fixo`, que existem no banco mas **estão nulas e não são lidas por ninguém**. |

**Nenhuma regra comercial foi assumida neste relatório.** Os valores que a
planilha Pastore carrega existem, mas foram deliberadamente bloqueados na
migração e não estão no banco — não posso afirmar quais são sem que você os
declare.

---

# 6. CENÁRIO IDEAL — desenho mínimo, sem implementar

Nada abaixo foi codificado. É o modelo mínimo que fecha as lacunas provadas
acima, respeitando as invariantes que o sistema já tem.

## 6.1 SoproLife — valor variável informado no atendimento → LAN individual

O fluxo já existe e está correto. O mínimo é **fechar a cobertura**, não
reescrever:

| lacuna provada | correção mínima |
| --- | --- |
| valor em branco = nenhum lançamento, silenciosamente | **decisão explícita obrigatória** no formulário: valor informado **ou** um motivo do domínio fechado (`Cortesia`, `Pendente de acerto`, `Não cobrado`). Nada de valor default. |
| `POST /espirometrias` cria exame sem qualquer financeiro | manter o endpoint (é usado por importadores), mas o exame nascido por ele entra numa **fila "atendimento sem definição financeira"** — a mesma ideia da conciliação extra-Pastore, sem constante fixa |
| 13 lançamentos `Recebido` sem `data_recebimento` | tornar `data_recebimento` obrigatória quando `status = "Recebido"`. A UI já valida isso (`central-cadastros.js:1218-1220`); a **API não** — daí os 13 |
| 10 lançamentos sem `data_competencia` | backfill auditável a partir de `spirometry_exams.data_exame`, com `data_competencia_precisao` registrando que foi derivada |
| CON-000001 sem lançamento | mesma fila de definição financeira |
| ESP-000016 / ESP-TF0001 (testes) poluindo | marcador de registro de teste, ou expurgo auditado |

**Nenhuma dessas correções inventa valor.** Todas transformam "ausência
silenciosa" em "ausência declarada".

## 6.2 Pastore — do exame ao recebimento agregado

Fluxo alvo:

```
tipo do exame + BD
   ↓  [1] tabela de preço vigente  ← NOVO, único lugar onde nasce dinheiro Pastore
valor contratual esperado (por exame)
   ↓  [2] regra de participação da SoproLife
valor devido à SoproLife (por exame)
   ↓  [3] item do fechamento mensal   ← estende partner_settlement_items
   ↓
valor esperado do fechamento = Σ itens
   ↓  [4] comparação esperado × confirmado pela Pastore  ← NOVO
   ↓  [5] recebimento agregado (já existe)
LAN agregado (já existe, intocado)
```

### [1] Tabela de preço vigente — a peça que falta

Entidade nova, digamos `PartnerPriceRule`, com:

| campo | papel |
| --- | --- |
| `partner_id`, `partner_unit_id` (nulo = todas) | escopo |
| `tipo_exame` | `espirometria` hoje; abre espaço para Pastore Assist e outros |
| `broncodilatador` | `true` / `false` / `null` = regra única para ambos |
| `valor_contratual` | o que a Pastore cobra do paciente/convênio |
| `modelo_participacao` | `percentual` \| `valor_fixo` \| `integral` |
| `percentual_soprolife` / `valor_fixo_soprolife` | mutuamente exclusivos |
| `complemento_pastore` | a diferença que a Pastore complementa, quando existir |
| `vigencia_inicio`, `vigencia_fim` (nulo = vigente) | **vigência**, não sobrescrita |

Invariantes que essa entidade precisa herdar do que já existe no sistema:

- **Vigência, nunca edição.** Mudou o preço? Nasce uma regra nova com nova
  vigência; a antiga é fechada. Mesma filosofia de `FinancialEntry`, cujo `valor`
  é imutável de propósito (`schemas.py:462-466`: *"correção de valor é um novo
  lançamento (trilha íntegra), nunca uma edição silenciosa"*).
- **Resolução fail-closed.** Duas regras vigentes cobrindo o mesmo
  (unidade, tipo, BD, data) → erro estruturado, não "escolhe a primeira". Mesmo
  padrão de `canonical_pastore()`.
- **`broncodilatador IS NULL` não casa com regra de "sem BD".** Em produção há
  **2 exames** nessa situação (ESP-000013, ESP-000014). Precificar `NULL` como
  "sem BD" seria assumir regra comercial. O correto é o fechamento **recusar** o
  exame com BD não informado e pedir a correção do cadastro — e a M25.17 já
  criou o `PATCH /espirometrias` que permite corrigir `broncodilatador`
  (`operations.py:298-300`).

### [2] e [3] Item do fechamento com valor esperado

`partner_settlement_items` hoje é **deliberadamente não monetário**
(`models.py:665-672`). O mínimo é acrescentar, **por item**:

- `price_rule_id` — qual regra vigente foi aplicada (rastreabilidade);
- `valor_contratual_esperado`;
- `valor_devido_soprolife_esperado`.

Congelados **no momento da inclusão no fechamento**, não recalculados na
exibição — senão uma mudança de preço futura reescreveria o passado.

### [4] Comparação esperado × confirmado — o controle que hoje não existe

No `PartnerSettlement`, ao lado do `valor_total` já existente (que passa a ser
inequivocamente **o confirmado pela Pastore**):

- `valor_esperado` = Σ `valor_devido_soprolife_esperado` dos itens — **derivado,
  nunca digitado**;
- `divergencia` = `valor_total − valor_esperado`;
- quando houver divergência, exigir `motivo_divergencia` do gestor antes de
  permitir `a_receber`. Diferença não explicada não avança de estado.

Isto **não** enfraquece a regra atual — o gestor continua sendo quem confirma o
que a Pastore efetivamente pagou. O que muda é que o sistema passa a ter uma
opinião própria para comparar, em vez de aceitar qualquer número em silêncio.

### [5] Recebimento agregado — sem mudança

`POST /pastore/fechamentos/{id}/receber` continua igual: um `FinancialEntry`
agregado por fechamento, `partner_settlement_id` único, `origem_preco="Parceria"`,
imutabilidade após recebido. **Essa parte está pronta e correta.**

### Casos que o modelo precisa acomodar desde o desenho

| caso | como o modelo acomoda |
| --- | --- |
| preço **sem** BD | linha com `broncodilatador = false` |
| preço **com** BD | linha com `broncodilatador = true` |
| **percentual** da SoproLife | `modelo_participacao = percentual` |
| **Pastore Assist** | outro `tipo_exame` — nova linha, não novo campo |
| **diferença complementada** pela Pastore | `complemento_pastore`, componente separado do devido |
| **vigências futuras** | `vigencia_inicio` / `vigencia_fim`; regra antiga fechada, nunca sobrescrita |
| BD **não informado** | não casa com nenhuma regra → fechamento recusa e pede correção |

### O que NÃO deve ser feito

- **Não** reutilizar `partnerships.percentual_repasse`. É repasse *da SoproLife
  para o parceiro* (`PartnerTransfer`), direção oposta ao dinheiro Pastore, que
  entra. Misturar as duas semânticas na mesma coluna é dívida garantida.
- **Não** ressuscitar automaticamente `valor_exame_sem_bd` / `valor_exame_com_bd`
  da planilha. Foram bloqueados por decisão de contrato (M14.2) e ninguém provou
  que ainda valem. Se forem os números certos, devem ser **declarados** e
  gravados como regra vigente com data de início — não inferidos de um import.
- **Não** criar `FinancialEntry` individual por exame Pastore. As duas travas
  (`attendances.py:262-270`, `finance.py:437-448`) são a razão de os 5 exames
  estarem limpos hoje.
- **Não** tornar `valor_total` derivado. O gestor continua confirmando o
  recebimento real; o esperado é um número **ao lado**, para conferência.

---

# 7. CONCLUSÃO

## SOPROLIFE: **PARCIAL**

### O que está integrado ✅

| eixo | estado | evidência |
| --- | --- | --- |
| Valor informado no atendimento → LAN individual automático | **integrado** | `attendances.py:283-305`; provado em ESP-000012→LAN-000001, ESP-000011→LAN-000002, ESP-000015→LAN-000003 |
| Vínculo técnico ESP ↔ LAN sem PII | **integrado** | 13/13 lançamentos com `spirometry_exam_id`; 0 lançamentos soltos |
| Atomicidade do atendimento | **integrado** | `attendances.py:110-121` |
| Categoria canônica | **integrado** | 13/13 com categoria `Espirometria` exata |
| Proteção contra duplicidade | **integrado e provado** | **0 LAN duplicados** em produção — índice parcial único + guarda no POST/PATCH/lote |
| Integridade referencial | **integrado e provado** | **0 órfãos**, **0 LAN sem vínculo** |

### O que falta ❌

1. **O lançamento é opcional e a omissão é silenciosa.** Valor em branco no
   formulário → nenhum LAN, sem aviso, sem pendência, sem fila. Não há como
   distinguir "não cobrei" de "esqueci de informar".
2. **Existe um segundo caminho sem financeiro nenhum.** `POST /espirometrias`
   (`operations.py:207-262`) cria exame e **nunca** cria lançamento. Nada
   reconcilia depois.
3. **10 dos 13 lançamentos não são receita individual verdadeira.** LAN-000004…013
   são o rateio igualitário do total histórico de R$ 3.044,79
   (`finance.py:534`). A soma está certa; a atribuição por exame, não.
4. **13 de 13 lançamentos estão `Recebido` sem `data_recebimento`.** A UI exige
   essa data; a API não. O caixa não tem data.
5. **10 de 13 lançamentos não têm `data_competencia`.** Somem de qualquer
   apuração mensal.
6. **CON-000001 (consulta realizada em 2026-06-01) não tem lançamento.** Receita
   SoproLife não reconhecida.
7. **2 exames sem LAN** — ESP-000016 e ESP-TF0001, ambos artefatos de teste
   (M25.13 e seed), que contaminam qualquer indicador de cobertura.

**Por que "PARCIAL" e não "INTEGRADO":** o mecanismo automático funciona e está
provado, mas ele só cobre o atendimento que passa pelo formulário completo com
valor preenchido. Fora disso, o sistema aceita o silêncio.

**Por que "PARCIAL" e não "NÃO INTEGRADO":** há três lançamentos completos
gerados corretamente pela cadeia, zero duplicidade, zero órfão, e o total
financeiro do período histórico fecha ao centavo.

---

## PASTORE: **PARCIAL**

Sub-veredito por eixo, porque o rótulo único esconde uma assimetria grande:

| eixo | estado |
| --- | --- |
| **Contenção** — não gerar receita individual indevida | ✅ **INTEGRADO E PROVADO** — 0 violações em 5 exames, com duas travas independentes |
| **Captura** — exame com parceiro, unidade e BC corretos | ✅ **INTEGRADO** — 5/5 na unidade ativa correta, parceiro canônico resolvido corretamente |
| **Agregação** — exame → item de fechamento | ⚠️ **IMPLEMENTADO, NUNCA EXERCIDO** — 0 fechamentos, 0 itens, 0 eventos de auditoria |
| **Precificação** — tipo + BD → valor esperado | ❌ **NÃO EXISTE** |
| **Conferência** — esperado × confirmado | ❌ **NÃO EXISTE** |
| **Recebimento agregado → LAN** | ⚠️ **IMPLEMENTADO, NUNCA EXERCIDO** — R$ 0,00 reconhecido |

### O que falta ❌

1. **Não existe valor esperado, em lugar nenhum.** Nenhuma tabela, nenhuma
   constante, nenhuma função. `partner_settlements.valor_total` é 100% digitado
   pelo gestor — três pontos de escrita, todos manuais.
2. **BD é coletado e nunca precificado.** Está no banco, no laudo e na
   estatística; não aparece em nenhuma linha de código que produza dinheiro.
3. **Não existe conferência esperado × confirmado.** A única comparação é
   confirmado × confirmado (`pastore.py:423`).
4. **Nenhuma competência foi jamais fechada.** 5 exames — 2 de 2026-07 e 3 de
   2026-08 — estão elegíveis **hoje** (verificado: todos passam no filtro, com
   unidade ativa) e nenhum fechamento foi criado. Julho fechou há 11 dias.
5. **R$ 0,00 reconhecido.** Zero settlements, zero itens, zero recebimentos, zero
   lançamentos agregados, zero eventos de auditoria Pastore.
6. **2 dos 5 exames têm BD não informado.** Qualquer regra de preço futura vai
   travar neles até que o cadastro seja corrigido.
7. **A parceria comercial está `em_negociacao` com `modelo_repasse =
   "indefinido"`.** E mesmo que fosse preenchida, `pastore.py` não lê
   `Partnership` — o campo não é uma regra ativa.

### O bloqueio real

Não é técnico. A estrutura de fechamento está completa, correta e defendida por
constraints de banco. **O bloqueio é uma decisão comercial que nunca foi
tomada:** qual é o valor contratual da espirometria Pastore com e sem
broncodilatador, e qual a participação da SoproLife.

Enquanto essa decisão não for **declarada**, o sistema está fazendo exatamente o
que foi projetado para fazer na M22 — recusar-se a inventar. O comportamento
atual não é um defeito; é a consequência correta de uma regra ausente. Mas o
custo dela é visível: **5 exames prestados, nenhum real reconhecido.**

---

## Ação imediata possível — sem definir nenhum preço

Vale registrar: mesmo **sem** a regra de preço, é possível hoje criar os
fechamentos de **2026-07** (ESP-000013, ESP-000014) e **2026-08** (ESP-000017,
ESP-000018, ESP-000019), que nascem com `valor_total = NULL` e status `incluido`.
Isso já tiraria os 5 exames do limbo "sem fechamento" e daria à Pastore uma lista
concreta para conferir — o valor entra depois, quando for confirmado.

**Não executei essa ação.** Esta etapa é somente leitura, e criar fechamento é
escrita.

---

## Apêndice — o que foi executado

**Leitura de código** (branch `painel-soprolife-v01`, commit `075528b`):
`models.py`, `schemas.py`, `finance_categories.py`, `status_display.py`,
`snapshots.py`, `serializers.py`, `routers/attendances.py`, `routers/pastore.py`,
`routers/finance.py`, `routers/operations.py`, `services/pastore.py`,
`services/integrity.py`, `migration/adapters.py`, `migration/staging.py`,
`js/central-cadastros.js`, `js/espirometria-financeiro.js`,
`data/parcerias-pastore-summary.json`.

**Leitura de produção** (`soprolife_m15` na VPS, via `su - postgres`):
2 scripts, ~26 consultas, **exclusivamente `SELECT`**. Nenhum
`INSERT`/`UPDATE`/`DELETE`/`DDL`, nenhuma migration, nenhum serviço reiniciado,
nenhum deploy. Os arquivos `.sql` temporários foram removidos da VPS ao fim de
cada execução.

**Não executado, conforme instruído:** criação de paciente, exame, lançamento ou
fechamento; alteração de valor ou status; migration; deploy; commit de alteração
de código.
