# M26.3 — Regra Pastore, fechamento automático e redesign do Financeiro

**Data:** 31/08/2026
**Branch:** `claude-m26-3-financeiro-pastore-redesign`
**Base:** `origin/painel-soprolife-v01` @ `9288f3c`
**Worktree:** `/home/fedorasurf/soprolife-worktrees/claude-m26-3-financeiro-pastore-redesign`

Documento vivo — atualizado durante a missão.

Fonte de contexto: `RELATORIO_M26_2_FINANCEIRO_AUTOMATICO_ESPIROMETRIA_20260831.md`.
A auditoria não é reaberta. Laudo, assinatura, PDF e fluxo da Dra. Ana ficam
intocados por contrato.

Nenhum nome de paciente aparece aqui. Só códigos públicos.

---

## Estado da missão

| Etapa | Situação |
|---|---|
| 1. Worktree limpo | **concluída** |
| 2. Conferência prévia dos valores em produção | **concluída** |
| 3. Regra comercial Pastore no modelo | pendente |
| 4. Fechamento automático + valor previsto | pendente |
| 5. Redesign do Financeiro | pendente |
| 6. Testes focados | pendente |
| 7. Commit / push / integração | pendente |
| 8. Backup + migração + regularização | pendente |
| 9. Deploy + smoke | pendente |
| 10. Prova final | pendente |

---

## 0. Conferência prévia — os valores batem, a escrita está liberada

O pedido manda parar antes de escrever se algum valor divergir. Nenhum
divergiu. Leitura `SELECT` em produção (`soprolife-painel-01`,
`/opt/soprolife/soprolife-site` @ `9288f3c`, Alembic head `a3f6b0d94c17`,
`soprolife-m15-api` ativo, `/api/v1/health` → 200), 31/08/2026 23h50 (-03):

| Conferência | Esperado | Produção | |
|---|---|---|---|
| Receita SoproLife direta recebida | R$ 3.494,79 | **R$ 3.494,79** (15 LAN, todas `Recebido`) | ✅ |
| Receita pendente hoje | — | **R$ 0,00** | ✅ |
| Fechamento 2026-07 #1 | 2 exames, R$ 219,00 | 2 itens, `a_receber`, R$ 219,00 | ✅ |
| Fechamento 2026-08 #1 | 3 exames, R$ 328,50 | 3 itens, `a_receber`, R$ 328,50 | ✅ |
| Fechamento 2026-08 #2 | 14 exames, R$ 1.533,00 | 14 itens, `incluido`, `valor_total = NULL` | ✅ (valor a definir) |
| Proporção histórica | R$ 109,50/exame | 219,00 = 2×109,50 · 328,50 = 3×109,50 | ✅ |
| LAN-000017 / ESP-000039 | R$ 230,00 intacta | R$ 230,00, `Recebido`, `Pix`, receb. 29/08 | ✅ |
| Recibos de fechamento existentes | 0 | **0** | ✅ |

Aritmética conferida: 14 × 109,50 = **1.533,00**; Pastore total
219,00 + 328,50 + 1.533,00 = **2.080,50**; total geral
3.494,79 + 2.080,50 = **5.575,29**.

### 0.1 Forma de pagamento — pesquisada antes de preencher

O pedido manda pesquisar registro/extrato/metadado antes de escolher, e não
inventar "Pix". Pesquisa feita em produção:

| Onde procurei | Achado |
|---|---|
| `partnerships.observacao` (PAR-000001) | `NULL` |
| `partners.observacao` (Pastore) | `NULL` |
| `partner_transfers` | **0 linhas** |
| Observação dos 2 fechamentos com valor | cita o extrato e o valor, **nada sobre forma de pagamento** |
| `audit_logs` com `pastore.*` / "extrato" / "forma_pagamento" | 8 eventos, **nenhum registra forma** |
| `financial_entries.forma_pagamento` já usadas | `Pix` × 5, `NULL` × 10 — todas de **pagamento direto de paciente**, fluxo diferente |

**Não há forma de pagamento documentada para os recebimentos Pastore.** O
modelo já suporta a representação neutra `"Outro"` (`FormaPagamento =
Literal["Pix", "Dinheiro", "Cartão", "Outro"]`) — nenhum ajuste de schema é
necessário. Os três recibos usam `"Outro"` mais observação e trilha
explícitas. Nenhum "Pix" é inventado.

---

## 1. A regra comercial ganhou casa própria

Migração `b1f4c72d9e08` (aditiva, reversível, testada nos dois sentidos):

