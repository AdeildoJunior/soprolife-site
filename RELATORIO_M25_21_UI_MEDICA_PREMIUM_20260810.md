# M25.21 — Restaurar a bancada médica e deixar a UX premium

**Data:** 10/08/2026
**Branch:** `claude-m25-21-ui-medica-premium`
**HEAD inicial:** `1ae5e230b0e8ce9f206697e711fa3cf17cb8a960`
**Escopo:** UX/UI e bug de contagem. Nenhuma migration, nenhuma alteração de
texto clínico, nenhum endpoint novo.

---

## 1. Causa raiz

Duas falhas, uma forma só: **uma estrutura nova encaixada numa estrutura antiga
que continuou obedecendo à própria regra.** Nenhuma das duas foi um erro de
digitação; as duas foram consequências corretas de regras corretas.

### 1.1 A bancada estrangulada — grade de duas colunas com três filhos

`renderPhysicianWorkspace()` montava:

```js
<div class="report-physician-shell">
  ${renderQueue()}              // filho 1
  ${renderPhysicianDetail()}    // filho 2
</div>
```

e o CSS dizia:

```css
.report-physician-shell {
  display: grid;
  grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
}
```

Duas colunas, dois filhos: fila estreita à esquerda, bancada larga à direita.

A M25.20 (commit `05f7cad`) acrescentou a central de assinatura externa como
**terceiro filho**, sem tocar na grade:

```js
<div class="report-physician-shell">
  ${renderSignatureCenter()}    // filho 1  → linha 1, coluna 1 (~300px)
  ${renderQueue()}              // filho 2  → linha 1, coluna 2 (o resto)
  ${renderPhysicianDetail()}    // filho 3  → linha 2, COLUNA 1 (~300px)
</div>
```

O posicionamento automático fez o que faz: encheu a linha 1 e desceu para a
linha 2, **na primeira coluna**. A bancada clínica inteira — resumo do
paciente, PDF da MIR, painel do laudo, 17 botões de conclusão, textareas —
passou a viver numa faixa de 260–320px, com a coluna larga da linha 2 vazia.

Daí, item por item, tudo o que foi relatado:

| sintoma relatado | mecanismo |
| --- | --- |
| bancada colapsa numa faixa estreita à esquerda | filho 3 na coluna de 260–320px |
| ~70–80% da página vazia à direita | célula linha 2 / coluna 2 sem conteúdo |
| PDF da MIR e painel do laudo microscópicos | `.report-clinical-split` divide 300px em duas |
| botões de conclusão viram coluna estreita e altíssima | `flex-wrap` com ~140px úteis: um botão por linha |
| palavras quebradas letra por letra | `overflow-wrap: anywhere` em `.report-item-name`, `.report-exam-context strong` e afins — `anywhere` **também zera a largura mínima do elemento**, então em contêiner estreito o nome desce caractere a caractere |
| cards PACIENTE / EXAME / LOCAL estreitíssimos | grade de 3 colunas dentro de 300px, com coluna fixa de 96px só para o rótulo |

A parte superior continuava larga porque a linha 1 estava correta. Só o que
caiu para a linha 2 quebrou — e o que caiu para a linha 2 era a tela inteira de
trabalho clínico.

### 1.2 O `undefined` — envelope adivinhado

A carga autenticada normalizava toda resposta com uma heurística única:

```js
state[label] = (value && Array.isArray(value.itens)) ? value.itens : value;
```

"Se vier `{itens: [...]}`, a lista é `itens`; senão, a resposta **é** a lista."
Isso valia enquanto todo endpoint devolvesse ou lista crua ou o envelope
paginado. Os dois endpoints novos da M25.20 têm envelopes próprios:

| endpoint | envelope real | o que a heurística fez |
| --- | --- | --- |
| `/laudos/assinatura-externa/pendentes` | `{total, laudos}` | guardou o **objeto** em `state.signaturePending`. `lista.length` → `undefined` → **"Aguardando assinatura qualificada — undefined"**. E como `undefined` é falso, a central mostrava *"Nenhum laudo aguardando assinatura"* mesmo com laudos aguardando. |
| `/laudos/assinatura-externa/fila` | `{estados, itens}` | acertou o `itens` e **jogou fora o `estados`**. A fila de entrega administrativa perdia os contadores por estado e, sem `fila.itens`, listava "Nenhum laudo neste estado" sempre. |

