# M25.15 — Laudos prontos para operação real

**Data:** 09/08/2026
**Branch oficial:** `painel-soprolife-v01`
**VPS:** `root@soprolife-painel-01` · `/opt/soprolife/soprolife-site`

---

## 1. Estado inicial e commits

| Item | Valor |
| --- | --- |
| HEAD local inicial | `a5b758339ed0c4b61a27cac3ce2b31df28c09a07` |
| `origin/painel-soprolife-v01` inicial | `a5b7583` (idêntico — sem reconciliação necessária) |
| HEAD da VPS antes do deploy | `18e2396` (um commit de documentação atrás) |
| HEAD final (local, origin e VPS) | `01ce87e7b3e0d64cd1688a4203ec2bc9e7813191` |

Commits desta missão, todos integrados por **fast-forward** (`a5b7583..01ce87e`):

| Commit | O que resolve |
| --- | --- |
| `19c320e` | A operação reconhecia códigos, não pessoas — nome primário, busca por nome, seletor 0/1/N, CRM canônico, semântica da assinatura, gate CFM |
| `365d501` | O nome quebrava letra a letra no acompanhamento operacional (grid de 3 colunas herdado do layout antigo) |
| `01ce87e` | A ajuda do localizador ainda mandava buscar só pelo código ESP |

Nenhum `reset --hard`, `force push`, `force-with-lease` ou remoção de worktree.

---

## 2. Mudanças de UX — onde o nome passou a aparecer

A regra: **em interfaces autenticadas, a referência humana principal é o nome
do paciente; os códigos continuam sempre visíveis, como metadado de
rastreabilidade.** A composição adotada em todas as telas:

```
NOME DO PACIENTE                      ← forte, navy, 14px
Espirometria • data • unidade         ← contexto, discreto
[chip de status]
ESP-000123 · LAU-000045               ← monoespaçado, 11px, esmaecido
```

| Superfície | Antes | Depois |
| --- | --- | --- |
| Localizador de exame | Só campo de código exato | Nome, ESP ou LAU; resultados nome-primeiro |
| Resultado “exame localizado” | `ESP-000016` + data | Nome + contexto + códigos |
| Formulário de upload/atribuição | Códigos | Nome do paciente confirmado antes de anexar |
| Acompanhamento operacional | `LAU-…` / `ESP-…` / status | Nome + contexto + status + códigos |
| “Meus laudos” (fila da médica) | `LAU-…` / `ESP-…` + origem | Nome + contexto + status + códigos |
| Cabeçalho da bancada clínica | “Documento atribuído” sob 2 códigos | Nome como título, contexto acima, códigos abaixo |
| Documentos / downloads | Só `LAU-…` | Nome + `ESP · LAU` acima dos dois PDFs |

`PES-…` nunca é informação principal: viaja no payload como
`patient.public_code` e aparece apenas no cartão de detalhe (“Registro”).

### Política de privacidade aplicada

O que mudou foi **onde o nome aparece**, não **quem pode vê-lo**:

* fila operacional — já exigia `require_role(ROLE_OPERACIONAL)`;
* fila da médica — além do papel, filtra por atribuição ativa dela;
* localizador `/laudos/exames` — `ROLE_OPERACIONAL` (papel `leitura` recebe 403);
* documentos/downloads — médico **atribuído** àquele documento.

Garantias preservadas e testadas:

* **rota pública de validação (`/laudos/validacao/{codigo}`) continua sem
  qualquer dado de paciente** — nem nome, nem `PES-…`;
* **auditoria continua sem nome** — `laudo_original_atribuido` grava
  `report_code`, `exam_code`, `physician_profile_id`, `assignment_id`;
* **id interno da pessoa continua fora das filas**;
* bloco de identidade é **fechado**: só `full_name` e `public_code`. Nascimento,
  contato e qualquer dado clínico continuam fora da fila.

---

## 3. Pesquisa humana

Novo endpoint `GET /laudos/exames` (`ROLE_OPERACIONAL`), que aceita as três
formas como uma pessoa se refere a um exame:

| Termo | Comportamento |
| --- | --- |
| `ESP-000016` | Código exato do exame |
| `LAU-000001` | Código do laudo (antes era beco sem saída: “formato não reconhecido”) |
| `Maria da Silva` | Nome normalizado, mínimo 3 letras; abaixo disso → 422 `termo_de_busca_curto` |

**Homônimos:** com mais de um resultado, nada é escolhido automaticamente. Os
candidatos são listados com nome, **data**, **unidade** e **ESP** — suficiente
para diferenciar sem decorar código, e sem expor contato ou nascimento.

