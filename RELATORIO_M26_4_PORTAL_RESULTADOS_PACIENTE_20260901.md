# M26.4 — Portal de Resultados do Paciente + Entrega Automatizada

**Executado em 01–02/09/2026.** Worktree novo, a partir de
`origin/painel-soprolife-v01` (`5e1345c`).

**Estado:** implementado, testado, integrado e implantado. Falta **uma**
ação externa — o registro DNS no Registro.br — descrita na seção 12.

| | |
|---|---|
| Branch de trabalho | `claude-m26-4-portal-resultados-paciente` |
| HEAD oficial (`painel-soprolife-v01`) | `b39def0` |
| HEAD do site público (`main`) | `d66246d` |
| HEAD da VPS | `b39def0` |
| Migração | `c3a9e15f7d84` (head) |
| Backup pré-migração | `/opt/soprolife/backups/m26-4/m26-4-pre-20260902T025536Z.dump` (603 KB) |
| Testes | 46 novos; suíte completa **1602 passed, 30 skipped, 1 failed** (a falha é pré-existente — ver §10) |

---

## 1. Auditoria da arquitetura (feita ANTES de escrever código)

### 1.1 Como `soprolife.com.br` está hospedado hoje

```
$ dig +short soprolife.com.br
185.199.111.153  185.199.109.153  185.199.108.153  185.199.110.153
$ dig +short www.soprolife.com.br CNAME
adeildojunior.github.io.
$ dig +short soprolife.com.br NS
f.sec.dns.br.   e.sec.dns.br.
```

**Site institucional = GitHub Pages**, e a auditoria achou aqui o primeiro
detalhe que mudaria o plano:

```
$ gh api repos/AdeildoJunior/soprolife-site/pages
"source": {"branch": "main", "path": "/"}
```

O Pages serve **`main`**, não a branch do painel. Publicar em
`painel-soprolife-v01` NÃO colocaria a página no ar. E `main` **não contém**
`painel-soprolife/` — a separação está certa e não pode ser desfeita: fundir
a branch do painel em `main` publicaria o Command Center inteiro na
internet.

Por isso a página do paciente foi para `main` em **um commit próprio, com
três arquivos**, nenhum deles do painel (seção 6).

**DNS autoritativo no Registro.br** (`*.sec.dns.br`) — painel externo, sem
API disponível. É a origem da única pendência.

### 1.2 Onde vive o Command Center hoje

VPS Hostinger (`srv1791147.hstgr.cloud`, Ubuntu 24.04.4), IPv4 público
**187.127.39.5**, acesso administrativo só por Tailscale.

- `soprolife-m15-api` → `127.0.0.1:8015`, health em `/api/v1/health`
- `soprolife-painel-loopback` → `:8765` (estático, exposto só na `tailscale0`)
- PostgreSQL → `127.0.0.1:5432`
- `ufw` ativo: só OpenSSH e 8765 na `tailscale0`
- **nenhum nginx, nenhum certbot instalados**
- `app/config.py::_regras_de_prod` recusa bind não-loopback em produção —
  trava real, **não afrouxada**

### 1.3 O gatilho clínico que já existe (M25.29H)

`ExternalSignedDocument.status` vira `recebido_assinado` em exatamente dois
lugares de `app/routers/reports.py`:

- `POST /laudos/assinatura-externa/enviar` — a médica devolve o PDF
  assinado; `avaliar_guardas_documentais` decide sobre os bytes;
- `POST /laudos/assinatura-externa/confirmar` — compatibilidade com lotes
  abertos antes da M25.29H, que reaplica as mesmas guardas.

São os dois — e só os dois — pontos onde o acesso do paciente passa a nascer.

### 1.4 Onde estão os dois documentos

| Documento | Origem exata |
|---|---|
| Laudo assinado final | `ExternalSignedDocument.report_document_version_id` |
| Exame técnico (MIR) | versão `kind == "original"` do MESMO `ReportDocument` |

`source_version_id` e `current_version_id` **não** são usados para entrega.

### 1.5 Integração WhatsApp existente

