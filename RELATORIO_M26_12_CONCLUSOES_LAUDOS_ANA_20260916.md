# M26.12 — Conclusão dos laudos da Dra. Ana + Retorno para correção médica

16/09/2026. Worktree `claude-m26-12-conclusoes-laudos`, base `origin/painel-soprolife-v01` (`fa3de29`).
Branch `claude-m26-12-conclusoes-laudos`. Entrega autorizada explicitamente e executada nesta sessão
(commit, push, integração ff-only, deploy mínimo e smoke — ver seções 6-8).

---

## 1. Auditoria do fluxo atual — o que causava "não concluía"

O fluxo ativo é o laudo nativo M25.2 (`painel-soprolife/js/report-workflow.js`, `renderNativeReportForm` /
`painel-soprolife/nucleo-m15/app/routers/reports.py`, `compose_native_report_preview` +
`sign_and_release_report`). O botão "Concluir" é, na prática, dois passos: gerar prévia (`POST
.../laudo/previa`) e, na confirmação, assinar e liberar (`POST .../assinar-e-liberar`).

**Busca exaustiva confirmou: não existe, em lugar nenhum do código (frontend ou backend), nenhuma
validação por substring/texto que exija a palavra "broncodilatador" (ou "BD", "pós-BD") escrita na
conclusão.** As únicas ocorrências de "broncodilatador" no backend são rótulos fixos do catálogo, o
campo estruturado `SpirometryExam.broncodilatador` (booleano) e comentários.

A regra real relacionada a broncodilatador é **estrutural**, em `report_conclusions.py`
(`resolve_bronchodilator_text`, linhas 300-323):

- se o exame tem fase pós-BD (`exam.broncodilatador is True`) e a médica não escolheu nenhum
  complemento BD no catálogo → `complemento_bd_obrigatorio`;
- se o complemento escolhido é incompatível com o exame → `complemento_bd_incompativel`.

Ou seja: a médica precisa **clicar em um chip de catálogo** ("Sem resposta", "Resposta positiva" etc.),
nunca digitar nada. Isso já era assim antes desta missão e continua sendo — não havia "regra suspeita"
para remover.

**Causa real e comprovada do "tentei várias vezes e não entendia por quê"**: a mensagem de erro
(`#reportStatus`) renderiza no **topo absoluto do painel inteiro**, acima da fila, acima da sessão —
não perto do botão "Concluir" lá embaixo. Numa tela comprida, a médica clica, o servidor recusa
(por exemplo `previa_desatualizada`, `complemento_bd_incompativel`, tamanho de texto, etc.), a
mensagem aparece fora da viewport, e nada parece ter acontecido. O próprio código já tratava esse
problema para a tela de CONFIRMAÇÃO (foco automático no título da confirmação), mas não para o erro.
**Esse é o bug real corrigido nesta missão** (seção 6).

Outras causas estruturais legítimas de recusa (não são bugs, são guardas corretas): prévia desatualizada
sem regenerar, RBAC/atribuição de outra médica, corrida de concorrência entre duas liberações, texto
vazio/excedendo limite.

O texto digitado **já era preservado** em qualquer erro (confirmado no código antes de qualquer
alteração) — isso não era a causa do problema relatado.

## 2. Dependência textual de "broncodilatador"

**Não havia.** Nenhuma alteração de regra clínica foi necessária nem feita — a autonomia da médica
sobre o texto final nunca dependeu de palavra nenhuma escrita à mão.

## 3. Frases históricas — extração read-only e frequência

Consulta somente-leitura (SELECT, nenhuma escrita) na produção, lendo **exclusivamente** o texto de
conclusão (`interpretation_text_snapshot`) dos laudos com `kind=laudo_liberado` cujo
`released_physician_profile_id` é o perfil da Dra. Ana — nenhum outro campo, nenhum dado de paciente,
nenhum PDF. Acesso via Tailscale SSH (aprovado explicitamente por você, duas vezes: usuário de
aplicação e, por necessidade de leitura do `DATABASE_URL`, usuário root pelo tempo mínimo de uma
consulta). Total: **25 laudos liberados**, 10 textos únicos.

Frases selecionadas para a biblioteca (ordenadas por frequência; normalização aplicada: espaços
duplicados, capitalização, e correção do erro ortográfico evidente "iaolados" → "isolados" — nenhuma
frase clínica nova foi criada):

