# M25.30 — Regularização dos 3 laudos assinados históricos

**Data:** 29/08/2026
**Branch:** `claude-m25-30-regularizacao-3-laudos` → integrada ff-only em `painel-soprolife-v01`
**Commit:** `328c4e8`
**VPS:** `root@soprolife-painel-01` (100.87.98.100) — `/opt/soprolife/soprolife-site`
**Estado final:** aplicado em produção, auditado, **0 falhas**

> **Sobre nomes de pacientes.** Este relatório é versionado. Seguindo a regra
> do repositório, pacientes aparecem pelo código interno `PES-XXXXXX`, nunca
> pelo nome. O nome consta apenas no `received_filename` gravado no banco —
> que é registro do arquivo como chegou, fora do Git — e foi conferido em
> terminal durante a execução.

---

## 1. O problema

Três laudos foram concluídos e assinados de verdade, com certificado, durante
o período em que a recepção de assinados estava quebrada. Os PDFs assinados
existiam; o sistema nunca recebeu os bytes. O que a administração via era o
oposto do que era verdade: documentos assinados no mundo real parados em
**"Aguardando assinatura"**, sem caminho de entrega pela tela.

O estado de produção **antes** da regularização, lido diretamente do banco:

| LAU | Exame | Paciente | Versão final corrente | Assinado registrado |
|---|---|---|---|---|
| LAU-000010 | ESP-000029 | PES-000041 | v3 `laudo_liberado` | 1 × `recusado` |
| LAU-000014 | ESP-000025 | PES-000037 | v3 `laudo_liberado` | 1 × `recusado` |
| LAU-000015 | ESP-000030 | PES-000042 | v4 `laudo_liberado` | 1 × `recusado` |

Os três recusados eram os arquivos errados que circularam na época: para
LAU-000010 e LAU-000015, o PDF final devolvido **byte a byte idêntico**, sem
assinatura nenhuma acrescentada; para LAU-000014, um arquivo que não
correspondia à versão final. O de LAU-000015 chegou a ficar marcado
`validado_externamente` por uma conferência humana — o falso positivo que
motivou a M25.29H. **Nenhum deles foi tocado nesta missão.**

---

## 2. Por que não foi um upload pela médica

O caminho normal seria a Dra. Ana reenviar os arquivos pelo painel. Ele não
serve aqui, e o motivo não é técnico: **ela não executou upload nenhum
agora**. Registrar o recebimento como se tivesse executado colocaria na
trilha uma ação que não aconteceu, com data e ator errados — exatamente o
tipo de registro que depois ninguém consegue reconstruir.

O script separa duas coisas que o fluxo normal mantém coladas porque
normalmente coincidem:

- `physician_profile_id` → **a médica responsável pelo laudo**. Continua sendo
  a Dra. Ana. É o vínculo clínico, e ele é verdadeiro.
- `uploader_user_id` → **quem executou o registro**. Passa a ser a conta
  administrativa `contato@soprolife.com.br`. É o vínculo operacional, e ele
  também é verdadeiro.

E o `audit_log` diz isso explicitamente, com
`contexto = manutencao_administrativa_historica`.

---

## 3. O que foi implementado

`painel-soprolife/nucleo-m15/scripts/importar_assinados_historicos.py`

**Dry-run por padrão. `--apply` explícito. Tudo ou nada. Não existe
`--forcar`.**

### As guardas da M25.29H, inteiras

O script não relaxa nenhuma verificação — ele troca o transporte (HTTP
multipart autenticado pela médica) por um transporte administrativo, e nada
mais. Rodam as mesmas funções de `app/services/signature_acceptance.py`:

- a origem é a versão final **corrente** (`current_version_id`);
- o arquivo não é prévia;
- o arquivo não é byte a byte igual ao PDF final sem assinatura;
- o arquivo traz estrutura de assinatura (`/ByteRange` + `/Sig`);
- a associação com o laudo é **forte** — carimbo coerente, código de
  verificação, ou o PDF final inteiro contido no arquivo. Nunca o código LAU
  impresso sozinho, que a prévia também carrega.

