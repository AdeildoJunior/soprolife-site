# M26.2 — Auditoria e correção do Financeiro automático da espirometria

**Data:** 31/08/2026
**Branch:** `claude-m26-2-financeiro-automatico-espirometria`
**Base:** `origin/painel-soprolife-v01` @ `fc2a7ac`
**Worktree:** `/home/fedorasurf/soprolife-worktrees/claude-m26-2-financeiro-automatico`

Documento vivo — atualizado durante a missão.

Escopo intocado por contrato: template do laudo, fluxo da Dra. Ana, assinatura
externa, PDFs históricos.

Nenhum nome de paciente aparece aqui. Só códigos públicos.

---

## Estado da missão

| Etapa | Situação |
|---|---|
| 1. Auditoria read-only do código | **concluída** |
| 2. Auditoria read-only de produção | pendente de acesso SSH |
| 3. Exames Pastore | pendente de produção |
| 4. Regra comercial | pendente de produção |
| 5. Arquitetura | **concluída** |
| 6. Correção + script | em andamento |
| 7. Testes | pendente |
| 8. Relatório vivo | este arquivo |
| 9. Execução completa | pendente |
| 10. Prova final | pendente |

---

## 1. Auditoria do código — respostas provadas

Todas as afirmações abaixo saem de leitura de código na base `fc2a7ac`.

### A. Qual evento cria LAN para atendimento SoproLife?

**`POST /atendimentos` e `POST /atendimentos/novo-paciente`**, na mesma
transação do exame, via `_criar_financeiro`
(`nucleo-m15/app/routers/attendances.py:505`), chamado em
`attendances.py:371`.

O gatilho não é o exame existir. É o payload trazer o bloco
`financeiro.espirometria`:

```python
if fin is None:
    return []
...
if fin.espirometria is not None:
    if exam is None:
        raise _erro("financeiro_sem_exame", ...)
    entry = _entry(db, tipo="receita", categoria=CATEGORIA_ESPIROMETRIA,
                   valor=bloco.valor, ..., spirometry_exam_id=exam.id)
```

O valor gravado é `bloco.valor` — **o que o operador digitou**. Nenhuma
consulta a tabela de preço acontece em lugar nenhum do caminho.

### B. Por que Vanessa gerou LAN-000017 automaticamente?

Porque o cadastro de `ESP-000039` foi feito pela Central de Cadastros
(`js/central-cadastros.js:1542` → `POST /atendimentos`) **com o bloco
financeiro preenchido, valor R$ 230,00**. O servidor materializou aquele valor
como `FinancialEntry` na mesma transação, vinculado por
`spirometry_exam_id`.

O automático é o **vínculo e a transação**, não o **preço**. Chamar isso de
"preço automático" é o que faz o Pastore parecer quebrado quando não está.

### C. Qual evento cria LAN para Pastore?

**Um só: `POST /pastore/fechamentos/{id}/receber`**
(`app/routers/pastore.py`, `receive_monthly_settlement`). É o único ponto do
código que cria `FinancialEntry` com `partner_settlement_id`, e ele exige do
gestor três coisas digitadas: `valor_confirmado`, `data_recebimento`,
`forma_pagamento`.

O lançamento nasce `tipo="receita"`, `categoria="Recebimento de parceiro"`,
`origem_preco="Parceria"`, `status="Recebido"` — agregado do mês, nunca
ligado a exame individual.

### D. O exame Pastore sozinho cria financeiro?

**Não, e é proibido explicitamente** (`attendances.py:512`):

```python
if payload.tipo == TIPO_PASTORE:
    if fin is not None:
        raise _erro("pagamento_direto_pastore_proibido",
                    "Espirometria Pastore não aceita pagamento direto do paciente.")
    return []
```

Tentar mandar bloco financeiro num atendimento Pastore é erro 422. Depende
inteiramente do fechamento. **Isso é desenho deliberado, não bug.**

### E. O fechamento é manual ou automático?

Misto, e o dinheiro é sempre manual:

| Etapa | Automático? | Onde |
|---|---|---|
| Detectar exames elegíveis do mês | **sim** | `GET /pastore/fechamentos` → `grupos_elegiveis` |
| Agrupar por unidade + competência | **sim** | mesmo endpoint |
| Criar fechamento e vincular exames | clique único, sem digitar valor | `POST /pastore/fechamentos` |
| **Definir o valor mensal** | **100% manual** | `PATCH .../{id}` — `a_receber` sem valor é recusado |
| **Confirmar o recebimento** | **100% manual** | `POST .../{id}/receber` |
| Criar o lançamento | automático, **só no recebimento** | mesmo endpoint |

### F. Onde fica o valor por exame?

**Em lugar nenhum.** Não existe coluna, constante, arquivo de configuração ou
tabela de preço de exame no sistema. O único lugar onde um valor por exame
poderia estar declarado diz explicitamente que não está
(`app/snapshots.py:585`):

