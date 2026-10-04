# M25.26 — Fluxo de Espirometria SoproLife

**Data:** 12–13/08/2026
**Branch de trabalho:** `claude-m25-26-fluxo-espirometria-soprolife`
**Branch oficial:** `painel-soprolife-v01`
**HEAD inicial:** `87271d2c3b75cbe0510d0fe399993998f4cdbd4f`
**HEAD final:** `bdaf7a557361e447f6f619d8b50a2853c0bd7840`
**VPS:** `root@soprolife-painel-01` — deployada e verificada em `bdaf7a5`

---

## 1. Causa raiz do "cadastro incompleto"

A mensagem que travou a Dra. Ana e o operador **não vinha de uma regra de
negócio errada**. Vinha da forma como o erro era devolvido.

`app/errors.py`, no tratador de `RequestValidationError`:

```python
erros = [{"campo": ".".join(str(p) for p in e["loc"]), "tipo": e["type"]}
         for e in exc.errors()]
return _envelope(request, 422, "validacao", "Payload inválido.", {"campos": erros})
```

E `js/m15-nucleo.js`, em `readableApiError`, lia **somente** `erro.mensagem`.

O resultado, reproduzido com dados sintéticos:

```json
{"erro": {"codigo": "validacao",
          "mensagem": "Payload inválido.",
          "campos": [{"campo": "body.espirometria.data_exame", "tipo": "missing"}]}}
```

O operador via literalmente **"Payload inválido."**. A informação de qual
campo faltava existia na resposta, mas em caminho técnico
(`body.espirometria.data_exame`) e sem rótulo — e a tela a descartava.

Pior no caso das regras de negócio: o tratador extraía apenas `loc` e `type`,
nunca `msg`. Então a explicação real — *"Atendimento SoproLife não aceita
parceiro/unidade — use o tipo 'espirometria_pastore'"* — era **suprimida pelo
próprio servidor**, virando `{"campo": "body", "tipo": "value_error"}`.

### 1.1 A segunda causa, mais grave: a data que virava nada

Auditando o caminho da data, apareceu um defeito que ninguém tinha reportado
porque ele **não produz erro nenhum**:

```
parse_incomplete_date("12082026")  ->  value=None, precisao="desconhecida"
```

Digitar a data sem barras não era recusado. O exame era criado com
`data_exame = NULL`, precisão `desconhecida` e follow-up desligado
(`motivo: "nao_aplicavel"`), e o operador via **"criado com sucesso"**.

O problema só aparecia semanas depois: `data_realizacao_exame` é requisito
**bloqueante** da CFM 2.381/2024 em `report_compliance.py`. Ou seja — o exame
lançado assim chegava à Dra. Ana como pendente, e quem digitou já não estava
por perto para dizer quando o exame tinha acontecido.

Isto liga a Fase 0 à Fase F: a máscara de data não é conforto, é a correção
da entrada que alimentava o defeito.

### 1.2 A terceira: o cadastro não conseguia coletar o que o laudo exige

O formulário de pessoa nova oferecia nome, WhatsApp, nascimento, e-mail e
consentimento. **Não oferecia CPF** — que é requisito bloqueante da CFM em
`report_compliance.py` — nem sexo.

E `people.sexo`, criado na M25.2 e impresso no laudo, era **coluna morta**:
nenhum schema o aceitava, nenhuma rota o gravava. O laudo dizia "não
informado" para todos os pacientes, sem que houvesse qualquer forma de
corrigir isso pela interface.

---

## 2. Quais campos eram realmente obrigatórios

Auditado campo a campo, com reprodução sintética:

| Campo | Exigido para criar o atendimento? | Exigido para o laudo? |
|---|---|---|
| `espirometria.data_exame` | **Sim** (schema) | **Sim, bloqueante** |
| `person_id` | **Sim** | — |
| `tipo` | **Sim** | — |
| `pessoa.nome_completo` | Sim (mín. 2 caracteres) | **Sim, bloqueante** |
| `pessoa.cpf` | Não | **Sim, bloqueante** ("quando houver") |
| `pessoa.data_nascimento` | Não | Não (impresso) |
| `pessoa.sexo` | Não | Não (impresso) |
| `modalidade` / `local` | Não | Endereço/contato bloqueantes |
| valor da espirometria | Não | — |