### O que ele acrescenta

Verificações que só fazem sentido numa importação manual e nomeada, contra um
**manifesto versionado** (`CASOS_M25_30`, só identificadores e hashes — sem
nome de paciente): SHA-256 do arquivo, código de verificação, código do exame
e número da versão final.

### Identificação é pelo conteúdo

`identificar_pelo_conteudo()` **não recebe o nome do arquivo**. Lê o carimbo
em metadado e, como reserva, os códigos impressos. O nome só é gravado em
`received_filename`, como registro do que chegou — nunca como critério.

### O que ele nunca faz

Não apaga nada. Não toca nos recusados históricos. Não reescreve `audit_logs`.
Não marca entrega. Não cria `validado_externamente`. Não preenche
`validated_by_user_id` / `validated_at`. Não afirma assinatura qualificada.

---

## 4. Testes

`painel-soprolife/nucleo-m15/tests/test_m25_30_regularizacao_assinados_historicos.py`
— **18 testes, todos verdes**, com pacientes, médicas, CRMs e PDFs sintéticos.

| Cenário exigido | Teste |
|---|---|
| LAU correto aceita | `test_lau_correto_e_regularizado_e_sai_da_fila` |
| `validation_code` errado recusa | `test_validation_code_errado_recusa` |
| versão errada recusa | `test_versao_final_esperada_errada_recusa` |
| prévia recusa | `test_previa_assinada_recusa` |
| PDF sem assinatura recusa | `test_pdf_sem_assinatura_recusa` |
| arquivo de outro LAU recusa | `test_arquivo_de_outro_laudo_recusa` |
| idempotência | `test_segunda_execucao_nao_duplica` |
| histórico recusado preservado | `test_historico_recusado_e_preservado` |
| download final devolve o SHA novo | `test_download_administrativo_devolve_o_sha_importado` |

Extras: trilha de auditoria sem PII, nome de arquivo não identifica o laudo,
ator com papel médico é recusado, reimportar bytes já recusados para, tudo ou
nada, manifesto sem arquivo correspondente para, e dry-run não escreve.

**Suíte completa:** 1468 passaram, 30 puladas, **13 falhas pré-existentes e
alheias** — 12 em `test_live_multisheet_reader.py` por ausência de
`googleapiclient`, e 1 em `test_m25_17_operacao_limpa.py` por PNGs versionados
no commit `1604ba1` (M25.21). Nenhuma tocada por esta missão.

---

## 5. Execução em produção

| Etapa | Resultado |
|---|---|
| Backup do banco | `/opt/soprolife/backups/m15/manual/soprolife_m15-20260829T042756Z.dump` — 372.953 bytes, verificado com `pg_restore --list`, SHA-256 `8f5fe24e…90bd2` |
| Staging privado | `/opt/soprolife/private/staging-m25-30/` — `0700 soprolife:soprolife`, arquivos `0600` |
| SHA-256 após a cópia | **os três idênticos ao original local** |
| Código na VPS | `cf00073` → `328c4e8`, ff-only. Diff: **somente os 2 arquivos novos** |
| Restart | **não executado** — nenhuma alteração em código de aplicação o exigia |
| Dry-run | 3 analisados, **3 aprovados, 0 divergentes** |
| `--apply` | **3 documentos regularizados** |
| Reexecução | **3 já regularizados, 0 a gravar** — idempotente |
| Health HTTP | `200` na API (`/api/v1/health`, `banco: ok`), no proxy loopback e no HTTPS público |

A escrita rodou como `sudo -u soprolife` com `umask 0077`, para que os blobs
novos nascessem com o mesmo dono e modo dos existentes. Rodar como root teria
gravado arquivos que o serviço não conseguiria ler depois.

**Lote de manutenção:** `BAT-000061` — `direction=upload`, 3 documentos,
criado pela conta administrativa.

---

## 6. Auditoria final — prova por documento

### LAU-000010

