# M25.23 — Gate de autenticação antes de qualquer conteúdo do Command Center

**Data:** 2026-08-11
**Branch de trabalho:** `claude-m25-22-verificacao-financeiro-integrado`
**Branch oficial:** `painel-soprolife-v01` — fast-forward `075528b..d4deb4a`
**Commit:** `d4deb4a` — *fix(m25.23): o Command Center inteiro era servido antes de qualquer login*
**VPS:** `root@soprolife-painel-01` — `/opt/soprolife/soprolife-site`, HEAD **`d4deb4a`**, working tree limpo
**Migration:** **nenhuma criada, nenhuma executada**

---

## CONCLUSÃO

# M25.23 — COMMAND CENTER FECHADO ANTES DA AUTENTICAÇÃO E ACESSO MÉDICO ISOLADO

Provado em **produção**, pela URL real, em contexto de navegador limpo. Com uma
ressalva honesta, registrada na seção de pendências: o login **autenticado real**
em produção não foi executado por mim, porque isso exigiria credencial da
operação. Todo o resto foi provado ponta a ponta.

---

## 1. CAUSA RAIZ

Não era um bug de CSS nem de RBAC. Eram **três falhas encaixadas**, e cada uma
sozinha já bastava para o vazamento.

### 1.1 A camada estática não tinha noção de sessão

`painel-soprolife/scripts/command-center-local-server.py` servia o painel com
`http.server.SimpleHTTPRequestHandler`, a partir da **raiz do repositório**
(`main()` faz `os.chdir(repo_root)`). O `do_GET` decidia apenas entre três
casos: rota da API M15, rota de status, e **"todo o resto → `super().do_GET()`"**.

Esse `super().do_GET()` é um servidor de arquivos comum: sem autenticação, sem
allowlist, **com listagem de diretório ligada**. Ele não servia "o painel" — ele
servia **o repositório inteiro**.

### 1.2 O cookie de sessão tornava o gate impossível

`session_cookie_path` era `/painel-soprolife/api/m15`
(`nucleo-m15/app/config.py:53`). O navegador só envia o cookie para caminhos sob
o `Path` declarado, então **nenhuma requisição de página ou de JSON carregava o
cookie**. A camada estática era **estruturalmente incapaz** de saber quem estava
pedindo — mesmo que quisesse verificar.

Esta é a causa raiz mais profunda: sem corrigi-la, nenhum gate no servidor
funcionaria.

### 1.3 O isolamento do papel médico era classe CSS

`js/report-workflow.js:346-359` (`setPhysicianNavigationIsolation`) marcava
`document.body.classList.toggle("report-physician-only", …)` e alternava a classe
`active` das seções. O DOM administrativo **permanecia montado**: bastava o
inspetor, o `Ctrl+F` ou um leitor de tela.

Somava-se a isso o `can()` de `js/m15-nucleo.js:74-79`, que devolvia `true`
quando a identidade não estava resolvida — **fail-open** na UI —, e o `init()` de
`js/app.js`, chamado no topo do arquivo, que disparava a leitura de **todos** os
`data/*.json` imediatamente, para qualquer visitante.

---

## 2. ORIGEM DOS DADOS VISTOS SEM LOGIN

Os números do seu print vieram de **um arquivo estático no disco**, servido a
qualquer um. Medido por HTTPS, sem nenhuma sessão, **antes** da correção:

```
GET /painel-soprolife/data/financeiro-summary.local.json   →  200, 1135 bytes
```

Conteúdo (é o próprio arquivo que se descreve):

| campo | valor |
| --- | --- |
| `totais.receita_recebida` | **3044.79** |
| `ticket_medio_real` | **234.21** |
| `exames_pagos` | 13 |
| `por_categoria` | `[{"categoria": "Espirometria", "valor": 3044.79}]` |
| `source.generator` | `nucleo-m15/app/snapshots.py` |
| `source.containsPersonalData` | `false` |

