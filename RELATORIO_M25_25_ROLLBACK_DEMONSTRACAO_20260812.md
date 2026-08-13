# M25.25 — Rollback da demonstração de 12/08/2026

Remoção cirúrgica do lote de demonstração feito para o sócio e a Dra. Ana em
12/08/2026, com retorno do estado operacional ao momento imediatamente posterior
à conclusão da M25.24. **Não** houve restore de dump: o alvo era
`pós-M25.24 − demonstração`, e um restore do `m15-antes-m25-24.dump` teria
apagado as escritas legítimas daquela missão.

Data de execução: 12/08/2026, 23h07–23h23 (`America/Sao_Paulo`).

---

## 1. HEAD inicial

| Item | Valor |
|---|---|
| HEAD da VPS | `87271d2c3b75cbe0510d0fe399993998f4cdbd4f` |
| HEAD local (worktree) | `87271d2c3b75cbe0510d0fe399993998f4cdbd4f` |
| Branch da VPS | `painel-soprolife-v01` |
| `git status` da VPS | limpo (0 linhas) |

Confere com o HEAD oficial esperado. Nenhum commit, reset, checkout ou revert foi
feito nesta missão — a aplicação já estava correta; o que se restaurou foi o
**estado dos dados**.

## 2. Alembic

`a2f6c81d4b73` antes e depois. Nenhuma migration foi criada ou aplicada.

## 3. Cutoff usado

O commit documental da M25.24 (`87271d2`, `2026-08-12T02:41:24Z` =
`2026-08-11 23:41:24 -03`) serviu de âncora, mas o cutoff real veio da trilha de
auditoria, que apresentou um intervalo de **12 horas sem nenhuma escrita**:

| audit id | timestamp local | evento |
|---|---|---|
| 431 | 2026-08-11 23:40:08 | `exame_reaberto_para_laudo` — último ato da M25.24 |
| — | *(12h de silêncio)* | — |
| 432 | 2026-08-12 11:40:59 | `auth.token_emitido` — primeiro ato da demonstração |

**Cutoff adotado: `audit_logs.id >= 432`, equivalente a `2026-08-12 11:40:59 -03`.**
A demonstração inteira cabe em 42 minutos (11:40:59 → 12:22:46), 143 eventos.

O fuso foi tratado explicitamente em todas as consultas
(`ts_utc AT TIME ZONE 'America/Sao_Paulo'`). O banco roda com `timezone =
America/Sao_Paulo` e as colunas são `timestamptz`, então nenhuma comparação
dependeu de assumir UTC.

## 4. Identificação por auditoria e timestamps

Nenhum registro foi classificado só por data. Cada candidato foi confirmado pelo
cruzamento de trilha de auditoria + `created_at`/`updated_at` + encadeamento
paciente → exame → laudo → versão → assinatura + usuário executor.

Usuários da janela: `annapec3` (médica, 125 eventos), `contato` (admin, 15),
`luizlopes214` (gestor, 3 — apenas sessão).

Cadeias reconstruídas:

- **PES-000033** (11:45:31) → `ESP-000020` (pastore) → `LAU-000006` → 3 versões → liberado 11:54:11
- **PES-000034** (12:03:39) → `LEA-000013` → `ESP-000021` (soprolife) → `LAN-000014`; e `ESP-000022` (pastore) → `LAU-000007` → 4 versões → liberado 12:13:48 → `BAT-000001` (upload de assinado)
- **`BAT-000002`** (12:22:09) — download de 4 laudos para assinatura externa

Uma varredura de **todas as colunas `timestamptz` de todas as tabelas** confirmou
que nada além disso foi tocado após o cutoff.

## 5. Lote congelado

Definido por IDs explícitos, nunca por `WHERE created_at >= ...`.

| Código | Entidade | Criado (local) | Por que é demonstração | Existia antes? |
|---|---|---|---|---|
| PES-000033 | pessoa | 12/08 11:45:31 | nome sintético iniciado por "TESTE"; `candidatos_identidade: 0` | não |
| PES-000034 | pessoa | 12/08 12:03:39 | idem | não |
| ESP-000020 | exame | 12/08 11:45:31 | atendimento de PES-000033 | não |
| ESP-000021 | exame | 12/08 12:05:38 | atendimento de PES-000034 | não |
| ESP-000022 | exame | 12/08 12:09:30 | atendimento de PES-000034 | não |
| LAU-000006 | laudo | 12/08 11:49:23 | laudo de ESP-000020 | não |
| LAU-000007 | laudo | 12/08 12:10:13 | laudo de ESP-000022 | não |
| LEA-000013 | lead | 12/08 12:03:39 | lead de PES-000034 | não |
| LAN-000014 | financeiro | 12/08 12:05:38 | receita R$ 10,00 gerada por ESP-000021 | não |
| BAT-000001 | lote assinatura | 12/08 12:17:07 | upload de assinado de LAU-000007 | não |
| BAT-000002 | lote assinatura | 12/08 12:22:09 | download para assinatura externa | não |