```
$ grep -rn "wa.me|graph.facebook|Cloud API" app/
app/services/followup.py:226:  return f"https://wa.me/{phone}?text={quote(message)}"
```

**Não existe integração oficial Meta WhatsApp Business / Cloud API** — nem no
código, nem nas configurações. Só o construtor `wa.me` que CRM e follow-up já
usam, com a regra do README: "monta URL para revisão humana; **nunca** dispara
envio automático". ⇒ **V1 manual**, conforme a missão.

---

## 2. O desenho

```
                  INTERNET                          │        TAILSCALE
  paciente                                          │   Dra. Ana / operação
     │ 1. abre o link do WhatsApp                   │          │
     ▼                                              │          ▼
  soprolife.com.br/resultados/#t=<token>            │   Command Center :8765
  (GitHub Pages — estático, sem backend)            │          │
     │ 2. POST {token, nascimento}                  │          ▼
     ▼                                              │   API M15 :8015 (privada)
  resultados-api.soprolife.com.br                   │          │
  nginx  ──►  127.0.0.1:8016                        │          │ gatilho
              soprolife-portal-resultados  ◄────────┼──────────┘
```

A fronteira é feita de coisas verificáveis, e todas foram verificadas em
produção (seção 9):

| | Command Center | Portal público |
|---|---|---|
| processo | `soprolife-m15-api` | `soprolife-portal-resultados` |
| porta | `127.0.0.1:8015` | `127.0.0.1:8016` |
| exposição | só Tailscale | nginx → internet |
| rotas | `/api/v1/**` (153) | `/p/v1/**` (6) |
| papel de banco | `soprolife_m15` (dono) | `soprolife_portal` (GRANT por coluna) |
| cookie | `soprolife_m15_sessao` | `soprolife_resultado` |
| segredo do cookie | `M15_AUTH_SECRET` | `M15_PORTAL_SESSION_SECRET` |
| deriva links? | sim (`M15_PORTAL_TOKEN_KEY`) | **não tem a chave** |

---

## 3. O token

```
token = base64url( HMAC-SHA256( M15_PORTAL_TOKEN_KEY, "<access_id>:<geração>" ) )
```

32 bytes = **256 bits**, não sequencial. O banco guarda **só**
`sha256(token)`.

Isso resolve dois requisitos que normalmente brigam:

- **nunca armazenar o segredo** — o processo público compara hashes e não
  reconstrói link nenhum, nem lendo a tabela inteira;
- **poder reenviar o mesmo link** — o painel privado re-deriva quando o
  operador pede, porque só ele tem a chave.

Regenerar é `generation += 1`: o hash muda, o link que estava no WhatsApp
morre no instante, a linha permanece inteira para auditoria.

O token viaja no **fragmento** (`#t=`), que o navegador não envia ao
servidor: fora do access log, fora do `Referer`, fora do proxy. Também fora
de `audit_logs` — a allowlist de `app/audit.py` não tem chave para ele — e
fora do console. O `access_log` do uvicorn e do nginx estão desligados.

---

## 4. Os dois fatores

1. o **link** (256 bits);
2. a **data de nascimento**, comparada sem margem. Cadastro sem data nunca
   autentica.

Falhou qualquer um — inclusive token inexistente — a resposta é a **mesma**:
401, código `acesso_invalido`, mesma mensagem. Sem oráculo de existência.

| camada | onde vive | limite |
|---|---|---|
| por acesso (data errada) | colunas `failed_attempts` / `locked_until` | 5 tentativas → 15 min |
| por origem (token chutado) | memória do processo, `sha256(ip)[:16]` | 20 falhas / 5 min |
| na borda | `limit_req` do nginx | 30 req/min, burst 10 |

O contador por acesso vive no **banco**: reiniciar o processo público não
pode ser o jeito de zerar um bloqueio.

Autenticado: sessão de **30 min**, cookie `Secure` + `HttpOnly` +
`SameSite=Strict` + `Path=/p/v1`, só o hash do segredo no banco. Sem IP, sem
user-agent, sem identificador de aparelho.

---

## 5. Estado de entrega e expiração

Domínio próprio, separado do clínico:

```
disponivel ─(operador abre o envio)─► enviado ─(paciente autentica)─► acessado
     └──────────────(admin revoga)──────────────► revogado
```

`created_at`, `sent_at`, `first_access_at`, `last_access_at`,
`last_download_at`, `revoked_at`, `download_count`.

Nenhum afirma que o paciente **leu**. Dizem: disponibilizado, enviado,
acessado, baixado.

**Expiração: 90 dias**, renováveis. Justificativa: resultado de exame é
consultado depois — consulta seguinte, perícia, convênio. Janela de 24–48h
transformaria toda consulta tardia em pedido de suporte; link de documento
médico eterno é passivo. Expirado, a página diz exatamente *"Este acesso
expirou. Entre em contato com a SoproLife para gerar um novo link."*

---

## 6. O que foi publicado no site (`main`)

Três arquivos, e o segundo é o achado desta seção.

**`resultados/index.html`** — a página do paciente. Lê o token do fragmento,
pede a data de nascimento, fala com **uma** origem. A CSP no `<head>` lista
essa origem e nenhuma outra, então "não há terceiro nesta página" é
verificado pelo navegador. Sem Analytics, sem pixel da Meta, sem tag
manager, sem fonte externa, sem CDN.

**`sw.js` — o service worker antigo ainda estava publicado.** A M25.29E
aposentou esse arquivo, mas na branch do painel; é `main` que o Pages serve.
O que estava no ar continuava sendo a versão com handler de `fetch` que
intercepta todo GET, guarda num cache de nome `sl-$V` (placeholder nunca
substituído — logo, nunca invalida) e em qualquer falha devolve o cacheado.

Nenhuma página o registra hoje, mas quem visitou o site enquanto o registro
existia **continua com ele instalado, escopo `/`**. Ele interceptaria
`/resultados/` e as respostas cross-origin da API do portal, colocando laudo
assinado e exame técnico no Cache Storage do aparelho. Um PDF médico num
cache que nunca invalida é exatamente o que esta etapa existe para evitar.
Foi substituído pela versão que só se desinstala.

**`robots.txt`** — `Disallow: /resultados/`. A URL também não entra no
sitemap.

---

## 7. WhatsApp

Botão abre `wa.me` com telefone e **mensagem pronta**; o operador aperta
ENVIAR. Sem telefone: "Telefone não cadastrado", e Copiar link + QR
continuam de pé.

A mensagem não carrega nada clínico — primeiro nome, o fato de existir um
resultado de espirometria, o link, a instrução do segundo fator.

Arquitetura pronta para a Cloud API: `patient_results.py` decide *o que*
enviar e *para qual acesso*; o canal é quem transporta. Nada foi contratado
nem configurado.

---

## 8. Os cinco defeitos que só a implantação mostrou

Registrados porque cada um custou um ciclo e nenhum apareceria em revisão de
código.

**1. `IdentifierError` na migração.** A convenção do projeto
(`fk_<tabela>_<coluna>_<tabela_referida>`) gerou
`fk_patient_result_accesses_report_document_version_id_report_document_versions`
— 78 caracteres, contra o limite de **63** do PostgreSQL. O SQLite da suíte
aceita qualquer tamanho. A migração roda em DDL transacional, então o
`upgrade` reverteu sozinho: banco intacto em `b1f4c72d9e08`, sem tabela
órfã. Corrigido com nomes curtos (`fk_pra_*`, `fk_prs_*`) idênticos no
modelo e na migração, mais
`test_todo_identificador_cabe_no_postgres`, que compila o `CREATE TABLE` das
48 tabelas com o dialeto do PostgreSQL.

**2. Raiz de PDFs errada.** O deploy escrevia
`/opt/soprolife/private/m15-reports`; a raiz real é
`/opt/soprolife/private/reports` (drop-in `reports-pilot.conf`, M24D). Com
`ProtectSystem=strict` o sintoma seria mudo: 503 no download de um PDF que
está no disco, íntegro, a um diretório de distância. O script passou a **ler**
`M15_REPORTS_STORAGE_DIR` do EnvironmentFile interno e a exigir que a unit
libere exatamente esse caminho.