```python
"financeiro_parametros": {
    "valor_exame_sem_broncodilatador": None,
    "valor_exame_com_broncodilatador": None,
    "repasse_percentual_pastore": None,
    "nota": "Valores comerciais não definidos; o sistema não infere.",
}
```

E `GET /pastore/fechamentos` devolve, como contrato,
`"regra_valor": "Não inferido; exige confirmação do gestor."`

### G. Existe preço no `Partnership`?

**Não no sentido de recebimento.** `Partnership` tem `modelo_repasse`,
`percentual_repasse`, `valor_repasse_fixo` (`app/models.py`). São dinheiro que
**SAI** da SoproLife para o parceiro — direção oposta ao recibo do fechamento,
que é `tipo="receita"`. Além disso os três campos são **inertes**: só o
formulário de CRM em `js/m15-nucleo.js` os lê e escreve; nenhum código de
servidor os consome.

### H. Existe preço no `PartnerUnit`?

**Não, e por contrato escrito.** `PartnerUnit` só tem identificação e endereço.
`PartnerUnitConfig` traz o comentário explícito no modelo:

> "NUNCA carrega valor monetário: colunas de preço/repasse/custo da fonte são
> referência bloqueada (M14.2) e ficam fora do modelo por contrato."

### I. Existe regra diferente por unidade?

**Não existe regra alguma**, logo não existe diferença por unidade. A Pastore
tem uma única unidade ativa (`UNI-000002`).

### J. Existe regra diferente com/sem broncodilatador?

**Não.** `exam.broncodilatador` é lido em exatamente três lugares — geração de
laudo (`routers/reports.py`), correção documental (`cli.py`) e contagem
estatística (`snapshots.py`). **Zero ocorrências em qualquer caminho
financeiro.** O par de chaves `valor_exame_com/sem_broncodilatador` existe no
snapshot apenas para declarar que ambos valem `None`.

### K. Achado adicional — segundo caminho de criação de exame, sem financeiro

`POST /espirometrias` (`app/routers/operations.py:207`) cria `SpirometryExam` e
**nunca cria lançamento nenhum**, nem para SoproLife direto. Não há bug de
produção hoje: a interface do painel só cria exame pela Central de Cadastros
(`POST /atendimentos`); essa rota é usada por CLI, importação e testes. Mas é
uma porta por onde um exame pode nascer sem receita sem que nada avise — está
coberta pelo script de reconciliação como classe auditada.

### L. Idempotência hoje existente

Três camadas, todas verificadas no código:

1. `idempotent_create` por `idempotency_key` no exame, na consulta e no recibo
   de fechamento (`app/services/idempotency.py`).
2. Índice único parcial `uq_financial_entries_receita_espirometria` sobre
   `spirometry_exam_id` para `tipo='receita'` e categoria espirometria
   (`app/models.py:826`) — **um exame não consegue ter duas receitas próprias,
   nem por corrida nem por script**.
3. `PartnerSettlementItem.spirometry_exam_id` é `unique=True` — um exame não
   entra em dois fechamentos.

Mais `FinancialEntry.partner_settlement_id` `unique=True`: um fechamento tem
no máximo um recibo.

### M. Jobs / timers

Nenhum job financeiro. Os dois timers systemd do repositório
(`soprolife-operational-refresh.timer`, `soprolife-update-data.timer`) geram
JSON de painel; não escrevem em `financial_entries` nem em
`partner_settlements`.

---

## 5. Arquitetura — onde o preço deve morar

Recomendação mantida e reforçada em relação à M26: **`Partnership`**, com
`PartnerUnit` como exceção posterior, não como base.

Razões objetivas:

1. O que se registra é o **acordo**, e é o acordo que tem status, vigência e
   histórico. `PartnerUnit` é endereço físico.
2. A Pastore tem **uma** unidade ativa. Criar granularidade por unidade agora é
   modelar uma exceção que não existe.