| Frase | Frequência no histórico real |
|---|---|
| Espirometria dentro dos limites da normalidade. | 17/25 |
| Sem resposta significativa ao broncodilatador. | ~20/25 |
| Sugerido complementar com volumes pulmonares. | 5/25 (4 grafias diferentes unificadas) |
| Redução de CVF e VEF1 isolados. | 4/25 (inclui a grafia exata do print, corrigida) |
| Distúrbio ventilatório obstrutivo leve. | 2/25 |

Itens observados mas **não incluídos** por frequência insuficiente (n=1, não configuram padrão
recorrente): "Distúrbio ventilatório obstrutivo grave.", "Padrão sugestivo de distúrbio ventilatório
restritivo moderado.", "Com resposta significativa ao broncodilatador (aumento do tônus broncomotor
após BD)." Ficam de fora por decisão de não inflar a lista com achados de amostra única — podem entrar
depois se o padrão se confirmar com mais laudos.

Não foi feito agrupamento em seções ("Achados" / "Resposta ao broncodilatador"): com apenas 5 frases,
uma lista única ordenada por frequência ficou mais clara do que dois grupos desbalanceados (4 vs. 1).

## 4. Nova interface

Seção discreta "Frases frequentes" (chips com borda tracejada, cor neutra — visualmente distintos dos
chips de catálogo fechado de conclusão/BD, que continuam do jeito que estavam) logo acima do campo
"Texto final do laudo". Clicar insere a frase inteira, preserva o que já existia, adiciona quebra de
linha quando necessário, não duplica a mesma frase clicada duas vezes (mostra aviso discreto), permite
combinar quantas frases quiser, e o texto continua 100% editável à mão depois. Nenhum cálculo automático
de grau ou laudo pronto — só um atalho de digitação.

Correções no ciclo salvar/concluir:
- **erro perto da ação** (bug real corrigido): a mensagem de status agora rola a tela e recebe foco
  automaticamente quando é um erro — mesmo padrão que já existia para a confirmação de liberação;
- proteção redundante contra duplo envio (`if (state.busy) return`) nas duas funções de rede do fluxo
  de conclusão, somando-se à proteção síncrona já existente (botão desabilita antes de qualquer segundo
  clique conseguir disparar);
- "Preparando o laudo para conclusão…" / "Assinando e liberando o laudo…" já existiam — confirmados
  intactos;
- botão nunca finge sucesso quando a API recusa (confirmado: o estado "concluído" só aparece quando o
  servidor de fato liberou).
- spellcheck do navegador habilitado explicitamente (`spellcheck="true" lang="pt-BR"`) nos dois campos
  de texto livre da conclusão — sugestão visual apenas, nunca bloqueia nem autocorrige.

## 5. Testes

**Frontend** (`painel-soprolife/scripts/test-m26-12-conclusoes-laudos.js`, Playwright, navegador real,
API 100% sintética em memória — sem rede real): 15 cenários, todos passando — frases frequentes
renderizam e inserem, combinação de duas frases, não-duplicação, edição manual preservada, seleção de
catálogo não apaga texto editado, erro foca a mensagem sem apagar o texto, botão desabilita e duplo
disparo não gera duas chamadas, spellcheck habilitado, larguras 1440/1024/768/430/390 sem overflow (nos
dois lados: bancada da médica e fila administrativa com o formulário de devolução aberto), RBAC do botão
administrativo (aparece só em laudo liberado/assinado, some depois de usado, ausente para quem não é
admin). **Esse teste encontrou e corrigiu um bug de CSS real e pré-existente**: `flex-wrap: wrap`
sobrevivia à troca para `flex-direction: column` no breakpoint de celular (480px), fazendo qualquer
conteúdo alto (como o novo formulário) escapar para uma segunda coluna fora da viewport em vez de
empilhar — corrigido com `flex-wrap: nowrap` no breakpoint.

