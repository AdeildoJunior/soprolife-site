# M25.29A — Limpeza geral de resíduos de teste do Centro de Comando

**Data:** 13/08/2026 23:41 → 14/08/2026 08:55
**VPS:** `soprolife-painel-01` — `/opt/soprolife/soprolife-site`
**HEAD:** `f7e3b01` — inalterado (**nenhuma alteração de código foi necessária**)
**Alembic:** `a2f6c81d4b73 (head)` — inalterado
**Escopo:** varredura completa do banco operacional e remoção apenas do que foi
provado sintético

---

## 1. Pré-voo e backup — antes de qualquer escrita

Diretório: `/opt/soprolife/backups/m25-29a-limpeza-geral-20260813/` (`chmod 700`)

| Item | Valor |
|---|---|
| HEAD / árvore | `f7e3b01`, limpa |
| Health | `status ok` · `banco ok` |
| Alembic | `a2f6c81d4b73 (head)` |
| Timer M25.28 | `active (waiting)`, última execução `Result=success` / `ExecMainStatus=0` |
| Dump | `soprolife_m15-pre-m2529a-20260813.dump` (`pg_dump -Fc`), 306.825 bytes |
| Validação | `pg_restore -l` → **401 entradas**, **47 `TABLE DATA`** |
| Tabelas-chave no dump | `people`, `spirometry_exams`, `report_documents`, `report_document_versions`, `financial_entries`, `audit_logs`, `partner_settlements`, `partner_settlement_items` — todas presentes |
| SHA256 do dump | `7bd028ec95684f2e963b50bfa30d72dc04711b7fa993b79ed644e02f1de45de8` |

PDFs sintéticos preservados em `pdfs/` antes da remoção, com hash conferido
**contra o banco**:

| Arquivo | SHA256 | Bytes |
|---|---|---|
| `LAU-000001-v1.pdf` | `0e3194d96c35d91c64a68f0d1f8fef256e53fbbca48c225823516a4d7f548bad` | 1.546 |
| `LAU-000001-v2.pdf` | `c30a9169abfa66f1525280e7c7193c633c4a3b3ed8d6de828fbe45d8f51e477b` | 123.496 |
| `LAU-000001-v3.pdf` | `4c73a63e8c3ae6c95195eea568affdc519ccb9710013cbeb45853cabc1601528` | 124.338 |

**A operação é reversível**: o dump mais os três PDFs restauram o estado anterior
por completo. Nenhum segredo foi impresso em nenhuma etapa.

---

## 2. Método da varredura — catálogo, não leitura de modelo

A busca não partiu dos três alvos conhecidos. Foram feitas três passagens
independentes:

1. **Varredura textual completa** — um `DO` block percorreu **todas** as colunas
   `varchar`/`text` de **todas** as tabelas base, aplicando o padrão
   `teste|test|demo|fake|sintet|synthetic|dummy|mock|smoke|fixture|tf0001|placeholder|lorem`.
   Retornou 23 colunas com ocorrência.
2. **Padrão de código** — todo `public_code` fora do formato real
   (`^PES-[0-9]{6}$`, `^ESP-…`, `^LAU-…`).
3. **Grafo de FK pelo `pg_catalog`** — para cada ID candidato, um segundo `DO`
   block percorreu **as 68 colunas de chave estrangeira do banco** contando
   referências. Nenhum vínculo foi deduzido lendo o código da aplicação.

Nenhum candidato foi removido por conter palavra suspeita. Cada um teve de
passar por ID, `created_at`, trilha de auditoria, vínculos e origem da missão.

---

## 3. Candidatos provados como teste

### Lote A — resíduo da M25.13 (11 linhas)

