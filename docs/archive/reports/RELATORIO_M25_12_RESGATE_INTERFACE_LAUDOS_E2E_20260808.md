# Relatório M25.12 — Resgate da interface clínica de laudos + fluxo fictício ponta a ponta

Data: 2026-08-08
Branch: `claude-m25-12-resgate-laudos-e2e`
Base: `b5f8a8064d4b3c1378f43681c73e88e47942b84a`
Commit criado: `029f7da`

> **Estado desta etapa:**
> **IMPLEMENTAÇÃO CONCLUÍDA ATÉ O GATE DE LOGIN MANUAL — EVIDÊNCIA VISUAL FINAL PENDENTE**
>
> O fluxo completo está implementado, corrigido e **comprovado no navegador
> de verdade**, com 13 capturas de tela da interface real. Mas essa
> comprovação foi feita contra a **instância local**, não contra a produção:
> desta máquina não há SSH para a VPS nem credencial de nenhuma conta de
> produção — e eu não vou pedir, redefinir nem procurar senha. O deploy e a
> repetição do roteiro em produção dependem de você. Detalhes na seção 14.
>
> **O código já está integrado no GitHub** (`painel-soprolife-v01`, por
> fast-forward — seção 17-A). **A VPS continua em `b5f8a80`**, servindo a
> versão antiga: o deploy ainda não foi feito.

---

# PARTE 1 — A CAUSA RAIZ DO `ESP-TF0001`

## 1. `ESP-TF0001` nunca existiu — e não podia existir

Você não digitou "errado" no sentido de trocar um dígito. Digitou um código
que **este sistema é incapaz de emitir**.

Todo código público sai de um único lugar, `app/ids.py`:

```python
def format_public_code(prefix: str, value: int) -> str:
    return f"{prefix}-{value:06d}"
```

Prefixo + **seis dígitos**, sempre. Não existe caminho no código que produza
letras depois do hífen. `ESP-TF0001` é estruturalmente impossível.

**De onde veio o "TF", então?** Do laudo `LAU-TF0001`, citado nos relatórios
M25.9 e M25.10 — resíduo *append-only* do teste de fumaça daquela etapa,
inserido à mão fora do alocador de códigos. Vendo `LAU-TF0001` na lista
operacional, a conclusão natural é que o exame correspondente seria
`ESP-TF0001`. Não é: `LAU-` identifica o **laudo**, `ESP-` identifica o
**exame**, e os dois têm numeração independente.

## 2. Onde o exame está no banco de produção — o que pude e o que não pude apurar

| Pergunta | Resposta |
| --- | --- |
| `ESP-TF0001` existe em produção? | **Não pude consultar o banco** (seção 14). Mas é impossível pelo formato, salvo inserção manual — e nenhum relatório registra uma. |
| Em qual tabela estaria? | `spirometry_exams`, coluna `public_code` (única, `String(12)`) |
| Qual endpoint o frontend usa? | `GET /api/m15/espirometrias?public_code=…` |
| Esse endpoint aceita código institucional? | **Sim**, com igualdade exata e `.strip().upper()` (`operations.py:188-192`) |
| Frontend e backend usam campos diferentes? | **Não.** Ambos usam `public_code`. |
| Status HTTP real para código inexistente | **200 com lista vazia** — ausência, não erro |
| Permissões do administrador | `admin` implica `operacional` implica `leitura` — a busca é permitida |
| Filtro de tipo/status na busca | **Nenhum.** Qualquer espirometria cadastrada é localizável. |
| O exame precisa existir no CRM Espirometria? | **Sim.** O laudo é criado *sobre* um exame existente. |
| A API de produção está no banco certo? | `GET /painel-soprolife/api/m15/health` → `ambiente: prod`, `banco: ok` |

## 3. A falha SILENCIOSA — esta é a correção principal

Mesmo com o código certo em mãos, a tela tinha um defeito que sozinho explica
"o sistema não fez nada". O campo era assim:

```html
<input id="reportExamCode" name="exam_code" autocomplete="off"
       placeholder="ESP-000001" pattern="ESP-[0-9]{1,9}" required>
```

O atributo `pattern` aceita **apenas dígitos**. Ao digitar `ESP-TF0001` e
clicar em "Localizar exame", a **validação nativa do navegador aborta o
submit**:

