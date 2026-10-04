# M25.17 — Operação real limpa, rubrica privada e downloads amigáveis

**Data:** 09/08/2026
**Branch oficial:** `painel-soprolife-v01`
**VPS:** `root@soprolife-painel-01` · `/opt/soprolife/soprolife-site`

---

## 1. Preflight e commits

| Item | Valor |
| --- | --- |
| HEAD local inicial | `e7d9c259cc6f868e36d5ad85cda799da30aea585` ✅ conforme esperado |
| `origin/painel-soprolife-v01` inicial | `e7d9c25` (idêntico — sem reconciliação) |
| HEAD da VPS inicial | `e7d9c25`, branch correta, `git status` limpo |
| Health inicial | `{"status":"ok","banco":"ok"}` |
| `M15_REPORTS_MODE` inicial | `pilot` |
| **HEAD final (local, origin e VPS)** | **`18ed77dc8e8d0bed3909cad8c7c36513d0c35673`** |
| `M15_REPORTS_MODE` final | **`pilot` — inalterado** |

Integrado por **fast-forward** (`e7d9c25..18ed77d`). Nenhum `reset --hard`,
`force push`, `force-with-lease` ou remoção de worktree.

---

## 2. Causa do `unidade_origem_incompativel`

O formulário pedia **dois campos livres** — "Origem do exame" e "Unidade
parceira" — e deixava o operador descobrir sozinho quais combinações o
servidor aceita. A regra existente (`_validate_origin`) só permite unidade
quando a origem é `clinica_parceira`; a escolha natural para um exame na
Pastore era a origem `pastore`, que é um valor **diferente** no domínio.

Resultado no primeiro uso real (Geoffrey Kirk Barnes / ESP-000017):

```
origem "Pastore" + unidade "Pastore Ipanema"  →  422 unidade_origem_incompativel
origem "clínica parceira" + unidade "Pastore Ipanema"  →  aceito
```

O enigma foi criado pelo sistema e resolvido por tentativa e erro. **O exame
já sabia onde tinha sido feito o tempo todo**: `modalidade='clinica_parceira'`,
`partner_id=Pastore`, `partner_unit_id=Pastore Ipanema`.

---

## 3. Como o contexto passou a ser derivado

Novo módulo `app/services/report_origin.py`. Fonte **exclusivamente
estruturada**:

| Campo do exame | Papel |
| --- | --- |
| `modalidade` | decide a origem (`clinica_parceira` / `cowork` / `residencial`) |
| `partner_unit_id` | decide a unidade e o endereço impresso |
| `partner_id` | confere a coerência entre parceiro e dono da unidade |

**Texto livre nunca decide.** `exam.origem` e `exam.local_atendimento` contêm
coisas como `"Pastore"` e `"Pastore Ipanema - TESTE M25.13"`. Casar a palavra
"Pastore" ali escolheria a unidade **financeira** por heurística de string —
um exame domiciliar cujo campo livre menciona a clínica passaria a creditá-la.
Há teste dedicado a isso.

### Três desfechos, deliberadamente distintos

| Situação | Desfecho |
| --- | --- |
| **Coerente** (modalidade + unidade batem) | Deriva e grava. `completo: true` |
| **Contraditória** (clínica parceira sem unidade; unidade com modalidade errada; unidade de outro parceiro; unidade sem modalidade) | **Fail closed.** Envio bloqueado, com mensagem e `como_corrigir` |
| **Ausente** (nada registrado) | Origem genérica `outro`, **sem endereço inventado**, `completo: false`, sinalizado na tela — não bloqueia |

**Por que ausência não bloqueia** — e isto é um julgamento que vale destacar:
**13 dos 18 exames em produção vieram de importação sem `modalidade`.**
Bloqueá-los trocaria "laudo sem endereço" por "nenhum laudo", travando a
operação num dado que ela pode completar depois. Contradição é diferente:
seguir em frente ali imprimiria o endereço de uma clínica onde o exame não
aconteceu. O portão CFM da M25.15 continua contando endereço/contato ausentes
como pendência, então nada é declarado conforme por omissão.

### Onde corrigir o cadastro

A instrução "corrija o atendimento" exigia que existisse **onde** fazê-lo:
`ExamUpdate` não aceitava `modalidade`, `partner_id` nem `partner_unit_id`.
Passou a aceitar, com a coerência validada **na escrita** (e não só na
emissão), e escolher uma unidade preenche o parceiro sozinho.

