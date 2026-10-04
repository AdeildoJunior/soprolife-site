# Auditoria do Financeiro automático da espirometria — 30/08/2026

Branch de trabalho: `claude-m26-auditoria-financeiro-espirometria`
Base: `origin/painel-soprolife-v01` @ `67ddcd3`
Produção auditada: VPS `soprolife-painel-01`, `/opt/soprolife/soprolife-site` @ `67ddcd3`
(mesmo commit — código local e produção estão idênticos)

Toda a coleta desta primeira parte foi **somente leitura** (`SELECT` no Postgres
de produção). Nenhum lançamento, fechamento ou preço foi criado ou alterado.
Nenhum nome de paciente aparece neste documento — só códigos públicos.

---

## 1. Resposta curta

O Financeiro **não está parado**. Ele está fazendo exatamente o que foi
programado, e o buraco está num ponto só:

> **Os 14 exames Pastore não estão "aguardando fechamento" por falta de preço.
> Eles estão presos porque a competência 2026-08 já tem um fechamento criado, e
> o sistema só admite UM fechamento por (parceiro, unidade, competência).
> O botão "Criar fechamento" do painel devolve HTTP 409 e não há nenhum outro
> caminho — na API ou na interface — para anexar exames a um fechamento que já
> existe.**

Isso é um defeito estrutural, independente de preço, e pode ser corrigido sem
decidir nenhum valor comercial.

A questão do preço é **separada e continua em aberto** (seção 6).

---

## 2. O que o achado da Vanessa realmente prova (e o que não prova)

`ESP-000039` (domiciliar SoproLife, 28/08) gerou de fato `LAN-000017`,
R$ 230,00, na mesma transação do cadastro. O mesmo aconteceu com `ESP-000038`
(cowork, 26/08) → `LAN-000016`, R$ 220,00.

Mas o mecanismo **não é precificação automática**. Em
`nucleo-m15/app/routers/attendances.py:505` (`_criar_financeiro`):

```python
if payload.tipo == TIPO_PASTORE:
    ...
    return []
if fin is None:
    return []
```

O lançamento só nasce porque o operador **digitou o bloco `financeiro`** no
formulário de atendimento. O servidor não consulta tabela de preço nenhuma —
ele grava o valor que veio no payload. O que é automático é a **ligação**
(exame ↔ lançamento, na mesma transação, com índice único impedindo receita
duplicada), não o **valor**.

Trilha que confirma: não existe evento `lancamento.criado` para LAN-000016 nem
LAN-000017 (esse evento só existe no lote histórico M18 de 25/07). Existe um
`lancamento.atualizado` em 29/08 19:40 sobre LAN-000017
(`campos: [status, data_recebimento, forma_pagamento]`) — foi o operador
marcando "Recebido" depois, à mão, exatamente como a guarda
"Recebido exige data de recebimento" obriga.

**Conclusão:** o fluxo SoproLife direto está íntegro. Nada a mexer nele, como
pedido. Só é preciso não chamá-lo de "preço automático" — ele é
"lançamento automático a partir de valor digitado".

---

## 3. Os 14 exames Pastore aguardando fechamento

Todos em `UNI-000002 — Pastore Ipanema`, todos `status = Realizado`, todos
**com broncodilatador**, todos na competência **2026-08**, todos **sem nenhum
lançamento financeiro** e **sem vínculo com fechamento**.