3. Preço por unidade, quando aparecer, é caso raro — cabe em tabela de exceção
   (`partnership_unit_prices`), que **sobrescreve** o valor da parceria. A
   preferência arquitetural do pedido ("PartnerUnit sobrescreve, Partnership é
   default") fica assim atendida, sem pagar o custo de a exceção virar o
   caminho principal antes de existir.

**O que NÃO fazer:** gravar o valor de recebimento em `valor_repasse_fixo`.
Esse campo descreve dinheiro saindo da SoproLife (`docs/parceria-pastore-planilha.md:46`
define `repasse_pastore` como "valor repassado À Pastore", e a linha 130 o soma
em `custo_total`). Reaproveitá-lo inverteria a direção do dinheiro e
contaminaria todo relatório de custo.

Campos sugeridos quando a regra comercial for decidida:

```
partnerships.modelo_recebimento        # 'valor_por_exame' | 'percentual' | 'indefinido'
partnerships.valor_recebido_por_exame  # Numeric(12,2), NULL enquanto indefinido
partnerships.vigencia_inicio           # Date
```

### Fechamento: confirmação do gestor ou automático?

**Opção A — continuar exigindo confirmação, mas sugerir o valor.**

Motivo objetivo, não preferência: a Pastore paga por extrato, e os dois
fechamentos existentes registram por escrito *"Sem data e sem valor de recibo
no extrato — não há recebimento comprovado"*. Um lançamento `Recebido`
automático afirmaria que o dinheiro entrou. Ninguém conferiu que entrou.

O que resolve a dor real ("dezenas de exames feitos sem aparecerem no
Financeiro") não é automatizar o dinheiro — é **nenhum exame ficar órfão de
fechamento**. Esse é o buraco que esta etapa fecha, e ele não depende de preço.

---

## 6. O que esta etapa muda no código

### 6.1 A regra de fechamento deixou de ser privada do endpoint

`_eligible_exams`, `_fechamento_aberto` e `_settlements_da_competencia`
viviam dentro de `app/routers/pastore.py`. O script de reconciliação precisa da
**mesma** regra, e uma segunda implementação seria uma segunda verdade:
bastaria uma divergir para o script criar vínculo que o painel considera
impossível, ou o contrário.

Elas foram para `app/services/pastore.py` como
`eligible_exams`, `open_settlement`, `settlements_of_competency`, mais duas
novas:

* `planned_action(settlements)` → `criar` | `incorporar` | `complementar`.
  O rótulo que o painel mostra antes do clique passa a sair daqui.
* `attach_eligible_exams(db, partner, unit, competencia, observacao)` → faz o
  vínculo e devolve `SettlementAttachment(settlement, exams, acao, sequencia)`.
  **Não decide preço, não cria lançamento, não toca em `valor_total`.**

`POST /pastore/fechamentos` passou a ser uma casca fina sobre essa função. O
contrato HTTP não mudou em nada — os 34 testes de `test_m26_*`, `test_m22_*` e
`test_conciliacao_extra_pastore` passam sem alteração.

### 6.2 `scripts/reconciliar_financeiro_espirometria.py`

Dry-run por padrão; `--apply` exige `--email` de um usuário **ativo** com papel
`admin` ou `gestor`, e a trilha registra esse `user_id`.

O que ele **nunca** faz, por desenho e por teste:

| Nunca | Porque |
|---|---|
| cria `FinancialEntry` para exame Pastore | receita Pastore só existe como recibo agregado do fechamento, com valor confirmado contra o extrato |
| inventa preço para exame próprio | não existe tabela de preço; o valor sempre veio digitado. Exame próprio sem receita é **reportado**, nunca lançado |
| altera/reclassifica/apaga lançamento existente | histórico correto é intocável |
| duplica | a 2ª execução não acha nada elegível e não escreve |

O único efeito de `--apply` é **não monetário**: vincular exames órfãos a um
fechamento que nasce (ou continua) com `valor_total = NULL`. Isso tira o exame
do limbo e o põe na fila onde o gestor digita o valor — sem afirmar nada sobre
quanto a Pastore pagou.

---

## 7. Testes

`nucleo-m15/tests/test_m26_2_financeiro_automatico_espirometria.py` — 13 testes,
cobrindo os 11 pontos pedidos:

| # | Pedido | Teste |
|---|---|---|
| 1 | domiciliar cria exatamente uma LAN | `test_1_domiciliar_soprolife_cria_exatamente_uma_receita` |
| 2 | repetir não duplica | `test_2_repetir_o_mesmo_atendimento_nao_duplica_receita` |
| 3 | Pastore cria/fecha conforme a regra | `test_3_pastore_recusa_receita_no_exame_e_so_cria_no_recebimento` |
| 4 | PartnerUnit aplica override | `test_4_nenhum_preco_vem_de_partner_unit` — prova que **não existe** override |
| 5 | Partnership usa default | `test_5_nenhum_preco_vem_de_partnership` — prova que **não existe** default |
| 6 | com/sem BD respeita a regra vigente | `test_6_broncodilatador_nao_altera_valor_nenhum` |
| 7 | exame cancelado não gera receita | `test_7_exame_cancelado_nao_entra_em_fechamento` |
| 8 | financeiro independe do laudo | `test_8_financeiro_nao_depende_do_laudo` |
| 9 | financeiro independe da assinatura | `test_9_financeiro_nao_depende_da_assinatura_digital` |
| 10 | backfill 2× não cria nada na 2ª | `test_10_backfill_executado_duas_vezes_nao_cria_nada_na_segunda` |
| 11 | LAN-000017 permanece correta | `test_11_reconciliacao_nunca_toca_receita_propria_existente` |
| + | trilha de auditoria do backfill | `test_12_backfill_deixa_trilha_de_auditoria` |
| + | exame próprio sem preço é reportado, não lançado | `test_13_exame_proprio_sem_receita_e_reportado_nunca_lancado` |

Os testes 4 e 5 merecem nota: o pedido presumia que existe override de preço em
`PartnerUnit` e default em `Partnership`. **Não existe nem um nem outro.** Em
vez de escrever um teste para uma funcionalidade inexistente, eles travam o
fato — se alguém amanhã acrescentar um campo de preço a `PartnerUnit`, ou fizer
um caminho financeiro consumir `valor_repasse_fixo`, o teste quebra e obriga a
decisão a passar por revisão.

### 7.1 Prova do script contra uma réplica da FORMA de produção

Banco sintético (nenhum dado real, nenhum nome) com a mesma topologia:
15 exames próprios com receita, 2 fechamentos Pastore já valorados
(jul R$ 219,00 e ago R$ 328,50), 14 exames Pastore órfãos de agosto.

Dry-run classificou:

```
A) com financeiro                      : 15
B) sem financeiro (não-Pastore)        : 0
C) Pastore aguardando fechamento       : 14
D) fechamentos existentes              : 2
E) duplicidades de receita             : 0
F) LAN de espirometria sem ESP         : 0
G) divergências verificáveis           : 0
   receita total no Financeiro         : R$ 3380.00

AÇÕES POSSÍVEIS SEM DECIDIR PREÇO (vínculo, valor fica NULL):
   UNI-000002  2026-08  14 exame(s)  → complementar  (já existem 1)
```

1ª aplicação: `fechamento criado (sequência 2), 14 exame(s), valor_total = NULL`
— C cai para 0, D sobe para 3, **receita total inalterada em R$ 3380,00**.

2ª aplicação: `nada a fazer. Já estava reconciliado.` — D continua 3, receita
continua R$ 3.380,00. **Zero duplicidades criadas.**

---

## 2. Auditoria read-only de produção — 31/08/2026, 23h (-03)

VPS `soprolife-painel-01`, `/opt/soprolife/soprolife-site` @ `fc2a7ac`
(mesmo commit da base local), Alembic head `a3f6b0d94c17`,
`soprolife-m15-api` ativo, `/api/v1/health` → 200.

Só `SELECT`. Nenhum nome de paciente lido nem exibido.

### 2.1 Os 34 exames, exame a exame

| ESP | data | origem | unidade | BD | status | LAN | valor | competência | estado financeiro | fechamento |
|---|---|---|---|---|---|---|---|---|---|---|
| ESP-000001 | 2026-06-01 | SoproLife direto | — | ? | Exame realizado | LAN-000004 | 238,58 | — | com receita | — |
| ESP-000002 | 2026-06-01 | SoproLife direto | — | ? | Exame realizado | LAN-000005 | 238,58 | — | com receita | — |
| ESP-000003 | 2026-06-01 | SoproLife direto | — | ? | Exame realizado | LAN-000006 | 238,58 | — | com receita | — |
| ESP-000004 | 2026-06-01 | SoproLife direto | — | ? | Exame realizado | LAN-000007 | 238,58 | — | com receita | — |
| ESP-000005 | 2026-06-01 | SoproLife direto | — | ? | Exame realizado | LAN-000008 | 238,58 | — | com receita | — |
| ESP-000006 | 2026-06-01 | SoproLife direto | — | ? | Exame realizado | LAN-000009 | 238,58 | — | com receita | — |
| ESP-000007 | 2026-06-01 | SoproLife direto | — | ? | Exame realizado | LAN-000010 | 238,58 | — | com receita | — |
| ESP-000008 | 2026-06-01 | SoproLife direto | — | ? | Exame realizado | LAN-000011 | 238,58 | — | com receita | — |
| ESP-000009 | 2026-06-01 | SoproLife direto | — | ? | Exame realizado | LAN-000012 | 238,58 | — | com receita | — |
| ESP-000010 | 2026-07-01 | SoproLife direto | — | ? | Exame realizado | LAN-000013 | 238,57 | — | com receita | — |
| ESP-000011 | 2026-07-02 | SoproLife direto | — | ? | Exame realizado | LAN-000002 | 219,00 | 2026-07 | com receita | — |
| ESP-000012 | 2026-07-10 | SoproLife direto | — | ? | Realizado | LAN-000001 | 220,00 | 2026-07 | com receita | — |
| **ESP-000013** | 2026-07-14 | **Pastore** | UNI-000002 | com BD | Liberado | — | — | — | por desenho | **2026-07#1 a_receber** |
| **ESP-000014** | 2026-07-18 | **Pastore** | UNI-000002 | com BD | Liberado | — | — | — | por desenho | **2026-07#1 a_receber** |
| ESP-000015 | 2026-07-15 | SoproLife direto | — | ? | Realizado | LAN-000003 | 220,00 | 2026-07 | com receita | — |
| **ESP-000017** | 2026-08-04 | **Pastore** | UNI-000002 | com BD | Realizado | — | — | — | por desenho | **2026-08#1 a_receber** |
| **ESP-000018** | 2026-08-04 | **Pastore** | UNI-000002 | com BD | Realizado | — | — | — | por desenho | **2026-08#1 a_receber** |
| **ESP-000019** | 2026-08-01 | **Pastore** | UNI-000002 | com BD | Realizado | — | — | — | por desenho | **2026-08#1 a_receber** |
| ESP-000025 | 2026-08-15 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000026 | 2026-08-15 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000027 | 2026-08-15 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000028 | 2026-08-15 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000029 | 2026-08-15 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000030 | 2026-08-18 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000031 | 2026-08-22 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000032 | 2026-08-22 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000033 | 2026-08-22 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000034 | 2026-08-25 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000035 | 2026-08-25 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000036 | 2026-08-25 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000037 | 2026-08-25 | Pastore | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |
| ESP-000038 | 2026-08-26 | SoproLife direto | — | com BD | Realizado | LAN-000016 | 220,00 | 2026-08 | com receita | — |
| **ESP-000039** | 2026-08-28 | **SoproLife direto** | — | com BD | Realizado | **LAN-000017** | **230,00** | 2026-08 | **com receita** | — |
| **ESP-000040** | 2026-08-29 | **Pastore** | UNI-000002 | com BD | Realizado | — | — | — | **órfão** | **nenhum** |

Chave de idempotência: `NULL` em todos os 34 — nenhum atendimento foi criado
com `idempotency_key`. A proteção contra receita dupla que **está** valendo é
o índice parcial único `uq_financial_entries_receita_espirometria`, não a
chave.

### 2.2 Classificação pedida

| Grupo | Qtd | Detalhe |
|---|---|---|
| **A) exames com financeiro correto** | **15** | todos SoproLife direto; total R$ 3.494,79 |
| **B) exames sem financeiro** | **0** (não-Pastore) | nenhum exame próprio órfão de receita |
| **C) Pastore aguardando fechamento** | **14** | ESP-000025..037 + ESP-000040 |
| **D) fechamentos já concluídos** | **0 recebidos** | 2 existem, ambos `a_receber` sem recibo |
| **E) possíveis duplicidades** | **0** | nenhum exame com mais de uma receita |
| **F) LAN sem ESP correspondente** | **0** | nenhuma receita de espirometria solta |
| **G) divergências de valor** | **0** | nenhum recibo diverge; nenhuma competência diverge |

