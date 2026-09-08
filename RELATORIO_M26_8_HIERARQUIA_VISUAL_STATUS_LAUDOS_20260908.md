# M26.8 — Hierarquia visual dos status de laudos

**Data:** 08/09/2026
**Branch de trabalho:** `claude-m26-8-hierarquia-visual-status`
**Base:** `origin/painel-soprolife-v01` em `bf04dfd`
**Natureza:** UI/UX dos estados. Sem migration, sem mudança de regra clínica,
de fluxo, de RBAC, de financeiro, de PDF ou de portal do paciente.

---

## 1. O que a Dra. Ana relatou, e o que a auditoria encontrou

O relato foi: **os cartões da fila são parecidos demais** — mesma fonte, mesmo
fundo, mesma cor — e ela precisava ler cartão por cartão para descobrir o que
faltava laudar, o que faltava assinar e o que já estava assinado.

A auditoria do código confirmou o relato e mediu a causa. No navegador, com
dados fictícios, os nove cartões da fila tinham:

| medida | antes |
| --- | --- |
| cores de fundo distintas na fila inteira | **1** (branco em todos) |
| faixas laterais distintas | **1** (nenhuma faixa) |
| estados com regra de cor de selo, de 8 possíveis | **3** — e uma delas errada |

E encontrou algo que o relato não podia ver — **um erro de significado, não de
estética**:

```css
/* css/report-workflow.css, antes da M26.8 */
.report-liberado,
.report-liberado-flag {
  background: var(--ok-soft);   /* VERDE */
  color: #0b7355 !important;
}
```

`liberado` é o estado cujo próprio rótulo, no mesmo arquivo, diz:

> **"Concluído — aguardando assinatura qualificada"**

Ou seja: o laudo clínico está pronto, o **PDF assinado não existe**, e a médica
ainda precisa levá-lo ao certificado dela fora do painel. Ele era pintado com o
**mesmo verde de `assinado`**, e aparecia lado a lado com ele na mesma fila.

Havia mais três pontos onde verde afirmava assinatura que não existia:

* `.report-queue-flag.is-locked` — a marca "concluído" (conteúdo **congelado**,
  não assinado) era verde;
* o selo do **ativo de assinatura manuscrita** ("Cadastrada") emprestava a
  classe `report-liberado-flag` — uma imagem PNG cadastrada ficava com a cor de
  documento assinado;
* na fila de entrega, `assinado_recebido_validacao_pendente` — cujo rótulo é
  **"Exceção técnica — documento sem aceite"** — tinha faixa **teal**, a cor de
  destaque positivo do painel.

---

## 2. Estados reais encontrados e a família atribuída a cada um

Nada foi inventado e nada foi renomeado. Os estados vêm de
`nucleo-m15/app/models.py` e `nucleo-m15/app/routers/reports.py`, e os testes
leem essas constantes diretamente — um estado novo no banco quebra a suíte
antes de chegar à tela.

### 2.1 `report_documents.status` — `STATUS_LAUDO_VALUES` (a fila da médica)

| estado | rótulo na tela | família | cor |
| --- | --- | --- | --- |
| `atribuido` | Pendente de laudo | `warning` | **âmbar** |
| `em_elaboracao` | Em elaboração clínica | `info` | **azul** |
| `assinatura_pendente` | Laudado — aguardando assinatura qualificada | `signature` | **roxo** |
| `liberado` | Concluído — aguardando assinatura qualificada | `signature` | **roxo** ← *era verde* |
| `assinado` | Assinado — assinatura conferida | `success` | **verde** |
| `rascunho` (legado M24A) | `rascunho` | `info` | azul |
| `em_revisao` (legado M24A) | `em_revisao` | `info` | azul |
| `finalizado` (legado M24A) | `finalizado` | `signature` | roxo |

Os três legados M24A não nascem mais de nenhum endpoint, mas documentos antigos
estão neles e a fila precisa saber pintá-los. Todos são **anteriores** à
assinatura: `finalizado` entra em `SIGNATURE_STATUS_PENDENTE` por construção
(comentário em `app/models.py`), então roxo é a leitura honesta — nenhum deles é
verde.

### 2.2 Fila administrativa de entrega — `FILA_ROTULOS`

| estado | rótulo | família | cor |
| --- | --- | --- | --- |
| `aguardando_laudo` | Aguardando laudo | `warning` | âmbar |
| `aguardando_assinatura` | Aguardando assinatura | `signature` | roxo |
| `assinado_recebido_validacao_pendente` | Exceção técnica — documento sem aceite | `danger` | **vermelho** ← *era teal* |
| `pronto_para_entrega` | Pronto para entrega | `success` | verde |
| `entregue` | Entregue | `success-muted` | verde suave |