| # | ESP | Data | Unidade | BD | Responsável | Lançamento | Laudo | Estado |
|---|-----|------|---------|----|-------------|-----------|-------|--------|
| 1 | ESP-000025 | 2026-08-15 | UNI-000002 | com BD | FAUSTINO | nenhum | LAU-000014 liberado | Aguardando fechamento mensal |
| 2 | ESP-000026 | 2026-08-15 | UNI-000002 | com BD | FAUSTINO | nenhum | LAU-000011 liberado | Aguardando fechamento mensal |
| 3 | ESP-000027 | 2026-08-15 | UNI-000002 | com BD | FAUSTINO | nenhum | LAU-000012 liberado | Aguardando fechamento mensal |
| 4 | ESP-000028 | 2026-08-15 | UNI-000002 | com BD | Adeildo | nenhum | LAU-000013 liberado | Aguardando fechamento mensal |
| 5 | ESP-000029 | 2026-08-15 | UNI-000002 | com BD | Adeildo | nenhum | LAU-000010 liberado | Aguardando fechamento mensal |
| 6 | ESP-000030 | 2026-08-18 | UNI-000002 | com BD | FAUSTINO | nenhum | LAU-000015 liberado | Aguardando fechamento mensal |
| 7 | ESP-000031 | 2026-08-22 | UNI-000002 | com BD | FAUSTINO | nenhum | LAU-000016 liberado | Aguardando fechamento mensal |
| 8 | ESP-000032 | 2026-08-22 | UNI-000002 | com BD | FAUSTINO | nenhum | LAU-000017 liberado | Aguardando fechamento mensal |
| 9 | ESP-000033 | 2026-08-22 | UNI-000002 | com BD | FAUSTINO | nenhum | LAU-000018 liberado | Aguardando fechamento mensal |
| 10 | ESP-000034 | 2026-08-25 | UNI-000002 | com BD | FAUSTINO | nenhum | LAU-000022 liberado | Aguardando fechamento mensal |
| 11 | ESP-000035 | 2026-08-25 | UNI-000002 | com BD | FAUSTINO | nenhum | LAU-000021 liberado | Aguardando fechamento mensal |
| 12 | ESP-000036 | 2026-08-25 | UNI-000002 | com BD | FAUSTINO | nenhum | LAU-000020 liberado | Aguardando fechamento mensal |
| 13 | ESP-000037 | 2026-08-25 | UNI-000002 | com BD | FAUSTINO | nenhum | LAU-000019 liberado | Aguardando fechamento mensal |
| 14 | ESP-000040 | 2026-08-29 | UNI-000002 | com BD | Adeildo | nenhum | LAU-000025 atribuído | Aguardando fechamento mensal |

Valor: **nenhum**, em nenhum dos 14. Por desenho — o exame Pastore nunca é um
recebimento (seção 5).

Os 13 primeiros vieram do mutirão de 15 a 25/08; o 14º é a Terezinha, de ontem.

---

## 4. Por que estão aguardando fechamento — a causa exata

Estado atual dos fechamentos Pastore em produção:

| Competência | Unidade | Itens | Exames | Valor | Status | Recibo financeiro |
|---|---|---|---|---|---|---|
| 2026-07 | UNI-000002 | 2 | ESP-000013, ESP-000014 | R$ 219,00 | `a_receber` | **nenhum** |
| 2026-08 | UNI-000002 | 3 | ESP-000017, ESP-000018, ESP-000019 | R$ 328,50 | `a_receber` | **nenhum** |

Ambos criados em **11/08/2026 23:33–23:34** (`pastore.fechamento_criado`), a
partir do extrato Pastore entregue pelo gestor. Desde então **não houve mais
nenhum evento `pastore.*`** na trilha de auditoria — nem criação, nem
atualização, nem recebimento.

O fechamento de agosto foi criado **no dia 11**, quando só existiam 3 exames de
agosto. Os outros 14 chegaram entre 15 e 29/08. E aí:

**Bloqueio nº 1 — competência ocupada** (`app/routers/pastore.py:277`):

```python
duplicate = db.execute(select(PartnerSettlement).where(
    PartnerSettlement.partner_id == partner.id,
    PartnerSettlement.partner_unit_id == unit.id,
    PartnerSettlement.competencia == competencia,
)).scalar_one_or_none()
if duplicate is not None:
    raise _erro("fechamento_mensal_duplicado",
                "Já existe fechamento para esta unidade e competência.", 409)
```

Reforçado no banco por `UniqueConstraint(partner_id, partner_unit_id,
competencia)` (`app/models.py:704`). O painel mostra o grupo
"Pastore Ipanema · Competência 2026-08 · 14 exame(s)" com o botão
**Criar fechamento** (`js/pastore-settlement.js:120`); clicar devolve 409 e a
mensagem de erro na faixa de status. Não há mais nada a fazer na tela.

**Bloqueio nº 2 — logo atrás do primeiro** (`app/routers/pastore.py:296`):
`_eligible_exams()` seleciona **todos** os exames concluídos do mês, inclusive
os 3 que já pertencem ao fechamento de agosto, e então rejeita o conjunto
inteiro com `exame_em_outro_fechamento` (409). Ou seja: mesmo que a constraint
de competência fosse relaxada, o segundo portão ainda barraria.

