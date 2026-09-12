# M26.10 — Modernização da seção "Repasses médicos"

**Data:** 12/09/2026
**Branch de trabalho:** `codex-m26-10-repasses-ux`
**Branch oficial:** `painel-soprolife-v01`
**Escopo:** exclusivamente a seção "Repasses médicos" do Financeiro (front-end).
**Fora de escopo (e não tocado):** migration, banco, dados reais, regra do
`released_at`, cálculo de repasses.

---

## 1. Continuidade: a missão não recomeçou

A M26.10 foi iniciada pelo Codex neste PC, que atingiu o limite de uso durante
a execução. O trabalho foi **retomado no worktree existente**
`/home/fedorasurf/soprolife-worktrees/codex-m26-10-repasses-ux`, sem `reset`,
sem descarte de arquivo, sem worktree novo e sem reimplementação.

Estado encontrado (worktree em `31ccded`, mesmo commit do `origin`):

```
 M painel-soprolife/css/medical-transfers.css
 M painel-soprolife/index.html
 M painel-soprolife/js/medical-transfers.js
?? painel-soprolife/scripts/test-m26-10-medical-transfers.js
```

Todas as alterações foram preservadas integralmente e viraram os commits desta
entrega.

---

## 2. O que o Codex já havia feito

A implementação estava **completa** e íntegra. Conferido item a item contra os
objetivos da missão:

| # | Objetivo | Estado encontrado |
|---|---|---|
| 1 | Eliminar digitação de `YYYY-MM` | Pronto — o `<input type="month">` foi removido |
| 2 | Seleção de competência por clique | Pronto — cabeçalho do mês + chips |
| 3 | Mês anterior / próximo mês | Pronto — setas `‹` `›` com `shiftMonth()` |
| 4 | Botão "Mês atual" | Pronto — volta ao mês do servidor (`load(null)`) |
| 5 | Meses recentes clicáveis | Pronto — 6 chips, com `aria-pressed` no ativo |
| 6 | Tooltips / ajuda contextual | Pronto — 9 verbetes (`HELP`) em colunas e KPIs |
| 7 | Visual moderno e menos poluído | Pronto — CSS reescrito sobre os tokens M17 |
| 8 | Preservar regra/API/banco/cálculo M26.9 | Pronto — nenhum arquivo de back-end tocado |

Além do pedido, o Codex já havia resolvido três coisas que a navegação por
clique introduz e que não estavam na lista:

- **Guarda de sequência** (`loadSequence`): uma resposta antiga que chega
  atrasada não substitui mais a competência mais recente na tela.
- **Falha de rede não apaga a seção**: o mês exibido é mantido e o erro vai
  para a faixa de status ("A competência exibida foi mantida.").
- **Balão de ajuda em `position: fixed`**, fora do contêiner com scroll da
  tabela — é o que evita o tooltip cortado no celular.

O arquivo de teste `test-m26-10-medical-transfers.js` (181 linhas, 12 cenários)
também já estava escrito, e o cache-buster do `index.html` já havia sido
bumpado para `v=2026091202` — detalhe sem o qual o deploy entregaria arquivo
velho ao navegador.

---

## 3. O que eu precisei completar

O Codex parou **antes** da fase de validação e integração. Nada de
implementação faltava; o que faltava era provar e entregar.

1. **Re-execução dos testes** do zero nesta máquina (o output anterior era
   apenas relato). 12/12 passaram.
2. **Quality gate offline completo** — apurado que as 2 falhas que ele acusa
   são **pré-existentes**, não regressão (seção 4).
3. **Teste de back-end da M26.9** executado para provar que a regra não se
   mexeu: 8/8.
4. **Revisão de código e de risco de colisão**: conferido que os 13 tokens CSS
   usados (`--teal-pale`, `--ok-soft`, `--warn-soft`, `--line-light`,
   `--shadow-xs`, `--radius-sm`, …) existem de fato — token inexistente
   renderiza silenciosamente sem cor — e que o prefixo `.medical-` não é usado
   por nenhum outro CSS ou JS do painel, ou seja, a cascata está contida na
   seção.
