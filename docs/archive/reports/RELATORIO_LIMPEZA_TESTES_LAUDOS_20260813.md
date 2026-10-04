# Limpeza dos testes da área médica — 13/08/2026

**Data:** 13/08/2026, 22:30–22:41
**VPS:** `soprolife-painel-01` — `/opt/soprolife/soprolife-site`
**HEAD:** `343eb71` (inalterado — **nenhuma alteração de código foi necessária**)
**Alembic:** `a2f6c81d4b73 (head)` — inalterado
**Escopo:** remoção operacional dos dois fluxos de teste de hoje

---

## 1. Backup antes de qualquer escrita

Diretório: `/opt/soprolife/backups/cleanup-testes-20260813/` (`chmod 700`)

| Item | Valor |
|---|---|
| Dump | `soprolife_m15-pre-cleanup-20260813.dump` (`pg_dump -Fc`), 509.380 bytes |
| Validação | `pg_restore -l` → **401 entradas**, 47 `TABLE DATA` |
| Tabelas-chave no dump | `people`, `spirometry_exams`, `report_documents`, `report_document_versions`, `financial_entries`, `audit_logs` — todas presentes |
| SHA256 do dump | `8845793d326f97cb6dd8bc24822ac6fbba9e718d0ffddcd8bd4df15ba6805d0d` |
| PDFs preservados | `pdfs/LAU-000008-v1.pdf` · `b91ddbc09358232e688954101a7dc3630401de1d7565e7ac333ca1c1debcde8e` |
| | `pdfs/LAU-000009-v1.pdf` · `b6d36eb686e6a9c7dd5fa61400f9a19a5061b3c742ff0fc809208fb79675f9b0` |

Pré-voo antes do dump: HEAD `343eb71`, árvore limpa, health `ok`/banco `ok`,
Alembic em head, timer da M25.28 ativo com última execução `success`.

**A operação é reversível**: o dump mais os dois PDFs restauram o estado
anterior por completo.

---

## 2. Descoberta — por ID e por FK, nunca por nome

Os alvos foram resolvidos a partir dos códigos institucionais e o grafo foi
percorrido pelo **catálogo do Postgres** (`information_schema`), não pela
leitura do modelo — para não depender da minha interpretação do código.

| Código | ID | Criado em |
|---|---|---|
| ESP-000023 | `c90d88ed-0399-4b9d-82d3-ed6232a6b4a4` | 13/08 11:21:18 |
| ESP-000024 | `a447cab3-796c-46f7-b2e2-9fc5d3858090` | 13/08 11:47:36 |
| LAU-000008 | `63d4e265-51bd-4ecb-9a0f-5c861c4db462` | 13/08 11:22:00 |
| LAU-000009 | `f6249f89-ad41-4caf-bc51-9379159bd3d7` | 13/08 11:48:11 |
| PES-000035 | `baf9ceb4-9568-4479-be73-ede68b9c8b8b` | 13/08 11:21:17 |
| PES-000036 | `9538fe10-b599-47e1-80c7-d30ec1c39e34` | 13/08 11:47:36 |
| LAN-000015 | `f8400989-966b-4166-844c-4f20ad624c7e` | 13/08 11:21:18 |

ESP-000023: modalidade `cowork`, local literalmente `"teste"`.
ESP-000024: modalidade `clinica_parceira`, local `Pastore Ipanema`.

### LAN-000015 — relação CONFIRMADA antes de remover

```
tipo=receita  categoria=Espirometria  valor=220.00  status=Cortesia
descricao = 'Espirometria ESP-000023'
spirometry_exam_id = c90d88ed-...  →  ESP-000023   ✅ CONFIRMADO
consultation_id = None   partner_referral_id = None
```

Criado em 13/08 11:21:18.765, ou seja, no mesmo instante do exame. Sem
dependentes: `payment_allocations`, `partner_transfers` e
`partner_referrals` retornaram **0 linhas** apontando para ele.

---

## 3. Pessoas — os 7 critérios, provados um a um

Ambas foram criadas **segundos antes** do respectivo exame e não têm nenhum
vínculo fora do teste.

