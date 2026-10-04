# M25.24 — OPERAÇÃO MÉDICA GUIADA, HISTÓRICO ENCERRADO E PASTORE RECONCILIADA

**Data:** 11/08/2026
**Branch de trabalho:** `claude-m25-24-operacao-medica-historico-pastore`
**Branch oficial:** `painel-soprolife-v01`
**VPS:** `root@soprolife-painel-01` — `/opt/soprolife/soprolife-site`

---

## 1. HEAD INICIAL

| | |
|---|---|
| HEAD esperado | `0b85f6941d1a947d70974e11e3a522e6250654ab` |
| HEAD do worktree ao iniciar | `0b85f69` ✅ confere |
| HEAD da VPS ao iniciar | `0b85f69` ✅ confere |
| working tree (local e VPS) | limpo |
| Alembic head aplicada | `e7c4b03a91df` (M25.20) |
| health | `{"status":"ok","banco":"ok"}` |

---

## 2. BACKUP

Tudo em `/opt/soprolife/backups/m25-24/`, feito **antes** de qualquer escrita.

| arquivo | prova |
|---|---|
| `m15-antes-m25-24.dump` | `pg_dump -Fc`, 291.317 bytes |
| validação do dump | `pg_restore -l` → **385 objetos** legíveis |
| sha256 do dump | `e828a39332515c1dce6c382dc76ae2d09ee0d645fc614a30f8afb039a71bf82c` |
| `m15.env.bak` | modo 600, conteúdo **nunca impresso** |
| `command-center-config.local.json.bak` | modo 600, conteúdo **nunca impresso** |

**Armadilha encontrada:** `M15_DATABASE_URL` é uma URL do SQLAlchemy
(`postgresql+psycopg://`). O `pg_dump` não entende o sufixo do driver — ele
ignora a URL inteira e tenta o socket local como o usuário do shell, falhando
com `role "root" does not exist`. É preciso remover `+psycopg` antes.

---

## 3. TOOLTIPS E AJUDA CONTEXTUAL (FASE A)

### 3.1 Por que não é `title=""`

Quatro motivos, e o primeiro sozinho já decide:

1. **`title` não abre por toque.** No iPhone da médica ele simplesmente não
   existe. A ajuda precisava funcionar exatamente ali.
2. Não abre por teclado e não fecha com Esc.
3. Tempo de aparição, tamanho e quebra de linha são do sistema operacional —
   textos de três linhas viram uma tira ilegível.
4. Leitores de tela tratam `title` de forma inconsistente, e vários o ignoram
   quando o elemento já tem nome acessível.

**O componente:** um `<button>` real (foco natural na ordem de Tab,
Enter/Espaço nativos, alvo de toque de 28 px) seguido de um irmão
`role="tooltip"`. O botão aponta para a bolha por `aria-describedby` — assim a
explicação é anunciada ao pousar no botão, mesmo antes de a bolha aparecer, e
não depende de hover em lugar nenhum.

| entrada | comportamento |
|---|---|
| hover | abre / fecha (sair para dentro da bolha **não** fecha — o texto precisa poder ser lido) |
| foco por teclado | abre; `blur` fecha |
| toque / clique | alterna; tocar fora fecha |
| `Escape` | fecha e **devolve o foco** a quem estava com ele |

**Não depende só de cor:** no estado aberto o ícone inverte (fundo cheio em
navy), além de mudar de tom. **Não depende só de hover:** a abertura é sempre
atributo (`hidden` + `.is-open`), nunca `:hover` puro no CSS.

**O `?` intercepta antes de tudo.** Ele mora dentro de linhas e botões
clicáveis; sem interromper primeiro, tocar nele abriria o documento ou
dispararia um download. É o primeiro `if` de `handleClick`, com
`stopPropagation()`.

**Nenhum framework adicionado.**

### 3.2 Privacidade dos tooltips

Os textos vivem num catálogo constante (`HELP`, `STATUS_HELP`) e são
**literais** — não há uma única interpolação de estado neles. Teste
automatizado varre o catálogo e falha se aparecer `${`, `state.`, `patient`,
`full_name`, `valor` ou `R$`. O vazamento é impossível por construção, e não
por revisão de texto.

---

## 4. SEMÂNTICA REAL DE CADA STATUS

Auditada no código, não no rótulo.