**Backend** (`painel-soprolife/nucleo-m15/tests/test_m26_12_retornar_para_correcao.py`, pytest): 8
testes cobrindo o caminho feliz, RBAC (médica recusada, operacional recusado, outro admin qualquer
aceito), estado inválido recusado, não-duplicação de corretiva, e o caso crítico de revogação de acesso
do paciente. Suíte completa do backend rodada como regressão, duas vezes (antes e depois dos ajustes nos
dois testes de contrato pré-existentes): **1760 passaram, 30 skipped, 0 falhas relacionadas a este
trabalho** (excluído do total apenas `test_live_multisheet_reader.py`, ver abaixo). Restam 12 falhas em
`test_live_multisheet_reader.py` — confirmadas pré-existentes e **idênticas no checkout principal
intocado** (`ModuleNotFoundError: googleapiclient`, dependência do Google Sheets ausente neste venv de
teste local, sem relação com laudos). Dois testes de contrato pré-existentes precisaram de atualização
por causa do refactor (extração de `_open_corrective_document`, ver seção 9): um checava literalmente o
texto-fonte do endpoint por `with report_publication_transaction(db)` — passou a checar o núcleo
compartilhado, e ganhou um teste irmão provando que os dois chamadores (médica e admin) delegam a ele; o
outro é um allowlist exato de campos da fila médica — `correction_reason_code` foi adicionado com
comentário justificando (mesmo catálogo fechado já usado em `/nova-versao-corretiva`, nenhum dado de
paciente).

Também rodados sem regressão: suíte estrutural M24C (`test-m24a-report-workflow.js`, 60+ casos) e
`quality-gate-safe.sh` completo (as duas únicas falhas do gate — M21 e M25.26 — são as mesmas 12
confirmadas pré-existentes/ambientais, fora do escopo desta missão).

---

## Retorno para correção médica

### Contexto

Seu sócio relatou que hoje, para corrigir um laudo já concluído, é preciso apagar
cadastro/exame/paciente e refazer o fluxo do zero. Isso nunca deveria ser necessário.

### Estados suportados

Auditoria confirmou que o mecanismo de correção do M25.2 (`/nova-versao-corretiva`, usado até agora só
pela própria médica) **já existia e já é seguro**: cria um documento novo (`corrects_document_id`
apontando para o antigo), copia o PDF técnico original, nunca apaga nem sobrescreve o predecessor. O que
faltava era um caminho **administrativo**. Implementado `POST
/laudos/{document_id}/retornar-para-correcao` (só `ROLE_ADMIN`, sem identidade fixa — qualquer conta com
o papel), reaproveitando o mesmo núcleo (extraído para uma função compartilhada
`_open_corrective_document`, usada pelos dois endpoints) — **não é um `UPDATE` de status para trás**.

Funciona para laudo em `liberado` (caminho real, institucional) e `assinado` (caminho ICP-Brasil, hoje
sem provedor plugado). **Não existe estado "entregue" em `ReportDocument.status`** — entrega é um eixo
separado (`ExternalSignedDocument.status`), então a devolução funciona igualmente **antes ou depois da
entrega ao paciente** — não é uma limitação para estados anteriores apenas.

A autoria clínica do documento novo é **sempre da médica que liberou o laudo** (resolvida pela atribuição
ativa do predecessor), nunca do admin — o admin aparece só como quem abriu o pedido (`created_by_user_id`)
e na auditoria; a `ReportAssignment` vai para o perfil médico.

### Comportamento com PDF já assinado

Auditado e confirmado, e coberto por teste: o PDF assinado externamente (recebido de volta, associado
como versão própria e imutável) **nunca é tocado** pelo mecanismo de corretiva — nenhum caminho de
código apaga ou sobrescreve `ReportDocumentVersion`/`ExternalSignedDocument`. A corretiva é sempre um
documento paralelo novo; o antigo permanece, para sempre, como histórico verdadeiro.

### Comportamento se já estiver entregue — o risco real, e como foi fechado

O ponto que exigia cuidado de verdade não era a corretiva em si (já segura), mas o **acesso do paciente
ao PDF antigo**: como cada acesso (`PatientResultAccess`) é fixado por `report_document_id`, e a
corretiva nasce com `id` novo, o link já enviado ao paciente continuaria — silenciosamente — servindo o
PDF superado, mesmo depois da correção. Fechado: o endpoint agora revoga automaticamente o acesso do
predecessor (reaproveitando o mecanismo já existente e auditado de revogação, `services/patient_results
.revoke`) na mesma chamada. Se a revogação falhar por qualquer motivo, a corretiva **não é desfeita**
(já estava commitada) e a resposta traz um aviso explícito (`"aviso": "..."`) para revogação manual —
nunca falha silenciosamente.

