# M26.4 — Portal de Resultados do Paciente

Como o paciente da SoproLife recebe o próprio laudo, e por que a operação
não ganhou um passo a mais para isso acontecer.

> **Atualizado na M26.5 (02/09/2026).** Duas coisas que este documento
> descrevia como pendentes já aconteceram: o registro DNS
> `resultados-api.soprolife.com.br → 187.127.39.5` foi criado no
> Registro.br e o certificado Let's Encrypt foi emitido em 02/09/2026
> 17:02 UTC. O portal está em **HTTPS**. As seções 11 a 13 refletem o
> estado atual e o que a M26.5 mudou na borda.

---

## 1. O desenho em uma tela

```
                  INTERNET                          │        TAILSCALE
                                                    │
  paciente                                          │   Dra. Ana / operação
     │                                              │          │
     │ 1. abre o link do WhatsApp                   │          │
     ▼                                              │          ▼
  soprolife.com.br/resultados/#t=<token>            │   Command Center
  (GitHub Pages — HTML estático, sem backend)       │   127.0.0.1:8765
     │                                              │          │
     │ 2. POST {token, nascimento}                  │          ▼
     ▼                                              │   API M15 (privada)
  resultados-api.soprolife.com.br                   │   127.0.0.1:8015
  nginx  ──►  127.0.0.1:8016                        │          │
              soprolife-portal-resultados           │          │ gatilho
              ├── 6 rotas, nenhuma administrativa   │          │
              ├── papel de banco por COLUNA         ◄──────────┘
              └── sem a chave que deriva links      │
                                                    │
```

A linha vertical é a fronteira, e ela é feita de coisas verificáveis:

| | Command Center | Portal público |
|---|---|---|
| processo | `soprolife-m15-api` | `soprolife-portal-resultados` |
| porta | `127.0.0.1:8015` | `127.0.0.1:8016` |
| exposição | só Tailscale | nginx → internet |
| rotas | `/api/v1/**` (153) | `/p/v1/**` (6) |
| fora do prefixo | — | 404 idêntico, com cabeçalhos |
| papel de banco | `soprolife_m15` (dono) | `soprolife_portal` (GRANT por coluna) |
| cookie | `soprolife_m15_sessao` | `soprolife_resultado` |
| segredo do cookie | `M15_AUTH_SECRET` | `M15_PORTAL_SESSION_SECRET` |
| deriva links? | sim (`M15_PORTAL_TOKEN_KEY`) | **não tem a chave** |
| servidor padrão do nginx | — | **não** — há `default_server` próprio (M26.5) |

## 2. O gatilho

O acesso nasce em exatamente dois pontos, que são os dois lugares onde
`ExternalSignedDocument.status` vira `recebido_assinado`:

- `POST /api/v1/laudos/assinatura-externa/enviar` — o caminho normal, em que
  a médica devolve o PDF assinado e as guardas documentais da M25.29H
  decidem sobre os bytes;
- `POST /api/v1/laudos/assinatura-externa/confirmar` — compatibilidade com
  lotes abertos antes da M25.29H, que reaplica as mesmas guardas.

Nos dois, a chamada é a mesma:
`_abrir_resultado_para_o_paciente()` → `patient_results.ensure_access()`.

**A Dra. Ana não ganhou nenhum passo.** O fluxo clínico dela é idêntico ao
de antes: laudar, concluir, baixar, assinar por fora, devolver.

O gatilho é isolado num `try/except` que apenas registra a falha. É
deliberado: uma exceção na ENTREGA não pode derrubar o recebimento de um PDF
assinado que já passou nas guardas e já foi gravado. No pior caso o acesso
não nasce agora, a fila mostra "Resultado online: não gerado" e o
administrador cria com um clique.

Nunca nasce acesso para prévia, PDF sem estrutura de assinatura, arquivo
idêntico ao final, documento recusado ou `recebido_validacao_pendente`
legado — e a lista de status entregáveis vive no serviço, não no chamador.

## 3. O token

```
token = base64url( HMAC-SHA256( M15_PORTAL_TOKEN_KEY, "<access_id>:<geração>" ) )
```

- 32 bytes = **256 bits**, não sequencial, não derivável de nada público;
- o banco guarda **só** `sha256(token)`;
- o **processo público** compara hashes — não tem a chave e não consegue
  reconstruir link nenhum, nem lendo a tabela inteira;
