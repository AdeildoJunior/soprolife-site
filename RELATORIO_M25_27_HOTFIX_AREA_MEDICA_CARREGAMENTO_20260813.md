# M25.27 — Hotfix: área médica presa em "Carregando o fluxo seguro de laudos…"

**Data:** 2026-08-13
**Branch:** `claude-m25-27-hotfix-area-medica-carregamento`
**Base:** `4aca9fd5e3ee6f4547b1ba142be37e78821b625a` (HEAD oficial esperado — confirmado)

> **Estado desta etapa: corrigida, implantada e verificada em produção.**
> O SSH foi reautenticado pelo operador e todas as fases foram concluídas.
> VPS em `ea0cf75`, health `ok`, smoke aprovado, sem processos órfãos.

---

## 1. Sintoma

Após a M25.26, com dois lançamentos controlados em produção
(ESP-000023/LAU-000008 — Espirometria SoproLife; ESP-000024/LAU-000009 —
Espirometria Pastore), a sessão administrativa enxerga ambos normalmente em
"Laudos de espirometria / Acompanhamento operacional".

Ao entrar com a conta da médica, a página fica indefinidamente em
**"Carregando o fluxo seguro de laudos…"** e a bancada nunca monta.

No screenshot da sessão médica travada aparecem **Atualizar** e o avatar **SL**,
mas **não** aparece o botão **Sair** que a M25.26 adicionou entre os dois.

## 2. Causa raiz

**Um único defeito produz os dois sintomas.**

A M25.23 (`d4deb4a`) passou a exigir papel administrativo para tudo sob
`painel-soprolife/data/`:

```python
if kind == _gate.PROTECTED_DATA and not _is_administrative(identidade):
    self._deny(403, "Permissão insuficiente para este dado.", method)
```

`painel-soprolife/data/m15-config.json` caiu nessa regra por vizinhança de
diretório. Mas ele **não é dado operacional**: é o *manifesto de boot* do painel
(`enabled`, `reports_enabled`, `api_base`) — o arquivo que diz às telas que elas
podem se montar. `_PAPEIS_ADMINISTRATIVOS` é
`{admin, gestor, operacional, leitura}`; `medico` não está lá, por desenho.

Resultado para uma sessão exclusivamente clínica:

| Consumidor do manifesto | Comportamento com 403 | Sintoma visível |
|---|---|---|
| `report-workflow.js` → `boot()` | `config = {}` → `config.enabled !== true` → `return` mudo | bancada nunca chama `render()`; o placeholder do HTML permanece **para sempre** |
| `m15-nucleo.js` → `boot()` | corpo do 403 é JSON válido → `enabled` indefinido → `return` mudo | `activate()` nunca roda → `wireSairDoTopo()` nunca roda → **"Sair" fica `hidden`** |

O botão nasce `<button id="topbarSair" hidden>` e só é revelado pelo núcleo.
Por isso a **ausência do "Sair" no screenshot não era sintoma de cache: era a
confirmação positiva da causa raiz** — a prova de que o núcleo não havia
inicializado naquela sessão.

O defeito existe desde a M25.23. Não foi introduzido pela M25.26 — a M25.26 foi
apenas quando a conta médica voltou a ser exercitada em produção.

## 3. Evidência de cache — hipótese DESCARTADA com prova

A pista de cache foi investigada primeiro, como pedido, e **descartada por
medição**, não por opinião.

**3.1 — A M25.26 bumpou o selo de todos os arquivos que alterou.**
Arquivos tocados: `central.css`, `style.css`, `index.html`, `central-cadastros.js`,
`m15-datepicker.js`, `m15-nucleo.js`. Selos bumpados no mesmo commit para
`style.css`, `m15-datepicker.js`, `m15-nucleo.js`, `central-cadastros.js`
(`2026072402/2026072501` → `2026081201`). `central.css` é carregado dinamicamente
já com selo. Nenhum HTML novo apontando para JS antigo na mesma URL.