**Bloqueio nº 3 — não existe caminho alternativo.** A API Pastore tem quatro
rotas: `GET configuracao-atendimento`, `GET fechamentos`,
`POST fechamentos`, `PATCH fechamentos/{id}`, `POST fechamentos/{id}/receber`.
`PastoreSettlementUpdate` aceita apenas `status`, `valor_total`, `data_envio`,
`observacao` (`app/schemas.py:718`). **Nenhuma rota adiciona exames a um
fechamento existente.** Não é um caso não implementado na interface: não existe
no servidor.

Não é falta de preço, não é permissão (o operador é gestor), não é status de
exame (todos "Realizado", todos elegíveis por `is_completed_pastore_exam`), não
é laudo (13 de 14 já liberados). **É a competência ocupada.**

---

## 5. A regra comercial gravada hoje para a Pastore

**Não existe regra comercial cadastrada.** Verificado registro a registro:

| Objeto | Valor em produção |
|---|---|
| Parceiro canônico | `CLI-000002` Pastore, `status = ativa` |
| Parceria `PAR-000001` | `status = em_negociacao` |
| `modelo_repasse` | `indefinido` |
| `percentual_repasse` | `NULL` |
| `valor_repasse_fixo` | `NULL` |
| `data_inicio` | `NULL` |
| `partner_unit_configs` (UNI-000002) | só agenda: Terça e Sábado, 08:00–12:00 |
| `partner_transfers` | 0 linhas |
| `GET /pastore/fechamentos` → `regra_valor` | `"Não inferido; exige confirmação do gestor."` |

O valor de **R$ 109,50 por exame existe apenas como fato histórico**, deduzido
das observações dos dois fechamentos criados em 11/08:

- 2026-07: `219,00 = 2 × 109,50`
- 2026-08: `328,50 = 3 × 109,50`

Ambas as observações dizem literalmente *"Valor documentado de repasse... Sem
data e sem valor de recibo no extrato — não há recebimento comprovado."*
Nunca foi registrado como regra em lugar nenhum do sistema.

---

## 6. Onde o preço deveria morar: `Partnership`, não `PartnerUnit`

E, hoje, **não pode morar em nenhum dos dois** — o campo não existe.

Os três campos de dinheiro da `Partnership` (`modelo_repasse`,
`percentual_repasse`, `valor_repasse_fixo`) descrevem dinheiro que **SAI** da
SoproLife para o terceiro. Isso é explícito no código e na documentação:

- `docs/parceria-pastore-planilha.md:46` define `repasse_pastore` como
  "valor repassado À Pastore" e a linha 130 o soma em `custo_total`;
- `PartnerReferral` tem `valor_recebido` **e** `valor_repasse` como colunas
  separadas — a direção importa;
- `repasse_medico_valor`, em atendimentos, é o que a SoproLife **paga** ao médico;
- já o recibo de fechamento nasce como
  `tipo="receita", categoria="Recebimento de parceiro"` — **ENTRADA**.

Gravar R$ 109,50 em `valor_repasse_fixo` **inverteria a direção do dinheiro** e
contaminaria qualquer relatório de custo. Não faça isso.

Além disso, esses três campos são **inertes**: só o formulário de CRM em
`js/m15-nucleo.js` os lê e escreve. Nenhum código de servidor os consome.
Preenchê-los não automatiza fechamento nenhum.

**Recomendação de onde colocar quando houver decisão comercial:**

`Partnership` — porque o que se está registrando é o **acordo**, e o acordo é
que tem vigência, status e histórico. `PartnerUnit` é endereço físico; preço por
endereço é exceção, não regra, e a Pastore tem uma unidade ativa só.

Campos novos sugeridos (nomes que declaram a direção, sem reaproveitar os de
repasse):

```
partnerships.modelo_recebimento        # 'valor_por_exame' | 'percentual' | 'indefinido'
partnerships.valor_recebido_por_exame  # Numeric(12,2), NULL enquanto indefinido
partnerships.vigencia_inicio           # Date — desde quando esse valor vale
```

Se um dia uma unidade tiver preço próprio, isso vira uma tabela de exceção
(`partnership_unit_prices`) — **não** agora.