Fora do escopo, registrado: **CON-000001** (teleconsulta 01/06/2026,
"Consulta realizada") não tem lançamento nenhum — nem receita bruta nem
repasse médico. É a única consulta do sistema.

Observação benigna: **LAN-000014 e LAN-000015 não existem.** Não é lançamento
apagado — é número de sequência consumido por uma alocação que não chegou a
commitar. `allocate_public_code` não devolve número ao contador. Nenhum
impacto financeiro; registrado para não virar susto numa auditoria futura.

### 2.3 Os dois fechamentos existentes

| Competência | Seq | Unidade | Status | Valor | Itens | Recibo | Observação |
|---|---|---|---|---|---|---|---|
| 2026-07 | 1 | UNI-000002 | `a_receber` | R$ 219,00 | 2 | **nenhum** | "Extrato Pastore fornecido pelo gestor em 11/08/2026. Valor documentado…" |
| 2026-08 | 1 | UNI-000002 | `a_receber` | R$ 328,50 | 3 | **nenhum** | idem |

### 2.4 Linha do tempo

| Quando | O quê |
|---|---|
| 23/07 23:48 | `lancamento.criado` LAN-000003 |
| 25/07 00:43 | lote M18 de conciliação histórica — LAN-000004 a LAN-000013, marcador `m18_conciliacao_lote` |
| **11/08 23:33** | `pastore.fechamento_criado` 2026-07, 2 exames |
| **11/08 23:34** | `pastore.fechamento_criado` 2026-08, **3 exames** — e os dois `fechamento_atualizado` para `a_receber` com valor |
| 15/08 – 25/08 | mutirão Pastore: 13 exames realizados, **nenhum evento `pastore.*`** |
| 26/08 | ESP-000038 → LAN-000016 (R$ 220,00) |
| **28/08** | **ESP-000039 → LAN-000017 (R$ 230,00)** — último lançamento automático |
| 29/08 | ESP-000040 (Pastore) realizado — órfão |
| 29/08 19:40 | `lancamento.atualizado` LAN-000017 → `Recebido` (ajuste manual do operador) |
| **desde 11/08 23:34** | **nenhum evento `pastore.*` na trilha** |