---

## 4. O que saiu do formulário operacional

| Campo | Antes | Depois |
| --- | --- | --- |
| Origem do exame (6 opções) | `<select>` obrigatório | **removido** — derivado |
| Unidade parceira | `<select>` | **removido** — derivado |
| Rótulo operacional seguro | `<input>` | **removido** |
| Local do exame | não existia | **novo, somente leitura** |

Formulário resultante, conferido em produção:

```
PACIENTE           Geoffrey Kirk Barnes · Espirometria • 04/08/2026 • Pastore Ipanema
                   ESP-000017 · LAU-000002
MÉDICO RESPONSÁVEL [ Dra. Ana Cristina do Nascimento Cunha • Médica
                     Pneumologista • CRM-RJ 52.62307-5 • RQE 58224 ]
LOCAL DO EXAME     Pastore — Pastore Ipanema
                   Rua Teixeira de Melo, 54, Ipanema, Zona Sul, RJ   (leitura)
PDF ORIGINAL       [ Procurar arquivo ]
                   [ Enviar e atribuir ]
```

Os valores de origem **não foram apagados do domínio** (`ORIGIN_TYPES`,
`ORIGIN_LABELS` seguem intactos e ainda rotulam o agrupamento da fila) — só
deixaram de ser pergunta no formulário de atribuição, como a missão pediu.

O backend continua **fail-closed contra payload artificial**: quando o exame
tem local registrado, uma origem ou unidade divergente enviada à mão é
recusada (`origem_divergente_do_exame` / `unidade_divergente_do_exame`).

---

## 5. Seletor da médica (M25.15 preservada)

Continua vindo de `/laudos/medicos-disponiveis`, sem nada hardcoded.
Elegibilidade inalterada: perfil ativo, usuário ativo, papel médico explícito,
`verification_status = verified` e evidência de verificação completa.

Conferido em produção — **1 elegível, pré-selecionada, seletor visível**:

```
Dra. Ana Cristina do Nascimento Cunha • Médica Pneumologista • CRM-RJ 52.62307-5 • RQE 58224
```

Com 2+ elegíveis a escolha volta a ser explícita (coberto por teste).

---

## 6. Política dos testes internos e registros arquivados

**Mecanismo estruturado, não regra por nome.** Não existia sinalizador em
`people`; foi criado seguindo a convenção que `partners.arquivado` (M20) já
usava — o registro **nunca é apagado**, apenas sai das listas operacionais.

Migration `c9d3a17f4b60` (aditiva, reversível, `batch_alter_table`):
`arquivado`, `arquivado_em`, `arquivado_motivo`, com CHECK amarrando estado e
evidência (arquivado exige data **e** motivo).

**Por que não "nome começa com TESTE":** um paciente real chamado Teste
sumiria da fila, e um registro de teste renomeado voltaria a aparecer. A
marcação é explícita, registro a registro, via CLI auditada
(`m15 arquivar-cadastro`, dry-run por padrão, `--motivo` obrigatório).

### Registros arquivados

| Pessoa | Exame | Laudo | Motivo |
| --- | --- | --- | --- |
| `PES-000029` (TESTE M25.13 Paciente Ficticio) | `ESP-000016` | `LAU-000001` | cenário interno das missões M25.13/M25.14 |
| `PES-TF0001` (TESTE APAGAR Paciente Fumaca) | `ESP-TF0001` | `LAU-TF0001` | idem |

### O que foi preservado

Estado do banco depois do arquivamento: **31 pessoas, 18 exames, 3 laudos,
7 versões, 207 registros de auditoria** — nada apagado. Os dois registros
seguem íntegros, com data e motivo, e a ação gerou auditoria própria
(`pessoa.arquivada`) listando exames e laudos afetados.

### Visibilidade

| Superfície | Comportamento |
| --- | --- |
| Fila da Dra. Ana (`/laudos/meus`) | **nunca** mostra arquivado — sem modo técnico, nem por parâmetro de URL |
| Acompanhamento operacional | oculta por padrão; `?incluir_arquivados=true` mostra |
| Localizador / busca por nome | oculta por padrão; mesmo parâmetro explícito |

---

## 7. Rubrica da Dra. Ana

### Infraestrutura utilizada (já existente, não reinventada)

