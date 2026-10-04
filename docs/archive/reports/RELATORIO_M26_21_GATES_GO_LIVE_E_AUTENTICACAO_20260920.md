# M26.21 — Reconciliação entre os gates de go-live e a autenticação do painel

Data da execução: 20/09/2026 (America/Sao_Paulo)
Branch oficial: `painel-soprolife-v01`
Commit do incidente investigado: `f9c076102ef8603f550c57f85e4da51b1a94338f`

## Resultado

O deploy de `f9c0761` abortou fail-closed, ANTES de qualquer mutação, com:

```
ERRO REPORTS GO-LIVE (fail-closed): reports_https_workspace_markup_missing
```

O workspace de laudos **estava** no release. O que faltava era compatibilidade
entre duas camadas que evoluíram em momentos diferentes: os gates de go-live,
escritos quando o painel era público, e o gate estático de autenticação, que
fechou o painel na M25.23.

Esta entrega reconcilia as duas sem abrir nada e sem remover verificação
alguma. Nenhum artefato protegido voltou a ser público, nenhuma credencial
administrativa entrou no deploy e o conjunto de provas ficou mais estrito do
que era antes — passou a incluir provas NEGATIVAS (o que não pode vazar).

Nenhum deploy foi executado. Nenhuma alteração em `main`.

## 1. Causa raiz

Três datas explicam tudo:

| Quando | O quê |
| --- | --- |
| 19/07/2026 (M15.5B) | `go_live_https_gate.py` nasce fazendo GET **anônimo** do `index.html` administrativo e de `data/m15-config.json`, exigindo 200 nos dois. |
| M24B/M24D | `reports_go_live_gate.py::check_https_workspace` nasce no mesmo modelo: GET anônimo de `/painel-soprolife/` procurando `id="laudos-espirometria"` e `report-workflow.js`, e GET anônimo do manifesto exigindo 200. |
| 11/08/2026 (M25.23) | `panel_access_gate.py` + `command-center-local-server.py` fecham o painel: sem sessão, `/painel-soprolife/` devolve **login.html com 200** e `data/m15-config.json` devolve **401**. |

Os gates nunca foram atualizados. A partir da M25.23 eles passaram a pedir uma
coisa que o produto, corretamente, deixou de fazer.

**Confirmado no código, não por suposição:**

- `panel_access_gate.classify("/painel-soprolife/")` → `protected_page`;
  `is_panel_entry()` → `True`; o servidor então chama `_serve_login()`, que
  responde **200 com `login.html`** (`command-center-local-server.py:434`).
- `classify("/painel-soprolife/data/m15-config.json")` → `protected_data`;
  sem sessão o servidor responde **401** (`_deny`, mesma função).
- `painel-soprolife/index.html` contém `id="laudos-espirometria"` (linha 577) e
  `report-workflow.js` (linha 975) no commit do incidente; `login.html` não
  contém nenhum dos dois.

Logo: a falha era consequência do login gate, **não** de ausência do workspace.

### Segunda incompatibilidade, latente atrás da primeira

O preflight só não falhou depois porque abortou antes. Com a marcação
corrigida, ele falharia em `reports_https_api_frontend_disagree`: na VPS o
checkout já está no commit alvo quando o script roda (o próprio deploy exige
`HEAD == commit esperado`), então o `m15-config.json` servido já diz
`reports_enabled=true` enquanto o backend ainda roda o release anterior,
desabilitado. O gate antigo exigia concordância entre os dois nesse momento —
o que tornava a primeira ativação do piloto impossível por construção.

### Terceira, no postflight geral

`go_live_https_gate::checar_https_pos` exigia GET anônimo 200 de
`data/m15-config.json` e do `index.html` administrativo com a ordem dos
scripts. Ambos são hoje protegidos. Esse postflight também estava incompatível.

## 2. Arquitetura da correção

A prova foi partida em duas metades, cada uma na fonte que de fato pode
provar aquilo:

**(a) O que é público prova-se por HTTPS anônimo — inclusive negativamente.**

- `/painel-soprolife/` → 200 **com a tela de login** (`id="loginForm"`,
  `id="password"`, `m15-security.js`);
