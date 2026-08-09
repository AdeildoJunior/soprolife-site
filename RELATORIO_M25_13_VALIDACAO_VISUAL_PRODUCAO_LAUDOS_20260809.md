# M25.13 — Validação visual real da M25.12 em produção

**Data:** 09/08/2026

**Estado:** `M25.13 — VALIDAÇÃO VISUAL DE PRODUÇÃO PARCIAL: 15 DE 16 ITENS
COMPROVADOS; LIBERAÇÃO BLOQUEADA POR BUG DE PRODUÇÃO`

> **Atualização de 09/08/2026 (M25.14):** o 16º item foi destravado. A causa de
> §13.1 foi corrigida por migration e a liberação de `LAU-000001` foi comprovada
> em produção, sem erro 500. Ver
> `RELATORIO_M25_14_CORRECAO_LIBERACAO_LAUDO_PRODUCAO_20260809.md`.

A validação **não** pode ser declarada concluída. A bancada clínica inteira foi
comprovada no navegador real contra produção — PDF MIR à esquerda, laudo à
direita, 17+1 conclusões, 5 complementos, DVO Leve, RBD+, texto editável, prévia
e downloads separados. Mas a **liberação do laudo falha com erro 500** por um
defeito de schema descrito em §13.1: nenhum laudo pode ser liberado hoje em
produção.

Fluxos administrativo e médico executados; evidências A0 a J3 capturadas.

> Nenhum dado real de paciente foi usado, criado ou capturado. `M15_REPORTS_MODE`
> permanece `pilot` e não foi tocado. Nenhum deploy foi feito.

---

## 1. Preflight

### 1.1 Local

| Item | Valor |
| --- | --- |
| PC | PC de casa (`fedorasurf`) |
| Worktree | `/home/fedorasurf/soprolife-worktrees/claude-m25-13-validacao-producao-laudos` |
| Branch | `claude-m25-13-validacao-producao-laudos` |
| HEAD inicial | `bbd9ebcfd20a8407e789a3a4f4b62b292e67b5f5` |
| `git status` | limpo |

### 1.2 GitHub

| Item | Valor |
| --- | --- |
| `origin/painel-soprolife-v01` | `bbd9ebcfd20a8407e789a3a4f4b62b292e67b5f5` |
| Confere com a base esperada | **sim** |

### 1.3 VPS

Acesso por Tailscale SSH legítimo. O nome MagicDNS não estava no `known_hosts`,
mas o IP do tailnet (`100.87.98.100`) já tinha a chave do host previamente
confiada — a conexão foi feita por esse IP, **sem** desativar verificação de
chave e **sem** aceitar chave nova.

| Item | Valor |
| --- | --- |
| Repositório | `/opt/soprolife/soprolife-site` |
| HEAD da VPS | `bbd9ebcfd20a8407e789a3a4f4b62b292e67b5f5` |
| Branch da VPS | `painel-soprolife-v01` |
| `git status` da VPS | limpo |
| Backup pré-M25.12 | `/opt/soprolife/backups/m25-12/20260809T033330Z` — **existe** |
| `M15_REPORTS_MODE` | `pilot` (não alterado) |
| `soprolife-m15-api.service` | `active` |
| `soprolife-painel.service` | `active` |
| `soprolife-painel-loopback.service` | `active` |
| PostgreSQL | PostgreSQL 16.14 respondendo |

**Health (rota real de produção, `/painel-soprolife/api/m15/health`, HTTP 200):**

```json
{"status":"ok","versao":"0.1.0","ambiente":"prod","banco":"ok"}
```

Observação: `/api/v1/health` na raiz do domínio devolve 404. A API só é exposta
publicamente sob o prefixo `/painel-soprolife/api/m15/`, que é exatamente o
`apiBase` usado pelo front (`js/m15-nucleo.js:34`). Não é defeito.

### 1.4 Versão estática servida em produção

Confirmado no HTML servido pela produção:

- `./js/report-workflow.js?v=2026080901` ✅
- `./css/report-workflow.css?v=2026080901` ✅
- `report-clinical-split` presente no JS efetivamente servido ✅
- novo localizador de exame presente ✅

**Preflight sem divergências.** Prosseguido.

---

## 2. O que existe hoje no banco de produção (somente leitura)

Nenhum `INSERT`, `UPDATE` ou `DELETE` foi executado. Nenhum registro apagado.

### 2.1 LAU-TF0001 — existe, mas está inutilizável

| Campo | Valor |
| --- | --- |
| Tabela | `report_documents` |
| `public_code` | `LAU-TF0001` |
| `id` | `8b59d411-caa7-4f38-a58d-04f45bc212c2` |
| `status` | `atribuido` |
| `signature_status` | nulo |
| `current_version_id` | **nulo** |
| `reviewer_user_id` | **nulo** |
| `origin_type` / `origin_label` | `clinica_parceira` / `teste-apagar` |
| `validation_code` | nulo |
| Criado em | 08/08/2026 10:31 |

É o **único** laudo do banco (`count = 1`).

### 2.2 Exame associado

`ESP-TF0001` **existe** (a missão pedia para não assumir isso — está confirmado):

| Campo | Valor |
| --- | --- |
| `id` | `344638af-615a-47f1-8bfa-dbff72a45602` |
| `person_id` | `69789908-…` → `PES-TF0001` |
| `status` | `Realizado` |
| `broncodilatador` | **true** |
| `data_exame`, `modalidade`, `local_atendimento`, `partner_unit_id` | todos nulos |

Paciente: `PES-TF0001` — **"TESTE APAGAR Paciente Fumaca"**, 100% fictício.