**Descoberta central:** um paciente com cadastro incompleto **nunca foi
recusado** no `POST /atendimentos` — reproduzido: pessoa só com nome →
HTTP 201. O "cadastro incompleto" que a operação encontrou vinha do
**caminho do laudo**, semanas à frente, ou da mensagem opaca do 422 por
outro motivo (tipicamente a data).

Nenhuma validação obrigatória foi removida. O que mudou foi tornar o que é
exigido **preenchível e visível** — que é o que a missão pedia.

---

## 3. Fluxo antigo

1. Aba **Novo atendimento** abria com um campo de busca e nada mais.
2. Para um paciente novo, era preciso descobrir e clicar
   **"+ Cadastrar nova pessoa"** — só então os campos apareciam.
3. Dentro dessa caixa havia um checkbox grande:
   *"Cadastrar apenas a pessoa, sem criar exame ou consulta"*. Marcá-lo
   apagava os passos 2 e 3 e trocava o rótulo do botão.
4. Ao salvar: **duas chamadas HTTP em sequência** — `POST /pessoas` e, só
   depois de a pessoa existir, `POST /atendimentos`.
5. Falha na segunda deixava **paciente órfão**. O operador tipicamente
   recomeçava do zero e criava a mesma pessoa de novo.
6. Erro do servidor virava "Payload inválido." sem indicar campo nem lugar.

---

## 4. Fluxo novo

1. **Passo 1 — Paciente.** Busca no topo; abaixo, o formulário do paciente
   novo **já visível**. Nenhum clique intermediário: todo atendimento
   pertence obrigatoriamente a uma pessoa, então o clique não decidia nada.
2. Selecionar um paciente existente **substitui o formulário por um cartão**
   com nascimento, contato, CPF mascarado e — o ponto novo — **o que falta
   no cadastro dele**, com o botão **"Corrigir cadastro"**.
3. **Uma única operação atômica** para paciente novo:
   `POST /atendimentos/novo-paciente`. Qualquer falha adiante desfaz também
   o paciente. Paciente existente segue em `POST /atendimentos`.
4. Duplicado devolve **409 com os candidatos** — a decisão continua humana,
   nunca fusão automática.
5. Erro do servidor vira **lista de campos com o rótulo do formulário**, e o
   campo correspondente é **destacado e trazido para a vista**.

---

## 5. Semântica de "cadastrar pessoa sem atendimento"

Saiu do caminho principal. Agora é **ação secundária, fora do `<form>`**, com
a frase exigida pela missão:

> *"Cria somente o cadastro da pessoa e seus contatos. Nenhum exame ou
> consulta será criado agora."*

A razão de não ser mais um checkbox no meio do passo 1: é uma **operação
diferente** (pré-cadastro, contato, CRM), e no lugar antigo parecia etapa
normal do Novo atendimento — além de esconder metade do formulário ao ser
marcada.

Garantido por teste: o bloco da ação **não chama** `/atendimentos`, nem
`montarEspirometria`, `montarConsulta` ou `montarFinanceiro`.

---

## 6. Modalidade × Local/Unidade

### 6.1 O que a auditoria dos dados reais mostrou

Consulta somente leitura em produção:

| modalidade | local_atendimento | parceiro | exames |
|---|---|---|---|
| *(null)* | *(null)* | *(null)* | **13** |
| `clinica_parceira` | Pastore Ipanema | Pastore | 3 |
| `clinica_parceira` | *(null)* | Pastore | 2 |
| `clinica_parceira` | Pastore Ipanema - TESTE M25.13 | **nenhum** | 1 |
| `cowork` | Coworking | *(nenhum)* | 1 |

Três conclusões, todas contra o desenho antigo:

1. **Nenhum exame usa `local_atendimento` como categoria.** Os valores
   gravados são **nomes de lugar** ("Pastore Ipanema", "Coworking"). A lista
   `["Domiciliar", "Clínica", "Empresa", "Parceiro", "Outro"]` que a tela
   sugeria **nunca foi usada** — era uma segunda taxonomia competindo com
   Modalidade, e é o que permitia "residencial + Clínica".