`physician_signature_assets` + `app/services/signature_asset.py` +
endpoints admin-only. Foi conferida antes e é adequada: PNG obrigatório
(JPEG/SVG recusados), teto de 2 MiB, validação de dimensão e proporção,
hash SHA-256 conferido na leitura, armazenamento fora do webroot, RBAC de
admin e auditoria de inclusão. **Nada de novo precisou ser construído.**

### Preparação visual

Fonte: `/home/fedorasurf/Documents/SoproLife/Rubrica Ana Cristina.jpeg`
(JPEG, 122×193, 5 339 bytes).

1. recorte pela extensão real do traço (limiar firme, margem de 5 px) →
   82×111 px, descartando a área do documento de origem;
2. fundo → transparente por **alfa derivado da luminância**, preservando o
   antialiasing (binarizar engrossaria e deformaria a caligrafia);
3. ponto de branco em 188 para eliminar o texto impresso que aparecia como
   fantasma no fundo (primeira tentativa, com 216, deixava-o visível);
4. cor da tinta medida no núcleo do traço — não um preto arbitrário;
5. reamostragem LANCZOS 4× para não pixelizar no PDF.

Resultado: **PNG RGBA 328×444, 58 913 bytes,
`sha256 74adfe7951bfda3159378615e61efbcaa7fd77fb78e293abbecf37224ca78815`**,
proporção 0,739 (dentro de 0,25–12,0).

**Nada foi redesenhado, inventado ou substituído** — só recorte, remoção de
fundo e reamostragem. Comparação lado a lado conferida visualmente.

### Caminho privado no servidor

```
/opt/soprolife/private/reports/assinaturas/
  59709f0c-0511-44b8-9ddc-0547bd01a3f5/ba824525-a293-4e0b-9846-bdeac32717bb.png
```

`58913 bytes`, permissão **`600`**, fora do webroot do painel. Instalada pelo
endpoint admin (`POST /laudos/admin/medicos/{id}/assinatura` com a confirmação
exigida `ATIVO DE ASSINATURA AUTORIZADO`), portanto auditada. O arquivo
temporário usado no transporte foi destruído com `shred`.

### Confirmação de que não entrou no Git

* `git ls-files | grep -ci rubrica` → **0**
* `git status` após o commit → **limpo**, nenhum arquivo de imagem
* nenhuma imagem em `painel-soprolife/` (busca por `*rubrica*` / `*assinatura*.png` → vazio)
* URLs públicas testadas → **HTTP 404**
* teste automatizado `test_rubrica_real_nao_esta_versionada` varre `git ls-files`
* nenhum base64 no código; a API devolve só metadado (sha256, dimensões), nunca bytes nem caminho

### Vínculo

Perfil `59709f0c-0511-44b8-9ddc-0547bd01a3f5` — Dra. Ana Cristina do
Nascimento Cunha. Ativo `ba824525…`, `active: true`.

### Layout no PDF

Conferido em produção com PDF de teste controlado:

```
                    [ rubrica ]
        ────────────────────────────────
        Dra. Ana Cristina do Nascimento Cunha
   Médica Pneumologista • CRM-RJ 52.62307-5 • RQE 58224
```

Centralizada, sobre a linha de assinatura, **45,8 × 62,0 pt** dentro de uma
área reservada de 180 × 62 pt — proporcional, discreta, sem encobrir texto e
sem tocar os selos laterais. Nenhuma mudança de paginação. O código de desenho
já era proporcional e centralizado; **não precisou ser alterado**.

### Médicos futuros

Sem hardcode. `resolve_signature_asset(db, profile_id)` pergunta ao perfil se
há ativo válido: havendo, desenha; não havendo, o laudo sai normalmente sem
imagem (coberto por teste). Cada médico com seu próprio ativo privado.

### Rubrica **não** é ICP-Brasil

Nada mudou na semântica regulatória:

* selo do PDF continua `ASSINADO ELETRONICAMENTE / LIBERAÇÃO INSTITUCIONAL`;
* o texto impresso continua: *"Esta liberação não constitui, por si só,
  assinatura digital qualificada ICP-Brasil."*;
* `qualified_signature` continua `false`;
* o portão CFM continua marcando assinatura qualificada como **pendência**
  mesmo com a rubrica desenhada — há teste dedicado a isso.

---

## 8. Nomes de download e sanitização

