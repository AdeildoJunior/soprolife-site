# M25.29B — Remoção dos laudos criados exclusivamente para teste

**Data:** 14/08/2026, 09:30 → 09:40
**VPS:** `soprolife-painel-01` — `/opt/soprolife/soprolife-site`
**HEAD:** `f7e3b01` — inalterado (**nenhuma alteração de código**)
**Alembic:** `a2f6c81d4b73 (head)` — inalterado
**Decisão:** autorização humana explícita posterior à M25.29A, que havia
preservado estes quatro laudos por ambiguidade

---

## 1. Mapeamento LAU → ESP → pessoa

Resolvido **por FK e ID**, nunca por nome de paciente.

| LAU | ID do laudo | ESP real | pessoa | estado | versões | atribuição | assinatura | arquivos |
|---|---|---|---|---|---|---|---|---|
| LAU-000002 | `0ebb7cc8-…` | ESP-000017 · 04/08 | PES-000030 | liberado | 3 | 1 `initial_assignment` | 1 institucional | 3 |
| LAU-000003 | `15f7bc32-…` | ESP-000018 · 04/08 | PES-000031 | liberado | 3 | 1 `initial_assignment` | 1 institucional | 3 |
| LAU-000004 | `1e352866-…` | ESP-000019 · 01/08 | PES-000032 | liberado | 3 | 1 `initial_assignment` | 1 institucional | 3 |
| LAU-000005 | `dd073cd8-…` | ESP-000019 · 01/08 | PES-000032 | liberado | 4 | 1 `corrective_document` | 1 institucional | 4 |

**São 4 laudos sobre 3 exames distintos.**

- **ESP-000019 tem dois laudos**: LAU-000005 é o documento corretivo de
  LAU-000004 (`corrects_document_id = 1e352866-…`). Por isso o corretivo foi
  removido **primeiro** na transação — a auto-FK exige essa ordem.
- **ESP-000013 e ESP-000014** (os dois exames Pastore de julho) **nunca tiveram
  laudo** no Centro de Comando. Não entraram nesta manutenção em nenhum momento.

Todas as atribuições eram da Dra. Ana. `report_addenda`,
`external_signature_batches`, `external_signed_documents` e
`qualified_signature_requests`: **0 linhas** — não existia ramo de assinatura
externa nem adendo.

**Nenhum artefato era compartilhado entre laudos de exames diferentes.**

---

## 2. Achado do dry-run — o exame técnico mora dentro do laudo

As 13 versões **não eram todas laudo**. Divididas por `kind`:

| espécie | qtd | o que é | ação |
|---|---|---|---|
| `laudo_previa` | 5 | prévia gerada pelo Centro de Comando | removido |
| `laudo_liberado` | 4 | laudo liberado pelo Centro de Comando | removido |
| `original` | 4 | **PDF técnico da MIR** | **arquivo preservado** |

O código é explícito (`app/routers/reports.py:225`):

```python
# M25.2 — espécies do laudo próprio da SoproLife (documento separado do
# PDF técnico da MIR, que permanece na versão `original`).
KIND_LAUDO_PREVIA = "laudo_previa"
KIND_LAUDO_LIBERADO = "laudo_liberado"
```

e em `reports.py:2556`: `is_technical_exam = version.kind == KIND_ORIGINAL`.

Ou seja: **o exame técnico original é uma versão do registro do laudo**. Apagar
o laudo sem separar antes teria destruído o documento MIR do exame real — o
oposto do que a manutenção pede. Essa distinção não estava visível no dry-run da
M25.29A.

Os 4 registros `original` correspondem a **3 arquivos distintos** por SHA256:

| ESP | SHA256 | bytes | conteúdo |
|---|---|---|---|
| ESP-000017 | `b6d36eb686e6a9c7dd5fa61400f9a19a5061b3c742ff0fc809208fb79675f9b0` | 465.016 | 7 objetos de imagem — laudo MIR escaneado |
| ESP-000018 | `a7f57faa70b2046ba2a348bd354f1d0a730c04b8ae0843812c25b4768ecd03e4` | 455.108 | 7 objetos de imagem — laudo MIR escaneado |
| ESP-000019 | `3bfb285fca044f8585d779bf3cb88fb814f814138dd2060931086e0f24e4abe5` | 9.772 | **0 imagens**, PDFCreator 1.7.2 |

