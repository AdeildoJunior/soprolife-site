# M25.14 — Correção da liberação do laudo e destravamento da UI após erro

**Data:** 09/08/2026
**Worktree:** `/home/fedorasurf/soprolife-worktrees/claude-m25-13-validacao-producao-laudos`
**Branch:** `claude-m25-13-validacao-producao-laudos`

## M25.14 — LIBERAÇÃO DO LAUDO CORRIGIDA E COMPROVADA EM PRODUÇÃO

`LAU-000001` foi liberado no PostgreSQL de produção, **sem erro 500**, sobre o
cenário 100% fictício da M25.13, com `M15_REPORTS_MODE=pilot` inalterado.

---

## 1. Proveniência confirmada antes de editar

| Item | Antes | Depois |
| --- | --- | --- |
| HEAD local | `bbd9ebc` | `18e2396` |
| `origin/painel-soprolife-v01` | `bbd9ebc` | `18e2396` |
| HEAD da VPS | `bbd9ebc` | `18e2396` |
| `git status` local e VPS | limpos | limpos |
| Integração | — | **fast-forward** (`origin` era ancestral) |

Nenhuma mudança concorrente apareceu. Sem `reset --hard`, sem force push, sem
rebase, sem apagar worktree.

---

## 2. Causa raiz

A liberação institucional grava o valor **`liberada_institucional`
(22 caracteres)**. Ele não cabia em `VARCHAR(20)`, e o PostgreSQL abortava a
transação com `StringDataRightTruncation` → HTTP 500.

O modelo era internamente contraditório: a CHECK constraint
`ck_report_documents_clinical_state_coherent` **exigia** esse valor exato quando
`status = 'liberado'`, numa coluna que não o comportava. Nenhum laudo jamais
pôde ser liberado em produção.

**Por que a suíte não pegava:** `tests/conftest.py:45` usa SQLite, que ignora o
limite de `VARCHAR`. O PostgreSQL o impõe. Teste verde, produção quebrada.

### 2.1 A varredura direcionada achou uma SEGUNDA coluna

Antes de escrever a migration, `scripts/auditar_larguras_status_laudo.py`
cruzou os literais das CHECK constraints com o limite declarado de cada coluna,
nas tabelas `report_*`, `qualified_signature_*` e `physician_*` — 150 pares
verificados:

```
report_documents.signature_status: VARCHAR(20) não comporta 'liberada_institucional' (22)
report_signatures.status:          VARCHAR(20) não comporta 'liberada_institucional' (22)
```

`report_signatures.status` é gravado com o mesmo valor em
`app/routers/reports.py:3896`, **na mesma transação da liberação**. Corrigir só
a primeira coluna teria movido o erro 500 alguns milissegundos adiante. A
exigência de varrer todas as colunas de status do domínio foi o que evitou um
segundo ciclo de deploy quebrado.

Nenhuma outra incompatibilidade foi encontrada no domínio de laudos.

---

## 3. Migration criada

`migrations/versions/b8e4d2a71c53_m25_14_widen_signature_status.py`
(revises `d4a71c88b2e6`)

### Schema antes → depois

| Tabela.coluna | Antes | Depois |
| --- | --- | --- |
| `report_documents.signature_status` | `VARCHAR(20)` | **`VARCHAR(40)`** |
| `report_signatures.status` | `VARCHAR(20)` | **`VARCHAR(40)`** |

### Constraints preservadas

`ck_report_documents_clinical_state_coherent` e
`ck_report_signatures_status_valido` continuam presentes — verificado por
consulta a `pg_constraint` antes, depois do upgrade, depois do downgrade e
depois do upgrade de novo: **2 de 2 em todos os estados**. A regra clínica não
foi removida nem enfraquecida, e **nenhum valor funcional foi encurtado para
caber**.

### Dados preservados

`ALTER COLUMN TYPE` alargando não toca em dado existente. O **downgrade é
fail-closed**: recusa rodar se houver qualquer valor maior que 20 caracteres,
porque truncar o status de um laudo liberado destruiria evidência clínica.

### Modelo