Novo `app/services/download_names.py` — helper **único**.

| Documento | Nome | Conferido em produção |
| --- | --- | --- |
| Laudo liberado | `<Paciente> - Assinado.pdf` | `Geoffrey Kirk Barnes - Assinado.pdf` ✅ |
| Exame técnico MIR | `<Paciente> - Exame técnico.pdf` | `Geoffrey Kirk Barnes - Exame técnico.pdf` ✅ |

Regras: remove `/ \ : * ? " < > |` e caracteres de controle; normaliza espaços
repetidos; impede `.` e `..` como nome; **impede CR/LF** (o valor vai para um
cabeçalho HTTP); preserva acentos via `filename*=UTF-8''` (RFC 5987) com
`filename` ASCII como reserva; fallback pelo código institucional.

| Entrada | Saída |
| --- | --- |
| `João da Silva` | `João da Silva - Assinado.pdf` |
| `Maria / Souza` | `Maria Souza - Assinado.pdf` |
| ausente / `.` / `..` | `LAU-000123 - Assinado.pdf` |
| `Fulano\r\nX-Injetado: sim` | CR/LF removidos, sem cabeçalho novo |

**O frontend passou a respeitar o servidor.** `anchor.download = ""` fazia o
navegador inventar um nome a partir da object URL — a causa exata do "nome
técnico/aleatório" relatado. Agora o `Content-Disposition` é lido no cliente
da API e transportado até o clique.

**Sobre a palavra "Assinado":** nomeia o arquivo entregue, não o estado
criptográfico. Nada em status, selo ou texto do PDF foi alterado.

---

## 9. Testes

**Suíte completa: 1063 passaram, 30 puladas.** Quality gate: todos os checks OK.

Novo `tests/test_m25_17_operacao_limpa.py` — **41 testes**:

| Grupo | Cobertura |
| --- | --- |
| Origem/unidade | clínica parceira deriva origem e unidade; SoproLife não recebe unidade; texto livre nunca decide; 3 contradições fail-closed com `como_corrigir`; ausência não bloqueia; upload sem origem; payload divergente recusado; a combinação exata do primeiro uso real é inalcançável; localizador devolve local derivado e mostra o exame contraditório; correção do atendimento funciona e é validada |
| Testes internos | somem da fila da médica, do acompanhamento e do localizador; modo técnico ainda alcança; fila clínica sem modo técnico; auditoria/versões/hashes preservados; CHECK exige evidência |
| Rubrica | ativo cadastrado sem expor bytes/caminho/URL; PDF válido sem rubrica; rubrica **não** altera `qualified_signature`; sem rota pública; não versionada |
| Download | 8 casos de nome; 5 de caracteres perigosos; injeção de cabeçalho; acento em `filename*`; download real |

Regressão M25.14/M25.15 e demais fluxos: toda a suíte anterior continua verde.
Um teste da M25.14 (`test_migrations`) teve a head esperada atualizada para
`c9d3a17f4b60` — o que ele prova (existir **exatamente uma** head) segue igual.

---

## 10. Backup

| Item | Valor |
| --- | --- |
| Diretório | `/opt/soprolife/backups/m25-17/20260810T020424Z` |
| HEAD anterior | `HEAD_ANTERIOR.txt` → `e7d9c259cc6f868e36d5ad85cda799da30aea585` |
| Dump PostgreSQL | `m15.dump` (260 K) — **validado**: `pg_restore --list` → 376 entradas |
| Env | `m15.env.bak`, modo `600` — **nenhum segredo impresso** (só contagem de linhas e prefixo do SHA-256) |
| Assets privados | `private-reports.tar.gz` (844 K) — tirado **antes** de gravar a rubrica |

---

## 11. Deploy

`git fetch` + `git merge --ff-only` (`e7d9c25 → 18ed77d`).
Migration aplicada: `b8e4d2a71c53 → c9d3a17f4b60`.
Restart apenas de `soprolife-m15-api.service`.

| Verificação | Resultado |
| --- | --- |
| HEAD da VPS | `18ed77dc8e8d0bed3909cad8c7c36513d0c35673` |
| `git status` da VPS | limpo |
| Alembic | `c9d3a17f4b60 (head)` |
| Health | `{"status":"ok","ambiente":"prod","banco":"ok"}` |
| Banco | **ok** |
| Painel | HTTP 200 |
| Serviços | `soprolife-m15-api`, `soprolife-painel`, `soprolife-painel-loopback` → `active` |
| `M15_REPORTS_MODE` | **`pilot`** — não alterado |