- o mesmo corpo **não** pode conter `m15-nucleo.js`, `id="laudos-espirometria"`
  nem `report-workflow.js` — se o vazamento da M25.23 voltar, o deploy aborta;
- `data/m15-config.json` → **401**, e a recusa não pode carregar fragmento do
  manifesto;
- health M15 → 200 com `status="ok"`.

**(b) O que é administrativo prova-se na fonte local versionada do host.**

- o release implantado é reaprovado no `check-source` (ordem dos scripts,
  `api_base`, marcadores da guarda, ausência de script externo, não
  persistência de token);
- o workspace de laudos é verificado em `index.html` + `js/report-workflow.js`
  do checkout — mesmo código de erro histórico
  (`reports_https_workspace_markup_missing`), agora disparado só quando o
  workspace realmente falta;
- `reports_enabled`/`reports_mode` saem do `m15-config.json` do checkout,
  validados um contra o outro como já eram.

**(c) Serviço e configuração efetiva correspondem ao release implantado.**

- o health informa `versao`, e ela tem de ser igual ao `__version__` de
  `nucleo-m15/app/__init__.py` no checkout, com `ambiente="prod"` e
  `banco="ok"` — prova de que o processo em execução é o do release;
- `login.html` e `m15-security.js` servidos são comparados **byte a byte** com
  os do checkout — os dois já são públicos por desenho, então comparar não abre
  nada, e a igualdade prova que o HTTPS serve ESTE release;
- o estado efetivo de laudos vem do probe anônimo de `/api/m15/laudos`, que
  distingue três casos sem sessão nenhuma, porque `_require_reports_enabled`
  roda antes da autenticação:

  | Resposta anônima | Estado efetivo |
  | --- | --- |
  | `401` | piloto servindo (a autenticação é que recusa) |
  | `503` + `relatorios_desabilitados` | desabilitado |
  | `503` + `relatorios_producao_bloqueada` | produção bloqueada |

  Qualquer outra combinação é `reports_https_api_response_invalid`.

**Preflight × postflight.** As três provas são as mesmas; o rigor do item (c)
difere de propósito. No preflight o checkout já é o release alvo e os serviços
ainda são os anteriores — exigir concordância ali seria exigir que o deploy já
tivesse acontecido. O preflight exige apenas que o backend declare um estado
reconhecido. O postflight exige que ele seja exatamente o do release
implantado.

### Nenhuma verificação foi removida — todas mudaram de fonte

| Código antigo | Onde a exigência está agora |
| --- | --- |
| `reports_https_workspace_markup_missing` | mantido; agora dispara sobre o `index.html` do checkout implantado |
| `reports_https_config_not_200` / `reports_https_config_invalid` | virou o oposto: `reports_https_config_not_protected` exige **401**; o conteúdo vem do checkout |
| `reports_https_frontend_flag_invalid` | `versioned_reports_flag_not_boolean` (mesma checagem, fonte local) |
| `reports_https_api_base_invalid` | `versioned_api_base_invalid` |
| `reports_https_frontend_mode_invalid` / `..._mode_flag_mismatch` | `versioned_reports_mode_invalid` / `versioned_reports_mode_flag_mismatch` |
| `reports_https_api_frontend_disagree` | `reports_https_target_flag_mismatch` / `..._mode_mismatch`, agora contra o **backend efetivo** |
| `enabled=true` servido em `m15-config.json` (postflight geral) | `check-source` no checkout + `versao` do health == `__version__` do release + bytes servidos idênticos |
| ordem dos scripts no `index.html` servido | mesma checagem, no `index.html` do checkout (`checar_fonte_alvo`) |

Novos, todos fail-closed: `reports_https_workspace_markup_leaked`,
`reports_https_login_screen_missing`, `reports_https_config_not_protected`,
`reports_release_index_unreadable`, `reports_release_workflow_script_missing`.

**O que NÃO mudou:** o gate estático, o servidor, o modelo de sessão, o
bloqueio incondicional de produção de laudos (M24C), autorização dedicada do
piloto, storage, `ReadWritePaths`, manifesto de backup, prompt interativo,
backup/bundle/pg_dump e todo o hardening de units.

## 3. Arquivos alterados