Esta etapa **não** cria esses campos: criá-los sem o valor decidido seria mobília
vazia, e com o valor decidido seria inventar preço. Fica para a etapa em que
você fechar a regra.

---

## 7. O fechamento depende de digitação manual? Sim — em dois pontos

| Etapa | Automático? | Onde |
|---|---|---|
| Detectar exames elegíveis do mês | **Sim**, automático | `GET /pastore/fechamentos` → `grupos_elegiveis` |
| Agrupar por unidade + competência | **Sim**, automático | mesmo endpoint |
| Criar o fechamento e vincular os exames | Botão único, sem digitação | `POST /pastore/fechamentos` |
| **Definir o valor mensal** | **100% manual** | `PATCH .../{id}` com `valor_total` — recusa `a_receber` sem valor |
| **Confirmar o recebimento** | **100% manual** | `POST .../{id}/receber` com valor + data + forma |
| Criar o lançamento financeiro | Automático, **mas só no recebimento** | mesmo endpoint |

Ou seja: **nenhum FinancialEntry Pastore nasce antes de um humano confirmar
valor, data e forma de pagamento efetivamente recebido.** Isso é desenho
deliberado, não bug — o exame Pastore não é um recebimento.

O efeito colateral é que os dois fechamentos em `a_receber` (R$ 547,50) **não
aparecem no Financeiro** e não vão aparecer até alguém registrar o recibo.

---

## 8. Quanto dinheiro não está aparecendo no Financeiro

| Origem | Qtd | Valor | Por quê |
|---|---|---|---|
| 14 exames Pastore ago/2026 sem fechamento | 14 | **R$ 1.533,00** ⚠️ | Bloqueio da seção 4. Valor calculado a 109,50 — **taxa histórica, não regra cadastrada** |
| Fechamento 2026-07 `a_receber` | 2 exames | R$ 219,00 | Valor confirmado, recibo nunca registrado |
| Fechamento 2026-08 `a_receber` | 3 exames | R$ 328,50 | idem |
| **Total Pastore fora do Financeiro** | **19 exames** | **R$ 2.080,50** | |
| CON-000001 (teleconsulta 01/06/2026) | 1 | valor desconhecido | Consulta "realizada" sem nenhum lançamento |

⚠️ **Os R$ 1.533,00 são uma projeção pela taxa histórica de R$ 109,50, não um
número que o sistema afirma.** Ver seção 10.

Para contexto: o Financeiro tem hoje 15 lançamentos somando **R$ 3.494,79**,
todos receita de espirometria própria. Se os 19 exames Pastore entrassem à taxa
histórica, o Financeiro cresceria ~60%.

---

## 9. Outros exames fora da Pastore sem financeiro

**Nenhum.** Os 17 exames sem parceiro têm todos lançamento vinculado:

- ESP-000001 a ESP-000012 e ESP-000015 → LAN-000001 a LAN-000013 (lote de
  conciliação histórica M18, 25/07)
- ESP-000038 → LAN-000016 (R$ 220,00)
- ESP-000039 → LAN-000017 (R$ 230,00)

Não há exame próprio órfão de receita. **O fluxo direto está limpo.**

Uma pendência não-Pastore encontrada: **CON-000001**, teleconsulta de
01/06/2026 com status "Consulta realizada", não tem lançamento nenhum — nem
receita bruta, nem repasse médico. É a única consulta do sistema. Fica
registrada aqui; não é do escopo desta correção.

### 9.1 Defeito secundário: painel de conciliação extra-Pastore com pendente negativo

`GET /financeiro/conciliacao/extra-pastore` compara um alvo histórico
congelado (`TOTAL_ALVO_EXTRA_PASTORE = 3044.79`, de 13 exames de jun–jul) com
a soma de **todos** os exames sem parceiro (`_exames_extra_pastore` não tem
recorte temporal). Com ESP-000038 e ESP-000039 no ar:

```
total_vinculado = 3.494,79
total_pendente  = 3.044,79 − 3.494,79 = −450,00
```

O painel exibe **pendente de −R$ 450,00**. O alvo histórico foi 100%
conciliado em 25/07; os exames novos simplesmente não pertencem a ele. Corrigido
nesta etapa (seção 11) sem mexer no alvo, que é fato histórico.

---

## 10. O que precisa da sua decisão — e só isso