| valor | o que inclui | como ENTRA | como SAI | próxima ação da médica |
|---|---|---|---|---|
| `atribuido` | laudo criado com o PDF do equipamento anexado e médica atribuída; nenhum texto clínico escrito | `POST /laudos` (operação anexa a MIR e atribui) ou `POST /{id}/reatribuir` | primeira prévia gerada, ou anotação do fluxo antigo | abrir e laudar |
| `em_elaboracao` | existe ao menos uma prévia; o texto ainda é editável | `POST /{id}/laudo/previa` ou `POST /{id}/compor` | concluir o laudo, ou finalizar revisão | revisar e concluir |
| `assinatura_pendente` | fluxo antigo (M24C/M25.8): conteúdo congelado com marcador, esperando o PDF assinado voltar | `POST /{id}/preparar-assinatura` ou `/finalizar-revisao` | upload aceito em `POST /laudos/lote/enviar` | assinar por fora e devolver |
| `assinado` | o PDF assinado voltou e passou na conferência | `POST /laudos/lote/enviar` validado, ou retorno do VIDaaS/IntegraICP | — (estado final) | nada |
| `liberado` | **fluxo em uso hoje**: laudo clinicamente concluído, com código de validação; `signed_at` é NULO por constraint | `POST /{id}/assinar-e-liberar` | recebimento do assinado externo | baixar em "Assinatura externa", assinar, devolver |

### 4.1 Correção de honestidade — "Assinados com ICP-Brasil"

**O rótulo afirmava exatamente a parte que o sistema não faz.**

O que `verify_signed_pdf` → `validate_pades` **confere** ao receber o PDF:

- o marcador bate (mesmo laudo, mesma versão, mesmo hash de conteúdo);
- o assinado é o preparado **+ apêndice** — não foi reimpresso nem reexportado;
- existe campo de assinatura PAdES;
- o CMS SignedData é válido e o `messageDigest` bate;
- a assinatura confere contra a **chave pública do próprio certificado**;
- o titular é o mesmo certificado já vinculado àquela médica.

O que **ninguém verifica**:

- que o certificado foi emitido por uma AC da ICP-Brasil (não há âncora de
  confiança, não há validação de cadeia);
- revogação (CRL/OCSP);
- carimbo de tempo.

| onde | antes | depois |
|---|---|---|
| filtro de estados | "Assinados com ICP-Brasil" | **"Assinados — assinatura conferida"** |
| chip de status | "Assinado com ICP-Brasil" | **"Assinado — assinatura conferida"** |
| resultado do lote | "Assinatura ICP-Brasil validada" | **"Assinatura digital conferida"** |

A ajuda do estado descreve o alcance real, com a negativa explícita: *"O
sistema NÃO verifica a cadeia ICP-Brasil nem a revogação do certificado."*

O ramo do **VIDaaS/IntegraICP (M25.7) é outra coisa** e continua podendo
afirmar ICP-Brasil — lá quem assina é o HSM da AC. Ele segue indisponível em
produção, e a própria tela já diz isso.

**Um guard rail do projeto moldou a redação final.** A primeira versão do
rótulo era "Assinado digitalmente — assinatura conferida", mas
`test-m24a-report-workflow.js` barra a fórmula "assinado digitalmente" em toda
a tela, para que a liberação institucional nunca seja vendida como assinatura.
O guard é mais valioso que a minha frase; o rótulo foi reescrito.

### 4.2 O `<option>` nativo e a solução adotada

Não há tooltip confiável dentro de um `<select>` em navegador nenhum, e no iOS
o seletor vira uma roda do sistema onde nada além do rótulo aparece. Portanto:

- o `<select>` ficou **simples**;
- um `?` ao lado do campo explica todos os estados de uma vez;
- **uma frase contextual, sempre visível, abaixo do campo** explica o estado
  escolhido — ajuda que não exige descobrir que existe um `?`;
- o `?` fica **fora** do `<label>`: dentro dele, cada toque no ícone abriria o
  seletor junto.

### 4.3 "Como funciona"

Bloco recolhível no topo, seis passos. O passo 6 diz **"A SoproLife cuida da
etapa administrativa de entrega ao paciente"** — a médica responde pelo ato
médico e pela assinatura, não pelo envio.