Busca por código **não foi removida**; ganhou companhia.

---

## 4. Seletor dinâmico de médicos

A lista vem de `GET /laudos/medicos-disponiveis` — **não há nome de médico
escrito no código da tela** (verificado por teste: `"Ana Cristina" not in
WORKFLOW_JS`).

### Critérios de elegibilidade (todos obrigatórios)

1. perfil médico `active = true`;
2. conta de usuário `ativo = true`;
3. papel médico **explícito** (nunca herdado de admin);
4. `verification_status = "verified"`;
5. evidência de verificação completa (`verified_at`, `verified_by_user_id`,
   `verification_reference`) — garantida por CHECK no banco.

### Comportamento por quantidade

| Elegíveis | Comportamento |
| --- | --- |
| **0** | **Fail closed.** O formulário mostra o motivo e o botão “Enviar e atribuir” fica desabilitado. O servidor recusa de qualquer forma (`medico_nao_elegivel`, 409) — a trava da tela existe para não gastar o trabalho do operador. |
| **1** | Pré-selecionado, **mas o seletor continua visível**, com a nota “Único médico elegível hoje — este laudo será atribuído a ele”. |
| **2+** | Nenhum vem marcado; a opção “Selecione o médico responsável” força escolha explícita. Nunca escolhe o primeiro em silêncio. |

**Hoje, em produção, aparece exatamente uma opção:**

```
Dra. Ana Cristina do Nascimento Cunha • Médica Pneumologista • CRM-RJ 52.62307-5 • RQE 58224
```

A atribuição continua auditada em `laudo_original_atribuido`.

---

## 5. CRM — correção somente de apresentação

Novo módulo `app/services/crm_display.py`.

| Campo | Antes | Depois |
| --- | --- | --- |
| `crm_number` (persistido) | `52623075` | `52623075` — **inalterado** |
| `crm_display` (persistido) | `5262307-5` | `5262307-5` — **inalterado** |
| `verification_status` | `verified` | `verified` — **inalterado** |
| `verification_reference` | — | **intacta** |
| `crm_formatted` (**novo**, calculado) | — | `52.62307-5` |
| `crm_full` (**novo**, calculado) | — | `CRM-RJ 52.62307-5` |

Aplicado na interface, no PDF do laudo, nos rodapés (teste e piloto) e na rota
de validação — a mesma função nos quatro lugares, para que conferir o papel
contra a tela nunca falhe por divergência cosmética.

**Máscara só onde foi conferida.** A única UF com máscara é RJ, e só para
exatamente 8 dígitos. Provado em teste que os demais casos saem sem alteração:

| Entrada | UF | Saída |
| --- | --- | --- |
| `52623075` | RJ | `52.62307-5` |
| `52623075` | SP | `52623075` |
| `123456` | RJ | `123456` |
| `526230751` | RJ | `526230751` |
| `1234567` | MG | `1234567` |

---

## 6. Prontidão do PDF — CFM 2.381/2024

Novo módulo `app/services/report_compliance.py` + endpoint **admin-only**
`GET /laudos/{id}/conformidade-cfm`, que confere o conteúdo **que de fato vai
para o PDF** e **calcula** o veredito (não existe caminho para declará-lo).

Resultado real, medido em produção sobre `LAU-000001`:

| Requisito | Estado |
| --- | --- |
| Identificação do médico responsável | ✅ |
| Número de inscrição no CRM e UF | ✅ `CRM-RJ 52.62307-5` |
| RQE (quando há especialidade) | ✅ `RQE 58224` |
| Identificação do paciente | ✅ nome, nascimento, sexo, registro |
| **CPF do paciente, quando houver** | ❌ **pendência declarada** |
| Data de emissão | ✅ |
| Data de realização do exame | ✅ |
| Endereço profissional | ✅ `Rua Teixeira de Melo, 54 — Ipanema, Zona Sul — RJ` |
| Contato profissional | ✅ `Central: (21) 2508-9001` |
| **Assinatura qualificada ICP-Brasil** | ❌ **pendência declarada** |

**Nada foi adicionado ao PDF nesta missão porque nada obtenível estava
faltando:** todos os campos que o sistema sabe guardar já eram impressos. Um
teste agora trava isso contra regressão futura.

