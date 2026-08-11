# Inspeção visual da conta médica (M25.21)

Bancada de teste visual com **dados 100% fictícios**. Nenhum paciente, exame,
laudo ou clínica real passa por aqui — o cliente autenticado é substituído por
um dublê que devolve payloads inventados, com os **mesmos envelopes** que a API
devolve de verdade (foi um envelope mal interpretado que produziu o
"Aguardando assinatura qualificada — undefined" da M25.20).

Existe porque teste unitário não vê layout. A M25.20 passou em toda a suíte e
mesmo assim entregou a bancada clínica espremida numa faixa de 300px.

## Arquivos

| arquivo | o que é |
| --- | --- |
| `harness.html` | página que carrega o CSS e o JS REAIS do painel com um `window.SoproM15` falso |
| `fake_pdf.py` | gera um PDF fictício com a densidade de um laudo de equipamento (tabela + curvas), para julgar a legibilidade do visualizador |
| `shots.py` | Chrome headless via CDP: navega, abre um paciente, mede a página e captura |

O harness **não fica** dentro de `painel-soprolife/`: ele é copiado para lá na
hora de rodar e removido depois. Uma página que dispensa autenticação não pode
ser servida junto do painel.

## Como rodar

```bash
PANEL=painel-soprolife
SAIDA=/tmp/m25-21-shots

python3 nucleo-m15/tests/visual/fake_pdf.py "$PANEL/_harness_exemplo.pdf"
cp nucleo-m15/tests/visual/harness.html "$PANEL/harness.html"

(cd "$PANEL" && python3 -m http.server 8791 --bind 127.0.0.1 &)
google-chrome --headless=new --disable-gpu --remote-debugging-port=9341 \
  --user-data-dir=/tmp/m25-21-chrome about:blank &
sleep 3
curl -s http://127.0.0.1:9341/json/version > /tmp/m25-21-version.json

python3 nucleo-m15/tests/visual/shots.py \
  http://127.0.0.1:8791/harness.html "$SAIDA" /tmp/m25-21-version.json

rm -f "$PANEL/harness.html" "$PANEL/_harness_exemplo.pdf"
```

Requer `websockets` e `reportlab` (ambos já nas dependências de teste) e um
Chrome/Chromium local.

## O que o `shots.py` mede

Além das capturas, ele devolve `medidas.json` com o que os olhos deveriam
conferir e a memória não guarda:

- `bancada_dentro_do_resumo` — precisa ser `false`. Se virar `true`, a bancada
  voltou para dentro da coluna da central de assinatura (a falha da M25.20);
- `largura_bancada` × `largura_janela` — a bancada usa a largura útil inteira;
- `largura_col_assinatura` / `largura_col_meus_laudos` — a proporção ~34/66 da
  faixa de resumo;
- `altura_mir` — o visualizador do exame técnico em altura de leitura;
- `colunas_conclusoes` — quantas colunas a grade de siglas formou de fato;
- `overflow_horizontal` — precisa ser `false` em toda largura;
- `tem_undefined` / `tem_nan` / `selo_assinatura` — nenhuma dessas palavras
  pode aparecer no texto renderizado, em nenhum cenário.

Cenários: `a-lista-medica` (fila, sem paciente aberto), `b-paciente-aberto`
(bancada completa) e `c-central-vazia` (nenhum laudo aguardando assinatura —
o caso em que a contagem precisa ser `0`, e não `undefined`).

Cada cenário rende duas capturas: `-viewport` (a tela real: 1920x1080,
1366x768, 430x932…) e a de página inteira. Alturas em `vh` só significam algo
na primeira; na de página inteira elas ficam infladas pelo redimensionamento.