**Quando o Financeiro "parou":** ele não parou. O último lançamento automático
normal é **LAN-000017, de 28/08**, e ele funcionou. O que parou foi a operação
Pastore, em **11/08 às 23:34** — desde então nenhum fechamento foi criado,
atualizado ou recebido, e os 14 exames feitos depois disso não tinham para
onde ir.

### 2.5 Valor ainda não refletido no Financeiro

| Origem | Exames | Valor | Situação |
|---|---|---|---|
| Fechamento 2026-07 `a_receber` | 2 | **R$ 219,00** | valor confirmado, recibo nunca registrado |
| Fechamento 2026-08 `a_receber` | 3 | **R$ 328,50** | idem |
| 14 exames Pastore órfãos | 14 | **sem valor** | não há regra cadastrada — ver seção 4 |
| CON-000001 | — | desconhecido | fora do escopo |
| **Total com valor conhecido** | **5** | **R$ 547,50** | |

Os R$ 547,50 estão fora do Financeiro por decisão de desenho, não por defeito:
o recibo só nasce quando alguém confirma que o dinheiro entrou, e as duas
observações dizem literalmente *"Sem data e sem valor de recibo no extrato —
não há recebimento comprovado."*

Para os 14 órfãos, **este relatório não afirma valor nenhum.** Se valesse a
taxa histórica de R$ 109,50/exame seriam R$ 1.533,00, mas essa taxa nunca foi
cadastrada como regra — ver seção 4.

