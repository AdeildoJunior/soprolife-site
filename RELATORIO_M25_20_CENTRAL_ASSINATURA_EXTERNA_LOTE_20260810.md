# M25.20 — Central de assinatura externa em lote

**Data:** 2026-08-10
**Worktree:** `/home/adeildo/soprolife-worktrees/claude-m25-20-central-assinatura-lote`
**Branch:** `claude-m25-20-central-assinatura-lote`
**Branch oficial:** `painel-soprolife-v01`

A médica continua laudando **um a um**. Nada nesta entrega interpreta exame,
escolhe conclusão ou decide conteúdo clínico. O que virou lote foi o trabalho
burocrático que vem **depois** da conclusão: baixar os PDFs, levar para
assinar fora com o certificado dela, e devolver os assinados.

---

## 1. Preflight

| Item | Estado no início |
| --- | --- |
| HEAD local | `1eb85391e68721c4f9c0767e6049d6a5388e1b18` ✅ igual ao esperado |
| `origin/painel-soprolife-v01` | `1eb8539` — 0 à frente, 0 atrás |
| HEAD da VPS | `1eb8539`, branch `painel-soprolife-v01` |
| `git status` local e VPS | **limpos** |
| Serviços na VPS | `soprolife-m15-api`, `soprolife-painel`, `soprolife-painel-loopback`, `postgresql` → todos `active` |
| Health | `{"status":"ok","versao":"0.1.0","ambiente":"prod","banco":"ok"}` |
| Alembic | `d1e7b9c34a25 (head)` — sem migration pendente |
| `M15_REPORTS_MODE` | `pilot` |

Nenhuma alteração concorrente. Nenhum `reset --hard`, nenhum force push.

---

## 2. A descoberta que definiu a arquitetura

Existiam **dois caminhos paralelos** para assinatura em lote, e o de lote
estava morto.

| | Fluxo real (M25.18, em produção) | Fluxo M25.8 (código órfão) |
| --- | --- | --- |
| Botão | **"Concluir laudo"** | nenhum — nenhuma tela o chama |
| Rota | `POST /laudos/{id}/assinar-e-liberar` | `POST /laudos/{id}/finalizar-revisao` |
| Status resultante | `liberado` | `assinatura_pendente` |
| Carimbo no PDF | **não existia** | `stamp_signing_metadata` |
| Central de lote | — | filtrava por `assinatura_pendente` |

A barra de lote da M25.8 (`renderBatchBar`) só aparece para documentos em
`assinatura_pendente`. Como nenhuma tela chama `/finalizar-revisao`, **nenhum
documento jamais alcançava esse status** — a central existia no código e nunca
aparecia para ninguém.

**Decisão (confirmada com o usuário):** construir a central sobre o estado
**real** de produção. `liberado` já é, desde a M25.18, rotulado *"Concluído —
aguardando assinatura qualificada"* — ele **é** o "aguardando assinatura" da
missão §15. O ciclo de assinatura externa virou tabelas próprias, sem tocar em
`assinar-e-liberar`, no selo do PDF, na fila `/meus` nem na constraint
`clinical_state_coherent`.

### O `/lote/enviar` da M25.8 declara ICP-Brasil sem verificar a cadeia

`app/services/report_pades.py::_verify_cms` confere o `ByteRange`, o digest, o
`messageDigest` assinado e a assinatura contra a chave pública **do
certificado embutido no próprio arquivo**. Ele **não** valida cadeia, âncora
de confiança, revogação (CRL/OCSP) nem janela de validade — um certificado
autoassinado passa em todos esses testes.

Mesmo assim, `/lote/enviar` grava:

```python
"qualified_signature": True,
"trust_chain": "ICP-Brasil",
```

É exatamente o que a §14 proíbe. Ver §12 deste relatório (pendências) para o
estado atual dessa rota.

---

## 3. Modelo de dados

Arquitetura **relacional explícita** (§23) — nenhum JSON solto para dado que
precisa de integridade.

