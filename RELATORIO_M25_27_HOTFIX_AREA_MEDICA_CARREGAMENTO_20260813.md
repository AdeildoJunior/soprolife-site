# M25.27 — Hotfix: área médica presa em "Carregando o fluxo seguro de laudos…"

**Data:** 2026-08-13
**Branch:** `claude-m25-27-hotfix-area-medica-carregamento`
**Base:** `4aca9fd5e3ee6f4547b1ba142be37e78821b625a` (HEAD oficial esperado — confirmado)

> **Estado desta etapa: correção provada localmente, NÃO implantada.**
> O acesso SSH à VPS caiu na reautenticação do Tailscale durante a Fase 1 e não
> foi restabelecido. Tudo que depende da VPS ou do banco de produção — Fases 1
> (itens 1–4, 10), 5 e os itens 7/14/15 da Fase 6 — está **pendente**, listado
> na seção 14. Nada foi implantado, nada foi commitado em produção.

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

**PENDENTE — não verificável sem a VPS.** Nada foi laudado, concluído,
assinado ou apagado. Nenhum paciente, exame ou laudo novo foi criado.

A verificação exigida (physician_id, estado, encerramento histórico, disposição
operacional, versão, origem, permissões) depende de leitura do banco de
produção e está listada na seção 14.

O que **é** possível afirmar sem a VPS: a correção não toca conteúdo clínico,
atribuição nem permissão de laudo. Ela altera apenas quem pode ler o manifesto
de boot. Se LAU-000008/009 estiverem atribuídos à Dra. Ana, aparecerão na fila
assim que a bancada montar; se não estiverem, continuarão não aparecendo — e
isso deverá ser tratado explicitamente na administração, sem correção
silenciosa.

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
Não são desta etapa e não foram agravadas por ela. (Ver pendência 14.4.)

Contra os itens exigidos na Fase 6:

| # | Item | Situação |
|---|---|---|
| 1 | sem login → somente login | **provado** (401 no JS do painel; casca de login na porta de entrada) |
| 2 | médica → somente Laudos de espirometria | **provado** (dados operacionais seguem 403; superfície administrativa não é montada) |
| 3 | admin → Command Center completo | **provado** (200 em todo o conjunto administrativo) |
| 4 | Sair aparece para ambos | **provado por contrato de fonte**; falta confirmação visual em produção |
| 5 | Sair funciona | inalterado — mesmo `POST /auth/logout`; falta smoke |
| 6 | área médica não trava | **provado** (causa raiz removida + boot resiliente) |
| 7 | LAU-000008/009 visíveis | **pendente** (VPS) |
| 8 | bancada MIR/laudo monta | **pendente de smoke** (caminho desbloqueado e provado em HTTP) |
| 9 | tooltips M25.24 | **provado** (`bindHelpTips` preservado; `test-m24a` PASS) |
| 10 | assinatura externa | **provado** (`test-m24a` PASS; código não tocado) |
| 11 | Pastore sem receita individual | **provado** (`test-m22-pastore.js` PASS) |
| 12 | SoproLife R$220 editável | **provado** (`test-m25-26-fluxo-espirometria.js` PASS) |
| 13 | máscaras de data | **provado** (`test-m15-ui-calendar.js`, `test-m23-1-pastore-datepicker.js` PASS) |
| 14 | financeiro histórico 13 LAN / R$ 3.044,79 | **pendente** (VPS) |
| 15 | Pastore jul R$219 / ago R$328,50 a receber, zero recebido | **pendente** (VPS) |

## 10. Financeiro

**Não tocado.** Nenhuma alteração em `Financeiro_Lancamentos`, em regra de
preço, em conciliação ou em qualquer serializador financeiro. O diff não inclui
um único arquivo de finanças. A verificação numérica dos itens 14 e 15 continua
pendente da VPS (seção 14).

## 11. Deploy

**Não realizado.** Requisito da missão: deploy só após correção comprovada, com
health e smoke real — e ambos exigem a VPS, que está inacessível.

Estado atual: alterações **commitadas na branch de trabalho**, sem push, sem
merge em `painel-soprolife-v01`, sem tocar a VPS. Nenhum `reset --hard`, nenhum
force push, nenhum DELETE em produção.

## 12. Smoke

**Pendente.** Nenhum smoke de produção foi executado.

O que substituiu o smoke localmente: o `test-m25-27-area-medica.py` sobe o
**mesmo** `command-center-local-server.py` que roda na VPS
(`soprolife-painel-loopback.service`) e mede as respostas HTTP por papel com
identidade sintética. É a prova mais próxima do comportamento real que se pode
obter sem a VPS.

## 13. HEAD final

- Worktree local: `4aca9fd` + 1 commit desta etapa (ver seção 11).
- Base confirmada igual ao HEAD oficial esperado
  (`4aca9fd5e3ee6f4547b1ba142be37e78821b625a`).
- **HEAD da VPS: não confirmado** — SSH indisponível.

## 14. Processos e pendências

**Processos temporários órfãos (local):** `pgrep -af 'createdb|pytest|m2527'`
não retorna nenhum processo — apenas o próprio wrapper do comando. Nenhum banco
temporário foi criado; nenhuma suíte pesada rodou. **Na VPS: não verificado**
(SSH indisponível). Nada foi executado lá nesta etapa, então não há origem para
órfãos desta missão.

### Pendências

**14.1 — Acesso à VPS (bloqueador de tudo abaixo).**
O SSH falhou com:

```
# Tailscale SSH requires an additional check.
# To authenticate, visit: https://login.tailscale.com/a/l1c346b67377eee
```

É uma reautenticação interativa que só o operador pode concluir.

**14.2 — Verificações que dependem da VPS:** HEAD, health, Alembic, serviços,
logs da API no horário do teste; situação de LAU-000008/009; financeiro
histórico (13 LAN / R$ 3.044,79); Pastore (julho R$219, agosto R$328,50 a
receber, zero recebido); smoke real da bancada médica; deploy.

**14.3 — Selos de cache defasados** em seis assets (seção 5). Risco latente,
neutralizado hoje pelo `no-store`. Merece uma etapa própria com estratégia
determinística por commit — não foi feito aqui porque a condição da Fase 3 não
se verificou.

**14.4 — Duas suítes já falhavam antes desta etapa:**
`test-m25-12-resgate-laudos.js` (8 falhas) e `test-m25-14-destravar-ui.js`
(2 falhas). Não investigadas — fora do escopo deste hotfix, mas são dívida real
sobre a mesma área médica e deveriam ser endereçadas.

**14.5 — `sw.js` órfão na raiz** com `CACHE_NAME = 'sl-$V'` (placeholder nunca
substituído) e sem nenhum registro no repositório. Inerte hoje. Deveria ser
removido ou consertado para não virar armadilha futura.

---

## Conclusão

A conclusão pedida pela missão —
**"M25.27 — ÁREA MÉDICA CARREGA DE FORMA CONFIÁVEL SEM DEPENDER DE LIMPEZA
MANUAL DE CACHE"** — **não pode ser declarada nesta etapa**, porque a missão a
condiciona a prova, e a prova em produção depende da VPS.

O que está provado:

1. a causa raiz foi **identificada, reproduzida e corrigida**, e não era cache;
2. a hipótese de cache foi **descartada com medição**, não descartada por
   suposição;
3. o placeholder eterno foi eliminado por construção, não só por remoção da
   causa;
4. o gate M25.23 permanece íntegro, com teste que falha se a isenção do
   manifesto vazar para o diretório.

O que falta para declarar a conclusão: reautenticar o Tailscale, implantar e
rodar o smoke real com a conta da médica.