**Nasce recolhido para quem já conhece o fluxo**, derivado da própria fila
(existe algum laudo além de "pendente de laudo"?). A preferência **não** vai
para armazenamento do navegador: `test-m24a-report-workflow.js` proíbe
`localStorage.setItem`/`sessionStorage` em todo o fluxo de laudos, e essa trava
de privacidade vale mais que a conveniência de lembrar entre recargas.

### 4.4 Controles cobertos

`Assinatura externa` · `contador` · `Selecionar todos` · `Baixar selecionados` ·
`Selecionar PDFs assinados` · `Meus laudos` · `filtro Status` ·
`Exame técnico (MIR)` · `Laudo SoproLife` · `Adendo` · `Documento corretivo` ·
`Motivo técnico da correção` · marca `corrigido`.

Nenhuma regra clínica foi alterada para encaixar texto.

---

## 5. LISTA CONGELADA DE EXAMES PENDENTES (FASE B1)

Consulta a produção espelhando exatamente
`/laudos/exames?somente_sem_laudo=true`, **antes de qualquer escrita**.

**15 exames.** Sem CPF, telefone, e-mail ou endereço.

| ESP | pessoa | data | status | modalidade | parceiro | unidade | BD |
|---|---|---|---|---|---|---|---|
| ESP-000015 | PES-000027 | 15/07/2026 | Realizado | cowork | — | — | nulo |
| ESP-000014 | PES-000026 | 18/07/2026 | Liberado | clinica_parceira | Pastore | UNI-000002 | nulo |
| ESP-000013 | PES-000025 | 14/07/2026 | Liberado | clinica_parceira | Pastore | UNI-000002 | nulo |
| ESP-000012 | PES-000023 | 10/07/2026 | Realizado | — | — | — | nulo |
| ESP-000011 | PES-000011 | 02/07/2026 | Exame realizado | — | — | — | nulo |
| ESP-000010 | PES-000010 | 01/07/2026 | Exame realizado | — | — | — | nulo |
| ESP-000009 | PES-000009 | 01/06/2026 | Exame realizado | — | — | — | nulo |
| ESP-000008 | PES-000008 | 01/06/2026 | Exame realizado | — | — | — | nulo |
| ESP-000007 | PES-000007 | 01/06/2026 | Exame realizado | — | — | — | nulo |
| ESP-000006 | PES-000006 | 01/06/2026 | Exame realizado | — | — | — | nulo |
| ESP-000005 | PES-000005 | 01/06/2026 | Exame realizado | — | — | — | nulo |
| ESP-000004 | PES-000004 | 01/06/2026 | Exame realizado | — | — | — | nulo |
| ESP-000003 | PES-000001 | 01/06/2026 | Exame realizado | — | — | — | nulo |
| ESP-000002 | PES-000003 | 01/06/2026 | Exame realizado | — | — | — | nulo |
| ESP-000001 | PES-000002 | 01/06/2026 | Exame realizado | — | — | — | nulo |

**Achado que reorganizou as fases B e C:** `ESP-000013` e `ESP-000014` estão
nesta lista porque **não têm laudo nenhum no sistema**. Dos cinco Pastore, só
`ESP-000017`, `ESP-000018` e `ESP-000019` tiveram laudo produzido no Centro de
Comando. Isso não muda o resultado pedido — muda qual motivo estruturado cabe a
cada um.

Nenhuma regra por data foi criada. Um exame novo que entre amanhã **não** é
afetado: o encerramento é marcado exame a exame.

---

## 6. MECANISMO DE ENCERRAMENTO HISTÓRICO

Procurei um mecanismo adequado antes de criar um. Não existia: `people.arquivado`
(M25.17) arquiva a **pessoa inteira**, o que esconderia também os exames futuros
de um paciente real que continua sendo atendido.

### 6.1 As três saídas erradas, e por que foram descartadas

1. **Apagar o exame** — destrói prontuário e a rastreabilidade do atendimento.
2. **Fabricar um laudo** — faria o sistema afirmar que produziu um documento
   médico que nunca produziu. É falsificação, mesmo com boa intenção.
3. **Regra por data** — silenciaria sozinha exames futuros que ninguém
   autorizou silenciar.

### 6.2 O que foi implementado

Quatro colunas em `spirometry_exams`, migration **`a2f6c81d4b73`** (aditiva;
o padrão NULL é exatamente o comportamento de hoje):

| campo | |
|---|---|
| `encerramento_motivo` | catálogo **fechado** de 4 motivos |
| `encerrado_em` | data da decisão |
| `encerrado_por_user_id` | FK para `users` |
| `encerramento_observacao` | o caso concreto, máx. 200 caracteres |