**3.2 — O painel é servido `no-store` desde a M25.23.** Medido no servidor real:

```
/painel-soprolife/                200  Cache-Control: no-store
/painel-soprolife/js/m15-nucleo.js      200  Cache-Control: no-store
/painel-soprolife/js/app.js             200  Cache-Control: no-store
```

O HTML e todo o JS do painel são incacheáveis por contrato. **Um navegador não
consegue servir HTML velho nem JS velho do painel**, com ou sem selo. A hipótese
de "HTML atual + JS antigo em cache" é impossível na arquitetura atual.

**3.3 — Service worker: não há.** `sw.js` existe na raiz mas o registro foi
removido em `7fc2ae9`; não há `serviceWorker.register` em lugar nenhum do
repositório. (Achado colateral: o arquivo órfão tem `CACHE_NAME = 'sl-$V'`, com
o placeholder `$V` nunca substituído — ver seção 14.)

**3.4 — Os assets chegam idênticos aos dois papéis.** Medição direta, servidor
real, identidades sintéticas:

```
ALVO                                       MÉDICA    ADMIN
/painel-soprolife/                            200      200
/painel-soprolife/js/m15-nucleo.js            200      200
/painel-soprolife/js/report-workflow.js       200      200
/painel-soprolife/data/m15-config.json        403      200   <<< DIVERGE
```

**Exatamente uma divergência entre os dois papéis, e não é asset.**

## 4. Erro JS/API real

Não há exceção JavaScript. Há uma **resposta HTTP 403 tratada como silêncio**.

- API: `GET /painel-soprolife/data/m15-config.json` → **403** para sessão
  `papeis_efetivos = ["medico"]`.
- O `/api/m15/auth/me` responde 200 normalmente — por isso o `boot-gate.js`
  funciona e poda o DOM corretamente para o papel clínico. O painel *sabia* quem
  era a médica; o que faltava era deixá-la ler o manifesto de boot.
- Nenhum 401/404/422/500 envolvido. Não é problema exclusivamente de cliente:
  a origem é a decisão de autorização no servidor estático.

## 5. Mecanismo de cache — antes e depois

**Não alterado.** A Fase 3 era condicional ("se a causa envolver assets antigos")
e a condição **não se verificou**. Um redesenho do cache busting seria mudança
grande sem defeito correspondente, e foi deliberadamente não feito.

A auditoria pedida foi executada mesmo assim, em todos os assets críticos do
Command Center — não só a área médica. Método: comparar o commit que fixou o
selo de cada asset com o último commit que alterou o arquivo.

Seis assets têm selo **defasado** em relação ao conteúdo:

| Asset | Selo | Commit do selo | Último commit do arquivo |
|---|---|---|---|
| `js/operational-actions.js` | 2026070701 | `e7494e4` | `e38e127` |
| `js/b2b-actions.js` | 2026070701 | `5d4b696` | `742555a` |
| `js/espirometria-financeiro.js` | 2026070902 | `0b29353` | `7bf27b0` |
| `js/app.js` | 2026080101 | `a7e0757` | `d4deb4a` |
| `js/central-cadastros.js` | 2026081201 | `de08407` | `7fa30a7` |
| `js/crm-workspace.js` | 2026072501 | `7c32222` | `8651d2f` |

**Impacto real hoje: nenhum**, porque o `no-store` da M25.23 torna o selo
irrelevante para JS/HTML. É risco latente, não defeito ativo: se algum dia o
painel voltar a ser cacheável, esses seis voltam a poder servir código velho.
O caso mais delicado é `js/app.js`, alterado pela própria M25.23 (que lhe
adicionou o gate de boot) sem bump de selo. Registrado como pendência, não
corrigido aqui — está fora do escopo do hotfix.

**O que mudou nos selos nesta etapa:** apenas os arquivos que esta etapa
alterou, mais o par exigido por contrato existente.