**Sobre o CPF — a pendência é de esquema, não de preenchimento.** O cadastro de
pessoas (`people`) **não possui coluna de CPF**: nem modelo, nem migration, nem
campo de formulário. Não é um dado em branco num registro; é um dado que o
sistema ainda não sabe guardar. Conforme a instrução da missão, isso foi
**registrado como pendência** em vez de fabricado, e o requisito é marcado como
não atendido de propósito — dar por satisfeito “porque não há CPF cadastrado”
transformaria uma limitação em conformidade aparente.

Quando a unidade não tem endereço ou contato cadastrado, o requisito também vira
pendência — nunca um endereço institucional genérico no lugar.

O laudo da SoproLife continua sendo **documento separado** do PDF técnico da MIR.

---

## 7. Assinatura — o que existe e o que não existe

Não há provedor de assinatura qualificada integrado neste ambiente
(`integraicp_ready() == false`). O que existe é **liberação institucional
autenticada**, e a UX passou a dizer isso sem eufemismo.

| Superfície | Antes | Depois |
| --- | --- | --- |
| Rótulo de status na tela | “Liberado (assinatura eletrônica interna)” | **“Liberado — aguardando assinatura qualificada”** |
| Selo do PDF | `ASSINADO ELETRONICAMENTE / LIBERAÇÃO INSTITUCIONAL` | inalterado (já era honesto) |
| Declaração do PDF | “Esta liberação não constitui, por si só, assinatura digital qualificada ICP-Brasil.” | inalterada |
| Rota de validação | `qualified_signature: false` | inalterada |
| Painel de assinatura | “Assinatura digital qualificada — provedor não configurado” | inalterado |

O rótulo antigo sugeria um tipo de assinatura que este documento não tem. O PDF
já era honesto; **a tela é que estava sendo mais otimista que o papel**.

O fluxo intermediário seguro pedido pela missão **já existia e foi preservado**:
a médica conclui, o sistema congela o conteúdo, gera o PDF, mantém versão +
SHA-256 + código de verificação, e os dois documentos ficam disponíveis para
download separado. O selo do PDF passa a declarar ICP-Brasil **sozinho** no dia
em que houver evidência criptográfica gravada — `_seal_signature_kind` lê a
evidência real, nunca a intenção. Nenhum “checkbox” foi aceito como prova.

---

## 8. QR / validação pública

`M15_REPORTS_VALIDATION_BASE_URL` **continua não configurada** em produção
(conferido no `m15.env`). Consequências, todas já implementadas de fábrica:

* nenhum QR é desenhado no laudo — sem URL, `validation_url` é `None`;
* o **código de verificação interno continua existindo** e impresso
  (`Y6ZVEF9XZY7Z` no cenário fictício);
* a rota `/laudos/validacao/{codigo}` **exige sessão autenticada** — não é
  verificação pública anônima.

Nenhum domínio foi inventado e nenhuma URL Tailscale foi usada como validação
pública. **Pendência separada:** não existe infraestrutura pública HTTPS
preparada no projeto para validação por paciente; habilitá-la exige decisão de
privacidade própria (o que a página mostraria a um visitante anônimo).

---

## 9. Testes

**Suíte completa: 1022 passaram, 30 puladas.** Quality gate: **todos os checks OK**.

Novo arquivo `tests/test_m25_15_operacao_real.py` — **40 testes**, cobrindo item
a item a seção 12 da missão:

| Exigência da missão | Teste |
| --- | --- |
| nome na fila médica | `test_fila_da_medica_identifica_pelo_nome_e_preserva_os_codigos` |
| nome no acompanhamento administrativo | `test_acompanhamento_operacional_identifica_pelo_nome` |
| códigos preservados como secundários | idem + `test_frontend_preserva_os_codigos_como_metadado` |
| isolamento médico preservado | `test_isolamento_medico_continua_valendo_com_nome_na_fila` |
| seletor dinâmico | `test_frontend_busca_medicos_no_backend_e_nao_em_lista_fixa` |
| inativo não aparece | `test_medico_nao_elegivel_nao_aparece[perfil-inativo]` / `[conta-inativa]` |
| pending não aparece | `test_medico_nao_elegivel_nao_aparece[verificacao-pendente]` |
| verified+active aparece | `test_medico_verificado_e_ativo_aparece` |
| 2 médicos aparecem | `test_dois_medicos_validos_aparecem_os_dois` |
| CRM `52623075 → 52.62307-5` | `test_crm_rj_da_dra_ana_e_desenhado_com_ponto_e_hifen` |
| outras UFs não mascaradas | `test_outros_crm_nao_sao_mascarados_por_analogia` (4 casos) |
| PDF mantém campos obrigatórios | `test_pdf_reune_os_campos_obrigatorios_que_o_sistema_possui` |
| nenhum nome vazando para rota pública | `test_rota_publica_de_validacao_nunca_devolve_nome_de_paciente` |
| upload continua funcionando | `test_upload_continua_funcionando_e_cria_atribuicao` |
| atribuição continua auditada | `test_auditoria_da_atribuicao_nao_grava_nome_de_paciente` |
| M25.14 continua funcionando | `test_m25_14_liberacao_postgres.py` (8/8 com PG real) |
| downloads separados | `test_documentos_para_download_dizem_de_quem_sao` |

