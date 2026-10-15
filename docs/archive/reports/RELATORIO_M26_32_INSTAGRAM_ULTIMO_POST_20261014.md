# M26.32 — Convite de Instagram com a publicação mais recente

**Data:** 14/10/2026
**Base:** `origin/painel-soprolife-v01` em `68533ff` (M26.31)
**Worktree:** `/home/fedorasurf/soprolife-worktrees/claude-m26-32-instagram-ultimo-post`

---

## 1. O pedido e o que de fato dava para entregar

O botão "Instagram" da M26.31 manda ao paciente o endereço do perfil. O
pedido desta etapa é mandar o **permalink da publicação mais recente**, para
que o WhatsApp monte a prévia grande (imagem ou Reel) em vez de um link cru.

A auditoria respondeu antes de qualquer linha de código: **não existe nenhuma
credencial da Meta neste projeto.** Então o que esta milestone entrega é a
integração inteira — provider oficial, cache, validação e os três fallbacks —
**ligada a um interruptor que está desligado**. No dia em que o token existir,
ligar é editar o `m15.env` e reiniciar um serviço; nada de código muda.

Enquanto isso, o botão faz exatamente o que fazia ontem, e a frase do convite
foi reescrita conforme o enunciado.

---

## 2. Auditoria — o que existia

### 2.1 A M26.31, peça por peça

| Peça | Onde | Estado |
|---|---|---|
| `INSTAGRAM_URL` (fonte única do perfil) | `app/services/patient_results.py:763` | intacta |
| `MENSAGEM_INSTAGRAM` / `build_instagram_message` | `app/services/patient_results.py:765` | reescrita (§4) |
| `GET /laudos/{id}/convite-instagram` | `app/routers/patient_results.py:341` | estendida (§5) |
| Botão e painel na fila de entrega | `js/report-workflow.js:3335` e `:3344` | **sem alteração** |
| Recusas (opt-out, consentimento, sem telefone) | `app/routers/patient_results.py:316` | intactas |
| Trilha `convite_instagram_exibido` | `app/routers/patient_results.py:384` | ganhou `fonte_instagram` |

### 2.2 Credenciais da Meta — procura e resultado

Procurado em três lugares, nesta ordem:

```
# repositório (todo o worktree)
grep -rniI "graph.facebook|facebook|meta_app|IG_USER|GRAPH_API" --include=*.py --include=*.sh --include=*.env* .
  → 3 ocorrências, todas em TESTES que PROÍBEM pixel do Facebook em página
    pública (test_m26_19, test_m26_4) e uma citação em relatório antigo.

# VPS — nomes das chaves, nunca valores
ssh root@<tailnet> 'grep -oE "^[A-Za-z0-9_]+=" /opt/soprolife/secrets/m15.env'
  → 14 chaves: M15_API_HOST, M15_API_PORT, M15_AUTH_SECRET, M15_CORS_ORIGINS,
    M15_DATABASE_URL, M15_DISPLAY_TIMEZONE, M15_ENV, M15_PORTAL_ENABLED,
    M15_PORTAL_PUBLIC_BASE_URL, M15_PORTAL_TOKEN_KEY, M15_REPORTS_ENABLED,
    M15_REPORTS_MODE, M15_REPORTS_STORAGE_DIR, M15_TOKEN_TTL_MINUTES.
    Nenhuma de Meta/Instagram/Facebook.

# demais segredos da VPS
/opt/soprolife/secrets/: ana-primeiro-acesso.txt, m15.env (+3 backups),
  m26-4-portal.env, marketing-readonly.json, nfse-certificate-margin.env
  → marketing-readonly.json é conta de serviço GOOGLE (Search Console/GA4),
    não Meta. m26-4-portal.env só tem chaves M15_PORTAL_*/M15_REPORTS_*.
```

**Conclusão:** zero integração Meta, zero credencial Meta. Nenhum valor de
segredo foi lido, impresso ou copiado — só a lista de nomes de chave.

---

## 3. O provider — `app/services/instagram_feed.py` (novo)

### 3.1 Qual API oficial, e por que esta

Duas portas oficiais levam ao último post:

- **Instagram API with Facebook Login** (`graph.facebook.com`) — exige Página
  do Facebook vinculada, escopos `instagram_basic` + `pages_read_engagement`,
  e às vezes `ads_management`;
- **Instagram API with Instagram Login** (`graph.instagram.com`) — token
  emitido pela própria conta profissional do Instagram, escopo único
  `instagram_business_basic`, sem Página do Facebook no caminho.