### 2.3 Três motivos que impedem reutilizar esse conjunto

1. **O PDF técnico não existe em disco.** Há uma linha em
   `report_document_versions` (kind `original`, 431 bytes) apontando para
   `laudos/344638af…/8b59d411…/0614b72f….pdf`, mas
   `/opt/soprolife/private/reports/` está **vazio** (`0` PDFs, diretório sem
   subpastas). A versão é órfã: a coluna esquerda da bancada clínica não teria
   o que exibir.
2. **A atribuição não é da Dra. Ana.** A atribuição ativa em
   `report_assignments` aponta para o perfil `45dc66e3-…` =
   **"TESTE APAGAR Medica Fumaca"**, que está `active = false` e
   `verification_status = pending`. O usuário dono desse perfil
   (`teste-apagar-fumaca@soprolife.local`) está **inativo** e **sem papel algum**.
   O laudo jamais apareceria em "Meus laudos" da Dra. Ana.
3. **O código tem letras.** `ESP-TF0001` é recusado por regra de formato — ver §5.

Nada disso foi corrigido, reatribuído ou apagado.

### 2.4 Demais registros fictícios das M25.9/M25.10/M25.11

Não há resíduo além do conjunto `*-TF0001` acima. Pessoas de teste no banco:
apenas `PES-TF0001`. Laudos de teste: apenas `LAU-TF0001`.

### 2.5 Exames de produção — atenção

Existem **16 espirometrias**: `ESP-000001` … `ESP-000015` (reais, importadas)
mais `ESP-TF0001` (fictícia). Os 15 exames numerados **pertencem a pacientes
reais** e, por isso, **não serão usados** nesta missão — laudar qualquer um
deles criaria um documento clínico real, o que a missão proíbe.

Todos os 15 aparecem hoje na lista "espirometrias recentes sem laudo" da tela.
Isso é tratado em §7 (risco de captura).

### 2.6 Configuração de laudo já existente

- 6 templates ativos (`NORMAL_PROVISORIO`, `OBSTRUTIVO_PROVISORIO`,
  `OBSTRUTIVO_BD_PROVISORIO`, `SUGESTIVO_RESTRITIVO_PROVISORIO`,
  `MISTO_PROVISORIO`, `INESPECIFICO_QUALIDADE_PROVISORIO`).
- 2 rodapés ativos, incluindo `PILOTO_INTERNO_NAO_ASSINADO`.
- `physician_signature_assets`: **0 registros** (nenhuma assinatura manuscrita
  cadastrada — coerente com a proibição da missão).
- `qualified_signature_requests`: **0 registros**.
- `audit_logs` para o cenário `*-TF0001`: **0 registros**.

---

## 3. Perfil da Dra. Ana (somente leitura — nada foi corrigido)

| Campo | Valor em produção | Esperado pela missão | Confere? |
| --- | --- | --- | --- |
| `professional_name` | `Dra. Ana Cristina do Nascimento Cunha` | Ana Cristina do Nascimento Cunha | conteúdo confere; há o prefixo `Dra.` gravado no nome |
| Login (`users.email`) | `annapec3@hotmail.com` | idem | ✅ |
| `users.nome` | `Ana Cristina do Nascimento Cunha` | idem | ✅ |
| `crm_number` | `52623075` | — | — |
| `crm_state` | `RJ` | RJ | ✅ |
| **`crm_display`** | **`5262307-5`** | **`52.62307-5`** | ❌ **sem o ponto — suspeita confirmada** |
| `rqe` | `58224` | 58224 | ✅ |
| `active` | `true` | — | ✅ |
| `verification_status` | `verified` | — | ✅ |
| `verification_reference` | `CREMERJ-BUSCA-PUBLICA-20260808-CRM5262307-5` | — | registrado |
| Papel | `medico` | — | ✅ |
| Usuário ativo | `true` | — | ✅ |

**A suspeita do briefing está confirmada:** `crm_display` está gravado como
`5262307-5`, sem o ponto de `52.62307-5`. **Não foi corrigido** — alterar
identidade pode forçar reverificação. Fica registrado para decisão humana.

**Unidades autorizadas:** o modelo não vincula médico a unidade. As unidades
existentes são `Pastore` (inativa) e `Pastore Ipanema` (ativa). A unidade entra
no laudo pela origem do documento, não pelo perfil.

---

## 4. QR de validação — diagnóstico

Verificado no ambiente **real** da API em execução (`EnvironmentFile` de
`soprolife-m15-api.service`), não apenas no código ou na documentação:

> **`M15_REPORTS_VALIDATION_BASE_URL` — NÃO CONFIGURADA**

A variável não existe entre as chaves carregadas pelo serviço. Nada foi
configurado nesta missão, conforme instruído. Consequência prática: o QR/link
de validação do laudo não terá URL base — a ser decidido fora desta missão.

---

## 5. Achado técnico: `ESP-TF0001` é recusado por construção

`js/report-workflow.js:25` define `EXAM_CODE_RE = /^ESP-\d{1,9}$/i` — só
dígitos após o hífen. O servidor repete a regra em `_SAFE_EXAM_CODE_RE`.

Isso **não** é regressão: é a decisão deliberada da M25.12, documentada no
próprio arquivo (`js/report-workflow.js:1075-1087`). O que a M25.12 corrigiu foi
a *falha silenciosa* — antes, o `pattern` nativo do HTML abortava o submit sem
mensagem alguma; agora quem recusa é a aplicação, **explicando o motivo na
tela** e permanecendo nela, além de oferecer a lista de exames recentes para
clicar sem digitar código.