```
partnerships.modelo_recebimento        VARCHAR(20) NOT NULL DEFAULT 'indefinido'
partnerships.valor_recebido_por_exame  NUMERIC(12,2) NULL
partnerships.vigencia_inicio           DATE NULL
```

Mais duas restrições de banco:

* `valor_recebido_nao_negativo`;
* `recebimento_por_exame_completo` — quem declara `valor_por_exame` declara
  junto QUANTO e A PARTIR DE QUANDO. Meia regra vira erro no banco, porque um
  previsto sem vigência é um número que ninguém consegue justificar contra o
  extrato.

**Por que campos novos e não `valor_repasse_fixo`.** `docs/parceria-pastore-planilha.md:46`
define `repasse_pastore` como "valor repassado **À** Pastore" e a linha 130 o
soma em `custo_total`. Reaproveitar aquele campo inverteria a direção do
dinheiro e contaminaria todo relatório de custo. Um teste trava isso: nenhum
arquivo do caminho financeiro pode ler `.valor_repasse_fixo` nem
`.percentual_repasse`.

### 1.1 O override por unidade cabe depois sem virar caminho principal

`app/services/partner_pricing.py` é o **único** leitor de
`valor_recebido_por_exame` no sistema inteiro — e há um teste que falha se
aparecer um segundo. `resolve_valor_por_exame(db, partner, unit, competencia)`
já recebe a unidade e tem o degrau de override declarado e deliberadamente
vazio. A Pastore tem uma unidade ativa; modelar hoje uma exceção que não existe
seria pagar o custo dela antes de precisar. Quando existir, a leitura entra
naquele ponto e nem endpoint, nem snapshot, nem painel mudam.

---

## 2. O exame entra no fechamento sozinho

`ensure_settlement_for_exam` (em `app/services/pastore.py`) é chamado nos três
pontos onde um exame nasce ou se torna elegível:

| Ponto | Quando importa |
|---|---|
| `POST /atendimentos` | o cadastro normal, pela Central de Cadastros |
| `POST /espirometrias` | CLI, importação — a porta que a M26.2 apontou como "exame pode nascer sem que nada avise" |
| `PATCH /espirometrias/{id}` | exame que nasceu agendado e virou "Realizado" |

Devolve `None` sem escrever nada quando não há o que fazer (sem parceiro, não
concluído, já vinculado, unidade inativa) — é esse `None` que o torna seguro de
chamar em todo caminho de escrita, quantas vezes for.

**Idempotência em três camadas:** a checagem de vínculo na função, o `NOT IN`
de `eligible_exams`, e a unicidade de `PartnerSettlementItem.spirometry_exam_id`
como backstop de corrida.

### 2.1 Previsto é derivado, nunca gravado

`valor_previsto = quantidade de itens × regra vigente`, calculado na
serialização. Uma coluna guardaria o número do dia em que alguém a escreveu e
envelheceria em silêncio a cada exame novo. Derivando, o recálculo acontece
sozinho — sem job, sem clique, sem coluna velha.

`valor_total` continua significando **uma** coisa: o valor conferido contra o
extrato. Previsto e conferido nunca se sobrescrevem.

### 2.2 Realizado ≠ recebido

| Evento | Efeito |
|---|---|
| exame Pastore realizado | entra no fechamento; soma em **A receber** |
| gestor confirma o pagamento | nasce **um** recibo; sai de "a receber", entra em **Receita recebida** |

Nenhum exame vira receita sozinho. O painel mostra o que a Pastore deve antes
de o dinheiro entrar, e só conta como recebido depois da confirmação.

---

## 3. O que estava quebrado na página Financeiro

Os dois gráficos vazios e a tabela vazia não eram falha de dado: **a tela pedia
um contrato que não existia mais.** A M23 passou a gerar o resumo do PostgreSQL
(`app/snapshots.py`), e o `renderFinance()` continuava lendo `por_servico`,
`por_local` e `lancamentos_agregados` — três chaves que o gerador novo nunca
produziu. O `financeiro-summary.local.json` do repositório ainda era o
demonstrativo de junho, com o schema antigo, o que escondeu a divergência.

### 3.1 A nova tela

Quatro indicadores, nenhum permanentemente zerado:

1. **Receita recebida** — o que tem lançamento confirmado;
2. **A receber** — pendente próprio + o que os fechamentos de parceria já devem;
3. **Receita da competência atual**;
4. **Exames pagos**, com a receita média por exame como apoio.

