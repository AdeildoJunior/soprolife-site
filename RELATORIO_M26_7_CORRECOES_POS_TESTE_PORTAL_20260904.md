# M26.7 — as duas correções que o teste real do portal cobrou

**Data:** 04/09/2026
**Branch:** `claude-m26-7-correcoes-pos-teste-portal` (worktree limpo a partir de `origin/painel-soprolife-v01`, base `89a7a36`)
**Origem:** os dois defeitos encontrados pelo teste humano controlado da M26.6

---

## O que aconteceu, em uma frase

Um paciente sintético abriu o link em produção, digitou a data de nascimento
correta e a tela respondeu **"Não foi possível conectar agora. Verifique sua
internet"**. A internet estava boa. O servidor tinha respondido 500, e o
paciente não tinha como saber disso — nem nós, olhando só a tela.

Dois defeitos independentes se somaram: um quebrava o portal para **todo**
paciente, e o outro **escondia** o primeiro atrás de um diagnóstico falso.

---

## Defeito 1 — `GRANT INSERT` não basta para `INSERT ... RETURNING`

### O sintoma no journal

```
psycopg.errors.InsufficientPrivilege: permission denied for table audit_logs
[SQL: INSERT INTO audit_logs (ts_utc, request_id, user_id, acao, entidade,
                              entidade_id, detalhes)
      VALUES (...) RETURNING audit_logs.id]
acao: 'resultado_portal_autenticado'
```

### A causa

O papel `soprolife_portal` tinha exatamente o que a M26.4 achou suficiente:

```sql
GRANT INSERT ON audit_logs TO soprolife_portal;
GRANT USAGE ON SEQUENCE audit_logs_id_seq TO soprolife_portal;
```

O que faltava não é óbvio e é a regra do PostgreSQL: **`RETURNING` é uma
leitura.** Ele exige `SELECT` na coluna devolvida, mesmo quando a linha lida
é a que você acabou de escrever. E o SQLAlchemy emite `RETURNING id` em toda
inserção de chave autoincremental — não há como pedir que não emita.

Resultado: o único caminho do portal que grava auditoria — a autenticação do
paciente — era o único que não funcionava. O smoke da M26.4
(`deploy-portal-resultados.sh verificar|borda`) exercitava 401, 404 e
cabeçalhos: caminhos que não auditam nada. O portal subiu "verde" e quebrado.

### A correção

`scripts/sql/m26-4-portal-db-role.sql`:

```sql
GRANT INSERT (ts_utc, request_id, user_id, acao, entidade, entidade_id,
              detalhes)
  ON audit_logs TO soprolife_portal;
GRANT SELECT (id) ON audit_logs TO soprolife_portal;
GRANT USAGE ON SEQUENCE audit_logs_id_seq TO soprolife_portal;
```

Duas decisões, ambas na direção de MENOS privilégio, não mais:

- **`SELECT` é só de `id`.** A correção preguiçosa seria
  `GRANT SELECT ON audit_logs`, que também faria o `RETURNING` passar — e de
  quebra entregaria a um processo exposto na internet a trilha inteira do
  sistema: quem fez o quê, quando, em qual entidade. O `id` é um contador e
  não conta nada. `acao`, `entidade`, `entidade_id`, `user_id`, `detalhes` e
  `ts_utc` seguem **ilegíveis** para o portal.
- **O `INSERT` passou a ser por COLUNA.** Antes era da tabela inteira, o que
  inclui `id`. São as sete colunas que o ORM escreve, e nenhuma a mais:
  ninguém deve poder escolher o número da própria linha de auditoria.

### Fonte única

Auditei o repositório inteiro: **um único arquivo** concede privilégio a
`soprolife_portal` (o SQL acima), e **um único script** o aplica
(`deploy-portal-resultados.sh`, etapa `banco`, idempotente). Não há
bootstrap, restore ou migration alternativa criando o papel. Existe um teste
que varre a árvore e falha se aparecer uma segunda fonte.

---

## Defeito 2 — o 500 saía sem CORS, e o navegador chamava isso de queda de rede

### A causa

No Starlette o `ServerErrorMiddleware` é **sempre** a camada mais externa —
acima de qualquer middleware da aplicação, CORS incluído. Uma exceção não
tratada produzia a resposta lá em cima, sem `Access-Control-Allow-Origin` e
sem os cabeçalhos de segurança da M26.4/M26.5.

Para o navegador, uma resposta cross-origin sem `Access-Control-Allow-Origin`
**não é** um erro HTTP: o `fetch` rejeita com `TypeError`, exatamente como
faria se o cabo estivesse desconectado. O `.catch` do frontend disparava e
dizia a única coisa que sabia dizer: verifique sua internet.