**3. `nginx -t` recusava o vhost.** Bloco `listen 443 ssl` com certificado
comentado à espera do certbot → `no "ssl_certificate" is defined`. Impasse: o
certbot precisa de configuração válida. O arquivo passou a nascer no estado
pré-TLS honesto (porta 80, com todos os `location` e limites), e o
`certbot --nginx` promove esse mesmo bloco. Como o certbot vira dono do
arquivo instalado, a etapa `nginx` agora **recusa** sobrescrever um vhost que
já tenha `ssl_certificate` — reinstalar por cima derrubaria o HTTPS num
deploy de rotina.

**4. Health medido sem espera.** A etapa reiniciava `soprolife-m15-api` e
consultava o health na hora: `Failed to connect to 127.0.0.1 port 8015`. A
API estava subindo, e subiu. O portal tinha laço de tentativa; a API interna,
que carrega um mapa maior, não tinha.

**5. O portal respondia sobre laudos, e sem cabeçalhos.** `/api/v1/laudos`
devolvia `503 relatorios_desabilitados` — `install_error_handling`, compartilhado
com o Command Center, responde antes das rotas a esse prefixo. Conteúdo
inofensivo, forma péssima: um caminho que responde **diferente** conta a quem
varre que a máquina tem a ver com um sistema de laudos. E a resposta saía sem
`Cache-Control: no-store`, sem `X-Robots-Tag`, sem CSP — porque no Starlette o
middleware adicionado por **último** é o mais **externo**, e o meu estava sendo
adicionado primeiro. Cabeçalho de segurança que depende do caminho feliz não
é garantia, é coincidência. Agora a fronteira pública é a camada mais externa
e faz as duas coisas.

---

## 9. Prova de produção

### 9.1 Da médica ao paciente (fixture 100% sintética)

`test_prova_final_da_medica_ao_paciente`, saída real da suíte:

```
  PROVA FINAL M26.4
    ✓ 1-2 PDF assinado recebido e aceito pelas guardas M25.29H
    ✓ 3 acesso criado automaticamente, sem ação extra da médica
    ✓ 4-5 QR e mensagem de WhatsApp prontos, sem dado clínico
    ✓ 6-7 paciente abriu o link e confirmou a data de nascimento
    ✓ 8 dois documentos oferecidos, e só eles
    ✓ 9 SHA do laudo e do técnico conferem nos dois sentidos
    ✓ 10 painel mostra Acessado, com data/hora e 2 downloads
```

### 9.2 O site público

```
$ curl -o /dev/null -w '%{http_code}' https://soprolife.com.br/resultados/
200
$ curl -s https://soprolife.com.br/resultados/ | grep robots
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet, noimageindex">
$ curl -s https://soprolife.com.br/robots.txt | grep Disallow
Disallow: /resultados/
$ curl -s https://soprolife.com.br/sw.js | head -1
// M25.29E — service worker DESATIVADO de propósito.
```

### 9.3 A superfície pública, vista da internet

```
$ curl -H "Host: resultados-api.soprolife.com.br" http://187.127.39.5/p/v1/health
{"status":"ok","servico":"portal-resultados"}                            → 200

$ curl -H "Host: ..." http://187.127.39.5/api/v1/laudos                  → 404
$ /dev/tcp/187.127.39.5/8765   (painel)                          → fechado
$ /dev/tcp/187.127.39.5/8015   (API M15)                         → fechado
$ /dev/tcp/187.127.39.5/5432   (PostgreSQL)                      → fechado
```

### 9.4 Smoke do portal, sem paciente real

```
health loopback: {"status":"ok","servico":"portal-resultados"}
token inexistente (espera 401): 401
sem sessão (espera 401): 401
rota administrativa no portal (espera 404): 404
e sem falar em laudo (espera vazio): (vazio)
docs no portal (espera 404): 404
cache-control: no-store, private, max-age=0
x-frame-options: DENY
referrer-policy: no-referrer
x-robots-tag: noindex, nofollow, noarchive, nosnippet
content-security-policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'
```

### 9.5 O Command Center continua privado