### `external_signature_batches` — BAT-000001

Um download ou uma devolução. Responde *"que arquivos saíram juntos, para
quem, quando"*.

`id`, `public_code`, `direction` (`download`/`upload`), `physician_profile_id`,
`created_by_user_id`, `document_count`, `created_at`.

### `external_signed_documents` — o PDF que voltou

`id`, `report_document_id`, `report_document_version_id` (a versão nova),
`source_version_id` + `source_sha256` (o laudo concluído que o originou),
`batch_id`, `physician_profile_id`, `uploader_user_id`, `sha256`,
`size_bytes`, `received_filename`, `match_method`, `status`, `received_at`,
`confirmed_at`, `validated_by_user_id`, `validated_at`, `validation_method`,
`validation_reference`, `delivered_at`, `delivered_by_user_id`.

**Constraints que carregam regra de negócio:**

| Constraint | O que impede |
| --- | --- |
| `uq_assinado_documento_sha256` (`report_document_id`, `sha256`) | o mesmo arquivo virar duas versões — idempotência no banco, não só no código |
| `validacao_externa_coerente` | meia evidência de validação (quem sem quando, método sem quem) |
| `status_validado_exige_validador` | "validado externamente" sem ninguém que tenha validado |

**Um arquivo que não pôde ser identificado com segurança nunca vira linha
aqui.** Ele é reportado na conferência e devolvido como "não identificado".

### Novo tipo de versão

`laudo_assinado_externo_recebido`. O nome **não** diz "validado": receber um
PDF que parece assinado não é o mesmo que ter conferido a cadeia ICP-Brasil.

---

## 4. Migration — `e7c4b03a91df`

Aditiva e reversível. `down_revision = d1e7b9c34a25`.

1. `report_document_versions.kind`: `VARCHAR(20)` → `VARCHAR(40)`
   (`laudo_assinado_externo_recebido` tem 31 caracteres). Alargar não reescreve
   valor algum.
2. `ck_report_document_versions_kind_valido` recriado com o tipo novo.
3. `external_signature_batches` e `external_signed_documents` criadas.
4. Sequência `BAT` preseedada.

**Nenhum `UPDATE`, nenhum `DELETE`** em tabela de laudo. Nenhum PDF já gravado
é tocado.

O `downgrade()` **aborta com erro claro** se existir versão gravada com o tipo
novo: estreitar a coluna truncaria a evidência de que um laudo assinado foi
recebido.

---

## 5. Fluxo da médica

### Central "Assinatura externa"

Seção própria no topo da bancada:

```
Aguardando assinatura qualificada — 3

[ ] TESTE APAGAR Antonio Lopes
    Espirometria • 05/08/2026 • Unidade
    ESP-918018 · LAU-000021
```

Vazia: **"Nenhum laudo aguardando assinatura."**

A lista vem de `GET /laudos/assinatura-externa/pendentes`, que aplica o
recorte **no servidor**: laudo concluído, atribuído a ela, cadastro não
arquivado, ainda sem assinado recebido. O navegador nunca recalcula esse
recorte — a lista que ele manda só pode **estreitar** a seleção.

### Download — 1 vira PDF, 2+ viram ZIP

`POST /laudos/assinatura-externa/baixar`

| Seleção | Resposta |
| --- | --- |
| 1 documento | `application/pdf` — `<NOME DA PACIENTE> - Para assinatura.pdf` |
| 2 ou mais | `application/zip` — `SoproLife - Laudos para assinatura - 2026-08-10.zip` |

Obrigar um ZIP de um arquivo só criaria um passo de extração no iPhone sem
nenhum ganho.

**Dentro do ZIP: estrutura plana.** Sem subpastas, sem manifesto, sem
instruções, sem MIR, sem prévia, sem versão antiga. Cada arquivo a mais é uma
chance de assinar o documento errado.

```
TESTE APAGAR Antonio Lopes - Para assinatura.pdf
TESTE APAGAR Maria de Souza - Para assinatura.pdf
```