`SIGNATURE_STATUS_LEN = 40` passa a ser a fonte única da largura
(`app/models.py`), com `assert` que quebra se um status novo não couber — o
modelo não volta a divergir do banco silenciosamente.

---

## 4. Testes em PostgreSQL real

`tests/test_m25_14_liberacao_postgres.py` — **8 casos, todos passando**. O banco
é criado do zero e migrado com `alembic upgrade head`, então o que está sob
teste é a migration, não `create_all`.

| Prova | Como |
| --- | --- |
| `liberada_institucional` cabe | `information_schema` nas duas colunas |
| a transição para `liberado` funciona | grava e relê status, `released_at`, `validation_code` e a assinatura |
| a constraint continua válida | recusa `liberado` sem `released_at`; recusa status fora do domínio (mesmo cabendo em 40) |
| rollback não deixa estado parcial | `flush` + `rollback` → documento volta a `atribuido`, zero assinaturas |
| modelo e banco não divergem | largura declarada == largura migrada |

**SQLite não é aceito como prova:** sem `M25_14_TEST_DATABASE_URL` os testes são
**pulados**, nunca aprovados por omissão.

### Ciclo de migration verificado

```
upgrade head   → 40 / 40   | checks 2 de 2
downgrade -1   → 20 / 20   | checks 2 de 2
upgrade head   → 40 / 40   | checks 2 de 2
```

No estado pré-M25.14 a largura é **20** — abaixo dos 22 exigidos —, então o
teste de largura falha, como deve.

---

## 5. Correção do congelamento da UI

**12 handlers** de `js/report-workflow.js` tinham:

```js
} catch (e) { announce(...); render(); }
finally     { state.busy = false; }
```

O `render()` pintava a tela ainda com `busy = true` (botões desabilitados) e
nada repintava depois. Agora:

```js
} finally { state.busy = false; render(); }
```

A mensagem de erro continua visível porque quem a exibe é o `announce`, que
permanece no `catch`. Comportamento clínico das ações inalterado.

`scripts/test-m25-14-destravar-ui.js` trava o contrato: percorre todos os blocos
`finally`, exige que todo bloco que zera o flag repinte **depois** de zerá-lo, e
impede o retorno do padrão antigo.

---

## 6. Correção da mensagem enganosa

"Marcar conteúdo pronto para assinatura" respondia *"Gere uma prévia antes de
preparar a assinatura"* com a prévia já gerada — instrução impossível.

Essa ação pertence ao fluxo de **anotação sobre o PDF da MIR** e exige um
rascunho composto (`kind = "rascunho"`); a prévia do laudo nativo não serve.
Correção mínima, sem misturar os fluxos:

- a ação só habilita quando existe rascunho composto (`hasComposedDraft`);
- sem ele, fica desabilitada e explica o porquê;
- o bloco diz a que caminho pertence e aponta **"Assinar e liberar laudo"** para
  o laudo próprio;
- a mensagem do backend deixa de dar ordem impossível.

O botão **"Assinar e liberar laudo" foi preservado** e é o que efetivamente
liberou o laudo.

---

## 7. Testes executados antes do deploy

| Suíte | Resultado |
| --- | --- |
| Backend completo (SQLite) + PostgreSQL | **974 passaram**, 22 pulados, **0 falhas** |
| `test_m25_14_liberacao_postgres.py` | 8/8 |
| Migrations (`test_migrations.py`) | passa; head única atualizada para `b8e4d2a71c53` |
| `test-m25-14-destravar-ui.js` | 16/16 |
| `test-m25-12-resgate-laudos.js` | todos |
| Todos os `scripts/test-*.js` | passam |
| `node --check report-workflow.js` | OK |
| `alembic upgrade/downgrade/upgrade` | OK, constraints preservadas |

**Falhas preexistentes, não causadas por esta missão:**

- `tests/test_live_multisheet_reader.py` (12) — `ModuleNotFoundError:
  googleapiclient`. Ambiental: o Marketing usa venv separado em produção. O
  arquivo não menciona laudos e não foi tocado.