- `js/m15-nucleo.js`: `2026081201` → `2026081301`
- `js/report-workflow.js`: `2026081102` → `2026081301`
- `css/report-workflow.css`: `2026081102` → `2026081301`

O CSS entrou porque `test-m24a-report-workflow.js` exige que o par
`report-workflow.css`/`.js` carregue sempre o **mesmo** selo. Bumpar só o JS
quebrou esse contrato e a falha foi capturada pela suíte antes da entrega — o
CSS é, aliás, o único dos três que o gate deixa realmente cacheável.

## 6. Boot resiliente

Independente da causa raiz, o placeholder não pode mais ser eterno. O
`report-workflow.js` foi endurecido:

- **Toda saída de `boot()` pinta um estado.** Antes havia dois `return` mudos.
- **Falha de leitura ≠ feature desligada.** O antigo `catch { config = {} }`
  apagava a diferença: um 403 era indistinguível de "piloto desabilitado".
  - falha (HTTP ≠ 200, JSON ilegível, rede): *"Não foi possível carregar os
    laudos."* + botão **Tentar novamente**;
  - desligada: *"O fluxo de laudos não está habilitado nesta instalação."*
    (sem botão — insistir não muda configuração).
- **A espera pelo núcleo é limitada.** `bindClient()` tinha
  `setTimeout(bindClient, 200)` **sem teto**: se `window.SoproM15` nunca
  aparecesse, a tela carregava para sempre, sem erro e sem console. Agora são
  `CLIENT_MAX_WAITS = 30` ciclos (~6 s, a janela legítima de um script `defer`)
  e depois estado de falha com ação. Não é polling de dado e não foi
  introduzido polling novo.
- **Sem PII e sem valor financeiro na tela de erro.** O motivo técnico vai só
  para `console.error`; o DOM recebe apenas a frase acordada.
- **Reentrada é segura.** O "Tentar novamente" chama `boot()` de novo; os
  ouvintes clínicos (`click`/`change`/`input`/`submit`) são ligados uma única
  vez via `listenersWired`. Sem isso, cada clique da médica passaria a valer
  dois — dois downloads, duas liberações.

## 7. Isolamento de papel preservado (efeito colateral que precisou ser contido)

Corrigir o 403 faz o núcleo M15 passar a rodar **também** para a médica. Isso
reabriria o buraco que a M25.23 fechou: `activate()` injetava
`<section id="m15-nucleo">` e um item de menu **"Núcleo administrativo"**
*depois* que o `boot-gate.js` já havia podado o DOM.

Contido em `m15-nucleo.js`:

- a superfície administrativa (menu + seção) saiu para
  `montarSuperficieAdministrativa()`, chamada **só** quando
  `SoproBootGate.somenteClinico` é falso;
- `boot()` agora espera `SoproBootGate.pronto` antes de decidir — sem isso, a
  corrida entre os dois `fetch` decidiria por sorteio se a médica veria o menu
  administrativo. Falha do gate não abre nada: o gate já é fail-closed;
- o **"Sair"** continua sendo revelado para toda sessão autenticada — ele é de
  quem está autenticado, não de quem é administrador.

## 8. Situação de LAU-000008 e LAU-000009

Leitura direta do banco de produção, sem alterar nada. Nenhum paciente, exame ou
laudo foi criado; nada foi laudado, concluído, assinado ou apagado.

| | LAU-000008 | LAU-000009 |
|---|---|---|
| exame | ESP-000023 | ESP-000024 |
| `status` | `atribuido` | `atribuido` |
| `origin_type` | `coworking` (SoproLife) | `clinica_parceira` (Pastore) |
| versão corrente | existe | existe |
| atribuição ativa | sim | sim |
| `reason_code` | `initial_assignment` | `initial_assignment` |
| `ended_at` (encerramento) | nulo | nulo |
| `finalized_at` / `released_at` / `signed_at` | nulos | nulos |
| `physician_profile_id` | `59709f0c…a3f5` | `59709f0c…a3f5` |

