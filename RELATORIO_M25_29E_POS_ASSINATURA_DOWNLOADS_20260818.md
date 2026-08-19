# M25.29E — Depois da assinatura: quem faz o quê, e o que o download entrega

**Data:** 18/08/2026
**Branch:** `claude-m25-29e-pos-assinatura-downloads`
**Base:** `f5d3052` (M25.29D já integrada e implantada)

Dois relatos da operação, investigados juntos porque acontecem na mesma tela
e pela mesma razão de fundo: o sistema descrevia o **estado dele** em vez de
dizer **de quem é a próxima ação** — e confiava no navegador para decidir o
que fazer com uma resposta HTTP.

---

## 1. Os dois incidentes

**Da médica.** A Dra. Ana devolve o PDF assinado, o painel responde *"a
validação da assinatura segue pendente"*, e ela entende que ainda falta *ela*
certificar alguma coisa. Não falta. O estado é correto e a etapa seguinte é da
administração.

**Do sócio.** Ao rebaixar documentos na área administrativa, o navegador
entregou um arquivo chamado `conteúdo 5.jsold`.

---

## 2. Causa raiz do `conteúdo 5.jsold`

### 2.1 O backend nunca esteve errado

Verificado no código implantado (`_entregar_pdf`, `reports.py:4586`):

```python
return Response(
    content=stored.data,
    media_type="application/pdf",
    headers={
        "Content-Disposition": content_disposition(nome, disposition="attachment"),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    },
)
```

E confirmado por HTTP autenticado contra fixture sintética: `HTTP 200`,
`application/pdf`, bytes começando em `%PDF`, nome
`<Paciente> - Assinado.pdf` / `<Paciente> - Exame técnico.pdf`.

### 2.2 O frontend baixava por âncora crua

```js
<a class="m15-btn" download
  href="${esc(apiHref(`/laudos/${item.document_id}/exame-tecnico/conteudo`))}">
```

Um `<a download>` salva **qualquer** resposta como arquivo: o `401` em JSON, o
HTML de offline, um asset velho de cache. O nome vem do último segmento da
URL — `conteudo` — o número é o contador de repetições do navegador, e a
extensão é adivinhada pelo `Content-Type` recebido. Nada disso é PDF.

Medido em produção, sem sessão:

```
/api/v1/laudos/<id>/exame-tecnico/conteudo  →  401  application/json
```

O painel **já tinha** o caminho certo desde a M25.18 — `apiBlob`, que rejeita
`!resp.ok`, exige `Content-Type: application/pdf` e lê o nome do
`Content-Disposition`. Os dois botões administrativos simplesmente nunca foram
migrados.

### 2.3 O service worker fantasma

`sw.js` continuava versionado e servido na raiz do site, mas **nenhuma página
o registra** — a busca por `serviceWorker` no repositório inteiro não retorna
nada. Quem visitou o site enquanto o registro existia segue com ele instalado,
com escopo `/`, que cobre `/painel-soprolife/`. A versão antiga:

```js
self.addEventListener('fetch', e => {
  e.respondWith(
    fetch(e.request)
      .then(resp => { caches.open(CACHE_NAME).then(c => c.put(e.request, clone)); return resp; })
      .catch(() => caches.match(e.request).then(m => m || caches.match('/offline.html')))
  );
});
```

1. interceptava todo GET, **inclusive respostas da API**;
2. guardava tudo num cache cujo nome trazia um placeholder de versão nunca
   substituído na publicação — então **o cache jamais invalidava**;
3. na falha, devolvia o cacheado ou `/offline.html` (HTML).

Isso explica o `.jsold` **e** o item "frontend antigo no navegador".

Nenhum arquivo `.jsold` existe no repositório ou na VPS — o nome é do
navegador, não do projeto.

---

## 3. As correções

