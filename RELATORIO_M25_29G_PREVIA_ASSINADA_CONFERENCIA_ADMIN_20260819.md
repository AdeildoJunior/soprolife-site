# M25.29G — Fila de assinatura higienizada e conferência administrativa simples

**Data:** 19/08/2026 · **Branch:** `claude-m25-29g-previa-assinada-conferencia`
**HEAD final:** `0fd6e2fcd31d77e3c81e9260131e2f6b2d69d79d` (local = oficial = VPS)
**Migration:** `b8d3e2f7a145` aplicada em produção
**Backup:** `/opt/soprolife/backups/pre-m2529g-20260819-093444.dump` (401 entradas, validado)

> ## ✅ CONCLUÍDA — código implantado, dados corrigidos, fila higienizada

---

## 1. Os dois achados

**Da fila.** Assim que a M25.29F destravou os downloads, o PDF que a fila
administrativa entregava como "laudo assinado" do **LAU-000014** pôde ser
aberto por uma pessoa. Ele continha, impresso no papel:

```
PRÉVIA
PRÉVIA — DOCUMENTO NÃO CONCLUÍDO
Código de verificação: —
versão 2
```

com assinatura digital embutida. Era a **prévia assinada** por fora, antes de
o laudo existir como documento final.

**Da tela.** Ao testar a fila, um `window.prompt()` exigia *digitar* a frase
`Confirmo a conferência externa` — e não estava claro qual botão o disparava.

---

## 2. A auditoria dos cinco laudos

Executada como `root`, somente leitura, em 19/08. Os **hashes** decidiram a
classificação sem depender de inferência:

| laudo | sha da v3 (final) | sha da v4 (recebida) | tamanho | pareado por | categoria |
| --- | --- | --- | --- | --- | --- |
| LAU-000010 | `3138653edcd0…` | **`3138653edcd0…`** | 202162 = 202162 | metadado | **C** |
| LAU-000011 | `00fa69409c3e…` | **`00fa69409c3e…`** | 202182 = 202182 | metadado | **C** |
| LAU-000012 | `6931d59d5e50…` | `fc55601acc1d…` | 202234 → 315304 | metadado | **A** |
| LAU-000013 | `cfc316d9532e…` | `28a62ebfbe90…` | 202164 → 315222 | metadado | **A** |
| LAU-000014 | `5bc43fc2f0c2…` | `b291122ccd49…` | 202167 → **167098** | código impresso | **B** |

* **A** — final assinado, válido para conferência operacional
* **B** — prévia assinada
* **C** — PDF final devolvido **sem assinatura**: hash idêntico ao original

Nenhum caso ficou ambíguo.

### O campo que mentia

A auditoria imprimia `origem era PRÉVIA? = False` para o LAU-000014 — e o
arquivo era uma prévia. O campo é **derivado do pareamento** e herdou o erro
dele: como o pareamento foi pelo código LAU **impresso** (que a prévia também
carrega) e o laudo já estava concluído, o sistema amarrou o blob à versão
corrente e gravou uma origem falsa.

O script de manutenção confirmou lendo o **conteúdo** do arquivo:

```
conteúdo parece prévia?................. True
tem estrutura de assinatura?............ True
menor que o final?...................... True
```

> **Lição:** para julgar a origem de um assinado, valem `match_method`, o
> hash e o conteúdo — não o campo de origem.

Os LAU-000010 e 011 se revelaram outro caso: `idêntico ao final? True` e
`tem estrutura de assinatura? False`. A médica devolveu **o mesmo arquivo que
baixou**, sem assinar.

---

## 3. O estado `recusado`

Um estado **genérico e reutilizável**, não um por modo de falha. O motivo
detalhado vive na auditoria, onde cabe texto e onde não é preciso criar um
status novo a cada erro documental descoberto.

```python
ASSINADO_RECUSADO = "recusado"
```

Motivos de catálogo fechado: `previa_assinada_antes_da_conclusao`,
`documento_sem_assinatura_externa`,
`documento_nao_corresponde_a_versao_final`.

Um documento recusado **continua existindo** — blob, hash, versão histórica e
trilha. O que ele perde é o direito de representar o laudo:

| | |
| --- | --- |
| aparece como conferência pendente | ❌ |
| pode ser baixado como laudo assinado | ❌ (404 controlado) |
| pode ser conferido | ❌ (409 com mensagem própria) |
| pode ser entregue | ❌ |
| bloqueia nova assinatura | ❌ |
| blob, hash, versão e trilha | ✅ preservados |

`_assinado_mais_recente()` passou a ignorá-lo — é ela que decide, **num lugar
só**, o estado da fila, o que o download entrega e se um novo arquivo entra.

### Duas correções que só apareceram ao testar

1. **Um recusado prendia o laudo para sempre.** O filtro de elegibilidade
   contava qualquer assinado não-em-conferência como "já recebido", então o
   laudo sumia da lista da médica — e o único conserto seria apagar o
   registro, exatamente o que não se pode fazer.
2. **Reenviar o mesmo arquivo recusado respondia "já havia sido recebido"**,
   o que soa como sucesso. Agora responde que o arquivo não serve e que o PDF
   final precisa ser assinado de novo.

### Migration

`b8d3e2f7a145`, pequena e reversível. O `downgrade` **falha de propósito** se
ainda houver linha em `recusado`: reverter apagando a classificação de um
documento inválido seria pior do que não reverter.

---

## 4. A confirmação administrativa

### Qual clique disparava o popup — provado

O dispatch usa `event.target.closest("button")` e três atributos `data-*`
**distintos**, verificados com `matches()`, que exige o atributo exato:

| botão | atributo | ação |
| --- | --- | --- |
| Baixar exame técnico | `data-delivery-download-mir` | download, **zero** popup |
| Baixar laudo assinado | `data-delivery-download-assinado` | download, **zero** popup |
| Registrar conferência | `data-delivery-validate` | abre a confirmação |

**Não havia sobreposição de handler.** O popup vinha do botão correto; o
problema era a digitação exigida.

### O que existe agora

```
Confirmar conferência do PDF assinado?

Confirme apenas se você conferiu externamente o documento assinado.
A SoproLife não realiza validação criptográfica da cadeia ICP-Brasil.

            [Cancelar]  [Confirmar conferência]
```

A intenção da M25.20 — *um clique distraído não pode virar testemunho de uma
pessoa identificada* — **continua**: dois passos deliberados, na própria tela.
O que saiu foi a digitação, que virava copiar e colar.

**O contrato da API não foi afrouxado:** ela segue exigindo a frase, agora
constante do cliente. `qualified_signature` permanece **falso**.

A conferência exige **`ROLE_ADMIN`**: um usuário `operacional` vê a fila mas
recebe `403` — recusa clara, não sucesso silencioso.

---

## 5. Correção dos dados — executada

Rodada em produção após backup, em dry-run e depois `--apply`, com o script
validando a evidência antes de cada escrita.

| laudo | mudança | evidência que sustentou |
| --- | --- | --- |
| LAU-000014 | `recebido_validacao_pendente` → `recusado` | o conteúdo do PDF traz as marcas de prévia |
| LAU-000010 | `recebido_validacao_pendente` → `recusado` | hash idêntico ao final — nada foi acrescentado |
| LAU-000011 | `recebido_validacao_pendente` → `recusado` | hash idêntico ao final — nada foi acrescentado |

Cada um gerou uma linha `assinado_externo_recusado` na trilha, em
2026-08-19 09:36.

### O que NÃO foi alterado

* **Nenhuma conclusão clínica** foi tocada
* **Nenhum exame, paciente ou MIR** foi alterado
* **Nenhum `DELETE`** — nem de linha, nem de arquivo
* **Blobs, hashes e histórico preservados** — as quatro versões de cada laudo
  continuam onde estavam
* Código de verificação, `current_version_id`, `signature_status` e os 35
  lotes: intactos
* Financeiro, Pastore e CRM: não tocados

---

## 6. Estado final dos cinco laudos