Como códigos institucionais reais são sempre `ESP-` + 6 dígitos, o cenário desta
missão precisa de um exame com **código numérico gerado pelo sistema**. O
próximo código da sequência será `ESP-000016` (`code_sequences.ESP = 16`).

Isso será aproveitado como evidência extra: a recusa explicada de `ESP-TF0001`
comprova visualmente a correção da M25.12.

---

## 6. Cenário fictício da M25.13 — decisão

Reutilizar o conjunto existente foi avaliado e **descartado** pelos três motivos
de §2.3. Será preparado um conjunto novo, explicitamente marcado **TESTE M25.13**:

- paciente fictício novo (nome contendo `TESTE M25.13`);
- espirometria fictícia com `broncodilatador = true` (necessária para provar RBD+);
- unidade `Pastore Ipanema` (ativa);
- código ESP gerado pelo sistema (numérico → aceito pelo localizador);
- PDF técnico **sintético** já gerado e conferido:
  `/home/fedorasurf/Documents/SoproLife/_EVIDENCIAS_M25_13_PRODUCAO/PDF_TECNICO_SINTETICO_M25_13.pdf`
  (1546 bytes, 1 página, PDF 1.4 válido), com os dizeres
  **"DOCUMENTO FICTICIO - TESTE INTERNO M25.13"**,
  "NAO E EXAME DE PACIENTE REAL - VALORES INVENTADOS", todos os parâmetros
  **zerados de propósito** e o rodapé
  "PILOTO INTERNO - DOCUMENTO NAO ASSINADO / NAO LIBERAR AO PACIENTE".
  Não imita resultado verdadeiro de paciente.

O criador será pelos **fluxos oficiais da interface real** em produção. Nenhum
`INSERT` manual. O script de seed da M25.12
(`scripts/seed_m25_12_exame_sem_laudo.py`) **não serve**: é fail-closed e recusa
rodar fora de `M15_ENV=dev` com SQLite — corretamente.

---

## 7. Catálogo clínico — conferido no código-fonte servido

Extraído de `nucleo-m15/app/services/report_conclusions.py` (conferência prévia;
a contagem **na tela** ainda será feita após o login):

- `CONCLUSION_OPTIONS`: **18** = **17 clínicas + `PERSONALIZADO`** ✅
- `BRONCHODILATOR_OPTIONS`: **5** ✅
  (`RBD+`, `RBD−`, `REV completa`, `REV parcial`, `BD não realizado`)

Textos automáticos que a missão exige provar:

| Sigla | Texto por extenso |
| --- | --- |
| **DVO Leve** | **"Distúrbio ventilatório obstrutivo leve."** ✅ |
| **RBD+** | **"Com resposta significativa ao broncodilatador."** ✅ |

Aviso do piloto no front (`js/report-workflow.js:17`):
`PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE` ✅

**Risco de captura a mitigar:** a lista "espirometrias recentes sem laudo"
exibirá os 15 exames reais (código institucional + data, sem nome). Nas
screenshets em que essa lista aparecer, as linhas que não forem do cenário
M25.13 serão borradas antes da captura. A intervenção será declarada na legenda
da evidência.

---

## 8. Navegador real contra produção

| Item | Valor |
| --- | --- |
| Navegador | Google Chrome 150.0.7871.181, janela visível |
| Perfil | perfil limpo e descartável no scratchpad da sessão (sem cookies pessoais) |
| URL | `https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/` |
| `window.isSecureContext` | `true` |
| Selo na tela | "Acesso seguro (HTTPS)" |
| Rodapé da API | "Proxy/API: ok (prod, banco ok)" |

Nenhum `localhost`, nenhum servidor local, nenhum HTML sintético.

### Screenshots até aqui

| # | Arquivo | Conteúdo |
| --- | --- | --- |
| 00 | `00_login_producao_ponto_de_partida.png` | produção aberta, ainda não autenticada |
| 01 | `01_nucleo_administrativo_login_vazio.png` | Núcleo administrativo, campos **vazios**, selo HTTPS e "prod, banco ok" |

Pasta: `/home/fedorasurf/Documents/SoproLife/_EVIDENCIAS_M25_13_PRODUCAO/`

Nenhuma senha, token, cookie ou dado real de paciente foi capturado.

---

## 9. Fluxo administrativo em produção — executado

Sessão administrativa autenticada **manualmente pelo usuário** no navegador
(`Adeildo José de Lima Junior · admin`). Nenhum cookie, token ou senha foi lido,
extraído ou registrado; toda a operação usou a sessão já autenticada na página.

### 9.1 Cenário TESTE M25.13 criado (Central de Cadastros → Novo atendimento)

| Item | Valor |
| --- | --- |
| Paciente | **PES-000029** — "TESTE M25.13 Paciente Ficticio" |
| Exame | **ESP-000016** (código emitido pelo sistema, numérico) |
| Data | 09/08/2026 |
| Status | `Realizado` |
| **Broncodilatador** | **`true`** — necessário para provar RBD+ |
| Modalidade | `clinica_parceira` |
| Local | "Pastore Ipanema - TESTE M25.13" |
| Origem / responsável | "TESTE M25.13" |
| Valor financeiro | **em branco de propósito** |
| Lançamentos financeiros gerados | **0** (verificado no banco) |

Sem CPF, telefone, e-mail, endereço ou qualquer dado real. Nenhum `INSERT`
manual: tudo pelo formulário oficial.