**Ambos estão corretamente atribuídos à Dra. Ana** — mesmo
`physician_profile_id`, atribuição ativa e inicial, sem encerramento histórico.
Não houve nada a corrigir na administração: a atribuição era exatamente a
intenção do lançamento.

Permissões confirmadas na tabela de papéis:

| conta | papéis |
|---|---|
| Dra. Ana | `{medico}` |
| contato@soprolife.com.br | `{admin}` |
| (gestor) | `{gestor}` |

A conta da médica tem **exclusivamente** o papel `medico` — é precisamente o
caso que o 403 quebrava, e a razão de o defeito nunca ter aparecido para as
outras duas contas.

Com a correção implantada, os dois laudos entram na fila "Pendentes de laudo"
assim que a bancada monta, porque a atribuição já estava certa e o que faltava
era apenas a tela conseguir inicializar.

## 9. Regressões

Suítes executadas localmente, nesta worktree:

| Suíte | Resultado |
|---|---|
| `test-panel-access-gate.py` (+ casos M25.27) | **PASS** |
| `test-m25-27-area-medica.py` (novo) | **PASS** |
| `test-m25-27-boot-resiliente.js` (novo) | **PASS** |
| `test-guardas-estaticas.py` | **PASS** |
| `test_command_center_m15_proxy.py` | **PASS** |
| `test-systemd-units.py` | **PASS** |
| `test-m24a-report-workflow.js` | **PASS** |
| `test-m25-26-fluxo-espirometria.js` | **PASS** |
| `test-contratos.js` | **PASS** |
| `test-m21-auth-crm-nav.js` | **PASS** |
| `test-m22-pastore.js` | **PASS** |
| `test-m23-1-financeiro-duplicidade.js` | **PASS** |
| `test-m15-ui-calendar.js` | **PASS** |
| `test-m23-1-pastore-datepicker.js` | **PASS** |
| `test-m25-12-resgate-laudos.js` | FAIL — **pré-existente** |
| `test-m25-14-destravar-ui.js` | FAIL — **pré-existente** |
| `node --check` em `m15-nucleo.js` e `report-workflow.js` | **PASS** |

As duas falhas foram medidas contra o HEAD limpo (`git stash`) e apresentam
**contagem idêntica antes e depois** da mudança — 8 e 2 falhas respectivamente.
Não são desta etapa e não foram agravadas por ela. (Ver pendência 14.2.)

Contra os itens exigidos na Fase 6:

| # | Item | Situação |
|---|---|---|
| 1 | sem login → somente login | **provado** (401 no JS do painel; casca de login na porta de entrada) |
| 2 | médica → somente Laudos de espirometria | **provado** (dados operacionais seguem 403; superfície administrativa não é montada) |
| 3 | admin → Command Center completo | **provado** (200 em todo o conjunto administrativo) |
| 4 | Sair aparece para ambos | **provado por contrato de fonte** + causa da ausência removida; confirmação visual em 14.4 |
| 5 | Sair funciona | inalterado — mesmo `POST /auth/logout`, nenhum segundo contrato criado |
| 6 | área médica não trava | **provado** (causa raiz removida + boot resiliente) |
| 7 | LAU-000008/009 visíveis | **provado** — ambos `atribuido` e ativos para a Dra. Ana (seção 8) |
| 8 | bancada MIR/laudo monta | **provado no servidor** (manifesto 200 + scripts 200 na sessão médica); confirmação visual pendente com a Dra. Ana (14.4) |
| 9 | tooltips M25.24 | **provado** (`bindHelpTips` preservado; `test-m24a` PASS) |
| 10 | assinatura externa | **provado** (`test-m24a` PASS; código não tocado) |
| 11 | Pastore sem receita individual | **provado** (`test-m22-pastore.js` PASS) |
| 12 | SoproLife R$220 editável | **provado** (`test-m25-26-fluxo-espirometria.js` PASS) |
| 13 | máscaras de data | **provado** (`test-m15-ui-calendar.js`, `test-m23-1-pastore-datepicker.js` PASS) |
| 14 | financeiro histórico 13 LAN / R$ 3.044,79 | **provado** — exato até 12/08; o 14º é o teste de hoje (seção 10) |
| 15 | Pastore jul R$219 / ago R$328,50 a receber, zero recebido | **provado** — exato, 0 transferências (seção 10) |