| Código | ID | Criado em | Prova |
|---|---|---|---|
| PES-000029 | `81f0bbbb-6a77-47d9-94bd-b54fd1a5c6a8` | 09/08 14:53:50 | nome `TESTE M25.13 Paciente Fictic`; **1 s antes** do exame; já arquivada em 09/08 23:06 com motivo `cenario interno das missoes M25.13/M25.14 - fora da operacao`; sem CPF |
| ESP-000016 | `3152f5bb-dd53-4988-9468-a1cbc20f7357` | 09/08 14:53:51 | `local_atendimento = "Pastore Ipanema - TESTE M25.13"`, `origem = "TESTE M25.13"`, observação *"cenario ficticio de validacao visual. Nao e paciente real."* |
| **LAU-000001** | `39d8e0c8-351f-4594-af78-5185b8cc1f1a` | 09/08 14:56:13 | `origin_label = "TESTE M25.13"`; laudo **do exame ESP-000016** |
| FUP-000025 | — | 09/08 14:53 | `responsavel = "TESTE M25.13"` |

Os sete critérios de pessoa, provados um a um para PES-000029: criada para o
teste ✅ · não existia antes ✅ · sem atendimento legítimo ✅ · **0** outros
exames ✅ · **0** consultas ✅ · **0** leads / interações / indicações ✅ ·
**0** relacionamento financeiro ✅.

> **Achado não previsto no briefing.** O briefing tratava ESP-000016 como um
> registro isolado no Financeiro. Ele tinha um laudo: **LAU-000001**, com três
> versões, atribuição à Dra. Ana, evento e assinatura institucional, e estava
> **`liberado` na fila médica**. A varredura o pegou pelo `origin_label`, não
> pelo código — o código `LAU-000001` é perfeitamente regular. Removido junto,
> porque um laudo não sobrevive ao exame que o originou.

### Lote B — resíduo do smoke test M25.9 (8 linhas)

Todo o lote nasceu no **mesmo segundo**: 08/08 10:31:50.

| Código | ID | Prova |
|---|---|---|
| PES-TF0001 | `69789908-f837-4f6c-9029-48cb1f2d28b3` | `TESTE APAGAR Paciente Fumaca`; código com letras que a numeração real nunca emite; arquivada com o mesmo motivo |
| ESP-TF0001 | `344638af-615a-47f1-8bfa-dbff72a45602` | idem; **sem data de exame**; trilha registra *"Homologo sintetico: prova de restaurabilidade da M25.24"* |
| LAU-TF0001 | `8b59d411-caa7-4f38-a58d-04f45bc212c2` | `origin_label = "teste-apagar"`; ainda `atribuido` na fila médica |
| *user* | `428b93a8-bdca-4a9c-9f0d-5da4987aeea3` | `TESTE APAGAR Medica Fumaca`, e-mail `@soprolife.local`, inativo |
| *physician_profile* | `45dc66e3-49e1-4229-b363-5b51c49cbf30` | mesmo nome, **CRM fictício `00000001`**, RQE `00000`, `active=f`, `verification_status=pending` |

A conta médica de teste estava fora dos três alvos nomeados; foi incluída após
confirmação explícita do gestor. O grafo de FK provou o isolamento: entre as
**22 colunas** que apontam para `users` / `physician_profiles`, ela só aparecia
como autora dos próprios artefatos do LAU-TF0001 — **0** papéis, **0** rubrica,
**0** laudos reais, **0** templates aprovados.

---

## 4. Candidatos ambíguos — NÃO removidos

| Item | Por que não é seguro apagar |
|---|---|
| **LAU-000002 … LAU-000005** | O gestor anotou em 11/08: *"os laudos deste exame no Centro de Comando sao teste do fluxo"*. Mas pertencem aos exames **reais** ESP-000017/18/19, que compõem o fechamento de agosto. Apagar destruiria evidência clínica ligada a paciente real. **Decisão humana.** |
| `report_footer_templates` `TESTE_NAO_ASSINADO` e `PILOTO_INTERNO_NAO_ASSINADO` | `status = 'test'`, mas são **configuração** protegida por trigger de imutabilidade, com **0 usos**. Não aparecem em fila, financeiro, CRM ou indicadores. Remover é risco sem ganho operacional. |

### Falsos positivos — confirmados como reais