Dependentes, todos alcançados a partir dos IDs acima: 7 versões de laudo,
2 atribuições, 2 eventos de atribuição, 2 assinaturas, 1 documento assinado
externo, 4 followups, 2 contatos, 2 consentimentos.

### Distinção entre duplicata e registro histórico

Nenhum cadastro de hoje era uma duplicata de paciente histórico: `PES-000033` e
`PES-000034` nasceram com `candidatos_identidade: 0` e nome sintético. Nenhum
registro anterior a 12/08 foi removido. Os 5 Pastore, os 18 históricos e o
homólogo sintético `ESP-TF0001` seguem intactos.

## 6. Ambiguidades

**Nenhuma.** Os dois cadastros do dia têm nome literalmente iniciado por "TESTE",
foram criados pelo admin dentro da janela de 42 minutos e não têm qualquer
vínculo com registro anterior. Não houve nenhum atendimento real posterior à
M25.24 — logo, nada foi marcado como `AMBÍGUO — NÃO REMOVIDO`.

## 7. Backup

Diretório novo, sem sobrescrever nada: `/opt/soprolife/backups/m25-25-rollback-demo-20260812/` (modo 700).

| Arquivo | Conteúdo |
|---|---|
| `soprolife_m15-antes-m25-25.dump` | `pg_dump -Fc` do banco **atual** (pós-M25.24, com a demo), modo 600 |
| `DUMP.sha256` | `5a15b79ff5f688d4bf88ead664352341cfdc86cac2a839fc17910caec89a91d8` |
| `pg_restore_-l.txt` | validação do dump — 401 objetos legíveis |
| `MANIFEST_pdfs_demo.sha256` | hash dos 7 PDFs da demo |
| `MANIFEST_storage_completo_antes.sha256` | hash dos 24 arquivos do storage antes |
| `private-reports-demo/` | cópia íntegra dos PDFs removidos |
| `VPS_HEAD.txt` / `VPS_GIT_STATUS.txt` | HEAD e árvore da VPS |
| `m15.env` | env modo 600, nunca impresso |
| `m25-25-rollback-demo.sh` | o script de manutenção executado |

## 8. Registros removidos por entidade

```
REGISTROS REMOVIDOS

Pessoas novas da demonstração:  2      (PES-000033, PES-000034)
Exames novos:                   3      (ESP-000020, ESP-000021, ESP-000022)
Laudos novos:                   2      (LAU-000006, LAU-000007)
Versões:                        7
Arquivos privados:              7
Batches assinatura:             2      (BAT-000001, BAT-000002)
Signed documents:               1
FinancialEntries:               1      (LAN-000014)
Followups:                      4
Outros:                        11      leads 1, consents 2, person_contacts 2,
                                       report_assignments 2,
                                       report_assignment_events 2,
                                       report_signatures 2
                                       (report_addenda 0, qualified_signature_requests 0)
```

Ordem de remoção derivada das FKs reais do banco (folhas primeiro):
`external_signed_documents` → `report_signatures` → `report_addenda` →
`qualified_signature_requests` → `report_assignment_events` →
`report_assignments` → `report_document_versions` → `report_documents` →
`external_signature_batches` → `payment_allocations` → `financial_entries` →
`spirometry_exams` → `interactions` → `followups` → `leads` → `consents` →
`person_contacts` → `people`. Tudo em **uma única transação**.

### Duas restaurações (não remoções)

A demonstração alterou dois registros **legítimos**, que precisavam voltar ao
estado da M25.24 em vez de serem apagados:

1. **`ESP-000019` foi reaberto** às 11:59:15 com o motivo "TESTE DE HOJE 12AGO",
   desfazendo o encerramento histórico da M25.24. Foi **reencerrado** com
   `reason_code = laudo_externo_e_teste_do_fluxo` e o texto recuperado
   literalmente de seus dois irmãos encerrados no mesmo lote (`ESP-000017`,
   `ESP-000018`).
   *Ressalva honesta:* o `encerrado_em` original (microssegundo exato) **não é
   recuperável** — a reabertura sobrescreveu o campo e não existe nenhum dump
   entre a M25.24 e a demonstração (o mais recente é de 11/08 23:26, anterior aos
   encerramentos das 23:34). Usou-se o `ts_utc` do próprio evento de auditoria
   429 — `2026-08-11 23:34:56.004647-03` — que é o registro autoritativo daquele
   encerramento. Nada foi inventado.