- `scripts/test-m24a-browser-e2e.js` — verificado com meus dois arquivos
  revertidos (`git stash`) no commit base `bbd9ebc`: **falha idêntica**.

---

## 8. Commits

| Commit | Conteúdo |
| --- | --- |
| `5eb5a90` | schema: as duas colunas para `VARCHAR(40)`, migration, varredura, testes PostgreSQL |
| `18e2396` | UI: 12 handlers destravados, UX do fluxo antigo, cache-bust `2026080902` |

Integrados em `painel-soprolife-v01` por **fast-forward**; push normal.

---

## 9. Backup e deploy

**Backup (antes de tudo):** `/opt/soprolife/backups/m25-14/20260809T220653Z/`

| Item | Detalhe |
| --- | --- |
| Dump PostgreSQL | `soprolife_m15.dump`, 261.493 bytes, formato custom |
| **Validação** | `pg_restore --list` → **376 itens**, incluindo `TABLE DATA report_documents` e `report_signatures` |
| `m15.env` | copiado root-only (`0600`); conteúdo **não impresso** |
| HEAD anterior | `bbd9ebc…` gravado em `HEAD_ANTERIOR.txt` |
| `reports_mode` no backup | `pilot` |

**Deploy:** `git merge --ff-only` na VPS → `18e2396`; `alembic upgrade head`;
restart de `soprolife-m15-api` (modelo novo) e do painel estático (cache-bust).

### Validação pós-deploy

| Verificação | Resultado |
| --- | --- |
| `soprolife-m15-api` / `painel` / `painel-loopback` | os três `active` |
| Health HTTPS de produção | `{"status":"ok","ambiente":"prod","banco":"ok"}` — 200 |
| `M15_REPORTS_MODE` | **`pilot`** (inalterado) |
| Alembic head na produção | **`b8e4d2a71c53`** |
| Larguras em produção | `20 → 40` nas duas colunas |
| CHECK constraints | 2 de 2 presentes |
| Estático servido | `report-workflow.js?v=2026080902` e `.css?v=2026080902` |
| Correção presente no JS servido | `hasComposedDraft` encontrado |

---

## 10. Reteste do mesmo cenário fictício

Nenhum paciente, exame ou laudo novo foi criado. Reusados **PES-000029 /
ESP-000016 / LAU-000001**, com a sessão da Dra. Ana que sobreviveu ao deploy
(reload forçado sem cache para carregar o JS novo).

| Passo | Resultado |
| --- | --- |
| Abrir `LAU-000001` | ✅ |
| Conclusões preservadas | ✅ **DVO Leve** e **RBD+** ainda selecionados |
| Texto preservado | ✅ "Distúrbio ventilatório obstrutivo leve. / Com resposta significativa ao broncodilatador." |
| Prévia preservada | ✅ (não foi preciso gerar de novo) |
| "Assinar e liberar laudo" | ✅ |
| Confirmação consciente | ✅ `alertdialog` com "Sim, assinar e liberar laudo" |
| Confirmar | ✅ **"Laudo liberado. Código de verificação Y6ZVEF9XZY7Z."** |
| **Erro 500** | ❌ **não ocorreu** |

### Estado final no banco

| Campo | Valor |
| --- | --- |
| `status` | **`liberado`** |
| `signature_status` | **`liberada_institucional`** (os 22 caracteres agora cabem) |
| `released_at` | 09/08/2026 19:09:52 −03 |
| `validation_code` | `Y6ZVEF9XZY7Z` |
| `released_by_user_id` / `released_physician_profile_id` | preenchidos |
| `report_signatures` | `liberada_institucional`, provider `institutional_release` — **a segunda coluna também gravou** |
| Versões | v1 `original` (1.546 B) · v2 `laudo_previa` (123.496 B) · v3 **`laudo_liberado`** (124.338 B, com `validation_code_snapshot`) |
| Auditoria | `laudo_assinado_e_liberado` carimbado |

### PDF liberado (texto extraído do arquivo baixado)