- o **painel privado** re-deriva o mesmo link quando o operador pede, o que
  resolve o reenvio sem guardar o segredo em lugar nenhum;
- **regenerar** é `generation += 1`: o hash muda, o link que estava no
  WhatsApp morre no mesmo instante, e a linha inteira permanece para
  auditoria.

O token viaja no **fragmento** da URL (`#t=`). O navegador não envia
fragmento ao servidor: ele não aparece em log de acesso, nem no cabeçalho
`Referer`, nem em proxy. Também não aparece em `audit_logs` (a allowlist de
`app/audit.py` não tem chave para ele) nem no console.

## 4. Os dois fatores

1. **o link** (token de 256 bits);
2. **a data de nascimento**, comparada sem margem. Cadastro sem data
   cadastrada nunca autentica.

Falhou qualquer um dos dois — inclusive token inexistente — a resposta é a
**mesma**: HTTP 401, código `acesso_invalido`, mesma mensagem. Não há
oráculo para descobrir se um paciente existe.

Freios, em duas camadas:

| camada | onde vive | limite |
|---|---|---|
| por acesso (data errada) | coluna `failed_attempts` / `locked_until` | 5 tentativas → 15 min de cooldown |
| por origem (token chutado) | memória do processo, `sha256(ip)[:16]` | 20 falhas / 5 min |
| na borda | `limit_req` do nginx | 30 req/min, burst 10 |

O contador por acesso vive no **banco** de propósito: reiniciar o processo
público não pode ser o jeito de zerar um bloqueio.

Autenticado, nasce uma sessão de **30 minutos**: cookie `Secure`,
`HttpOnly`, `SameSite=Strict`, `Path=/p/v1`, com só o hash do segredo no
banco. Nada de IP, user-agent ou identificador de aparelho — o portal não
precisa deles, e o que não é guardado não vaza.

## 5. Os dois documentos

| botão | de onde saem os bytes |
|---|---|
| BAIXAR LAUDO ASSINADO | `PatientResultAccess.report_document_version_id`, que é uma cópia de `ExternalSignedDocument.report_document_version_id` |
| BAIXAR EXAME TÉCNICO | versão `kind = "original"` do **mesmo** `ReportDocument` |

Nunca `source_version_id` (o PDF que foi assinar), nunca
`current_version_id` genérico, nunca "o último PDF". A releitura passa por
`read_and_validate_stored_pdf`, que confere tamanho, páginas e sha256 contra
os metadados: arquivo trocado no disco vira erro, não download silencioso do
conteúdo errado.

## 6. Estado de entrega

Domínio próprio (`patient_result_accesses`), separado do estado clínico:

```
disponivel ──(operador abre o envio)──► enviado ──(paciente autentica)──► acessado
     └──────────────────(admin revoga)──────────────────► revogado
```

Timestamps: `created_at`, `sent_at`, `first_access_at`, `last_access_at`,
`last_download_at`, `revoked_at` e `download_count`.

Nenhum deles afirma que o paciente **leu** o laudo. Dizem que foi
disponibilizado, que o operador abriu o envio, que a página foi autenticada
e que um PDF saiu.

## 7. Expiração — 90 dias

Resultado de exame é consultado depois: na consulta seguinte, na perícia, no
convênio. Uma janela de 24 ou 48 horas transformaria cada consulta tardia
num pedido de suporte. Por outro lado, link de documento médico vivo para
sempre é passivo.

Noventa dias, renováveis pelo administrador ("Gerar novo acesso"). Depois
disso a página diz exatamente: *"Este acesso expirou. Entre em contato com a
SoproLife para gerar um novo link."*

## 8. WhatsApp — V1 manual, e por quê

A auditoria desta etapa procurou integração oficial Meta WhatsApp Business /
Cloud API no projeto e nas configurações. **Não existe.** Só o construtor de
URL `wa.me` que CRM e follow-up já usam, com a regra do README: "monta URL
para revisão humana; nunca dispara envio automático".

Então a V1 é a honesta: o botão abre o WhatsApp com o telefone do paciente e
a mensagem **pronta**; o operador aperta ENVIAR. Nada de bot, WhatsApp Web
automatizado, Selenium ou serviço não oficial — automação não oficial é
derrubada pela Meta e não deixa trilha que alguém consiga explicar.

Sem telefone cadastrado, a tela diz "Telefone não cadastrado" e **mantém**
Copiar link e QR Code.