Havia um irmão mais silencioso do mesmo defeito: o 404 do catch-all — o que
responde a tudo fora de `/p/v1/` — também nascia acima do `CORSMiddleware`,
que no portal é o middleware mais **interno**.

### A correção

`app/portal/main.py` e `app/portal/security.py`:

1. **A fronteira pública passou a capturar a exceção.** Ela é a camada mais
   externa da aplicação; morrendo ali, o 500 é montado por nós, com envelope
   próprio, cabeçalhos de segurança e CORS.
2. **`aplicar_cors(request, response)`** põe `Access-Control-Allow-Origin`
   em resposta que não passou pelo `CORSMiddleware`. Comparação **exata**
   contra `M15_PORTAL_CORS_ORIGINS` — sem curinga, sem prefixo, sem reflexão
   de `Origin` arbitrário. `https://soprolife.com.br.atacante.tld`,
   `http://soprolife.com.br` e `https://soprolife.com.br:8443` são origens
   diferentes e nenhuma passa. `Vary: Origin` sempre; cabeçalho de CORS só
   para origem autorizada.
3. **O traceback continua indo para o journal.** Engolir a exceção sem
   registrar trocaria um diagnóstico ruim por nenhum — foi exatamente o
   traceback do journal que permitiu achar o defeito 1. Há teste para isso.
4. **Handler de `Exception` próprio do portal**, sobrepondo o do Command
   Center, cuja mensagem ("consulte os logs pelo request_id") é escrita para
   um operador, não para um paciente.

### O que o paciente lê agora

| situação | mensagem |
|---|---|
| servidor respondeu 5xx | "Não foi possível acessar o resultado agora. Tente novamente em alguns instantes." |
| não houve resposta (DNS, TLS, rede) | "Não foi possível conectar agora. Verifique sua internet e tente novamente." |

`resultados/index.html` passou a tratar `status >= 500` com **mensagem fixa,
escrita aqui** — o corpo de um 5xx pode ter sido escrito por um proxy no
caminho, e nada que venha de lá deve chegar à tela de quem espera um exame.
A montagem da tela também saiu de dentro do `catch` de rede: um defeito ao
desenhar o resultado não pode mais ser anunciado como falta de internet.

---

## Testes

`tests/test_m26_7_cors_erros_e_papel.py` — **30 testes focados**, todos
verdes.

**Não são vazios:** rodados contra o código anterior (`git checkout HEAD --`
dos quatro arquivos alterados), **15 falham** — exatamente os que descrevem
o comportamento novo. Os outros 15 são invariantes que já valiam e seguem
valendo (ausência de curinga, 401 real com CORS, download por navegação de
topo, `Vary` etc.).

Cobertura:

- GRANT mínimo: `SELECT` só de `id`; `SELECT` da tabela inteira proibido;
  `INSERT` por coluna batendo **exatamente** com `AuditLog.__table__`
  (coluna nova de auditoria quebra o teste, não a produção); script
  idempotente; motivo do `SELECT (id)` documentado no próprio arquivo; fonte
  única no repositório; prova executável presente no deploy.
- Fronteira: 500 com CORS e cabeçalhos de segurança; 500 sem vazamento
  (`Traceback`, `permission denied`, `audit_logs`, `sqlalchemy`, nome de
  arquivo, "Consulte os logs"); 500 **sem** CORS para cinco origens não
  autorizadas; traceback preservado no log; 404 do catch-all com CORS; 401
  real; 401/410/429 de domínio; nenhuma resposta com `*`; sem `Origin` não
  se inventa cabeçalho.
- Tela: separação 5xx × rede; mensagem genérica sem termo técnico;
  `renderizar` fora do `catch` de rede; download segue por navegação de topo
  (regressão da M25.18).

**Regressão:** `test_m26_4_portal_resultados.py` +
`test_m26_5_hardening_portal.py` + os novos = **133 passed**.

A prova do `GRANT` contra PostgreSQL de verdade não cabe na suíte (que roda
em SQLite): ela vive na etapa `banco` do deploy, que agora executa
`INSERT ... RETURNING id` dentro de `BEGIN/ROLLBACK` — a trilha é append-only
por gatilho, e uma linha de teste comitada ali não teria como ser apagada.

---

## O que esta etapa NÃO fez

- Não recriou fixture sintético nem criou acesso novo em produção.
- Não usou paciente real, não enviou WhatsApp, não tocou no Financeiro.
- Não reabriu a M26.6, que segue encerrada (acesso revogado, link 410,
  cadastro arquivado).
- Não removeu o `GRANT SELECT (id)` já aplicado em produção — o script é
  idempotente e reaplicá-lo é inócuo.
