# M25.29H — Aceite automático do PDF assinado, paridade administrativa do sócio e integridade do download

> Missão encerrada. Início: 2026-08-19. Conclusão: 2026-08-20.
> **Implantada em produção.**

## Identificação

| item | valor |
|---|---|
| branch oficial | `painel-soprolife-v01` |
| HEAD de partida | `ec0a9d8` (M25.29G encerrada) |
| **HEAD do CÓDIGO — local = oficial = VPS** | **`e012e24b5d3de2ec53c5cb9edfedb240e76504e6`** |
| commits posteriores da branch | acrescentam **somente este relatório**; nenhum efeito em produção |
| migration aplicada | **`c4a97b1e6d20`** |
| worktree | `/home/adeildo/soprolife-worktrees/claude-m25-29h-aceite-automatico-assinado` |
| branch da missão | `claude-m25-29h-aceite-automatico-assinado` |

---

## 1. O achado que justifica a missão: LAU-000015

A auditoria read-only em produção (`scripts/auditar_m25_29h_integridade_assinados.py`,
commit `82e12a4`, executada com root em 2026-08-19) encontrou o seguinte:

**LAU-000015 estava com `status = validado_externamente` — isto é, marcado como
conferido por uma pessoa identificada — apesar de o arquivo recebido ser byte a
byte idêntico ao PDF final e não conter nenhuma estrutura de assinatura.**

Isso é um **falso positivo da conferência manual**. Alguém clicou em "Registrar
conferência do PDF assinado" para um arquivo que nunca foi assinado.

É a justificativa central para retirar a conferência humana do fluxo: ela não
acrescentava validação criptográfica nenhuma — a SoproLife não tem integração
ICP-Brasil — e, como qualquer etapa de atenção humana repetitiva, produzia erro.
As guardas documentais que a substituem teriam recusado aquele arquivo em
milissegundos, com dois testes objetivos: hash idêntico ao final, e ausência de
`/ByteRange` e `/Sig`.

### Situação completa da fila na auditoria

> Esta tabela é o retrato **de 2026-08-19, antes de qualquer escrita**. O estado
> final, já aplicado em produção, está na seção 8.

| laudo | estado auditado | evidência | passa no aceite novo |
|---|---|---|---|
| LAU-000010 | `recusado` | idêntico ao final, sem estrutura de assinatura | não |
| LAU-000011 | `recusado` | idêntico ao final, sem estrutura de assinatura | não |
| LAU-000012 | `recebido_validacao_pendente` | associação forte, difere do final, tem assinatura, não é prévia | **sim** |
| LAU-000013 | `recebido_validacao_pendente` | associação forte, difere do final, tem assinatura, não é prévia | **sim** |
| LAU-000014 | `recusado` | prévia assinada | não |
| LAU-000015 | `validado_externamente` | **idêntico ao final, sem estrutura de assinatura** | **não** |

Também provado pela auditoria, em produção:

- **isolamento preservado** — nenhuma versão recebida está ligada a um
  `report_document` diferente do seu, e nenhuma versão é compartilhada entre dois
  documentos;
- conta principal (`contato@soprolife.com.br`): papel `admin`;
- Luiz Antonio Faustino Lopes de Oliveira: papel `gestor` — daí o `http_403`;
- Ana: papel `medico`.

---

## 2. O relato do download "de outra paciente"

Tratado como incidente de integridade até a evidência decidir. Duas frentes:

**Nos dados reais** — a auditoria refez, sem HTTP, exatamente a resolução que o
endpoint faz (documento → assinado vigente → versão recebida → bytes em disco) e
comparou hash do banco, hash do registro e hash do blob em disco para cada
documento. Resultado: isolamento preservado, nenhum cruzamento.

**No código** — `GET /laudos/{id}/assinado/conteudo` resolve por
`ExternalSignedDocument.report_document_version_id`, escopado ao `document_id` da
URL (`app/routers/reports.py:4677`). Nunca por `source_version_id`, nunca por
`current_version_id`, nunca por "último assinado global".