2. `report_locations.py` já documentava o campo como *"texto operacional
   livre… complemento do rótulo, nunca endereço estruturado"*.
3. **A armadilha já produziu um registro quebrado em produção:** o exame
   `clinica_parceira` sem parceiro nenhum. Essa combinação é recusada por
   `derive_report_origin` desde a M25.17 — mas só na hora do laudo.

### 6.2 Definição adotada

- **MODALIDADE** = natureza do atendimento. Campo fechado; é dela que sai a
  origem do laudo (`MODALIDADE_PARA_ORIGEM`).
- **LOCAL/UNIDADE** = onde especificamente aconteceu. Nome do lugar, com
  rótulo e sugestões **dependentes da modalidade** — nunca uma segunda lista
  de categorias.

Oferecido para Espirometria SoproLife:

| Modalidade | Local | Unidade |
|---|---|---|
| Domiciliar (no endereço do paciente) | "Domicílio do paciente" (padrão, editável) | não se aplica |
| Cowork / espaço SoproLife | nome do espaço (**obrigatório**) | não se aplica |

**"Clínica parceira" foi retirada da lista SoproLife**, com o motivo
declarado na resposta da API: o schema proíbe parceiro/unidade fora do tipo
Pastore, então escolhê-la criava um exame que a emissão do laudo depois
recusava por falta de unidade. Exame em clínica parceira lança-se por
**Espirometria Pastore**, que já vincula parceiro e unidade.

### 6.3 "Empresa" — não inventamos classificação

A missão pediu para descobrir antes de decidir. O achado: **`empresa` não é
modalidade** — não existe no schema, não existe em nenhum exame em produção,
e `ensure_sem_pcmso` mantém PCMSO fora da operação ativa. "Empresa" só existiu
como sugestão no campo de local. Pertence a **origem** (como o paciente
chegou), que já é campo próprio. Nada foi criado.

### 6.4 Onde a regra passou a rodar

`validar_combinacao_no_cadastro()` mora **no mesmo arquivo** que
`derive_report_origin` (`services/report_origin.py`), de propósito: em
arquivos separados, um dia uma aceitaria o que a outra recusa.

Um teste percorre as seis combinações e prova que **cadastro e laudo julgam
igual**. `ausência ≠ contradição` foi preservado: sem modalidade e sem
unidade o cadastro passa — é o que mantém válidos os 13 exames importados.

**Nenhum dado histórico foi alterado.**

---

## 7. Regra do valor R$ 220,00

Nova configuração: `M15_ESPIROMETRIA_SOPROLIFE_VALOR_PADRAO`, padrão
`Decimal("220.00")`, servida por `GET /api/v1/atendimentos/configuracao`.

- O campo **nasce preenchido** com `220,00` e **permanece editável**.
- **Nenhum `220` codificado em JavaScript** — provado por teste que rejeita
  o literal fora do `placeholder`.
- **O servidor continua não inferindo valor nenhum.** A configuração é
  sugestão de tela; se o operador apagar o campo, nenhum lançamento é criado.
  A ausência permanece ausência (regra do M20, intacta).

Financeiro histórico **não tocado**: 13 LAN / R$ 3.044,79 preservados;
nenhuma mudança no Pastore.

---

## 8. Máscara de datas

Implementada em `m15-datepicker.js` — o componente único por onde passam
nascimento, data do exame, competência, recebimento e acompanhamento.

Digitar `12082026` mostra `12/08/2026` progressivamente. O cuidado está no
que a máscara **não** faz:

1. **Apagar nunca remascara** (detectado por `inputType`). Sem isso a barra
   reaparece no instante em que é apagada e o campo trava.
2. **Barra digitada pelo humano manda.** Quem escreve `08/2026` está dizendo
   "mês e ano".
3. **Em campo parcial, a reformatação só começa no 5º dígito.** Abaixo disso
   `2026` ainda é **ano válido** — contrato legítimo do domínio. Uma máscara
   ingênua o transformaria em `20/26` e prenderia o operador. Em campo de
   data completa, que não tem leitura parcial legítima, a barra entra já no
   3º dígito.

