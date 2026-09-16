# M26.13 — Laudo efetivo, produção da médica, repasse e limpeza operacional

16/09/2026. Worktree `claude-m26-13-laudos-efetivos-repasse`, base `painel-soprolife-v01` (`cfe71fd`,
o mesmo commit já em produção ao final da M26.12). Trabalho técnico e testes concluídos; **commit/push/
integração/deploy aguardando autorização explícita** (mesma regra permanente do repositório —
`soprolife-etapa-segura`: "commit só do usuário" — e desta vez o enunciado também não pediu deploy
incondicional, só relatar "se houver").

---

## 1. Causa real de cada problema encontrado

### 1.1 — "O número do laudo mudou (35 → 36)"

**Não é um bug.** `allocate_public_code` (`nucleo-m15/app/ids.py`) é uma sequência atômica por prefixo
de tabela (`LAU-NNNNNN`, `SELECT ... FOR UPDATE`, nunca reaproveita valor emitido). Uma corretiva
(mecanismo da M26.12) é sempre um `ReportDocument` NOVO, com `public_code` novo — por desenho, desde a
M25.2. O predecessor mantém o próprio número para sempre. Isso já estava certo; o problema real estava
uma camada acima, em quem CONTA essas linhas.

### 1.2 — Repasse médico contava a correção como um segundo laudo

**Confirmado, bug real.** `eligible_report_count` (`nucleo-m15/app/services/medical_transfers.py`)
contava `DISTINCT ReportDocument.id` filtrando só por `released_physician_profile_id` +
`released_at` na janela da competência — **sem excluir corretivas**. Um exame corrigido e reliberado
virava dois laudos elegíveis (o original + a corretiva), cada um contando na competência da SUA
PRÓPRIA `released_at`. Corrigido — ver seção 2.

### 1.3 — Não existia contador de produção por médica

Não era um bug, era uma lacuna: não havia nenhum endpoint nem tela agregando "quantos laudos, quantos
corrigidos, quantos assinados/entregues, quantos pendentes" por médica. A única aproximação existente
(`quantidade_laudos_elegiveis` do repasse) carregava o mesmo problema da seção 1.2 e não detalhava nada
além do total.

### 1.4 — Acompanhamento operacional mostrando laudos já resolvidos como pendentes

**Confirmado, bug real, e mais sério do que parecia.** `ReportDocument.status` **nunca muda** quando um
laudo é superado por corretiva (predecessor fica `liberado` para sempre — decisão deliberada da M26.12,
para nunca reescrever histórico) e **também nunca muda** quando o laudo é entregue ao paciente (entrega
é rastreada só em `ExternalSignedDocument.status`, tabela separada). Como consequência, **todo** laudo
liberado — recém-liberado, já assinado, já entregue, ou já corrigido — mostrava exatamente o mesmo chip
"Concluído — aguardando assinatura qualificada" para sempre, e `/laudos` (o endpoint que alimenta o
Acompanhamento operacional) trazia todos eles sem distinção, sem nenhum filtro que os tirasse da fila
ativa. O rótulo em si não mentia sobre o status *daquele documento*; o problema é que a lista nunca
soube que aquele documento tinha deixado de ser relevante para o trabalho do dia.

## 2. Regra final adotada para "laudo efetivo"

> **O documento-RAIZ (`corrects_document_id IS NULL`) é o único que conta como laudo efetivo, sempre
> pela competência da SUA PRÓPRIA `released_at`. Uma corretiva nunca conta, mesmo depois de liberada de
> novo — ela é a mesma produção clínica do original, só corrigida.**

Por que a competência é sempre a do original, e não a da correção: uma competência já pode estar
**fechada e paga** (`PhysicianTransfer.status = "Pago"`) quando uma correção acontece meses depois.
Deixar a correção "mover" o laudo para o mês novo reabriria retroativamente uma competência já
encerrada — o pior resultado possível para um dado de folha de pagamento. Contar sempre pela
`released_at` do original é estável por construção: corrigir um laudo nunca cria, nunca duplica e nunca
desloca uma contagem já fechada.

Essa é a única regra nova desta missão que envolve julgamento de negócio explícito — documentada aqui
por transparência, não porque tenha ficado ambígua a ponto de eu precisar parar: a alternativa (mover
para a competência da correção) tem uma desvantagem concreta e verificável (reabrir competência paga) e
nenhuma vantagem que compensasse.