- **"DOCUMENTO LIBERADO"** (não mais "PRÉVIA")
- **Código de verificação: `Y6ZVEF9XZY7Z`** (antes vinha vazio)
- Selo "ASSINADO ELETRONICAMENTE — LIBERAÇÃO INSTITUCIONAL"
- Unidade **Pastore Ipanema** com endereço
- **Dra. Ana Cristina do Nascimento Cunha · CRM-RJ 5262307-5 · RQE 58224**
- **PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE** mantido
- Ressalva impressa: a liberação não constitui assinatura ICP-Brasil

### Downloads separados após a liberação

| Documento | Tamanho |
| --- | --- |
| Exame técnico (MIR) — v1, intacto | 1.546 bytes |
| Laudo médico SoproLife — **v3 liberado** | 124.338 bytes |

### Erro controlado — a tela se recupera sem F5

Falha 500 **injetada no cliente** apenas na rota de adendo (a requisição não
chega ao servidor; nada é publicado). Resultado:

| Antes (M25.13) | Agora |
| --- | --- |
| 4 botões desabilitados, só F5 recuperava | **0 botões desabilitados** |
| — | mensagem de erro visível na tela |
| — | ações utilizáveis imediatamente, **sem F5** |

Verificado depois: **0 adendos** publicados, 3 versões, status `liberado`. O
`fetch` original foi restaurado.

---

## 11. Evidências

Pasta: `/home/fedorasurf/Documents/SoproLife/_EVIDENCIAS_M25_14_PRODUCAO/`

Todas contra `https://soprolife-painel-01.tailcaf0e4.ts.net/` — sem localhost,
sem HTML sintético. Sem senha, cookie, token ou dado real de paciente.

| # | Arquivo |
| --- | --- |
| K1 | `K1_lau_000001_pronto_antes_da_liberacao.png` |
| K2 | `K2_confirmacao_consciente.png` |
| K3 | `K3_liberacao_concluida_sem_erro.png` |
| K4 | `K4_estado_final_laudo_liberado.png` |
| K5 | `K5_downloads_separados_apos_liberacao.png` |
| K5 | `K5_baixado_LAUDO_LIBERADO_LAU-000001.pdf` (124.338 B) |
| K5 | `K5_baixado_PDF_TECNICO_MIR_ESP-000016.pdf` (1.546 B) |
| K6 | `K6_erro_controlado_ui_se_recupera_sem_f5.png` |

---

## 12. Rollback

**Código:** `git checkout bbd9ebc` na VPS (HEAD anterior em
`/opt/soprolife/backups/m25-14/20260809T220653Z/HEAD_ANTERIOR.txt`) + restart.

**Migration:** `alembic downgrade d4a71c88b2e6`. **Atenção:** o downgrade
recusa rodar enquanto existir laudo liberado — hoje `LAU-000001` está liberado
com `liberada_institucional`. Para voltar seria preciso decidir antes o que
fazer com esse registro. Isso é proteção, não defeito.

**Banco inteiro:** `pg_restore` a partir de
`/opt/soprolife/backups/m25-14/20260809T220653Z/soprolife_m15.dump`
(validado com `pg_restore --list`).

**Ordem correta do rollback:** primeiro o código, depois a migration — a API
nova não funciona com o schema antigo.

---

## 13. Pendências

- **`crm_display` da Dra. Ana continua `5262307-5`**, sem o ponto de
  `52.62307-5`. Não corrigido, conforme instruído. Sai assim no PDF liberado.
- **`M15_REPORTS_VALIDATION_BASE_URL` continua não configurada.** O código de
  verificação existe (`Y6ZVEF9XZY7Z`) e é impresso, mas não há URL base para o
  QR/validação pública.
- **Nenhuma assinatura manuscrita cadastrada** e **nenhum provedor ICP-Brasil
  configurado** — a liberação é institucional, como o próprio PDF declara.
- `LAU-000001` está **liberado** e é 100% fictício. Se atrapalhar, remover exige
  decisão humana (o downgrade da migration o bloqueia).
- Falhas preexistentes de §7 seguem em aberto, fora do escopo desta missão.
- `M15_REPORTS_MODE` permanece **`pilot`**; nada foi alterado nesse ponto.