**Decisão registrada:** o tipo escolhido foi **"Espirometria SoproLife"**, e não
"Espirometria Pastore". A opção Pastore vincula o atendimento ao parceiro e
alimenta repasse/acertos — um exame fictício ali contaminaria a conciliação
financeira real. A unidade **Pastore Ipanema** foi aplicada no **laudo**, que é
onde ela tem efeito clínico (define o local impresso), sem tocar no financeiro
do parceiro. Requisito atendido no lugar certo.

### 9.2 Evidência extra A0 — a correção da M25.12 comprovada em produção

Digitado `ESP-TF0001` (código com letras) no localizador. A aplicação **recusou
explicando na tela** e permaneceu nela:

> **Formato não reconhecido: ESP-TF0001**
> Este não é um código institucional de espirometria. Os códigos são emitidos
> pelo sistema e não contêm letras depois do hífen.
> […] Se você viu um código começando com LAU-, ele identifica o laudo, não o exame

É exatamente o oposto da falha silenciosa que a M25.12 corrigiu: antes o
`pattern` nativo abortava o submit sem mensagem alguma.

### 9.3 Passos administrativos

1. **Exame localizado** — `ESP-000016` digitado e submetido; feedback verde
   "ESP-000016 localizado · Exame de 09/08/2026 · Realizado". Evidência **A**.
2. **Upload habilitado** — o formulário de atribuição apareceu junto com o
   feedback. Evidência **B**. A lista "Médico responsável" ofereceu **apenas a
   Dra. Ana**; o perfil `TESTE APAGAR Medica Fumaca` (inativo, `pending`) **não**
   é oferecido — o fail-closed funciona.
3. **PDF sintético anexado** — `PDF_TECNICO_SINTETICO_M25_13.pdf`, 1546 bytes,
   `application/pdf`.
4. **Atribuição preenchida** — médica: Dra. Ana; origem: `clínica parceira`;
   rótulo: `TESTE M25.13`; unidade: **Pastore Ipanema · Ipanema, Zona Sul**.
5. **"Enviar e atribuir"** → **`LAU-000001` recebido e atribuído com segurança**.
   Evidências **C1** e **C2**.
6. **O exame saiu da lista de "sem laudo"** (16 → 15) e passou a aparecer no
   **Acompanhamento operacional** como `LAU-000001 · ESP-000016 · Pendente de
   laudo` — o comportamento que a M25.12 corrigiu ("o exame sumia da tela").

### 9.4 Estado confirmado no banco (leitura)

| Campo | Valor |
| --- | --- |
| `public_code` do laudo | **LAU-000001** |
| `public_code` do exame | **ESP-000016** |
| `status` | `atribuido` |
| Unidade | **Pastore Ipanema** |
| `origin_type` / `origin_label` | `clinica_parceira` / `TESTE M25.13` |
| Médica atribuída | Dra. Ana Cristina do Nascimento Cunha (CRM display `5262307-5`, RQE 58224) |
| Atribuição ativa | `true`, `initial_assignment` |
| Versão | `original` v1 — 1546 bytes, 1 página, `application/pdf` |
| **PDF em disco** | **gravado e presente** (1546 bytes) — ao contrário do LAU-TF0001, que é órfão |

**Auditoria** (`audit_logs`): `pessoa.criada` → `atendimento.criado` →
`laudo_original_atribuido`, todos carimbados.

### 9.5 Screenshots do fluxo administrativo

| # | Arquivo | Conteúdo |
| --- | --- | --- |
| A0 | `A0_recusa_explicada_codigo_com_letras.png` | recusa explicada de `ESP-TF0001` |
| A | `A_exame_ficticio_localizado.png` | `ESP-000016` localizado, feedback verde |
| B | `B_upload_habilitado_e_preenchido.png` | upload habilitado, Dra. Ana, Pastore Ipanema, PDF anexado |
| C1 | `C1_laudo_criado_confirmacao.png` | "LAU-000001 recebido e atribuído" |
| C2 | `C2_laudo_atribuido_acompanhamento.png` | acompanhamento operacional + aviso do piloto |

**Intervenção declarada:** nas telas em que a lista "espirometrias recentes sem
laudo" aparece, as linhas dos **15 exames reais** foram borradas por CSS
(`filter: blur`) **apenas na exibição**, imediatamente antes da captura. Nenhum
dado foi alterado; `ESP-000016` permanece legível. A medida evita que códigos e
datas de exames de pacientes reais entrem em evidência.

---

## 9-B. Fluxo médico em produção — executado

Sessão da médica autenticada **manualmente pelo usuário**
(`Sessão: Ana Cristina do Nascimento Cunha · Papel clínico explícito`). Nenhum
cookie, token ou senha lido, extraído ou registrado.

### D — LAU-000001 em "Meus laudos" ✅

A fila clínica restrita exibe `LAU-000001 · ESP-000016 · 09/08/2026 · clínica
parceira · Pendente de laudo`. `LAU-TF0001` **não** aparece para ela — está
atribuído ao perfil de teste inativo, como esperado.
Evidência: `D_meus_laudos_fila_da_medica.png`

### E — Bancada clínica ✅

Geometria medida ao vivo em viewport de 1600 px:

| Painel | Título | x | largura | `position` |
| --- | --- | --- | --- | --- |
| Esquerda | **Exame técnico (MIR)** | 619 | 460 px | **`sticky`, `top: 12px`** |
| Direita | **Laudo SoproLife** | 1091 | 460 px | `static` |

O PDF técnico sintético é renderizado à esquerda e fica **legível na captura**
("DOCUMENTO FICTICIO - TESTE INTERNO M25.13"). A coluna do exame é
comprovadamente sticky.
Evidência: `E_bancada_pdf_esquerda_laudo_direita.png`

