# M25.21 — Restaurar a bancada médica e deixar a UX premium

**Data:** 10/08/2026
**Branch:** `claude-m25-21-ui-medica-premium`
**HEAD inicial:** `1ae5e230b0e8ce9f206697e711fa3cf17cb8a960`
**Escopo:** UX/UI, bug de contagem e o texto do selo do PDF pré-assinatura
(seção 12-A). Nenhuma migration, nenhuma alteração de texto clínico, nenhum
endpoint novo.

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
| `painel-soprolife/nucleo-m15/app/services/report_native_pdf.py` | selo pré-assinatura sem "AGUARDANDO ASSINATURA" (seção 12-A) |
| `painel-soprolife/nucleo-m15/tests/test_m25_21_selo_pdf_pre_assinatura.py` | **novo** — 16 testes do selo |
| `painel-soprolife/nucleo-m15/tests/visual/` | **novo** — harness sintético + CDP + PDF fictício + smoke de produção + README |
| `painel-soprolife/docs/m25-21/` | **novo** — capturas sintéticas e `medidas.json` |

Nenhuma migration. Nenhum endpoint alterado. O único arquivo de `app/` tocado
é o gerador do PDF, e só no desenho do selo (seção 12-A) — a correção da UI em
si não encosta em `app/`.

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
| `prod-paciente-1920.png` | **produção**, página real, 1920x1080 |
| `prod-paciente-1366.png` | **produção**, página real, 1366x768 |
| `prod-mobile-430.png` | **produção**, página real, iPhone 430x932 |
| `prod.json` | medições do smoke em produção |
| `selo-pre-assinatura.png` | o selo corrigido, ampliado |
| `selo-qualificado.png` | o selo do ramo ICP-Brasil, inalterado |
| `laudo-pre-assinatura-completo.png` | a página inteira do laudo sintético |

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

### Git — concluído

| passo | resultado |
| --- | --- |
| commit | `85a0180` |
| push da branch | `claude-m25-21-ui-medica-premium` → origin, novo branch |
| `painel-soprolife-v01` | **fast-forward** `1ae5e23..85a0180`, sem `--force` |
| verificação de FF | `git merge-base --is-ancestor origin/painel-soprolife-v01 HEAD` antes do push |
| migration | nenhuma criada, nenhuma executada |
| banco | não tocado — o diff não encosta em `app/`, `migrations/` nem em serviço |

### VPS — aplicado

O primeiro acesso parou numa checagem interativa do Tailscale SSH
(`# Tailscale SSH requires an additional check`). Depois da autenticação
humana, o deploy foi executado.

Nota para a próxima vez: o FQDN `soprolife-painel-01.tailcaf0e4.ts.net`
**não está** no `known_hosts` desta máquina — só o nome curto
`soprolife-painel-01` e o IP `100.87.98.100`. Conectar pelo FQDN devolve
`Host key verification failed`, que **não** é a VPS recusando: é um nome ainda
não fixado. Use o nome curto ou o IP.

```
cd /opt/soprolife/soprolife-site
git fetch origin painel-soprolife-v01
git merge --ff-only origin/painel-soprolife-v01
Updating 1ae5e23..611a7ec
Fast-forward
```

| verificação | antes | depois |
| --- | --- | --- |
| HEAD da VPS | `1ae5e23` | **`611a7ec`** |
| working tree | limpo | limpo |
| branch | `painel-soprolife-v01` | `painel-soprolife-v01` |
| arquivos de produção no diff | — | `css/report-workflow.css`, `js/report-workflow.js`, `index.html` |
| diff em `migrations/` ou `app/` | — | **vazio** |

### Reinício de serviço — verificado como desnecessário, e não feito

O painel é servido por `command-center-local-server.py`
(`soprolife-painel.service`, 100.87.98.100:8765), que lê os arquivos do disco a
cada requisição. Todo o diff de produção é asset estático. Confirmado **sem
reiniciar nada**:

```
index.html aponta para:  report-workflow.css?v=2026081002
                         report-workflow.js?v=2026081002
css/report-workflow.css?v=2026081002   HTTP 200   50.985 bytes
js/report-workflow.js?v=2026081002     HTTP 200  151.167 bytes
```