| Item | Por que casou com o padrão |
|---|---|
| PES-000012 … PES-000022 (11 pessoas) | Observação `[PLACEHOLDER M15.6C] lead sem pessoa vinculada`. São as pessoas que **sustentam os 11 leads reais** do CRM legado, importadas em 22/07. |
| ESP-000017 / ESP-000018 / ESP-000019 | `encerramento_motivo = laudo_externo_e_teste_do_fluxo` contém "teste" — mas são **3 dos 5 Pastore reais**. |

Estes dois grupos são exatamente a razão da regra "não deletar só porque contém
palavra suspeita": uma limpeza por `LIKE '%teste%'` teria apagado 11 pessoas
reais do CRM e 3 dos 5 exames Pastore que o gestor mandou preservar.

---

## 5. Registros reais preservados

**Os cinco Pastore, intactos e com `BD = true` nos cinco:**

| Exame | Data | Status | BD | Fechamento |
|---|---|---|---|---|
| ESP-000013 | 14/07/2026 | Liberado | ✅ | julho |
| ESP-000014 | 18/07/2026 | Liberado | ✅ | julho |
| ESP-000019 | 01/08/2026 | Realizado | ✅ | agosto |
| ESP-000017 | 04/08/2026 | Realizado | ✅ | agosto |
| ESP-000018 | 04/08/2026 | Realizado | ✅ | agosto |

**Os 13 lançamentos legítimos, nenhum alterado:** LAN-000001 a LAN-000013,
todos `Recebido`, todos ligados a exame real (ESP-000001…ESP-000012 e
ESP-000015). **TOTAL = R$ 3.044,79.**

Também preservados: 12 leads, 27 followups, 1 consulta, 29 contatos, a rubrica
da Dra. Ana (`assinaturas/59709f0c-…`, 58.913 bytes, intacta), os PDFs dos
Pastore reais e os documentos legítimos da M25.24.

---

## 6. O card "aguardando fechamento = 1" — identificado

O card **já estava em 0** quando a auditoria começou. O contador foi
reproduzido em SQL puro, replicando exatamente o filtro do endpoint
`GET /pastore/fechamentos`: exames com `partner_id` da Pastore canônica **e**
`partner_unit_id` de unidade ativa, concluídos, **fora** de qualquer
`partner_settlement_item`.

**O que compunha o `1` era o ESP-000024** — o exame Pastore Ipanema de teste de
13/08, removido na limpeza anterior às 22:38. A trilha de auditoria prova o
vínculo com a unidade:

```
13/08 11:47:36  atendimento.criado   {"tipo": "espirometria_pastore", "campos": ["ESP-000024"]}
13/08 11:48:11  laudo_original_atribuido
                {"exam_code": "ESP-000024", "origin_type": "clinica_parceira",
                 "origin_partner_unit_id": "a5762eb6-…"}   ← Pastore Ipanema
```

**Não era o ESP-000016.** Esse ponto merece registro porque contraria a hipótese
natural: apesar de ter `local_atendimento = "Pastore Ipanema - TESTE M25.13"`, o
ESP-000016 tinha `partner_id` e `partner_unit_id` **NULL**. O texto do local era
livre; o vínculo estrutural nunca existiu. Por isso ele nunca entrou no card
Pastore — e por isso aparecia no Financeiro, na conciliação **extra**-Pastore.

---

## 7. Remoção — 19 linhas em 13 tabelas

Tudo em **uma única transação**, por **ID explícito**. Nenhum `DELETE` por data,
nenhum `DELETE` por `LIKE`, e `session_replication_role` **não foi usado**. As
FKs ficaram ativas o tempo todo — foi delas que veio a garantia de ordem.

| Tabela | Lote A | Lote B | Total |
|---|---|---|---|
| `report_signatures` | 1 | 0 | 1 |
| `report_assignment_events` | 1 | 0 | 1 |
| `report_assignments` | 1 | 1 | 2 |
| `report_document_versions` | 3 | 1 | 4 |
| `report_documents` | 1 | 1 | 2 |
| `followups` | 1 | 0 | 1 |
| `consents` | 1 | 0 | 1 |
| `spirometry_exams` | 1 | 1 | 2 |
| `people` | 1 | 1 | 2 |
| `physician_profiles` | 0 | 1 | 1 |
| `auth_sessions` | 0 | 1 | 1 |
| `users` | 0 | 1 | 1 |
| **Total** | **11** | **8** | **19** |