O caminho completo do dado era: PostgreSQL → `snapshots.py` → arquivo
`data/*.local.json` no disco → **servidor de arquivos sem autenticação** →
`app.js` → tela. O elo quebrado era o penúltimo.

### 2.1 A exposição era muito maior do que o print sugeria

Levantamento completo, sem sessão, **antes** da correção:

| recurso | antes | conteúdo |
| --- | --- | --- |
| `/painel-soprolife/` | **200** (49.643 b) | Command Center inteiro |
| **14** `data/*.json` operacionais | **200** | financeiro, marketing, leads, CRM, custos, auditoria, parcerias, saúde operacional |
| `data-private/` | **200** | **listagem** dos 12 arquivos privados |
| `data-private/followup-pacientes.local.json` | **200** (16.843 b) | **13 registros de paciente com `nome` e `telefone`** |
| `data-private/command-center-config.local.json` | **200** | **`apiToken` vivo (43 caracteres) + `webAppUrl`** |
| `data-private/financeiro-lancamentos.local.json` | 200 | razão financeiro bruto |
| `data-private/custos-investimentos.local.json` | 200 | custos, sócios, rateio |
| `.git/HEAD`, `.git/index`, `.git/logs/HEAD`, `.git/config` | **200** | **repositório clonável, com histórico** |
| `nucleo-m15/app/config.py`, `scripts/*.py` | 200 | código-fonte do backend |
| `nucleo-m15/.env.example` | 200 | modelo de configuração |

**Alcance real:** `tailscale serve` está como *tailnet only* — não estava na
internet aberta, e sim exposto a **qualquer dispositivo do tailnet**. É
exatamente por isso que importava agora: no momento em que a médica entrasse no
tailnet para usar o link, ela passaria a ter acesso a tudo acima.

**A API M15 nunca fez parte do problema.** Antes da correção, sem sessão:
`auth/me`, `auth/sessao`, `lancamentos`, `espirometrias`, `pessoas` e
`pastore/fechamentos` já respondiam **401**. O furo era 100% da camada estática.

---

## 3. ARQUIVOS ALTERADOS

### Novos

| arquivo | papel |
| --- | --- |
| `painel-soprolife/scripts/panel_access_gate.py` | Classificador puro de caminho: `public` / `protected_page` / `protected_data` / `forbidden`. Zero rede, zero DOM. |
| `painel-soprolife/scripts/test-panel-access-gate.py` | 90 casos que fixam o contrato, incluindo os seis caminhos que vazavam. |
| `painel-soprolife/login.html` | Casca mínima de autenticação. CSS embutido — não carrega a folha da área restrita. |
| `painel-soprolife/js/login.js` | Único JS que roda sem sessão. Faz `POST /auth/token` e recarrega. Não busca dado, não guarda papel. |
| `painel-soprolife/js/boot-gate.js` | Resolve o papel em `/auth/me` e **remove** da árvore o que estiver fora dele. |
| `painel-soprolife/scripts/evidencia_m25_23_gate.py` | Harness CDP que prova A/B/C/D/F em navegador real. |

### Modificados

| arquivo | mudança |
| --- | --- |
| `scripts/command-center-local-server.py` | `_static_allowed()` como ponto único por onde todo arquivo passa; `_session_identity()` consulta `/auth/me`; `_is_administrative()`; `_serve_login()`; `list_directory()` desativada; `do_HEAD` no mesmo gate; `Cache-Control: no-store`. |
| `nucleo-m15/app/config.py` | `session_cookie_path`: `/painel-soprolife/api/m15` → **`/painel-soprolife`**. |
| `nucleo-m15/app/security.py` | `set_session_cookie()` apaga o cookie do escopo antigo antes de emitir o novo. |
| `nucleo-m15/.env.example` | `M15_SESSION_COOKIE_PATH` alinhado ao novo default. |
| `nucleo-m15/tests/test_m21_session_auth.py` | `_set_cookies()` lê **todos** os `Set-Cookie` (o httpx concatena com vírgula em `.get()`); novo teste da limpeza do escopo legado. |
| `scripts/test_command_center_m15_proxy.py` | +8 testes do gate; dois testes antigos reescritos para o contrato novo. |
| `painel-soprolife/index.html` | `boot-gate.js` carregado **antes** de `app.js`. |
| `painel-soprolife/js/app.js` | `init()` só roda depois do papel resolvido, e nunca para papel exclusivamente clínico. |