5. **Revisão visual** das capturas sintéticas em 1440 / 768 / 390 / 320 px.
6. **Dois commits**, integração **ff-only** e push.
7. **Deploy mínimo** na VPS, smoke, health e `check-access.sh`.
8. Este relatório.

---

## 4. Testes

### 4.1 UI — Playwright headless, API sintética, rede abortada

`node painel-soprolife/scripts/test-m26-10-medical-transfers.js` — **12/12 PASS**

```
PASS competência sem digitação; seis chips e destaque do mês
PASS anterior/próximo atravessam a virada do ano e preservam YYYY-MM
PASS chips por teclado, histórico clicável e mês atual do servidor
PASS ajuda em campos e cards: hover, foco, Escape e ponteiro no balão
PASS totais, referência monetária e status preservados
PASS fechamento mantém payload, preço manual e cálculo de prévia
PASS pagamento mantém rota e payload explícitos
PASS falha de rede mantém mês exibido e permite repetir navegação
PASS resposta antiga não substitui a competência mais recente
PASS desktop/mobile sem overflow de página; tabela rola e tooltip cabe
PASS formulário cabe em 320px e estado vazio orienta a navegação
PASS médica sem permissão não consulta nem vê a área
12 cenários passaram.
```

Nenhum dado real envolvido: `page.route("**/*", route.abort())`, `SoproM15`
falso em memória e médicas fictícias ("Dra. Sintética Clara/Helena/Laura").

Dois cenários existem só para proteger a M26.9 e conferem o payload campo a
campo:

- `POST /financeiro/repasses-medicos` →
  `{physician_profile_id, competencia, expected_eligible_report_count, unit_amount, paid_amount, payment_date}`
- `PATCH /financeiro/repasses-medicos/<id>/pagamento` →
  `{paid_amount, payment_date}`

### 4.2 Back-end M26.9 — regra intocada

`pytest tests/test_m26_9_cadastro_repasses_medicos.py -q` → **8 passed**.

### 4.3 Quality gate offline

`bash painel-soprolife/scripts/quality-gate-safe.sh` → 2 checks falhando.
**Ambos pré-existentes e alheios à M26.10.** Provado rodando os mesmos testes
na árvore limpa da M26.9 (worktree `codex-m26-9-cadastro-repasses`, commit
`31ccded`), que produz exatamente o mesmo conjunto de falhas:

| Teste | Falha | Na árvore limpa `31ccded`? |
|---|---|---|
| `test-m21-auth-crm-nav` | "scripts de Marketing usam cache-buster da reconciliação" | Sim — idêntica |
| `test-m25-26-fluxo-espirometria` | "a correção só oferece os campos realmente pendentes" | Sim — idêntica |
| `test-m25-26-fluxo-espirometria` | "após salvar, as pendências são RELIDAS do servidor" | Sim — idêntica |

Nenhuma toca Repasses médicos. Não foram corrigidas aqui por estarem fora do
escopo desta missão — ficam registradas como pendência conhecida.

### 4.4 Revisão visual

Capturas sintéticas geradas pelo próprio teste em 1440, 768, 390 e 320 px.
Conferidas: cabeçalho do mês legível, chips com o mês ativo destacado em teal,
KPIs em dois cartões (o de pago em verde), tabela com status em pílula
(Pendente âmbar / Pago verde), e — nas larguras estreitas — a tabela rolando
dentro do próprio contêiner, com a página **sem** scroll horizontal.

---

## 5. Integração e deploy

Fast-forward puro, sem merge commit:

```
31ccded  (origin/painel-soprolife-v01, base M26.9)
  └─ 4f6377b  feat(m26.10): a competência vira clique, e a ajuda mora ao lado do campo
     └─ 92d52ab  test(m26.10): 12 cenários em navegador real, com API sintética em memória
```