```
painel 8765 (via Tailscale): 200
API interna: {"status":"ok","ambiente":"prod","banco":"ok"}
ufw: OpenSSH, 8765/tcp on tailscale0, 80/tcp, 443/tcp — e nada mais
nenhum vhost referencia 8015 nem 8765
8016 escuta apenas em 127.0.0.1
```

### 9.6 O papel de banco do portal, medido no PostgreSQL

Tudo o que ele alcança:

```
audit_logs               -> INSERT   (só; SELECT em 0 colunas)
patient_result_accesses  -> SELECT + UPDATE em 8 colunas de entrega
patient_result_sessions  -> SELECT, INSERT, UPDATE(revoked_at)
people                   -> SELECT em 4 colunas: id, nome_completo,
                                                 data_nascimento, arquivado
spirometry_exams         -> SELECT em 3 colunas: id, person_id, data_exame
report_document_versions -> SELECT em 8 colunas técnicas
```

Tudo o que ele **não** alcança (SELECT em 0 colunas):

```
financial_entries · users · partners · leads · followups ·
report_documents · external_signed_documents · person_contacts · audit_logs
```

E provado por execução, dentro do próprio deploy:

```
OK: o papel do portal NÃO consegue ler financial_entries.
OK: o papel do portal NÃO consegue ler people.cpf.
OK: o papel do portal lê patient_result_accesses.
```

Foi para caber neste GRANT que as leituras do portal viraram dataclasses
estreitas em vez de `db.get(Person, ...)` — carregar a entidade inteira
obrigaria a liberar a tabela inteira, CPF incluído.

### 9.7 Separação dos segredos, medida nos arquivos

```
/opt/soprolife/secrets/m15.env          M15_PORTAL_TOKEN_KEY      presente
                                        M15_PORTAL_SESSION_SECRET ausente
/opt/soprolife/secrets/m26-4-portal.env M15_PORTAL_SESSION_SECRET presente
                                        M15_PORTAL_TOKEN_KEY      AUSENTE
                                        -rw------- root root
```

O deploy falha fechado se a chave de derivação aparecer no arquivo público.

---

## 10. Testes

46 testes novos em `tests/test_m26_4_portal_resultados.py`, cobrindo os 25
pontos exigidos:

| # | exigência | teste |
|---|---|---|
| 1 | `recebido_assinado` cria acesso | `test_01_...` |
| 2 | recusado não cria | `test_02_...` |
| 3 | prévia não cria | `test_03_...` |
| 4 | PDF idêntico sem assinatura não cria | `test_04_...` |
| 5 | entropia do token (256 bits, Hamming ≈ metade) | `test_05_...` + `05b` (nada na auditoria) |
| 6 | token errado não revela paciente | `test_06_...` |
| 7 | nascimento errado não revela paciente | `test_07_...` (mensagem idêntica à do 6) |
| 8 | rate limiting | `test_08_...` (por acesso) + `08b` (por origem) |
| 9 | nascimento correto autentica | `test_09_...` |
| 10 | laudo retorna o SHA do assinado | `test_10_...` |
| 11 | técnico retorna o SHA do exame | `test_11_...` |
| 12 | paciente A nunca baixa B | `test_12_...` |
| 13 | dois simultâneos não cruzam | `test_13_...` |
| 14 | revogação invalida acesso e sessão | `test_14_...` |
| 15 | regeneração mata o token antigo | `test_15_...` |
| 16 | novo PDF reaponta o acesso | `test_16_...` + `16b` (revogado não ressuscita) |
| 17 | cookie administrativo não é atalho | `test_17_...` (nos dois sentidos) |
| 18 | admin continua Tailscale-only | `test_18_...`, `18b`, `18c` |
| 19 | QR só com a URL segura | `test_19_...` (leitor próprio) + `19b` (vs `qrencode`) |
| 20 | ausência de telefone não quebra | `test_20_...` |
| 21 | WhatsApp manual gera mensagem correta | `test_21_...` + `21b` |
| 22 | nada clínico na mensagem | `test_22_...` |
| 23 | rota pública não expõe outros endpoints | `test_18_e_23_...` |
| 24 | Cache-Control / noindex / cabeçalhos | `test_24_...`, `24b`, `24c` |
| 25 | mobile responsivo | `test_25_...` + `25b` |