Escolhida a segunda: **uma credencial a menos para existir, renovar e vazar.**
A Basic Display API, que seria a terceira porta, foi desligada pela Meta em
04/12/2024 e não é opção.

Contrato usado:

```
GET https://graph.instagram.com/v25.0/{me|IG_ID}/media
    ?fields=permalink,timestamp,media_type&limit=12
    Authorization: Bearer <token>
```

A versão `v25.0` é constante de código (disponibilidade garantida pela Meta
até 29/07/2028), não variável de ambiente — mesma decisão do SERPRO e do
NFS-e de produção: configuração não pode apontar o cliente para outro servidor
nem para uma versão não testada.

### 3.2 Regra fundamental: nada de scraping

Nenhum HTML de `instagram.com` é lido em lugar nenhum. A única origem do
permalink é a Graph API, e o host é conferido imediatamente antes da chamada
(`instagram_feed.py`, defesa em profundidade sobre uma constante).

### 3.3 Escolha da publicação

Pedimos uma página de 12 e **reordenamos aqui** pelo `timestamp` que a própria
Meta informou. A Meta hoje devolve em ordem reversa de criação, mas "devolve
hoje" não é contrato — e um teste com a lista deliberadamente embaralhada
prova que a escolha não depende da ordem recebida.

Imagem, vídeo, carrossel e Reel valem igual. **Não existe preferência por
formato**: o critério é só a data.

**Item sem `timestamp` legível não concorre.** Pedimos `timestamp`
explicitamente à API: um item que volta sem ele não oferece evidência de ser
"o mais recente", e chamá-lo assim seria afirmar o que não se sabe. Se
nenhum item tiver data legível, o resultado é "não há publicação" e a ordem
normal de fallback assume. A comparação é entre `datetime` — não texto —,
o que também resolve fusos diferentes entre itens.

### 3.4 O permalink é validado antes de ir para o paciente

Esta string vai para o WhatsApp de uma pessoa, então sai daqui com forma
conhecida: HTTPS, host `www.instagram.com` ou `instagram.com`, caminho
começando em `/p/`, `/reel/`, `/reels/` ou `/tv/`, código curto alfanumérico,
sem querystring, sem fragmento, sem credencial embutida, no máximo 300 bytes.
Qualquer outra coisa é tratada como "não há publicação" e o convite cai para
o perfil.

---

## 4. A mensagem

**Com publicação recente** (o caso que o pedido quer):

```
Olá, {primeiro nome}. 😊

Acompanhe a SoproLife diariamente no Instagram para receber informações,
orientações e conteúdos sobre saúde respiratória.

Veja nossa publicação mais recente:
{permalink}

Siga @soprolife para acompanhar as próximas publicações.

SoproLife Diagnósticos e Soluções em Saúde
```

**Sem publicação** (perfil — o convite de hoje, com a frase nova):

```
Olá, {primeiro nome}. 😊

Acompanhe a SoproLife diariamente no Instagram para receber informações,
orientações e conteúdos sobre saúde respiratória:

https://www.instagram.com/soprolife

SoproLife Diagnósticos e Soluções em Saúde
```

Duas decisões de formatação que não são cosméticas:

- **o permalink ocupa uma linha sozinho** — URL isolada é a forma que o
  WhatsApp reconhece melhor; colada a texto, costuma virar só link azul;
- **`@soprolife` fecha a mensagem como texto, não como segunda URL** —
  havendo duas URLs, a prévia tende a sair da primeira, e a primeira tem de
  ser a publicação.

**A prévia não é garantida.** O permalink isolado, como primeira e única
URL, *favorece* a geração do cartão, mas quem decide renderizar é o
WhatsApp (e o que o Instagram serve a ele). Não há, e não deve haver,
prévia própria montada por nós.

---

## 5. Cache e a ordem exata dos fallbacks

**TTL: 45 minutos** (configurável em `M15_INSTAGRAM_FEED_CACHE_MINUTES`, com a
faixa 30–60 imposta por validação: abaixo de 30 o botão viraria tráfego à Meta,
acima de 60 uma publicação do dia demoraria demais a aparecer).

Cache **em memória de processo**, com `threading.Lock`. Não foi criada tabela
nem dependência nova: o dado é público, tem dezenas de bytes e vive minutos —
Postgres ou Redis para isso seria infraestrutura nova para guardar aquilo que
o Instagram já publica. Guarda `permalink`, `timestamp` da publicação, o
instante da obtenção e o estado da fonte.