| Critério | PES-000035 | PES-000036 |
|---|---|---|
| 1. criada em 13/08 para o teste | 11:21:17 (1 s antes do exame) | 11:47:36 (9 ms antes) |
| 2. não existia antes | ✅ | ✅ |
| 3. sem atendimento legítimo | ✅ | ✅ |
| 4. outros exames fora do lote | **0** | **0** |
| 5. consultas | **0** | **0** |
| 6. leads / interactions / referrals | **0 / 0 / 0** | **0 / 0 / 0** |
| 7. relacionamento financeiro legítimo | só LAN-000015 (do teste) | nenhum |

Ramo acessório de cada uma, todo criado pelo próprio cadastro do teste:
1 `person_contact` (whatsapp), 1 `consent`, 1 `followup` — FUP-000033
(origem ESP-000023) e FUP-000034 (origem ESP-000024).

**Conclusão:** as duas eram exclusivamente de teste. Removidas.

---

## 4. Lote removido — 17 linhas em 10 tabelas

| Tabela | Linhas |
|---|---|
| `report_assignment_events` | 2 |
| `report_assignments` | 2 |
| `report_document_versions` | 2 |
| `report_documents` | 2 |
| `financial_entries` | 1 |
| `followups` | 2 |
| `spirometry_exams` | 2 |
| `consents` | 2 |
| `person_contacts` | 2 |
| `people` | 2 |

Zero em: `report_signatures`, `report_addenda`, `external_signature_batches`,
`external_signed_documents`, `qualified_signature_requests`,
`partner_referrals`, `partner_settlement_items`, `payment_allocations`,
`partner_transfers`. Nenhuma auto-referência entre laudos
(`corrects_document_id` / `superseded_by_id` = `None` nos dois).

---

## 5. Travas de imutabilidade — técnica estreita

`session_replication_role='replica'` **não foi usado**. As FKs permaneceram
ativas o tempo todo — é delas que veio a garantia de ordem das remoções.

Foram suspensos, **um a um pelo nome**, exatamente os três triggers que
barram `DELETE` em evidência clínica:

```
ALTER TABLE report_assignment_events  DISABLE TRIGGER trg_report_assignment_events_m24c_immutable
ALTER TABLE report_assignments        DISABLE TRIGGER trg_report_assignments_m24c_history
ALTER TABLE report_document_versions  DISABLE TRIGGER trg_report_document_versions_m24c_immutable
```

Reativados **antes do COMMIT**, com verificação programática dentro da mesma
transação: se algum não voltasse ao estado `O`, o script abortaria com
rollback. Tudo em uma única transação.

`audit_logs.trg_audit_append_only` **nunca foi tocado** — nada foi apagado da
trilha.

### Prova de que as travas voltaram a funcionar

Não bastou conferir o flag. Foram tentados `DELETE` reais (sempre revertidos):

| Tabela | Resultado |
|---|---|
| `report_document_versions` | **REJEITADO** — `M24C append-only: immutable clinical evidence` |
| `report_assignments` | **REJEITADO** — `M24C append-only: assignment cannot be...` |
| `report_assignment_events` | **REJEITADO** — `M24C append-only: immutable clinical evidence` |
| `audit_logs` | **REJEITADO** — `audit_logs e append-only (M15)` |

---

## 6. PDFs removidos

Ambos tiveram caminho e SHA256 registrados e conferidos **contra o banco**
antes da remoção — os hashes em disco batiam exatamente com os gravados em
`report_document_versions`.

| Laudo | Caminho | SHA256 | Bytes |
|---|---|---|---|
| LAU-000008 v1 | `laudos/c90d88ed-…/63d4e265-…/a14f96bc-….pdf` | `b91ddbc0…debcde8e` | 11.199 |
| LAU-000009 v1 | `laudos/a447cab3-…/f6249f89-…/b6dbd53a-….pdf` | `b6d36eb6…75f9b0` | 465.016 |

Os diretórios dos dois exames foram removidos inteiros de
`/opt/soprolife/private/reports/laudos/`, que passou de **6 para 4**
diretórios — os 4 restantes são os históricos legítimos.

**Não tocados:** `assinaturas/59709f0c-…` (rubrica da Dra. Ana, intacta,
de 09/08), PDFs históricos e documentos da M25.24/M25.27.

---

