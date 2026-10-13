# M26.21 — Reconciliação de Custos & Investimentos e rateio dos sócios

- **Data:** 2026-10-12
- **Worktree:** `/home/fedorasurf/soprolife-worktrees/claude-m26-21-custos-socios`
- **Branch:** `claude-m26-21-custos-socios` → integrada ff-only em `painel-soprolife-v01`
- **Commit:** `69e8ede`
- **Origem dos fatos:** reconciliação informada pelos sócios
- **Escopo tocado:** apenas o módulo Custos & Investimentos do Command Center.
  Site público, laudos, portal de resultados e NFS-e não foram tocados.

---

## 1. O problema que esta missão resolveu

Os agregados de rateio viviam **escritos à mão** dentro do JSON privado
(`rateio_socios`, `rateio_itens`). Quem corrigisse um pagamento tinha de corrigir
o total à mão também — e foi exatamente aí que o painel passou a divergir do
histórico real:

- a contabilidade (R$ 2.600,00 já pagos) **não existia no cadastro**;
- impostos (R$ 50,06) e Mais Laudo (R$ 130,00) **não existiam no cadastro**;
- as posições 10, 11 e 12 do Koko estavam como *pendentes sem pagador* quando na
  verdade eram pagamentos de Faustino;
- o adiantamento do Koko aparecia como R$ 5.000,00 de desembolso de Faustino,
  misturando 4 parcelas realmente pagas (R$ 2.000,00) com a parcela bonificada;
- o Workspace exibia `R$ 32,72` na coluna **"Pago Adeildo"** — era um mês, não o
  total desembolsado.

A correção estrutural foi trocar o modelo: cada item agora tem um
**livro-caixa único** (`movimentos`), e **todo agregado é derivado** dele por um
gerador determinístico. Nada é fixado no código para bater com um print —
mudar um movimento e rodar o gerador muda o painel.

### Modelo de status do livro-caixa

| status | significado |
|---|---|
| `pago` | saída de caixa confirmada |
| `pendente` | ainda não vencido, ou sem pagador definido |
| `vencido_aguardando_confirmacao` | o vencimento passou e **ninguém confirmou o débito** |
| `baixada_por_desconto` | posição liquidada **sem saída de caixa** (bonificação) |

---

## 2. Dry-run (obrigatório, executado antes de qualquer escrita)

`m26-21-reconciliar-custos-socios.py` roda em **dry-run por padrão**; só grava
com `--apply`, e só se as verificações passarem. Executado duas vezes: no
worktree local e **nos dados reais da VPS**, com resultado idêntico e 0 erros.

| Item | Registro atual → proposto | Valor atual → proposto | Pagador atual → proposto | Status atual → proposto | Motivo |
|---|---|---|---|---|---|
| Google Workspace | 0 mov. → 0 mov. (livro-caixa) | — → — | Adeildo → Adeildo | ativo → ativo | Responsabilidade corrente confirmada; nº de meses pagos não informado → `pago` vira `null` em vez de `R$ 32,72` (um mês exibido como total) |
| Tailscale | 0 mov. → 0 mov. | — → — | Adeildo → Adeildo | ativo → ativo | Idem; `R$ 42,00` deixa de aparecer como total desembolsado |
| Hostinger VPS | 0 mov. → 0 mov. | R$ 635,00 → R$ 635,00 | Adeildo → Adeildo | ativo → ativo | Periodicidade **anual real preservada**; desembolso segue não confirmado |
| **Contabilidade / Contador** | **ausente** → 10 movimentos | — → R$ 260,00/mês | — → Faustino e Adeildo | — → ativo | Item novo: histórico jan..out/2026 informado pelos sócios |
| **Impostos / Tributos** | **ausente** → 3 movimentos | — → variável | — → Faustino | — → ativo | Item novo: jul/ago/set 2026, competência preservada |
| **Mais Laudo** | **ausente** → 6 movimentos | — → variável | — → Faustino | — → ativo | Item novo; meses de R$ 0,00 **não** geram lançamento |
| MIR / Spirobank II Smart | 0 mov. → 10 parcelas | R$ 12.464,61 → R$ 12.464,61 | Adeildo → Adeildo | parcelado → parcelado | Calendário da fatura (dia 10) gerado; 4 vencidas → `aguardando confirmação`, nunca "pago" |
| Seringa MIR 3L | 0 mov. → 10 parcelas | R$ 1.997,00 → R$ 1.997,00 | Adeildo → Adeildo | parcelado → parcelado | Idem |
| **Espirômetro Koko** | 20 posições → 20 posições | R$ 9.500,00 → R$ 9.500,00 | Faustino e Adeildo (mantido) | parcelado → parcelado | **10/11/12 eram pagamentos de Faustino**, não pendências; adiantamento corrigido para R$ 2.000,00 + bonificação |
| CREMERJ — Regularização PJ | 1 pagamento → 1 movimento | R$ 2.296,06 → R$ 2.296,06 | Faustino → **Faustino (preservado)** | pago → pago | Pagamento histórico verdadeiro — **não reatribuído** |