---

## 12. Smoke test em produção

Sem criar paciente novo e sem alterar conteúdo clínico.

| | Comprovação | Resultado |
| --- | --- | --- |
| **A** | TESTE M25.13 não aparece para a Dra. Ana | ✅ fila com 1 item: Geoffrey Kirk Barnes |
| **B** | TESTE APAGAR fora do acompanhamento normal | ✅ 1 item; com `incluir_arquivados=true` voltam os 3 |
| **C** | Exame real de clínica parceira sem seletor confuso | ✅ `seletorDeOrigemAindaExiste: false`, `seletorDeUnidadeAindaExiste: false`; local read-only: `Pastore — Pastore Ipanema · Rua Teixeira de Melo, 54, Ipanema, Zona Sul, RJ`, derivado de `exame_unidade_parceira` |
| **D** | Dra. Ana elegível | ✅ única, pré-selecionada, com CRM `52.62307-5` e RQE 58224 |
| **E** | PDF novo exibe a rubrica | ✅ PDF de teste controlado, com a rubrica real e credenciais reais, sem gravar nada |
| **F** | Rubrica não acessível publicamente | ✅ modo 600 fora do webroot; URLs → 404; sem rota de conteúdo |
| **G** | Download do laudo com nome humano | ✅ `Geoffrey Kirk Barnes - Assinado.pdf` |
| **H** | Download do MIR com nome humano | ✅ `Geoffrey Kirk Barnes - Exame técnico.pdf` |
| **I** | Sem regressão clínica | ✅ ver abaixo |

### LAU-000002 / Geoffrey — intacto

```
status          : liberado
liberado em     : 2026-08-09 21:55:27 (inalterado)
versão corrente : v3, kind laudo_liberado, sha256 d57b1b8d95dc4141f547…
conclusão       : Distúrbio ventilatório obstrutivo leve.
```

**Nada foi modificado retroativamente.** A rubrica não foi aplicada à versão
já liberada; ela vale para os **próximos** laudos, e foi comprovada com PDF de
teste controlado, como a seção 16 da missão determina. Nenhum PDF liberado foi
substituído em silêncio.

### Screenshots

Capturados em sessão autenticada real contra o Postgres de produção (Chrome
headless + CDP via encaminhamento SSH de loopback):

| Arquivo | O que comprova |
| --- | --- |
| `shots17/admin-upload-seletor-1440.png` | Formulário simplificado: paciente, médico, **local read-only**, PDF |
| `shots17/admin-operacional-1440.png` | Acompanhamento sem os cadastros de teste |
| `shots17/bancada-medica-1440.png` | Bancada da médica preservada |
| `rubrica/comparacao.png` | Original × preparada, lado a lado |
| `rubrica/prod-faixa.png` | Faixa de assinatura do PDF de produção com a rubrica |

> Ficaram no scratchpad da sessão e **não foram commitados**: as telas
> administrativas mostram nomes de pacientes reais, e as duas últimas contêm a
> imagem da rubrica — ativo privado da médica.

---

## 13. Pendências

1. **Assinatura qualificada ICP-Brasil** — sem provedor integrado. Continua
   sendo o que mantém `reports_mode=pilot`. A rubrica **não** altera isso.
2. **12 exames sem local registrado** — passam a aparecer sinalizados
   (`completo: false`) e geram laudo sem endereço. Completar a modalidade de
   cada atendimento é trabalho humano; a edição já está disponível na API.
   Nenhum deles tem laudo hoje.
3. **CPF do paciente** — `people` continua sem a coluna (pendência da M25.15).
4. **Edição de local pela interface** — a correção do atendimento existe na
   API (`PATCH /espirometrias/{id}`), mas ainda não tem campo na tela; hoje o
   bloco de local só informa o que corrigir.
5. **`test_finance_duplicate_revenue_postgres.py`** — quebrado desde a M25.2,
   independente desta missão (registrado na M25.15).

---

## Conclusão

**M25.17 — OPERAÇÃO REAL LIMPA, TESTES ARQUIVADOS, RUBRICA PRIVADA APLICADA E DOWNLOADS AMIGÁVEIS**