`CHECK encerramento_com_evidencia`: ou os quatro estão vazios, ou os quatro
estão preenchidos. Não existe "encerrado sem motivo" nem "encerrado por
ninguém" — nem por SQL direto (há teste que prova isso).

**Separado do status clínico.** `status` descreve o **ato** (o exame foi
realizado, liberado) e é editado por outros motivos; reaproveitá-lo para dizer
"não me mostre mais" faria a fila depender de um campo que muda sozinho.

**Motivos estruturados:**

| chave | rótulo |
|---|---|
| `laudo_externo_ja_entregue` | Laudo externo já entregue — histórico anterior à operação na plataforma |
| `laudo_externo_e_teste_do_fluxo` | Histórico — laudo já entregue externamente; os laudos deste exame no Centro de Comando são teste do fluxo após a entrega externa |
| `duplicidade_operacional` | Registro operacional duplicado |
| `atendimento_cancelado` | Atendimento cancelado |

**Superfícies:** `POST /laudos/exames/{esp}/encerramento` (operacional),
`POST /laudos/exames/{esp}/reabertura` (**admin**),
`GET /laudos/exames/encerrados`, `GET /laudos/exames/motivos-encerramento`.
CLI `m15 encerrar-exame-historico` e `m15 reabrir-exame`, **dry-run por
padrão**.

A assimetria de papel é deliberada: **tirar da fila é rotina de operação;
devolver trabalho clínico é decisão de gestão.**

### 6.3 Comportamento das filas

Exame encerrado sai das **cinco filas de trabalho** e de nenhuma outra:

| fila | |
|---|---|
| `/laudos/meus` (médica) | sai — inclusive em "Todos" e em **cada** filtro de estado |
| `/laudos/exames?somente_sem_laudo=true` | sai |
| `/laudos` (acompanhamento operacional) | sai do padrão |
| `/laudos/assinatura-externa/pendentes` | sai |
| `/laudos/assinatura-externa/fila` (entrega) | sai |

Continua localizável em **"Históricos encerrados"** e na busca por código
exato (`incluir_encerrados=true`). O "Todos" da administração **não** mistura
pendência com histórico: o padrão é a fila ativa, `somente_encerrados=true` dá
a visão própria, `incluir_encerrados=true` junta as duas — e toda linha carrega
o carimbo `encerramento`.

### 6.4 Defeito encontrado na verificação em produção, e corrigido

"Históricos encerrados" dizia **19** para 18 exames encerrados. O `outerjoin`
com `report_documents` multiplica a linha do exame, e o `ESP-000019` tem dois
laudos. A tela mostraria o mesmo paciente duas vezes, cada uma com metade da
evidência.

Corrigido em `912439f`: é lista de **exames** — um exame, uma linha, com os
laudos agregados dentro (`laudos: [{report_code, report_status, is_corrective}]`).
Teste `test_exame_com_dois_laudos_conta_como_UM_historico` trava a regressão.

---

## 7. LAUDOS DE TESTE PRESERVADOS (FASE C)

Os três exames Pastore com laudo produzido no Centro de Comando foram
encerrados com `laudo_externo_e_teste_do_fluxo`:

| ESP | laudos preservados | |
|---|---|---|
| ESP-000017 | LAU-000002 | `liberado` |
| ESP-000018 | LAU-000003 | `liberado` |
| ESP-000019 | LAU-000004 + **LAU-000005** | ambos `liberado`; o segundo é o **corretivo** |

**Nada foi apagado, alterado ou regenerado.** Conclusão, PDF, hash e versão
intactos — ver a prova no item 9.4. O encerramento é **metadado operacional**;
não entrou uma única letra dentro de PDF clínico.

Observação gravada em cada um:

> *Paciente ja recebeu o laudo pela Pastore; os laudos deste exame no Centro
> de Comando sao teste do fluxo. Autorizado pelo gestor em 11/08/2026.*

---

## 8. RECONCILIAÇÃO DOS CINCO PASTORE (FASE D)

Parceiro canônico **CLI-000002 "Pastore"**, unidade ativa única **UNI-000002
"Pastore Ipanema"** — resolvidos pela API, não assumidos.