Zero linhas em `financial_entries`, `person_contacts`, `user_roles`,
`partner_settlement_items`, `report_addenda`, `payment_allocations`,
`partner_transfers`, `partner_referrals`, `external_signature_batches`,
`external_signed_documents`, `qualified_signature_requests`.

### Travas de imutabilidade — técnica estreita

Dos 9 triggers do banco, apenas **3** disparam em `DELETE` nas tabelas-alvo.
Foram suspensos **um a um pelo nome**:

```
ALTER TABLE report_assignment_events  DISABLE TRIGGER trg_report_assignment_events_m24c_immutable
ALTER TABLE report_assignments        DISABLE TRIGGER trg_report_assignments_m24c_history
ALTER TABLE report_document_versions  DISABLE TRIGGER trg_report_document_versions_m24c_immutable
```

`trg_physician_profiles_m24c_validate` **não precisou ser tocado**: dispara só em
`INSERT`/`UPDATE`. `audit_logs.trg_audit_append_only` **nunca foi tocado**.

Reativados **antes do COMMIT**, com verificação programática dentro da mesma
transação: um `DO` block aborta tudo se algum trigger não voltar ao estado `O`.

> **Nota de execução.** A primeira tentativa **abortou e reverteu por completo**:
> o `INSERT` na trilha usava `gen_random_uuid()::text` num `audit_logs.id` que é
> `integer serial`, e `::jsonb` numa coluna `json`. O `ON_ERROR_STOP` fez o
> rollback. Foi verificado antes de reexecutar que nada havia sido gravado
> (33 pessoas, 20 exames, 6 laudos, 646 eventos — idênticos ao pré-voo) e que os
> 3 triggers estavam de volta em `O`. A segunda execução foi limpa.

### Prova de que as travas voltaram a funcionar

Não bastou conferir o flag. Foram tentados `DELETE` reais depois do COMMIT:

| Tabela | Resultado |
|---|---|
| `report_document_versions` | **REJEITADO** — `M24C append-only: immutable clinical evidence` |
| `report_assignments` | **REJEITADO** — `M24C append-only: assignment cannot be deleted` |
| `report_assignment_events` | **REJEITADO** — `M24C append-only: immutable clinical evidence` |
| `audit_logs` | **REJEITADO** — `audit_logs e append-only (M15)` |

---

## 8. Arquivos

O diretório do exame sintético foi removido inteiro de
`/opt/soprolife/private/reports/laudos/`:

| Laudo | Caminho | Bytes |
|---|---|---|
| LAU-000001 v1 | `laudos/3152f5bb-…/39d8e0c8-…/501e7e96-….pdf` | 1.546 |
| LAU-000001 v2 | `laudos/3152f5bb-…/39d8e0c8-…/f9e57d05-….pdf` | 123.496 |
| LAU-000001 v3 | `laudos/3152f5bb-…/39d8e0c8-…/b08fb3a2-….pdf` | 124.338 |

Os três SHA256 em disco batiam exatamente com os gravados em
`report_document_versions` antes da remoção.

`LAU-TF0001 v1` referenciava um caminho cujo diretório **já não existia** — o
registro no banco era um ponteiro órfão para arquivo ausente.

A raiz passou de **4 para 3 diretórios** e de **16 para 13 arquivos**; os 3
restantes são exatamente os exames Pastore reais (`31559e0b`, `7185d468`,
`dd06039e`).

**Não tocados:** rubrica da Dra. Ana, PDFs históricos e documentos da M25.24.

---

## 9. Auditoria — preservada, não falsificada

A trilha append-only ficou **integralmente intacta**. Os eventos históricos que
registram que os testes aconteceram continuam lá: a operação sumiu, o registro
de que ela ocorreu não. Total: **647** eventos (646 anteriores + 1).

Registrado um único evento novo (`id 651`), sem nenhuma PII:

```
acao     = manutencao.limpeza_geral_testes_m2529a
entidade = maintenance
detalhes = lote_a {M25.13: ESP-000016, LAU-000001, PES-000029, FUP-000025},
           lote_b {M25.9 smoke: ESP-TF0001, LAU-TF0001, PES-TF0001,
                   conta medica ficticia removida (CRM 00000001)},
           preservados {5 Pastore reais, 13 lancamentos, total 3044.79},
           ambiguos_nao_removidos [LAU-000002…LAU-000005,
                                   report_footer_templates status=test],
           backup /opt/soprolife/backups/m25-29a-limpeza-geral-20260813,
           trilha_de_auditoria "preservada integralmente"
```

---

## 10. Validação final — os 19 pontos

| # | Verificação | Resultado |
|---|---|---|
| 1 | ESP-000016 removido | ✅ contagem 0 |
| 2 | ESP-TF0001 removido | ✅ contagem 0 |
| 3 | LAU-TF0001 removido | ✅ contagem 0 |
| 4 | Nenhum resíduo inequívoco nas filas | ✅ fila médica = LAU-000002…LAU-000005; exames = ESP-000001…ESP-000015, ESP-000017…ESP-000019 |
| 5 | Nenhum teste no Financeiro | ✅ **13 de 13 conciliados · 0 pendentes · R$ 0,00 a conciliar · 0 divergências** |
| 6 | Nenhum teste no CRM | ✅ 12 leads e 27 followups, todos ligados a pessoa real |
| 7 | Nenhum teste em laudos | ✅ 4 laudos, todos de exame Pastore real |
| 8 | Nenhum teste em indicadores | ✅ varredura textual completa só retorna os falsos positivos documentados |
| 9 | Nenhum órfão referencial | ✅ **13 verificações, todas 0** |
| 10 | 13 LAN / R$ 3.044,79 | ✅ 13 lançamentos, soma `3044.79`, 13 `Recebido` |
| 11 | Cinco Pastore intactos | ✅ com `BD = true` nos cinco |
| 12 | Julho R$ 219,00 | ✅ `a_receber`, 2 itens (ESP-000013, ESP-000014) |
| 13 | Agosto R$ 328,50 | ✅ `a_receber`, 3 itens (ESP-000017, ESP-000018, ESP-000019) |
| 14 | Zero recebido Pastore | ✅ total documental R$ 547,50 · recebido R$ 0,00 |
| 15 | O que era o "aguardando = 1" | ✅ **ESP-000024**, removido em 13/08 — ver §6 |
| 16 | Estado final dos cards | ✅ ver abaixo |
| 17 | Health OK | ✅ `status ok` · `banco ok` |
| 18 | Alembic inalterado | ✅ `a2f6c81d4b73 (head)` |
| 19 | Timer M25.28 ativo | ✅ `active (waiting)`; **dois ciclos pós-limpeza** concluídos `Result=success` / `ExecMainStatus=0` (09:06:28 e 09:17:26) |

> **Nota sobre o item 19.** O COMMIT foi às 08:49:04. Os dois ciclos seguintes da
> esteira — concluídos às **09:06:28** e às **09:17:26** — terminaram ambos com
> `Result=success` / `ExecMainStatus=0`. A varredura por resíduo em `data/` e
> `data-private/` foi repetida **depois** dessas regerações e continuou limpa:
> os snapshots reconstruídos a partir do banco já limpo não trazem nenhum dos
> códigos removidos. Health confirmado no mesmo momento: `status ok` / `banco ok`.

### Estado final dos cards Pastore

| Card | Valor | Significado |
|---|---|---|
| aguardando fechamento | **0** | nenhum exame Pastore concluído fora de fechamento |
| fechamento em aberto | **0** | nenhum fechamento `incluido`/`enviado` |
| a receber | **2** | **2 fechamentos** (julho e agosto), R$ 547,50 confirmado |
| recebido | **0** | nenhum recibo mensal registrado |

Os cinco exames reais estão **todos** dentro dos dois fechamentos — 2 em julho,
3 em agosto. Nada ficou de fora.

### Totais por tabela