Extras: expiração e a instrução exata, validade de 90 dias, portal desligado
não cria acesso nem responde, papel operacional obrigatório, histórico só
com clique explícito, sessão guarda hash e não segredo, fila não expõe o
link, fronteira pública com 404 idêntico em 10 caminhos, ordem dos
middlewares, identificadores no PostgreSQL, e a prova final de ponta a ponta.

Resultado final da suíte: **1602 passed, 30 skipped, 1 failed** em 9min28s.

A única falha é **pré-existente e sem relação com esta etapa**:
`test_m25_17_operacao_limpa.py::test_rubrica_real_nao_esta_versionada` —
falso positivo do filtro por nome de arquivo (screenshots sintéticos do selo
commitados na M25.21), já documentado na M26.1. Confirmada rodando no
checkout base, intocado.

O `quality-gate-safe.sh` também acusa uma falha pré-existente, igualmente
confirmada no checkout base: `test-m21-auth-crm-nav` → "scripts de Marketing
usam cache-buster da reconciliação".

Duas falhas que ESTA etapa causou foram corrigidas antes da entrega, e
valeram a pena por serem guardas reais do projeto: `.report-result-url`
usava `word-break: break-all`, proibido no painel inteiro desde a M25.21
porque verticaliza nome de paciente (trocado por `overflow-wrap: anywhere`,
que resolve a URL longa sem reintroduzir a regra); e o teste de cache
busting da M25.29E, que exige `?v=` novo sempre que
`report-workflow.{js,css}` mudam — atualizado para `2026090102`, que é
exatamente o que ele existe para forçar.

---

## 11. Sobre a Pastore, e sobre os laudos históricos

**Pastore:** independente. Um exame Pastore continua sendo lançado e fechado
normalmente e, ao mesmo tempo, o paciente recebe o acesso SoproLife. Uma
coisa não bloqueia a outra — não há um único ponto do código em que uma
consulte a outra.

**Históricos:** nada foi enviado a ninguém. As tabelas novas estão em zero
linhas. O botão *Gerar acesso ao resultado* aparece por laudo, e alguém
clica — um por vez, com o nome do paciente na tela. A automação vale daqui
para frente.

---

## 12. A ÚNICA ação que falta

O domínio é administrado no **Registro.br**, painel externo sem API
disponível nesta sessão.

**Passo 1 — criar o registro** (Registro.br → `soprolife.com.br` → DNS →
Editar zona):

```
Nome:  resultados-api
Tipo:  A
Dado:  187.127.39.5
TTL:   3600
```

`187.127.39.5` é o IPv4 público da VPS. Já verificado de fora: a porta 80
responde por ele, com o vhost correto.

**Passo 2 — emitir o certificado**, depois que
`getent hosts resultados-api.soprolife.com.br` resolver:

```bash
ssh root@100.87.98.100
cd /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15
sudo ./scripts/deploy-portal-resultados.sh tls
```

A etapa recusa rodar antes de o nome resolver, emite o certificado com
`certbot --nginx --redirect`, recarrega o nginx e confere
`https://resultados-api.soprolife.com.br/p/v1/health`.

Nada mais precisa ser feito: o serviço, o papel de banco, a migração, a unit,
o vhost, o ufw e a página pública já estão no lugar.

---

## 13. Estado final

| item | estado |
|---|---|
| `soprolife.com.br/resultados` | **no ar** (HTTP 200, `noindex`) |
| API pública `/p/v1` | **no ar** por IP; HTTPS pendente de DNS |
| Command Center | **privado**, Tailscale, portas internas fechadas |
| Migração `c3a9e15f7d84` | aplicada |
| Backup pré-migração | preservado |
| Serviços | `soprolife-m15-api`, `soprolife-portal-resultados`, `soprolife-painel-loopback`, `nginx`, `postgresql` — todos `active` |
| Cache busting | `report-workflow.{js,css}?v=2026090102` |
| Dados de paciente real | **nenhum** foi usado, lido ou enviado |