**Hipótese mais provável para o relato**, sem contradizer o isolamento: o
documento baixado era um dos que hoje estão recusados — LAU-000010, 011 ou 015 —
cujo conteúdo é o **PDF final sem assinatura**. Um administrador que abrisse
aquele arquivo veria, corretamente, um laudo sem assinatura. Isso não é o
download entregando documento alheio: é o arquivo recebido nunca ter sido
assinado, que é o defeito que esta missão elimina na origem.

Os testes novos fecham a questão por hash, com dois pacientes sintéticos —
descritos na seção 6.

---

## 3. O fluxo novo

```
médica conclui laudo
  → baixa o PDF final
  → assina por fora
  → devolve
  → o sistema associa por evidência objetiva
  → o sistema aplica as guardas documentais
  → aceita e fica PRONTO PARA ENTREGA
```

Sem telefonema, sem mensagem, sem clique administrativo, sem "Registrar
conferência", sem prompt e sem confirmação humana intermediária. O trabalho da
médica termina no envio.

### O estado novo

`recebido_assinado` (`ASSINADO_ACEITO` em `app/models.py`).

Semântica exata: *o PDF assinado foi recebido, associado ao laudo final e passou
pelas guardas documentais da SoproLife.* Não diz validado, não diz conferido, não
diz ICP-Brasil.

Ele mapeia para o estado de fila **`pronto_para_entrega`**, que já existia.

`recebido_validacao_pendente` **não nasce mais de nada**. Permanece no domínio
porque documentos históricos estão nele e a auditoria precisa lê-los; o rótulo na
fila virou **"Exceção técnica — documento sem aceite"**, e o filtro só aparece na
tela se houver algum documento ali.

### As guardas documentais

`app/services/signature_acceptance.py` — funções puras, sem banco e sem HTTP, usadas
pelo mesmo código no upload, na manutenção e na auditoria.

Para aceitar automaticamente, **todas** precisam valer:

1. existe versão final concluída;
2. a origem é a versão final corrente;
3. não é prévia (`looks_like_preview`);
4. tem estrutura de assinatura (`/ByteRange` **e** `/Sig`);
5. não é idêntico ao PDF final;
6. **associação forte**.

Associação forte é qualquer uma destas três, e **nunca** o código LAU sozinho:

- **`contem_o_final`** — o PDF final é prefixo byte a byte do arquivo devolvido.
  Assinar anexa; é a prova documental mais forte que existe sem criptografia, e
  não depende de metadado sobreviver;
- **`metadado_coerente`** — o carimbo bate com a versão final em código, número de
  versão, hash de origem e estado `concluido`;
- **`codigo_validacao_coerente`** — o código de verificação da versão final está no
  arquivo. A prévia imprime "—" no lugar dele, então não pode vir de uma.

`codigo_laudo_no_conteudo` sozinho **não aceita nada**. Foi exatamente esse
fallback fraco que permitiu a prévia assinada do LAU-000014: prévia e final
carregam o mesmo código LAU impresso.

### O que acontece com o que não passa

O arquivo **não vira linha no banco**. Ele é recusado no recebimento, com o motivo
objetivo gravado na auditoria e uma frase acionável devolvida à médica na hora:

| motivo | frase que a médica lê |
|---|---|
| `documento_e_previa` | "Este arquivo corresponde a uma prévia. Baixe o PDF final e assine novamente." |
| `documento_sem_assinatura_externa` | "Este arquivo é igual ao PDF final sem assinatura. Assine o PDF final e envie novamente." |
| `documento_identico_ao_final` | idem |
| `origem_nao_e_a_versao_final` | "Este arquivo não corresponde à versão final atual do laudo…" |
| `associacao_com_o_laudo_insuficiente` | "Não foi possível confirmar que este arquivo é o PDF final deste laudo…" |
| `laudo_sem_versao_final` | "Este laudo ainda não tem uma versão final concluída…" |