| ESP | data | BD | repasse documentado |
|---|---|---|---|
| ESP-000013 | 14/07/2026 | true | R$ 109,50 |
| ESP-000014 | 18/07/2026 | true | R$ 109,50 |
| ESP-000019 | 01/08/2026 | true | R$ 109,50 |
| ESP-000017 | 04/08/2026 | true | R$ 109,50 |
| ESP-000018 | 04/08/2026 | true | R$ 109,50 |

### 8.1 Correção do broncodilatador (D2)

`PATCH /espirometrias/{id}` altera o campo mas **não tem onde registrar de onde
veio a correção**. Num exame histórico, mudar `broncodilatador` de nulo para
`true` é uma afirmação sobre o que foi feito no paciente — sem a evidência
gravada junto, ninguém consegue depois distinguir correção documentada de
palpite.

Foi criada a CLI `m15 corrigir-broncodilatador`, que **exige `--evidencia`** e a
grava na auditoria (`espirometria.broncodilatador_corrigido_por_evidencia`).

O dry-run mostrou, antes de escrever:

| ESP | valor atual | alvo | muda? |
|---|---|---|---|
| ESP-000013 | `null` | `true` | **sim** |
| ESP-000014 | `null` | `true` | **sim** |
| ESP-000017 | `true` | `true` | não |
| ESP-000018 | `true` | `true` | não |
| ESP-000019 | `true` | `true` | não |

Resultado: `corrigidos: [ESP-000013, ESP-000014]`,
`ja_estavam_corretos: [ESP-000017, ESP-000018, ESP-000019]`.
**Os três que já estavam corretos não foram reescritos e não geraram
auditoria.**

Evidência registrada: *"Extrato Pastore fornecido pelo gestor em 11/08/2026"*.
Nenhum outro dado clínico foi tocado.

### 8.2 Fechamentos (D3)

Criados pelo mecanismo canônico (`POST /pastore/fechamentos`), com os itens
resolvidos **pelo servidor** a partir de parceiro + unidade + competência.

| competência | itens | exames | valor documentado | estado |
|---|---|---|---|---|
| 2026-07 | **2** | ESP-000013, ESP-000014 | R$ 219,00 | `a_receber` |
| 2026-08 | **3** | ESP-000017, ESP-000018, ESP-000019 | R$ 328,50 | `a_receber` |
| **total** | **5** | | **R$ 547,50** | |

**Sobre a escolha do estado.** Os estados possíveis são `aberto` → `incluido` →
`a_receber` → `enviado` → `recebido`. `incluido` não aceita valor;
`enviado` exigiria uma `data_envio` que não existe; `recebido` está proibido.
**`a_receber` — "A receber da Pastore" — é o estado menos avançado que registra
o valor documentado sem declarar pagamento**, e é exatamente o que o extrato
diz: o repasse é devido, e o recibo está em branco. Não houve necessidade de
forçar semântica; **não há lacuna a registrar aqui.**

Observação gravada em cada fechamento:

> *Extrato Pastore fornecido pelo gestor em 11/08/2026. Valor documentado de
> repasse: N x R$ 109,50. Sem data e sem valor de recibo no extrato — nao ha
> recebimento comprovado.*

### 8.3 Nenhuma regra futura de preço criada

R$ 109,50 é **evidência destes cinco exames**, gravada na observação dos
fechamentos. Nenhuma tabela de preço, nenhum `modelo_repasse`, nenhuma
inferência sobre exame sem BD, sobre Pastore Assist ou sobre vigência futura.
A parceria segue como estava.

---

## 9. PROVAS

### 9.1 Nenhum recebimento criado (D1)

| verificação | resultado |
|---|---|
| `POST /pastore/fechamentos/{id}/receber` chamado? | **não** — é o único caminho que cria `FinancialEntry` |
| `financial_entries` com `partner_settlement_id` | **0** |
| total de `financial_entries` | **13** (o mesmo de antes) |
| fechamentos com `status = recebido` | **0** |
| `recebimento` nos dois fechamentos | `null` |
| `data_envio` | `null` |

Nenhuma data de recebimento, forma de pagamento ou receita individual por ESP
Pastore foi inventada.

### 9.2 Financeiro histórico intacto (Fase E)

| | antes | depois |
|---|---|---|
| lançamentos de receita de espirometria | **13** | **13** |
| soma | **R$ 3.044,79** | **R$ 3.044,79** |

