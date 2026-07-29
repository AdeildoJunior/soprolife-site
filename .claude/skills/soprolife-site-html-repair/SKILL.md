---
name: soprolife-site-html-repair
description: Reparo estrutural de HTML do site SoproLife (soprolife-site) — detecta HTML/JS preso dentro de <style>, blocos e tags nao fechados, containers desbalanceados, ids e atributos duplicados, JS morto por comentario "//" achatado, footer/barra WhatsApp/bibliotecas duplicados; recupera conteudo do historico Git, valida no navegador e publica com relatorio TXT. Usar ao corrigir corrupcao estrutural de qualquer *.html do site (nao usar para redesign, SEO ou consolidacao de CSS).
---

# Reparo estrutural de HTML — site SoproLife

Escopo: **somente** defeito estrutural. Proibido redesign, reescrita de SEO,
reescrita de texto medico e consolidacao de CSS.

## 1. Detectar (varredura antes de tocar em qualquer arquivo)

Rodar sobre todos os `git ls-files '*.html'` e classificar:

| Classe | Sinal |
|---|---|
| HTML/JS preso em `<style>` | dentro do bloco, apos remover `/* */` e strings: `</script`, `<div`, `<script`, `if(!`, `document.getElementById`, `addEventListener` |
| Bloco cru nao fechado | `<style>`/`<script>` sem o `</style>`/`</script>` correspondente |
| CSS nao fechado | `{` != `}` no bloco (ignorando comentarios) |
| Containers desbalanceados | `div`, `section`, `header`, `main`, `aside`, `footer`: abre != fecha, `stray-close`, `never-closed` |
| Id duplicado | inclui ids em `<style id>` / `<script id>` (mascarados por parsers ingenuos) |
| Atributo duplicado | ex.: `data-sl="wa" data-sl="wa"` |
| JS morto por `//` achatado | `<script> // Comentario (function(){ ...` numa unica linha comenta o arquivo inteiro |
| Duplicacao de blocos | mais de um `<footer`, `.sl-whatsapp-bar`, mesmo `<script src>` |
| JSON-LD invalido | `JSON.parse` falha |

Assinatura tipica de `sed` guloso (destroi um trecho e cola JS dentro do CSS):

```
#depoimentos .sl-tarrow--left{left:16px !important; if(!root) return; ... </script>
```

Falsos positivos comuns: markup dentro de `url("data:image/svg+xml;...")` e
mencao a `<style>` em comentario CSS — mascarar comentarios e strings antes.

## 2. Recuperar

1. **Historico Git primeiro.** Achar o commit que introduziu a corrupcao:
   `for c in $(git log --format=%h -- <arquivo>); do echo "$c $(git show $c:<arquivo> | grep -c '<assinatura>')"; done`
   e restaurar o trecho **literalmente** de `<commit>^`.
2. Se nao houver versao integra, usar uma **pagina irma ja reparada apenas como
   referencia estrutural** (wrappers e fechamento de CSS) — nunca copiar texto.
3. Nao inventar texto medico. Conteudo irrecuperavel: preservar o que existe,
   reconstruir so o markup necessario e **documentar a lacuna** no relatorio.
4. Marcar o trecho restaurado com comentario `<!-- SOPRO:BLOCO_RESTAURADO ... -->`
   citando o commit destruidor e o commit de origem.
5. Ao reinserir um trecho antigo, alinhar links de WhatsApp/CTA a mensagem
   **ja usada na propria pagina**, e registrar isso no relatorio.

## 3. Preservar (verificar depois de cada edicao)

`title`, `description`, `canonical`, `robots`, Open Graph, JSON-LD, URLs e links
internos, horarios e unidades de agendamento, regras Pastore, mapas, analytics
(GA4/Pixel), WhatsApp institucional `5521998901775`, texto medico, layout
responsivo e identidade visual.

## 4. Testar

- varredura estrutural = 0 ocorrencias em **todos** os `*.html`;
- parser estrito (`parse5`) e comparacao do esqueleto DOM antes/depois;
- `JSON.parse` de todo `application/ld+json`;
- links internos resolvem para arquivo existente;
- `node --check` no JS alterado; `git diff --check`;
- servidor local em **porta livre** (nunca matar processo alheio);
- navegador headless em **1440 / 768 / 390 px**: zero erro de console, zero
  scroll horizontal, tabs, CTAs, WhatsApp, rodape e navegacao funcionando;
- screenshots antes/depois de cada pagina alterada (copia limpa de `HEAD` via
  `git archive HEAD | tar -x -C before/`). Diferenca visual so e aceitavel se
  for **consequencia direta** do reparo — descrever no relatorio.

## 5. Publicar

```
git status --short && git diff --stat && git diff --check
git add <apenas os arquivos reparados>
git commit -m "fix: <descricao do reparo>"
git push -u origin HEAD
BR=$(git rev-parse --abbrev-ref HEAD)
git switch main && git pull --ff-only origin main
git merge --no-ff "$BR" -m "merge: <descricao>"
# conflito => parar sem push
git push origin main
```

Depois: conferir HTTP 200 das URLs publicas alteradas.

**Nunca** usar `sudo`, `rm -rf`, `git reset --hard`, `git clean`.
Nunca expor credenciais nem dados de paciente.

## 6. Relatorio (obrigatorio)

TXT em portugues em
`/home/$USER/soprolife/auditorias/RELATORIO_<ASSUNTO>_YYYYMMDD-HHMMSS.txt` com:
arquivos varridos, problemas achados e corrigidos, conteudo recuperado ou
irrecuperavel, arquivos alterados, testes, hashes de commit/merge, URLs publicas
e status HTTP, `git status` final, pendencias, e confirmacao de que nenhum dado
de paciente foi introduzido.