`ActiveEnterTimestamp` das duas units continua anterior a esta etapa
(`soprolife-painel` 10/08 01:06, `soprolife-m15-api` 10/08 14:36): nenhum
serviço foi reiniciado.

### Os bytes em produção são os bytes testados

```
sha256 local                          sha256 servido em produção
f3f863a4…cd5d5e  report-workflow.css  f3f863a4…cd5d5e   ✅ idêntico
117ee504…7f09d4  report-workflow.js   117ee504…7f09d4   ✅ idêntico
```

### HTTPS público (Tailscale serve)

```
index   HTTP 200
css     HTTP 200
js      HTTP 200
health  HTTP 200
```

### Health e banco

```json
{"status":"ok","versao":"0.1.0","ambiente":"prod","banco":"ok",
 "agora_utc":"2026-08-11T02:01:44Z"}
```

| banco `soprolife_m15` | antes | depois |
| --- | --- | --- |
| `alembic_version` | `e7c4b03a91df` | **`e7c4b03a91df`** (inalterado) |
| tabelas em `public` | 47 | **47** |
| `report_documents` | 5 | **5** |
| `external_signed_documents` | 0 | 0 |

**Nenhuma migration executada. Banco não alterado.**

---

## 12. Smoke visual em produção

Executado contra
`https://soprolife-painel-01.tailcaf0e4.ts.net/painel-soprolife/index.html` —
a página real, com o CSS e o JS que a médica vai baixar, dentro do shell real
do Command Center (barra lateral, cabeçalho, largura útil de 1624px em 1920).

**Nenhum laudo real foi lido, aberto, concluído ou alterado.** A página é
carregada de verdade; só então o cliente autenticado é substituído por um dublê
que devolve payloads inventados com os envelopes reais da API. O dublê
intercepta `api()` antes de qualquer requisição, então nenhuma chamada à base
de laudos sai do navegador. Roteiro somente leitura.

Script: `nucleo-m15/tests/visual/prod_smoke.py`. Evidência:
`docs/m25-21/prod-*.png` e `docs/m25-21/prod.json`.

| verificação | resultado |
| --- | --- |
| Central sem "undefined" | ✅ título "Aguardando assinatura qualificada", selo `3`; `tem_undefined: false`, `tem_nan: false` em todas as larguras |
| "Meus laudos" largo e legível | ✅ cartões em 2 colunas, nome protagonista, badge de status, códigos terciários |
| abrir paciente **não** colapsa o layout | ✅ `bancada_dentro_do_resumo: false`; bancada 1619px = 100% da raiz (1619px) |
| resumo paciente / exame / local | ✅ 3 cartões horizontais, sem quebra letra por letra |
| MIR e laudo lado a lado no desktop | ✅ visualizador com **799px** de altura em 1920x1080; **568px** em 1366x768 |
| conclusões em grade | ✅ 3 colunas em 1920x1080, 2 em 1366x768 |
| documentos do exame em largura normal | ✅ 1585px de 1619 |
| assinatura externa funcionando | ✅ fila, seleção, ações e o envio de assinados renderizam (apenas conferidos — **nada foi baixado nem enviado**) |
| sem overflow horizontal | ✅ em 1920, 1366 e 430 |
| iPhone 430x932 | ✅ empilhado, central funcional, sem overflow |

### O que este smoke NÃO cobre

Ele prova que **os bytes publicados produzem esta tela**. Ele não entra na
conta da médica — não há credencial nesta sessão, e entrar seria manipular
sessão clínica real. Falta a olhada final de quem tem a conta, com os dados
dela: abrir o painel, conferir que a central mostra a contagem certa e que
abrir um paciente real não colapsa nada. Como os bytes servidos são
byte-idênticos aos testados (seção 11), essa olhada é **confirmação**, não
verificação pendente.

---

## 12-A. Adendo — o carimbo que não sobrevivia ao próprio prazo

Correção pequena e obrigatória, pedida depois do deploy da UI.

### O problema

O selo do laudo pré-assinatura tinha quatro linhas:

```
   CONCLUÍDO          →  CONCLUÍDO
   PELA MÉDICA           PELA MÉDICA
   ─────────
   AGUARDANDO
   ASSINATURA
```