`LAN-000001` a `LAN-000013`, todos `Recebido`, categoria `Espirometria`.
Nada redistribuído, recalculado, individualizado, alterado de competência ou
excluído. O rateio médio histórico segue exatamente como o gestor o deixou.

### 9.3 Estado das filas

| fila | antes | depois |
|---|---|---|
| "Espirometrias recentes sem laudo" | 15 | **0** |
| "Aguardando assinatura qualificada" (Ana) | 4 (LAU-000002/3/4/5) | **0** |
| "Meus laudos" (Ana) | — | **0** |
| Fila de entrega (administração) | — | **0** |
| **"Históricos encerrados"** | 0 | **18**, sem duplicatas |

Encerrados: 15 por `laudo_externo_ja_entregue` + 3 por
`laudo_externo_e_teste_do_fluxo`.

### 9.4 Nenhum byte clínico alterado

Prova por comparação contra o **dump feito antes de qualquer escrita**,
extraindo os dados sem criar banco algum:

```
ANTES   — versões: 17 | md5 dos sha256: a17f8aef891b95a46b3f4018c4a83329
DEPOIS  — versões: 17 | md5 dos sha256: a17f8aef891b95a46b3f4018c4a83329
```

Mesma quantidade de versões, **mesmo hash agregado de todos os `sha256`**.
Nenhum PDF, versão, hash ou status de laudo foi tocado.

### 9.5 Idempotência

| prova | resultado |
|---|---|
| reexecutar o lote de encerramento | `encerrados: []`, `ja_estavam_encerrados: [...]` — data e autoria da decisão original **preservadas** |
| encerrar com motivo divergente | **recusado**: `exame_ja_encerrado_com_outro_motivo` — as duas decisões precisam ficar na trilha, e não a segunda apagar a primeira |
| criar fechamento duplicado | **409** `fechamento_mensal_duplicado` |
| exame em dois fechamentos | impedido por `UNIQUE` em `partner_settlement_items.spirometry_exam_id` |
| `--para true` em exame já `true` | não escreve, não audita |
| reabrir exame já aberto | `alterado: false` |

### 9.6 Restaurabilidade

Provada no cadastro **sintético** `ESP-TF0001` — nenhum registro real foi
reaberto para o teste:

```
estado inicial   ESP-TF0001 | sem encerramento
encerra          {"encerrados": ["ESP-TF0001"]}
REABRE (gestor)  {"reabertos": ["ESP-TF0001"]}
estado final     ESP-TF0001 | - | - | - |     (os quatro campos limpos)
trilha           exame_reaberto_para_laudo
                 exame_encerrado_operacionalmente
```

Reabrir por conta `operacional` devolve **403** (teste automatizado).

### 9.7 Gate da M25.23 não regrediu

| caminho | sem sessão |
|---|---|
| `/painel-soprolife/index.html` | 200 — **casca mínima de 5.596 bytes**, com 0 ocorrências de `reportWorkflowRoot`, `Financeiro`, `CRM` e `nav-item` |
| `/painel-soprolife/js/report-workflow.js` | **401** |
| `/painel-soprolife/api/m15/laudos` | **401** |
| `/painel-soprolife/data-private/…local.json` | **404** |
| `/painel-soprolife/login.html` | 200 |

### 9.8 Auditoria

A trilha ganhou três ações: `exame_encerrado_operacionalmente`,
`exame_reaberto_para_laudo` e
`espirometria.broncodilatador_corrigido_por_evidencia`. Todas carregam apenas
código institucional, motivo fechado e a observação operacional — **nenhum nome
de paciente** (teste automatizado prova).

---

## 10. TESTES

**Suíte completa: 1259 passaram, 30 puladas, 13 falharam.** As 13 são
**exatamente as mesmas do baseline medido antes de eu tocar em qualquer coisa**:

- 12 em `test_live_multisheet_reader.py` — `googleapiclient` ausente no venv
  local (ambiental);
- 1 em `test_m25_17_operacao_limpa.py::test_rubrica_real_nao_esta_versionada` —
  sinaliza dois PNGs versionados em `docs/m25-21/`, commitados na M25.21 em
  `1604ba1`, **antes desta missão**.

**44 testes novos** em `test_m25_24_operacao_medica_historico.py`, cobrindo:
preservação de exame/laudo/versões/hashes, saída das cinco filas,
localizabilidade, reversibilidade e papel exigido, idempotência nos quatro
eixos, recusas explícitas, o serviço isolado, honestidade dos rótulos,
acessibilidade do componente de ajuda, privacidade do catálogo, e a CLI com
dry-run.