Contrato de precisão parcial preservado ponta a ponta: `2026` → `2026`,
`08/2026` → `2026-08`, `12/08/2026` → `2026-08-12`. Cursor só vai para o fim
quando já estava no fim; `inputmode="numeric"` no celular.

**No servidor:** data preenchida e ilegível passou a ser **recusada** com
código `data_ilegivel`, em vez de virar `NULL`. Campo vazio continua sendo
ausência legítima.

---

## 9. Botão Sair

Adicionado ao cabeçalho do Command Center, entre "Atualizar" e o avatar.

- Usa o **mesmo** `POST /auth/logout` do núcleo — **nenhum segundo contrato
  de autenticação** (verificado por teste: existe uma única ocorrência da
  rota no código).
- A decisão de exibir mora em `m15-nucleo.js`, dono do estado de sessão. Um
  botão desenhado por fora teria de consultar autenticação de outro módulo.
- Oculto sem sessão; visível para qualquer autenticado (sessão ou token).
- Mobile: encolhe o **espaçamento**, nunca a palavra — ícone de porta sozinho
  é adivinhação, e a missão pede que sair seja fácil de achar.

---

## 10. Regressão Pastore

O usuário declarou o fluxo Pastore correto. **Ele não foi redesenhado.**

Provas:

- `test_pastore_continua_derivando_tudo_do_parceiro_canonico` — modalidade,
  local e origem continuam derivados da unidade canônica, não escolhidos.
- `test_pastore_continua_recusando_pagamento_direto` — 422
  `pagamento_direto_pastore_proibido`.
- **Zero receita individual**: `lancamentos == []` e nenhuma linha em
  `financial_entries`.
- `test_exame_pastore_criado_agora_emite_laudo_sem_contradicao` — a coerência
  nova não quebrou o caminho do laudo.
- Suíte M22 (JS, 25 casos) e `test_m22_pastore_settlements.py` verdes.
- Os cinco históricos e os settlements **intactos** (seção 11).

O contrato de tela do Pastore ficou **mais forte**: as checagens que
verificavam "aparece em até N caracteres" foram trocadas por um recorte real
dos dois ramos, exigindo cada campo no ramo correto **e confirmando sua
ausência no outro** — o que também pega o campo que vazar para o lado Pastore.

---

## 11. Financeiro intacto — verificado em produção

Consulta somente leitura, **antes e depois** do deploy, com resultado idêntico:

```
LANCAMENTOS                  | 13 | 3044.79
PASTORE_2026-07-01_a_receber |  1 |  219.00
PASTORE_2026-08-01_a_receber |  1 |  328.50
EXAMES                       | 20
PESSOAS                      | 33
```

- 13 LAN / **R$ 3.044,79** — intactos.
- Pastore julho **R$ 219,00 a receber**; agosto **R$ 328,50 a receber**.
- **Zero recebimento** (nenhum settlement em `recebido`).
- Contagem de exames e pessoas inalterada: **nenhum dado de teste entrou em
  produção**.

---

## 12. Testes

| Suíte | Resultado |
|---|---|
| `test_m25_26_fluxo_espirometria.py` (novo) | **31 passaram** |
| `test-m25-26-fluxo-espirometria.js` (novo) | **70 casos, todos passaram** |
| Suíte Python completa | **1302 passaram**, 30 skipped, 1 falha pré-existente |
| Quality gate | **PASSOU — todos os checks OK** |

### Os 21 itens exigidos