Verificações do dry-run: sem id de item duplicado, sem `ref` de movimento
duplicada, sem pagamento repetido na mesma competência, totais fechando com os
agregados, e nenhum campo sensível remanescente.

---

## 3. Koko — a parte que mais precisava de cuidado

| | valor |
|---|---|
| Contrato nominal | 20 × R$ 500,00 = **R$ 10.000,00** |
| Bonificação / desconto | **R$ 500,00** (1 parcela) |
| **Custo efetivo** | **R$ 9.500,00** |
| Pago por Adeildo | **R$ 1.500,00** — posições 3, 6 e 7 |
| Pago por Faustino | **R$ 6.500,00** — 9 pagamentos normais (1, 2, 4, 5, 8, 9, 10, 11, 12) + 4 do adiantamento |
| Desembolsado em dinheiro | **R$ 8.000,00** |
| Pendente | **R$ 1.500,00** — posições 13, 14 e 15, sem data e sem pagador |
| Conferência | R$ 8.000,00 + R$ 1.500,00 = **R$ 9.500,00** ✔ |

O adiantamento de 13/05/2026 liquidou as posições 16–20 com **saída de caixa de
R$ 2.000,00** (4 parcelas) **mais uma bonificação de R$ 500,00**. A posição 20/20
fica registrada com `valor: 0.00` e `valor_nominal: 500.00`, status
`baixada_por_desconto` e **pagador `null`** — bonificação não é desembolso de
ninguém. Garantido por teste: `R$ 3.000,00`, `R$ 2.500,00` e `R$ 10.000,00` **não
aparecem** em lugar nenhum do módulo renderizado.

---

## 4. MIR e seringa — calendário automático, conciliação não

Os dois são pagos no cartão de Adeildo, cuja fatura é debitada todo dia 10. O
calendário é gerado; **a baixa não**:

| | MIR / Spirobank II Smart | Seringa MIR 3L |
|---|---|---|
| Total | R$ 12.464,61 | R$ 1.997,00 |
| Parcela | R$ 1.246,46 (10×) | R$ 199,70 (10×) |
| Responsável | Adeildo | Adeildo |
| Vencimento | dia 10 de cada competência | dia 10 |
| Parcelas confirmadas como pagas | **0** | **0** |
| Vencidas aguardando confirmação | **4** (jul, ago, set, out/2026) | **4** |
| Total já pago | R$ 0,00 | R$ 0,00 |
| Saldo restante | R$ 12.464,60 | R$ 1.997,00 |
| Próximo vencimento | 10/11/2026 (parcela 5/10) | 10/11/2026 |
| Mais antiga em aberto | 1/10 · jul/2026 | 1/10 · jul/2026 |

"Próximo vencimento" e "mais antiga em aberto" são **dois campos distintos de
propósito**: mostrar só o mais antigo como "próximo" esconderia o atraso;
mostrar só o futuro esconderia a fila acumulada.

---

## 5. Totais finais por sócio (derivados, não digitados)