Duas observações registradas para conferência humana:

1. **O `original` de ESP-000019 está duplicado.** LAU-000004 e LAU-000005
   apontam para arquivos diferentes com **SHA256 idêntico**: ao criar o
   documento corretivo, o original foi copiado. Preservado uma única vez.
2. **O `original` de ESP-000017 tem o mesmo SHA256** que a limpeza de 13/08
   removeu como `LAU-000009 v1` (`b6d36eb6…75f9b0`). O PDF MIR real vinha sendo
   reaproveitado nos testes da plataforma.
3. **Anomalia em ESP-000019:** 9.772 bytes, nenhuma imagem, produzido por
   PDFCreator 1.7.2 — não se parece com os MIR escaneados dos outros dois. Por
   decisão do gestor foi tratado **pelo tipo declarado** (`kind='original'`), não
   pelo conteúdo, e preservado como os demais. **Vale sua conferência.**

---

## 3. Backup — novo, imediatamente antes da escrita

Diretório: `/opt/soprolife/backups/m25-29b-laudos-teste-20260814/` (`chmod 700`)

| Item | Valor |
|---|---|
| Timestamp do pré-voo | `2026-08-14T09:30:32-0300` |
| HEAD VPS | `f7e3b01`, árvore limpa (0 alterações) |
| Health | `status ok` · `banco ok` |
| Alembic | `a2f6c81d4b73 (head)` |
| Timer M25.28 | `active` |
| Dump | `soprolife_m15-pre-m2529b-20260814.dump` (`pg_dump -Fc`), 304.758 bytes |
| Validação | `pg_restore -l` → **401 entradas**, **47 `TABLE DATA`** |
| Tabelas-chave no dump | `report_documents`, `report_document_versions`, `report_assignments`, `report_assignment_events`, `report_signatures`, `spirometry_exams`, `people`, `financial_entries`, `partner_settlements`, `partner_settlement_items`, `audit_logs` — todas presentes |
| SHA256 do dump | `ddcfb81ece79600ebcda519e11c31c691432f1f80b92f4d9fe406e02cfb7761f` |

**Os 13 PDFs foram copiados para `pdfs/` com nome legível
(`LAU-000002-v1-original.pdf` etc.) e o SHA256 de cada um foi conferido contra
o valor gravado no banco antes da cópia: 13 conferidos, 0 divergentes.**

Nenhum segredo foi impresso em nenhuma etapa.

---

## 4. Objetos removidos por tabela — 29 linhas em 5 tabelas

Uma única transação, **por ID explícito**. Sem `DELETE` por data, sem `LIKE`,
sem `session_replication_role`. As FKs ficaram ativas o tempo todo.

| Tabela | Linhas |
|---|---|
| `report_signatures` | 4 |
| `report_assignment_events` | 4 |
| `report_assignments` | 4 |
| `report_document_versions` | 13 |
| `report_documents` | 4 |
| **Total** | **29** |

Zero linhas tocadas em `spirometry_exams`, `people`, `person_contacts`,
`consents`, `followups`, `financial_entries`, `partner_settlements`,
`partner_settlement_items`, `physician_profiles`,
`physician_signature_assets`, `users`.

### Travas clínicas — técnica estreita

Suspensos **um a um pelo nome**, apenas os 3 que disparam em `DELETE`:

```
ALTER TABLE report_assignment_events  DISABLE TRIGGER trg_report_assignment_events_m24c_immutable
ALTER TABLE report_assignments        DISABLE TRIGGER trg_report_assignments_m24c_history
ALTER TABLE report_document_versions  DISABLE TRIGGER trg_report_document_versions_m24c_immutable
```

Reativados **antes do COMMIT**, com verificação programática na mesma transação
(um `DO` block aborta tudo se algum não voltar ao estado `O`).
`audit_logs.trg_audit_append_only` **nunca foi tocado**.

---

## 5. Arquivos

### Preservados — o exame técnico original