## 10. Financeiro

**Não tocado por esta etapa.** O diff não inclui um único arquivo de finanças.
Verificado no banco de produção:

- **Histórico intacto:** lançamentos criados até 12/08/2026 somam exatamente
  **13 LAN / R$ 3.044,79** — o número exigido pela missão, sem divergência.
- **Total atual: 14 LAN / R$ 3.264,79.** A diferença de **R$ 220,00** é
  `LAN-000015` (receita, Espirometria, competência 2026-08-13, status
  *Cortesia*), vinculada a `ESP-000023` — o lançamento de teste que o operador
  fez hoje. Não é alteração de histórico: é o registro novo do próprio teste.
- **ESP-000024 (Pastore) não gerou lançamento financeiro**, coerente com a
  decisão comercial de a Pastore não ter regra de preço individual.

**Pastore (item 15):**

| competência | valor | status |
|---|---|---|
| julho/2026 | R$ 219,00 | `a_receber` |
| agosto/2026 | R$ 328,50 | `a_receber` |

Transferências registradas: **0** — total recebido **R$ 0,00**. Confere
exatamente com o esperado.

## 11. Deploy

Executado após a reautenticação do Tailscale pelo operador. Sequência exata:

1. commit único na branch de trabalho (`ea0cf75`);
2. push da branch de trabalho;
3. `painel-soprolife-v01` avançada por **ff-only** (`4aca9fd..ea0cf75`);
4. push normal da branch oficial;
5. VPS: `git fetch origin painel-soprolife-v01` + `git merge --ff-only`;
6. restart **apenas** de `soprolife-painel-loopback` — é o único serviço cujo
   código mudou (`panel_access_gate.py` e `command-center-local-server.py`).
   `soprolife-m15-api` **não** foi reiniciada: nenhum arquivo dela foi tocado;
7. health e smoke.

Backup: **não aplicável.** A etapa não altera schema, migração nem dado — não
há estado novo a preservar, e o ff-only é reversível por si.

Nada de `reset --hard`, force push, `--force-with-lease` ou DELETE em produção.

## 12. Smoke

Executado contra o código de produção, após o restart.

**Health:** `{"status":"ok","versao":"0.1.0","ambiente":"prod","banco":"ok"}` —
HTTP 200.

**Sem sessão** (comportamento do gate preservado):

```
/painel-soprolife/data/m15-config.json  -> 401
/painel-soprolife/js/report-workflow.js -> 401
/painel-soprolife/                      -> 200  (casca de login)
```

**Matriz de papéis** (`test-m25-27-area-medica.py` rodado na VPS, sobre o
servidor real, com identidade sintética — sem banco temporário e sem suíte
pesada): **todos os 25 casos PASS**, incluindo:

- médica lê `data/m15-config.json` → **200** (era 403: a causa raiz);
- médica **não** lê `resumo.json`, `leads.json`, `crm-clinicas.json`,
  `marketing.json`, `financeiro-summary.local.json` → **403** (M25.23 intacta);
- `data-private/`, `nucleo-m15/`, `scripts/` e `.git/` → **404** para os dois
  papéis.

**Selos servidos pelo arquivo implantado:** `m15-nucleo.js?v=2026081301`,
`report-workflow.js?v=2026081301`, `report-workflow.css?v=2026081301`.

## 13. HEAD final

| Onde | Commit |
|---|---|
| worktree local | `ea0cf75` |
| `origin/painel-soprolife-v01` | `ea0cf75` |
| VPS `/opt/soprolife/soprolife-site` | `ea0cf75` |

