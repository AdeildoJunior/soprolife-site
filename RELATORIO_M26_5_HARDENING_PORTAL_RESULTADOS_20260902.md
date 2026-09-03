# M26.5 — Endurecimento do Portal de Resultados

**Executado em 02–03/09/2026**, no PC de casa. Worktree novo e limpo, criado
a partir de `origin/painel-soprolife-v01` (`0ea720d`).

| | |
|---|---|
| Branch de trabalho | `claude-m26-5-hardening-portal` |
| Base | `0ea720d` (HEAD oficial após a M26.4) |
| Testes novos | 57 |
| Suíte | ver §7 |
| Escopo | pool do banco, borda nginx, escolha de IP público, documentação |
| Fora de escopo, e intocado | portal, fluxo clínico, Financeiro, `tailscale serve`, WhatsApp, qualquer paciente real |

---

## 1. O que a auditoria encontrou ANTES de escrever código

A M26.4 encerrou dizendo que faltava **uma** ação externa: o registro DNS no
Registro.br. A primeira coisa que esta etapa fez foi medir, e a medição
mudou o ponto de partida.

```
$ dig +short resultados-api.soprolife.com.br
187.127.39.5

$ dig +short -x 187.127.39.5
srv1791147.hstgr.cloud.

$ echo | openssl s_client -connect resultados-api.soprolife.com.br:443 \
        -servername resultados-api.soprolife.com.br 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
subject=CN=resultados-api.soprolife.com.br
issuer=C=US, O=Let's Encrypt, CN=YE2
notBefore=Sep  2 17:02:44 2026 GMT
notAfter=Dec  1 17:02:43 2026 GMT
```

**O DNS foi criado e o certificado foi emitido em 02/09/2026 17:02 UTC.** O
portal já estava em HTTPS quando esta etapa começou. Isso tem duas
consequências:

1. a documentação da M26.4 passou a estar errada em dois lugares (§6);
2. a borda pública ficou **mensurável de fora** pela primeira vez — e é de
   lá que vêm três dos cinco defeitos abaixo. Nenhum deles apareceria na
   suíte, e nenhum apareceria medindo `127.0.0.1:8016`, que é onde a M26.4
   mediu.

De passagem, a auditoria também confirmou que `187.127.39.5` **é** mesmo o
IPv4 público da VPS: o reverso resolve para `srv1791147.hstgr.cloud`, cujo
`A` volta ao mesmo endereço. O IP documentado estava certo; o que faltava
era a guarda que impede o próximo operador de escrever o errado (§4).

---

## 2. Os três defeitos medidos na internet

### 2.1 O 404 do catch-all saía praticamente pelado

```
$ curl -sI https://resultados-api.soprolife.com.br/qualquercoisa
HTTP/1.1 404 Not Found
Server: nginx
Content-Type: text/html
Content-Length: 146
Strict-Transport-Security: max-age=15552000
```

Um cabeçalho. Sem CSP, sem `X-Robots-Tag`, sem `Cache-Control: no-store`,
sem `X-Frame-Options`. Compare com a mesma máquina em `/p/v1/health`, que
sai com os nove — porque ali quem os coloca é a aplicação.

A M26.4 tratou este ponto **dentro** do processo Python (a fronteira pública
virou o middleware mais externo, e faz isso bem). O que ela não podia
alcançar é a resposta que o nginx inventa sozinho e que nunca chega ao
Python: o 404 do `location /`, o 429 do `limit_req`, o 413 do
`client_max_body_size`, o 502 quando o portal está fora do ar.

E a causa de o `add_header` do `server` não valer ali é uma regra do nginx
que é fácil de ler ao contrário:

> `add_header` declarado num `location` **substitui** todo o conjunto
> herdado do `server`. Não soma.

O vhost tinha um único `add_header` no `server` (o HSTS) e nenhum nos
`location`. Por isso o 404 herdava exatamente aquele um, e só.

### 2.2 O bloco do portal era o servidor padrão de 443

```
$ curl -skI -H 'Host: nao-existe.example' https://187.127.39.5/
HTTP/1.1 404 Not Found
Strict-Transport-Security: max-age=15552000
```

Um `Host` inventado, um SNI que não é o do portal — e o vhost do portal
respondeu assim mesmo. Não é acidente: quando um bloco é o único a escutar
uma porta, o nginx o elege servidor padrão dela. Qualquer nome que passe a
apontar para `187.127.39.5` — inclusive de terceiros, inclusive por engano
alheio — passa a ser atendido por este vhost.