| Campo | Valor |
|---|---|
| LAU / ESP | `LAU-000010` / `ESP-000029` |
| Paciente | `PES-000041` |
| Médica responsável | Dra. Ana Cristina do Nascimento Cunha — `59709f0c-0511-44b8-9ddc-0547bd01a3f5` |
| `source_version_id` | `9186b358-8504-4fb1-b723-1fedf81d7b93` (v3 `laudo_liberado`) |
| Nova signed version | `081c6374-b8cf-4000-b698-b84670274c0f` (v5 `laudo_assinado_externo_recebido`) |
| `signed_document_id` | `a73148ea-bf03-4062-b902-aa4607c74119` |
| SHA original local | `c600fe6340c8a18f8721632ac58b1ae0d301e3a983f13b0db6914467ebf65816` |
| SHA no banco | **idêntico** (`external_signed_documents` e `report_document_versions`) |
| SHA do blob na VPS | **idêntico** |
| SHA do download administrativo | **idêntico** (HTTP 200) |
| Status | `recebido_assinado` |
| `qualified_signature` | **false** |
| Estado da fila | `pronto_para_entrega` |

### LAU-000014

| Campo | Valor |
|---|---|
| LAU / ESP | `LAU-000014` / `ESP-000025` |
| Paciente | `PES-000037` |
| Médica responsável | Dra. Ana Cristina do Nascimento Cunha — `59709f0c-0511-44b8-9ddc-0547bd01a3f5` |
| `source_version_id` | `35ab4c1d-2dce-47b2-ab83-a3342e63c4e1` (v3 `laudo_liberado`) |
| Nova signed version | `bd970c9d-4a13-4fd8-9cb6-bfa76d12e6dd` (v5 `laudo_assinado_externo_recebido`) |
| `signed_document_id` | `7d478e07-741c-426f-b6c4-707139af7ddc` |
| SHA original local | `05ef4c075865e21f4f197de100d3d0401ff94bccab73e32abee97ba2afdc7c87` |
| SHA no banco | **idêntico** |
| SHA do blob na VPS | **idêntico** |
| SHA do download administrativo | **idêntico** (HTTP 200) |
| Status | `recebido_assinado` |
| `qualified_signature` | **false** |
| Estado da fila | `pronto_para_entrega` |

### LAU-000015

| Campo | Valor |
|---|---|
| LAU / ESP | `LAU-000015` / `ESP-000030` |
| Paciente | `PES-000042` |
| Médica responsável | Dra. Ana Cristina do Nascimento Cunha — `59709f0c-0511-44b8-9ddc-0547bd01a3f5` |
| `source_version_id` | `1442cda0-6595-4ba5-bbbc-4c7a1229ffad` (v4 `laudo_liberado`) |
| Nova signed version | `54e7eb1a-6f52-43e4-b10e-54edca849606` (v6 `laudo_assinado_externo_recebido`) |
| `signed_document_id` | `45a4cb63-71a9-4548-8de7-d57ccbbe671a` |
| SHA original local | `b4f1d78049483cc7e2e191fc03eded9753aad1975933497d21ce08fc4dc84c2a` |
| SHA no banco | **idêntico** |
| SHA do blob na VPS | **idêntico** |
| SHA do download administrativo | **idêntico** (HTTP 200) |
| Status | `recebido_assinado` |
| `qualified_signature` | **false** |
| Estado da fila | `pronto_para_entrega` |

> O download administrativo foi exercitado pelo **handler real**
> (`GET /api/v1/laudos/{id}/assinado/conteudo`), com autenticação e RBAC
> reais: um token foi emitido em memória para a conta administrativa — o
> mesmo que o login produz —, usado uma vez e descartado. Ele nunca foi
> impresso nem gravado.

### Guardas documentais registradas no aceite

| Guarda | LAU-000010 | LAU-000014 | LAU-000015 |
|---|---|---|---|
| `origem_e_a_versao_final` | ✅ | ✅ | ✅ |
| `parece_previa` | não | não | não |
| `identico_ao_final` | não | não | não |
| `tem_estrutura_assinatura` | ✅ | ✅ | ✅ |
| `contem_o_final` | ✅ | ✅ | ✅ |
| `metadado_coerente` | — | — | ✅ |
| `codigo_validacao_coerente` | ✅ | ✅ | ✅ |
| **Veredito** | ACEITO | ACEITO | ACEITO |