Criada a raiz `private/reports/exames-originais/`, **indexada por exame**, não
por laudo — porque o documento pertence ao ESP, não ao LAU:

```
exames-originais/31559e0b-…/ESP-000017-exame-original.pdf   465.016 b6d36eb6…
exames-originais/7185d468-…/ESP-000018-exame-original.pdf   455.108 a7f57faa…
exames-originais/dd06039e-…/ESP-000019-exame-original.pdf     9.772 3bfb285f…
```

Dono `soprolife:soprolife`, diretórios `0700`, arquivos `0600` — o mesmo regime
do restante do armazenamento privado. Os três hashes foram conferidos **depois**
da cópia e **antes** de qualquer remoção.

### Removidos — apenas o que o Centro de Comando gerou

A árvore `private/reports/laudos/` foi esvaziada: **13 arquivos, 3 diretórios de
exame**. Os 3 originais MIR já estavam preservados fora dela.

### Estado final do armazenamento privado

| Área | Arquivos |
|---|---|
| `laudos/` | **0** |
| `exames-originais/` | **3** |
| `assinaturas/` | **1** (rubrica da Dra. Ana, intacta) |

**Não tocados:** rubrica da Dra. Ana (`59709f0c-…`), PDFs MIR originais,
qualquer documento da Pastore, documentos pertencentes ao ESP.

---

## 6. Objetos preservados

- **Pessoas reais:** 31, nenhuma alterada
- **Exames:** 18, nenhum alterado
- **Dra. Ana:** perfil ativo (1) e rubrica (1) intactos — só as atribuições dos
  laudos de teste saíram
- **Estado histórico da M25.24:** preservado nos cinco exames (§7)
- **Nenhum órfão referencial:** 7 verificações, todas **0**

Não foi criado paciente, exame, laudo, assinatura nem lançamento. Nenhum valor
foi alterado.

---

## 7. Os cinco ESP reais — provados um a um

| ESP | Data | Status | BD | Pessoa | Unidade | Histórico M25.24 | Laudos |
|---|---|---|---|---|---|---|---|
| ESP-000013 | 14/07/2026 | Liberado | ✅ | PES-000025 | Pastore Ipanema | `laudo_externo_ja_entregue` | 0 |
| ESP-000014 | 18/07/2026 | Liberado | ✅ | PES-000026 | Pastore Ipanema | `laudo_externo_ja_entregue` | 0 |
| ESP-000017 | 04/08/2026 | Realizado | ✅ | PES-000030 | Pastore Ipanema | `laudo_externo_e_teste_do_fluxo` | 0 |
| ESP-000018 | 04/08/2026 | Realizado | ✅ | PES-000031 | Pastore Ipanema | `laudo_externo_e_teste_do_fluxo` | 0 |
| ESP-000019 | 01/08/2026 | Realizado | ✅ | PES-000032 | Pastore Ipanema | `laudo_externo_e_teste_do_fluxo` | 0 |

Os cinco mantêm pessoa, data, unidade Pastore Ipanema, `broncodilatador = true`
e todos os demais dados clínico-operacionais.

**O mecanismo histórico da M25.24 já existia e foi preservado por construção**:
ele vive em `spirometry_exams.encerramento_motivo`, tabela que não foi tocada.
A base continua sabendo que **o exame aconteceu de verdade na Pastore e foi
laudado fora da plataforma** — sem nenhum LAU operacional atribuído ao Centro de
Comando. Nenhum laudo substituto foi criado, nenhum PDF foi inventado.

---

## 8. Financeiro SoproLife

**13 lançamentos · TOTAL = R$ 3.044,79** — inalterado.

Nenhum LAN foi criado ou removido nesta manutenção.

**Conciliação própria (extra-Pastore):**

| | |
|---|---|
| Exames | 13 |
| Conciliados | **13 de 13** |
| Pendentes | **0** |
| Ainda a conciliar | **R$ 0,00** |
| Divergências | **0** |

---

## 9. Fechamentos Pastore — nem um centavo alterado