### F — Contagem feita NA TELA ✅

Contados os botões efetivamente renderizados e visíveis:

| Item | Exigido | **Contado na tela** |
| --- | --- | --- |
| Conclusões clínicas | 17 | **17** ✅ |
| PERSONALIZADO | 1 | **1** ✅ |
| **Total de botões de conclusão** | **18** | **18** ✅ |
| Complementos pós-BD | 5 | **5** ✅ |

Conclusões: Normal · DVO Leve · DVO Moderado · DVO Mod. grave · DVO Grave · DVO
Muito grave · DVR sug. Leve · DVR sug. Moderado · DVR sug. Mod. grave · DVR sug.
Grave · DVR sug. Muito grave · DVM sug. Leve · DVM sug. Moderado · DVM sug. Mod.
grave · DVM sug. Grave · DVM sug. Muito grave · DVI · **Personalizado**

Complementos: **RBD+** · RBD− · REV completa · REV parcial · BD não realizado
(a tela indica "Exame com fase pós-broncodilatador", lendo o `broncodilatador =
true` do exame).
Evidência: `F_conclusoes_17_mais_personalizado_e_5_pos_bd.png`

### G — DVO Leve ✅

Clique real no botão **DVO Leve** (`aria-pressed=true`, `is-selected`). O campo
"Texto final do laudo" recebeu **imediatamente**:

> **Distúrbio ventilatório obstrutivo leve.**

Evidência: `G_dvo_leve_texto_automatico.png`

### H — RBD+ e texto composto ✅

Clique real em **RBD+**. O texto passou a ser, exatamente:

```
Distúrbio ventilatório obstrutivo leve.
Com resposta significativa ao broncodilatador.
```

**Edição manual testada:** acrescentou-se uma frase ao final; o conteúdo
automático foi **integralmente preservado** (`readOnly=false`, nada apagado em
silêncio). Em seguida o campo foi devolvido ao texto clínico exato de duas
linhas, e a marcação de teste movida para "Observações complementares", que é o
lugar correto.
Evidência: `H_rbd_mais_texto_composto.png`

### I — Prévia ✅

"Gerar prévia do laudo" → "Prévia gerada. Confira o documento antes de assinar."
A bancada passou a exibir **dois PDFs lado a lado**:

| Painel | Título | x |
| --- | --- | --- |
| Esquerda | Exame técnico (MIR) — **continua intacto** | 630 |
| Direita | **Prévia do laudo** | 1102 |

Conteúdo do PDF gerado (texto extraído do arquivo baixado):

- Unidade: **Pastore Ipanema — Rua Teixeira de Melo, 54 — Ipanema, Zona Sul — RJ** ✅
- **PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE** ✅
- "PRÉVIA — DOCUMENTO NÃO LIBERADO" ✅
- Nome/Registro/Exame: paciente fictício · PES-000029 · ESP-000016 · Pós-BD: realizado ✅
- CONCLUSÕES: as duas frases exatas ✅
- **Dra. Ana Cristina do Nascimento Cunha — Médica Pneumologista • CRM-RJ
  5262307-5 • RQE 58224** ✅
- "Código de verificação: —" — **vazio**, coerente com o QR não configurado (§4)
- Nota impressa: o traçado original fica no PDF técnico, "documento SEPARADO
  deste laudo, inalterado, com download próprio"

Evidência: `I_previa_pdf_mir_esquerda_laudo_direita.png`

### J1 — Ação de finalização ⚠️

A ação **"Marcar conteúdo pronto para assinatura"** existe e está habilitada,
dentro do bloco **"Preparar assinatura qualificada (ICP-Brasil, pendente)"**, que
se descreve na própria tela: *"Congela os snapshots e deixa o documento
aguardando um provedor de assinatura qualificada. Nenhum provedor está
configurado nesta versão."*

Acionada, ela **recusou** com `rascunho_composto_ausente` — "Gere uma prévia
antes de preparar a assinatura".

A recusa está **correta**: `preparar-assinatura`
(`app/routers/reports.py:2249-2259`) exige que a versão corrente seja um
`KIND_RASCUNHO` — o rascunho do fluxo antigo de *anotação sobre o PDF da MIR*
(M25.2/M24C), não a prévia do laudo próprio. São dois caminhos distintos, e o do
laudo SoproLife é "Assinar e liberar". A mensagem, porém, é **enganosa**: manda
"gerar uma prévia" que já existe; o que falta é outra coisa.
Evidências: `J1_acao_de_finalizacao_disponivel.png`, `J1b_achado_interface_travada_apos_erro.png`

### J2 — Confirmação consciente ✅

O botão "Assinar e liberar laudo" abre um `role="alertdialog"` que exige
confirmação explícita, com os avisos:

> **Confirmar assinatura e liberação** — Você vai assinar e liberar este laudo
> com a sua identificação profissional. O conteúdo será congelado e o documento
> passa a valer para entrega.
> · Confira a prévia exibida acima — é exatamente o PDF final.
> · Depois da liberação, correções só entram como adendo ou versão corretiva.
> · A liberação é registrada com seu usuário, data e hora.

Botões: **"Sim, assinar e liberar laudo"** e "Cancelar". Nenhum clique isolado
assina.
Evidência: `J2_confirmacao_consciente.png`

### J2-bis — A liberação FALHA em produção ❌ (bug bloqueante)

Confirmada a liberação, a API respondeu **500 — "Erro interno. Consulte os logs
pelo request_id."** Ver §13.