Extras: fail closed sem médico elegível, busca por nome/ESP/LAU, homônimos,
termo curto, `leitura` recebe 403 no localizador, veredito CFM calculado, gate
admin-only, contrato do frontend (arquivo estático, sem build).

### Testes atualizados por mudança de política

Dois testes e duas asserções de script codificavam a política **anterior**
(“nenhum traço do paciente na fila”), que a M25.15 inverteu deliberadamente para
interfaces autenticadas. Foram reescritos preservando o que continuava valendo —
id interno fora, texto clínico fora, rota pública limpa:

* `test_m24c_medical_workflow.py::test_upload_cria_uma_atribuicao_e_fila_com_nome_sem_id_interno`
* `test_m24a_frontend_contract.py::test_filas_e_detalhe_separam_metadado_operacional_de_identidade`
* `scripts/test-m24a-report-workflow.js` — 2 checks

### PostgreSQL

Rodado contra PostgreSQL 16 real (container efêmero, banco por suíte):

| Suíte | Resultado |
| --- | --- |
| `test_m25_14_liberacao_postgres.py` | **8 passaram** — a prova de largura de coluna da M25.14 |
| `test_migrations_postgres.py`, `test_m23_postgres_only.py` | passaram |
| `test_import_batch_status_postgres.py` | 5 passaram |

**Nenhuma migration foi criada nesta missão** — a mudança é de leitura e
apresentação. Alembic head do código (`b8e4d2a71c53`) já era a aplicada no banco
de produção.

---

## 10. Achado pré-existente (fora do escopo, não corrigido)

`tests/test_finance_duplicate_revenue_postgres.py` falha quando executado com
PostgreSQL real: 3 testes, erro `column "sexo" of relation "people" does not
exist`.

**Não tem relação com a M25.15.** O teste faz `command.downgrade(cfg,
"b8c4e6d21a90")` e depois insere um `Person` usando o modelo ORM **atual**. A
coluna `sexo` entrou em `a3f1d7c25e90` (M25.2, **05/08/2026**), enquanto o teste
é de **26/07/2026** — quebrou quando a M25.2 entrou, dez dias antes desta missão.
Passa despercebido porque é pulado sem `M15_TEST_POSTGRES_URL`. Fica registrado
como pendência para uma missão de finanças/migrations.

---

## 11. Backup e deploy

| Item | Valor |
| --- | --- |
| Diretório | `/opt/soprolife/backups/m25-15/20260809T232717Z` |
| HEAD anterior | `HEAD_ANTERIOR.txt` → `18e2396f41a2546e8b80fb40002309af687a7562` |
| Dump PostgreSQL | `m15.dump` — 262 631 bytes, formato custom |
| **Validação do dump** | `pg_restore --list` → **376 entradas**; `people`, `physician_profiles`, `report_documents`, `report_document_versions`, `report_assignments`, `spirometry_exams` presentes |
| Backup do env | `m15.env.bak`, modo `600` — **nenhum segredo impresso** (só contagem de linhas e prefixo do SHA-256) |

**Deploy:** `git fetch` + `git merge --ff-only` na VPS (`18e2396 → 01ce87e`).
Sem migrations (head já aplicada). Restart apenas de
`soprolife-m15-api.service`; os dois commits seguintes eram CSS/JS estáticos e
não exigiram restart.

### Verificação pós-deploy

| Verificação | Resultado |
| --- | --- |
| HEAD da VPS | `01ce87e7b3e0d64cd1688a4203ec2bc9e7813191` |
| `git status` da VPS | limpo (0 alterações locais) |
| Health da API | `{"status":"ok","ambiente":"prod","banco":"ok"}` |
| Banco | **ok** |
| Painel | HTTP 200 |
| Serviços | `soprolife-m15-api`, `soprolife-painel`, `soprolife-painel-loopback` → todos `active` |
| `reports_mode` | **`pilot`** (inalterado) |

---

## 12. Smoke test de produção

Feito **apenas** com o cenário fictício já existente
(`PES-000029` / `ESP-000016` / `LAU-000001`), sem criar paciente novo e **sem
liberar ou alterar nenhum registro**.