O `undefined` era a ponta visível: **a central da médica e a fila da
administração estavam ambas cegas desde a M25.20.** Corrigir só o `<h3>` teria
escondido o sintoma e mantido as duas listas vazias.

---

## 2. Correção

### 2.1 Estrutura — dois níveis, não três irmãos numa grade

```js
<div class="report-physician-shell">          // PILHA (uma coluna)
  <div class="report-physician-summary">      // NÍVEL 2: única grade de colunas
    ${renderSignatureCenter()}                //   ~34%
    ${renderQueue()}                          //   ~66%
  </div>
  <div class="report-physician-workbench">    // IRMÃO, largura útil inteira
    ${renderPhysicianDetail()}
  </div>
</div>
```

```css
.report-physician-shell   { grid-template-columns: minmax(0, 1fr); }
.report-physician-summary { grid-template-columns: minmax(280px, 34%) minmax(0, 1fr); }
```

O shell virou **pilha**. Quem decide sobre colunas é a faixa de resumo, e só
ela. Qualquer bloco acrescentado ao shell no futuro nasce em largura inteira —
o comportamento seguro. Repetir o acidente da M25.20 exigiria colocar o bloco
novo deliberadamente dentro da faixa de resumo.

Nenhuma largura foi forçada em filho nenhum. Não há `width: 100%` de
maquiagem em lugar algum desta correção.

### 2.2 Envelope declarado, contagem sempre inteira

```js
const PAYLOAD_ENVELOPES = {
  signaturePending: "laudos",   // {total, laudos}
  deliveryQueue: "",            // objeto inteiro: {estados, itens}
};
```

`unwrapPayload(label, value)` consulta o mapa; endpoint sem envelope declarado
segue pela regra antiga. Uma resposta quebrada devolve `[]`, nunca um objeto
travestido de lista.

E a renderização não confia nem nisso:

```js
function pendingSignatureList() {
  return Array.isArray(state.signaturePending) ? state.signaturePending : [];
}
```

A contagem saiu do título e virou **selo**: o título é estável
("Aguardando assinatura qualificada") e o número é um elemento próprio, que
com 0 fica cinza em vez de gritar em navy.

Cenários verificados executando as funções **reais** em Node
(`test_contagem_da_central_nunca_vira_undefined_nan_ou_null`):

| payload | contagem | título |
| --- | --- | --- |
| `{total: 0, laudos: []}` | 0 | sem `undefined` |
| `{total: 1, laudos: [1]}` | 1 | sem `undefined` |
| `{total: 3, laudos: [3]}` | 3 | sem `undefined` |
| `{laudos: [1]}` (sem `total`) | 1 | sem `undefined` |
| `null` (falha parcial) | 0 | sem `undefined` |
| `{itens: [...]}` (envelope errado) | 0 | sem `undefined` |
| lista crua | 0 | sem `undefined` |

---

## 3. Arquivos alterados

| arquivo | mudança |
| --- | --- |
| `painel-soprolife/js/report-workflow.js` | dois níveis no workspace; `PAYLOAD_ENVELOPES` + `unwrapPayload`; `pendingSignatureList`; cabeçalho da central com selo; cabeçalho do paciente horizontal; lote M25.8 recolhido no rodapé da fila |
| `painel-soprolife/css/report-workflow.css` | shell em pilha + faixa de resumo; cartões de contexto; grade de conclusões; altura do visualizador no desktop; split 53/47; fila em colunas; estados de hover/seleção; `anywhere` → `break-word` nos dados de paciente |
| `painel-soprolife/index.html` | `?v=2026081001` → `?v=2026081002` (CSS e JS) |
| `painel-soprolife/nucleo-m15/tests/test_m25_21_ui_medica_premium.py` | **novo** — 39 testes estruturais |
| `painel-soprolife/nucleo-m15/tests/visual/` | **novo** — harness sintético + CDP + PDF fictício + README |
| `painel-soprolife/docs/m25-21/` | **novo** — capturas sintéticas e `medidas.json` |

Nenhuma migration. Nenhum arquivo de `app/` tocado. Nenhum endpoint alterado.

---

## 4. Antes e depois da hierarquia

**Antes (M25.20, 1920px):**