O rollback funcionou: `LAU-000001` permanece `em_elaboracao`, sem `released_at`,
sem `validation_code`. **Nenhum estado corrompido.**

### J3 — Downloads separados ✅

O bloco "Documentos do exame" lista os dois documentos com botões **"Baixar"
independentes**, e ambos os downloads foram executados de verdade:

| Documento | Descrição na tela | Arquivo baixado |
| --- | --- | --- |
| **Exame técnico (MIR)** | "PDF original do equipamento, sem qualquer alteração" · v1 | **1.546 bytes** |
| **Laudo médico SoproLife** | "Documento próprio com a conclusão médica" · Prévia do laudo v2 | **123.496 bytes** |

Arquivos guardados como evidência:
`J3_baixado_PDF_TECNICO_MIR_ESP-000016.pdf` e
`J3_baixado_LAUDO_SOPROLIFE_LAU-000001.pdf`.
Evidência de tela: `J3_downloads_separados.png`

---

## 10-B. Placar da experiência aprovada

| # | Item exigido | Resultado |
| --- | --- | --- |
| 1 | PDF técnico original visível à esquerda | ✅ |
| 2 | Área de trabalho do laudo à direita | ✅ |
| 3 | Coluna do exame sticky em tela larga | ✅ (`position: sticky`) |
| 4 | 17 conclusões clínicas | ✅ contadas na tela |
| 5 | + PERSONALIZADO | ✅ |
| 6 | Total de 18 botões | ✅ |
| 7 | 5 complementos pós-BD | ✅ |
| 8 | Botão DVO Leve | ✅ |
| 9 | Botão RBD+ | ✅ |
| 10 | DVO Leve gera o texto por extenso | ✅ |
| 11 | RBD+ acrescenta o complemento | ✅ |
| 12 | Texto continua editável | ✅ sem perda silenciosa |
| 13 | Prévia gerada/atualizada | ✅ |
| 14 | PDF MIR visível durante a escolha | ✅ |
| 15 | **Finalização disponível** | ⚠️ **botão e confirmação sim; execução FALHA (500)** |
| 16 | Downloads separados | ✅ |

**15 de 16 comprovados.** O único item não comprovado é a liberação efetiva —
por bug de produção, não por limitação do piloto.

---

## 10. Incidente de segurança operacional

Numa primeira tentativa de consultar o banco, um comando imprimiu a mensagem de
erro do `psql` contendo a **URL de conexão com a senha do usuário
`soprolife_m15`** no log desta sessão. O erro foi meu.

Correções aplicadas de imediato:

- passou-se a usar um helper em `/root/.m25_13_psql.sh` que monta o DSN
  internamente e **filtra** qualquer ocorrência da URL na saída;
- a credencial não voltou a aparecer em nenhuma saída posterior.

A remediação definitiva — rotação da credencial — foi executada e está
registrada em §10-A. Os logs da sessão **não** foram apagados nem reescritos: a
rotação é a remediação correta, esconder o registro não seria.

---

## 10-A. Remediação da credencial PostgreSQL

**Motivo da rotação.** A senha do role PostgreSQL `soprolife_m15` apareceu uma
vez, em texto claro, numa mensagem de erro do `psql` registrada no log local
desta sessão (§10). A credencial passou a ser considerada comprometida e foi
rotacionada antes de qualquer login manual.

**Horário.** 09/08/2026, 17:41:43 UTC (14:41:43 BRT). Janela total de
indisponibilidade da API: ~6 s (um único `restart`).

**Preflight imediatamente antes da alteração**

| Verificação | Resultado |
| --- | --- |
| HEAD da VPS | `bbd9ebcfd20a8407e789a3a4f4b62b292e67b5f5` (inalterado) |
| Branch / `git status` da VPS | `painel-soprolife-v01`, limpo |
| `M15_REPORTS_MODE` | `pilot` |
| Health antes | `status ok`, `ambiente prod`, `banco ok` |
| Serviços antes | os 3 `active` |

**Identificação**

| Item | Valor |
| --- | --- |
| Variável com a conexão | `M15_DATABASE_URL` |
| Arquivo | `/opt/soprolife/secrets/m15.env` |
| Driver / host / porta / banco | `postgresql+psycopg` · `127.0.0.1` · `5432` · `soprolife_m15` |
| Role PostgreSQL efetivo | `soprolife_m15` |
| Serviços consumidores | `soprolife-m15-api.service` e `soprolife-update-data.service` (timer de 10 em 10 min) — ambos recebem a variável **via `EnvironmentFile` do systemd**, lido pelo PID 1 como root |
| Não consomem o banco | `soprolife-painel.service`, `soprolife-painel-loopback.service` |
| Nenhum script lê o arquivo diretamente | confirmado por varredura em `/opt/soprolife` e `/etc` |

**Backup criado** (root-only, `0600`, diretório `0700`):

```
/opt/soprolife/backups/m25-13/20260809T174143Z/m15.env
```

Apenas o **caminho** é registrado; o conteúdo não foi impresso em momento algum.

**Como a troca foi feita**

- Senha nova gerada com `secrets.choice` sobre `[A-Za-z0-9]`, 48 caracteres
  (~285 bits), existindo **apenas na memória do processo**. Nunca impressa,
  nunca gravada em disco, nunca em argumento de processo nem em histórico de
  shell.
- O que chegou ao PostgreSQL foi o **verificador SCRAM-SHA-256** derivado da
  senha (`password_encryption = scram-sha-256`), e não a senha em claro —
  defesa em profundidade, mesmo com `log_statement = none` confirmado.