Financeiro hoje: **15 lançamentos, R$ 3.494,79**, 100% receita de espirometria
própria.

---

## 3. Os 14 exames Pastore aguardando fechamento

| ESP | data | unidade | BD | fechamento | LAN | valor conhecido | motivo pendente |
|---|---|---|---|---|---|---|---|
| ESP-000025 | 2026-08-15 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | competência sem fechamento aberto |
| ESP-000026 | 2026-08-15 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem |
| ESP-000027 | 2026-08-15 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem |
| ESP-000028 | 2026-08-15 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem |
| ESP-000029 | 2026-08-15 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem |
| ESP-000030 | 2026-08-18 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem |
| ESP-000031 | 2026-08-22 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem |
| ESP-000032 | 2026-08-22 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem |
| ESP-000033 | 2026-08-22 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem |
| ESP-000034 | 2026-08-25 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem |
| ESP-000035 | 2026-08-25 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem |
| ESP-000036 | 2026-08-25 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem |
| ESP-000037 | 2026-08-25 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem |
| **ESP-000040** | 2026-08-29 | UNI-000002 | com BD | nenhum | nenhum | **nenhum** | idem — **Terezinha** |

Todos: mesma unidade, mesma competência, todos `Realizado`, todos com
broncodilatador, todos elegíveis por `is_completed_pastore_exam`.

### 3.1 Qual das hipóteses é a verdadeira

| Hipótese | Veredito |
|---|---|
| preço não configurado | **verdadeiro, mas não é a causa do travamento** — nenhum fechamento precisa de preço para nascer |
| preço configurado mas fechamento não executado | falso — não há preço configurado |
| fechamento exige confirmação manual | **verdadeiro** — o fechamento é um clique do gestor, e ninguém clicou desde 11/08 |
| bug que impede o fechamento | **era verdadeiro até a M26** — competência ocupada devolvia 409 e não havia rota alternativa. **Corrigido e em produção** desde 30/08 (`22f5908`, coluna `sequencia`, head `a3f6b0d94c17`) |
| fechamento concluído sem gerar LAN | falso — nenhum fechamento foi concluído; os 2 existentes estão em `a_receber` |
| arquitetura intencional | **verdadeiro para o LAN**: exame Pastore nunca vira receita individual, por desenho |

**A causa hoje é a soma de duas coisas:** o defeito estrutural já está
corrigido em produção, mas **ninguém exerceu a correção** — o botão
"Criar fechamento complementar 2" existe e funciona desde 30/08 e nunca foi
clicado. É exatamente isso que esta etapa executa, sem tocar em valor.

---

## 4. Regra comercial — a ambiguidade é real e continua

Verificado registro a registro em produção, hoje:

| Objeto | Valor |
|---|---|
| Parceria `PAR-000001` (Pastore) | `status = em_negociacao` |
| `modelo_repasse` | `indefinido` |
| `percentual_repasse` | `NULL` |
| `valor_repasse_fixo` | `NULL` |
| `data_inicio` | `NULL` |
| `partner_transfers` | **0 linhas** |
| `GET /pastore/fechamentos` → `regra_valor` | "Não inferido; exige confirmação do gestor." |

**R$ 109,50/exame é fato histórico de dois extratos, nunca regra cadastrada:**

* 2026-07: 219,00 = 2 × 109,50
* 2026-08: 328,50 = 3 × 109,50

E os dois fechamentos que usaram essa taxa **continuam sem recebimento
comprovado** — nem eles confirmaram a taxa na prática.

### 4.1 Portanto: PARO AQUI, e só aqui

Conforme combinado, **nenhum valor foi digitado, gravado ou inferido para os
14 exames.** A tabela para você decidir:

| Competência | Unidade | Exames | Se R$ 109,50/exame | Decisão necessária |
|---|---|---|---|---|
| 2026-08 (complementar 2) | UNI-000002 | **14** | R$ 1.533,00 | **valor mensal a confirmar** |

Quatro perguntas que só você responde:

1. **R$ 109,50 por exame continua valendo para agosto/2026?**
2. **Muda alguma coisa por serem 14 num mês só** (escala, desconto)?
3. **O broncodilatador altera o valor?** Os 14 são "com BD" — mas os 5 já
   fechados também eram, então o extrato histórico não separa. O código não
   distingue em lugar nenhum.