O valor a aplicar aos 14 exames é uma **dúvida real**, não uma lacuna técnica:

- a parceria está `em_negociacao`, `modelo_repasse = indefinido`;
- o sistema declara por escrito que não infere (`regra_valor`);
- R$ 109,50 saiu de dois extratos, não de um acordo cadastrado;
- e os dois fechamentos que usaram esse valor continuam **sem recebimento
  comprovado** — ou seja, nem eles confirmaram a taxa na prática.

Por isso, conforme combinado, **paro aqui neste ponto**:

| Competência | Unidade | Exames | Se R$ 109,50/exame | Decisão necessária |
|---|---|---|---|---|
| 2026-08 (complementar) | UNI-000002 | 14 | R$ 1.533,00 | **valor mensal a confirmar** |

Perguntas que só você responde:

1. R$ 109,50 por exame continua valendo para agosto/2026?
2. Muda alguma coisa por serem 14 exames num mês só (escala, desconto)?
3. O broncodilatador altera o valor? (Os 14 são "com BD"; os 5 já fechados
   também eram, então o extrato histórico não separa.)
4. Os 14 entram como **fechamento complementar de agosto** (recomendado — a
   competência é a do exame) ou você prefere jogá-los para setembro?

**Nenhum fechamento em massa foi criado. Nenhum lançamento manual foi criado.
Nenhum preço foi gravado.**

---

## 11. O que esta etapa corrige (independe de preço)

### 11.1 Fechamento complementar — o mês pode fechar mais de uma vez

`partner_settlements` ganha a coluna **`sequencia`**, e a chave única passa de
`(parceiro, unidade, competência)` para
`(parceiro, unidade, competência, sequência)`.

O `POST /pastore/fechamentos` deixou de olhar "já existe fechamento neste mês?"
e passou a olhar "sobrou exame para fechar neste mês?". A partir disso ele faz
uma de duas coisas, e diz qual no campo `acao` da resposta:

- **`incorporado`** — a competência tem um fechamento ainda *aberto*
  (`incluido` **e** sem `valor_total`): os exames que faltavam entram nele.
  Não faz sentido fragmentar um mês que ainda nem declarou valor.
- **`criado`** — os fechamentos daquela competência já têm valor conferido:
  nasce um **complementar**, com sequência seguinte, `valor_total = NULL`,
  recibo próprio.

A distinção entre os dois casos é o ponto inteiro da correção. O fechamento de
agosto declara R$ 328,50 **para 3 exames**, número batido contra o extrato da
Pastore. Enfiar mais 14 exames ali dentro não "conserta" nada — transforma um
valor verificado em afirmação falsa sobre um conjunto que ninguém conferiu.
Por isso `_fechamento_aberto()` exige as duas condições, e não só o estado.

`_eligible_exams()` passou a excluir exames já vinculados **na consulta**. Antes
ele trazia o mês inteiro e o endpoint rejeitava o conjunto todo ao topar com um
exame já vinculado — era o segundo portão da seção 4, e sozinho já bastaria para
travar tudo mesmo sem a chave única.

Continua valendo, sem mudança: **nenhum preço é inferido em lugar nenhum.** O
complementar nasce sem valor, `regra_valor` continua dizendo
"Não inferido; exige confirmação do gestor.", e nenhum `FinancialEntry` nasce
antes de alguém confirmar valor, data e forma do recebimento.

### 11.2 O painel avisa antes do clique

`grupos_elegiveis` passou a carregar `fechamentos_existentes`, `acao_prevista`
(`criar` | `incorporar` | `complementar`) e `acao_rotulo`. O botão deixou de
dizer sempre "Criar fechamento": em agosto ele diz
**"Criar fechamento complementar 2"**, com a nota explicando que a competência
já tem 1 fechamento com valor conferido. O operador entende a consequência antes
de agir, em vez de descobrir pelo 409.

### 11.3 Pendente negativo na conciliação extra-Pastore

`total_pendente` não fica mais negativo: dívida histórica não fica negativa, ela
acaba. O que passar do alvo aparece com o nome certo, em `total_alem_do_alvo`,
com `alvo_conciliado: true` e uma nota dizendo que aquilo é receita de exames
posteriores ao fechamento histórico e não indica erro. Hoje seriam
**R$ 450,00** de excedente, no lugar de um "−R$ 450,00" que não existia.
O mesmo recorte foi aplicado ao endpoint de lote, para que a conferência de
soma não ficasse incoerente com o que a tela mostra.