- O `ALTER ROLE` foi entregue ao `psql` por **stdin** (`-f -`), portanto não
  apareceu em `ps` nem em histórico.
- O `m15.env` foi reescrito de forma **atômica** (`os.replace`), substituindo
  **somente** a senha e preservando driver, usuário, host, porta, banco e
  demais parâmetros e linhas do arquivo.
- Para evitar colisão, o `soprolife-update-data.timer` foi parado durante a
  janela, e a execução em andamento aguardada até terminar (`ExecMainStatus=0`).

**Permissões.** `root:soprolife 640` → **`root:root 600`**. Seguro porque os
dois consumidores recebem o env pelo systemd (lido como root), não lendo o
arquivo como usuário `soprolife`.

**Serviço reiniciado.** Apenas `soprolife-m15-api.service` — o único serviço de
longa duração que usa o banco. `soprolife-painel` e `soprolife-painel-loopback`
**não** foram reiniciados por não consumirem a credencial.

**Validação após a rotação**

| Verificação | Resultado |
| --- | --- |
| `soprolife-m15-api.service` | `active` ✅ |
| `soprolife-painel.service` | `active` ✅ |
| `soprolife-painel-loopback.service` | `active` ✅ |
| Health HTTPS de produção | `{"status":"ok","ambiente":"prod","banco":"ok"}` — HTTP 200 ✅ |
| Painel estático | HTTP 200 ✅ |
| `M15_REPORTS_MODE` | continua **`pilot`** ✅ |
| HEAD da VPS | `bbd9ebc…`, `git status` limpo ✅ |
| Esteira `update-data` com a senha nova | execução de validação com `Result=success`, "Fonte operacional: PostgreSQL (Núcleo M15)" ✅ |
| `soprolife-update-data.timer` | religado, `active` e `enabled` ✅ |

Além do health, a conexão com a credencial nova foi testada diretamente pelo
venv da API antes do restart; se tivesse falhado, o script restauraria o env do
backup e abortaria.

**Nenhum segredo foi impresso.** Em toda a remediação não apareceram: senha
antiga, senha nova, `M15_DATABASE_URL` completa, DSN, token ou qualquer outro
segredo. As saídas do `journalctl` foram filtradas por precaução.

**Helper removido.** `/root/.m25_13_psql.sh` foi apagado com `shred -u -n 3`,
assim como o script de rotação `/root/.m25_13_rotacao.py`. Remoção confirmada.
Consultas futuras ao banco nesta missão usarão a API ou um helper recriado na
hora.

**Observação fora do escopo:** o `update-data` emite o alerta "Erros de escrita
na auditoria". É **preexistente** — 140 ocorrências nas últimas 24 h, inclusive
às 14:41:39, quatro segundos *antes* da rotação. Não foi causado por esta
mudança e não foi investigado aqui.

**Não** houve deploy, alteração de código, commit ou mudança de `reports_mode`.

---

## 13. Achados de produção (nada foi corrigido)

### 13.1 BLOQUEANTE — nenhum laudo pode ser liberado (erro 500)

**Sintoma.** "Sim, assinar e liberar laudo" → HTTP 500.

**Causa raiz**, do log da API:

```
sqlalchemy.exc.DataError: (psycopg.errors.StringDataRightTruncation)
value too long for type character varying(20)
[parameters: {'status': 'liberado', 'signature_status': 'liberada_institucional', …}]
```

A coluna `report_documents.signature_status` é **`varchar(20)`**
(`app/models.py:1286` → `mapped_column(String(20))`), mas o valor que o próprio
sistema exige gravar é **`'liberada_institucional'`, com 22 caracteres**
(`app/models.py:1042`). Pior: a CHECK constraint
`ck_report_documents_clinical_state_coherent` **obriga** exatamente esse valor
quando `status = 'liberado'` (`app/models.py:1369`).

O modelo é internamente contraditório: exige na coluna um valor que não cabe
nela. Conclusão: **a liberação institucional nunca funcionou em produção** e
sempre falhará, para qualquer laudo — não é específico deste cenário de teste.

**Por que os testes não pegaram.** `tests/conftest.py:45` usa
`sqlite:///…/test.db`, e o SQLite **não impõe** limite de `VARCHAR`; o
PostgreSQL impõe. O teste de liberação passa no SQLite e a produção quebra.
É precisamente o caso da regra desta missão: *o objetivo não é ter testes verdes*.

**Correção sugerida** (não aplicada — exigiria migração + deploy autorizados):
migração Alembic ampliando `report_documents.signature_status` para
`varchar(40)`, mantendo a CHECK constraint. Verificar também as demais colunas
que guardam status com valores longos.

**Impacto operacional.** A médica consegue laudar, gerar prévia, conferir e
baixar — mas **não consegue liberar**. O documento fica preso em
`em_elaboracao`.

### 13.2 ALTO — a bancada congela depois de qualquer erro

**Sintoma.** Após a recusa (correta) de "Marcar conteúdo pronto para
assinatura", **as 4 ações principais ficaram desabilitadas** ("Gerar prévia do
laudo", "Assinar e liberar laudo", "Gerar anotação sobre o PDF da MIR", "Marcar
conteúdo pronto"). A médica fica sem saída, sem explicação. Só um F5 recupera.

**Causa raiz** (`js/report-workflow.js:2204-2221`):

```js
} catch (error) {
  announce(readableError(error), "erro");
  render();            // pinta a tela AINDA com state.busy = true
} finally {
  state.busy = false;  // zera depois — e ninguém repinta
}
```

O `render()` do `catch` roda antes do `finally`. A tela é desenhada com tudo
desabilitado e nunca é repintada.