| | Adeildo | Faustino |
|---|---|---|
| **Total desembolsado confirmado** | **R$ 2.540,00** | **R$ 10.536,12** |
| Compromisso mensal individual | R$ 1.573,80 | R$ 0,00 |
| Compromisso mensal compartilhado | R$ 760,00 (contabilidade R$ 260 + Koko R$ 500) | idem |
| Saldo pendente atribuído | R$ 14.461,60 (MIR + seringa) | R$ 0,00 |
| Pendente sem pagador definido | R$ 1.500,00 (não atribuído a nenhum dos dois) | idem |
| Itens vinculados | 7 | 5 |
| Itens de histórico não confirmado | 3 | 0 |

**Composição de Adeildo:** contabilidade R$ 1.040,00 + Koko R$ 1.500,00.
**Composição de Faustino:** Koko R$ 6.500,00 + CREMERJ R$ 2.296,06 +
contabilidade R$ 1.560,00 + Mais Laudo R$ 130,00 + impostos R$ 50,06.

Agregados do módulo: custo mensal atual **R$ 2.333,80**; investido em
equipamentos **R$ 23.961,61**; bonificação total **R$ 500,00**; 10 itens
cadastrados; 4 pendências.

---

## 6. Conflitos encontrados

1. **MIR: divergência de R$ 0,01.** O cadastro declara total R$ 12.464,61, mas
   10 × R$ 1.246,46 = R$ 12.464,60. Os dois números declarados foram
   **preservados** e a diferença fica registrada em
   `divergencia_arredondamento`, exibida na aba Parcelamentos. Nenhuma parcela
   foi inflada para fechar a conta. *Resolver quando houver a fatura real.*
2. **Workspace, Tailscale e Hostinger: desembolso não confirmado.** A
   responsabilidade corrente é de Adeildo (informado pelos sócios), mas o número
   de meses efetivamente pagos e o desembolso do plano anual de R$ 635,00 não
   foram informados. O painel mostra **"—"**, não `R$ 0,00`. Esses valores
   **não entram** no total desembolsado — é por isso que o total de Adeildo é
   R$ 2.540,00 e não mais.
3. **Koko: R$ 1.500,00 sem pagador.** Posições 13, 14 e 15 seguem sem data e sem
   pagador. Aparecem como terceira categoria no gráfico, **nunca somadas** a
   nenhum sócio.
4. **Colisão de numeração de missão.** Já existe
   `RELATORIO_M26_21_GATES_GO_LIVE_E_AUTENTICACAO_20260920.md` no Git — outra
   missão também numerada M26.21. Os nomes de arquivo não colidem, mas a
   numeração está duplicada.
5. **Propriedade de arquivos na VPS.** `docs/` estava como `root:root` (escrito
   por um deploy anterior rodado como root) e **bloqueou o `git pull`**.
   Devolvido a `soprolife:soprolife`. Restam **107 arquivos `root`** no
   repositório, que vão bloquear futuros pulls que os toquem — **não corrigidos
   nesta missão** (decisão para o humano).
6. **Pull parcialmente aplicado.** O pull que falhou por permissão deixou os
   arquivos novos em disco com o HEAD antigo. Foi verificado byte a byte que o
   conteúdo em disco era idêntico ao commit alvo antes de limpar e refazer.

Nenhum conflito exigiu parada: nenhum deles obrigava a inventar valor.

---

## 7. Código

Houve **código e dados**.

| Arquivo | O que mudou |
|---|---|
| `painel-soprolife/scripts/generate-custos-investimentos.py` | **novo** — gerador do resumo seguro a partir do livro-caixa, validado pelo `pii_guard` na escrita |
| `painel-soprolife/scripts/m26-21-reconciliar-custos-socios.py` | **novo** — migração auditável, dry-run por padrão |
| `painel-soprolife/scripts/test-custos-socios-m26-21.py` | **novo** — 165 verificações de dados |
| `painel-soprolife/scripts/test-custos-socios-visual-m26-21.js` | **novo** — 852 verificações visuais |
| `painel-soprolife/js/app.js` | nova aba Parcelamentos; coluna Bonificação; "Pago" sem sufixo `/mês`; pendências e dicas vindas do dado |
| `painel-soprolife/index.html` | aba e painel `ci-parcelamentos` |
| `painel-soprolife/scripts/pii_guard.py` | dispensa estreita para nome próprio de fornecedor + 6 self-tests |