| # | o que era | o que passou a ser |
| --- | --- | --- |
| 1 | `<a download href>` nos dois downloads | `<button>` → `apiBlob` + `saveBlob`; erro vira mensagem, nunca arquivo |
| 2 | `apiHref()` (só existia para montar aquele href) | removido — código morto |
| 3 | `sw.js` interceptando tudo | worker que se desinstala e limpa caches; **sem handler de `fetch`** |
| 4 | "a validação da assinatura segue pendente" | "PDF assinado recebido com sucesso. Seu trabalho terminou. Aguardando conferência administrativa da SoproLife." |
| 5 | rótulo "Assinado recebido — validação pendente" | "Assinado recebido — aguardando conferência da SoproLife" |
| 6 | botão "Registrar validação da assinatura" | "Registrar conferência do PDF assinado" |
| 7 | fila: "Assinado recebido — validação pendente" | "Assinado recebido — conferência administrativa pendente" |
| 8 | clique frustrado abria lote novo | trava de reentrância nos três downloads |
| 9 | alvo de toque de 40px no celular | 48px, empilhado, largura total |

**Nenhum valor persistido mudou.** `recebido_validacao_pendente` continua
sendo o valor gravado no banco — mudou só o rótulo. A etapa administrativa
**não** foi removida.

---

## 4. Não simular ICP-Brasil

O sistema não verifica cadeia ICP-Brasil, e agora nenhum texto sugere que
verifica. O `prompt` da conferência já era honesto e continua:

> A SoproLife **NÃO** valida a cadeia ICP-Brasil — o que será registrado é a
> sua conferência externa, com seu usuário e a data/hora.

`qualified_signature` permanece **falso**, com teste travando isso. O
comportamento legado da M25.8 não foi ressuscitado.

---

## 5. O fluxo, agora explícito

```
MÉDICA                          ADMINISTRAÇÃO SOPROLIFE
──────                          ───────────────────────
lauda
conclui  (1 confirmação)
baixa PDF final
assina externamente
devolve PDF assinado
confirma envio
✓ "Seu trabalho terminou"   →   Assinado recebido
                                Conferência administrativa pendente
                                [Registrar conferência do PDF assinado]
                                Pronto para entrega
                                [Baixar laudo assinado] [Baixar exame técnico]
                                [Marcar como entregue]
```

MIR e laudo assinado seguem **separados**: um botão cada, um arquivo cada,
nomes distintos — com teste que compara os bytes dos dois.

---

## 6. Testes — 23 novos

| # | prova |
| --- | --- |
| 1 | `recebido_validacao_pendente` é estado administrativo e aparece na fila |
| 2 | a médica lê "Seu trabalho terminou" |
| 3 | o rótulo diz de quem é a pendência |
| 4-5 | nomenclatura de conferência, não de validação |
| 6 | `qualified_signature` continua falso |
| 7-10 | os dois downloads: `200`, `application/pdf`, `%PDF`, `filename` `.pdf` |
| 11 | nunca `.jsold` |
| 12 | nenhuma âncora crua sobrou; `apiHref` morto removido |
| 13 | erro `404` é JSON sem `Content-Disposition` — não vira arquivo |
| 14 | MIR e assinado são arquivos distintos |
| 15-16 | médica não baixa pela rota administrativa, não registra conferência nem entrega (`403`) |
| 17 | admin continua acessando |
| 18 | nada marcado como entregue sozinho |
| 19-21 | worker não intercepta, se desinstala, sem placeholder eterno |
| 22 | alvo de toque de 48px e empilhamento no celular |
| 23 | trava de reentrância nos três downloads |
| + | o script de auditoria roda, não escreve e não vaza nome |
| + | nenhum dado real nos testes |

Dois contratos anteriores mudaram porque o contrato de UX mudou:

* **M25.20** — passou a exigir os dois botões e a chamada, em vez da URL
  literal que deixou de existir. Foram **quatro** asserções no lugar de duas:
  o contrato ficou mais forte, não mais fraco.
* **M25.21** — acompanha o novo rótulo do mesmo estado. Só a string mudou.

Nenhuma asserção foi removida ou enfraquecida.

---

## 7. Auditoria read-only da fila real

`scripts/auditar_fila_assinados.py` lista os documentos em
`recebido_validacao_pendente` com laudo, exame, estado, existência do arquivo,
tamanho em disco, hash abreviado, versão ligada e se **o backend consegue
reler o PDF** pelo mesmo caminho do download.

Ele não escreve nada, não marca validado, não marca entregue, não regrava PDF
e não imprime nome de paciente. Se achar arquivo ausente ou corrompido, ele
**para** e manda levar o diagnóstico a uma decisão humana.

```bash
cd /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15
/opt/soprolife/venvs/m15/bin/python scripts/auditar_fila_assinados.py
```