| Competência | Exames | Valor | Status | Quais |
|---|---|---|---|---|
| Julho/2026 | 2 | **R$ 219,00** | `a_receber` | ESP-000013, ESP-000014 |
| Agosto/2026 | 3 | **R$ 328,50** | `a_receber` | ESP-000017, ESP-000018, ESP-000019 |
| **Total** | **5** | **R$ 547,50** | | |
| Recebido | | **R$ 0,00** | | |

`partner_settlements` (2) e `partner_settlement_items` (5) intactos; o vínculo
ESP → fechamento permanece exatamente como estava. Apagar os laudos não alterou
nenhum valor: os fechamentos são calculados a partir do **exame**, não do laudo.

---

## 10. Estatísticas, indicadores e snapshots

Este era o objetivo da manutenção: impedir que laudos de teste contaminem os
números operacionais. Estado após a remoção:

| Indicador | Valor |
|---|---|
| Laudos produzidos | **0** |
| Laudos liberados | **0** |
| Versões de laudo | **0** |
| Assinaturas | **0** |
| Atribuições à médica | **0** |
| Perfis médicos ativos | 1 (Dra. Ana, preservado) |
| Rubricas | 1 (preservada) |

Nenhum JSON foi editado à mão. A varredura por `LAU-0000` em `data/` e
`data-private/` está limpa: **nenhum dos quatro laudos aparece em nenhum
snapshot.**

> ### ⚠️ REGRESSÃO ABERTA — a esteira não regenera o passo 2/6
>
> **A regeneração pelo mecanismo normal NÃO foi concluída.** Desde o COMMIT
> (09:31:55) foram **24 ciclos consecutivos com `Result=exit-code` /
> `ExecMainStatus=1`** — 23 automáticos do timer mais 1 execução manual
> autorizada às 13:35. Ver §16.

### O que os snapshots congelados realmente mostram

Apenas o passo **2/6** (snapshots do PostgreSQL) falha. Os passos 3/6, 4/6 e 6/6
seguem rodando, então os arquivos se dividem em dois grupos:

| Grupo | Arquivos | Última geração |
|---|---|---|
| Passos que ainda rodam | `marketing-seo`, `runtime-status`, `saude-operacional-summary`, `ultimos-lancamentos-summary` | **13:35** (atual) |
| Passo 2/6 congelado | `auditoria-summary`, `crm-clinicas`, `crm-contatos-b2b`, `financeiro-summary`, `followup-clinicas`, `followup-pacientes`, `leads-summary`, `parcerias-pastore-summary`, `resumo-dashboard` | **09:27** |

**O conteúdo congelado, porém, continua correto.** A remoção tocou
exclusivamente tabelas `report_*`, e **nenhum snapshot operacional deriva
delas** — a única menção a "laudo" em toda a pasta está em
`auditoria-summary.local.json`, e são **nomes de ação da trilha**
(`laudo_conteudo_entregue`, `laudo_original_atribuido`…), que devem mesmo
permanecer porque a auditoria é preservada por decisão da missão.

Conferido nos snapshots de 09:27:

```
parcerias-pastore-summary : exames_realizados 5 · receita_confirmada 0.0 · fechamentos.a_receber 2   ✅
financeiro-summary        : total_lancamentos 13                                                     ✅
```

A única defasagem real é a contagem da auditoria: **647** no arquivo contra
**648** no banco — exatamente o evento de manutenção desta missão.

---

## 11. Auditoria

Trilha **append-only preservada integralmente**. Os **6 eventos históricos** que
mencionam LAU-000002…LAU-000005 continuam na trilha: a operação saiu do
operacional, o registro de que ela existiu não foi falsificado.

Total: **648** eventos (647 + 1). Registrado um único evento novo (`id 652`),
sem PII:

```
acao     = manutencao.remocao_laudos_teste_pastore_m2529b
entidade = maintenance
detalhes = laudos_removidos [LAU-000002…LAU-000005], exames_distintos 3,
           exames_preservados [ESP-000017, ESP-000018, ESP-000019],
           linhas_removidas {…5 tabelas, 29 linhas…},
           pdfs_mir_originais "preservados em private/reports/exames-originais",
           nao_alterado [spirometry_exams, people, financial_entries,
                         partner_settlements, partner_settlement_items],
           estado_historico_m2524 "preservado nos 3 exames",
           backup /opt/soprolife/backups/m25-29b-laudos-teste-20260814
```