Acentos preservados (`Conceição Ramalhão Júnior - Para assinatura.pdf`).
Homônimos recebem sufixo com o código do laudo em vez de se sobrescreverem.

---

## 6. Identificação robusta do PDF que volta

### O carimbo (§8)

O laudo concluído sai **carimbado** com chaves privadas no *Document
Information Dictionary*:

```
/SoproLifeReportCode      LAU-000021
/SoproLifeValidationCode  ABCDEFGHJKMN
/SoproLifeVersion         3
/SoproLifeSourceHash      <sha256 do conteúdo antes do carimbo>
```

Nenhum dado clínico ou de paciente entra aí — tudo isso já está impresso no
papel.

Comprovado por teste que o carimbo **sobrevive** tanto a um *incremental
update* quanto à criação de um campo de assinatura PAdES real, e que
**não altera uma única letra do texto impresso**.

O carimbo é **relido** antes de o PDF ser gravado. Sem essa prova, um escape
mal feito só apareceria semanas depois, quando já não há como recarimbar um
documento assinado.

### A ordem do pareamento

1. metadado `/SoproLifeReportCode` ou `/SoproLifeValidationCode`;
2. código `LAU-xxxxxx` extraído do **conteúdo** do PDF;
3. código de verificação extraído do conteúdo;
4. **o nome do arquivo nunca entra.**

A marca legada da M25.8 em `/Keywords` continua sendo **lida**, então um laudo
preparado por aquele fluxo ainda é reconhecido.

### Compatibilidade retroativa (§9)

Laudos concluídos **antes** da M25.20 não têm carimbo — mas sempre tiveram o
LAU e o código de verificação impressos. Os caminhos 2 e 3 cobrem exatamente
esse caso, com teste dedicado.

Sem identificação segura: **"Não foi possível identificar com segurança a qual
laudo este arquivo pertence."** Nada é gravado.

### O teste central da missão

Um PDF **sem nenhum código**, cujo nome de arquivo é **exatamente**
`TESTE APAGAR Antonio Lopes - Para assinatura.pdf` — o nome da paciente, no
formato que o próprio sistema gera — resulta em **0 identificados**.

Outro teste manda um arquivo nomeado com a paciente A carregando o código da
paciente B: o pareamento segue o **código**, não o nome.

---

## 7. Upload em lote e proteções

`POST /laudos/assinatura-externa/enviar` — aceita vários PDFs **ou** um ZIP.

| Proteção | Como |
| --- | --- |
| **Zip slip** | o membro é reduzido ao nome final; `../../../../etc/cron.d/x.pdf` vira `x.pdf` |
| **ZIP aninhado** | recusado por assinatura de bytes (`PK\x03\x04`) |
| **Zip bomb** | tamanho declarado conferido **antes** de ler; razão de compressão > 200:1 recusada; leitura de 1 byte a mais que o limite pega cabeçalho mentiroso |
| **Limite de arquivos** | 200 por lote |
| **Limite por PDF** | 25 MB |
| **Limite descompactado** | 150 MB no lote inteiro |
| **Diretórios** | ignorados |
| **Não-PDF** | recusado por conteúdo, não por extensão |
| **Protegido por senha** | recusado |

Uma recusa **nunca** impede os outros arquivos de seguirem.

### O `/AcroForm` — um bloqueio real que precisou de correção

Toda assinatura PAdES é gravada como **campo de formulário**: o PDF ganha um
`/AcroForm` e uma anotação `/Widget`. Ambos estavam na lista de conteúdo ativo
**proibido** de `validate_pdf_bytes`.

Comprovado empiricamente: um PDF com campo de assinatura era recusado com
`pdf_conteudo_ativo_formulario`. **Nenhum PDF assinado poderia entrar** — o
perfil fechado recusava justamente o arquivo que o fluxo existe para receber.
(É mais uma prova de que o `/lote/enviar` da M25.8 nunca rodou com um arquivo
assinado de verdade.)