```
┌───────────────┬──────────────────────────────────────────────┐
│ Assinatura    │ Meus laudos                                  │
│ (~300px)      │                                              │
├───────────────┼──────────────────────────────────────────────┤
│ BANCADA       │                                              │
│ CLÍNICA       │              (vazio, ~1570px)                │
│ INTEIRA       │                                              │
│ (~300px)      │                                              │
│ ...           │                                              │
└───────────────┴──────────────────────────────────────────────┘
```

**Depois (M25.21, 1920px — medido no navegador):**

```
┌──────────────────────────────────────────────────────────────┐
│ NÍVEL 1  Laudos de espirometria · sessão · papel · sair       │
├───────────────────────┬──────────────────────────────────────┤
│ NÍVEL 2               │                                      │
│ ASSINATURA EXTERNA ③  │ Meus laudos                          │
│ 635px (34%)           │ 1216px (65%) — cartões em 3 colunas   │
├───────────────────────┴──────────────────────────────────────┤
│ PACIENTE          │ EXAME            │ LOCAL DE REALIZAÇÃO    │
├──────────────────────────────┬───────────────────────────────┤
│ EXAME TÉCNICO MIR            │ LAUDO SOPROLIFE               │
│ 960px (53%) · viewer 799px   │ 857px (47%) · conclusões 4 col │
├──────────────────────────────┴───────────────────────────────┤
│ DOCUMENTOS DO EXAME (largura inteira)                        │
└──────────────────────────────────────────────────────────────┘
```

Bancada: **1867px de 1920** de janela (a diferença é a margem da página).

### Cabeçalho do paciente

Cada cartão era uma pilha de linhas `rótulo | valor` com coluna fixa de 96px
para o rótulo — dentro de um cartão estreito sobravam ~60px para o valor, e o
nome descia letra por letra. Virou **protagonista + linhas de apoio**:

```
PACIENTE                    EXAME                    LOCAL DE REALIZAÇÃO
Ana Exemplo Ribeiro         ESP-000401               Clínica Exemplo Ipanema
Nasc. 17/04/1979 •          01/08/2026 às 09:40 •    Rua Fictícia 100, sala 4
PAC-000777                  Com fase pós-BD          (21) 0000-0000
                            Indicação: ...
```

Nenhum dado saiu da tela (nome, nascimento, registro, código, data, hora,
fase pós-BD, indicação, unidade, endereço, contato). O que saiu foi a coluna de
rótulos que espremia o valor. Desktop: 3 áreas equilibradas. Tablet (≤1180px):
2 + 1, com o local em largura inteira. Celular (≤720px): empilhado.

### Conclusões

`flex-wrap` (botões com a largura do próprio texto, linhas irregulares, coluna
única em painel estreito) → **grade** `repeat(auto-fill, minmax(170px, 1fr))`.
As contagens abaixo são consequência do espaço medido, não números escritos no
CSS: **4 colunas em 1920x1080, 3 em 1440x900 e 1366x768, 4 em 1024x768** (onde
a bancada empilha e o painel do laudo fica em largura inteira), **2 no
iPhone**. Botões com `min-height: 44px`, altura uniforme, texto centralizado.

### Quebra de texto

`overflow-wrap: anywhere` → `break-word` em `.report-item-name`,
`.report-context-main`, `.report-context-meta`, `.report-signature-name`,
`.report-queue-item span`, `.report-operation-row span`, `.report-exam-pick
span`, `.report-documents-list span` e `.report-location-readonly strong`.
Nenhum `word-break: break-all` em lugar nenhum do arquivo.

### Dois blocos de assinatura na mesma tela

Achado durante a inspeção visual, não relatado: `renderBatchBar` (fluxo M25.8)
faz **o mesmo trabalho** da central da M25.20 — contar o que aguarda
assinatura, baixar em lote, receber os assinados — e ficava **no topo de "Meus
laudos"**, com um seletor de arquivos por cima da lista de pacientes. Duas
contagens concorrentes do mesmo fato e um upload competindo com a fila.

**Nada foi removido.** Os botões, o `input[type=file]` e todos os handlers são
byte a byte os mesmos; o bloco desceu para o rodapé do painel e nasce recolhido
num `<details>` rotulado "Lote de assinatura (fluxo M25.8)". A central da
M25.20 — que confere antes de gravar — é a porta principal. Se um envio deixar
resultados na tela, o `<details>` abre sozinho.