| # | Prova | Onde |
|---|---|---|
| 1 | Novo paciente + Espirometria numa única operação | `test_novo_paciente_e_espirometria_numa_unica_operacao` |
| 2 | Paciente existente completo | `test_modalidades_soprolife_sao_aceitas` |
| 3 | Incompleto: lista campos, corrige, preserva o exame | `test_pendencias_...`, `test_corrigir_cadastro_zera_as_pendencias`, JS §H |
| 4 | Nenhuma duplicação de pessoa | `test_duplicado_e_avisado_e_so_nasce_com_confirmacao_humana` |
| 5 | Modalidade/local compatíveis | `test_modalidades_soprolife_sao_aceitas` |
| 6 | Combinação inválida recusada antes do submit | JS §D + `test_coerencia_do_cadastro_usa_as_regras_do_laudo` |
| 7 | Valor nasce R$ 220,00 | `test_valor_padrao_vem_da_configuracao`, JS §E |
| 8 | Valor editável | `test_valor_editado_pelo_operador_e_o_que_vale`, JS §E |
| 9 | Data autoinsere `/` | JS §A |
| 10 | Colar `12082026` → `12/08/2026` | JS §A |
| 11 | Backspace funciona | JS §A |
| 12 | Mobile 430 | JS §I |
| 13 | Desktop 1920 | JS §I |
| 14 | Sair visível | JS §F |
| 15 | Logout funciona | JS §F + smoke |
| 16 | Pastore sem regressão | seção 10 |
| 17 | M25.23 gate sem regressão | smoke: casca de acesso 5,6 KB; `/data/*` → 401 |
| 18 | M25.24 laudos/tooltips sem regressão | suíte completa verde; ajuda no mesmo padrão |
| 19 | 13 LAN / R$ 3.044,79 intactos | seção 11 |
| 20 | Pastore jul 219,00 / ago 328,50 / zero recebimento | seção 11 |
| 21 | Nenhum dado real criado em testes | fixtures sintéticas; contagens de produção inalteradas |

### Contratos de tela reescritos — e por quê

Quatro suítes existentes falharam. **Nenhuma por comportamento**: todas por
**distância de caractere** ("aparece em até N caracteres depois de X"). Uma
linha de comentário a mais derrubava a checagem. Um alarme que dispara
sozinho ensina a equipe a ignorá-lo, então foram reescritas para recortar o
trecho relevante e afirmar sobre ele:

- **M22 Pastore** — fonte partido nos dois ramos (mais forte que antes).
- **M11** — chave de idempotência procurada no corpo da aba (estava no
  caractere 9528, janela era 9000); ganhou um caso novo.
- **M23.1** — a regex fixava `(pastore, ehPastore)`; a função ganhou `cfg` e
  seis casos caíram juntos. A propriedade protegida não depende da assinatura.
- **M20** — aqui o contrato mudou **de propósito**: o checkbox e o clique
  deixaram de existir. Os casos passam a proteger o contrato novo.

---

## 13. Deploy

Sequência executada, na ordem:

1. Quality gate local: **PASSOU**.
2. Suíte Python completa local: **1302 passaram**.
3. Baseline financeiro de produção capturado (somente leitura).
4. Seis commits pequenos; push da branch de trabalho.
5. `painel-soprolife-v01` atualizada por **`--ff-only`** (`87271d2..bdaf7a5`),
   ancestralidade verificada antes.
6. **Backup na VPS**: `pg_dump -Fc` + ponto de rollback gravados em
   `/var/backups/soprolife/` (carimbo `20260813-010131`).
7. Alembic conferido: VPS em `a2f6c81d4b73`, que **é o head** — **nenhuma
   migration aplicada**. As colunas `people.sexo` e `people.cpf` já existiam.
8. VPS: `git fetch` + `git merge --ff-only` → `bdaf7a5`.
9. **Restart mínimo**: apenas `soprolife-m15-api.service`.
10. Health: `{"status":"ok","banco":"ok","ambiente":"prod"}`.
11. Smoke somente leitura: endpoints novos respondem **401 sem sessão**;
    selo de cache dos assets em `2026081201`; gate M25.23 servindo a casca de
    acesso; financeiro idêntico ao baseline.

Não foi usado `reset --hard`, `force push`, `force-with-lease`, `DELETE` de
dados reais nem banco temporário na VPS. Testes pesados rodaram **só na
máquina local** — o incidente do `createdb m2524_verif` não se repetiu.

---

## 14. HEAD final

| Onde | Commit |
|---|---|
| `claude-m25-26-fluxo-espirometria-soprolife` | `bdaf7a5` |
| `origin/painel-soprolife-v01` | `bdaf7a5` |
| VPS `/opt/soprolife/soprolife-site` | `bdaf7a5` |