### 2.3 Os dois domínios que ficaram de fora, e por quê

A primeira versão desta etapa também mapeou `external_signed_documents.status`
e o ciclo VIDaaS, "para o futuro". Os dois saíram, por dois motivos.

O primeiro é que **nenhum dos dois pinta coisa alguma**: o estado do documento
assinado chega à tela já derivado em `estado` pelo servidor
(`_estado_de_entrega`), e o painel da assinatura qualificada é uma linha de
texto, não um cartão. Mapa que ninguém consulta é código morto que envelhece
calado.

O segundo é concreto e foi a suíte que apontou: escrever
`validado_externamente` no mapa fez o nome **reaparecer no JS**, e a M25.29E
guarda exatamente isso — nada na tela administrativa pode afirmar validação de
assinatura, porque o sistema não verifica cadeia ICP-Brasil. O guard estava
certo e a inclusão especulativa estava errada.

Quando um desses estados for para a tela, o domínio volta — e a regra do verde
vale para ele desde o primeiro commit. Há um teste que guarda essa fronteira
nos dois sentidos (`test_dominios_do_mapa_sao_exatamente_os_que_pintam_algo`).

### 2.4 A regra, e por que ela é mecânica agora

> **VERDE É RESERVADO A DOCUMENTOS JÁ ASSINADOS OU ETAPAS POSTERIORES À
> ASSINATURA.**

Os **três** — e apenas três — pontos de todo o sistema que recebem verde:

```
laudo/assinado        entrega/pronto_para_entrega        entrega/entregue
```

`laudo/assinado` é o PDF assinado que voltou e passou nas guardas documentais.
`entrega/pronto_para_entrega` é derivado, no servidor, de um documento assinado
em `recebido_assinado` ou `validado_externamente` (`_FILA_POR_ASSINADO`) — os
dois estados em que o arquivo foi aceito. `entrega/entregue` é posterior a
ambos. O teste
`test_verde_e_exatamente_a_allowlist_de_estados_pos_assinatura` compara esse
conjunto, calculado executando o código de produção, contra a lista fechada
acima. Ele não pergunta "`liberado` deixou de ser verde?" — pergunta **"o que
ficou verde?"**. Acrescentar um item ali passa a ser uma decisão explícita sobre
assinatura, não um efeito colateral de cor.

**Estado desconhecido cai em `neutral` (cinza), nunca em verde.** Cinza é
"ninguém explicou este estado ainda"; verde seria uma afirmação sobre assinatura
que ninguém fez.

---

## 3. A linguagem visual

Fonte única em `js/report-workflow.js`: `STATUS_FAMILIES`, **um mapa por
domínio**. Domínios separados porque `rascunho` existe no ciclo do laudo e no da
assinatura qualificada com sentidos diferentes — num mapa único, um herdaria a
cor do outro em silêncio, que é a forma exata do defeito corrigido aqui.

Cada família define cinco tokens CSS num lugar só, e **fundo, faixa lateral e
selo derivam todos deles** — é isso que impede faixa, fundo e texto de
discordarem:

| família | faixa | fundo do cartão | tinta do selo |
| --- | --- | --- | --- |
| `warning` | `#d98a12` | `#fff9ee` | `#7a4d06` |
| `info` | `#2f7fc4` | `#f3f8fd` | `#12466f` |
| `signature` | `#7c5cc4` | `#f8f5fd` | `#4a2f86` |
| `success` | `#1fa87a` | `#f0f9f5` | `#0a6448` |
| `success-muted` | `#7fae9c` | `#f5f8f7` | `#2f5647` |
| `danger` | `#d3494a` | `#fef4f4` | `#8f2222` |
| `neutral` | `#a9b7c8` | `#f8fafc` | `#4a5b70` |

Um teste confere o **matiz no pixel**: uma faixa roxa batizada de `success`
passaria em qualquer teste de nome de classe e mentiria na tela.

### Não depender só de cor

Cada cartão combina **quatro** sinais:

1. **fundo** pastel muito leve;
2. **faixa lateral** de 5px, mais definida;
3. **selo textual** com o estado por extenso, em caixa alta e negrito;
4. **ícone** em SVG — triângulo de atenção (falta laudar), lápis (em
   elaboração), caneta sobre a linha de assinatura (falta assinar), tique
   (assinado), tique duplo (entregue), círculo com X (exceção), traço (neutro).