Abaixo: receita por origem (barras com valor e percentual), receita por
competência (barras compactas em CSS, sem canvas), lançamentos recentes e um
bloco discreto de resumo operacional. O banner verde "Fonte oficial dos
valores…" virou uma linha de rodapé.

### 3.2 Ticket médio: opção A

Trocado por **receita média por exame**. O motivo é objetivo: um recibo Pastore
é **um** lançamento que cobre 14 exames, então dividir receita por lançamento
faria a métrica crescer sozinha a cada mês fechado. `ticket_medio_real` fica
explicitamente `None` no resumo para que nenhum consumidor herde o número
errado em silêncio.

### 3.3 Quatro coisas que a implementação obrigou a corrigir

**A competência é do fuso da operação.** `datetime.now(timezone.utc)` entre 21h
e meia-noite em Brasília já virou o mês seguinte — o card mostraria o próximo
mês zerado. Passou a usar `today_local()`.

**O gráfico mensal é por competência, não por crédito.** O recibo de julho pago
em 31/08 pertence a julho, que é quando aqueles exames foram feitos. Chavear
pela data do crédito empilharia toda a regularização histórica numa barra só e
apagaria a produção real dos meses anteriores.

**Receita sem data nenhuma é declarada.** O lote M18 (R$ 2.385,79, 10
lançamentos) não tem competência nem recebimento. Somá-lo num mês inventado
seria mentira; ignorá-lo em silêncio faria a soma das barras não bater com
"Receita recebida". Aparece como nota abaixo do gráfico.

**Descontos saíram.** O modelo não tem esse conceito — o card exibia um zero
fabricado.

### 3.4 A guarda de PII recusou o resumo, e ela estava certa

A primeira versão exportava `descricao` nos lançamentos recentes.
`check-access.sh` barrou: "possível nome de pessoa em campo não institucional".
Era falso positivo no conteúdo ("Fechamento Pastore" são duas palavras
capitalizadas), **mas o campo é texto livre que o operador pode digitar**. Um
snapshot marcado como seguro não deve depender de um detector para continuar
seguro.

Correção: o resumo passou a exportar só vocabulário fechado (`categoria`),
código público (`referencia`) e `competencia`. O painel compõe a frase na tela.
Nenhuma dispensa foi acrescentada ao ruleset — a guarda ficou intacta.

---

## 4. Testes

| Arquivo | Testes | Cobre |
|---|---|---|
| `test_m26_3_regra_pastore_e_painel.py` | **23** | regra 109,50; 2/3/14 exames = 219,00/328,50/1.533,00; recálculo do previsto; exame não duplica; agendado só entra ao virar realizado; complementar; recibo único e idempotente; SoproLife direto usa o valor digitado; recebido × a receber; origem; competência; lançamentos recentes; média por exame ≠ por lançamento; banco vazio não gera série nem métrica falsa |
| `test_m26_3_regularizacao_pastore.py` | **10** | dry-run não escreve; valores finais exatos; receitas próprias byte a byte iguais; 2ª execução não duplica; trilha sobrevive à sanitização; **4 guardas fail-closed** (contagem, receita própria, valor divergente, fechamento inesperado) |
| `test_migrations.py` | +1 | migração sobe, faz backfill de linha existente e desce sem perder dado |

**Suítes atualizadas ao novo contrato:** `test_m22_pastore_settlements.py`,
`test_m26_pastore_fechamento_complementar.py` e
`test_m26_2_financeiro_automatico_espirometria.py`. O mecanismo que elas provam
não mudou — mudou o **gatilho**, de clique para automático. As garantias
continuam as mesmas; o POST manual passou a ser testado como rota de
recuperação, e os testes do script de reconciliação passaram a montar órfãos
vindos de fora da API (importação), que é o caso que ele existe para resgatar.

Um teste da M26.2 mudou de sentido de propósito: `test_5` travava o fato de
**não existir** preço de recebimento. A regra passou a existir, e ele virou
`test_5_repasse_e_recebimento_nunca_se_misturam` — o invariante que importa
continua sendo que nenhum caminho de receita leia campo de repasse.

**Resultado:** `1513 passed, 30 skipped` em 8m51s.

Deselecionada 1: `test_rubrica_real_nao_esta_versionada`, falha **pré-existente
desde a M25.21** (falso positivo do filtro por nome sobre screenshots
sintéticos), conferida como já falhando na base limpa.

Quality gate: **PASSOU**, todos os checks.
