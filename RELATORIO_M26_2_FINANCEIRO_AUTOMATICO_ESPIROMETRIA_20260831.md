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