**Nenhuma migration.** Nenhum arquivo de dado tocado. Nenhum schema alterado.

---

## 4. COMPORTAMENTO — ANTES E DEPOIS

Medido na **mesma URL de produção**, sem sessão:

| recurso | antes | depois |
| --- | --- | --- |
| `/painel-soprolife/` | 200 — Command Center (49.643 b) | **200 — tela de login (5.603 b)** |
| `data/financeiro-summary.local.json` | 200 — receita e ticket reais | **401** |
| `data/ultimos-lancamentos-summary.local.json` | 200 | **401** |
| `data/custos-investimentos-summary.local.json` | 200 | **401** |
| `data/marketing-seo.local.json` | 200 | **401** |
| `data/leads-summary.local.json` | 200 | **401** |
| `data/followup-pacientes-summary.local.json` | 200 | **401** |
| `data/auditoria-summary.local.json` | 200 | **401** |
| `data-private/` (listagem) | 200 | **404** |
| `data-private/followup-pacientes.local.json` | 200 — PII de paciente | **404** |
| `data-private/command-center-config.local.json` | 200 — apiToken | **404** |
| `data-private/financeiro-lancamentos.local.json` | 200 | **404** |
| `data-private/custos-investimentos.local.json` | 200 | **404** |
| `.git/config`, `.git/HEAD`, `.git/index` | 200 | **404** |
| `nucleo-m15/app/config.py` | 200 | **404** |
| `scripts/command-center-local-server.py` | 200 | **404** |
| `nucleo-m15/.env.example` | 200 | **404** |

**404 e não 403** para a fonte privada e o `.git`: confirmar a existência já é
informação. O gate responde igual para "não existe" e "não pode".

### Site institucional — sem regressão

`/`, `/index.html`, `/espirometria.html`, `/servicos.html`, `/sitemap.xml`,
`/robots.txt`, `/favicon.ico` → **200** em produção, depois do deploy.

---

## 5. RBAC MÉDICO

### Menu e DOM — remoção, não CSS

`boot-gate.js` chama `Element.remove()`. O que não está na árvore não vaza por
CSS, por inspetor, por `Ctrl+F` nem por leitor de tela.

Provado em navegador real com sessão médica sintética:

| verificação | resultado |
| --- | --- |
| `#laudos-espirometria` existe | ✅ |
| `#financeiro` removida do DOM | ✅ |
| `#crm` removida | ✅ |
| `#marketing` removida | ✅ |
| `#overview` (Painel Geral) removida | ✅ |
| `#central-cadastros` removida | ✅ |
| `#parcerias-pastore` removida | ✅ |
| `#custos-investimentos` removida | ✅ |
| `#documentos` removida | ✅ |
| `#leads` removida | ✅ |
| itens de menu `financeiro`/`crm`/`marketing`/`overview` removidos | ✅ |
| nenhum rótulo financeiro no texto visível | ✅ |

A captura da sessão médica mostra a barra lateral com **um único item** —
"Laudos de espirometria" — sob o rótulo "Operação". Os rótulos de grupo que
ficariam vazios também são removidos: o **nome** da área já é informação.

### Backend — 403 por endpoint

O modelo de papéis já estava certo: `ROLE_MEDICO: {ROLE_MEDICO}`
(`security.py:69`) — médico **não implica** nenhum papel administrativo. O que
faltava era prova. Medido com sessões sintéticas:

| endpoint | sem sessão | **médica** | gestor |
| --- | --- | --- | --- |
| `GET /lancamentos` | 401 | **403** | 200 |
| `GET /financeiro/conciliacao/extra-pastore` | 401 | **403** | 200 |
| `GET /crm/kpis` | 401 | **403** | 200 |
| `GET /crm/indicadores` | 401 | **403** | 200 |
| `GET /crm/contatos-a-realizar` | 401 | **403** | 200 |
| `GET /parceiros` | 401 | **403** | 200 |
| `GET /pastore/fechamentos` | 401 | **403** | 409¹ |
| `GET /pastore/configuracao-atendimento` | 401 | **403** | 409¹ |
| `GET /pessoas` | 401 | **403** | 200 |
| `GET /espirometrias` | 401 | **403** | 200 |
| `GET /leads` | 401 | **403** | 200 |
| `GET /followups` | 401 | **403** | 200 |
| `GET /admin/usuarios` | 401 | **403** | 403² |
| `POST /marketing/refresh` | 401 | **403** | — |
| `GET /auth/me` | 401 | 200 | 200 |

¹ 409 `pastore_canonica_ambigua`: o seed sintético não tem parceiro Pastore. A
autorização passou — é erro de domínio, não de permissão.
² Correto: `/admin/*` é exclusivo de `admin`; gestor não alcança.

### Camada estática também respeita o papel

Sessão **não** é suficiente para ler dado operacional — o papel decide:

| recurso | sem sessão | **médica** | gestor |
| --- | --- | --- | --- |
| `/painel-soprolife/` | 200 (login) | 200 (painel) | 200 (painel) |
| `/painel-soprolife/js/app.js` | 401 | 200 | 200 |
| `/painel-soprolife/data/resumo.json` | 401 | **403** | 200 |
| `/painel-soprolife/data-private/…` | 404 | 404 | 404 |
| `/.git/config` | 404 | 404 | 404 |

Ou seja: **a médica não lê o financeiro nem digitando a URL do arquivo.**

---

## 6. REDE ANTES DO LOGIN

Capturado por CDP (`Network.requestWillBeSent`) em janela limpa, contra
**produção**. Foram **5** requisições, e nenhuma é de dado:

```
1. https://…/painel-soprolife/                      (a própria página de login)
2. https://…/painel-soprolife/assets/soprolife-logo.png
3. https://…/painel-soprolife/js/m15-security.js    (guarda de contexto seguro)
4. https://…/painel-soprolife/js/login.js
5. https://…/painel-soprolife/assets/soprolife-logo.png  (favicon)
```

Nenhuma chamada a financeiro, CRM, marketing, pacientes, parcerias ou laudos
clínicos. Isso não depende de disciplina do JavaScript: sem sessão, **a página
que monta esses módulos nem é enviada**.

---

## 7. TESTES

### Automatizados

| suíte | resultado |
| --- | --- |
| `test-panel-access-gate.py` (novo, 90 casos) | ✅ íntegro |
| `test_command_center_m15_proxy.py` (54 casos, +8 novos) | ✅ OK |
| `nucleo-m15/tests/test_m21_session_auth.py` (43 casos) | ✅ 43 passed |
| `test-guardas-estaticas.py` | ✅ todas as guardas |
| `test-systemd-units.py` | ✅ todos os casos |

Os testes do gate cobrem travessia (`..`), percent-encoding (`%2e%2e`, `%2Egit`),
barra invertida, NUL, variação de caixa (`DATA-PRIVATE`), e o princípio
fail-closed: **um arquivo novo largado sob `painel-soprolife/` nasce protegido.**

### DOM sem login (teste B do pedido)

Nenhum destes textos existe no HTML entregue: `Financeiro`, `Marketing`, `CRM`,
`Receita recebida`, `Ticket médio`, `Central de Cadastros`, `Parcerias`,
`Custos`, `Painel Geral`, `Leads`, `Laudos`, `Command Center`, `Documentos`,
`Tarefas`. Nenhum `R$` visível.