Base `4aca9fd` confirmada igual ao HEAD oficial esperado. Alembic em
`a2f6c81d4b73`, que é o **head** das 22 migrações do repositório — banco em dia,
nenhuma migração pendente e nenhuma aplicada por esta etapa.

## 14. Processos e pendências

**Processos temporários órfãos:** `pgrep -af 'creat[e]db|pytes[t]|m252[7]'` na
VPS não retorna nada. Nenhum banco temporário foi criado, nenhuma suíte pesada
rodou lá — apenas o smoke direcionado, que sobe um servidor em porta efêmera
no próprio processo e encerra ao final. As portas em escuta continuam sendo
apenas as duas esperadas (8015 API, 8765 proxy). O incidente `m2524_verif` não
se repetiu.

### Pendências remanescentes

Nenhuma bloqueia a operação. Todas são dívida técnica registrada, fora do
escopo deste hotfix:

**14.1 — Selos de cache defasados** em seis assets (seção 5). Risco latente,
neutralizado hoje pelo `no-store`. Merece uma etapa própria com estratégia
determinística por commit. O caso mais delicado é `js/app.js`, alterado pela
M25.23 sem bump de selo.

**14.2 — Duas suítes já falhavam antes desta etapa:**
`test-m25-12-resgate-laudos.js` (8 falhas) e `test-m25-14-destravar-ui.js`
(2 falhas). Contagem idêntica antes e depois da mudança — não são desta etapa,
mas são dívida real sobre a mesma área médica.

**14.3 — `sw.js` órfão na raiz** com `CACHE_NAME = 'sl-$V'` (placeholder nunca
substituído) e sem nenhum registro no repositório. Inerte hoje; deveria ser
removido ou consertado para não virar armadilha futura.

**14.4 — Confirmação visual pela Dra. Ana.** Todas as provas desta etapa são de
servidor, banco e contrato de código. O último passo — a médica abrir a tela,
ver a bancada montar e os dois laudos na fila — depende dela e não foi
simulado com a conta real (a senha não foi pedida nem alterada, como manda a
missão).

**14.5 — Alembic fora do serviço cai em SQLite default.** Rodar
`alembic current` na VPS sem o env do serviço usa a URL default do `alembic.ini`
(SQLite) em vez do PostgreSQL, o que produz uma leitura enganosa. A versão real
foi confirmada direto no banco. Vale corrigir o `alembic.ini`/env para não
induzir erro em diagnósticos futuros.

---

## Conclusão

Provado e implantado:

1. a causa raiz foi **identificada, reproduzida e corrigida** — e **não era
   cache**: era um 403 de autorização no manifesto de boot, tratado como
   silêncio por duas telas;
2. a hipótese de cache foi **descartada com medição** (painel servido
   `no-store`, assets idênticos aos dois papéis, service worker inexistente);
3. o placeholder eterno foi eliminado **por construção** — toda saída de
   `boot()` pinta estado, a espera pelo núcleo tem teto e existe "Tentar
   novamente" que não duplica ação clínica;
4. o **gate M25.23 permanece íntegro**, com teste que falha se a isenção do
   manifesto vazar do arquivo para o diretório;
5. **LAU-000008 e LAU-000009 estão corretamente atribuídos à Dra. Ana**,
   intocados, prontos para continuarem sendo usados como teste;
6. **financeiro histórico intacto**: 13 LAN / R$ 3.044,79 até 12/08; Pastore
   julho R$219 e agosto R$328,50 a receber, zero recebido.

**M25.27 — ÁREA MÉDICA CARREGA DE FORMA CONFIÁVEL SEM DEPENDER DE LIMPEZA
MANUAL DE CACHE**

A independência de cache não é promessa: o painel é servido `no-store`, de modo
que nenhuma limpeza manual pode ser exigida do usuário — e a causa real do
travamento foi removida na origem, com estado de erro acionável caso qualquer
inicialização futura falhe.