Ordem, na implementação e nos testes:

| # | Situação | Fonte | O paciente recebe |
|---|---|---|---|
| 0 | sem credencial configurada | `desligado` | **perfil** (a rede nem é tocada) |
| 0b | o convite já estava recusado | `nao_consultado` | **perfil** (a Meta nem é consultada — §5.1) |
| 1 | cache dentro do TTL | `cache` | a publicação |
| 2 | consulta à Meta deu certo | `meta` | a publicação |
| 3 | Meta falhou, reserva com < 7 dias | `cache_vencido` | **a última publicação conhecida** |
| 4 | Meta falhou, sem reserva utilizável | `indisponivel` | **perfil** |
| 5 | a conta respondeu e não tem publicação válida | `sem_publicacao` | **perfil** |

Dois detalhes que resolvem casos reais:

- **o cache vencido não é descartado, vira reserva.** Um permalink do
  Instagram não caduca: servir o último conhecido depois de a API falhar é
  melhor que cair no perfil;
- **o caso 5 APAGA o cache — e só ele.** Lista **vazia de verdade** significa
  que a publicação foi apagada, e insistir no permalink guardado mandaria ao
  paciente um link morto. Já um corpo cujo campo `data` não é lista é tratado
  como **falha de chamada**, não como conta vazia: um corpo que ninguém
  entendeu não pode custar o último permalink bom. Essa distinção era um
  defeito real da primeira versão (§8.1c).

**Teto de contingência — 7 dias.** A reserva do caso 3 não vale para
sempre. Passados `MAX_CONTINGENCIA_SEGUNDOS` (7 dias) sem **uma única**
consulta bem sucedida, o convite volta ao perfil. O caso real é o token
vencer e ninguém renovar: sem teto, o painel afirmaria "nossa publicação
mais recente" por meses sobre algo que já não se consegue confirmar.

O teto mede a **idade da confirmação, não a da publicação**. Uma conta que
não posta há três meses tem, sim, uma publicação mais recente, e divulgá-la
está correto — há teste próprio para impedir que alguém, lendo "7 dias",
passe a descartar posts antigos. Uma consulta bem sucedida **reinicia** o
prazo.

**Contenção de falha (5 min):** depois de uma falha, novas tentativas só saem
após `BACKOFF_SEGUNDOS`. Sem isso, com o token vencido — que é a falha que
dura dias —, cada clique do operador viraria uma chamada que já se sabe
perdida.

### 5.1 A recusa vem antes da Meta

A rota decide **primeiro** se este paciente pode ser contatado
(`_destino_do_convite`) e só **depois** procura o que mandar. Quem pediu
para não ser contatado, quem revogou o consentimento de WhatsApp, quem não
tem telefone discável e quem está com o cadastro arquivado **não geram uma
única chamada à Meta**.

Duas razões. A prática: não se gasta chamada externa — nem se registra uso
num serviço de terceiro — por uma mensagem que já se sabe que não vai sair.
A estrutural: nenhum dado do paciente participa da consulta (ela é sobre a
**conta da clínica**), e manter essa ordem é o que garante que isso continue
verdade se um dia alguém quiser "personalizar" o feed por paciente.

Nesses casos a mensagem exibida é a do perfil, e a trilha registra
`fonte_instagram = nao_consultado`. Quatro testes provam a ausência da
chamada com um dublê que **levanta exceção** se o provider for tocado — a
prova não pode ser "o permalink não apareceu", porque isso também seria
verdade se a chamada saísse e falhasse.

**Em nenhum dos seis casos o operador vê erro de integração.** O provider não
levanta exceção; o pior desfecho possível é a mensagem de ontem.

---

## 6. Segurança

- o token é `SecretStr` e **vive só no `m15.env` da VPS**. Não está no Git,
  não está em JS/HTML, não está na resposta HTTP, não está na trilha;
- **o token viaja no cabeçalho `Authorization`, nunca na querystring.** Por
  isso não existe URL com segredo para vazar em log, traceback ou métrica —
  e os loggers `httpx`/`httpcore` ficam em `WARNING` como cinto adicional;
- os logs de falha carregam **só o status HTTP e a classe da exceção**
  (`HTTP 401`, `ConnectError`). Nenhum corpo de resposta da Meta, nenhum
  cabeçalho, nenhuma URL;