Não existe estado ambíguo de saída: ou todas as guardas passam, ou um motivo
específico dispara. Nada fica em conferência eterna.

O veredito também informa **como o laudo foi reconhecido** mesmo quando o arquivo
é recusado — para a médica não ler "recusado" como "não achei o laudo" e reenviar
o mesmo arquivo esperando outro resultado.

### `qualified_signature` continua falso

Em toda a trilha, em toda resposta de API e em todo registro de auditoria. A
SoproLife não verifica cadeia, certificado, revogação nem identidade do
assinante — e o código diz isso onde alguém possa concluir o contrário.

---

## 4. A conferência externa legada não pode mais errar

A rota `POST /laudos/assinatura-externa/{id}/validacao-externa` continua existindo
para documentos históricos, mas **saiu da tela** e ganhou uma trava: antes de
aceitar o testemunho de quem quer que seja, ela reaplica as guardas documentais.
Um arquivo idêntico ao final e sem estrutura de assinatura — o caso LAU-000015 —
agora recebe `409 documento_assinado_nao_passa_nas_guardas`.

O contrato da rota **não foi afrouxado** ao remover o botão: ela continua exigindo
a frase de confirmação consciente. Remover uma etapa não podia virar porta aberta.

---

## 5. Uma `CHECK` que impedia a entrega

Achado durante a implementação, não previsto no escopo:

`status_validado_exige_validador` exigia `validated_by_user_id IS NOT NULL` para
`entregue` **e** para `validado_externamente`. Fazia sentido quando o único caminho
até a entrega passava por conferência humana. Com o aceite automático, o caminho
normal não tem validador nenhum — e a constraint impediria de entregar exatamente
os documentos que o sistema aprovou sozinho.

A migration restringe a exigência a `validado_externamente`, que é onde ela sempre
significou algo. Foi um erro real, pego pelos testes antes de chegar à operação.

---

## 6. Testes focados

Arquivo novo: `tests/test_m25_29h_aceite_automatico.py` — **26 testes, todos verdes**.

| item | teste |
|---|---|
| 1 | carimbo forte → pronto para entrega automático |
| 2 | código de verificação da final → pronto para entrega automático |
| 3 | prévia assinada → recusada, sem virar linha |
| 4 | PDF final devolvido sem assinar → recusado |
| 5 | só `codigo_laudo_no_conteudo` → não autoaceita |
| 5b | cada modo de falha dispara o motivo certo, na ordem certa |
| 6 | arquivo de outra médica → não identificado |
| 7 | dois pacientes vivos, e o arquivo cai no laudo certo |
| 8 | download do assinado devolve o **hash exato** do recebido |
| 9 | dois pacientes sintéticos nunca cruzam PDFs, nos dois sentidos |
| 9b | a rota lê `report_document_version_id`, não `source_version_id` |
| 10 | `qualified_signature` continua falso, na API e na fila |
| 11 | o aceite não usa `validado_externamente` nem grava validador |
| 12 | não existe "Registrar conferência" no fluxo novo |
| 13 | a médica encerra o trabalho no envio |
| 14 | o admin vê pronto para entrega e consegue entregar |
| 15 | promoção iguala a autoridade administrativa |
| 16 | promovido **não** recebe autoria médica |
| 17 | conta principal continua admin |
| 18 | médica continua sem funções administrativas |
| 19 | reconciliação: dry-run, promoção, idempotência, entregue intocado |
| 15b | promoção por papel para diante de ambiguidade, e não toca na conta principal |

Suítes anteriores atualizadas — sem afrouxar proteção, invertendo o que
descrevia a etapa removida: M25.20, M25.21, M25.29D, M25.29E, M25.29G.