Commits:

```
bdaf7a5 test(m25.26): provas do fluxo e contratos de tela menos frágeis
7fa30a7 feat(m25.26): Central de Cadastros — paciente primeiro e autoexplicativa
de08407 feat(m25.26): Sair visível no cabeçalho do Command Center
ad2e3b9 feat(m25.26): barra automática na data, sem destruir data parcial
7f69577 feat(m25.26): atendimento atômico, data legível e coerência no cadastro
056d131 feat(m25.26): o 422 passa a dizer qual campo falta
```

22 arquivos, +2838 / −218.

---

## 15. Pendências

### 15.1 Falha de teste pré-existente (não introduzida por esta missão)

`test_m25_17_operacao_limpa.py::test_rubrica_real_nao_esta_versionada` falha
apontando dois PNGs da M25.21:

```
painel-soprolife/docs/m25-21/laudo-pre-assinatura-completo.png
painel-soprolife/docs/m25-21/selo-pre-assinatura.png
```

**Verificado que já falhava em `87271d2` limpo** (via `git stash`). A guarda
da M25.17 procura imagem com "assinatura" no nome e não distingue a rubrica
real da médica de uma captura sintética do harness da M25.21. Precisa de
decisão humana: renomear os arquivos, refinar a guarda, ou confirmar que as
imagens são sintéticas e liberá-las explicitamente. **Não toquei** — mexer
numa guarda de privacidade sem confirmação seria pior que a falha.

### 15.2 Exame contraditório já gravado em produção

Existe **1 exame** com `modalidade = clinica_parceira` e **nenhum parceiro**
(`local_atendimento = "Pastore Ipanema - TESTE M25.13"`). É registro de teste
da M25.13. A partir de agora essa combinação é impossível de criar, mas o
registro existente **não foi alterado** — a missão proíbe mexer em histórico.
Sugestão: arquivar pelo sinalizador da M25.17, que é o mecanismo já existente
para tirar cenário de teste da fila clínica.

### 15.3 Cadastro dos pacientes atuais

As pendências agora aparecem no cartão do paciente, mas **preenchê-las é
trabalho humano**. Vale uma passada com a Dra. Ana pelos pacientes que já têm
exame, começando pelo CPF, que é o único bloqueante da CFM.

### 15.4 Validação com os operadores

Esta missão corrigiu o que o teste de ontem expôs. **O próximo teste real com
a Dra. Ana e o operador é o que confirma se o fluxo agora se explica sozinho**
— principalmente a máscara de data no celular e o "Corrigir cadastro".

### 15.5 Fora de escopo, observado

`js/espirometria-financeiro.js` (M11, Google Sheets) mantém a lista antiga
`["Domiciliar", "Clínica", "Empresa / PCMSO", "Parceiro", "Outro"]`. É o
contrato legado do Apps Script, **não** o fluxo nativo, e não foi tocado. Se
aquele caminho for aposentado, a lista sai junto.

---

## Conclusão

**M25.26 — FLUXO SOPROLIFE SIMPLES, COERENTE E AUTOEXPLICATIVO**

O fluxo deixou de fazer perguntas que ele mesmo já sabia responder:

- o erro diz **qual** campo falta e **onde** ele está, em vez de "Payload
  inválido.";
- a data digitada sem barras **não vira mais um exame sem data** — o defeito
  silencioso que reaparecia semanas depois na emissão do laudo;
- os campos do paciente estão visíveis **sem clique intermediário**, porque
  todo atendimento pertence a uma pessoa;
- paciente e atendimento nascem **numa transação só** — sem paciente órfão;
- o que falta no cadastro aparece **no momento da escolha**, com conserto no
  lugar, preservando o exame já digitado;
- modalidade e local **pararam de competir**, e a contradição é barrada no
  cadastro com as mesmas regras que a emissão do laudo aplica;
- o valor nasce em **R$ 220,00**, editável, vindo de configuração única;
- **Sair** está visível.

Pastore correto e intocado. Financeiro histórico intacto e verificado em
produção. M25.23 e M25.24 sem regressão.