- `locateExam()` **nunca é chamada**;
- **nenhuma requisição sai**;
- **nenhuma mensagem da aplicação aparece**;
- o formulário de anexar o PDF **nunca é montado** (ele só existe quando
  `state.locatedExam` está preenchido).

O único retorno era um balão nativo do Chrome ("Corresponda ao formato
solicitado"), que some sozinho e passa despercebido. **Falha silenciosa por
construção.**

Confirmado no arquivo **efetivamente servido pela produção**, não no código
local:

```
GET https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/js/report-workflow.js?v=2026080802
    → HTTP 200, 103.228 bytes
    → pattern="ESP-[0-9]{1,9}"          PRESENTE
    → EXAM_CODE_RE = /^ESP-\d{1,9}$/i   PRESENTE
    → cmp com o HEAD local:             IDÊNTICO
```

## 4. Correção aplicada

| Arquivo | Correção |
| --- | --- |
| `js/report-workflow.js` | `pattern` removido; formulário passa a `novalidate`. Quem recusa agora é a aplicação, não o navegador. |
| `js/report-workflow.js` | `renderExamLocator()` — bloco de resultado **fixo na tela**, com cinco desfechos nomeados |
| `js/report-workflow.js` | `locateExam()` reescrita com ramos explícitos e status HTTP real no erro de API |
| `js/report-workflow.js` | `renderRecentExams()` — lista de espirometrias sem laudo, clicáveis |
| `js/report-workflow.js` | `/espirometrias?tamanho=50` carregado junto com a área operacional |
| `js/report-workflow.js` | falha de upload também deixa mensagem fixa, preservando o exame localizado |
| `css/report-workflow.css` | `.report-locate-feedback` (erro/aviso/ok) e `.report-exam-pick` |

**Os cinco desfechos, cada um com texto próprio:**

| Situação | O que a tela diz |
| --- | --- |
| Campo vazio | "Informe um código — digite ou escolha um da lista abaixo." |
| Formato recusado | "**Formato não reconhecido: ESP-TF0001.** Os códigos são emitidos pelo sistema e não contêm letras depois do hífen. Se você viu um código começando com LAU-, ele identifica o laudo, não o exame — escolha o exame na lista abaixo." |
| Não encontrado | "**ESP-000123 não existe.** O exame precisa existir no CRM de Espirometria antes de receber um laudo." |
| Já tem laudo | "**ESP-000004 localizado — já possui laudo.** Enviar outro PDF cria um segundo laudo para a mesma espirometria." |
| Erro de API | "**Não foi possível consultar ESP-000004** — mensagem real **(HTTP 500)**. A busca falhou antes de chegar a uma resposta: o problema é no servidor, não no código digitado." |

## 5. A correção de fundo: não é mais preciso adivinhar código

O defeito de desenho por trás do episódio é que o **único** caminho para
anexar um PDF era acertar de cabeça um código exato, sem nenhuma pista na
tela. Agora, ao lado do campo, aparece:

> **▾ Espirometrias recentes sem laudo (1)**
> `ESP-000003` · 08/08/2026 · Realizado

Um clique localiza o exame. A lista cruza `/espirometrias` com os laudos já
existentes e mostra **só código institucional, data e status** — nenhum nome,
telefone ou dado de paciente.

---

# PARTE 2 — PROVENIÊNCIA DA INTERFACE CLÍNICA

## 6. Matriz de proveniência

Busca feita em **todo** o histórico e em todos os refs (`git log --all -S`,
`git log -G`, `git grep`, `git show`), somente leitura. Nenhum backup,
worktree histórico ou referência preservada foi alterado.

| Expressão / estrutura | Commit que introduziu | Arquivo | Removida depois? |
| --- | --- | --- | --- |
| `DVO Leve` | `0579e87` feat(m25.2) | `app/services/report_conclusions.py` | **não** |
| `Distúrbio ventilatório obstrutivo leve.` | `0579e87` feat(m25.2) | idem | **não** |
| `RBD+` | `0579e87` feat(m25.2) | idem | **não** |
| `Com resposta significativa ao broncodilatador.` | `0579e87` feat(m25.2) | idem | **não** |
| `PERSONALIZADO` | `0579e87` feat(m25.2) | catálogo + `js/report-workflow.js` | **não** |
| chips `report-conclusion-chip` / `report-bd-chip` | `0579e87`, refinados em `9ba5a58` | `js/report-workflow.js` | **não** |
| **split view** `report-comparison` | **`06ba4ac` feat(m24)** | `js/` + `css/` | **não** |

Commits inspecionados nominalmente: `9ba5a58`, `c6fdc3d`, `ff5b6e5`,
`0b74a66`, `c11a395`, `ccfc79c`, `3c65694`, `ecc8ca4`, `56517ce`, `7c890cc`,
`2977fdd`, `e069e7d` (= `backup/pc-casa-20260806`).

**Conclusão da proveniência:** nada foi perdido, sobrescrito ou revertido.
`git log --all -S` para cada expressão devolve **apenas commits de
introdução** — nenhum de remoção. A M25.10 e a M25.11 já haviam apurado isso
e estão corretas. **Nenhum trecho precisou ser recuperado do histórico.**

## 7. Mas havia sim uma perda real: o lado a lado tinha deixado de funcionar

Aqui a M25.10/M25.11 pararam cedo demais. Elas verificaram que as **strings**
e o bloco `report-comparison` existiam — e concluíram "está tudo lá". Não é a
mesma coisa que a experiência estar lá.

A montagem da tela clínica era, literalmente, esta ordem:

```
report-comparison   →  [PDF da MIR]  [PDF do laudo]   ← largura inteira
renderDocumentsPanel
renderNativeReportForm   ← as SIGLAS ficavam AQUI, abaixo dos dois PDFs
renderReleaseAction
```

Os dois PDFs ficavam lado a lado **entre si**, ocupando a largura toda, e o
formulário de conclusão vinha **depois deles**. Na prática: para clicar em
"DVO Leve" a médica precisava rolar **para longe do exame**, e o traçado da
MIR saía da tela exatamente no momento da decisão clínica.

É precisamente o "ficar mudando de tela" que você descreveu. A presença da
string no JavaScript nunca foi prova de funcionamento — como o próprio marco
alertava.

## 8. O que foi transplantado e o que foi adaptado

| Parte | Origem | Tratamento |
| --- | --- | --- |
| Catálogo de 17 conclusões + PERSONALIZADO | `0579e87` (M25.2), intacto no HEAD | **nada a fazer** — já correto |
| 5 complementos pós-BD | idem | **nada a fazer** |
| Expansão sigla → texto por extenso | idem | **nada a fazer** |
| Chips de sigla | `0579e87`/`9ba5a58` | **nada a fazer** |
| Duas colunas de PDF (`report-comparison`) | `06ba4ac` (M24C) | **adaptado** — o conceito foi preservado, a montagem mudou |
| **Bancada clínica lado a lado** | — | **novo, para reproduzir o comportamento descrito** |

Nenhum arquivo foi restaurado por `checkout` de commit antigo. Voltar a
`9ba5a58` teria apagado M25.4 (assinatura manuscrita), M25.6 (fila por
unidade), M25.7 (VIDaaS), M25.8 (lote externo) e M25.11 (referência de
verificação) — todas preservadas e verificadas por teste (seção 12, bloco F).

## 9. A bancada clínica

```
report-clinical-split
├── report-source-pane   (ESQUERDA, position: sticky)
│   └── Exame técnico (MIR) — PDF original, nunca alterado
└── report-work-pane     (DIREITA)
    ├── report-preview-pane — Laudo SoproLife / prévia
    ├── conclusão: 18 chips de sigla
    ├── pós-broncodilatador: 5 chips
    ├── texto final do laudo (editável)
    ├── observações complementares
    ├── "Gerar prévia do laudo"
    └── assinatura e liberação
```

A coluna da esquerda é `sticky`: **o traçado da MIR permanece à vista**
enquanto a médica escolhe as siglas, edita o texto e gera a prévia. Abaixo de
1100px a bancada empilha e o `sticky` é solto — duas colunas estreitas seriam
piores que a leitura sequencial.

---

# PARTE 3 — O CATÁLOGO CLÍNICO, CONFERIDO

## 10. As 17 conclusões + PERSONALIZADO

Contadas no catálogo e **contadas na tela do navegador** (o roteiro falha se
o número mudar):

| # | Código | Botão | Texto que vai ao PDF |
| --- | --- | --- | --- |
| 1 | `NORMAL` | Normal | Espirometria dentro dos limites da normalidade. |
| 2 | `DVO_LEVE` | **DVO Leve** | **Distúrbio ventilatório obstrutivo leve.** |
| 3 | `DVO_MODERADO` | DVO Moderado | Distúrbio ventilatório obstrutivo moderado. |
| 4 | `DVO_MOD_GRAVE` | DVO Mod. grave | Distúrbio ventilatório obstrutivo moderadamente grave. |
| 5 | `DVO_GRAVE` | DVO Grave | Distúrbio ventilatório obstrutivo grave. |
| 6 | `DVO_MUITO_GRAVE` | DVO Muito grave | Distúrbio ventilatório obstrutivo muito grave. |
| 7 | `DVR_SUG_LEVE` | DVR sug. Leve | Padrão sugestivo de distúrbio ventilatório restritivo leve. |
| 8 | `DVR_SUG_MODERADO` | DVR sug. Moderado | …restritivo moderado. |
| 9 | `DVR_SUG_MOD_GRAVE` | DVR sug. Mod. grave | …restritivo moderadamente grave. |
| 10 | `DVR_SUG_GRAVE` | DVR sug. Grave | …restritivo grave. |
| 11 | `DVR_SUG_MUITO_GRAVE` | DVR sug. Muito grave | …restritivo muito grave. |
| 12 | `DVM_SUG_LEVE` | DVM sug. Leve | Padrão sugestivo de distúrbio ventilatório misto leve. |
| 13 | `DVM_SUG_MODERADO` | DVM sug. Moderado | …misto moderado. |
| 14 | `DVM_SUG_MOD_GRAVE` | DVM sug. Mod. grave | …misto moderadamente grave. |
| 15 | `DVM_SUG_GRAVE` | DVM sug. Grave | …misto grave. |
| 16 | `DVM_SUG_MUITO_GRAVE` | DVM sug. Muito grave | …misto muito grave. |
| 17 | `DVI` | DVI | Padrão sugestivo de distúrbio ventilatório inespecífico. |
| — | `PERSONALIZADO` | Personalizado | *(texto escrito pela médica)* |

**17 conclusões clínicas + PERSONALIZADO = 18 botões.** Confirmado.

> Correção a um relatório anterior: a lista da seção 8 da M25.11 omitia `DVI`
> e somava 16 + PERSONALIZADO. O catálogo sempre teve 17 + PERSONALIZADO.

## 11. Os 5 complementos pós-broncodilatador

| Código | Botão | Texto que vai ao PDF |
| --- | --- | --- |
| `RBD_POSITIVO` | **RBD+** | **Com resposta significativa ao broncodilatador.** |
| `RBD_NEGATIVO` | RBD− | Sem resposta significativa ao broncodilatador. |
| `REV_COMPLETA` | REV completa | Reversibilidade completa após broncodilatador. |
| `REV_PARCIAL` | REV parcial | Reversibilidade parcial após broncodilatador. |
| `BD_NAO_REALIZADO` | BD não realizado | *(não acrescenta frase)* |

Quando o exame não tem fase pós-BD, só `BD não realizado` é oferecido.

### Confirmação medida no navegador

| Ação | Resultado observado |
| --- | --- |
| clique em **DVO Leve** | texto vira `"Distúrbio ventilatório obstrutivo leve."` |
| clique em **RBD+** | texto vira `"Distúrbio ventilatório obstrutivo leve.\nCom resposta significativa ao broncodilatador."` |

Imediato, sem ida ao servidor. O texto composto continua **totalmente
editável**, e uma redação já alterada à mão nunca é sobrescrita em silêncio.

---

# PARTE 4 — O FLUXO FICTÍCIO PONTA A PONTA

## 12. Testes executados

| Suíte | Comando | Resultado |
| --- | --- | --- |
| **M25.12 — regressões estruturais** (novo) | `node scripts/test-m25-12-resgate-laudos.js` | **38 contratos, todos passaram** |
| **M25.12 — localização e catálogo** (novo) | `pytest tests/test_m25_12_localizacao_e_catalogo.py` | **18 passaram** |
| M25.2/25.3 laudo nativo | `pytest tests/test_m25_2_native_report.py` | passou |
| M25.7 assinatura qualificada | `pytest tests/test_m25_7_qualified_signature.py` | passou |
| M25.8 lote externo | `pytest tests/test_m25_8_external_batch.py` | passou |
| M24A/M24C/M24D | 3 suítes | passaram |
| **Total pytest do módulo** | — | **164 + 18 = 182 passaram, 0 falharam** |
| Fluxo completo M25.3 (13 etapas) | `scripts/test_m25_3_fluxo_completo.py` | **49 verificações OK, 0 falhas** |
| Proxy do Command Center | `scripts/test_command_center_m15_proxy.py` | 46 passaram |
| Contratos M24C | `node scripts/test-m24a-report-workflow.js` | passaram |
| Guardas estáticas / contratos | 2 suítes | passaram |
| `node --check` | `js/report-workflow.js` | limpo |

O que as regressões novas travam: campo sem `pattern`, `novalidate`, os cinco
desfechos da busca, status HTTP no erro, lista de exames sem laudo, ausência
de dado de paciente nessa lista, bancada em duas colunas, `sticky` na coluna
do exame, formulário **dentro** da bancada, empilhamento responsivo, 18
conclusões, 5 complementos, as duas frases exatas, recomposição imediata,
preservação do texto editado, `PERSONALIZADO`, e a preservação de M25.4/6/7/8
e M25.11.

### Duas coisas que encontrei e corrigi de passagem

**1. O laudo saía com o CRM em formato errado.** O seed gravava
`crm_display = "5262307-5"`, sem o ponto. O CRM da Dra. Ana é **`52.62307-5`**.
A verificação 25 do roteiro E2E (`laudo traz «CRM-RJ 52.62307-5»`) **falhava
desde a M25.3** e ninguém tinha reportado. Corrigido: o roteiro passou de
48/49 para **49/49**.

> ⚠️ **Isto precisa ser conferido em produção.** O relatório M25.11 registra
> `crm=5262307-5` no perfil de produção. Se for o `crm_display`, o laudo da
> Dra. Ana está saindo sem o ponto. Correção pela tela de Contas médicas, sem
> SQL — mas **atenção**: alterar identidade força reverificação do perfil.

**2. `scripts/test-m24a-browser-e2e.js` enviava `verification_status:
"verified"` sem `verification_reference`**, exigência criada na M25.11.
Corrigido. Esse roteiro, porém, **continua falhando por outro motivo,
pré-existente**: ele nunca reinicia a API com os laudos habilitados, então a
fase "Administração sintética" é inalcançável. **Verificado rodando o roteiro
no commit base `b5f8a80` em worktree temporário: falha idêntica, byte a
byte.** Não é regressão desta etapa e consertar o harness está fora do escopo
da M25.12.

## 13. Evidência visual — 13 capturas da interface real

Roteiro `scripts/evidencia_m25_12_navegador.py`: Chromium de verdade, login
de verdade, cliques de verdade, 1600×1000. As asserções estão **dentro** do
roteiro — ele falha se os números ou as frases mudarem.

**Cenário 100% fictício:**

| Item | Valor |
| --- | --- |
| Paciente | João da Silva Teste (`PES-000001`) — fictício |
| Exame | **`ESP-000004`** — 08/08/2026, com fase pós-BD |
| Laudo | **`LAU-000004`** |
| Unidade | Clínica Pastore — Unidade Ipanema |
| PDF técnico | sintético, sem traçado e sem dado clínico real |
| Código de verificação | `S2KFEVKW7TR3` |

**Pasta:** `/home/fedorasurf/Documents/SoproLife/_EVIDENCIAS_M25_12/`

| # | Arquivo | O que mostra |
| --- | --- | --- |
| A0 | `A0-codigo-invalido-explicado.png` | **`ESP-TF0001` recusado COM explicação fixa na tela** — o defeito relatado, agora falando |
| A | `A-exame-localizado.png` | `ESP-000004` localizado pelo código institucional |
| B | `B-upload-habilitado.png` | campo de PDF habilitado, atribuição preenchida |
| C | `C-exame-atribuido.png` | PDF armazenado, laudo criado e atribuído |
| D | `D-fila-medica.png` | "Meus laudos" com o documento na fila |
| E | `E-bancada-lado-a-lado.png` | **MIR à esquerda, laudo e conclusão à direita** |
| F | `F-siglas-conclusoes.png` | **18 conclusões e 5 complementos**, contados |
| G | `G-dvo-leve-por-extenso.png` | **DVO Leve → "Distúrbio ventilatório obstrutivo leve."** |
| H | `H-rbd-mais-por-extenso.png` | **RBD+ → "Com resposta significativa ao broncodilatador."** |
| I | `I-previa-do-laudo.png` | prévia gerada ao lado do exame técnico |
| J1 | `J1-finalizacao-disponivel.png` | liberação e downloads separados |
| J2 | `J2-confirmacao-consciente.png` | confirmação consciente antes de assinar |
| J3 | `J3-laudo-liberado.png` | **liberado, dois PDFs com download separado** |

Também na pasta: `tecnico_mir.pdf` (858 bytes) e `laudo_soprolife.pdf`
(123.902 bytes) — os dois documentos, baixados separadamente.

**Ressalva honesta sobre as capturas:** os quadros de PDF aparecem **em
branco** nas imagens. O Chromium *headless* não embute o visualizador de PDF;
o `<iframe>` carrega mas não desenha. Não é defeito do produto — no navegador
comum o PDF aparece. O que as capturas provam é o **layout, os controles, os
textos e os estados**, não a renderização do PDF.

### Roteiro de 33 pontos

| # | Item | Estado |
| --- | --- | --- |
| 1–4 | paciente, exame, unidade, localização pelo código | ✅ A |
| 5–6 | upload do PDF técnico fictício e persistência | ✅ B, C |
| 7–8 | atribuição à médica e item em "Meus laudos" | ✅ C, D |
| 9–11 | abertura, MIR à esquerda, laudo à direita | ✅ E |
| 12–14 | 17 conclusões, PERSONALIZADO, 5 complementos | ✅ F |
| 15–18 | DVO Leve, RBD+, e as duas frases por extenso | ✅ G, H |
| 19–20 | composição automática e edição manual | ✅ G, H |
| 21–22 | prévia e finalização | ✅ I, J1, J2 |
| 23 | PDF médico final | ✅ J3 |
| 24–27 | Dra. Ana, CRM, RQE, unidade no PDF | ✅ E2E (49/49) |
| 28 | assinatura conforme configuração real | ✅ liberação institucional; VIDaaS declarado não configurado |
| 29–30 | download separado do exame e do laudo | ✅ J3 |
| 31 | QR | ⚠️ **ausente** — `M15_REPORTS_VALIDATION_BASE_URL` não configurada. *Fail-closed por desenho: nenhuma URL é inventada.* Sai só o código textual (`S2KFEVKW7TR3`). |
| 32 | trilha de auditoria | ✅ `laudo_original_atribuido` com códigos, atribuição e versão |
| 33 | ausência de dados reais | ✅ paciente, exame e PDF sintéticos |

**Nenhum dado real de paciente foi usado, exibido ou gravado.**

## 14. O que NÃO consegui fazer, e por quê

Duas barreiras, ambas fora do meu alcance legítimo:

**1. Não há SSH para a VPS desta máquina.** Verificado:

```
tailscale status            → soprolife-painel-01 (100.87.98.100) ONLINE
ping                        → 0% de perda
porta 22                    → conexão TCP aceita, mas NENHUM banner SSH
tailscale status --json     → SSH_HostKeys: false  (Tailscale SSH desligado)
ssh -o BatchMode=yes root@…  → trava, sem responder
```

Sem shell não consigo consultar o banco de produção, fazer backup, nem
implantar. As missões M25.9–M25.11 rodaram de `/home/adeildo/soprolife-site`
— **outra máquina**. Esta é `/home/fedorasurf/`.

**2. Não tenho credencial de nenhuma conta de produção** — e não vou pedir,
procurar em arquivo nem redefinir. Sem login não há como fotografar a
produção autenticada. **Não falsifiquei captura nem contornei autenticação.**

O que consegui provar da produção, por HTTPS público:

```
GET /painel-soprolife/api/m15/health   → status ok, ambiente prod, banco ok
GET /painel-soprolife/                 → 200, v=2026080802
GET .../js/report-workflow.js          → 200, 103.228 bytes, idêntico ao HEAD
GET .../espirometrias?public_code=…    → 401 "Token ausente." (rota existe e exige auth)
```

## 15. O que preciso de você

1. **Fazer o deploy** desta branch (procedimento na seção 17), ou me dar
   acesso SSH à VPS para que eu o faça com backup e rollback.
2. **Repetir o roteiro em produção** com o exame fictício, ou me fornecer
   uma sessão de teste legítima. Prefiro que seja você: é o teste de fumaça
   que importa.
3. **Conferir o `crm_display` da Dra. Ana em produção** — se estiver
   `5262307-5`, corrigir para `52.62307-5` (seção 12).
4. **Decidir o domínio de `M15_REPORTS_VALIDATION_BASE_URL`** — sem ela o
   laudo sai sem QR (pendência aberta desde a M25.7).
5. **Entregar a senha de primeiro acesso da Dra. Ana** por canal seguro e
   apagar `/opt/soprolife/secrets/ana-primeiro-acesso.txt` depois.
6. **Cadastrar a assinatura manuscrita** dela.

---

# PARTE 5 — ENTREGA

## 16. Arquivos

**Alterados**

| Arquivo | Mudança |
| --- | --- |
| `painel-soprolife/js/report-workflow.js` | localizador de exame reescrito; bancada clínica lado a lado |
| `painel-soprolife/css/report-workflow.css` | `.report-locate-feedback`, `.report-exam-pick`, `.report-clinical-split`, `.report-source-pane`, responsivo em 1100px |
| `painel-soprolife/index.html` | cache-bust `2026080802` → **`2026080901`** |
| `painel-soprolife/nucleo-m15/scripts/seed_m25_3_laudo_demo.py` | `crm_display` corrigido para `52.62307-5` |
| `painel-soprolife/scripts/test-m24a-browser-e2e.js` | `verification_reference` no PATCH (exigência da M25.11) |

**Novos**

| Arquivo | Papel |
| --- | --- |
| `painel-soprolife/scripts/test-m25-12-resgate-laudos.js` | 38 regressões estruturais |
| `painel-soprolife/nucleo-m15/tests/test_m25_12_localizacao_e_catalogo.py` | 18 regressões de busca e catálogo |
| `painel-soprolife/nucleo-m15/scripts/seed_m25_12_exame_sem_laudo.py` | espirometria fictícia sem laudo (fail-closed: `dev` + SQLite + `--confirmar`) |
| `painel-soprolife/nucleo-m15/scripts/evidencia_m25_12_navegador.py` | captura A–J com Playwright; credenciais só por argumento/env, nunca no arquivo |
| `RELATORIO_M25_12_RESGATE_INTERFACE_LAUDOS_E2E_20260808.md` | este relatório |

**Nenhuma migration nova.** Schema segue em `d4a71c88b2e6`. Nenhuma mudança
em autenticação, papéis, permissões ou regras de assinatura.

## 17. Deploy — verificações já feitas e o procedimento

**Conferido agora, ao vivo:**

```
git ls-remote origin refs/heads/painel-soprolife-v01
  → b5f8a8064d4b3c1378f43681c73e88e47942b84a
```

**A branch oficial NÃO mudou** durante a missão: continua exatamente na base
desta etapa. Não há divergência a reconciliar. O repositório principal
`/home/fedorasurf/soprolife-site` também está em `b5f8a80`.

```bash
# 1. Backup do banco ANTES de qualquer coisa
ssh <VPS> 'mkdir -p /opt/soprolife/backups/m25-12/$(date -u +%Y%m%dT%H%M%SZ)'
#    pg_dump conforme o procedimento das etapas anteriores; conferir com pg_restore -l

# 2. Registrar o commit implantado atual
ssh <VPS> 'cd /opt/soprolife/soprolife-site && git rev-parse HEAD'   # esperado: b5f8a80

# 3. Integrar por fast-forward — sem reset, sem force
git checkout painel-soprolife-v01 && git merge --ff-only claude-m25-12-resgate-laudos-e2e
git push origin painel-soprolife-v01

# 4. Na VPS
ssh <VPS> 'cd /opt/soprolife/soprolife-site && git pull --ff-only'
ssh <VPS> 'systemctl restart soprolife-m15-api.service'

# 5. Conferir DEPOIS
curl -s https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/api/m15/health
curl -s https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/ | grep -o 'v=2026080901'
```

**Não usei e não recomendo:** `git reset --hard`, `push --force`,
`--force-with-lease`, exclusão de histórico, remoção de worktrees com
trabalho. A branch foi construída por commits normais sobre `b5f8a80`.

## 17-A. Integração no GitHub — CONCLUÍDA (deploy NÃO)

Etapa autorizada e executada em 08/08/2026, **somente Git**. A VPS não foi
tocada: continua sem SSH funcional a partir desta máquina (seção 14).

**Verificações feitas ANTES de qualquer escrita:**

| Verificação | Resultado |
| --- | --- |
| `git fetch --all --prune` | sem divergência |
| `git ls-remote origin refs/heads/painel-soprolife-v01` (ao vivo) | `b5f8a80…` — **inalterado** |
| `git merge-base --is-ancestor origin/painel-soprolife-v01 HEAD` | **verdadeiro** — fast-forward possível |
| `git log HEAD..origin/painel-soprolife-v01` | **vazio** — nada a reconciliar |
| `git status` no worktree | limpo |
| `git status` em `/home/fedorasurf/soprolife-site` | limpo, em `painel-soprolife-v01` @ `b5f8a80` |

**Integração:** `git merge --ff-only`, seguido de `git push` normal. **Sem
`--force`, sem `--force-with-lease`, sem `reset --hard`, sem `rebase`.** O
histórico da branch oficial só avançou; nada foi reescrito.

**Commits M25.12 que entraram em `painel-soprolife-v01`:**

| Commit | Conteúdo |
| --- | --- |
| `029f7da` | correção do localizador de exame e a bancada clínica lado a lado |
| `d707329` | registro do commit criado no relatório |
| *(este)* | esta seção 17-A |

### O que continua pendente

| Item | Estado |
| --- | --- |
| Código no GitHub | ✅ integrado em `painel-soprolife-v01` |
| **Deploy na VPS** | ❌ **NÃO feito.** A VPS permanece em `b5f8a80`, servindo `v=2026080802`. |
| **Evidência visual de produção** | ❌ **pendente.** Depende do deploy e de login manual. |
| `reports_mode` | `pilot`, **inalterado** |

O deploy precisa ser feito de uma máquina com SSH para a VPS — o notebook de
trabalho (`/home/adeildo/soprolife-site`), que foi de onde as etapas M25.9 a
M25.11 rodaram. Procedimento na seção 17, passos 1, 2, 4 e 5 (o passo 3 já
está feito).

**A M25.12 NÃO está concluída.** A prova que importa — você abrir a produção
e reencontrar a bancada clínica — ainda não aconteceu.

## 18. Rollback

```bash
ssh <VPS>
cd /opt/soprolife/soprolife-site
git checkout painel-soprolife-v01
git reset --hard b5f8a80          # volta só arquivos estáticos
systemctl restart soprolife-m15-api.service
```

Esta etapa **não altera schema nem dados**: o rollback é só de arquivos
estáticos e do JS/CSS servidos. Os dados fictícios criados existem **apenas
no SQLite local** desta máquina — o banco de produção **não foi tocado**.

## 19. Modo piloto — inalterado

```
reports_mode = pilot
```

O aviso continua obrigatório, literal e travado por teste, e aparece em
**todas as 13 capturas**:

> **PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE**

Nada foi promovido a produção clínica. `reports_mode = "production"` continua
recusado por código (503 `relatorios_producao_bloqueada`).

## 20. Limitações restantes

1. **Evidência visual de produção pendente** — sem SSH e sem credencial.
2. **Laudo sem QR** — `M15_REPORTS_VALIDATION_BASE_URL` não configurada.
3. **`crm_display` da Dra. Ana em produção** possivelmente sem o ponto.
4. **`LAU-TF0001` permanece** por gatilho *append-only*; neutralizado.
5. **6 templates "PROVISÓRIO"** em `draft` na área administrativa — legado da
   M24, não bloqueiam o fluxo nativo.
6. **`test-m24a-browser-e2e.js` quebrado** antes desta etapa (seção 12).
7. **Um exame pode receber mais de um laudo** — hoje a tela **avisa**, mas o
   servidor não impede. Não corrigi: bloquear no servidor é decisão clínica
   (documento corretivo? segunda opinião?), não técnica.

## 21. URLs testadas

```
https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/                    → 200
https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/api/m15/health      → ok/prod
https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/js/report-workflow.js → 200
http://127.0.0.1:8765/painel-soprolife/                                            → cenário fictício
```

## 22. Segurança

- Nenhuma senha foi pedida, procurada, redefinida, exibida, registrada em
  log, comando, captura, relatório ou commit.
- Nenhuma autenticação foi contornada. Nenhuma captura foi forjada.
- Nenhum backup, worktree histórico ou referência preservada foi alterado —
  o histórico foi lido apenas com comandos de leitura.
- Nenhum dado real de paciente foi usado, exibido ou gravado.
- O `.env` local (com laudos habilitados para teste) é *gitignored* e não
  entra no commit.