Escopo executado: **17 arquivos de teste impactados, 427 testes, todos verdes**
(`test_m24a`, `test_m24b`, `test_m24d` ×2, `test_m25_15`, `test_m25_18`,
`test_m25_19`, `test_m25_20`, `test_m25_21` ×2, `test_m25_24`, `test_m25_29d`,
`test_m25_29e`, `test_m25_29f`, `test_m25_29g`, `test_m25_29h`,
`test_migrations`). Suíte completa não foi executada, por instrução — a operação
está usando o sistema.

Uma correção de fixture com consequência real: `_assinar_por_fora` anexava apenas
um comentário, sem `/ByteRange` nem `/Sig`. Os testes passavam por um caminho que
a produção não tem. Agora a assinatura sintética carrega um dicionário de
assinatura de verdade.

---

## 7. Manutenções versionadas e idempotentes

Nenhum SQL solto. Todas com dry-run por padrão e `--apply` explícito.

| script | função |
|---|---|
| `scripts/auditar_m25_29h_integridade_assinados.py` | auditoria read-only: cadeia completa, isolamento, hashes, papéis |
| `scripts/reconciliar_fila_assinada.py` | reaplica as guardas à fila histórica: promove o que passa, recusa o que não passa |
| `scripts/promover_admin_soprolife.py` | iguala o conjunto administrativo de uma conta ao da conta principal |

`reconciliar_fila_assinada.py` é a manutenção nova pedida para o falso positivo: o
script da M25.29G se recusa — corretamente — a tocar em documento já conferido,
porque não sabia reavaliar evidência. Este sabe. Ao recusar um
`validado_externamente`, grava um evento **próprio**,
`conferencia_externa_invalidada_por_evidencia`, dizendo que a conferência anterior
foi invalidada e por qual evidência documental. A auditoria anterior continua
gravada, com quem a fez e quando. Nada é apagado.

`promover_admin_soprolife.py` nunca concede papel médico, não cria
`physician_profile`, não reseta senha, não altera e-mail, não revoga sessão e não
imprime senha, hash, token, cookie ou segredo. `admin` não implica `medico` na
hierarquia (`app/security.py:60`), então a fronteira clínica é estrutural, não uma
promessa do script.

---

## 8. Deploy em produção — executado

| verificação | resultado |
|---|---|
| HEAD VPS = oficial = local (código) | `e012e24b5d3de2ec53c5cb9edfedb240e76504e6` |
| migration | `c4a97b1e6d20` aplicada |
| backup | preservado e válido, anterior a qualquer escrita |
| serviços | `soprolife-m15-api`, `soprolife-painel`, `soprolife-painel-loopback` ativos |
| health | HTTP 200 |
| timer | `success` |
| árvore Git da VPS | limpa |
| cache busting | `?v=2026082001` no JS e no CSS |
| reconciliação final | idempotente: 6 intocados, 0 novas alterações |

### Estado final da fila

| laudo | estado final | o que falta |
|---|---|---|
| LAU-000010 | `recusado` | nova assinatura do PDF final |
| LAU-000011 | `recusado` | nova assinatura do PDF final |
| **LAU-000012** | **`recebido_assinado`** | **nada — pronto para entrega** |
| **LAU-000013** | **`recebido_assinado`** | **nada — pronto para entrega** |
| LAU-000014 | `recusado` | nova assinatura do PDF final |
| **LAU-000015** | **`recusado`** | nova assinatura do PDF final |

O LAU-000015 saiu de `validado_externamente` — onde estava por engano humano —
para `recusado`, com motivo `documento_sem_assinatura_externa`. O registro da
conferência anterior **não foi apagado**: quem a fez e quando continuam gravados,
e um evento próprio, `conferencia_externa_invalidada_por_evidencia`, diz que ela
foi invalidada e por qual evidência documental.

### Contas

| conta | papel final | autoria clínica |
|---|---|---|
| `contato@soprolife.com.br` | `admin` | não |
| Luiz Antonio Faustino Lopes de Oliveira | **`admin`** | **não — sem papel `medico`, sem `physician_profile`** |
| Ana | `medico` | sim |