**Correção:** `validate_pdf_bytes(..., allow_signature_form=True)` libera
`/AcroForm` e `/Widget` — **e nada além disso**. Continuam proibidos `/XFA`,
JavaScript, `/Launch`, arquivo embutido, mídia ativa, `/OpenAction`, `/AA` e
`/SubmitForm`, com teste que prova que JavaScript segue recusado mesmo com a
exceção ligada.

O perfil é **derivado do `kind`** da versão, não passado por parâmetro, para
que gravação e releitura nunca divirjam — um PDF aceito com o campo e relido
sem ele seria declarado corrompido segundos depois de gravado.

---

## 8. Conferência antes de salvar (§11)

O upload **não grava nada em definitivo**. Ele grava o arquivo como
`em_conferencia` e devolve a lista:

```
ARQUIVOS RECEBIDOS — 12

✓ TESTE APAGAR Antonio Lopes
  LAU-000021 · identificado pelo código interno
⚠ documento-final.pdf
  Não foi possível identificar com segurança

11 identificados, 1 com problema

[ Confirmar 11 identificados ]  [ Cancelar ]
```

A médica confirma o lote **uma vez**. Enquanto não confirma, a administração
**não vê** o documento como recebido — ela não deve começar a trabalhar num
arquivo que pode ser descartado na tela seguinte (teste dedicado).

### Idempotência

Reenviar o mesmo arquivo devolve **"Este arquivo assinado já havia sido
recebido"** e **não** cria segunda versão.

O pareamento busca em **todos** os laudos da médica, não só nos pendentes.
Buscar só entre os pendentes fazia um reenvio — o caso real de quem manda o
lote de novo por não ter certeza se funcionou — voltar como *"não
identificado"*, que é a mensagem errada e assustadora. Defeito encontrado e
corrigido durante a implementação.

### Um bug de transação encontrado e corrigido

`report_publication_transaction` faz `db.commit()` no sucesso e
**`db.rollback()` da sessão inteira** na falha. Com o registro do lote apenas
em `flush()`, o **primeiro arquivo que falhasse ao gravar levava a linha do
lote junto** — e o arquivo válido seguinte tentava gravar apontando para um
`batch_id` inexistente, derrubando o lote inteiro com 409.

Corrigido tornando o lote durável antes do primeiro arquivo. O teste
`test_falha_de_gravacao_nao_leva_o_lote_junto` foi verificado **nos dois
estados**: com a correção revertida ele falha com o 409 real; com a correção,
passa. Um teste que passasse nos dois não provaria nada.

---

## 9. Armazenamento e honestidade sobre ICP-Brasil

### Nada é sobrescrito (§13)

O PDF assinado vira **versão nova**. Teste compara os hashes antes e depois:
`original` (MIR) e `laudo_liberado` (laudo concluído) permanecem **idênticos**,
e o total de versões cresce em exatamente 1.

Guardados: PDF privado (0600, fora do webroot), SHA-256, tamanho, timestamp,
`uploader_user_id`, `physician_profile_id`, `report_document_id`, versão de
origem, `source_sha256`, `batch_id`, filename recebido e trilha de auditoria.

### Receber não é validar (§14)

Estado após o upload: **`recebido_validacao_pendente`**.

| | |
| --- | --- |
| `qualified_signature` | **nunca** `true` por upload |
| Status clínico do documento | continua `liberado` — **não** vira `assinado` |
| `signed_at` | continua nulo |
| Resposta da API | `"assinatura_verificada_criptograficamente": false` |

Teste varre o texto das respostas de upload e confirmação: **"icp-brasil" não
aparece em lugar nenhum**.

---

## 10. Fila administrativa e entrega

`GET /laudos/assinatura-externa/fila` — os cinco estados, **derivados** do que
está gravado, nunca marcados à mão:

```
AGUARDANDO LAUDO
AGUARDANDO ASSINATURA
ASSINADO RECEBIDO — VALIDAÇÃO PENDENTE
PRONTO PARA ENTREGA
ENTREGUE
```

A transição para "assinado recebido" é **automática** quando a médica confirma
o lote. Ninguém precisa avisar a administração por WhatsApp (teste comprova a
transição sozinha).

A fila tem **tela**, não só rota: `renderDeliveryQueue()` no workspace
operacional, com filtro por estado, os dois downloads do §17 e as ações de
validação e entrega. A negativa sobre ICP-Brasil fica **visível na linha**:

> *"…identificado pelo código interno — a SoproLife não verificou a
> assinatura criptograficamente."*

Uma linha que dissesse apenas "assinado" convidaria a conclusão errada.

### Validação externa (§16)

`POST /laudos/assinatura-externa/{id}/validacao-externa` — exige confirmação
consciente por texto (*"Confirmo a conferência externa"*), papel admin, e
grava quem validou, quando, por qual método (`validar_iti` ou equivalente) e
uma referência opcional. **Nenhuma senha ou certificado é armazenado.**

Na tela, a frase é **digitada**, não um "ok" de caixa de diálogo, e o aviso é
explícito antes de digitar: *"A SoproLife NÃO valida a cadeia ICP-Brasil — o
que será registrado é a sua conferência externa."* O que está sendo gravado é
o testemunho de uma pessoa identificada; um clique distraído não deve produzir
isso.

A resposta diz honestamente o que **não** foi feito: *"A SoproLife não
realizou validação criptográfica da assinatura."*

### Downloads administrativos (§17)

| Botão | Nome do arquivo |
| --- | --- |
| Baixar laudo assinado | `<NOME DA PACIENTE> - Assinado.pdf` |
| Baixar exame técnico | `<NOME DA PACIENTE> - Exame técnico.pdf` |

Nomes humanos, com a sanitização única da M25.17 (separadores de caminho,
caracteres proibidos no Windows e controles fora; acentos preservados via
`filename*`).

### Entrega (§18)

`POST /laudos/assinatura-externa/{id}/entrega` marca a entrega feita pelos
canais atuais. **Não existe envio automático ao paciente** — nenhum canal
seguro foi definido, e o sistema não finge ter enviado o que não enviou.

---

## 11. Privacidade, retenção e testes

- ZIP **gerado sob demanda, em memória**, nunca em disco, nunca em webroot,
  nunca em Git, `Cache-Control: private, no-store`.
- Auditoria sem nome de paciente e sem nome de arquivo — teste varre o
  `AuditLog` inteiro procurando `"Antonio"` e `".pdf"`.
- `BAT-000001` fica **fora** do dicionário de códigos e do resolvedor: a tela
  fala em médica, data e quantidade (§19). A exclusão é por lista explícita
  (`INTERNAL_CODE_TABLES`), então uma entidade nova que esqueça o rótulo
  continua estourando em vez de sumir calada.
- **Nenhuma expiração automática** foi adicionada a PDF clínico. MIR, laudo
  concluído e assinado recebido são registro. ZIP é transporte.
- Rota pública de validação: teste compara o conjunto de campos **antes e
  depois** de receber um assinado — idêntico, nada novo vaza.

### iPhone (§6)

- Alvos de toque de **44px** mínimo; a linha inteira é o alvo de seleção
  (`<label>` embrulhando o input), não um quadradinho de 13px.
- Botão grande **"Selecionar PDFs assinados"** com
  `type=file multiple accept=".pdf,application/pdf,.zip,application/zip"`.
- **Nenhum caminho depende de drag-and-drop**, clique direito ou desktop.
- Em ≤480px os botões ocupam a linha inteira.

O teste que verifica o input **recorta o bloco exato** da central: sem isso,
um `assert "multiple" in JS` passaria por causa do input da M25.8 e diria
"sim" para uma central que não tem input nenhum. Pelo mesmo motivo, o teste de
CSS aponta para classes que **só** a M25.20 define — `report-signature-panel`
já existia desde a M25.7 e tornava a asserção anterior vazia.