### Dois defeitos que só os testes revelaram

- **`R$ 0,00` inventado na coluna mensal.** Impostos e Mais Laudo têm
  `valor_mensal: null` (valor variável), e `fmtBRL(null)` renderizava
  `R$ 0,00` — exatamente o que a regra proíbe. Passou a exibir "variável".
- **Saldo zero afirmado sem dado.** Itens de histórico não confirmado emitiam
  `pendente = 0`, e o Hostinger aparecia com saldo `R$ 0,00`. Mas não se sabe se
  os R$ 635,00 foram pagos: zero ali era uma **afirmação sem dado**. Agora
  histórico não confirmado emite `null` em `pago` **e** em `pendente`.

### O falso positivo do pii_guard

"Mais Laudo" é a **razão social** do serviço terceirizado, e a palavra "laudo"
acionava o scan de termo clínico, abortando a gravação do resumo. A correção é a
dispensa **mais estreita possível**: match **exato do valor inteiro** (não
substring) e dispensa **apenas** o scan de termo clínico. Telefone, CPF, e-mail,
token, URL de planilha e o detector de nome continuam valendo. Seis self-tests
provam que a dispensa não virou porta larga — inclusive que ela **não** basta
sozinha: o campo também precisa ser institucional, porque "Mais Laudo" tem duas
palavras capitalizadas e o detector de nome continua ativo.

---

## 8. Testes

| Suíte | Resultado |
|---|---|
| `test-custos-socios-m26-21.py` (dados) | **165/165** — local e na VPS contra os dados reais |
| `test-custos-socios-visual-m26-21.js` | **852/852** — chromium + firefox, 6 larguras (1440→320), 6 abas |
| `pii_guard.py --self-test` | **60/60** |
| `check-access.sh` | **OK** — local e na VPS, sem PII, arquivos gitignored, modo 600 |
| `test-command-center-visual.js` (regressão) | **OK** — 18 gráficos, 6 larguras, 2 motores |
| `git diff --check` | limpo |

Cobertura por ponto pedido: contabilidade, impostos, Mais Laudo, Koko,
bonificação do Koko, saldo pendente do Koko, pagador por parcela, MIR, seringa,
Workspace, Tailscale, Hostinger, totais por sócio, compromisso mensal, saldo
pendente, rateio por item, ausência de duplicidade, reload e responsividade.

**Falhas preexistentes não tocadas:** `test-m21-auth-crm-nav` (1) e
`test-m25-26-fluxo-espirometria` (2) falham no quality gate. Verificado por
`git stash` que falham **de forma idêntica em `origin/painel-soprolife-v01`
sem as minhas mudanças** — são anteriores a esta missão e estão fora do escopo
(fluxo de espirometria e Marketing).

---

## 9. Auditoria

Cada movimento carrega `origem: "reconciliacao informada pelos socios"`. O bloco
`auditoria` do JSON privado registra item, competência/parcela, valor, pagador,
status, origem, **usuário responsável** e **data/hora**, mais a lista de itens
antes e depois — provando por teste que **nenhum item do cadastro anterior
desapareceu**. O bloco fica **só no arquivo privado**, nunca no resumo público.

Backups antes da escrita:
- local: `painel-soprolife/data-private/custos-investimentos.local.json.bak-m26-21`
- VPS: idem, mais `~/backups-m26-21/*.pre-m26-21` com SHA-256 registrado.

Nenhum histórico legítimo foi apagado: toda posição do cadastro antigo entrou no
livro-caixa novo. Os campos `id_99pay`, `codigo_autorizacao`, `cnpj_favorecido` e
`pix_antecipado` foram removidos do arquivo privado (eram dados de transação,
não histórico financeiro) — a forma de pagamento e a data continuam registradas.

---

## 10. Deploy