## 3. Como ficou a lógica de contagem e repasse

- `eligible_report_count` (repasse, já existia) ganhou o filtro `corrects_document_id IS NULL`. Testado:
  liberar um exame, corrigi-lo, liberar a corretiva de novo — a contagem elegível continua em 1, na
  competência do original, antes e depois da correção.
- Nova função `physician_production_summary` (mesmo arquivo) e novo endpoint `GET
  /financeiro/repasses-medicos/{physician_profile_id}/producao?competencia=AAAA-MM` (`ROLE_GESTOR`,
  mesmo papel do repasse — médica e operacional recebem 403), devolvendo:
  - `efetivos` — mesma regra acima;
  - `corrigidos` — quantos dos efetivos da competência já têm corretiva aberta;
  - `assinados` / `entregues` / `aguardando_assinatura` — resolvidos pelo documento **VIGENTE** de cada
    laudo efetivo (a corretiva, se existir; senão o próprio original) contra `ExternalSignedDocument`;
  - `pendentes` — a bancada ATUAL da médica (`atribuido`/`em_elaboracao`), **não filtrado por
    competência** (um laudo ainda não concluído não tem `released_at` para filtrar por mês);
  - `distribuicao_conclusao` — contagem por código do catálogo FECHADO de conclusão
    (`report_conclusions.CONCLUSION_OPTIONS`, 17 códigos + personalizado), sempre lida do documento
    VIGENTE. **Nenhuma inferência clínica nova**: o campo já existia, estruturado, gravado em toda
    versão liberada (`ReportDocumentVersion.conclusion_code_snapshot`) — só nunca tinha sido agregado.
- Área "Repasses médicos" (`js/medical-transfers.js`) ganhou um botão "Ver produção" por médica, que
  abre um painel com 6 cartões (efetivos/corrigidos/assinados/entregues/aguardando assinatura/pendentes)
  e um donut por grupo de conclusão (normal/obstrutivo/restritivo/misto/inespecífico/personalizado —
  os 6 grupos já existentes no catálogo, nunca uma categoria nova). Cache por médica+competência; o
  painel fica **fora** da tabela (mesmo lugar do formulário "Registrar repasse") depois que um primeiro
  desenho dentro da linha da tabela se mostrou quebrado no celular — ver seção 5 dos testes.

## 4. Como ficou a lógica do acompanhamento operacional

`GET /laudos` (`list_report_documents_operational`) ganhou dois booleanos calculados por linha
(`has_corrective_successor`, `is_delivered`, via `EXISTS` correlacionado — sem N+1) e dois parâmetros
novos, no mesmo padrão já usado para exames encerrados (`incluir_encerrados`/`somente_encerrados`):

- `incluir_superados` (padrão `false`) — por padrão, um documento com corretiva aberta OU já entregue
  **não aparece** na fila ativa;
- `somente_superados` — traz só esses, para o histórico.

A fila ativa (`Acompanhamento operacional`) ficou visualmente igual para o que continua em aberto; o que
foi resolvido saiu e passou a morar numa seção recolhida nova, "Históricos operacionais", no mesmo
padrão de `<details>` já usado para "Históricos encerrados" — nada foi apagado, só deixou de poluir a
fila de trabalho.

## 5. Arquivos alterados

```
 painel-soprolife/css/medical-transfers.css                        |  58 +++
 painel-soprolife/index.html                                       |   4 +-
 painel-soprolife/js/medical-transfers.js                          | 151 ++++++-
 painel-soprolife/js/report-workflow.js                            |  45 ++
 painel-soprolife/nucleo-m15/app/routers/finance.py                |  26 ++
 painel-soprolife/nucleo-m15/app/routers/reports.py                |  51 ++-
 painel-soprolife/nucleo-m15/app/services/medical_transfers.py     | 187 ++++++-
 painel-soprolife/nucleo-m15/tests/test_m24a_frontend_contract.py  |   7 +
 painel-soprolife/nucleo-m15/tests/test_m25_29e_pos_assinatura_downloads.py | 6 +-
 painel-soprolife/scripts/test-m26-12-conclusoes-laudos.js         |  23 +-
 + novos: nucleo-m15/tests/test_m26_13_laudos_efetivos_producao.py,
          scripts/test-m26-13-producao-medica.js
```