2. **`LAU-000004` e `LAU-000005`** (pré-existentes, de `ESP-000019`) foram
   marcados como baixados para assinatura pelo `BAT-000002`. Como a auditoria tem
   **um único** evento `laudos_baixados_para_assinatura_externa` em toda a sua
   história — o da demo — e os demais laudos têm o campo nulo, o estado anterior
   era comprovadamente `NULL`. Restaurados para `NULL`.

### A trava M24C e como foi tratada

A primeira tentativa **falhou por projeto**: a M24C instalou triggers
`BEFORE DELETE OR UPDATE` que tornam a evidência clínica imutável no próprio
Postgres (`report_document_versions`, `report_assignment_events`,
`report_addenda`, `report_templates`, `report_footer_templates`, mais um guard
anti-DELETE em `report_assignments`). Não há bypass na aplicação. A transação foi
revertida inteira, sem alterar um único registro.

Com autorização explícita do administrador, a remoção foi feita suspendendo
**apenas os quatro triggers M24C envolvidos**, dentro da mesma transação —
deliberadamente **não** se usou `session_replication_role = 'replica'`, que
desligaria também a checagem de chaves estrangeiras. Assim a integridade
referencial continuou sendo cobrada pelo banco durante todos os DELETEs. Os
triggers foram reativados antes do `COMMIT`, com uma verificação que aborta a
transação se algum ficasse desabilitado.

Verificado depois: os 8 triggers M24C estão com `tgenabled = 'O'` e a trava
voltou a morder (um `UPDATE` de teste em `report_document_versions` foi
rejeitado com `M24C append-only: immutable clinical evidence`).

Nenhum histórico foi falsificado: o backup guarda o banco completo com a
demonstração, e os 143 eventos de auditoria da demo continuam no banco.

## 9. Arquivos privados removidos

7 PDFs, identificados por relação documental (`report_document_versions.storage_path`
das versões dos dois laudos da demo), com hash registrado no backup antes da
remoção:

```
laudos/<ESP-000020>/<LAU-000006>/  3 PDFs  (original, prévia, liberado)
laudos/<ESP-000022>/<LAU-000007>/  4 PDFs  (original, prévia, liberado, assinado externo)
```

**Prova de que o resto ficou intacto:** o storage tinha 24 arquivos e ficou com
17; os 17 remanescentes têm **hash SHA256 idêntico** ao manifesto pré-rollback
(`diff` sem divergência). MIR histórico, PDFs da M25.24, documentos Pastore e a
rubrica da Dra. Ana (1 `physician_signature_assets`) não foram tocados. Nenhum
conteúdo clínico foi impresso em nenhum momento.

## 10. Auditoria da manutenção

`audit_logs` é append-only por contrato ("sem endpoint de update/delete") e não
possui FKs que impedissem a remoção. Optou-se por **preservar os 143 eventos da
demonstração** (ids 432–574): eles não aparecem em CRM, laudos, financeiro,
indicadores ou filas, e apagá-los seria falsificar histórico sem nenhum ganho
operacional.

Foi acrescentada **uma** linha nova (id 575, 12/08 23:21:34 local):

- `acao`: `manutencao.rollback_demonstracao_20260812`
- `entidade`: `maintenance`
- `user_id`: conta admin (`contato`), que autorizou a manutenção
- `detalhes`: referência M25.25, cutoff, origem `cli_manutencao_m25_25`, contagem
  por entidade, códigos institucionais, as duas restaurações e a nota sobre a
  suspensão temporária das travas M24C — sem PII.

## 11. Prova do financeiro

| Prova | Esperado | Obtido |
|---|---|---|
| LAN históricos | 13 | **13** |
| Soma | R$ 3.044,79 | **R$ 3.044,79** |
| Settlement julho/2026 | 2 itens, R$ 219,00, a receber | **2 / 219.00 / a_receber** |
| Settlement agosto/2026 | 3 itens, R$ 328,50, a receber | **3 / 328.50 / a_receber** |
| Recebimento Pastore | zero | **0** |

A demonstração criou exatamente um lançamento (`LAN-000014`, receita
Espirometria, R$ 10,00, competência 12/08, vinculado a `ESP-000021`). Antes do
rollback: 14 LAN / R$ 3.054,79. Depois: 13 LAN / R$ 3.044,79 — a diferença é
exatamente os R$ 10,00 removidos.

Nada foi recalculado, redistribuído ou teve competência ou valor alterados. Os
settlements sequer foram tocados: nasceram em 11/08 23:33–23:34 (M25.24) e os
exames Pastore da demo não geraram item de settlement algum.

## 12. Filas antes e depois