1. Commit `69e8ede` na branch de missão, push.
2. Integração **ff-only** em `painel-soprolife-v01` (`b056c52..69e8ede`).
3. Backup dos dois JSON na VPS com SHA-256.
4. `docs/` devolvido de `root:root` para `soprolife:soprolife` (bloqueava o pull).
5. Estado de pull parcial verificado e limpo; `git pull --ff-only` concluído.
6. **Dry-run na VPS** → 0 erros → `--apply` → gerador → testes.
7. Validação: 165/165 na VPS, `check-access.sh` OK, árvore git limpa.

**Nenhum serviço foi reiniciado** — a mudança é de front estático (`app.js`,
`index.html`) mais dois JSON lidos a cada carregamento do painel. Reiniciar a
API não teria efeito sobre isto e só adicionaria risco.

### Health

| Verificação | Resultado |
|---|---|
| `GET /api/v1/health` | **200** · `status: ok`, `banco: ok`, `ambiente: prod` |
| Painel `/painel-soprolife/` | **200** |
| `js/app.js` e `data/*.local.json` | **401** — portão de autenticação ativo |
| `data-private/custos-investimentos.local.json` | **404** — nunca servido |
| Serviços ativos | `soprolife-m15-api`, `soprolife-painel`, `soprolife-painel-loopback`, `soprolife-portal-resultados`, `nginx` |

**O que não foi possível validar sozinho:** a renderização do módulo
autenticado em produção. O portão devolve 401 sem sessão e a senha do painel não
está nesta sessão — a conferência visual logada depende de etapa humana. O
conteúdo em disco na VPS está provado pelas 165 verificações executadas lá.

---

## 11. Pendências para decisão humana

1. Confirmar na fatura do cartão quais parcelas do MIR e da seringa foram
   debitadas (4 cada, jul..out/2026) para passarem de "aguardando confirmação" a
   "pago".
2. Definir data e pagador das posições 13, 14 e 15 do Koko (R$ 1.500,00).
3. Informar quantos meses de Workspace e Tailscale foram efetivamente pagos, e
   quem desembolsou os R$ 635,00 do Hostinger.
4. Resolver a divergência de R$ 0,01 do MIR com a fatura real.
5. Decidir sobre os 107 arquivos `root` no repositório da VPS.
6. Resolver a numeração duplicada de "M26.21".

---

# Adendo M26.21A — confirmação das faturas do cartão de Adeildo (2026-10-13)

**Origem:** confirmação explícita do sócio — todas as faturas do cartão pessoal
foram pagas automaticamente no dia 10.

A M26.21 deixou de propósito as parcelas já vencidas em
`vencido_aguardando_confirmacao`, porque o calendário era automático e a
conciliação não. Essa confirmação é exatamente o dado que faltava, e por isso as
parcelas vencidas passaram a `pago` — não por ter chegado o dia 10, mas porque o
débito foi confirmado.

## Parcelas atualizadas (8)

| Item | Parcela | Competência | Vencimento | Valor | Status anterior → novo |
|---|---|---|---|---|---|
| MIR / Spirobank II Smart | 1/10 | jul/2026 | 10/07/2026 | R$ 1.246,46 | aguardando confirmação → **pago** |
| MIR / Spirobank II Smart | 2/10 | ago/2026 | 10/08/2026 | R$ 1.246,46 | aguardando confirmação → **pago** |
| MIR / Spirobank II Smart | 3/10 | set/2026 | 10/09/2026 | R$ 1.246,46 | aguardando confirmação → **pago** |
| MIR / Spirobank II Smart | 4/10 | out/2026 | 10/10/2026 | R$ 1.246,46 | aguardando confirmação → **pago** |
| Seringa MIR 3L | 1/10 | jul/2026 | 10/07/2026 | R$ 199,70 | aguardando confirmação → **pago** |
| Seringa MIR 3L | 2/10 | ago/2026 | 10/08/2026 | R$ 199,70 | aguardando confirmação → **pago** |
| Seringa MIR 3L | 3/10 | set/2026 | 10/09/2026 | R$ 199,70 | aguardando confirmação → **pago** |
| Seringa MIR 3L | 4/10 | out/2026 | 10/10/2026 | R$ 199,70 | aguardando confirmação → **pago** |