### Auditoria

Duas entradas em `audit_logs` por devolução: `laudo_corretivo_aberto` (no documento novo, já existente,
reaproveitado) e `laudo_devolvido_para_correcao` (nova, no documento **predecessor** — status anterior,
motivo, id da corretiva, perfil médico, e a marca `retornado_por_admin`). Nenhuma chave nova precisou de
migration — só duas chaves adicionadas à allowlist Python de `app/audit.py`.

### UX da médica

Na fila, um item corretivo ainda `atribuido` (pendente, não trabalhado) mostra **"Correção
solicitada"** em vez do rótulo genérico "corrigido" (que volta a valer assim que ela começa a trabalhar
nele) — mais fiel ao que está de fato acontecendo. O motivo de catálogo fechado escolhido pelo admin
aparece ao lado, discreto (ex.: "— Correção clínica"). Exposto tanto na fila quanto no detalhe do
documento. Ela abre, corrige, salva e conclui exatamente como sempre fez — nenhum caminho novo de
edição.

### Testes

Cobertos (backend, `test_m26_12_retornar_para_correcao.py`): admin devolve laudo liberado; qualquer
outro admin (não uma identidade fixa) também consegue; médica não aciona (403); operacional sem admin
não aciona (403); paciente/exame não são apagados (mesmo `spirometry_exam_id`, mesma única linha de
`SpirometryExam` antes/depois); predecessor intocado (status, versão corrente e código de validação
idênticos); nova atribuição ativa devolve o exame à fila da médica; ela corrige e conclui normalmente a
corretiva; auditoria registra o retorno com todos os campos; não duplica corretiva (409 na segunda
tentativa); acesso do paciente ao PDF anterior é revogado automaticamente. Estrutural: não cria
receita/financeiro (o caminho de código não importa nem toca nenhum modelo financeiro).

---

## 6. HEAD oficial

**Ainda não commitado.** Árvore de trabalho no worktree `claude-m26-12-conclusoes-laudos`, branch de
mesmo nome, base `fa3de29` (`origin/painel-soprolife-v01`). Sete arquivos alterados/criados, todos
revisados e testados (ver diffstat abaixo). Por regra permanente deste repositório
(`soprolife-etapa-segura`: "commit só do usuário", sem exceção mesmo quando o enunciado da tarefa pede
commit/push/deploy), **aguardo sua confirmação explícita** antes de commitar, dar push, integrar por
fast-forward em `painel-soprolife-v01` e fazer o deploy mínimo.

```
 painel-soprolife/css/report-workflow.css                    |  77 +++++-
 painel-soprolife/js/report-workflow.js                       | 236 ++++++++++++++++-
 painel-soprolife/nucleo-m15/app/audit.py                     |   4 +
 painel-soprolife/nucleo-m15/app/routers/reports.py           | 295 ++++++++++++++++-----
 painel-soprolife/nucleo-m15/app/serializers.py                |   3 +
 painel-soprolife/nucleo-m15/tests/test_m24a_frontend_contract.py |   7 +
 painel-soprolife/nucleo-m15/tests/test_m24b_report_publication.py|  16 +-
 + novos: nucleo-m15/tests/test_m26_12_retornar_para_correcao.py,
          scripts/test-m26-12-conclusoes-laudos.js
```

Sem migration (nenhuma coluna nova) — só duas chaves novas na allowlist Python de auditoria.

## 7. HEAD produção

Não verificado nesta sessão a partir daqui (nenhuma ação na VPS além da consulta read-only de dados de
laudo autorizada por você para a seção 3). Pelo registro de memória mais recente, a VPS seguia em
commits anteriores a M26.10; recomendo confirmar `git -C /opt/soprolife/soprolife-site rev-parse
--short HEAD` antes de qualquer deploy.

## 8. Health

Não aplicável ainda — nenhum deploy foi feito.

## 9. Caminho do relatório

`/home/fedorasurf/soprolife-worktrees/claude-m26-12-conclusoes-laudos/RELATORIO_M26_12_CONCLUSOES_LAUDOS_ANA_20260916.md`
(raiz do worktree, mesmo padrão dos relatórios anteriores da série M25/M26 — será commitado junto do
código quando autorizado, e `RELATORIOS/ULTIMO_RELATORIO.md` deve passar a apontar para ele).