---

## 12. Testes

### Novo — `test_m25_20_central_assinatura_lote.py` (60 casos)

Cobrem, em ordem de gravidade: carimbo e sobrevivência dele; as três vias de
pareamento; **nome de arquivo sozinho nunca identifica**; download 1 → PDF e
2+ → ZIP; ZIP plano, Unicode, homônimos, exatidão da seleção; isolamento entre
médicas; cadastro arquivado fora; ZIP slip, bomb, aninhado, executável,
diretórios, limites; conferência antes de gravar; confirmação única;
idempotência; falha de gravação que não derruba o lote; versões originais
preservadas; SHA-256 e tamanho gravados; PDF com campo de assinatura real
ponta a ponta; JavaScript ainda recusado; **nenhuma declaração de ICP-Brasil**;
fila administrativa com transição automática; validação externa consciente;
entrega em ordem; downloads administrativos nomeados; auditoria sem PII; rota
pública inalterada; e a compatibilidade iPhone/mobile.

### Gate PostgreSQL 16 real — `scripts/test-postgres-efemero.sh`

```
== ciclo de migração no PostgreSQL 16 real ==
alembic upgrade head → check → downgrade base → upgrade head   ✅
== suíte completa ==
1186 passed, 4 failed, 9 skipped   (22min19)
```

As 4 falhas foram investigadas uma a uma:

| Falha | Veredito |
| --- | --- |
| `test_migrations_postgres::test_fk_ciclica_e_preseed_pg` | **minha** — o teste fixava `count == 13` e agora há 14 sequências. Corrigido ancorando em `PREFIXES`, que prova mais (todo prefixo é preseedado) em vez de congelar outro número. **24/24 em PostgreSQL real depois da correção.** |
| `test_finance_duplicate_revenue_postgres` (3 casos) | **pré-existentes.** Reproduzidos com todo o código da M25.20 **removido via `git stash`** — falham igualmente. Erro de coluna no `INSERT` em `people`, sem relação com esta missão. |

### Regressão dirigida — 231 casos, todos verdes

`test_m25_20` + `test_m25_18` + `test_m25_19` + `test_m25_8` + `test_m25_2` +
`test_crm_workspace` → **231 passed**. As bancadas da M25.18 e M25.19, o fluxo
nativo da M25.2 e o lote da M25.8 continuam íntegros.

### Dois testes que passavam por coincidência

`test_central_tem_layout_responsivo_de_iphone` afirmava `"report-signature" in
CSS` — e passava **antes de o CSS existir**, porque `report-signature-panel` já
vinha da M25.7. `test_upload_aceita_multiplos_pdfs_e_zip` afirmava `"multiple"
in JS`, satisfeito pelo input da M25.8. Ambos foram apertados para apontar
para o que só a M25.20 define: classes próprias e o bloco recortado do input
da central.

---

## 13. Backup

Diretório `700` em `/opt/soprolife/backups/m25-20/20260810T165527Z/`, feito
**antes** de qualquer alteração:

| Item | Prova |
| --- | --- |
| `HEAD_ANTERIOR.txt` | `1eb85391e68721c4f9c0767e6049d6a5388e1b18`, `painel-soprolife-v01` |
| `m15.dump` (`pg_dump -Fc`) | 455.533 bytes — **validado** com `pg_restore -l`: 377 entradas de TOC, 53 objetos das tabelas de laudo |
| `m15.env.bak` | copiado com `install -m 600`; **nenhum segredo impresso** — só linhas, permissão e SHA-256 |
| `private-reports.tar.gz` | 1.637.463 bytes, **9 PDFs** clínicos |

---

## 14. Deploy