## 7. Auditoria — preservada, não falsificada

A trilha append-only ficou **integralmente intacta**: os 372 eventos
históricos que registram que os testes aconteceram continuam lá. Isso é
proposital — a operação sumiu, o registro de que ela ocorreu não.

Total: **646** eventos (645 anteriores + 1 de manutenção).

Registrado um único evento novo, sem nenhuma PII:

```
acao     = manutencao.remocao_testes_20260813
entidade = maintenance
detalhes = exames [ESP-000023, ESP-000024], laudos [LAU-000008, LAU-000009],
           pessoas [PES-000035, PES-000036], lancamentos [LAN-000015],
           linhas_removidas {…10 tabelas…},
           backup /opt/soprolife/backups/cleanup-testes-20260813,
           trilha_de_auditoria "preservada integralmente"
```

---

## 8. Validação final — todas as verificações passaram

### Alvos inexistentes operacionalmente

`ESP-000023` · `ESP-000024` · `LAU-000008` · `LAU-000009` · `LAN-000015` ·
`PES-000035` · `PES-000036` · `FUP-000033` · `FUP-000034` — **todos com
contagem 0**.

### Nenhum órfão

Nove verificações de integridade referencial, todas **0**: laudo sem exame,
versão sem laudo, atribuição sem laudo, evento sem laudo, exame sem pessoa,
followup sem pessoa, contato sem pessoa, consentimento sem pessoa e
lançamento com exame inexistente.

### Filas

- **Fila médica:** LAU-000001 a LAU-000005 (`liberado`) + `LAU-TF0001`. Nenhum dos dois testes.
- **Aguardando assinatura:** 5 laudos históricos. Nenhum dos dois.
- **Fila administrativa:** ESP-000001…ESP-000015. Nenhum dos dois.

### Financeiro

**13 lançamentos · TOTAL = R$ 3.044,79** ✅ — exatamente a linha de base
esperada. Nenhum dos 13 foi alterado (LAN-000001 a LAN-000013, todos
`Recebido`).

### Pastore

- 5 exames históricos intactos: ESP-000013, ESP-000014, ESP-000017, ESP-000018, ESP-000019
- julho **R$ 219,00 `a_receber`** · agosto **R$ 328,50 `a_receber`**
- **zero recebido** ✅
- 0 `partner_settlement_items` referenciavam os exames-alvo

### Snapshots e serviços

- Esteira executada após a limpeza: `Result=success`, `ExecMainStatus=0`
- **Nenhum arquivo** em `data/` ou `data-private/` menciona qualquer um dos códigos removidos
- Health: `status ok`, `banco ok`
- Alembic: `a2f6c81d4b73 (head)` — inalterado
- Timer da M25.28: `active (waiting)`, próximo disparo agendado
- Nenhum processo órfão; scripts temporários da VPS removidos

### Totais finais

| Tabela | Antes | Depois |
|---|---|---|
| `people` | 35 | **33** |
| `spirometry_exams` | 22 | **20** |
| `report_documents` | 8 | **6** |
| `financial_entries` | 14 | **13** |
| `audit_logs` | 645 | **646** |

---

## 9. Pendência encontrada (fora do escopo autorizado)

`ESP-TF0001` / `LAU-TF0001` continua na fila médica com status `atribuido`.
É o resíduo do teste de fumaça da **M25.9** — código com letras depois do
hífen, que a numeração real nunca emite. **Não foi tocado**: não estava
entre os alvos autorizados. Vale uma decisão sua se ele deve sair na M25.29.

---

## 10. Conclusão

# TESTES ESP-000023/ESP-000024 REMOVIDOS INTEGRALMENTE DA OPERAÇÃO

Os dois fluxos não existem mais em pacientes/CRM, atendimentos, exames,
laudos, fila da médica, atribuições, versões/documentos, PDFs privados,
assinatura externa, financeiro, indicadores nem snapshots. As pessoas
associadas foram removidas por terem sido criadas exclusivamente para o
teste, com os sete critérios provados individualmente. A trilha de auditoria
foi preservada e as travas de imutabilidade voltaram a rejeitar exclusões,
comprovado por tentativa real.

Nenhuma alteração de código foi necessária e nenhum deploy de código foi
feito.