> Requer credencial de banco (`root`). **Ainda não executado em produção** —
> ver limitações.

---

## 8. Lotes repetidos

A auditoria da M25.29D mostrou muitos lotes de download em poucos minutos —
`BAT-000013` e `BAT-000014` com 0,8s de diferença, ambos com 4 documentos.
Nenhuma pessoa re-seleciona quatro laudos nesse tempo.

Causa: em caso de erro o `finally` reabilitava o botão **com a seleção
preservada** (ela só é limpa no sucesso), então cada tentativa frustrada
abria um lote novo. É registro legítimo de tentativas reais — e por isso
**nada foi apagado**. O que foi corrigido é a reentrância: um segundo clique
não entra enquanto o primeiro está no ar.

---

## 9. RBAC

| quem | pode |
| --- | --- |
| médica | só os próprios laudos; **`403`** nos dois downloads administrativos, na conferência e na entrega |
| operação | fila, conferência, downloads, entrega |

Gate M25.23 intacto: `401` sem sessão em `/laudos`, `/laudos/entrega`,
`/admin/usuarios`, `/pessoas` e até no estático do painel.

---

## 10. Suíte

```
1386 passed, 13 failed, 30 skipped   (suíte inteira, 25min40s)
1430 testes coletados  (1406 na M25.29D + 24 novos aqui)
```

As 13 falhas são pré-existentes e idênticas às do HEAD de partida: 12 do
`test_live_multisheet_reader` (`googleapiclient` fora do `requirements.lock`)
e o falso positivo da M25.17. **Zero regressão.**

Na conferência deste número descobriu-se que a contagem registrada no
relatório da M25.29D (1320 verdes) estava subnotificada — corrigida lá, com
nota.

---

## 11. Deploy — 18/08/2026

```
local        30493f7  →  push da branch M25.29E
integração   f5d3052..30493f7  ff-only em painel-soprolife-v01 (sem force)
VPS          f5d3052  →  30493f7  (git merge --ff-only, árvore limpa)
```

Nenhuma migration: Alembic head permanece `a2f6c81d4b73`.

### Pré-voo que a M25.29D ensinou

Antes do merge, conferi o dono de **todos** os diretórios que o commit
alcança — o deploy anterior parou no meio porque `nucleo-m15/tests/visual/`
pertence ao `root`. Os seis diretórios desta etapa são `soprolife:soprolife`,
e o fast-forward passou inteiro de primeira.

### Verificação pós-deploy

| prova | resultado |
| --- | --- |
| HEAD da VPS | `30493f76adf9d5f5ec623973aa104bb4fb16d2f1`, árvore limpa |
| Health | `HTTP 200`, `status: ok`, `banco: ok`, `ambiente: prod` |
| Alembic | head `a2f6c81d4b73`, nada a aplicar |
| Timer / snapshots | `Result=success`, `ExecMainStatus=0`, ciclo de 10min ativo |
| Gate M25.23 | `401` em `/laudos`, `/laudos/entrega`, `/admin/usuarios`, `/pessoas` |
| Cache busting | `report-workflow.js?v=2026081801` no ar |
| Downloads por botão | 3 ocorrências; **zero** âncoras cruas restantes |
| "Seu trabalho terminou" | presente |
| "Registrar conferência do PDF assinado" | presente |
| Service worker | sem handler de `fetch`; `registration.unregister()` presente |

### Pendente: restart da API

O `systemctl restart soprolife-m15-api` exige `root` e **ainda não foi
executado**. Nesta etapa isso é benigno: a única mudança de backend é o
rótulo da fila (`FILA_ROTULOS`). Travas, RBAC e downloads não mudaram no
servidor, e todo o resto da correção é frontend, que já está no ar.

---

## 12. Limitações declaradas

* A verificação de celular é **contrato de CSS** (bloco `@media` extraído por
  contagem de chaves), não medição em navegador como na M25.29D. A fila
  administrativa não está no *harness* visual.
* O `sw.js` desativado remove o modo offline do site público. Como **nada o
  registrava**, não havia modo offline em uso — mas a decisão está aqui,
  explícita, e não escondida num commit.
* A auditoria da fila real em produção depende de credencial `root` e ainda
  não foi executada.
* Continua **sem** validação criptográfica ICP-Brasil.

Nenhuma PII e nenhum segredo constam deste relatório.