| Passo | Resultado |
| --- | --- |
| Commit | `05f7cad908e40e79d562e508fa65e2a177efa2eb` |
| `origin/painel-soprolife-v01` antes | `1eb8539` — 0 atrás, 1 à frente. Sem alteração concorrente |
| Push | **fast-forward normal**. Sem force, sem `--force-with-lease`, sem `reset --hard` |
| VPS | `git merge --ff-only` → `1eb8539..05f7cad`, 17 arquivos, `git status` **limpo** |
| Migration | `d1e7b9c34a25` → **`e7c4b03a91df (head)`** |
| Restart | **somente `soprolife-m15-api`** — o painel e o loopback servem asset do disco a cada requisição e não têm código Python alterado |
| `M15_REPORTS_MODE` | `pilot` — **não alterado** |

### Schema conferido no banco real

```
tabelas novas:                2   (external_signature_batches, external_signed_documents)
report_document_versions.kind: VARCHAR(40)
sequência BAT:                1
```

### Verificações pós-deploy

| Verificação | Resultado |
| --- | --- |
| HEAD da VPS | `05f7cad908e40e79d562e508fa65e2a177efa2eb` |
| `git status` da VPS | **limpo** |
| Health | `{"status":"ok","versao":"0.1.0","ambiente":"prod","banco":"ok"}` |
| Banco | **ok** |
| Painel | **HTTP 200** |
| Serviços | `soprolife-m15-api`, `soprolife-painel`, `soprolife-painel-loopback`, `postgresql` → `active` |

---

## 15. Smoke de produção

**Nenhum paciente real teve estado alterado.** O smoke usa endpoints de
leitura, dados fictícios em memória e uma verificação read-only do banco.

### As 9 rotas estão vivas

Lidas do `openapi.json` do serviço em produção — `/pendentes`, `/baixar`,
`/enviar`, `/confirmar`, `/fila`, `/{id}/validacao-externa`, `/{id}/entrega`,
`/{doc}/assinado/conteudo`, `/{doc}/exame-tecnico/conteudo`.

### Autorização fail-closed

| Rota, sem sessão | HTTP |
| --- | --- |
| `GET /assinatura-externa/pendentes` | **401** |
| `GET /assinatura-externa/fila` | **401** |
| `POST /assinatura-externa/baixar` | **401** |
| `GET /laudos/validacao/{codigo}` | **401** (inalterada) |

### Fluxo funcional, executado NO servidor de produção, sem gravar nada

```
1. carimbo e releitura      codigo: LAU-999001 | verificacao: ABCDEFGHJKMN | versao: 2
2. sobrevive a assinatura   LAU-999001
3. retroativo (conteudo)    LAU no conteudo: LAU-999001 | verificacao: ABCDEFGHJKMN
4. ZIP plano                SoproLife - Laudos para assinatura - 2026-08-10.zip
                            - Conceicao Ramalhao Junior - Para assinatura.pdf
                            - Jose Alvares D Avila - Para assinatura.pdf
                            estrutura plana: True
5. protecoes                zip slip vira nome simples: mal.pdf
                            zip aninhado aceito? False
                            zip bomb aceito?    False
                            nao-PDF aceito?     False
```

### Compatibilidade retroativa provada contra dados REAIS

Os **3 laudos concluídos que já existiam** em produção são anteriores ao
carimbo. Leitura read-only dos PDFs efetivamente armazenados:

```
laudos concluidos anteriores a M25.20: 3
  carimbo? False | LAU do conteudo confere? True | codigo de verificacao confere? True
  carimbo? False | LAU do conteudo confere? True | codigo de verificacao confere? True
  carimbo? False | LAU do conteudo confere? True | codigo de verificacao confere? True
```

Sem carimbo — como esperado — e mesmo assim **identificáveis pelos dois
códigos impressos**, nos três casos. É exatamente o §9 funcionando sobre
documentos reais. Nenhum dado de paciente foi impresso: a saída diz só se o
código extraído bate com o do banco.

### Asset realmente servido pelo painel