**Não é isolado:** o mesmo padrão `catch { … render() } finally { state.busy =
false }` aparece em **13 handlers** do arquivo; apenas dois
(`js/report-workflow.js:1898` e `:1950`) fazem `finally { state.busy = false;
render(); }`, que é a forma correta.

**Correção sugerida** (não aplicada): mover o `render()` para o `finally`, depois
de `state.busy = false`, nos 13 pontos.

### 13.3 Observação — código de verificação vazio

O PDF imprime "Código de verificação: —" porque
`M15_REPORTS_VALIDATION_BASE_URL` não está configurada (§4). Coerente e sem
erro, mas o laudo liberado sairia sem meio de validação. Decisão humana.

---

## 11. Pendências e limitações

- ~~Fluxo administrativo e fluxo médico: não executados (aguardando logins).~~
  **Corrigido em 09/08/2026:** ambos **foram executados** em produção depois dos
  dois logins manuais. O fluxo administrativo está em §9 (evidências A0, A, B,
  C1, C2) e o fluxo médico em §9-B (evidências D a J3). Esta linha refletia o
  estado do relatório antes desses passos e ficou desatualizada.
- ~~Contagem de 17+1 conclusões e 5 complementos: conferida no código, ainda não
  na tela.~~ **Corrigido:** contada **na tela** em produção — 17 clínicas +
  PERSONALIZADO = 18 botões, e 5 complementos pós-BD (§9-B, evidência F).
- **A liberação, único item que faltava (§13.1), foi corrigida e comprovada em
  produção pela M25.14** — ver
  `RELATORIO_M25_14_CORRECAO_LIBERACAO_LAUDO_PRODUCAO_20260809.md`. `LAU-000001`
  está `liberado`, com código de verificação `Y6ZVEF9XZY7Z`.
- `crm_display` sem o ponto: **documentado, não corrigido** (decisão humana).
- `M15_REPORTS_VALIDATION_BASE_URL`: **não configurada**, não configurada por mim.
- `LAU-TF0001` e seu conjunto: **intactos**, apesar de inutilizáveis.
- Nenhuma assinatura manuscrita cadastrada (nenhuma foi cadastrada, conforme a
  proibição). A liberação institucional não depende dela — depende da correção
  de §13.1.
- Credencial PostgreSQL **rotacionada** em 09/08/2026 17:41:43 UTC (§10-A);
  produção validada e íntegra depois disso.
- `/opt/soprolife/secrets/m15.env` passou a ser `root:root 600`. Quem for rodar
  a CLI do núcleo manualmente **como usuário `soprolife`** precisará de `sudo`
  para ler o arquivo — pelos serviços do systemd nada muda.
- **`LAU-000001` ficou em `em_elaboracao`** com a prévia v2 pronta. É um laudo
  100% fictício; pode ser retomado assim que §13.1 for corrigido, ou descartado.
- **Intervenção manual declarada:** um `F5` foi necessário para destravar a
  interface depois do erro de §13.2, e as linhas de exames reais foram borradas
  por CSS antes das capturas do fluxo administrativo.
- A liberação foi tentada **de propósito** e sobre paciente fictício, em modo
  piloto: era o único jeito de comprovar (ou refutar) o passo final. Nenhum
  paciente real foi tocado; o rollback deixou o banco íntegro.

---

## 14. Inventário das evidências

Pasta: `/home/fedorasurf/Documents/SoproLife/_EVIDENCIAS_M25_13_PRODUCAO/`

Todas capturadas do Chrome real contra
`https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/`
(`isSecureContext: true`). Nenhuma de `localhost`, servidor local ou HTML
sintético. Nenhuma contém senha, token, cookie ou dado real de paciente.

| # | Arquivo |
| --- | --- |
| 00 | `00_login_producao_ponto_de_partida.png` |
| 01 | `01_nucleo_administrativo_login_vazio.png` |
| A0 | `A0_recusa_explicada_codigo_com_letras.png` |
| A | `A_exame_ficticio_localizado.png` |
| B | `B_upload_habilitado_e_preenchido.png` |
| C1 | `C1_laudo_criado_confirmacao.png` |
| C2 | `C2_laudo_atribuido_acompanhamento.png` |
| D | `D_meus_laudos_fila_da_medica.png` |
| E | `E_bancada_pdf_esquerda_laudo_direita.png` |
| F | `F_conclusoes_17_mais_personalizado_e_5_pos_bd.png` |
| G | `G_dvo_leve_texto_automatico.png` |
| H | `H_rbd_mais_texto_composto.png` |
| I | `I_previa_pdf_mir_esquerda_laudo_direita.png` |
| J1 | `J1_acao_de_finalizacao_disponivel.png` |
| J1b | `J1b_achado_interface_travada_apos_erro.png` |
| J2 | `J2_confirmacao_consciente.png` |
| J3 | `J3_downloads_separados.png` |
| — | `PDF_TECNICO_SINTETICO_M25_13.pdf` (fonte, fictício) |
| — | `J3_baixado_PDF_TECNICO_MIR_ESP-000016.pdf` (1.546 B) |
| — | `J3_baixado_LAUDO_SOPROLIFE_LAU-000001.pdf` (123.496 B) |

---

## 12. O que NÃO foi feito (conforme proibições)

Sem alteração de `reports_mode`; sem deploy; sem `reset --hard`; sem force push;
sem rebase; sem apagar worktree; sem deletar registros; sem limpar `LAU-TF0001`;
sem corrigir CRM; sem configurar QR; sem cadastrar assinatura; sem redefinir
senha; sem dados reais.