| laudo | estado | ação necessária |
| --- | --- | --- |
| **LAU-000010** | recusado | precisa somente nova assinatura do PDF final |
| **LAU-000011** | recusado | precisa somente nova assinatura do PDF final |
| **LAU-000012** | íntegro | aguardando conferência administrativa |
| **LAU-000013** | íntegro | aguardando conferência administrativa |
| **LAU-000014** | recusado | prévia assinada; precisa somente nova assinatura do PDF final |

**Nenhum dos três recusados precisa de novo laudo nem de nova conclusão
clínica.** O laudo final existe e está íntegro nos três. A médica abre, clica
em **"Baixar PDF para assinar"**, assina por fora e devolve.

A fila de conferência caiu de **cinco** para **dois** documentos — e os dois
que restam foram verificados: arquivo presente, tamanho igual ao registrado,
`%PDF` no início, backend relê sem erro.

---

## 7. Testes — 23 novos

Estado `recusado` existe e é genérico · não conta como assinado atual · não
pode ser conferido nem entregue · não é baixável como assinado · devolve o
laudo a "aguardando assinatura" · blob e histórico sobrevivem · libera nova
assinatura · conclusão clínica permanece · script em dry-run · script em
`--apply` · idempotência · script para diante de conferência já registrada ·
reenviar o mesmo arquivo recusado não o torna válido · confirmação nova com
dois botões · downloads sem popup · sem frase digitada · contrato do backend
intacto · `qualified_signature` falso · nada entregue sozinho · RBAC da
médica · `ROLE_ADMIN` na conferência · alvo de toque de 48px · sem dado real.

Contratos anteriores atualizados por mudança legítima: **M25.20** (a frase
digitada virou dois passos — asserções foram de 3 para 6) e
**test_migrations** (head esperada acompanha a migration nova, e a prova real
continua sendo existir **exatamente uma** head).

**Regressão focada:** 191 verdes em M25.18, M25.20, M25.29D, M25.29E,
M25.29F, M25.29G e migrations. Suíte completa não executada — a orientação
foi hotfix focado com a operação esperando.

---

## 8. Deploy e produção

```
integração  298a255..0fd6e2f  ff-only em painel-soprolife-v01 (sem force)
VPS         ff-only até 0fd6e2f, árvore limpa
migration   a2f6c81d4b73 → b8d3e2f7a145
restart     soprolife-m15-api + soprolife-painel + soprolife-painel-loopback
```

| prova | resultado |
| --- | --- |
| Backup antes de escrever | ✅ 520K, 401 entradas, validado por `pg_restore --list` |
| Alembic | ✅ `b8d3e2f7a145 (head)` |
| Serviços | ✅ API, Painel e Loopback `active` |
| Health | ✅ `HTTP 200`, `banco: ok`, `ambiente: prod` |
| Timer / snapshots | ✅ `active`, `Result=success`, `ExecMainStatus=0` |
| HEAD local = oficial = VPS | ✅ `0fd6e2fcd31d77e3c81e9260131e2f6b2d69d79d` |
| Árvore | ✅ limpa nos três |
| Cache busting | ✅ `?v=2026081901` — o navegador recebe a tela nova |

---

## 9. Limitações declaradas

* O sistema **não** valida cadeia ICP-Brasil. A checagem de `/ByteRange`,
  `/Sig` e `/SubFilter` feita pelo script distingue "tem estrutura de
  assinatura" de "é o mesmo PDF que saiu daqui" — e **nada além disso**.
* A verificação de celular é contrato de CSS, não medição em navegador.
* O motivo da recusa vive na auditoria; a tela mostra apenas o estado.
* **Ponto operacional fora do sistema:** a prévia assinada do LAU-000014 foi
  baixada pela administração em 16, 17 e 18/08. `delivered_at` está vazio — o
  sistema não registrou entrega — mas é preciso confirmar com a operação se
  algum desses arquivos chegou a sair para paciente ou clínica. Isso o
  sistema não alcança.

Nenhuma PII e nenhum segredo constam deste relatório.

---

**M25.29G — FILA DE ASSINATURA HIGIENIZADA, DOCUMENTOS INVÁLIDOS PRESERVADOS E FLUXO CLÍNICO LIBERADO**