Conforme pedido, a verificação **não** se apoia num valor específico: além dos
textos, checa a **ausência estrutural** de `.sidebar`, `.nav-item[data-section]`,
`section.section`, `canvas`, `#financeStats`, `#crmView`, `#mktKpiStrip`,
`#navHub` e `.app-shell`.

> Nota de processo: a primeira versão do `login.html` **falhou** este teste. O
> comentário explicativo que eu havia escrito no topo do arquivo citava os nomes
> dos módulos. Citar a área restrita, ainda que só em comentário, já é informação
> entregue antes do login — o comentário foi reescrito e o teste passou a proibir
> o vocabulário no HTML.

### Visual — desktop e mobile

| captura | contexto | resultado |
| --- | --- | --- |
| `01-sem-login-1920.png` | 1920×1080, janela limpa | só o cartão de login |
| `02-sem-login-430.png` | 430×932, mobile | cartão responsivo, sem barra lateral |
| `03-medica-1920.png` | médica **sintética** | um item de menu: "Laudos de espirometria" |
| `04-gestor-1920.png` | gestor **sintético** | painel completo |

**Nenhum screenshot entrou no Git.** Ficaram no diretório de trabalho da sessão,
fora do repositório. Confirmado por `git status`: os únicos arquivos novos
versionados são código, testes e este relatório.

Os papéis autenticados usaram **banco SQLite sintético** com `seed-demo` e dois
usuários fictícios (`gestor.sintetico@exemplo.test`, `medica.sintetica@exemplo.test`).
Nenhum dado real foi usado nas capturas autenticadas.

### Gestor — sem regressão

Seções `#overview`, `#financeiro`, `#crm`, `#marketing`, `#central-cadastros` e
a barra lateral **presentes** com sessão de gestor. O painel completo continua
funcionando.

---

## 8. DEPLOY

| passo | resultado |
| --- | --- |
| commit | `d4deb4a` |
| push da branch | `claude-m25-23-gate-autenticacao` → origin |
| integração | `painel-soprolife-v01` **fast-forward** `075528b..d4deb4a`, sem `--force` |
| verificação de FF | `git merge-base --is-ancestor origin/painel-soprolife-v01 HEAD` **antes** do push |
| backup | `/opt/soprolife/backups/m25-23/HEAD-antes-20260811.txt` (`075528b`) e `m15.env.bak-20260811` (modo 600) |
| deploy | `git merge --ff-only` na VPS |
| **HEAD da VPS** | `075528b` → **`d4deb4a`** |
| working tree da VPS | limpo |
| migration | **nenhuma** |
| banco | **não tocado** |

### Restart — só o necessário

| serviço | por quê |
| --- | --- |
| `soprolife-m15-api` | `config.py` e `security.py` mudaram (escopo do cookie) |
| `soprolife-painel-loopback` | o gate vive no servidor do painel |
| `soprolife-painel` | mesma coisa, no listener da tailnet |

### Health

| verificação | resultado |
| --- | --- |
| `systemctl is-active` (3 serviços) | **active**, **active**, **active** |
| `GET http://127.0.0.1:8015/api/v1/auth/me` | **401** (API viva e fechada) |
| `GET https://…/painel-soprolife/` | **200**, tela de login |
| `POST https://…/auth/token` com credencial inválida | **401** (fluxo de login vivo) |
| site institucional (7 caminhos) | **200** |

### Reteste em janela privativa nova, contra produção

Executado **depois** do deploy, em perfil de navegador descartável:
**36 provas, todas PASS.** Só a tela de login. Nenhuma informação administrativa.

O gate vale nos **dois** listeners — HTTPS pela tailnet e HTTP direto em
`100.87.98.100:8765`:

| recurso | HTTPS | HTTP direto |
| --- | --- | --- |
| `/painel-soprolife/` | 200 (login) | 200 (login) |
| `data/financeiro-summary.local.json` | 401 | **401** |
| `data-private/followup-pacientes.local.json` | 404 | **404** |
| `.git/config` | 404 | **404** |