---

## 12. Prova das travas

Os **9 triggers** do banco estão em estado `O` (habilitado).

Tentativas reais de `DELETE`, sempre revertidas:

| Tabela | Linhas | Resultado |
|---|---|---|
| `audit_logs` | 648 | **REJEITADO** — `audit_logs e append-only (M15)` |
| `report_templates` | 6 | **REJEITADO** — `M24C append-only: immutable clinical evidence` |
| `report_footer_templates` | 2 | **REJEITADO** — `M24C append-only: immutable clinical evidence` |

**Ressalva honesta:** as três tabelas cujos triggers foram suspensos
(`report_document_versions`, `report_assignments`, `report_assignment_events`)
ficaram com **0 linhas** após a remoção. Um `DELETE` nelas não encontra linha
alguma, então **a rejeição em nível de linha não pode ser exercida ali**. O que
está provado para elas é: (a) o estado `tgenabled = 'O'`, e (b) a verificação
programática executada **dentro da transação, antes do COMMIT**, que abortaria
tudo se algum não tivesse voltado. As duas tabelas testadas acima
(`report_templates`, `report_footer_templates`) pertencem à **mesma família
M24C** e provam que o mecanismo continua armado.

Nenhum laudo, exame ou paciente temporário foi criado para forçar um teste de
linha — isso violaria a regra "não gerar laudo".

---

## 13. Health, Alembic e timer

| Item | Estado |
|---|---|
Conferido às **13:35 de 14/08**:

| Item | Estado |
|---|---|
| Health | `status ok` · `banco ok` |
| Alembic | `a2f6c81d4b73 (head)` — **inalterado** |
| Timer M25.28 | `ActiveState=active` · `SubState=waiting` — a unit dispara normalmente |
| Último ciclo | ❌ `Result=exit-code` / `ExecMainStatus=1` — **último `success` foi 09:27:28**, anterior à remoção; ver §16 |
| HEAD VPS | `f7e3b01`, árvore limpa (0 alterações) |

### Diagnóstico de processos

Verificado a pedido do gestor: **não havia nenhum processo em espera**. Nem
local nem na VPS existiam `sleep`, `journalctl -f`, `tail -f`, shell órfã ou
processo de validação travado. **Nada foi encerrado, porque não havia nada
travado.** `postgresql`, `soprolife-m15-api`, `soprolife-painel`,
`soprolife-painel-loopback` e `tailscaled`: todos `active`, nenhum tocado.

A pausa não foi um processo pendurado — foi o turno anterior terminando com uma
pergunta de decisão sobre o `pii_guard` (§16). Os scripts `.sql` temporários
desta missão foram removidos de `/tmp` da VPS.

---

## 14. Estado final das filas

| Fila | Conteúdo |
|---|---|
| Fila médica | **vazia** — nenhum laudo atribuído |
| Aguardando assinatura | **vazia** |
| Laudos liberados | **vazia** |
| Fila administrativa (exames) | 18 exames reais: ESP-000001…ESP-000015, ESP-000017, ESP-000018, ESP-000019 |

Nenhum dos quatro LAU aparece em qualquer fila, versão, atribuição, assinatura
ou arquivo.

### Totais por tabela

| Tabela | Antes | Depois |
|---|---|---|
| `report_documents` | 4 | **0** |
| `report_document_versions` | 13 | **0** |
| `report_assignments` | 4 | **0** |
| `report_assignment_events` | 4 | **0** |
| `report_signatures` | 4 | **0** |
| `spirometry_exams` | 18 | **18** |
| `people` | 31 | **31** |
| `financial_entries` | 13 | **13** |
| `partner_settlements` | 2 | **2** |
| `partner_settlement_items` | 5 | **5** |
| `physician_profiles` | 1 | **1** |
| `audit_logs` | 647 | **648** |

---

## 16. ⚠️ Regressão que eu causei — esteira de snapshots parada

### O sintoma

O primeiro ciclo do timer após o COMMIT falhou, e desde então **24 ciclos
consecutivos falharam** (23 automáticos + 1 execução manual autorizada às
13:35). O passo 2/6 grava **tudo ou nada**, então nenhum dos 9 snapshots
derivados do PostgreSQL é atualizado:

```
2/6 - Gerando snapshots do painel a partir do PostgreSQL...
ERRO: Snapshot inseguro — nada foi gravado:
  ERRO PII [auditoria-summary.local.json]: possivel token/ID longo em
    'stats.por_acao.manutencao.remocao_laudos_teste_pastore_m2529b'
  ERRO PII [auditoria-summary.local.json]: possivel token/ID longo em
    'ultimos_eventos[0].acao'
ERRO: falha ao gerar snapshots a partir do PostgreSQL.
```

O painel **não está corrompido nem exibindo número errado**: ele continua
servindo o snapshot de **09:27:28**, cujo conteúdo permanece correto porque a
remoção só tocou tabelas `report_*`, das quais nenhum snapshot operacional
deriva (ver §10). A única defasagem é a contagem de eventos da auditoria, 647
contra 648. Mas os snapshots seguirão sem atualizar a cada ciclo até a causa ser
resolvida — e qualquer mudança operacional futura também deixará de aparecer.

A execução manual autorizada de 13:35 confirmou que a causa é **determinística e
a mesma**, e que o passo é **fail-closed**: `"ERRO: Snapshot inseguro — nada foi
gravado"`. Nenhum dado foi criado nem alterado por essa tentativa.

### A causa raiz — o nome da ação de auditoria

A regra em `scripts/pii_guard.py:88`:

```python
("possivel token/ID longo", re.compile(r"(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{35,}"))
```

O ponto não pertence à classe de caracteres, então o rótulo é quebrado em
segmentos:

| Ação | Segmento após o ponto | Tamanho | Resultado |
|---|---|---|---|
| `manutencao.remocao_laudos_teste_pastore_m2529b` | `remocao_laudos_teste_pastore_m2529b` | **35** | **estoura** |
| `manutencao.limpeza_geral_testes_m2529a` (M25.29A) | `limpeza_geral_testes_m2529a` | 27 | ok |
| `manutencao.remocao_testes_20260813` (13/08) | `remocao_testes_20260813` | 23 | ok |

**Exatamente um caractere acima do limite.** O segmento tem 35 e o gatilho é
`{35,}`. E como contém dígitos (`m2529b`), satisfaz o lookahead.

O nome `manutencao.remocao_laudos_teste_pastore_m2529b` foi o especificado no
briefing e eu o usei literalmente, sem medir o segmento contra o guard. **A
falha de conferência é minha** — a M25.28 documenta exatamente esta classe de
problema, e eu tinha esse histórico à mão.

### Por que não dá para simplesmente corrigir o dado

`audit_logs` é append-only por trigger, e isso foi **verificado com operação
real** (revertida):

```
UPDATE audit_logs SET acao='…' WHERE id=652;  →  ERROR: audit_logs e append-only (M15)
DELETE FROM audit_logs …                      →  ERROR: audit_logs e append-only (M15)
```