As duas de baixo eram **verdadeiras no instante em que o PDF era gerado e
falsas alguns minutos depois**. O fluxo é este: a médica baixa **exatamente
este arquivo**, aplica a assinatura qualificada nele por fora (VIDaaS, com o
certificado dela) e devolve o mesmo PDF assinado. A assinatura entra na camada
PDF; o desenho do selo continua impresso como saiu daqui. O documento **já
assinado** ficaria carimbado "AGUARDANDO ASSINATURA" para sempre — e quem o
recebesse leria, no próprio arquivo, a negativa mais forte que existe sobre
ele.

"CONCLUÍDO PELA MÉDICA" não tem esse problema: é um fato sobre o ato clínico,
permanece verdadeiro antes e depois da assinatura, e não afirma nada sobre
ICP-Brasil.

### O que mudou

`app/services/report_native_pdf.py`, `draw_signature_type_seal()`:

- **ramo não-qualificado** — duas linhas, centralizadas no anel (dy +4,6 /
  −4,4, corpo 6,6 / 5,6). A régua divisória saiu junto: ela separava duas
  afirmações, e agora há uma só;
- **ramo qualificado** — **idêntico**. Continua com quatro linhas,
  "ASSINADO / DIGITALMENTE / ICP-BRASIL / PADRÃO PAdES".

Conferido visualmente: os dois selos renderizados a 150 dpi, texto folgado
dentro do anel nos dois casos.

### O que não foi tocado

Verificado no PDF gerado: rubrica, nome da médica, CRM-RJ 52.62307-5, RQE
58224, selo institucional SoproLife, código de verificação, declaração de
hash SHA-256, conclusão, observações, códigos LAU/ESP, versionamento e a faixa
"DOCUMENTO LIBERADO".

**A negativa continua no rodapé** (`RELEASE_STATEMENT`), e era ela que
carregava a informação útil — onde conferir a assinatura de verdade. Ela é
texto que descreve o que o arquivo **é**, não um estado que expira.
"ICP-Brasil" continua aparecendo exatamente **uma vez**, na frase que a nega.

### O estado operacional não saiu de lugar nenhum

Continua onde sempre esteve, no Centro de Comando, que sabe a hora em que cada
um deixa de valer — coisa que um carimbo impresso não sabe:

`Aguardando assinatura qualificada` · `Assinado recebido — validação pendente`
· `Pronto para entrega` · `Entregue`

### Nada retroativo

O gerador roda na **emissão**, e o resultado vira versão imutável no
armazenamento. Um teste amarra as duas pontas: `report_storage.py` não conhece
o gerador, e **todas** as chamadas a `_native_pdf_bytes` estão dentro de
funções que criam versão nova (`compose_native_report_preview`,
`finalize_review_for_signature`, `sign_and_release_report`,
`add_report_addendum`, `start_qualified_signature`). A rota de download
(`download_report_version`) não constrói PDF. Laudos já emitidos mantêm os
bytes — e o hash — de então. **Só os novos saem com o selo corrigido.**

### Testes

`test_m25_21_selo_pdf_pre_assinatura.py` — **16 testes**, exatamente as quatro
provas pedidas e mais as guardas de escopo:

| prova | teste |
| --- | --- |
| PDF pré-assinatura contém "CONCLUÍDO PELA MÉDICA" | `test_selo_pre_assinatura_diz_concluido_pela_medica` |
| PDF pré-assinatura **não** contém "AGUARDANDO ASSINATURA" | `test_selo_pre_assinatura_nao_diz_aguardando_assinatura` |
| a UI ainda contém "Aguardando assinatura qualificada" | `test_estado_operacional_continua_na_interface`, `test_a_fila_da_medica_continua_rotulando_o_que_aguarda_assinatura` |
| a M25.20 continua identificando o PDF depois da assinatura | `test_o_pareamento_do_retorno_nao_depende_do_texto_do_selo`, `test_o_texto_que_a_m25_20_procura_continua_no_pdf` |

Dois detalhes que mudaram a forma das asserções:

1. **A asserção mira "AGUARDANDO", não "ASSINATURA".** A palavra "assinatura"
   aparece legitimamente no rótulo acima da rubrica e na frase que nega a
   ICP-Brasil. Quem só existia no carimbo era "AGUARDANDO".