**Dois testes existentes atualizados**, ambos consequência direta e esperada:

| teste | mudança |
|---|---|
| `test_migrations.py` | head do Alembic `e7c4b03a91df` → `a2f6c81d4b73` (o que ele prova é continuar existindo **uma** head) |
| `test_m24a_frontend_contract.py` | conjunto fechado de chaves da fila ganhou `encerramento` — metadado de fila, sem identidade, `None` em toda linha ativa |

**Quality gate:** ✅ passou em todos os commits.

**Um achado do próprio quality gate:** ele barrou duas coisas minhas, e as duas
barreiras estavam certas — `localStorage.setItem` no fluxo de laudos, e a
fórmula "assinado digitalmente" na tela. Ambas foram contornadas respeitando o
guard, não enfraquecendo-o.

---

## 11. DEPLOY

| passo | |
|---|---|
| commits | 5, pequenos e coerentes |
| push do branch de trabalho | ok |
| `painel-soprolife-v01` | **fast-forward** `0b85f69` → `22f8c4a` → `912439f` |
| VPS `git fetch` + `merge --ff-only` | ok, working tree limpo |
| migration | `e7c4b03a91df` → **`a2f6c81d4b73`** |
| restart | `soprolife-m15-api`, `soprolife-painel-loopback` **e** `soprolife-painel` |
| health | `{"status":"ok","banco":"ok"}` |
| serviços | os três `active` |

Sem `reset --hard`, sem force push, sem `force-with-lease`, sem `DROP`, sem
`DELETE` em massa. Todas as escritas foram específicas e auditáveis.

**Nota de operação:** o branch local `painel-soprolife-v01` do worktree
`~/soprolife-site` está em `bbd9ebc`, um commit antigo da mesma linhagem — ele
está apenas desatualizado. O fast-forward foi feito por push direto da ponta do
branch de trabalho para o remoto. **Aquele worktree precisa de um `git pull`.**

**Cache-busting:** o selo dos assets subiu de `2026081002` para `2026081102`.
Sem isso, o navegador da médica continuaria servindo o JS/CSS antigos e nem a
ajuda contextual nem o rótulo corrigido apareceriam para quem já usou o painel.

---

## 12. SMOKE

Verificado pela API, com token de vida curta das contas reais (somente
leituras; nenhum conteúdo clínico foi alterado durante o smoke):

| verificação | resultado |
|---|---|
| médica — fila de laudos | 0 itens |
| médica — aguardando assinatura | `{"total": 0, "laudos": []}` |
| administração — recentes sem laudo | 0 |
| administração — históricos encerrados | 18, sem duplicatas, com os laudos agregados |
| administração — fila de entrega | 0 |
| catálogo de motivos | os 4 do serviço, batendo com o schema |
| ajuda contextual no arquivo servido | 16 ocorrências no JS, 4 no CSS |

---

## 13. apiToken (FASE F)

**Não foi possível rotacionar end-to-end daqui, e a razão é estrutural.**

| pergunta | resposta |
|---|---|
| quem **emite** | o humano, em *Propriedades do Script* do Google Apps Script (`API_TOKEN`). **Nenhum código deste repositório gera esse valor.** |
| quem **consome** | **ninguém, desde a M23.** `command-center-local-server.py` tem **zero** ocorrências de `urllib.request` e de `apiToken`; a rota antiga responde **410** apontando o PostgreSQL como fonte canônica. Nenhum processo tinha o arquivo aberto. |
| ainda é necessário | **não**, para o painel |
| onde o valor legítimo deve residir | apenas nas Propriedades do Script |

**Ação executada** (autorizada por você): a cópia local em
`painel-soprolife/data-private/command-center-config.local.json` foi
**apagada** da VPS, depois de backup em modo 600 com sha256 conferido idêntico.
Verificado em seguida: painel de pé, health ok, rota legada ainda 410,
`/api/command-center/status` seguindo `{"decommissioned": true}`.

Nenhum token — antigo ou novo — foi impresso, gravado no Git ou incluído neste
relatório.

### Ação manual ainda necessária — sua