---

## 9. PENDÊNCIAS

### 9.1 Rotacionar o `apiToken` — ação sua, e urgente

`data-private/command-center-config.local.json` continha um `apiToken` de 43
caracteres que **esteve publicamente legível no tailnet**. O arquivo agora está
inacessível por HTTP, mas **o segredo já esteve exposto** e deve ser considerado
comprometido. Fechar a porta não desfaz o que passou por ela.

### 9.2 Login autenticado real em produção — falta o seu teste humano

Provei o fluxo até onde é possível sem credencial da operação: a tela de login é
servida, o endpoint responde, e credencial inválida devolve 401. **Não** entrei
com uma conta real — isso é seu. Ao fazer:

- se você já estava logado antes do deploy, **vai precisar entrar de novo**: o
  escopo do cookie mudou. Isso é esperado, e o login novo já limpa o cookie
  antigo automaticamente;
- confirme que gestor/admin vê o painel completo e que a médica vê só os laudos.

### 9.3 Papel médico e a feature de laudos no ambiente sintético

A captura da médica mostra "Carregando o fluxo seguro de laudos…" porque o
ambiente sintético local está com `M15_REPORTS_ENABLED` desligado — os endpoints
`/laudos/*` respondem 503 ali. Em produção a feature está ligada
(`M15_REPORTS_ENABLED` presente no env da VPS). **O isolamento do menu foi
provado; o funcionamento da bancada M25.21 sob o novo gate depende do seu teste
com a conta real da médica.** Nada no gate toca a bancada, "Meus laudos", a
central de assinatura externa, a assinatura em lote ou o upload de assinados.

### 9.4 O listener HTTP puro é redundante

`soprolife-painel.service` publica o painel em `http://100.87.98.100:8765` sem
TLS. O gate protege os dados ali também (tabela acima) e `m15-security.js`
desativa o formulário de senha em origem HTTP remota — então não há vazamento
nem credencial em claro. Ainda assim, **o serviço é redundante**: o acesso real
é pelo HTTPS do `tailscale serve`. Sugiro desativá-lo numa etapa própria — não
mexi nele porque estava fora do escopo desta missão.

### 9.5 Código JavaScript do painel continua legível com sessão

`js/app.js` e os demais módulos exigem sessão (401 sem ela), mas qualquer usuário
autenticado — inclusive a médica — pode lê-los. É **código, não dado**: não há
segredo ali, e nenhum valor operacional. Registro como decisão consciente, não
como descuido.

### 9.6 Suíte completa do núcleo não foi executada até o fim

Rodei os arquivos diretamente afetados (`test_m21_session_auth.py`, 43 passed) e
todas as suítes do painel. A suíte completa do `nucleo-m15` leva dezenas de
minutos (o hash de senha é deliberadamente lento) e foi interrompida — chegou a
~5% sem falha. **Não afirmo que ela está verde por inteiro.** Vale rodá-la numa
janela dedicada antes da próxima etapa.

### 9.7 Sessões antigas

Cookies emitidos antes do deploy usam o escopo antigo. O login novo os apaga
explicitamente (`security.py`), e a revogação de sessão sempre foi server-side,
então nenhuma sessão fica órfã. O efeito prático é apenas um re-login.

---

## 10. O QUE NÃO FOI TOCADO

Conforme a instrução da missão:

- financeiro da M25.22, preços Pastore, settlements — **intocados**;
- laudos, PDF, banco clínico, assinatura, conclusão médica — **intocados**;
- nenhuma migration criada ou executada;
- nenhum dado de paciente lido, exibido ou movido;
- nenhum valor financeiro alterado.

As únicas mudanças no backend foram o **escopo do cookie de sessão** e a
**limpeza do escopo antigo no login** — ambas de autenticação, ambas necessárias
para que o gate pudesse existir.