Os ícones são SVG, não glifos de fonte: num iPhone o mesmo caractere viraria
emoji colorido e competiria com a cor semântica do cartão. São `aria-hidden` —
o estado já está escrito por extenso ao lado, e o ícone **reforça**, não
substitui. Em `forced-colors: active` a faixa vira `CanvasText` e o selo ganha
borda visível, então o cartão nunca depende do matiz.

### Contraste (WCAG), medido no navegador

| | antes | M26.8 |
| --- | --- | --- |
| selo de estado (texto sobre o próprio fundo) | 5,25 – 14,58 | **6,08 – 8,37** |
| contexto do cartão (local, data) | **3,99** ✗ | **5,49 – 5,66** ✓ |

O contexto do cartão estava **abaixo de AA** antes desta etapa: `--muted`
(`#6e80a0`) dava 3,99:1 sobre branco, em texto de 12px. Sobre os fundos pastel
ficaria pior ainda, então a M26.8 introduziu um tom próprio (`#54657f`) para o
texto de apoio dentro de cartões de status — ≥ 5:1 nas sete famílias. É uma
melhoria de acessibilidade que a etapa trouxe de carona, não uma regressão
compensada.

### Prioridade visual

Âmbar (falta laudar) → roxo (falta assinar) → verde (assinado/pronto) → verde
suave (entregue, histórico). `danger` é exceção e não compete nessa fila: ele
aparece raramente, e quando aparece é para ser notado.

---

## 4. Onde foi aplicado

| tela | o que mudou |
| --- | --- |
| **Meus laudos** (fila da médica) | cartão com fundo + faixa + selo com ícone |
| **Assinatura externa** (central da médica) | idem; todos os cartões são `liberado`, garantido no servidor |
| **Acompanhamento operacional** (admin) | o estado deixou de ser um `<span>` cru e virou selo |
| **Fila de laudos / entrega** (admin) | linhas com fundo + faixa; os chips de filtro ganharam a família |
| **Cabeçalho do laudo aberto** | o selo de estado usa a mesma família |

**Filtros.** Na fila de entrega, cada chip inativo recebe apenas um ponto na cor
da faixa; só o **ativo** ganha o fundo da família. "Todos" fica neutro — não é um
estado. Na fila clínica o filtro é um `<select>` nativo, onde não há como colorir
`<option>` de forma confiável (o iOS o transforma numa roda do sistema); ele foi
deixado como está, e a frase de ajuda abaixo dele continua explicando o estado
escolhido.

**Nenhuma fila mudou de conteúdo.** Os filtros de cada lista, as opções do
`<select>`, o que entra na central de assinatura e o que entra na fila de
entrega continuam exatamente os mesmos — há testes de regressão para cada um.

### Duas escolhas que merecem registro

**O selo curto na central de assinatura.** Lá, *todos* os cartões estão no mesmo
estado e o título da seção já diz qual. Repetir "Concluído — aguardando
assinatura qualificada" em cada linha custava **60px de altura por cartão** num
iPhone (107 → 167px, medido). O selo usa o texto curto **"Aguardando
assinatura"**, que não é invenção desta tela: é o rótulo que o próprio servidor
já usa para este ponto do percurso (`FILA_ROTULOS["aguardando_assinatura"]`). O
estado e a cor por trás dele continuam sendo os mesmos.

**Hover e seleção deixaram de repintar o fundo.** A M25.21 separou o hover
("convite": borda teal + fundo levíssimo) da seleção ("fato": faixa navy à
esquerda + fundo teal-pale). Fundo e faixa esquerda agora pertencem à família do
estado — se a interação continuasse repintando, o cartão sob o cursor perderia a
etapa, que é o sintoma relatado, reintroduzido pela interação. A distinção
permanece por outro meio: hover = borda teal + sombra rasa; seleção = **anel teal
em volta do cartão inteiro**. Há teste para isso.

---

## 5. Um defeito encontrado ao medir, e corrigido

O selo novo na central de assinatura expôs um bug de layout **latente** desde a
M25.20:

`.report-signature-list` é uma grade com `max-height: 340px; overflow-y: auto`.
O Chrome comprimia as três linhas para caber nos 340px em vez de rolar — cada
cartão ficava com **106,7px** (= (340 − 4 − 16) / 3) e o conteúdo vazava por
baixo da própria borda. Com 107px de conteúdo o vazamento não aparecia; com 137px
(o selo entrou), aparecia — o texto do estado ficava cortado ao meio em 360px de
largura.