`metadado_coerente` é falso em LAU-000010 e LAU-000014 porque o carimbo
desses dois é anterior ao campo `document_state`, que a guarda exige. Não é
uma fraqueza no caso: os três têm **`contem_o_final` verdadeiro** — o PDF
final da SoproLife é prefixo byte a byte do arquivo devolvido, que é a prova
documental mais forte disponível sem criptografia — e ainda o código de
verificação, que a prévia não imprime. Cada um dos três passou por **dois
caminhos independentes** de associação forte; LAU-000015, por três.

### Contador administrativo

```
aguardando_laudo                       0
aguardando_assinatura                  0     ← era 3
assinado_recebido_validacao_pendente   0
pronto_para_entrega                    3     ← LAU-000010, LAU-000014, LAU-000015
entregue                              11
```

**Aguardando assinatura = 0 para LAU-000010, LAU-000014 e LAU-000015.**
E zero na fila inteira — nenhum outro laudo ficou parado nesse estado.

### O que foi preservado

| LAU | Recusado histórico | Status | Blob |
|---|---|---|---|
| LAU-000010 | `5292c9b3-dfaa-439f-aa09-8fac74da2fb9` | `recusado` | presente, intacto |
| LAU-000014 | `bec06587-9822-448b-b862-9459478b2998` | `recusado` | presente, intacto |
| LAU-000015 | `ea1f4605-9f66-4113-bf84-4bc3980d025c` | `recusado` | presente, intacto |

Nenhuma versão antiga foi apagada. Nenhum `audit_log` foi reescrito.

### Laudos que não podiam ser tocados

| LAU | Status | Versões | Assinados | Fila | Escrita da M25.30 |
|---|---|---|---|---|---|
| LAU-000012 | `liberado` | 4 | `entregue` | `entregue` | **nenhuma** |
| LAU-000013 | `liberado` | 4 | `entregue` | `entregue` | **nenhuma** |

### Trilha de auditoria

Uma linha por documento, ação `laudo_assinado_regularizado_historicamente`,
com `user_id` = conta administrativa,
`contexto = manutencao_administrativa_historica`,
`qualified_signature = false`, SHA do arquivo, versão de origem, lote e as
guardas documentais. **Nenhum nome de paciente entra na trilha** — verificado
por asserção na auditoria.

---

## 7. O que continua sendo verdade

Este trabalho é **documental, não criptográfico**. Nada aqui verifica cadeia
ICP-Brasil, certificado, revogação, integridade do digest ou identidade do
assinante. `qualified_signature` continua **falso** em toda a trilha, de
propósito, e a fila devolve
`assinatura_verificada_criptograficamente: false` em cada um dos três.

Os três estão **prontos para entrega**, e **nenhum foi marcado como
entregue**. "Assinado recebido" e "entregue ao paciente/clínica" continuam
sendo fatos diferentes; o segundo é uma ação humana que ninguém executou.

---

## 8. Pendência operacional

O staging privado `/opt/soprolife/private/staging-m25-30/` ainda contém os
três PDFs assinados. Os bytes já vivem no armazenamento oficial de laudos, com
hash conferido — a cópia em staging é redundante e é dado de saúde
identificável numa pasta a mais. **Não foi removida porque a missão não
autorizou remoção.** Removê-la é um comando de uma linha e uma decisão sua:

```bash
ssh root@100.87.98.100 'rm -rf /opt/soprolife/private/staging-m25-30'
```

---

**REGULARIZAÇÃO HISTÓRICA CONCLUÍDA — LAU-000010, LAU-000014 E LAU-000015
ASSOCIADOS AOS PDFs ASSINADOS CORRETOS E REMOVIDOS DA FILA DE AGUARDANDO
ASSINATURA.**