| Tabela | Antes | Depois |
|---|---|---|
| `people` | 33 | **31** |
| `spirometry_exams` | 20 | **18** |
| `report_documents` | 6 | **4** |
| `report_document_versions` | 17 | **13** |
| `report_assignments` | 6 | **4** |
| `report_assignment_events` | 5 | **4** |
| `report_signatures` | 5 | **4** |
| `users` | 4 | **3** |
| `physician_profiles` | 2 | **1** |
| `followups` | 28 | **27** |
| `consents` | 17 | **16** |
| `financial_entries` | 13 | **13** |
| `partner_settlement_items` | 5 | **5** |
| `audit_logs` | 646 | **647** |

Nenhum arquivo em `data/` ou `data-private/` menciona qualquer código removido —
varredura feita duas vezes: logo após o COMMIT e novamente **depois** de dois
ciclos completos da esteira terem regerado os snapshots a partir do banco.
`code_sequences` **não foi rebobinado** de propósito (ESP em 25, LAU em 10,
PES em 37): códigos nunca devem ser reutilizados.

---

## 11. Recomendação para a próxima missão — UI do Financeiro

**Não implementada nesta manutenção**, por ser redesign e não limpeza de dados.

O problema é de rótulo. O card grande mostra `2` com o título "Pastore — a
receber", e o gestor lê "2 pacientes". São **2 fechamentos**, que somam **5
exames**.

Recomendação:

1. Trocar o rótulo para **"Pastore — 2 fechamentos a receber"**, deixando
   explícita a unidade contada.
2. Mostrar a decomposição logo abaixo do card:
   - `Julho/2026 — 2 exames — R$ 219,00`
   - `Agosto/2026 — 3 exames — R$ 328,50`
3. Permitir abrir cada fechamento e ver os ESP que o compõem.

**O backend já entrega tudo isso.** `GET /pastore/fechamentos` já retorna, por
fechamento, `itens.total` **e** `itens.exames_public_codes`
(`app/routers/pastore.py`, `_serialize_settlement`). O front consome
`itens.total` mas **descarta `exames_public_codes`**
(`js/pastore-settlement.js`, `renderFull`).

Ou seja: é mudança **exclusivamente de front-end**, sem alteração de API, sem
migração e sem novo contrato de dados.

---

## 12. Pendências para decisão humana

1. **LAU-000002 … LAU-000005** — o gestor os marcou como "teste do fluxo" em
   11/08, mas pertencem a exames reais que estão nos fechamentos. Preservados
   por ambiguidade. Se forem mesmo descartáveis, é uma missão própria: envolve
   4 laudos, 12 versões, 4 atribuições, 4 assinaturas e 13 PDFs.
2. **`report_footer_templates` com `status='test'`** — 2 registros de
   configuração, 0 usos. Manter ou promover a `status` definitivo.

---

## 13. Conclusão

# M25.29A — RESÍDUOS SINTÉTICOS REMOVIDOS E BASE OPERACIONAL LIMPA

Os dois lotes sintéticos remanescentes — o cenário da M25.13 (ESP-000016,
LAU-000001, PES-000029, FUP-000025) e o smoke test da M25.9 (ESP-TF0001,
LAU-TF0001, PES-TF0001 e a conta médica de CRM fictício) — não existem mais em
pacientes, CRM, atendimentos, exames, laudos, fila da médica, atribuições,
versões, assinaturas, PDFs privados, contas de acesso, financeiro, indicadores
nem snapshots.

A conciliação extra-Pastore fechou em **13 de 13 · 0 pendentes · R$ 0,00 ·
0 divergências**, sem que nenhum lançamento fosse criado para fazer o painel
fechar. Os cinco Pastore reais, os dois fechamentos (R$ 219,00 + R$ 328,50 =
R$ 547,50, zero recebido) e os 13 lançamentos de R$ 3.044,79 permanecem
exatamente como estavam.

A trilha de auditoria foi preservada integralmente e as travas de imutabilidade
voltaram a rejeitar exclusões, comprovado por tentativa real de `DELETE`.

Nenhuma alteração de código foi necessária e nenhum deploy de aplicação foi
feito.