`align-content: start` **não** resolve (medido no Chrome 151: as linhas continuam
em 106,7px). O que amarra a linha ao conteúdo é `grid-auto-rows: min-content`. A
rolagem, que é o comportamento desejado, continua igual. A fila clínica
(`.report-queue-list`) foi medida no mesmo teste e **não** comprime — não foi
tocada.

---

## 6. Impacto na altura dos cartões

Medido no navegador real, dados fictícios, com o cache desligado:

| largura | fila: antes | fila: M26.8 | Δ máx |
| --- | --- | --- | --- |
| 1920 | 111–129px | 113–146px | +17px |
| 1366 | 111–129px | 113–146px | +17px |
| 430 (iPhone) | 111–129px | 113–146px | +17px |
| 360 | 128–180px | 130–182px | **+2px** |

Central de assinatura: 107 → 154px em 360px (o selo, que ali é obrigatório pela
regra "não depender só de cor", e que já foi encurtado uma vez — ver §4).

`overflow` horizontal: **falso** em todas as larguras testadas (1920, 1440,
1366, 430, 360), antes e depois. Nenhum "undefined"/"NaN" no texto renderizado.

---

## 7. Arquivos alterados

```
 painel-soprolife/css/report-workflow.css                       | +319 −27
 painel-soprolife/js/report-workflow.js                         | +210 −29
 painel-soprolife/nucleo-m15/tests/visual/harness.html          |  +18 −6
 painel-soprolife/nucleo-m15/tests/test_m26_8_hierarquia_visual_status.py | novo
 RELATORIO_M26_8_HIERARQUIA_VISUAL_STATUS_LAUDOS_20260908.md    | novo
```

O harness visual passou a cobrir **todos** os oito estados reais de
`report_documents.status` mais um estado inventado (`estado_fantasma`), que só
existe para provar na tela que um status novo cai em cinza e nunca em verde.

**Nenhum arquivo de backend foi tocado.** Nenhuma migration. Nenhum arquivo
solto em Documents ou na Home.

---

## 8. Testes

`tests/test_m26_8_hierarquia_visual_status.py` — **72 testes**, em quatro grupos:

1. **Cobertura** — todo estado que existe no backend tem família declarada. A
   lista vem de `STATUS_LAUDO_VALUES`, `FILA_ROTULOS`, `ASSINADO_STATUS_VALUES`
   e `QUALIFIED_SIGNATURE_STATUSES`, importados de verdade.
2. **A regra do verde** — executando as funções **reais** em Node (nada é
   copiado para o teste; os trechos são recortados do arquivo de produção): a
   allowlist fechada, estado por estado, e o fallback neutro — inclusive para
   `constructor`, `toString` e `__proto__`, que sem `hasOwnProperty` devolveriam
   uma função do `Object.prototype` no lugar da família.
3. **Não depender só de cor** — todo selo sai com ícone + texto por extenso;
   sete desenhos distintos; contraste WCAG calculado nas sete famílias; a faixa
   existe, é `::before` e não `border-left`; hover e seleção não repintam o
   fundo; a faixa sobrevive em alto contraste.
4. **Regressão** — os conjuntos de estados congelados; os rótulos visíveis
   inalterados; as opções do filtro; o conteúdo de cada fila; médico continua
   sem permissões administrativas; admin continua sem papel médico; nenhuma
   migration nova.

Suíte relacionada (M24A–M26, laudos/relatórios): **1059 passaram, 11 skipped,
2 falharam** — em 20min35s.

As duas falhas, e o que foi feito com cada uma:

1. `test_m25_29e::test_botao_administrativo_fala_em_conferencia_nao_em_validacao`
   — **causada por esta etapa, e corrigida.** O mapa especulativo de
   `external_signed_documents` trazia `validado_externamente`, e a M25.29E
   proíbe esse nome no JS fora da única linha em que ele é regra de negócio. O
   guard estava certo: a correção foi remover os dois domínios que não pintam
   nada (§2.3), não afrouxar o guard de outra milestone.
2. `test_m25_17::test_rubrica_real_nao_esta_versionada` — **pré-existente**.
   Reproduzida no checkout principal em `67ddcd3`, sem nenhuma alteração desta
   etapa. Falha por dois PNGs de documentação da M25.21
   (`painel-soprolife/docs/m25-21/laudo-pre-assinatura-completo.png` e
   `selo-pre-assinatura.png`) cujos nomes casam com o filtro
   "rubrica/assinatura". **Não foi tocada aqui** — é escopo alheio a esta
   missão, mas merece decisão: o guard existe justamente para impedir que uma
   rubrica real seja versionada, e vale conferir o conteúdo desses dois
   arquivos.