| Arquivo | O que mudou |
| --- | --- |
| `painel-soprolife/nucleo-m15/scripts/go_live_https_gate.py` | superfície anônima (login servido, admin não vaza, manifesto 401); `checar_https_pos` passou a receber o repo root e a provar release + serviço + bytes servidos; códigos estáveis de rejeição; aridade da CLI por subcomando |
| `painel-soprolife/nucleo-m15/scripts/reports_go_live_gate.py` | `check_release_workspace` (prova local); `_effective_backend_state` (probe anônimo de 3 estados); `check_https_workspace` reescrito; `check_pilot_postflight` recebe repo root; falha de transporte também sai fail-closed com mensagem |
| `painel-soprolife/nucleo-m15/scripts/lib-go-live-gate.sh` | fase `pos` exige o repo root; ausência dele é rejeição, não validação parcial |
| `painel-soprolife/nucleo-m15/scripts/deploy-producao-vps.sh` | passa `$REPO_ROOT` ao postflight geral; cabeçalho atualizado |
| `painel-soprolife/nucleo-m15/scripts/activate-reports-pilot-vps.sh` | as três chamadas de `check_https_workspace` passam o repo root |
| `painel-soprolife/nucleo-m15/scripts/test_go_live_https_gate.py` | probes reescritos para o modelo atual + casos de vazamento/manifesto público/versão divergente/bytes divergentes + aridade da CLI |
| `painel-soprolife/nucleo-m15/scripts/test_m26_21_go_live_auth_compat.py` | **novo** — prova as duas metades no mesmo teste |
| `painel-soprolife/nucleo-m15/scripts/test-deploy-go-live.sh` | fase `pos` sem repo root falha fechado; aridade da CLI; fiação do deploy |
| `painel-soprolife/nucleo-m15/tests/test_m24b_reports_go_live_gate.py` | fixtures no modelo atual; acordo provado contra o backend efetivo; vazamento e manifesto público recusados |
| `painel-soprolife/nucleo-m15/tests/test_m24d_reports_pilot.py` | fixtures no modelo atual |
| `painel-soprolife/nucleo-m15/tests/test_m24d_pilot_deployment.py` | fixtures no modelo atual; postflight recebe repo root; release fora de `pilot` é recusado |
| `painel-soprolife/scripts/quality-gate-safe.sh` | seção 8d roda o teste novo |
| `painel-soprolife/docs/m15-5b-go-live-deploy-bridge.md` | contrato pré/pós atualizado e a tabela do porquê |
| `painel-soprolife/docs/m24d-reports-pilot.md` | seção "Como o acordo é provado hoje (M26.21)" |

Não foram tocados: `panel_access_gate.py`, `command-center-local-server.py`,
`index.html`, `login.html`, `m15-config.json`, nem o bloco
"Pastore Ipanema — tráfego gerado pela SoproLife".

## 4. O que os testes provam

- anônimo recebe `login.html` em `/painel-soprolife/` e em
  `/painel-soprolife/index.html`, com os bytes REAIS do repositório, e nunca o
  Command Center;
- anônimo recebe 401 no manifesto de boot, em `data/*.local.json`, em
  `js/m15-nucleo.js` e em `js/report-workflow.js`; 404 em `data-private/`,
  `nucleo-m15/`, `scripts/` e `.git/`; HEAD passa pelo mesmo gate;
- `m15-security.js` continua público de propósito — é o que a própria tela de
  login carrega;
- preflight geral valida HTTPS, health e o 401 do manifesto;
- preflight de laudos aceita o cenário exato do incidente (checkout no release
  alvo, backend ainda desabilitado);
- postflight geral verifica o release sem exigir vazamento anônimo, e recusa
  versão divergente, `ambiente != prod`, banco degradado e bytes servidos de
  outro release;
- postflight de laudos exige piloto efetivo e recusa backend desabilitado e
  release fora de `pilot`;
- vazamento do Command Center, manifesto público e resposta irreconhecível da
  API derrubam os dois gates;
- nenhum script de deploy/gate menciona `Cookie`, `Authorization`, `Bearer` ou
  endpoint de autenticação, e `http_get` não tem parâmetro de cabeçalho — não
  há como autenticar mesmo querendo;
- o deploy continua interativo (`-t 0 && -t 1`), com `IMPLANTAR M15`, bundle e
  `pg_dump`, e os gates continuam antes de prompt, `sudo` e backup.