4. Os 14 entram como **complementar de agosto** (recomendado — a competência é
   a do exame) ou você prefere jogá-los para setembro?

E duas pendências antigas que continuam abertas:

5. Os fechamentos 2026-07 (R$ 219,00) e 2026-08#1 (R$ 328,50) **já podem ser
   marcados como recebidos?** Se o dinheiro entrou, o recibo os põe no
   Financeiro imediatamente (+R$ 547,50). Se não entrou, ficam como estão.

---

## 8. Execução — o que foi feito em produção

| Etapa | Resultado |
|---|---|
| Suíte completa local | **1506 passed, 30 skipped** em 8m39s |
| Falha conhecida deselecionada | `test_rubrica_real_nao_esta_versionada` — pré-existente desde a M25.21, falso positivo do filtro por nome sobre screenshots sintéticos |
| Quality gate | **PASSOU**, todos os checks |
| Commit | `0c86893` |
| Integração | `git merge --ff-only` em `painel-soprolife-v01`, `fc2a7ac..0c86893` |
| **Backup antes da escrita** | `/opt/soprolife/backups/m26-2-pre-20260831-230457.dump` (377 KB, `pg_dump -Fc`) |
| Deploy | `git merge --ff-only origin/painel-soprolife-v01` na VPS → `0c86893` |
| **Migração** | **nenhuma** — Alembic já em `a3f6b0d94c17` (head), a M26.2 não tem DDL |
| Restart | `systemctl restart soprolife-m15-api` → `active` |
| Health | `GET /api/v1/health` → **200**, zero warnings no journal |
| Dry-run em produção | idêntico à auditoria SQL: A=15, B=0, C=14, D=2, E=0, F=0, G=0 |
| `--apply` | fechamento **2026-08 complementar 2** criado, 14 exames, `valor_total = NULL` |
| 2ª execução do `--apply` | *"nada a fazer. Já estava reconciliado."* |

### 8.1 Estado do painel depois (chamada real ao endpoint)

```
indicadores: {"aguardando_fechamento": 0, "fechamento_em_aberto": 1,
              "a_receber": 2, "recebido": 0,
              "valor_a_receber_confirmado": "547.50", "valor_recebido": "0.00"}
grupos_elegiveis: []
regra_valor: Não inferido; exige confirmação do gestor.

  Fechamento 2026-08 — complementar 2   status=incluido   valor=None    itens=14  recibo=None
  Fechamento 2026-08                    status=a_receber  valor=328.50  itens= 3  recibo=None
  Fechamento 2026-07                    status=a_receber  valor=219.00  itens= 2  recibo=None
```

**"Aguardando fechamento mensal" caiu de 14 para 0.**

---

## 9. Prova final

### 9.1 Exame a exame — antes e depois

| ESP | origem | unidade | regra | valor | LAN antes | ação | LAN depois |
|---|---|---|---|---|---|---|---|
| ESP-000001..010 | SoproLife direto | — | valor digitado | 238,58 / 238,57 | LAN-000004..013 | **nenhuma** | LAN-000004..013 |
| ESP-000011 | SoproLife direto | — | valor digitado | 219,00 | LAN-000002 | **nenhuma** | LAN-000002 |
| ESP-000012 | SoproLife direto | — | valor digitado | 220,00 | LAN-000001 | **nenhuma** | LAN-000001 |
| ESP-000015 | SoproLife direto | — | valor digitado | 220,00 | LAN-000003 | **nenhuma** | LAN-000003 |
| ESP-000038 | SoproLife direto | — | valor digitado | 220,00 | LAN-000016 | **nenhuma** | LAN-000016 |
| **ESP-000039** | **SoproLife direto** | — | valor digitado | **230,00** | **LAN-000017** | **nenhuma** | **LAN-000017** |
| ESP-000013, 000014 | Pastore | UNI-000002 | recibo no fechamento | — | nenhum | **nenhuma** | nenhum (fech. 2026-07#1) |
| ESP-000017, 018, 019 | Pastore | UNI-000002 | recibo no fechamento | — | nenhum | **nenhuma** | nenhum (fech. 2026-08#1) |
| ESP-000025..037 (13) | Pastore | UNI-000002 | recibo no fechamento | **a definir** | nenhum | **vinculado ao fech. 2026-08#2** | nenhum — por desenho |
| **ESP-000040** | **Pastore** | UNI-000002 | recibo no fechamento | **a definir** | nenhum | **vinculado ao fech. 2026-08#2** | nenhum — por desenho |

### 9.2 Resumo numérico

| Métrica | Valor |
|---|---|
| Total de exames auditados | **34** |
| Total correto antes | **15** com receita + **5** Pastore já em fechamento = **20** |
| Total faltante antes | **14** (exames Pastore órfãos de fechamento) |
| Valor faltante | **R$ 0,00 lançável** — não há preço cadastrado. Se valesse a taxa histórica de R$ 109,50 seriam R$ 1.533,00, **mas o sistema não afirma isso** |
| Pastore aguardando fechamento (antes) | **14** |
| Pastore aguardando fechamento (depois) | **0** |
| Lançamentos financeiros criados | **0** |
| Valor acrescentado ao Financeiro | **R$ 0,00** |
| Duplicidades encontradas | **0** |
| **Duplicidades criadas** | **ZERO** |
| Total financeiro antes | 15 lançamentos, **R$ 3.494,79** |
| **Total financeiro depois** | 15 lançamentos, **R$ 3.494,79** — inalterado |
| Fechamentos antes / depois | 2 / **3** |
| Itens de fechamento antes / depois | 5 / **19** |

### 9.3 As três respostas

> **"NO FLUXO SOPROLIFE DIRETO, O EVENTO `POST /atendimentos` (o cadastro do
> atendimento com o bloco financeiro preenchido) CRIA O LANÇAMENTO
> FINANCEIRO."**
>
> Na mesma transação do exame, via `_criar_financeiro`
> (`app/routers/attendances.py:505`). O valor é o **digitado** pelo operador —
> o servidor não consulta tabela de preço nenhuma.

> **"NO FLUXO PASTORE, O EVENTO `POST /pastore/fechamentos/{id}/receber` (a
> confirmação do recebimento pelo gestor) CRIA O LANÇAMENTO FINANCEIRO."**
>
> É o único ponto do código que cria receita no domínio Pastore, e ele exige
> três coisas digitadas: valor confirmado, data de recebimento e forma de
> pagamento. O exame Pastore **nunca** gera recebível individual — tentar
> mandar bloco financeiro num atendimento Pastore é erro 422
> (`pagamento_direto_pastore_proibido`).