Depois da correção, os arquivos diretamente afetados foram re-executados:
`test_m26_8`, `test_m25_29e`, `test_m25_29h`, `test_m25_20` e
`test_m25_21` — **217 passaram**, em 3min44s.

Validação visual em Chrome headless (151.0.7922.108) em 1920, 1440, 1366, 1024,
768, 430 e 360px, com nomes longos, todos os estados e um estado desconhecido.
Somente dados sintéticos — nenhum paciente, exame, clínica ou código real.

**Limitação assumida:** as telas administrativas (fila de entrega,
acompanhamento operacional) não têm cenário no harness da médica. A emissão das
classes está coberta por teste estrutural, e o CSS é o mesmo; a conferência
visual foi feita numa amostra estática construída com as classes reais, fora do
repositório.

---

## 9. Deploy

| | |
| --- | --- |
| commit do conserto | `ee757ce` — `fix(m26.8)` |
| commit dos testes | `7d0ff32` — `test(m26.8)` |
| commit do ajuste pós-suíte | `bc4971b` — `fix(m26.8)` |
| HEAD oficial (`painel-soprolife-v01`) | `bc4971b` (ff-only a partir de `bf04dfd`) |
| HEAD produção (VPS) | `bc4971b` — árvore limpa |

**Nenhum restart.** A mudança é só de arquivo estático (`css/` e `js/`), e o
painel serve estático direto do disco na porta 8765. A API (8015) não foi
tocada; reiniciá-la seria derrubar o serviço sem motivo.

**Nenhuma migration.** Nenhum arquivo `data/*.local.json` ou `data-private/`
envolvido — esta etapa não lê nem escreve dado.

### Serviços, health e smoke

| | |
| --- | --- |
| `soprolife-painel.service` | active (não reiniciado) |
| `soprolife-m15-api.service` | active (não reiniciado) |
| `soprolife-painel-loopback.service` | active (não reiniciado) |
| `soprolife-portal-resultados.service` | active (não reiniciado) |
| painel `:8765/painel-soprolife/` | **200** |
| API `:8015/api/v1/health` | **200** — `{"status":"ok","banco":"ok","ambiente":"prod"}` |
| `check-access.sh` | **exit 0**, zero FALHA/ERRO/VAZAMENTO |

Smoke nos arquivos em disco, que é de onde o painel serve:

| verificação | resultado |
| --- | --- |
| `STATUS_FAMILIES` no JS | 2 ocorrências |
| classes `report-family-*` no CSS | 64 |
| `.report-liberado` (o verde antigo) | **0** |
| `grid-auto-rows: min-content` (correção do clip) | 1 |

Uma nota sobre o smoke: a primeira tentativa fez `curl` no JS pela porta 8765 e
leu 0 ocorrências de `STATUS_FAMILIES`. Não era arquivo velho — era o gate de
autenticação da M25.23 devolvendo `401 {"ok": false, "error": "Sessão
necessária."}`, e o `grep` medindo o corpo do 401. O JS do painel **é** gated;
o CSS e o `index.html` respondem 200. A conferência foi refeita no disco.

As duas linhas `ATENÇÃO` do `check-access.sh` (token/ID no proxy) são
**pré-existentes** e dizem respeito a `command-center-local-server.py`, que
esta etapa não toca.

### Um obstáculo encontrado antes do `pull`

`painel-soprolife/nucleo-m15/tests/visual/` na VPS pertencia ao **root**, sobra
de algum deploy antigo executado como root, e o usuário `soprolife` não
conseguia escrever nele. Como esta etapa altera `harness.html` ali dentro, o
`git pull --ff-only` teria abortado **no meio do checkout** — deixando parte
dos arquivos novos e parte antigos, que é exatamente a falha já registrada em
deploys anteriores deste repositório.

Verificado **antes** de tentar o pull (`touch` de teste no diretório →
`Permission denied`), e corrigido com um `chown` estreito, só naquele
diretório, devolvendo-o ao dono do repositório. Nenhuma alteração de permissão
em massa.

---

## 10. Prova final

**VERDE É RESERVADO A DOCUMENTOS JÁ ASSINADOS OU ETAPAS POSTERIORES À
ASSINATURA.**

Nenhum estado anterior à assinatura recebe verde. `liberado` — "Concluído —
aguardando assinatura qualificada" — é **roxo**. `assinatura_pendente` é
**roxo**. `aguardando_assinatura` é **roxo**. Um estado que ninguém mapeou é
**cinza**. E há um teste que falha se essa fronteira se mover em qualquer
direção.