O evento `id 652` é **permanente**. Renomeá-lo exigiria suspender
`trg_audit_append_only` e reescrever a trilha — o que o próprio briefing proíbe
("Audit log permanece append-only. Não apagar ou falsificar eventos
históricos"). **Não fiz e não recomendo.**

E o rótulo não sai sozinho com o tempo: `por_acao` é agregado sobre **todos** os
registros (`snapshots.py:419-428`), sem janela temporal.

### Por que o mecanismo existente não cobre este caso

A M25.28 já tratou esta família de problema. O ruleset `m23-snapshots` **já
declara** `mapas_de_contagem: ["por_acao", …]`, o que impede que a chave seja
lida como *nome de campo proibido*. Mas o comentário do próprio código
(`pii_guard.py:294`) é explícito sobre o limite dessa correção:

> *"Os rótulos seguem passando pelos scans de conteúdo."*

Ou seja: o scan de token/ID continua se aplicando ao rótulo — por decisão de
projeto, para que "um telefone travestido de chave siga barrado". O guard está
funcionando como desenhado. Quem está fora do contrato é o nome que eu gravei.

Vale notar que o módulo irmão `scripts/audit_summary_contract.py` **já resolve
isso**: ele neutraliza rótulos que casem com `^[a-z0-9_.]{1,80}$` antes de
varrer. Meu rótulo é um slug válido por essa regra. Os dois validadores
divergem.

### Opções — decisão do gestor

Nenhuma foi executada: a correção exige mudar código de aplicação, e o briefing
determina "Não integrar código de aplicação, porque esta é manutenção de dados".

1. **Alinhar `pii_guard.py` ao `audit_summary_contract.py`** — neutralizar
   rótulo de vocabulário válido nas chaves declaradas em `mapas_de_contagem`
   antes dos scans de conteúdo. É a correção de raiz, fecha a divergência entre
   os dois validadores e protege contra o próximo nome de ação comprido.
   Mudança pequena, mas é código de aplicação e pede missão própria com teste.
2. **Elevar o limite do scan de token** de 35 para, por exemplo, 48. Menor
   alteração, mas enfraquece um controle de segurança de forma genérica.
   Não recomendo.
3. **Não mexer no código** e conviver com a esteira parada até uma missão de
   correção. O painel fica congelado em 09:27:28 indefinidamente.
   Não recomendo.

**Recomendação: opção 1**, em missão separada de correção de código.

### O que esta regressão NÃO afeta

Nada do trabalho de dados desta manutenção depende da esteira. Verificado
diretamente no banco após a falha:

- os 4 laudos continuam removidos;
- os 5 exames Pastore, os fechamentos (R$ 547,50) e os 13 LAN (R$ 3.044,79)
  continuam intactos;
- `health` = `ok` / `banco` = `ok`;
- Alembic inalterado;
- a trilha de auditoria está íntegra;
- os snapshots atuais, embora velhos, **não contêm nenhum LAU** — a varredura
  por `LAU-0000` em `data/` e `data-private/` volta vazia — e os agregados que
  exibem continuam corretos (Pastore 5 exames / 2 fechamentos a receber;
  financeiro 13 lançamentos).

### Checklist da missão — o item que não fecha

Dos 15 pontos de validação exigidos no briefing, **14 estão provados**. O que
falha é exatamente um:

> *"último ciclo automático conhecido com `Result=success`"* — ❌ o último
> `success` é de **09:27:28**, anterior à remoção.

Por isso **não declaro a missão integralmente fechada**, embora a frase de
conclusão — laudos de teste removidos sem alterar exames reais ou financeiro
Pastore — esteja inteiramente provada.

---

## 15. Conclusão

# M25.29B — LAUDOS DE TESTE REMOVIDOS SEM ALTERAR EXAMES REAIS OU FINANCEIRO PASTORE

Os quatro laudos criados exclusivamente durante testes do Centro de Comando —
LAU-000002, LAU-000003, LAU-000004 e LAU-000005, sobre 3 exames distintos — não
existem mais operacionalmente: nem documento, nem versão, nem atribuição, nem
assinatura, nem PDF, nem fila.

Os cinco exames Pastore reais permanecem íntegros, com pessoa, data, unidade,
broncodilatador e o estado histórico da M25.24 que registra que foram laudados
fora da plataforma. O PDF técnico da MIR de cada exame foi separado do laudo e
preservado em `exames-originais/`, indexado pelo exame.

Os fechamentos (R$ 219,00 + R$ 328,50 = R$ 547,50 a receber, zero recebido) e os
13 lançamentos de R$ 3.044,79 não tiveram um centavo alterado. A conciliação
própria segue 13 de 13, sem pendência nem divergência.

A trilha de auditoria foi preservada integralmente e as travas de imutabilidade
continuam armadas.

Nenhuma alteração de código foi feita e nenhum deploy de aplicação foi
realizado — esta é manutenção de dados.

**Ressalva que impede o fechamento pleno:** a esteira de snapshots está parada
desde 09:38 por causa do nome da ação de auditoria que registrei — regressão
minha, detalhada em §16, com correção pendente de decisão. O objetivo de dados
da missão foi atingido e provado direto no banco; o objetivo de
**estatísticas/snapshots (§10) permanece em aberto** até a esteira voltar a
rodar.