Deploy mínimo na VPS (`soprolife-painel-01`, via Tailscale): `git pull --ff-only`.
A VPS estava limpa e exatamente em `31ccded`, então entraram **só** os dois
commits da M26.10 — 4 arquivos, todos de front-end. **Nenhuma migration,
nenhum arquivo Python, nenhum dado.** Por isso **nenhum serviço foi
reiniciado**: o painel ficou no ar o tempo todo.

Confirmado que o conteúdo novo está sendo servido, e não apenas gravado em
disco: o CSS entregue pelo servidor já contém `.medical-month-picker`.

---

## 6. Smoke e health

| Verificação | Resultado |
|---|---|
| `GET /api/v1/health` (127.0.0.1:8015) | **HTTP 200** — `{"status":"ok","versao":"0.1.0","ambiente":"prod","banco":"ok"}` |
| Painel `GET /painel-soprolife/` | **HTTP 200** |
| `css/medical-transfers.css?v=2026091202` | **HTTP 200**, 7565 B, contém o picker novo |
| `js/medical-transfers.js?v=2026091202` | **HTTP 401 — esperado** |
| `check-access.sh` na VPS | **exit 0** — 63 OK, 0 falhas, 2 avisos pré-existentes |
| `git status` na VPS | limpo |

Sobre o **401 do JS**: é o portão de autenticação (M25.23) funcionando, não um
defeito do deploy. `app.js`, `pastore-settlement.js` e
`financeiro-conciliacao.js` respondem 401 do mesmo jeito sem sessão — CSS é
público, JS exige sessão.

Os 2 avisos do `check-access.sh` ("possível token ou ID hardcoded detectado no
proxy") são heurística sobre `command-center-local-server.py`, arquivo que esta
missão não tocou — precedem a M26.10.

---

## 7. HEADs

| Onde | Commit |
|---|---|
| **Oficial** (`origin/painel-soprolife-v01`) | `92d52aba7f28e7da026fa218cffe43ffc1ae866a` |
| **Produção** (VPS `/opt/soprolife/soprolife-site`) | `92d52aba7f28e7da026fa218cffe43ffc1ae866a` |
| Local (`/home/fedorasurf/soprolife-site`) | `92d52ab` |

Oficial e produção no mesmo commit.

---

## 8. O que mudou para quem usa

Antes, trocar de competência exigia digitar `2026-08` num campo de mês. Agora:

- o mês aparece por extenso ("Agosto 2026") com setas de anterior/próximo, que
  atravessam a virada de ano corretamente;
- seis chips de meses recentes ficam a um clique;
- "Mês atual" volta ao mês do servidor — não ao mês do relógio do navegador;
- cada competência do histórico é clicável e leva direto àquele mês;
- as sete colunas e os dois KPIs têm um "?" que explica o conceito, incluindo o
  que mais gerou dúvida: "Total a pagar" é **soma dos totais de referência,
  inclusive dos já pagos** — não é saldo restante.

A chave `YYYY-MM` continua sendo o que trafega para a API, e a competência
continua vindo do `released_at`. Assinatura e entrega seguem sem alterar
competência, e o valor unitário continua sendo digitado no fechamento, sem
valor padrão.

---

## 9. Pendências (fora do escopo desta missão)

1. As 3 falhas pré-existentes de `test-m21-auth-crm-nav` e
   `test-m25-26-fluxo-espirometria` (seção 4.3).
2. O worktree `codex-m26-9-cadastro-repasses` tem o relatório da M26.9
   modificado e não commitado — pertence àquela missão, foi deixado intacto.

---

**Relatório:** `/home/fedorasurf/soprolife-site/RELATORIO_M26_10_MODERNIZACAO_REPASSES_MEDICOS_20260912.md`