### 11.4 Trilha de auditoria

`pastore.fechamento_criado` passou a registrar `sequencia`, e a incorporação
ganhou ação própria: **`pastore.fechamento_itens_incorporados`**. Sem isso a
trilha não distinguiria o fechamento original do complementar — que é
exatamente a pergunta que se faz ao auditar por que um mês fechou duas vezes.
`sequencia` foi adicionada à `ALLOWED_KEYS` de `app/audit.py`, senão a chave
seria descartada em silêncio.

### 11.5 Migração e backfill

`a3f6b0d94c17` (head anterior: `c4a97b1e6d20`). O backfill é **idempotente**:
`server_default = "1"` preenche as linhas existentes durante o `ALTER`, e o
default é removido logo em seguida — criar fechamento sem sequência explícita
passa a ser erro do servidor, não um `1` silencioso gravado pelo banco.

O `downgrade` **recusa reverter** se existir competência com mais de um
fechamento: a chave antiga rejeitaria linhas que já existem, e apagar um
fechamento para caber nela destruiria operação real. Enquanto não houver
complementar — que é o estado de produção hoje — o caminho de volta está aberto.

---

## 12. Provas

### 12.1 Ensaio contra cópia real do banco de produção

Um `pg_restore` do dump de produção num banco descartável
(`soprolife_m26_ensaio`), migrado e exercitado com o código novo:

```
parceiro: CLI-000002 | unidades ativas: ['UNI-000002']
aguardando_fechamento: 14
  grupo: {... 'competencia': '2026-08', 'quantidade': 14,
          'fechamentos_existentes': 1, 'acao_prevista': 'complementar',
          'acao_rotulo': 'Criar fechamento complementar 2'}
```

Criando o complementar **na cópia**:

```
acao: criado | sequencia: 2 | titulo: Fechamento 2026-08 — complementar 2
exames_adicionados: 14 | valor_total: None
exames: ESP-000025 … ESP-000037, ESP-000040
  2026-07-01 seq1 valor=219.00 status=a_receber -> INALTERADO
  2026-08-01 seq1 valor=328.50 status=a_receber -> INALTERADO
  2026-08-01 seq2 valor=None   status=incluido  -> NOVO
lancamentos antes/depois: 15 / 15
vinculos totais: 19 | exames distintos: 19
aguardando depois: 0 | grupos: []
valor_a_receber_confirmado: 547.50
```

Os 14 são exatamente os 14 da tabela da seção 3. Os fechamentos anteriores não
se moveram. **Zero lançamentos financeiros criados** — nenhum preço inventado.
Nenhum exame em dois fechamentos.

### 12.2 Backfill e rollback, em Postgres, sobre dados reais

| passo | resultado |
|---|---|
| `alembic upgrade head` sobre cópia de produção | ok |
| fechamentos existentes viraram `sequencia = 1` | valores `219,00` e `328,50` intactos |
| `sequencia` `NOT NULL`, sem `DEFAULT` residual | confirmado no `\d` |
| chave única | `(partner_id, partner_unit_id, competencia, sequencia)` |
| `CHECK sequencia >= 1` | criada |
| `downgrade` COM complementar existente | **recusado**, com a mensagem da guarda |
| `downgrade` sem complementar (estado de hoje) | volta a `c4a97b1e6d20`, coluna removida, dados intactos |

Ambos os bancos de ensaio foram destruídos ao fim.

### 12.3 Testes

`tests/test_m26_pastore_fechamento_complementar.py` — 10 testes novos que
reproduzem o cenário exato de produção (3 exames fechados e valorados,
14 realizados depois no mesmo mês) e cobrem: elegibilidade e aviso de
complementar; fechamento dos 14 sem tocar no valor conferido; incorporação
quando o mês ainda está aberto; recusa de incorporar em fechamento já valorado
mesmo em `incluido`; valor e recibo próprios do complementar; exame nunca em
dois fechamentos; trilha com sequência; ação própria da incorporação; exame não
concluído continua fora; competências independentes.