- a trilha de auditoria ganhou uma chave só, `fonte_instagram`, de catálogo
  fechado (as seis palavras da tabela acima), adicionada à `ALLOWED_KEYS`
  com justificativa. Sem ela a auditoria não distingue "o paciente recebeu a
  publicação do dia" de "o paciente recebeu o perfil porque a integração
  estava fora";
- **a resposta HTTP não carrega diagnóstico.** Ela devolve exatamente o que
  a M26.31 devolvia — `instagram_url`, `mensagem`, `whatsapp`. O estado da
  integração vive só na trilha: a tela não o usava, e informação interna que
  ninguém consome numa resposta é superfície sem dono;
- **a validade do cache é medida em relógio monotônico** (`time.monotonic`),
  nunca em hora de parede. Um ajuste de NTP, o horário de verão ou um
  `date -s` do operador movem a hora de parede para trás e fariam um cache
  vencido parecer novo — ou aposentariam um cache bom antes da hora. A hora
  de parede fica guardada à parte (`confirmado_em`), só para o humano ler;
- **a aplicação não renova o token e não escreve no arquivo de segredos.**
  Renovação é operação humana documentada (§9). Se um dia for automatizada,
  será unidade operacional separada — timer, escrita atômica, permissão e
  rollback —, nunca o processo web mexendo no próprio segredo;
- `M15_INSTAGRAM_USER_ID` é opcional e **só aceita dígitos** — este valor é
  concatenado no caminho da URL e não pode virar injeção de rota.

---

## 7. O que mudou no navegador

Quase nada, de propósito: a mensagem já descia pronta do servidor, então o
painel não precisou aprender nada sobre a Meta. A única alteração é uma regra
de CSS.

O permalink é uma palavra de 43 caracteres (`/reel/` + o código de 11 do
Instagram) que nenhuma quebra natural divide. Medido com Chrome headless: a
1920/1366/1024/430/390 ela ainda cabe; **a 360px (Android antigo) a linha
empurra a fila e a página ganha rolagem horizontal** — `scrollWidth` 361
contra 360 de viewport. Daí `overflow-wrap: anywhere` em
`.report-instagram-message` (`anywhere` e não `break-word`, porque só ele
também encolhe a caixa na medida do contêiner).

A bancada foi rodada nos dois sentidos: com a regra, as seis larguras passam;
sem a regra, 360px acusa a rolagem. É a prova de que a regra não é decorativa.

---

## 8. Testes

**`tests/test_m26_32_instagram_ultimo_post.py` — 59 casos novos**, cobrindo os
18 pontos pedidos:

| Pedido | Caso |
|---|---|
| 1 publicação mais recente | `test_publicacao_mais_recente_entra_na_mensagem`, `test_escolhe_por_data_e_nao_por_formato_nem_por_ordem` |
| 2 permalink `/p/` | `test_permalink_de_post_vale` |
| 3 permalink `/reel/` | `test_permalink_de_reel_vale_igual` |
| 4 Meta indisponível | `test_meta_indisponivel_nao_vira_erro` |
| 5 timeout | `test_timeout_nao_vira_erro` |
| 6 resposta inválida | `test_resposta_invalida_cai_para_o_perfil` (10 variantes: corpo não-JSON, forma errada, host falso, perfil no lugar de post, `javascript:`, HTTP 400/401/429/500) |
| 7 token não configurado | `test_token_nao_configurado_nao_toca_a_rede` (4 combinações; prova que **nenhuma** chamada sai) |
| 8 cache válido | `test_cache_valido_nao_consulta_a_meta_de_novo` |
| 9 cache expirado | `test_cache_expirado_consulta_de_novo_e_atualiza` |
| 10 fallback p/ último post | `test_fallback_para_o_ultimo_post_conhecido` |
| 11 fallback final p/ perfil | `test_a_ordem_dos_tres_fallbacks_na_mensagem` (os três, em sequência, pela rota) |
| 12 telefone ausente | `test_telefone_ausente_continua_recusando_com_o_motivo` |
| 13 opt-out | `test_opt_out_continua_valendo_mesmo_com_publicacao` |
| 14 consentimento revogado | `test_consentimento_revogado_continua_valendo` |
| 15 botão anterior intacto | `test_o_botao_anterior_continua_intacto` |
| 16 nenhum segredo no frontend | `test_nenhum_segredo_chega_ao_frontend` |
| 17 nenhum token em logs | `test_nenhum_token_nos_logs` |
| 18 responsividade | `test_a_mensagem_longa_nao_estoura_o_cartao` + bancada Chrome headless |