Nada vaza por isso hoje (o `location /` devolve 404 e as rotas exigem token
e data de nascimento). Mas "o portal atende só o próprio nome" é uma
afirmação que se quer verdadeira por construção, não por coincidência de
configuração.

**E aqui a auditoria da própria M26.5 quase erra feio.** A correção óbvia
para "seja explícito sobre o servidor padrão" é emitir

```nginx
listen 443 ssl default_server;   # catch-all
listen 443 ssl;                  # o portal
```

Foi o que este renderizador fez na primeira versão. Antes de implantar, li o
vhost REAL da VPS — e ele não usa isso:

```nginx
listen [2a02:4780:6e:665::1]:443 ssl; # tailscaled ocupa :443 no tailnet
listen 187.127.39.5:443 ssl;          # tailscaled ocupa :443 no tailnet
```

O comentário estava certo, e é verificável:

```
$ ss -tlnp | grep :443
187.127.39.5:443           nginx
[2a02:4780:6e:665::1]:443  nginx
100.87.98.100:443          tailscaled     ← `tailscale serve`, o painel
[fd7a:115c:a1e0::…]:443    tailscaled
```

O `tailscale serve` publica o **painel privado** (`127.0.0.1:8765`) em HTTPS
no endereço do tailnet, e para isso o `tailscaled` escuta a 443 desse
endereço. `listen 443 ssl` faz o nginx tentar `0.0.0.0:443` e disputar a
porta: ou o nginx não sobe, ou o painel privado sai do ar.

O curinga teria derrubado uma das duas coisas no primeiro `systemctl reload
nginx` — e "não mexer no `tailscale serve`" era condição explícita desta
etapa.

Por isso a escuta na 443 é **relida do vhost instalado** e reemitida
verbatim: os endereços dependem do IP que a VPS tem, então não são dado de
repositório. E o renderizador **recusa** `443`, `*:443`, `0.0.0.0:443` e
`[::]:443` — na leitura e no render, com material TLS sem endereço falhando
fechado em vez de gerar um vhost que toma a porta do painel.

O catch-all de 443 usa os mesmos endereços, um `default_server` por
endereço. E a etapa `nginx` termina em `etapa_tailscale_intacto`, que
confere depois do reload que a 443 do tailnet ainda é do `tailscaled` e que
nenhum vhost abriu curinga: "não mexi no `tailscale serve`" vale mais medido
que declarado.

### 2.3 A porta 80 anunciava a versão do nginx

```
$ curl -sI -H 'Host: resultados-api.soprolife.com.br' http://187.127.39.5/x
HTTP/1.1 301 Moved Permanently
Server: nginx/1.24.0 (Ubuntu)
```

O vhost tem `server_tokens off` — e ele funciona: na 443 o cabeçalho é
`Server: nginx`, seco. O bloco que respondeu aqui é **outro**: o de
redirecionamento, gerado pelo `certbot --nginx --redirect`, que não herda
nada do nosso arquivo e não tem `server_tokens off`.

Versão exata de servidor web é o primeiro campo que um scanner preenche.

---

## 3. Os outros dois defeitos

### 3.1 O pool entregava conexão morta

`build_engine` criava o engine sem `pool_pre_ping`. O pool guarda conexões
ociosas e as devolve como se estivessem boas; mas uma conexão parada morre
por fora do processo — `idle_session_timeout` do PostgreSQL, um firewall com
estado que esquece o fluxo TCP, a máquina que suspendeu. A primeira
requisição depois do silêncio recebe `OperationalError`.

Para o Command Center isso seria um erro esporádico e visível a quem está
trabalhando. Para o **portal**, "a primeira requisição depois de horas de
silêncio" não é um caso raro: é o caso normal. É o paciente abrindo o link
do WhatsApp às três da manhã.

```python
kwargs: dict = {"future": True, "pool_pre_ping": True}
if url.startswith("sqlite"):
    kwargs["connect_args"] = {"check_same_thread": False}
else:
    kwargs["pool_recycle"] = POOL_RECYCLE_SEGUNDOS   # 1800
```

O `pool_recycle` só vale para banco de rede: arquivo SQLite local não morre
sozinho, e reciclar por idade ali seria cerimônia sem função.

### 3.2 O vhost tinha virado um arquivo que só o certbot sabia reproduzir