`tests/test_conciliacao_extra_pastore.py` — 3 testes novos: excedente não vira
pendente negativo, `alem_do_alvo` zerado enquanto falta conciliar, e o lote
devolvendo o pendente real (`0.00`, não `−455,21`).

`tests/test_migrations.py` — 2 testes novos: backfill + chave por competência
(incluindo a recusa de sequência repetida e de sequência `0`), e a guarda do
downgrade.

Suíte completa: **1440 passaram, 13 falharam, 30 puladas.** As 13 falhas são
**pré-existentes e ambientais**, idênticas na base `67ddcd3` sem nenhuma
alteração minha (12 de `test_live_multisheet_reader.py`, que exige credenciais
Google, e `test_rubrica_real_nao_esta_versionada`, que procura um arquivo de
rubrica não versionado). Verificado com `git stash` na mesma máquina.

---

## 13. O que NÃO foi feito

- **Nenhum fechamento criado em produção.** Você pediu para não fazer
  fechamento em massa ainda, e criar o complementar dos 14 é exatamente isso.
  O botão está pronto e aguarda seu clique — ou sua ordem.
- **Nenhum lançamento financeiro criado ou alterado.**
- **Nenhum preço gravado.** Nem em `Partnership`, nem em `PartnerUnit`, nem em
  observação de fechamento. Os campos novos de preço da seção 6 **não** foram
  criados: sem o valor decidido seriam mobília vazia; com ele seriam invenção.
- **Nada mexido no fluxo SoproLife direto.** `_criar_financeiro` está
  intocado — LAN-000016 e LAN-000017 provaram que funciona.
- Nenhum exame, laudo, pessoa ou linha de auditoria alterado ou apagado.
- CON-000001 (seção 9) segue sem lançamento — fora do escopo desta correção.

---

## 13.1 Deploy em produção — executado em 30/08/2026

| verificação | resultado |
|---|---|
| commit local = GitHub = VPS | `22f5908` |
| branch | `painel-soprolife-v01` (fast-forward de `67ddcd3`) |
| backup pré-migração | `/opt/soprolife/backups/m15/manual/soprolife_m15-pre-m26-20260830T053131Z.dump` (`0600`, 401 objetos) |
| migração | `c4a97b1e6d20` → `a3f6b0d94c17 (head)` |
| serviços | `soprolife-m15-api`, `soprolife-painel`, `soprolife-painel-loopback` **ativos** |
| health | API `200`, painel `200` |
| journal pós-restart | sem `error`, `traceback` ou `exception` |
| árvore Git da VPS | limpa |
| cache busting | `?v=2026083001` em `style.css`, `pastore-settlement.css/js` e `financeiro-conciliacao.js` |

Leitura em produção, depois do deploy:

```
aguardando_fechamento: 14
grupo: Pastore Ipanema 2026-08 · 14 exames -> "Criar fechamento complementar 2"
fechamento: Fechamento 2026-08 | seq 1 | a_receber | 328.50 | itens 3
fechamento: Fechamento 2026-07 | seq 1 | a_receber | 219.00 | itens 2
regra_valor: Não inferido; exige confirmação do gestor.
conciliacao: alvo 3044.79 vinculado 3494.79 pendente 0.00
             alem_do_alvo 450.00 alvo_conciliado True
```

O botão que devolvia 409 agora diz o que vai fazer. O pendente de −R$ 450,00
virou R$ 450,00 de receita além do alvo histórico. E os dois fechamentos de
11/08 continuam byte a byte como estavam.

**Rollback disponível:** enquanto não existir fechamento complementar,
`alembic downgrade c4a97b1e6d20` volta atrás sem perda — ensaiado sobre cópia
real do banco (seção 12.2). O dump pré-migração cobre o resto.

---

## 14. Próximo passo

Responda as 4 perguntas da seção 10. Com o valor decidido, o resto é um clique:

1. **Criar fechamento complementar 2** de 2026-08 (14 exames, valor `NULL`);
2. **PATCH** com o `valor_total` que você determinar → estado `a_receber`;
3. quando a Pastore pagar, **Registrar recibo mensal único** com valor, data e
   forma reais → aí nasce o `FinancialEntry` e o dinheiro aparece no Financeiro.

E vale registrar de vez a regra comercial (seção 6) para que agosto seja o
último mês em que esse valor precise ser lembrado de cabeça.