2. **A presença de "PELA MÉDICA" é contada, não checada.** O rodapé já diz
   "concluído pela médica responsável" — procurar presença passaria mesmo com
   o selo apagado. A **dupla** ocorrência é o que só existe com o carimbo
   desenhado (pré-assinatura = 2; qualificado = 1).

O pareamento do retorno usa `META_REPORT_CODE` (metadado do PDF), o regex
`\bLAU-\d{6}\b` sobre o texto e o código de verificação — **nada que leia o
carimbo**. Se lesse, remover duas linhas dele quebraria o retorno do lote; o
teste trava essa independência.

Dois testes anteriores afirmavam o texto antigo
(`test_m25_2_native_report.py`, `test_m25_18_assinatura_externa.py`) e foram
invertidos, com o motivo registrado no lugar.

**Verificação de que o teste pega a regressão:** rodado contra o
`report_native_pdf.py` de `HEAD`,
`test_selo_pre_assinatura_nao_diz_aguardando_assinatura` e
`test_o_selo_pre_assinatura_tem_duas_linhas_e_nao_quatro` falham.

**Suíte completa: 1270 passed, 30 skipped.**

### Deploy do adendo — aqui o restart FOI necessário

Diferente do deploy da UI: este commit toca `app/`, e o módulo Python fica
carregado na memória do processo. Servir o arquivo novo do disco não bastaria.

```
cd /opt/soprolife/soprolife-site
git merge --ff-only origin/painel-soprolife-v01
Updating 51dba59..1604ba1

diff em app/:         painel-soprolife/nucleo-m15/app/services/report_native_pdf.py
diff em migrations/:  (vazio)

systemctl restart soprolife-m15-api.service
```

| verificação | resultado |
| --- | --- |
| HEAD da VPS | `1604ba1` |
| serviço | `active`, MainPID 1409973 → **1420857**, sem erro no journal |
| health | `{"status":"ok","banco":"ok","ambiente":"prod"}` |
| `alembic_version` | `e7c4b03a91df` — inalterado |
| tabelas / laudos | 47 / 5 — inalterados |

**Prova de que o código publicado produz o selo corrigido**, executada no
próprio venv da VPS, com laudo sintético em memória — sem tocar no banco, sem
gravar arquivo, sem criar versão de documento:

```
PRE-ASSINATURA  | 'AGUARDANDO': 0 | 'PELA MÉDICA': 2 | ICP-BRASIL: 1
QUALIFICADO     | PADRÃO PAdES: 1 | 'AGUARDANDO': 0
RESULTADO: OK
```

---

## 13. Estado final

| item | valor |
| --- | --- |
| HEAD inicial | `1ae5e230b0e8ce9f206697e711fa3cf17cb8a960` |
| HEAD final (branch, `painel-soprolife-v01` e VPS) | `1604ba1d4e7cee822884e4c74bcfc120f704e411` |
| suíte | 1270 passed, 30 skipped |
| migrations | nenhuma |
| banco | `alembic_version` `e7c4b03a91df` antes e depois; 47 tabelas; 5 laudos |
| health | `{"status":"ok","banco":"ok","ambiente":"prod"}` |
| HTTP | 200 em `index.html`, CSS, JS e `/api/m15/health` por HTTPS |
| serviços | UI: **não reiniciados** (asset estático). Adendo do selo: `soprolife-m15-api` reiniciado, porque toca `app/` |

_(o commit final de fechamento do relatório entra depois desta tabela; o HEAD
publicado passa a ser o dele.)_

### Pendências

1. **Olhada final na conta da médica** — a única coisa que faltou. Abrir o
   painel com a conta real e conferir a contagem da central e um paciente
   aberto. Não é verificação pendente: os bytes servidos são byte-idênticos
   aos testados e o layout já foi conferido na página real de produção
   (seção 12). É confirmação de quem opera.
2. **`:has()`** — `.report-queue-item:has(.report-queue-pick)` reserva espaço
   para a caixa de seleção do lote. Suportado em Safari 15.4+ e Chrome 105+;
   onde não houver, o pior caso é um nome muito longo passar por baixo da
   caixa — nunca um controle inacessível.
5. **Lote M25.8** — continua no código, recolhido. Se a central da M25.20 se
   confirmar como caminho único na operação, o bloco pode ser aposentado numa
   etapa própria, com a decisão registrada. Esta missão não fez essa escolha.