| Fila | Antes | Depois |
|---|---|---|
| Históricos encerrados | 17 | **18** |
| Laudos liberados | 7 | **5** |
| Laudos atribuídos | 1 | **1** (`LAU-TF0001`, homólogo sintético da M25.24) |
| Laudos baixados p/ assinatura | 4 | **0** |
| Assinados externos em conferência | 1 | **0** |
| Exames ativos sem laudo | 1 | **0** |
| Atribuições ativas | 8 | **6** |
| Followups pendentes | 32 | **28** |
| Pessoas | 35 | **33** |
| Exames | 23 | **20** |

Os únicos exames ativos remanescentes são `ESP-000016` (real, pré-existente) e
`ESP-TF0001` (homólogo sintético usado pela M25.24 como prova de
restaurabilidade) — nenhum da demonstração. A fila da médica não tem nenhum item
de teste, e não houve atendimento real posterior à M25.24 a preservar.

## 13. Prova dos 18 históricos e dos 5 Pastore

Históricos encerrados: **18** (eram 17 antes, porque a demo havia reaberto o
`ESP-000019`).

| Exame | Status | BD | Encerrado | `encerrado_em` |
|---|---|---|---|---|
| ESP-000013 | Liberado | **true** | sim | 2026-08-11 23:34:47.521604 |
| ESP-000014 | Liberado | **true** | sim | 2026-08-11 23:34:47.522478 |
| ESP-000017 | Realizado | **true** | sim | 2026-08-11 23:34:55.997869 |
| ESP-000018 | Realizado | **true** | sim | 2026-08-11 23:34:56.000541 |
| ESP-000019 | Realizado | **true** | sim | 2026-08-11 23:34:56.004647 *(restaurado)* |

`broncodilatador = true` nos cinco.

## 14. Idempotência

O script foi executado uma segunda vez com o mesmo comando:

- 0 registros removidos em **todas** as 16 entidades;
- 0 restaurações aplicadas (`ESP-000019_reencerrado: 0`);
- PDFs relatados como "já ausente", nenhum erro;
- `COMMIT` normal, sem exceção destrutiva;
- estado idêntico: 33 pessoas / 20 exames / 6 laudos / 13 LAN / R$ 3.044,79 / 18 encerrados;
- auditoria de manutenção continua com **1** linha (guardada por `WHERE NOT EXISTS`);
- 17 PDFs.

## 15. CPU e processos temporários

Atendendo à restrição da missão após o incidente do `createdb m2524_verif`:

- nenhum banco temporário foi criado — `pg_database` tem apenas `postgres` e `soprolife_m15` (o `m2524_verif` da etapa anterior já não existe);
- nenhuma suíte de testes foi executada na VPS; só consultas dirigidas;
- `pgrep -af 'createdb|m2525|m25-25|pytest'` → **nenhum processo**;
- carga ao final: `load average: 0.00, 0.00, 0.00`;
- disco: 5,9 GB de 48 GB (13%).

## 16. Health e serviços

```
{"status":"ok","versao":"0.1.0","ambiente":"prod","banco":"ok",
 "agora_utc":"2026-08-13T02:22:25Z","agora_local":"2026-08-12T23:22:25-03:00"}
```

`soprolife-m15-api`, `soprolife-painel` e `soprolife-painel-loopback`: **active**.

Observação não relacionada a esta missão: `soprolife-update-data.service` (sync
de planilhas) já estava em `failed` antes de qualquer escrita da M25.25 e
permanece assim. Não foi tocado — fica registrado como pendência independente.

Gate M25.23 e isolamento da conta médica intactos (nenhuma linha de código
alterada, HEAD idêntico): `annapec3` → papel `medico`; `contato` → `admin`;
`luizlopes214` → `gestor`. Os tooltips e a ajuda da M25.24 são estáticos do
frontend, servidos pelo mesmo commit `87271d2`.

## 17. HEAD final

`87271d2c3b75cbe0510d0fe399993998f4cdbd4f`, árvore limpa, branch
`painel-soprolife-v01`. Sem checkout, reset ou revert. O script de manutenção
vive fora do repositório, em `/opt/soprolife/backups/m25-25-rollback-demo-20260812/`,
justamente para não alterar a aplicação.

## 18. Conclusão

**M25.25 — DEMONSTRAÇÃO DE 12/08 REMOVIDA E ESTADO PÓS-M25.24 RESTAURADO**

Os 33 registros operacionais da demonstração e seus 7 PDFs saíram do sistema; as
duas alterações que a demonstração impôs a registros legítimos foram desfeitas;
e as evidências da M25.24 — 18 históricos, 5 Pastore com broncodilatador,
settlements de julho e agosto, 13 LAN somando R$ 3.044,79 — estão provadas
intactas.