Extras: 19 casos de validação de permalink (5 aceitos, 14 recusados), conta
sem publicação, contenção de falha, e configuração fora de faixa.

Nada toca a rede: a Graph API é `httpx.MockTransport` e o relógio é injetado.
Pacientes "TESTE APAGAR", telefones no prefixo 0000 (não atribuído no Brasil),
token sintético que nunca existiu.

### 8.1 Suíte completa

`3172 passed, 48 skipped, 0 failed` (17min18s), suíte inteira do núcleo. Três coisas tiveram de
ser consertadas no caminho, e vale registrar o que cada uma era:

**(a) Dois testes da M26.31 afirmavam a frase antiga.** Ambos checavam
`"seguir a SoproLife no Instagram"`, que o enunciado desta milestone
reescreveu. A asserção passou a afirmar a frase nova; o contrato que elas
protegem (convite ao Instagram, um endereço só, no corpo da mensagem) está
intacto.

**(b) O carimbo `?v=` dos assets estava divergente ANTES desta etapa.** A
M26.31 subiu `report-workflow.js/.css` para `2026101401` sem trazer o pino de
`test_assets_alterados_tem_cache_busting_atual`, que seguia em `2026100102` —
o teste já falhava no commit implantado `68533ff`. Como esta etapa altera o
CSS, os dois subiram para `2026101402` **com o pino junto**, fechando a
divergência. Sem isso, um navegador com o CSS velho em cache mostraria a
mensagem longa sem a quebra.

**(c) O teste de log media o nada na suíte inteira — e escondia um defeito.**
Ele usava `caplog`, que depende da propagação até a raiz e do estado global de
`logging`. Rodando o arquivo isolado capturava; na suíte, `caplog.records`
vinha vazio e `assert TOKEN not in ""` passava sempre. A primeira correção —
um handler pendurado em `feed.logger` — falhou igual, porque também depende de
o logger estar habilitado e no nível certo na hora. A correção que ficou
substitui `feed.logger` por um **dublê** que guarda o que o módulo manda
registrar: mede a chamada, não o encanamento do `logging`. Junto veio uma
**contagem de tentativas de rede**, para que um curto-circuito silencioso
falhe em vez de aprovar o silêncio.

Com o dublê medindo de verdade, apareceu um **defeito de produção**: o corpo
`{"data": "uma string"}` não gerava linha de log porque o código o tratava
como "a conta não tem publicação" — o caso que APAGA o cache. Uma resposta
malformada da Meta descartaria o último permalink bom. Corrigido, com teste
próprio (`test_corpo_malformado_preserva_o_cache`). **Um teste de segurança
que passa sem medir nada não só não protege: ele esconde.**

Duas falhas do `quality-gate-safe.sh` (`test-m21-auth-crm-nav` e
`test-m25-26-fluxo-espirometria`) são **anteriores a esta etapa**: o gate
falha exatamente igual no `68533ff` pristino (verificado com `git stash`).
Fora do escopo desta missão; registradas aqui para não serem confundidas com
regressão.

Nota de ambiente: `tests/test_live_multisheet_reader.py` (12 casos) exige
`google-api-python-client`, que não está em `requirements.txt` nem em
`requirements-dev.txt`. Numa venv limpa eles falham por `ModuleNotFoundError`;
com o pacote instalado, passam. Não é regressão, mas a dependência de teste
merece entrar no `requirements-dev.txt` numa próxima etapa.

---

## 8.2 Ajustes pedidos pela revisão externa

A revisão aprovou a arquitetura (API oficial, sem scraping, token só no
backend, Bearer, cache em memória, chamada fora do lock, fallback para
perfil, `v25.0`, validação rígida do permalink) e pediu nove ajustes. Todos
aplicados:

| # | Pedido | O que mudou |
|---|---|---|
| 1 | suíte real antes do deploy | nenhum placeholder; resultado em §8.1 |
| 2 | teto para o cache vencido | `MAX_CONTINGENCIA_SEGUNDOS = 7 dias` desde a última obtenção bem sucedida; 3 testes novos, incluindo o que separa idade da confirmação da idade da publicação |
| 3 | item sem `timestamp` não concorre | `timestamp_valido()`; comparação por `datetime`; teste com data ausente, vazia, ilegível e não-string |
| 4 | tirar o diagnóstico da resposta | `publicacao_url` e `publicacao_fonte` removidos; a resposta voltou a ser a da M26.31; `fonte_instagram` continua na trilha |
| 5 | recusa antes da Meta | rota reordenada (§5.1); 4 testes com dublê que explode se o provider for tocado |
| 6 | relógio monotônico | TTL, contenção e teto em `time.monotonic`; hora de parede só em `confirmado_em`; teste que inspeciona a fonte e proíbe hora de parede na aritmética |
| 7 | timeout 6 s → 3 s | padrão `3.0`, faixa 1–20 preservada; teste confirma que o valor chega ao cliente HTTP |
| 8 | não prometer a prévia | redação corrigida no código e no relatório (§4) |
| 9 | não automatizar renovação do token | nada implementado; a proibição está escrita no cabeçalho do módulo (§6) |