---

## 5. Breakpoints

| largura | faixa de resumo | bancada | observações |
| --- | --- | --- | --- |
| ≥1280px | 34% / 66% | 2 colunas 53/47 | visualizador `clamp(560px, 74vh, 880px)`; fila em colunas a partir de 1100px |
| 1180–1280px | 34% / 66% | 2 colunas 50/50 | |
| 1100–1180px | 38% / 62% | 2 colunas | contexto do paciente em 2 + 1 |
| 900–1100px | 38% / 62% | **empilha** | `.report-source-pane` solta o `sticky` |
| 720–900px | **empilha** | empilha | fila com `max-height` 320px |
| ≤720px | empilha | empilha | contexto em 1 coluna; visualizador 56vh |
| ≤480px | empilha | empilha | conclusões em 2 colunas; botões em largura inteira |

Medido em 1920x1080, 1440x900, 1366x768, 1024x768, 768x1024 e 430x932:
**`overflow_horizontal: false` em todas as 18 combinações** (3 cenários × 6
larguras).

---

## 6. Screenshots sintéticos

Em `painel-soprolife/docs/m25-21/` (capturas da tela real, não de página
inteira), geradas pelo harness de `nucleo-m15/tests/visual/`:

| arquivo | cenário |
| --- | --- |
| `a-lista-medica-1920.png` | fila médica, nenhum paciente aberto |
| `b-paciente-aberto-1920.png` | bancada completa em 1920x1080 |
| `b-paciente-aberto-1366.png` | 1366x768 |
| `b-paciente-aberto-1024.png` | 1024x768 (bancada empilhada) |
| `b-paciente-aberto-430.png` | iPhone 430x932 |
| `c-central-vazia-1920.png` | central sem laudos aguardando (**selo `0`**) |
| `medidas.json` | as 18 medições completas |