| Comprovação | Resultado |
| --- | --- |
| Nome como referência principal | `TESTE M25.13 Paciente Ficticio` no topo de todas as filas |
| Códigos secundários | `ESP-000016 · LAU-000001` na linha inferior, monoespaçado |
| Busca por nome | “Paciente Ficticio” → 1 exame, com data e `Pastore Ipanema` |
| Busca por ESP e por LAU | ambas resolvem para o mesmo paciente |
| Seletor de médico | 1 opção, pré-selecionada, seletor visível |
| Dra. Ana elegível | `verified` · `active` |
| CRM visual | `CRM-RJ 52.62307-5` na tela **e** na rota de validação |
| Fila da médica | 1 item, isolada ao perfil dela |
| Bancada | MIR à esquerda, laudo à direita, conclusões, adendo — **M25.14 intacta** |
| Downloads separados | `original v1 0e3194d96c35…` e `laudo_liberado v3 4c73a63e8c3a…` — ids distintos |
| Rota de validação | **sem `patient`, sem nome, sem `PES-…`**; `qualified_signature: false` |
| Estado do banco após o smoke | 30 pessoas, 17 exames, 2 laudos — **nada criado**; `LAU-000001` segue `liberado` com o mesmo `released_at` de 19:09:52 |

### Screenshots

Capturados em **sessão autenticada real contra o Postgres de produção**, com
Chrome headless + CDP através de encaminhamento SSH de loopback (o painel só
renderiza o campo de token em contexto seguro). Larguras 1440/1000/420 px, sem
overflow horizontal em nenhuma.

| Arquivo | O que comprova |
| --- | --- |
| `laudos-1440/1000/420.png` | Fila da médica nome-primeiro, responsiva |
| `admin-operacional-1440.png` | Localizador + acompanhamento operacional por nome |
| `admin-upload-seletor-1440.png` | Seletor com a Dra. Ana e CRM `52.62307-5` |
| `bancada-medica-1440.png` | Bancada completa da M25.14 preservada |

> Os arquivos ficaram no scratchpad da sessão e **não foram commitados**: as
> telas administrativas mostram nomes de pacientes reais da base de produção, e
> o CLAUDE.md proíbe dado real de paciente no repositório.

**Uma regressão visual real foi encontrada e corrigida por esses screenshots:**
no acompanhamento operacional o nome quebrava **letra a letra, na vertical** —
a linha tinha `grid-template-columns: minmax(0,1fr) auto auto`, feito para os
três campos curtos de antes; com quatro filhos, as duas colunas `auto`
espremiam a primeira. Corrigido em `365d501` e reconferido em produção.

---

## 13. Pendências reais

1. **Assinatura qualificada ICP-Brasil** — sem provedor integrado
   (`integraicp_ready() == false`). É a pendência que mantém `reports_mode=pilot`.
2. **CPF do paciente** — `people` não tem a coluna. Exige migration, campo de
   formulário e decisão de tratamento de PII. Não foi fabricado.
3. **Validação pública / QR** — `M15_REPORTS_VALIDATION_BASE_URL` não
   configurada; não há infraestrutura pública HTTPS preparada. Exige decisão de
   privacidade sobre o que um visitante anônimo veria.
4. **`test_finance_duplicate_revenue_postgres.py`** — quebrado desde a M25.2,
   independente desta missão (seção 10).
5. **Endereço/contato por unidade** — hoje `Pastore Ipanema` está completa. Uma
   unidade sem esses dados vira pendência no gate, não endereço inventado.

---

## 14. Gate de produção oficial

`M15_REPORTS_MODE=pilot` **não foi alterado**, e a mudança seria bloqueada de
qualquer forma: o router recusa `production` com 503
`relatorios_producao_bloqueada`, antes de qualquer autenticação.

O gate objetivo (`/laudos/{id}/conformidade-cfm`, medido em produção sobre
`LAU-000001`) responde:

```
pendencias_bloqueantes: ['cpf_paciente', 'assinatura_qualificada']
reports_mode: pilot
```

Isso **não impede** a Dra. Ana de entrar, ver o exame pelo nome do paciente,
abrir a bancada, gerar prévia, concluir e baixar os dois PDFs. Impede apenas que
um documento sem assinatura qualificada seja apresentado como documento
eletrônico oficial final.

---

## Conclusão

**OPERACIONAL PRONTO — ENTREGA ELETRÔNICA OFICIAL AGUARDA ASSINATURA QUALIFICADA**