**Rotacionar ou remover `API_TOKEN` nas Propriedades do Script do Apps
Script.** Depende de console externo, ao qual não tenho acesso. Como o painel
não consome mais esse token, **remover é mais seguro que rotacionar**: um token
que não existe não vaza. Se o Web App ainda for usado por alguma planilha fora
do painel, rotacione em vez de remover.

---

## 14. HEAD FINAL

| | |
|---|---|
| **HEAD do worktree** | `912439f5aa606fe338ff5e5f9df56cf35bd8f5bd` |
| **HEAD de `painel-soprolife-v01`** | `912439f` |
| **HEAD da VPS** | `912439f` — working tree **limpo** |
| **Alembic head** | `a2f6c81d4b73` |
| health / banco | ok |
| serviços | os três `active` |

**Commits:**

```
912439f fix(m25.24): exame com dois laudos contava como dois históricos
22f8c4a chore(m25.24): sobe o selo de cache dos assets do fluxo de laudos
c30167c feat(m25.24): correção de broncodilatador exige a evidência que a originou
e4c0dde feat(m25.24): exame encerrado como histórico sai da fila sem ser apagado
20c5991 feat(m25.24): a área médica passa a se explicar sozinha
```

---

## 15. PENDÊNCIAS

### 15.1 Rotacionar `API_TOKEN` no Apps Script — sua, e ainda aberta

Ver item 13. Console externo. **Recomendação: remover, não rotacionar.**

### 15.2 Teste visual humano da conta da médica

Provei os contratos de API e a estrutura do DOM/CSS por teste automatizado.
**O que falta é o seu olho na tela real**, com a conta da Dra. Ana:

- a bolha de ajuda abre por toque no iPhone e fecha ao tocar fora;
- o `?` do filtro Status e a frase contextual abaixo dele;
- "Como funciona" — para ela, que já laudou, deve nascer **recolhido**;
- a bancada M25.21 intacta;
- as duas larguras: **430** (celular) e **1920** (desktop).

O harness visual de `nucleo-m15/tests/visual/` continua disponível, mas ele usa
dublê de dados fictícios — não substitui o teste com a conta real.

### 15.3 A fila da médica está vazia

Consequência esperada e correta desta missão: os 18 exames eram todos
históricos. **O primeiro exame novo que a operação anexar aparece
normalmente** — nenhuma regra por data foi criada. Vale conferir isso no
próximo atendimento real.

### 15.4 Os dois PNGs versionados em `docs/m25-21/`

`test_rubrica_real_nao_esta_versionada` falha por causa deles desde a M25.21
(`1604ba1`), antes desta missão. São capturas de laudo sintético, não rubrica
real — mas o teste é uma trava de segurança e falhar por rotina ensina a
ignorá-lo. Merece uma etapa própria: mover para fora do Git ou tornar a regra
mais precisa.

### 15.5 Decisão comercial Pastore continua aberta

`modelo_repasse` segue indefinido, e nada nesta missão o inferiu. Preço/repasse
sem BD, vigência de R$ 109,50 e Pastore Assist continuam sem declaração.

### 15.6 O branch local do worktree `~/soprolife-site`

Está em `bbd9ebc`, desatualizado. Precisa de `git pull` antes do próximo
trabalho ali.

---

## CONCLUSÃO

**M25.24 — OPERAÇÃO MÉDICA GUIADA, HISTÓRICO ENCERRADO E PASTORE RECONCILIADA**

Provado:

- ajuda contextual acessível por hover, foco, toque e Escape, sem `title` e sem
  framework, com textos estáticos que não podem vazar dado;
- o rótulo que afirmava validação ICP-Brasil inexistente foi corrigido nos três
  lugares onde aparecia;
- 18 exames históricos saíram das cinco filas de trabalho **sem que um único
  byte de exame, laudo, versão ou hash fosse alterado** — provado por
  comparação de hash agregado contra o dump anterior;
- os cinco Pastore reconciliados: BD corrigido com evidência onde divergia,
  fechamentos de julho (2 itens, R$ 219,00) e agosto (3 itens, R$ 328,50) em
  "A receber", **sem nenhum recebimento criado**;
- financeiro histórico congelado: **13 lançamentos, R$ 3.044,79, antes e
  depois**;
- idempotente nos quatro eixos e reversível por gestor;
- gate da M25.23 e bancada M25.21 intactos.

Falta o seu teste visual com a conta da médica e a rotação do `API_TOKEN` no
console do Apps Script.