**Todos os dados são fictícios.** Nomes inventados ("Ana Exemplo Ribeiro
Nascimento"), códigos fora das faixas reais (ESP-0004xx, LAU-0003xx, PAC-000777),
clínica inventada ("Clínica Exemplo Ipanema", "Rua Fictícia 100"), e um PDF
gerado por `fake_pdf.py` carimbado "DOCUMENTO FICTICIO PARA TESTE DE LAYOUT".
Nenhum paciente real foi capturado em nenhum momento.

### Medições (cenário "paciente aberto")

| viewport | bancada | assinatura / meus laudos | MIR (viewer) | conclusões | overflow | `undefined` |
| --- | --- | --- | --- | --- | --- | --- |
| 1920x1080 | 1867px | 635 / 1216 | 799px | 4 col | não | não |
| 1440x900 | 1387px | 472 / 899 | 666px | 3 col | não | não |
| 1366x768 | 1313px | 446 / 851 | 568px | 3 col | não | não |
| 1024x768 | 971px | 369 / 590 | 492px | 4 col | não | não |
| 768x1024 | 715px | empilhado | 655px | 3 col | não | não |
| 430x932 | 382px | empilhado | 522px | 2 col | não | não |

`bancada_dentro_do_resumo: false` e `bancada_filha_do_shell: true` nas 18
combinações.

---

## 7. Inspeção visual

Feita neste PC com `/usr/bin/google-chrome` (150.0.7871.181) headless via CDP,
com inspeção olho a olho das capturas:

- sem faixa estreita — a bancada usa 97% da janela;
- sem texto letra por letra — nomes longos ("Carla Exemplo de Andrade
  Fontenelle") quebram por palavra;
- sem vazio absurdo — a fila em colunas preenche os 1216px de "Meus laudos";
- MIR legível — tabela de parâmetros e curvas nítidas em 934x799;
- laudo legível — texto final e observações em textareas largas;
- botões organizados em grade de altura uniforme;
- hierarquia premium: eyebrow → título → selo, cartões sem excesso de contorno,
  hover distinto da seleção (que ganhou faixa navy à esquerda).

Um ajuste nasceu da inspeção: a caixa de seleção do lote era o primeiro filho
do cartão da fila e, na grade, ocupava a linha inteira acima do nome — o cartão
marcável ficava mais alto que os vizinhos. Passou a flutuar no canto superior
direito, fora do fluxo, com o mesmo alvo de toque e o mesmo handler.

---

## 8. Testes

### Novo: `test_m25_21_ui_medica_premium.py` — 39 testes

Estruturais de propósito. Procurar `width: 100%` no CSS não distinguiria o
conserto do disfarce. O arquivo:

1. **monta a árvore DOM** do template de `renderPhysicianWorkspace` e verifica
   ninho — quem é filho de quem — e não texto;
2. **interpreta o CSS em regras** (seletor, declarações, media query) e verifica
   faixas de grade, larguras aplicáveis à bancada e regras de quebra;
3. **executa em Node** as funções reais de normalização e contagem contra sete
   payloads, inclusive respostas quebradas.

Cobre exatamente o que a missão pediu:

| exigência | teste |
| --- | --- |
| workspace clínico não está na coluna estreita | `test_bancada_clinica_nao_esta_dentro_da_coluna_da_assinatura` |
| bancada tem contêiner próprio full-width | `test_workspace_tem_dois_niveis_e_nao_tres_irmaos_numa_grade` |
| grade clínica com `minmax` adequado | `test_grade_clinica_usa_minmax_com_piso_zero` |
| nenhum estilo aplica ~200px à bancada | `test_nenhuma_largura_estreita_alcanca_a_bancada` |
| nenhum `break-all` nos dados do paciente | `test_dados_de_paciente_quebram_por_palavra`, `test_nenhum_word_break_break_all_no_workspace` |
| `undefined` não pode aparecer | `test_contagem_da_central_nunca_vira_undefined_nan_ou_null` |
| fechamento de div/template | `test_template_do_workspace_fecha_todas_as_tags` |

**Verificação de que o teste pega a regressão:** rodadas as mesmas asserções
contra os arquivos de `HEAD` (`1ae5e23`), todas falham —
shell com 0 filhos-elemento e 3 blocos soltos, `grid-template-columns` com 2
faixas, `.report-item-name` com `anywhere`, `.report-chip-grid` em `flex`,
título com `${lista.length}` e nenhum `PAYLOAD_ENVELOPES`.

### Suíte completa

```
1254 passed, 30 skipped in 377.95s
```

Rodada duas vezes (após a primeira leva de mudanças e após os ajustes finais).
Zero regressões. Nenhum teste existente precisou ser editado.

---

## 9. Regressões verificadas (M25.20 intacta)

Cobertas por `test_central_de_assinatura_externa_preservada` (16 marcas) e
pelos 1254 testes da suíte, incluindo os 63 de
`test_m25_20_central_assinatura_lote.py`:

- central de assinatura externa — presente, agora como cartão premium;
- download individual e ZIP para 2+ — `/assinatura-externa/baixar` intacto;
- upload de múltiplos PDFs e de ZIP — `multiple`, `.pdf`, `.zip`,
  `application/zip` preservados;
- identificação automática e confirmação de lote — `/enviar` e `/confirmar`
  intactos;
- fila administrativa — **voltou a funcionar** (recebia só `itens` e perdia
  `estados`);
- armazenamento e estados — nenhuma linha de `app/` tocada;
- compatibilidade iPhone — `min-height: 44px` verificado em
  `.report-signature-item` e nos botões da central; seleção pela linha inteira
  preservada; capturas em 430x932 conferidas.

---

## 10. Confirmação de que nada clínico mudou

`test_nada_de_clinico_foi_tocado` + `git diff` sobre `app/` (vazio):

- os textos das 17 conclusões continuam vindo de
  `/laudos/{id}/catalogo-conclusoes` — nenhuma sigla ou frase clínica está
  escrita no navegador (`"DVO Leve"` e `"RBD+"` **não existem** no JS);
- `PERSONALIZADO` e os 5 complementos BD: só a origem do servidor;
- lógica da conclusão, PDF clínico, rubrica, assinatura, armazenamento: nenhum
  arquivo de `app/`, `migrations/` ou de serviço foi alterado;
- códigos de paciente/exame/laudo: `ids.py` intocado;
- confirmações conscientes `ASSINAR E LIBERAR` e `PUBLICAR ADENDO`: intactas.

**Nenhuma migration foi criada ou executada.**

---

## 11. Deploy

_(preenchido na execução — ver seção 13)_

---

## 12. Smoke visual em produção

_(preenchido na execução — ver seção 13)_

---

## 13. Execução

_(a completar)_