Depois do `certbot --nginx`, o arquivo instalado passava a conter os
caminhos do certificado, e a fonte versionada não. A M26.4 resolveu isso da
única maneira segura naquele momento: a etapa `nginx` **se recusava** a
sobrescrever um vhost que já tivesse `ssl_certificate`, porque reinstalar a
fonte por cima derrubaria o HTTPS.

O preço disso só aparece na etapa seguinte, que é esta: **mudar uma regra do
vhost deixou de ser possível pelo deploy.** As correções 2.1, 2.2 e 2.3 são
exatamente mudanças de regra do vhost.

---

## 4. O que foi feito

### 4.1 `nginx_portal_vhost.py` — o vhost volta a sair do Git

Um renderizador, com validação fail-closed, que:

- lê o `server { … }` da fonte versionada e o replica na porta certa (80
  antes do TLS, 443 depois), **nos endereços explícitos** relidos do vhost
  instalado — nunca em curinga (§2.2);
- **relê** os caminhos do certificado de onde já estiverem — do vhost
  instalado (é lá que o certbot os escreveu) ou de
  `/etc/letsencrypt/live/<nome>/`. O certificado nunca é inventado nem
  movido; é reencontrado. Reconstruir o vhost deixou de derrubar o HTTPS;
- emite o bloco de redirecionamento 80→443 **com** `server_tokens off`;
- emite os `default_server` explícitos: `return 444` na 80 e
  `ssl_reject_handshake on` na 443 — que recusa o SNI desconhecido antes de
  haver requisição, e sem precisar de certificado nenhum.

A leitura do certificado ignora comentários. É a armadilha exata da M26.4
(um `# ssl_certificate` comentado à espera do certbot), e há teste para ela.

A validação recusa o render — e nada é escrito — se o bloco do portal for
`default_server`, se algum `listen` de 443 for curinga, se algum bloco
escutar 443 sem certificado e sem `ssl_reject_handshake`, se faltar qualquer
um dos nove cabeçalhos, se faltar o catch-all `location / { return 404; }`,
ou se aparecer 8015, 8765 ou 5432.

O render é **ponto fixo**: reconstruir a partir do próprio render dá o
arquivo idêntico. Sem isso, cada deploy de rotina mexeria no vhost público.

### 4.2 Os cabeçalhos mudaram de lugar, não de valor

Os nove de `app/portal/security.py::CABECALHOS_SEGUROS` passaram para o
nível do `server` no vhost, e **nenhum `location` declara `add_header`** —
que é o que faz todos herdarem, o 404 do catch-all inclusive.

Como a aplicação continua emitindo os dela (ela precisa estar correta
sozinha, para quem chega por loopback), as duas rotas proxiadas ganharam
`proxy_hide_header` para cada um dos nove. Sai exatamente uma cópia de cada,
e ela é sempre a da borda.

Um teste compara a lista do nginx com o dicionário do Python, chave e valor.
Se alguém apertar a CSP num lado e esquecer o outro, a suíte falha — e o
motivo de esse teste existir é que a divergência seria invisível: o 404 do
catch-all continuaria saindo com a política antiga, porque ele nunca passa
pela aplicação.

### 4.3 `rede_publica.py` — o IP do tailnet nunca vai para o DNS

A instrução da M26.4 pedia "`<IPv4 público desta VPS>`". O candidato mais à
mão para responder isso é `hostname -I | head -n 1` ou `tailscale ip -4`, e
os dois entregam `100.87.98.100`. Publicar esse endereço num registro A
erra de duas maneiras ao mesmo tempo:

- **não funciona** — 100.64.0.0/10 é CGNAT, ninguém fora do tailnet roteia
  para lá, e o desafio HTTP-01 do Let's Encrypt não completaria;
- **conta o que não devia** — registro DNS é público, fica em cache e em log
  passivo, e passaria a anunciar para qualquer um o endereço com que o
  Command Center é administrado.

O seletor descarta por interface (`tailscale*`, `lo`, `docker*`, `veth*`,
`wg*`), descarta CGNAT com mensagem própria, descarta RFC 1918, loopback,
link-local e não-unicast — e **falha fechado** quando sobra mais de um
candidato público. Escolher entre dois endereços públicos é adivinhar, e a
etapa não adivinha: lista os dois e pede a decisão.

Ele entrou em três lugares:

- etapa `ip-publico`, que imprime o endereço;
- a mensagem final do `todas`, que agora imprime o IP em vez de um
  `<placeholder>` — e que, se o HTTPS já responde, diz "nada falta";
- a etapa `tls`, que **antes do certbot** verifica cada endereço IPv4 para o
  qual o nome resolve e recusa continuar se algum não for público. Se o
  registro A estiver com o IP do tailnet, o certbot falharia de qualquer
  jeito — mas o registro já teria publicado o endereço interno.

### 4.4 A etapa `nginx` ganhou rede de segurança

O arquivo só é trocado depois de o `nginx -t` aprovar a configuração inteira
com o candidato no lugar. Reprovou: o anterior volta do `.bak-<STAMP>`, o
`sites-enabled/default` volta de onde foi guardado, e o nginx **não** chega
a ser recarregado. Depois do reload, três verificações no `nginx -T`
efetivo: nenhum vhost cita 8015/8765, existe `default_server` explícito na
80, e havendo TLS existe `ssl_reject_handshake` na 443.

A etapa `tls` passou a chamar a `nginx` **depois** do certbot, para devolver
o arquivo à forma versionada com os caminhos que ele acabou de escrever.

### 4.5 Etapa `borda` — o smoke que faltava

A M26.4 mediu cabeçalhos em `127.0.0.1:8016`, e por isso não viu 2.1. A
etapa nova mede pela internet: pede três caminhos inexistentes, exige os dez
cabeçalhos em cada 404, exige exatamente **uma** cópia de cada na resposta
do portal, e falha se a borda anunciar a versão do nginx.

---

### 4.6 Um FATAL que o próprio deploy teria disparado

O renderizador copiava para o destino o cabeçalho em prosa da fonte. Esse
cabeçalho cita as portas 8015 e 8765 — justamente para dizer que elas **não**
são publicadas. Só que a etapa `nginx` termina com uma guarda literal, herdada
da M26.4:

```bash
if grep -rn "8015\|8765" /etc/nginx/sites-enabled/; then
  fail "FATAL: algum vhost referencia porta interna do Command Center"
fi
```

Um comentário explicando que a porta não é publicada teria abortado o deploy
alegando que ela é. O render passa a levar do prefixo só as diretivas (as
duas zonas de limite), e a validação do renderizador procura as portas no
texto **cru**, comentários inclusive — reproduzindo a guarda do script em vez
de ser mais permissiva que ela. Achado antes de subir, com teste.

## 5. Uma armadilha de `bash` corrigida no caminho

O script roda com `set -Eeuo pipefail`. Nele, `[[ -f "$x" ]] && cmd` como
comando de cauda **encerra o script** quando o teste é falso — o status da
lista é 1 e o `set -e` age. O código novo nasceu com três dessas; viraram
`if`. Fora do caminho feliz, um deploy que "termina com sucesso" sem ter
feito a metade do trabalho é pior que um que falha.

---

## 6. A documentação da M26.4, corrigida

Três correções, e a segunda é a que mais importava:

1. **`docs/m26-4-portal-resultados-paciente.md` §11** dizia que o DNS
   precisava ser criado e o TLS emitido. Os dois já estão feitos desde
   02/09/2026 — a seção passou a registrar isso, com data, e a listar as
   oito etapas do script (um teste falha se o script ganhar etapa que a
   documentação não menciona).
2. **`deploy-portal-resultados.sh`** imprimia `Dado: <IPv4 público desta
   VPS>` na instrução do registro A. Um placeholder num passo que um humano
   executa uma única vez, sob pressão, é onde o IP do tailnet entra. Agora
   imprime o endereço, derivado pela regra do §4.3 — e há teste proibindo o
   placeholder de voltar.
3. **`RELATORIO_M26_4_...md`** ganhou nota no topo e na §12 registrando que
   a "única ação que falta" foi cumprida, com a data e o horário do
   certificado. As medições históricas ficam como estavam: elas eram
   verdadeiras quando foram feitas.

Uma quarta correção é sobre número, e está na seção seguinte.

---

## 7. Testes

**57 testes novos** em `tests/test_m26_5_hardening_portal.py`:

| grupo | o que prova |
|---|---|
| pool (5) | pre-ping ligado no SQLite e no PostgreSQL, `pool_recycle` só em banco de rede, conexão stale substituída sem erro — **e a contraprova de que a mesma manobra quebra um engine sem pre-ping** |
| vhost (20) | render pré-TLS sem 443, render com TLS preservando os quatro caminhos do certbot, ponto fixo, certificado comentado não conta como TLS, portal nunca é `default_server`, **endereços da 443 relidos verbatim e curinga recusado nas três formas**, catch-all nas duas portas, redirecionamento com `server_tokens off`, nenhuma porta interna **nem em comentário**, zonas de limite preservadas, e seis casos em que a validação **recusa** |
| cabeçalhos (4) | nginx e aplicação não divergem (chave e valor), nenhum `location` declara `add_header`, as duas rotas proxiadas descartam as nove cópias, o catch-all é 404 seco |
| IP público (10) | o tailnet é recusado por faixa e por interface, seis endereços não-públicos, o IP real da VPS passa, escolha entre candidatos, falha fechada com zero e com dois, leitura da saída real do `ip -4 -o addr`, e os dois modos do CLI |
| deploy (11) | `bash -n`, ausência de `hostname -I` e de `tailscale ip` fora da guarda, DNS verificado antes do certbot, `nginx -t` antes do reload, restauração do vhost na reprovação, reconstrução depois do certbot, guarda do `tailscale serve` depois do reload, ausência de `&&` de cauda sob `set -e`, e toda etapa documentada |

As verificações do script `bash` são estáticas (ordem das operações, presença
do caminho de rollback). Executá-lo de verdade exige `root`, `nginx`,
`certbot`, `systemd` e PostgreSQL; a parte com lógica de decisão foi tirada
do `bash` e posta em Python justamente para poder ser executada nos testes.
O comportamento do script em si foi medido no deploy (§8).

### Números exatos da suíte

Medidos, e não estimados:

| | testes | passed | skipped | failed |
|---|---|---|---|---|
| base `0ea720d`, intocada | 1590 | 1559 | 30 | 1 |
| com esta etapa | 1647 | 1616 | 30 | 1 |
| diferença | **+57** | **+57** | 0 | 0 |

As duas linhas foram medidas com execuções completas e separadas
(`pytest tests/ -q -p no:randomly`), a da base no checkout principal limpo
em `0ea720d`.

A única falha é **pré-existente e sem relação com esta etapa**:
`test_m25_17_operacao_limpa.py::test_rubrica_real_nao_esta_versionada` —
falso positivo do filtro por nome de arquivo (screenshots sintéticos do selo
commitados na M25.21), já documentado desde a M26.1. Reproduzida no checkout
base, intocado.

O `quality-gate-safe.sh` acusa uma falha, também pré-existente e também
confirmada na base: `test-m21-auth-crm-nav` ("scripts de Marketing usam
cache-buster da reconciliação").

> **Correção de número.** O relatório da M26.4 registrou "1602 passed, 30
> skipped, 1 failed". A suíte da base tem **1590 testes coletados** no
> total, então 1602 aprovados não cabem nela. O número certo da base é
> 1559 passed / 30 skipped / 1 failed. Nada muda de conclusão — a falha
> pré-existente é a mesma —, mas um relatório que dá o tamanho da suíte
> deve dar o tamanho da suíte.

---

## 8. Deploy

<!-- PREENCHIDO NA EXECUÇÃO -->

---

## 9. O que esta etapa não fez

- **não reimplementou o portal.** Nenhuma rota, nenhum contrato, nenhum
  cookie, nenhum token, nenhuma regra de expiração ou de bloqueio mudou;
- **não gerou acesso para paciente nenhum.** As tabelas do portal não foram
  lidas nem escritas por esta etapa;
- **não enviou WhatsApp**, nem mudou o construtor de mensagem;
- **não tocou no Financeiro** — nenhum arquivo de `app/routers/finance.py`,
  `pastore.py` ou lançamento;
- **não alterou o fluxo clínico** — `reports.py`, as guardas documentais da
  M25.29H e o gatilho de `recebido_assinado` estão idênticos;
- **não mexeu no `tailscale serve`** — e isto é medido, não afirmado: a
  etapa `nginx` termina conferindo que `100.87.98.100:443` continua do
  `tailscaled` e que nenhum vhost abriu curinga na 443. Nem a unit do
  painel, nem o ufw além das regras 80/443 que a M26.4 já criava.

O diff é: `app/db.py` (um dicionário de kwargs), o vhost, o script de
deploy, dois scripts novos, um arquivo de teste novo e três documentos.