Pagador de todas: **Adeildo**. A data de pagamento registrada é o **próprio
vencimento**, porque o fato confirmado é "a fatura é debitada no dia 10" — não se
inventou outra data. Competência e vencimento ficaram **intocados**.

| | MIR | Seringa | Total |
|---|---|---|---|
| Parcelas confirmadas | 4 | 4 | 8 |
| Total adicional | R$ 4.985,84 | R$ 798,80 | **R$ 5.784,64** |

## Efeito nos agregados (recalculados, não digitados)

| | antes (M26.21) | depois (M26.21A) |
|---|---|---|
| **Adeildo — total desembolsado** | R$ 2.540,00 | **R$ 8.324,64** |
| Adeildo — saldo pendente atribuído | R$ 14.461,60 | **R$ 8.676,96** |
| Faustino — total desembolsado | R$ 10.536,12 | R$ 10.536,12 (inalterado) |
| Total desembolsado confirmado | R$ 13.076,12 | **R$ 18.860,76** |
| MIR — parcela atual / pago / saldo | 0/10 · R$ 0,00 · R$ 12.464,60 | **4/10 · R$ 4.985,84 · R$ 7.478,76** |
| Seringa — parcela atual / pago / saldo | 0/10 · R$ 0,00 · R$ 1.997,00 | **4/10 · R$ 798,80 · R$ 1.198,20** |
| Pendências de cadastro | 4 | **3** |

Compromisso mensal de Adeildo (R$ 1.573,80), bonificação do Koko (R$ 500,00),
pendente sem pagador (R$ 1.500,00) e custo mensal atual (R$ 2.333,80) seguem
inalterados.

## O que NÃO mudou

- **Parcelas futuras intocadas:** as 6 restantes de cada parcelamento seguem
  `pendente`, sem data de pagamento. A trava continua valendo para elas — não
  viram pagas por chegar o dia 10.
- Workspace, Tailscale e Hostinger **não foram tocados** (seguem com desembolso
  não confirmado, exibindo "—").
- Koko, CREMERJ, contabilidade, impostos e Mais Laudo verificados **byte a byte**
  como intocados.
- Nenhum movimento criado ou removido: a contagem por item é idêntica
  (MIR 10, seringa 10, Koko 20, contabilidade 10, impostos 3, Mais Laudo 6,
  CREMERJ 1). Nenhuma duplicidade.
- Nenhum valor de parcela alterado.

A pendência `pend-004` ("confirmar na fatura quais parcelas foram debitadas")
foi **fechada**, porque era exatamente este dado. O fechamento está registrado
na auditoria; as outras três pendências permanecem.

## Auditoria

O bloco `auditoria` da M26.21 permanece **intacto** (verificado por teste). A
confirmação entrou em `auditoria.complementos[]` como missão **M26.21A**, com
origem, usuário responsável, data/hora, total adicional, as pendências fechadas
e, para cada uma das 8 parcelas, o **status anterior e o novo**.

Backup antes da escrita: `custos-investimentos.local.json.bak-m26-21a`
(local e na VPS).

## Testes

| Suíte | Resultado |
|---|---|
| `m26-21a-confirmar-faturas-cartao.py` (dry-run) | 0 erros · travas de quantidade, competência e valor |
| `test-custos-socios-m26-21.py` | **191/191** (eram 165) — local e na VPS |
| `test-custos-socios-visual-m26-21.js` | **1356/1356** — chromium + firefox, 6 larguras, 6 abas |
| `pii_guard.py --self-test` | 60/60 |
| `check-access.sh` | OK, sem PII |
| `test-command-center-visual.js` | OK |

O teste visual passou a provar que **nenhuma linha do histórico de parcelas**
exibe "aguardando confirmação" — checagem feita nas linhas da tabela, não na aba
inteira, porque a frase segue legítima na nota de política que vale para as
parcelas futuras.

O script é **idempotente por construção**: ele só atua sobre parcelas em
`vencido_aguardando_confirmacao` e aborta se a quantidade, as competências ou a
soma não baterem exatamente com o fato informado.
