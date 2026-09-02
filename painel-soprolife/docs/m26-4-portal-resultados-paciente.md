# M26.4 — Portal de Resultados do Paciente

Como o paciente da SoproLife recebe o próprio laudo, e por que a operação
não ganhou um passo a mais para isso acontecer.

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
| papel de banco | `soprolife_m15` (dono) | `soprolife_portal` (GRANT por coluna) |
| cookie | `soprolife_m15_sessao` | `soprolife_resultado` |
| segredo do cookie | `M15_AUTH_SECRET` | `M15_PORTAL_SESSION_SECRET` |
| deriva links? | sim (`M15_PORTAL_TOKEN_KEY`) | **não tem a chave** |

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

```bash
# na VPS, como root
cd /opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15
sudo ./scripts/deploy-portal-resultados.sh todas   # tudo, menos TLS
# ... criar o DNS ...
sudo ./scripts/deploy-portal-resultados.sh tls
```

O registro DNS necessário (painel do Registro.br):

```
Nome:  resultados-api      Tipo: A      Dado: <IPv4 público da VPS>     TTL: 3600
```

## 12. Onde olhar quando algo der errado

| sintoma | primeiro lugar |
|---|---|
| link do paciente dá "acesso expirado" | `patient_result_accesses.revoked_at` / `expires_at` |
| link do paciente dá "não foi possível abrir" | `generation` mudou? alguém regenerou? |
| a fila diz "Resultado online: não gerado" | `journalctl -u soprolife-m15-api \| grep resultado_acesso_falhou_no_gatilho` |
| o portal não responde | `systemctl status soprolife-portal-resultados` e `curl 127.0.0.1:8016/p/v1/health` |
| o QR não abre no celular | o QR aponta para a PÁGINA; confira se o DNS/TLS do subdomínio está de pé |