`test_m25_29e_pos_assinatura_downloads.py` mudou por um achado à parte, não relacionado ao escopo desta
missão: o bump do carimbo de cache-busting de `report-workflow.css/js` feito na M26.12
(`2026090102 → 2026091601`) havia deixado um teste de regressão pré-existente **quebrado desde então**
(`test_assets_alterados_tem_cache_busting_atual`, um pino literal do valor esperado). Encontrado ao
rodar a suíte completa nesta missão e corrigido — atualizei o valor esperado, não a garantia que o
teste protege. Não é uma falha de produção: o carimbo correto já estava publicado; era só a suíte de
testes que não tinha acompanhado.

## 6. Testes executados

**Backend** (`nucleo-m15/tests/test_m26_13_laudos_efetivos_producao.py`, pytest, dirigido pela API real
via helpers reaproveitados de `test_m25_2_native_report.py`/`test_m25_29d_...`): 11 testes —
correção não soma segundo laudo elegível; endpoint de repasse reflete a mesma regra; produção mostra
efetivo+corrigido sem duplicar, usando a conclusão do documento VIGENTE; sem correção usa a conclusão do
próprio original; assinado/entregue contam pelo vigente; pendente reflete a bancada atual sem filtrar
por competência; médica e operacional recebem 403; médica inexistente 404; laudo superado some da fila
ativa e aparece no histórico com o motivo certo; laudo entregue idem; laudo ativo normal continua visível
por padrão. Todos passando.

**Suíte completa do backend**, rodada duas vezes (antes e depois do achado da seção 5): sem falhas
relacionadas a este trabalho (excluído apenas `test_live_multisheet_reader.py`, ausência de
`googleapiclient` no venv local de teste — confirmada idêntica no checkout oficial intocado, nada a ver
com laudos).

**Frontend** (Playwright, navegador real, API sintética em memória):
- `scripts/test-m26-13-producao-medica.js` (novo) — 6 cenários: botão "Ver produção" por médica, nasce
  fechado; abrir busca o endpoint certo e mostra os 6 cartões; donut renderiza com legenda por grupo;
  fechar pelo cabeçalho do painel não refaz a chamada; reabrir a mesma médica usa cache; larguras
  1440/768/390 sem overflow. **Este teste encontrou um problema real**: a primeira versão do painel de
  produção morava dentro de uma `<tr>` da tabela de repasses (que tem scroll horizontal próprio) e por
  isso nunca conseguia refluir no celular — o grid de cartões ficava cortado. Corrigido movendo o painel
  para fora da tabela, no mesmo padrão já usado pelo formulário "Registrar repasse".
- `scripts/test-m26-12-conclusoes-laudos.js` (estendido) — 16 cenários (15 já existentes + 1 novo: laudo
  superado sai da fila ativa e aparece no histórico recolhido com o motivo certo).
- `scripts/test-m26-10-medical-transfers.js` (existente, sem alteração) — 12 cenários, sem regressão.
- `scripts/test-m24a-report-workflow.js` (existente, sem alteração) — suíte estrutural completa, sem
  regressão.
- `quality-gate-safe.sh` completo — as duas únicas falhas (M21, M25.26) são as mesmas já confirmadas
  pré-existentes/ambientais na M26.12, fora do escopo desta missão.

## 7. HEAD oficial

**Ainda não commitado.** Trabalho completo no worktree `claude-m26-13-laudos-efetivos-repasse`, base
`cfe71fd` (idêntica ao `origin/painel-soprolife-v01` e à VPS neste momento). Sem migration — nenhuma
coluna nova. Aguardando sua confirmação para commitar, dar push, integrar por fast-forward e (se
autorizado) fazer o deploy mínimo.

## 8. HEAD da produção

Sem deploy nesta missão até este ponto. A VPS segue em `cfe71fd` (confirmado limpo agora mesmo,
`git status --short` vazio).

## 9. Health/smoke

Não aplicável ainda — nenhum deploy foi feito.

## 10. Caminho do relatório

`/home/fedorasurf/soprolife-worktrees/claude-m26-13-laudos-efetivos-repasse/RELATORIO_M26_13_LAUDO_EFETIVO_REPASSE_OPERACIONAL_20260916.md`
(raiz do worktree, mesmo padrão da M26.12 — será commitado junto do código quando autorizado).