`renderSignatureCenter`, as quatro rotas da central, `renderDeliveryQueue`,
*"Nenhum laudo aguardando assinatura."*, *"Selecionar PDFs assinados"*,
`data-signature-upload` e a frase *"não verificou a assinatura
criptograficamente"* — todos presentes no `report-workflow.js` baixado do
painel. No CSS: `.report-signature-item`, `.report-signature-upload-label`,
`.report-delivery-row` e `min-height: 44px`. O `index.html` aponta para
`?v=2026081001`.

### Nenhuma regressão na bancada

```
versoes por tipo:    laudo_liberado = 3 | laudo_previa = 3 | original = 4
documentos:          atribuido = 1 | liberado = 3
assinados recebidos: 0
lotes:               0
```

Os 10 arquivos clínicos que existiam continuam existindo, com os mesmos tipos.
Os **3 laudos concluídos** são exatamente o que a médica verá na central como
*"Aguardando assinatura qualificada — 3"* — ela não nasce vazia.

---

## 16. Pendências

1. **`/lote/enviar` da M25.8 continua declarando `qualified_signature: True` e
   `trust_chain: "ICP-Brasil"`.** A rota é hoje **inalcançável com sucesso**:
   ela exige status `assinatura_pendente` (que nenhuma tela produz) e grava com
   `kind=laudo_liberado`, cujo perfil de validação **recusa** o `/AcroForm` de
   qualquer PDF realmente assinado. Ou seja, não consegue produzir o estado
   proibido. Ainda assim, remover as rotas órfãs da M25.8 (`/finalizar-revisao`,
   `/lote/baixar`, `/lote/enviar`, `renderBatchBar`) e o status
   `assinatura_pendente` é uma decisão de escopo próprio — mexe no contrato
   daquela milestone e na suíte `test_m25_8_external_batch.py`. **Não foi feito
   aqui** por não fazer parte da M25.20 e por a missão pedir explicitamente
   para não fazer auditoria geral.

2. **Validação criptográfica real de ICP-Brasil continua não existindo.**
   `_verify_cms` verifica a assinatura contra o certificado embutido, mas não
   constrói cadeia até uma âncora de confiança da ICP-Brasil, não checa
   revogação e não valida a janela temporal. Enquanto isso, o estado honesto é
   `recebido_validacao_pendente` + conferência externa registrada.

3. **Suíte de Marketing (`test_live_multisheet_reader.py`)** dependia de
   `google-api-python-client`, ausente do `requirements.lock`. Instalado no
   venv local para o gate ficar verde; **o `requirements.lock` não foi
   alterado**, então a pendência de empacotamento permanece.

---

## 17. HEAD final

| | |
| --- | --- |
| HEAD inicial | `1eb85391e68721c4f9c0767e6049d6a5388e1b18` |
| Commit do código | `05f7cad908e40e79d562e508fa65e2a177efa2eb` |
| `origin/painel-soprolife-v01` | `05f7cad` |
| HEAD da VPS | `05f7cad` |
| Alembic | `e7c4b03a91df (head)` |
| Health | `{"status":"ok","versao":"0.1.0","ambiente":"prod","banco":"ok"}` |

O HEAD acima é o do **código**. Este relatório entra num commit seguinte, que
não altera uma linha de aplicação — a distinção que a M25.19 pediu que fosse
registrada explicitamente.

---

## Conclusão

**M25.20 — CENTRAL DE ASSINATURA EXTERNA EM LOTE PUBLICADA EM PRODUÇÃO**

A médica continua laudando um a um. Depois de concluir, ela vê numa seção
própria o que está aguardando assinatura, marca o que quiser, baixa um PDF
(ou um ZIP plano com nomes que ela reconhece), assina fora com o certificado
dela, devolve vários PDFs ou um ZIP pelo botão grande, confere a lista e
confirma **uma vez**. A administração recebe a fila sozinha.

O que o sistema **não** afirma continua tão importante quanto o que ele faz:
nenhum caminho declara assinatura ICP-Brasil verificada, porque nenhuma cadeia
é conferida aqui.