> **"OS VALORES ESTAVAM PARADOS PORQUE O FECHAMENTO DE AGOSTO FOI CRIADO EM
> 11/08 COM OS 3 EXAMES QUE EXISTIAM E JÁ RECEBEU VALOR CONFERIDO; OS 14
> EXAMES FEITOS DEPOIS (15–29/08) FICARAM SEM ROTA PARA ENTRAR EM FECHAMENTO
> NENHUM — E, DEPOIS QUE A M26 CORRIGIU ISSO EM 30/08, NINGUÉM CHEGOU A USAR A
> CORREÇÃO."**
>
> Duas coisas separadas, que é o que confundia:
> **(a)** o travamento estrutural — corrigido na M26, exercido agora;
> **(b)** o preço — **nunca existiu regra cadastrada**, e continua não
> existindo. A parceria `PAR-000001` segue `em_negociacao` /
> `modelo_repasse = indefinido`.
>
> O Financeiro **não estava parado**: o último lançamento automático normal é
> LAN-000017, de 28/08, e funcionou perfeitamente.

### 9.4 Estado final provado

**ESP-000039 / LAN-000017** — intacta, conferida por `SELECT` depois do deploy:

```
LAN-000017 | 230.00 | Recebido | Espirometria | comp 2026-08-28 | receb 2026-08-29 | Pix | ESP-000039
```

**ESP-000040 (Terezinha)**:

```
ESP-000040 | 2026-08-29 | Realizado | fechamento 2026-08#2 | incluido | valor_total NULL
```

**Todos os Pastore pendentes**: os 14 estão agora no fechamento
**2026-08 complementar 2**, `status = incluido`, `valor_total = NULL`,
sem recibo. Órfãos restantes: **0**.

Trilha da escrita (única linha nova em `audit_logs`):

```
2026-08-31 23:10 | pastore.fechamento_criado |
  {"status":"incluido","total":14,"sequencia":2,
   "motivo":"reconciliacao_financeiro_espirometria_m26_2"}
```

---

## 10. O que continua na sua mão

Esta etapa **parou antes de qualquer escrita de valor**, como combinado. O que
falta é decisão comercial, não técnica:

1. **Valor do fechamento 2026-08 complementar 2 (14 exames).** Hoje ele está
   `incluido` com `valor_total = NULL`. Assim que você decidir, é um
   `PATCH` no painel — o botão já está lá.
2. **Os fechamentos 2026-07 (R$ 219,00) e 2026-08#1 (R$ 328,50)** seguem
   `a_receber` sem recibo. Se o dinheiro entrou, registrar o recebimento põe
   **+R$ 547,50** no Financeiro imediatamente.
3. **Cadastrar a regra comercial**, se ela existir: campos sugeridos na
   seção 5 (`partnerships.modelo_recebimento`,
   `valor_recebido_por_exame`, `vigencia_inicio`). Não foram criados aqui —
   sem valor decidido seriam mobília vazia; com valor decidido seria inventar
   preço.
4. **CON-000001** (teleconsulta de 01/06/2026, "Consulta realizada") não tem
   lançamento nenhum. Fora do escopo desta etapa; fica no radar.

O que **não** precisa mais da sua mão: nenhum exame Pastore volta a ficar
órfão. O caminho existe, está testado, está em produção, e o script de
reconciliação detecta e fecha a lacuna sempre que ela reaparecer — sem tocar
em dinheiro.