A mensagem não carrega nada clínico: primeiro nome, o fato de existir um
resultado de espirometria, o link e a instrução do segundo fator.

**Quando a Cloud API entrar**, ela vira outro CANAL do mesmo serviço:
`patient_results.py` continua decidindo *o que* enviar e *para qual acesso*;
só muda quem transporta. Nem o portal nem o gatilho mudam de forma.

## 9. Operação diária

Na fila de laudos, depois de `recebido_assinado`:

```
Resultado online: Disponível
[Link, QR e WhatsApp]  [Baixar exame técnico]  [Baixar laudo assinado]
```

Clicando em **Link, QR e WhatsApp** abre o painel com o QR Code, o link e os
botões *Enviar pelo WhatsApp* / *Copiar link* / *Gerar novo acesso* /
*Revogar acesso*.

O link e o QR **não** vêm na listagem da fila — só nessa chamada por laudo,
que é auditada (`resultado_acesso_link_exibido`). Um token por linha ficaria
na memória do navegador de todo mundo que abre a tela.

Depois: `Enviado: dd/mm/aaaa hh:mm` e, no primeiro acesso,
`Acessado: dd/mm/aaaa hh:mm`.

**Laudos históricos**: nada é enviado sozinho. O botão *Gerar acesso ao
resultado* aparece por laudo, e alguém clica — um por vez, com o nome do
paciente na tela.

**Pastore**: independente. Um exame Pastore continua sendo lançado e fechado
normalmente e, ao mesmo tempo, o paciente recebe o acesso SoproLife. Uma
coisa não bloqueia a outra.

## 10. Privacidade

- sem Google Analytics, pixel da Meta, tag manager, fonte externa ou CDN na
  página de resultados — e a CSP da página lista **uma** origem de rede;
- `noindex, nofollow, noarchive, nosnippet` na meta da página **e** como
  cabeçalho `X-Robots-Tag` da API (que vale também para os PDFs, onde meta
  tag não existiria);
- `Cache-Control: no-store, private` em toda resposta autenticada e em todo
  PDF; `Referrer-Policy: no-referrer`; `X-Frame-Options: DENY`;
  `X-Content-Type-Options: nosniff`; CSP `default-src 'none'`;
- `Disallow: /resultados/` no robots.txt, e a URL fora do sitemap;
- a auditoria registra o **evento** (`resultado_portal_autenticado`,
  `resultado_portal_documento_baixado`), nunca o token, nunca o nome.

## 11. Instalação

O script tem oito etapas, e cada uma roda sozinha:

| etapa | o que faz |
|---|---|
| `segredos` | gera os dois segredos e escreve os dois EnvironmentFiles |
| `banco` | backup, `alembic upgrade head` e o papel restrito `soprolife_portal` |
| `servico` | instala a unit, sobe o portal e espera o health dos dois processos |
| `ip-publico` | imprime o IPv4 público desta VPS — o dado do registro A |
| `nginx` | **reconstrói** o vhost a partir da fonte versionada e recarrega |
| `tls` | confere o DNS, emite o certificado e reconstrói o vhost |
| `verificar` | smoke em loopback, sem paciente real |
| `borda` | smoke pela internet: cabeçalhos, 404 do catch-all, versão do nginx |
| `tailscale` | confere que a 443 do tailnet continua do `tailscaled` |

```bash
# na VPS, como root
cd /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15
sudo ./scripts/deploy-portal-resultados.sh todas   # tudo, menos TLS
sudo ./scripts/deploy-portal-resultados.sh tls     # depois que o DNS resolver
```

**O registro DNS** (painel do Registro.br — o domínio é administrado lá, e
não há API disponível) já existe desde 02/09/2026:

```
Nome:  resultados-api      Tipo: A      Dado: 187.127.39.5      TTL: 3600
```

O endereço **não** se descobre com `hostname -I | head -n 1` nem com
`tailscale ip -4`: os dois entregam `100.87.98.100`, que é o endereço CGNAT
do tailnet — não roteia da internet e, pior, publicaria num registro DNS
permanente o endereço com que o Command Center é administrado. Quem responde
é:

```bash
sudo ./scripts/deploy-portal-resultados.sh ip-publico
```

A etapa `tls` também recusa emitir certificado se o nome resolver para um
endereço que não seja público.

## 12. O que a M26.5 mudou na borda

Cinco correções, todas medidas em produção antes de existirem:

1. **`pool_pre_ping`.** O portal passa a madrugada ocioso; o PostgreSQL
   fecha sessão parada e o firewall esquece o fluxo. Sem o pre-ping, a
   primeira requisição depois do silêncio — que é justamente a do paciente
   abrindo o link — recebia erro de conexão. Agora o pool testa a conexão
   antes de entregá-la e reconecta sozinho.
2. **O vhost voltou a ser reconstruível.** Depois do `certbot --nginx` o
   arquivo instalado divergia da fonte e o deploy se recusava a tocar nele,
   para não apagar os caminhos do certificado. Isso congelava o vhost:
   mudar uma regra virava trabalho manual na VPS.
   `scripts/nginx_portal_vhost.py` monta o arquivo a partir da fonte
   versionada **relendo** os caminhos do certificado de onde já estiverem,
   e a troca só se efetiva depois de o `nginx -t` aprovar — reprovou, o
   arquivo anterior volta e o nginx nem chega a ser recarregado.
3. **O portal deixou de ser o servidor padrão.** Sendo o único bloco a
   escutar 443, ele era eleito `default_server` pelo nginx e respondia a
   qualquer SNI apontado para o IP. Agora há um `default_server` explícito
   com `ssl_reject_handshake on` na 443 e `return 444` na 80.

   **E a 443 nunca sai em curinga.** Nesta VPS o `tailscaled` escuta a 443
   do endereço do tailnet, servindo o painel privado por `tailscale serve`:

   ```
   187.127.39.5:443           nginx
   [2a02:4780:6e:665::1]:443  nginx
   100.87.98.100:443          tailscaled   ← o painel privado
   ```

   Um `listen 443 ssl` faria o nginx tentar `0.0.0.0:443` e disputar a porta
   com o `tailscaled` — ou o nginx não sobe, ou o painel sai do ar. Por isso
   os endereços da escuta são **relidos do vhost instalado** (são dado de
   máquina, não de repositório) e o renderizador **recusa** emitir curinga.
   A etapa `nginx` termina conferindo que a 443 do tailnet continua do
   `tailscaled` e que nenhum vhost abriu curinga.
4. **Cabeçalhos no 404 do catch-all.** Medido em produção, o 404 saía com
   `Strict-Transport-Security` e mais nada — sem CSP, sem `X-Robots-Tag`,
   sem `Cache-Control`. A causa é uma regra do nginx: `add_header` dentro
   de um `location` **substitui** o conjunto do `server` em vez de somar.
   Os cabeçalhos passaram para o nível do `server`, nenhum `location`
   declara os seus, e as rotas proxiadas descartam a cópia da aplicação com
   `proxy_hide_header` — sai exatamente uma de cada. Um teste compara a
   lista do nginx com `app/portal/security.py::CABECALHOS_SEGUROS` e falha
   se as duas divergirem.
5. **A versão do nginx parou de vazar.** O bloco de redirecionamento gerado
   pelo certbot não tem `server_tokens off`, e `curl -I http://<ip>/`
   devolvia `Server: nginx/1.24.0 (Ubuntu)`. O bloco emitido pelo
   renderizador tem.

## 13. Onde olhar quando algo der errado

| sintoma | primeiro lugar |
|---|---|
| link do paciente dá "acesso expirado" | `patient_result_accesses.revoked_at` / `expires_at` |
| link do paciente dá "não foi possível abrir" | `generation` mudou? alguém regenerou? |
| a fila diz "Resultado online: não gerado" | `journalctl -u soprolife-m15-api \| grep resultado_acesso_falhou_no_gatilho` |
| o portal não responde | `systemctl status soprolife-portal-resultados` e `curl 127.0.0.1:8016/p/v1/health` |
| o QR não abre no celular | o QR aponta para a PÁGINA; confira se o DNS/TLS do subdomínio está de pé |
| erro de conexão com o banco só na primeira requisição do dia | era o sintoma do pool sem pre-ping; confira `app/db.py::build_engine` |
| o vhost não bate com o Git | `deploy-portal-resultados.sh nginx` reconstrói; o arquivo anterior fica em `.bak-<STAMP>` |
| um nome de terceiro responde neste IP | `nginx -T \| grep default_server` — o catch-all da M26.5 sumiu? |
| o painel por `tailscale serve` parou depois de mexer no nginx | `ss -tlnp \| grep 100.87.98.100:443` — algum vhost abriu curinga na 443 |