**Sobre o item 4:** auditei o frontend antes de remover. `js/report-workflow.js`
nunca leu `publicacao_url` nem `publicacao_fonte` — a etapa não tocou o
JavaScript, e o painel só consome `mensagem`, `whatsapp` e `instagram_url`.
Não havia razão de UI para mantê-los, então saíram. O teste
`test_nenhum_segredo_chega_ao_frontend` agora trava o formato da resposta
(`set(corpo) == {"instagram_url", "mensagem", "whatsapp"}`), e os testes que
liam o estado pela resposta passaram a lê-lo na trilha.

---

## 9. O que falta para ligar de verdade

Nenhum segredo é mostrado aqui. O que precisa existir:

1. **a conta `@soprolife` como conta profissional** (Business ou Creator) —
   conta pessoal não serve para nenhuma API oficial;
2. **um app da Meta do tipo Business**, com o produto *Instagram* adicionado e
   a conta conectada em *API setup with Instagram business login*;
3. **um token de usuário Instagram de longa duração**, escopo
   `instagram_business_basic`. Gerado no painel do app (*Generate token*) ou
   pelo fluxo de Business Login.

Configuração na VPS, em `/opt/soprolife/secrets/m15.env` (hoje `640`,
`root:soprolife`, lido pelo `EnvironmentFile` da unit `soprolife-m15-api`):

```
M15_INSTAGRAM_FEED_ENABLED=true
M15_INSTAGRAM_ACCESS_TOKEN=<o token de longa duração>
# opcionais, ambos com padrão seguro:
# M15_INSTAGRAM_USER_ID=<só dígitos; vazio = o dono do token>
# M15_INSTAGRAM_FEED_CACHE_MINUTES=45     (faixa 30–60)
# M15_INSTAGRAM_FEED_TIMEOUT_SECONDS=3.0  (faixa 1–20)
```

Depois: `systemctl restart soprolife-m15-api`. Nada mais.

**Manutenção que o recurso cria:** o token vence em **60 dias** e não se
renova sozinho. A renovação é `GET graph.instagram.com/refresh_access_token`
com o token ainda válido, e o recomendado é renovar entre o 30º e o 45º dia —
um token **vencido** não pode mais ser renovado e exige refazer o login. Se
vencer, nada quebra: o convite volta ao perfil sozinho, sem erro na tela.

---

## 10. Arquivos

| Arquivo | O que mudou |
|---|---|
| `nucleo-m15/app/services/instagram_feed.py` | **novo** — provider, cache, validação, fallbacks |
| `nucleo-m15/app/config.py` | 5 campos novos, 3 validadores, `instagram_feed_ready()` |
| `nucleo-m15/app/services/patient_results.py` | mensagem nova + `build_instagram_message(pessoa, publicacao=None)` |
| `nucleo-m15/app/routers/patient_results.py` | consulta o provider; resposta ganha `publicacao_url`/`publicacao_fonte`; trilha ganha `fonte_instagram` |
| `nucleo-m15/app/audit.py` | `fonte_instagram` na `ALLOWED_KEYS` |
| `css/report-workflow.css` | `overflow-wrap: anywhere` na mensagem |
| `index.html` | `?v=` de `report-workflow.js/.css` → `2026101402` |
| `nucleo-m15/tests/test_m25_29e_pos_assinatura_downloads.py` | pino do `?v=` alinhado (divergência herdada da M26.31) |
| `nucleo-m15/tests/test_m26_32_instagram_ultimo_post.py` | **novo** — 59 casos |
| `nucleo-m15/tests/test_m26_31_convite_instagram.py` | 2 asserções de redação |
| `docs/SOPROLIFE_MASTER_CONTEXT.md` | integração registrada, pendência registrada |

`js/report-workflow.js` e `index.html` **não foram tocados**.