A mensagem "Fila médica não aparece nesta conta" continua correta para o Luiz, e
é o comportamento desejado.

### O que NÃO foi alterado

Nenhuma conclusão clínica. Nenhum paciente, exame, MIR ou versão de laudo.
Nenhum blob, hash ou histórico. Nenhuma linha de auditoria apagada ou reescrita.
Nada em Financeiro, Pastore ou CRM. Nenhum lote antigo. Nenhuma senha resetada,
nenhum e-mail alterado, nenhuma sessão revogada. **Nenhum `DELETE`, em nenhuma
tabela.** Sem `reset --hard`, sem force push, sem `force-with-lease`.

---

## 9. As seis provas pedidas

1. **Ana** — assina → devolve → **acabou para ela**. O envio aplica as guardas e
   aceita na hora; não há lote a confirmar nem ninguém a avisar.
2. **Administração** — **não existe mais conferência manual obrigatória**. O
   botão, a confirmação e a função que a registrava foram removidos da tela.
3. **Documento** — associação automática **forte**, por evidência documental
   objetiva, e **NÃO validação criptográfica ICP-Brasil**. `qualified_signature`
   continua falso em toda a trilha, na API e na interface.
4. **Luiz** — tem a **mesma autonomia administrativa da conta principal**: papel
   `admin`, que engloba `gestor`, `operacional` e `leitura` pela hierarquia.
5. **Separação** — **Luiz NÃO recebeu papel médico.** `admin` não implica
   `medico` em `app/security.py`, o script nunca concede papel clínico e nunca
   cria perfil profissional. A fronteira é estrutural, não uma promessa.
6. **Download** — provado por **hash**: dois pacientes sintéticos assinam
   documentos distintos, e o download de cada laudo devolve exatamente o SHA-256
   do arquivo recebido daquele laudo — nunca o do outro, nunca o
   `source_version_id` (o PDF final sem assinatura). Em produção, a auditoria
   read-only confirmou isolamento preservado, sem nenhuma versão ligada a outro
   `report_document`.

---

## 10. Limitações declaradas

- A SoproLife **continua sem validar criptograficamente** a cadeia ICP-Brasil:
  não confere certificado, revogação, integridade do digest nem identidade do
  assinante. Tudo o que o aceite afirma é documental. Um validador criptográfico
  real, integrado, continua sendo trabalho futuro — e só ele permitiria
  `qualified_signature = true`.
- `tem_estrutura_de_assinatura` detecta a estrutura (`/ByteRange` e `/Sig`), não
  a validade dela. Um PDF com estrutura de assinatura forjada e que descendesse
  do final passaria — cenário que exige acesso ao PDF final da paciente e
  intenção deliberada, e que só um validador criptográfico distingue.
- Três laudos — 010, 011 e 014 — mais o 015 dependem de a médica assinar o PDF
  final de novo. Nenhum deles precisa de laudo novo ou de conclusão clínica nova.
- A suíte completa não foi executada nesta missão, por instrução. O escopo
  verificado foram os 17 arquivos impactados.
- **Resíduo de permissão no checkout da VPS.** Durante a missão, uma execução com
  root deixou `.git/index` e `.git/ORIG_HEAD` pertencendo a `root`. O `.git/` é do
  usuário `soprolife`, então a árvore continua íntegra e o código implantado está
  correto — mas o próximo `git merge --ff-only` falha ao gravar `ORIG_HEAD`. É o
  mesmo resíduo que quebrou um deploy no meio na M25.29D, e desta vez ele apareceu
  DEPOIS do código já estar implantado e verificado. Conserto: devolver os dois
  arquivos ao usuário `soprolife`. Enquanto isso, o único commit que não alcança a
  VPS é o deste relatório.

---

**M25.29H — PRODUÇÃO IMPLANTADA E FLUXO DE ASSINATURA AUTOMÁTICA OPERACIONAL**
