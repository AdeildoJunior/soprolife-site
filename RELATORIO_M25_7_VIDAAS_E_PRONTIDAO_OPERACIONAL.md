# Relatório M25.7 — Integração VIDaaS/IntegraICP e prontidão operacional

Data: 2026-08-06
Branch: `codex-m25a-search-console-reconciliation`

---

## 1. Estado inicial

Capturado antes de qualquer alteração:

```
pwd                     /home/adeildo/soprolife-site
git branch --show-current  codex-m25a-search-console-reconciliation
git log --oneline -1    c11a395 feat(m25.6): escolher unidade antes da fila...
git diff --check        (sem problemas)
```

`git status --short` trazia apenas mudanças legítimas anteriores, todas
**preservadas**: quatro arquivos do `/doctor` (3 SKILL.md + CLAUDE.md) e o
`RELATORIO_M25_5_M25_6_LAUDO_VISUAL.md` não versionado.

**`AGENTS.md` não existe neste repositório** — foi procurado e não encontrado.
As instruções seguidas foram as do `CLAUDE.md` e das skills do projeto.

## 2. Branch e commits

| Item | Valor |
| --- | --- |
| HEAD no início | `c11a3958d6180e65911c7ee1c97a50e41c008722` |
| M25.5 | `0b74a66` |
| M25.6 | `c11a395` |
| Commit criado nesta etapa | ver seção 16 |

## 3. Estado real da VPS (medido, não presumido)

Confirmado na etapa anterior por duas medições independentes:

- o `report-workflow.js` servido é **byte a byte idêntico** ao de
  `ff5b6e5`…`0b74a66` (80.396 bytes, SHA-256 `9de6149a…`);
- a rota `GET /laudos/validacao/{codigo}`, que **só existe a partir da
  M25.2**, responde `401` (montada) e não `404`.

**A VPS roda um commit entre `ff5b6e5` e `e069e7d`** — ou seja, a M25.2/25.3/
25.4 **já estão em produção**. Não é `9b8ae96` e não é `c11a395`.

A branch `painel-soprolife-v01` no GitHub está em `9b8ae96`, **atrás** do que
está implantado. Implantá-la faria a produção regredir. Nada foi implantado
nesta etapa.

## 4. Arquitetura encontrada

Já existia e **não foi reimplementado**: laudo nativo, fila médica, PDF da
MIR separado, liberação institucional, versionamento append-only, hash,
auditoria, cadastro privado da assinatura manuscrita e o contrato
`SignatureProvider` com apenas `UnconfiguredSignatureProvider`.

Migration aplicada na VPS: `a3f1d7c25e90` (head anterior).

## 5. Integração implementada

Cinco módulos novos, 1.450 linhas de implementação e 661 de teste.

| Módulo | Papel |
| --- | --- |
| `app/services/report_pades.py` | prepara o PDF, injeta o CMS, valida o resultado |
| `app/services/integraicp_client.py` | cliente HTTP + PKCE + digest Base64 |
| `app/services/qualified_signature.py` | máquina de estados e orquestração |
| `app/services/qualified_signature_store.py` | guarda o PDF preparado entre ida e volta |
| `app/services/transient_secrets.py` | cifra o `code_verifier` persistido |

### Decisão sobre dependências

**pyHanko foi deliberadamente evitado.** Ele traria mais de vinte pacotes
para um sistema médico em produção. A preparação PAdES é manipulação de
bytes do PDF e foi implementada nativamente; a validação criptográfica usa
`asn1crypto` (Python puro, 1.5.1) e `cryptography` (já presente). As duas
foram fixadas em `requirements.txt` e `requirements.lock`.

### Nível PAdES — declaração honesta

O módulo produz **PAdES-B-B** (assinatura básica). Ele **não** adiciona
carimbo do tempo RFC 3161 nem informações de revogação, portanto **não**
produz PAdES-T, -LT nem -LTA, e a constante `PADES_LEVEL_ACHIEVED` grava
exatamente isso no banco. Subir o nível exige implementar o que o nível
exige — não editar a constante.

Consequência prática a registrar: **sem carimbo do tempo, o laudo deixa de
validar quando o certificado da médica expirar.** Para laudo médico, que
precisa continuar verificável por anos, PAdES-LTA é o alvo. Isso fica como
trabalho seguinte, explicitamente não entregue aqui.

## 6. Endpoints e fluxo

| Método | Rota | Papel |
| --- | --- | --- |
| POST | `/laudos/{id}/assinatura-qualificada/iniciar` | prepara e devolve a URL do VIDaaS |
| POST | `/laudos/{id}/assinatura-qualificada/retorno` | consome o callback, valida e libera |
| GET | `/laudos/{id}/assinatura-qualificada` | acompanhamento (polling) |
| POST | `/laudos/{id}/assinatura-qualificada/cancelar` | cancelamento consciente |
| GET | `/laudos/admin/assinatura-qualificada/diagnostico` | diagnóstico (admin) |

Fluxo completo:

1. médica autenticada abre a espirometria e revisa a conclusão;
2. clica em **Assinar com VIDaaS** e confirma;
3. o servidor gera o PDF nativo, **prepara** com placeholder e ByteRange;
4. calcula o **SHA-256 do conteúdo assinável** (não do arquivo);
5. o PDF preparado é gravado na raiz privada, sob o UUID da solicitação;
6. PKCE (RFC 7636, S256) + `state` + `nonce` são gerados; o verifier é
   cifrado antes de ir ao banco;
7. a médica é levada ao VIDaaS e autoriza no aplicativo dela;
8. o retorno chega ao callback com `state`, `nonce` e `CredentialId`;
9. `GET /credentials/{id}` troca pelo certificado;
10. `POST /signatures` envia **somente o digest** em Base64 padrão;
11. o CMS é injetado no placeholder, sem alterar nenhum byte coberto;
12. `validate_pades` confere ByteRange, digest, `messageDigest` do CMS e a
    assinatura contra a chave pública do certificado;
13. **só então** o laudo é publicado como liberado, com selo ICP-Brasil.

Falhou qualquer etapa: a solicitação muda de estado, o laudo **continua em
elaboração** e nada é liberado.

## 7. Variáveis de ambiente

Prefixo `M15_`, seguindo o padrão do projeto. Todas fail-closed.

| Variável | Padrão | Papel |
| --- | --- | --- |
| `M15_REPORT_SIGNATURE_PROVIDER` | `unconfigured` | `unconfigured` ou `integraicp` |
| `M15_INTEGRAICP_ENABLED` | `false` | liga a integração |
| `M15_INTEGRAICP_BASE_URL` | — | base da API (HTTPS obrigatório) |
| `M15_INTEGRAICP_CHANNEL_ID` | — | canal contratado com a Valid |
| `M15_INTEGRAICP_CALLBACK_URL` | — | retorno (HTTPS obrigatório) |
| `M15_INTEGRAICP_SIGNATURE_POLICY` | — | política CMS acordada com a AC |
| `M15_INTEGRAICP_REQUEST_TIMEOUT_SECONDS` | `20.0` | timeout finito |
| `M15_INTEGRAICP_CREDENTIAL_LIFETIME_SECONDS` | `300` | validade da credencial |
| `M15_INTEGRAICP_CLEARANCE_LIFETIME_SECONDS` | `600` | janela de autorização |

`Settings.integraicp_ready()` só devolve verdadeiro com provedor
selecionado **e** habilitado **e** base **e** canal **e** callback. Faltando
qualquer peça, `get_signature_provider()` volta ao provedor nulo.

**Nenhum endpoint, canal, callback ou CPF está escrito no repositório.**

## 8. Migrations

`b6e2f94a17c3_m25_7_qualified_signature_requests.py`, com
`down_revision = "a3f1d7c25e90"` — encadeia exatamente na revisão já
aplicada na VPS.

Cria **uma tabela nova** e não toca em nenhuma existente. Testada:
`upgrade → downgrade -1 → upgrade` em banco temporário, tudo limpo.

## 9. Segurança

| Regra | Como é sustentada |
| --- | --- |
| Segredos só no ambiente privado | nenhum valor no repositório; validador exige HTTPS |
| Nada de segredo no navegador | o front recebe só `integracao_pronta` (booleano) |
| Nunca registrar CPF/verifier/canal | erros do cliente nunca carregam URL, corpo ou verifier — coberto por teste |
| Dado transitório protegido | `code_verifier` cifrado com AES-GCM, chave derivada por HKDF do segredo do M15 |
| State e nonce de uso único | guardados só como hash; `callback_consumed_at` marca o consumo |
| PKCE obrigatório | `generate_pkce()`, S256, verifier de 86 caracteres |
| Callback protegido contra replay | segundo uso responde `409 retorno_ja_utilizado` |
| Timeout e repetição idempotente | timeout finito; erro recuperável distinguido de definitivo |
| Ninguém assina pela médica | `_require_assigned_physician` + conferência de `physician_profile_id` |
| Sem assinatura automática | o adapter recusa uso não interativo, por teste |
| Sem troca de médica no meio | `retorno_de_outra_medica` (403) |
| Validação falhou, nada é liberado | `complete()` levanta antes de publicar |
| Interna nunca vira ICP-Brasil | selo dirigido por `signature_kind`, com teste dedicado |
| Gates de hash não afrouxados | `_qualified_signature_evidence` intacto |
| Sem chave/certificado no servidor | só o digest sai; a chave fica no HSM da AC |
| PDF e paciente não vão à AC | somente 32 bytes de digest — coberto por teste |
| MIR nunca assinado | o preparo recebe apenas o PDF nativo |

## 10. Montagem e validação PAdES

Preparação: o PDF é reescrito com dicionário `/Sig`
(`/SubFilter /ETSI.CAdES.detached`), campo de assinatura **invisível** (a
área de assinatura do laudo é exclusiva por regra do projeto) e
`/Contents` com 32 KB de zeros. O `/ByteRange` é corrigido no lugar, com
preenchimento por espaços para **não alterar o comprimento** — se alterasse,
todos os deslocamentos seguintes mudariam e o ByteRange passaria a mentir.

Não é atualização incremental de propósito: um laudo recém-gerado não tem
assinatura anterior a preservar, e reescrever é igualmente válido em PAdES
na primeira assinatura e muito menos frágil.

Validação (fail-closed, nesta ordem): ByteRange cobre o arquivo → digest do
conteúdo coberto bate com o enviado → CMS é SignedData legível → um único
signatário → `messageDigest` assinado bate com o digest → assinatura confere
contra a chave pública do certificado (RSA PKCS#1 v1.5 ou ECDSA).

## 11. Os dois hashes

Gravados em colunas separadas, com nomes que não deixam confundir:

- `prepared_sha256` — hash do **arquivo preparado**, âncora de idempotência;
- `signed_digest_sha256` — hash do **conteúdo coberto pelo ByteRange**, o
  único valor enviado à AC;
- `final_sha256` — hash do **arquivo depois da injeção**.

O primeiro e o terceiro **nunca coincidem** (a injeção muda bytes fora do
ByteRange). O segundo **não muda** com a injeção — é exatamente esse o ponto,
e há teste dedicado a cada uma dessas três propriedades.

## 12. Estados do fluxo

`rascunho` → `aguardando_autenticacao` → `aguardando_autorizacao` →
`assinatura_recebida` → `validando` → `assinado_liberado`.

Saídas: `recusado`, `expirado`, `falha_recuperavel`, `falha_definitiva`.

Só `falha_recuperavel` mantém o verifier cifrado, permitindo nova tentativa
com a **mesma** solicitação. Os demais estados terminais apagam o segredo.

Idempotência: uma solicitação viva bloqueia a abertura de outra
(`409 solicitacao_em_andamento`), e um índice único **parcial** no banco
garante no schema que um laudo não tem duas solicitações vencedoras.

## 13. Interface

Botão **Assinar com VIDaaS (ICP-Brasil)** ao lado — nunca no lugar — da
liberação institucional. Confirmação antes de iniciar, explicando que a
autorização acontece no aplicativo e que só o resumo criptográfico é
enviado. Status legível durante a espera, com indicador pulsante
(desativado sob `prefers-reduced-motion`), polling a cada 4 s, e botões de
cancelar e tentar novamente conforme o estado permita.

Sem integração configurada, a área mostra **"Integração aguardando
credencial da Valid"** e deixa explícito que a liberação institucional
continua válida. Nenhum detalhe técnico ou segredo é exibido. Responsivo:
o botão ocupa a largura toda abaixo de 640 px.

## 14. Testes e resultados

`tests/test_m25_7_qualified_signature.py` — **38 testes, todos passando**.
A autoridade certificadora é um servidor falso em memória
(`httpx.MockTransport`) e a cadeia é gerada na hora.

Cobertura por exigência: PKCE conforme RFC 7636 e sem repetição; digest em
Base64 **padrão** e não URL-safe; digest inválido; ByteRange correto;
os dois hashes distintos; tamanho preservado na injeção; PDF PAdES
verificável; reconstrução do preparado a partir dos bytes; CMS maior que o
espaço; digest divergente; CMS que assina outro conteúdo; assinatura
corrompida; CMS sem certificado; CMS sem `messageDigest`; dois signatários;
CMS lixo; PDF alterado após assinado; arquivo que não é PDF; cliente envia
só o digest; URL de autorização não carrega o verifier; timeout;
recusa (403); indisponibilidade (503); resposta malformada; erro que não
vaza URL/verifier/canal; cliente sem configuração; verifier cifrado e
inabrível com outra chave; integração desligada por padrão; configuração
incompleta; `http://` recusado; provedor nulo por padrão; adapter recusa uso
não interativo; diagnóstico não expõe valores.

**Um teste verde aqui NÃO significa que o fluxo real do VIDaaS foi
exercitado** — significa que a nossa metade do protocolo está correta.

### Dois defeitos reais encontrados pela própria suíte

**1. Truncamento aleatório do CMS.** O preenchimento do espaço reservado era
removido com `rstrip(b"\x00")`. Quando a assinatura RSA termina em `0x00` —
cerca de uma a cada 256 —, isso comia um byte **real** e o CMS deixava de
ser decodificável. A falha era intermitente: passou 36 vezes seguidas e
quebrou na rodada completa. O corte agora lê o comprimento declarado no
cabeçalho ASN.1. Dois testes de regressão fixam o comportamento, incluindo
a prova de que o `rstrip` perdia o byte.

**2. Validação escapando por decodificação preguiçosa.** O `asn1crypto`
decodifica sob demanda: `ContentInfo.load` aceitava lixo e o erro só
aparecia ao tocar em `.native`, **fora** do `try`. Um CMS inválido virava
exceção não tratada em vez de recusa limpa. A leitura do tipo e a
materialização do conteúdo passaram para dentro do bloco tratado.

### Suíte completa do backend

**931 passaram, 22 puladas, 1 falha** —
`test_m24d_pilot_deployment.py::test_preparacao_mantem_backup_em_terminal_interativo`.

Investigação dessa falha: passa isolada tanto na árvore com a M25.7 quanto
na árvore limpa (verificado com `git stash`); falha só na execução completa;
usa PTY, que é sensível ao ambiente; e testa o comportamento de backup de um
script de deploy — assunto sem relação com assinatura qualificada. Ela
**também falhou na execução completa anterior às minhas correções**. Não
consegui isolar a causa exata da dependência de ordem, e registro isso como
pendência em aberto em vez de afirmar que está resolvida.

Três outras falhas apareceram na primeira execução completa e **foram
corrigidas**: a head esperada em `test_migrations.py` (atualização prevista
pelo próprio comentário do teste), a divergência entre a migration e o
modelo (`created_at`/`updated_at` NOT NULL e nome da constraint única), e o
contrato fechado da fila em `test_m24a_frontend_contract.py`. Esta última
era **resíduo da M25.6**: naquele commit rodei apenas a suíte do laudo e não
a completa, então `location_key`/`location_name` entraram sem atualizar o
contrato. Corrigido aqui.

Demais suítes: `test-m24a-report-workflow.js` (todos os casos M24C),
proxy do Command Center (46), `git diff --check` limpo, `node --check` no
JS, migration reversível (`upgrade → downgrade -1 → upgrade`).

## 15. Arquivos alterados

**Novos:**

```
painel-soprolife/nucleo-m15/app/services/report_pades.py              526
painel-soprolife/nucleo-m15/app/services/integraicp_client.py        308
painel-soprolife/nucleo-m15/app/services/qualified_signature.py      468
painel-soprolife/nucleo-m15/app/services/qualified_signature_store.py 73
painel-soprolife/nucleo-m15/app/services/transient_secrets.py         75
painel-soprolife/nucleo-m15/migrations/versions/b6e2f94a17c3_*.py
painel-soprolife/nucleo-m15/tests/test_m25_7_qualified_signature.py  538
painel-soprolife/nucleo-m15/tests/pades_fakes.py                     123
```

**Modificados:** `app/config.py`, `app/models.py`, `app/schemas.py`,
`app/routers/reports.py`, `app/services/signature_provider.py`,
`requirements.txt`, `requirements.lock`, `js/report-workflow.js`,
`css/report-workflow.css`, `index.html`.

**Fora do commit da M25.7** (mudanças anteriores, preservadas): os quatro
arquivos do `/doctor` e o relatório da M25.5/M25.6.

## 16. Commit criado e git status final

Commit: **`ecf345e`** — `feat(m25.7): integrar assinatura qualificada
ICP-Brasil via VIDaaS/IntegraICP`, 21 arquivos.

`git status --short` ao final:

```
 M .claude/skills/soprolife-audit-patterns/SKILL.md
 M .claude/skills/soprolife-marketing-seo/SKILL.md
 M .claude/skills/soprolife-medical-docs-pop/SKILL.md
 M CLAUDE.md
?? RELATORIO_M25_5_M25_6_LAUDO_VISUAL.md
```

Os cinco itens acima são anteriores à M25.7 e foram **deliberadamente
deixados fora do commit**: os quatro primeiros vieram do `/doctor` e não
pertencem ao projeto SoproLife; o quinto é o relatório da M25.5/M25.6.

**Sem push, sem merge, sem deploy.**

## 17. Pendências externas e credenciais necessárias

**A integração real está bloqueada.** Falta, e nada disso é código:

1. **Certificado e-CPF ICP-Brasil em nuvem no CPF da Dra. Ana** (VIDaaS da
   Valid). Precisa ser pessoal, não do CNPJ. Vale perguntar à AC sobre o
   certificado de atributo do CFM, que amarra o CRM ao certificado.
2. **Channel ID** contratado com a Valid.
3. **Base URL** do ambiente (homologação e produção).
4. **Callback URL** registrado junto à Valid, apontando para o painel.
5. **Política de assinatura** (OID) acordada com a AC.
6. **Confirmação jurídica** de que laudo de espirometria exige assinatura
   qualificada. Leitura provável: CFM 1.821/2007 + Lei 14.063/2020 na faixa
   "qualificada" — **a confirmar com o jurídico**, não afirmado aqui.

## 18. Procedimento exato para configurar o Channel na VPS

Os segredos vivem **somente** em `/opt/soprolife/secrets/m15.env`, fora do
Git, lido pelo systemd.

```bash
ssh soprolife@soprolife-painel-01.tailcaf0e4.ts.net
sudo -e /opt/soprolife/secrets/m15.env
```

Acrescentar (substituindo pelos valores reais da Valid):

```
M15_REPORT_SIGNATURE_PROVIDER=integraicp
M15_INTEGRAICP_ENABLED=true
M15_INTEGRAICP_BASE_URL=https://<host-da-valid>
M15_INTEGRAICP_CHANNEL_ID=<channel-contratado>
M15_INTEGRAICP_CALLBACK_URL=https://<painel>/painel-soprolife/api/m15/laudos/retorno-vidaas
M15_INTEGRAICP_SIGNATURE_POLICY=<oid-da-politica>
```

Conferir permissão e reiniciar:

```bash
sudo chmod 600 /opt/soprolife/secrets/m15.env
sudo chown root:root /opt/soprolife/secrets/m15.env
sudo systemctl restart soprolife-m15-api.service
sudo systemctl status soprolife-m15-api.service --no-pager | head -5
```

Conferir pelo diagnóstico (entrar como admin no painel):

```
GET /painel-soprolife/api/m15/laudos/admin/assinatura-qualificada/diagnostico
```

Deve responder `"integracao_pronta": true`. **O diagnóstico nunca mostra o
valor de nenhum segredo — só se está presente.**

## 19. Procedimento para vincular a Dra. Ana

1. O certificado VIDaaS é **dela**, no aplicativo dela — o servidor nunca o
   armazena e não há nada a "cadastrar" no painel.
2. Ela precisa estar autenticada na **própria conta** e ser a médica
   **atribuída** ao laudo; qualquer outra pessoa recebe 403.
3. Na primeira assinatura, o VIDaaS pedirá a autorização dela no aplicativo.
4. A assinatura manuscrita (elemento visual, **não** é certificado) continua
   sendo cadastrada em Administração → Contas médicas → Ana → "Assinatura
   manuscrita (imagem)" — e **precisa ser cadastrada na VPS**, porque o
   arquivo não viaja pelo Git.

## 20. Tudo que falta para ela começar amanhã

| Item | Estado |
| --- | --- |
| Conta individual criada e ativa | **verificar na VPS** — só existe localmente |
| Papel `medico` e permissões | idem |
| CRM-RJ exibido como `5262307-5` | corrigido no código (M25.6); **falta deploy** |
| RQE 58224 | no seed local; verificar na VPS |
| Assinatura manuscrita privada | **falta cadastrar na VPS** |
| M25.5/M25.6 prontas | sim — **falta deploy** |
| Fila por unidade | sim — **falta deploy** |
| Pastore Ipanema cadastrada | verificar na VPS |
| Upload do PDF MIR | já em produção |
| Conclusão e pós-BD | já em produção |
| PDF nativo | já em produção (versão M25.4) |
| Downloads separados | já em produção |
| Auditoria | já em produção |
| Correção/adendo | já em produção |
| URL de validação verdadeira | **placeholder** — confirmar o domínio real |
| Teste com paciente fictício | feito localmente |

**Ela consegue laudar amanhã com a assinatura eletrônica interna**, que já
está em produção. A assinatura ICP-Brasil depende das credenciais da Valid.

## 21. Comandos exatos de push e deploy — NÃO EXECUTADOS

```bash
# push (não executado nesta etapa)
git push origin codex-m25a-search-console-reconciliation

# deploy (rodar NO TERMINAL DA VPS, não daqui)
ssh soprolife@soprolife-painel-01.tailcaf0e4.ts.net
cd /opt/soprolife/soprolife-site
git fetch origin
git checkout codex-m25a-search-console-reconciliation
git merge --ff-only origin/codex-m25a-search-console-reconciliation
git rev-parse HEAD

sudo SOPROLIFE_M15_GO_LIVE=YES \
     SOPROLIFE_M15_HTTPS_BASE_URL=https://soprolife-painel-01.tailcaf0e4.ts.net/ \
     SOPROLIFE_REPORTS_PILOT_AUTHORIZATION="HABILITAR PILOTO DE LAUDOS" \
     painel-soprolife/nucleo-m15/scripts/deploy-producao-vps.sh \
     <commit-40-hex> codex-m25a-search-console-reconciliation
```

**Não implantar `painel-soprolife-v01`**: ela está em `9b8ae96`, atrás do que
já roda em produção, e implantá-la causaria regressão.

## 22. Roteiro do teste real posterior (quando houver credencial)

1. Configurar `m15.env` de **homologação** e reiniciar o serviço.
2. Conferir `integracao_pronta: true` no diagnóstico admin.
3. Criar paciente **fictício** e espirometria fictícia com PDF de teste.
4. Entrar como a médica, gerar a prévia e conferir.
5. Clicar em **Assinar com VIDaaS** e confirmar.
6. Autorizar no aplicativo VIDaaS.
7. Conferir no PDF final: selo ICP-Brasil e assinatura reconhecida.
8. Validar o PDF em `validar.iti.gov.br` — é o juiz externo.
9. Conferir no banco: `prepared_sha256`, `signed_digest_sha256` e
   `final_sha256` distintos onde devem ser, e `pades_level = PAdES-B-B`.
10. Repetir recusando a autorização e conferir que **nada** foi liberado.
11. Repetir deixando expirar e conferir o estado `expirado`.
12. Só depois disso, repetir em produção com paciente real.

## 23. Caminho completo do relatório

```
/home/adeildo/soprolife-site/RELATORIO_M25_7_VIDAAS_E_PRONTIDAO_OPERACIONAL.md
```

---

# ADENDO M25.8 — Fluxo gratuito de assinatura externa em lote

Data: 2026-08-06 (mesma sessão)

## 24. Correção de escopo

A Dra. Ana **já tem certificado ICP-Brasil e já assina pelo VIDaaS**. A API
comercial IntegraICP (M25.7) deixa de ser o caminho principal e passa a ser
uma alternativa futura, desligada. O caminho prioritário é gratuito: o painel
prepara os laudos, ela assina fora com o que já usa, e devolve em lote.

## 25. O que o VIDaaS permite em lote — resposta objetiva

Pesquisa feita sem contratar serviço e sem enviar dado real.

| Ferramenta | Assina vários PDFs numa sessão? | Base |
| --- | --- | --- |
| Ferramenta oficial "Assinar com VIDaaS" | **NÃO CONFIRMADO** | a página pública não descreve a funcionalidade; nada encontrado documenta lote |
| VIDaaS Connect (Adobe Reader) | **NÃO CONFIRMADO** | o material oficial demonstra assinatura de *um* documento |
| API IntegraICP (comercial) | **SIM** — até 20 documentos por requisição | documentação de integração da Valid |
| Assinador SERPRO (gratuito) | **SIM** — função "Assinar em Lote", seleção múltipla com Ctrl/Shift | tutorial oficial do SERPRO |

**Conclusão honesta: não foi possível comprovar que a ferramenta oficial do
VIDaaS assina em lote.** Enquanto isso não for confirmado com a Valid, o
sistema não afirma que existe — as instruções dentro do ZIP dizem
explicitamente para considerar assinatura arquivo por arquivo, e há teste
travando esse texto (`test_instrucoes_nao_prometem_assinatura_em_lote`).

Caminho a investigar com a médica: o **Assinador SERPRO** documenta lote e é
gratuito, mas a compatibilidade dele com certificado em nuvem VIDaaS **não
foi confirmada** — precisa ser testada por ela, na máquina dela.

Fontes:
- <https://assinar-com.vidaas.com.br/visualiza/>
- <https://valid-sa.atlassian.net/wiki/spaces/PDD/pages/958365697/Manual+de+Integra+o+com+VIDaaS+-+Certificado+em+Nuvem>
- <https://tutorial.assinadorserpro.estaleiro.serpro.gov.br/html/demo_17.html>
- <https://validcertificadora.com.br/pages/certificado-em-nuvem/>

## 26. O que foi implementado nesta etapa

`app/services/external_signature.py` — núcleo completo e testado:

- **Carimbo de identificação** (`stamp_signing_metadata`): grava código do
  laudo, versão e hash do conteúdo em `/Keywords`. É o que reencontra o
  laudo na volta, **sem depender do nome do arquivo**.
- **Pacote de assinatura** (`build_signing_package`): ZIP com os PDFs
  nativos em `assinar/`, `manifesto.json`, `manifesto.csv` e
  `COMO-ASSINAR.txt`. O PDF técnico da MIR fica **fora por padrão** e, quando
  pedido, vai para `exame-tecnico-mir-NAO-ASSINAR/`.
- **Entrada flexível** (`extract_pdfs`): aceita PDFs soltos ou um ZIP.
- **Verificação por arquivo** (`verify_signed_pdf`): nove desfechos
  distintos, nunca um booleano para o lote inteiro.
- **Contadores** (`summarize`): total, validados, com erro.

### Duas decisões de projeto que precisam ficar registradas

**1. O carimbo guarda o hash do CONTEÚDO, não o do arquivo.** A primeira
versão tentou gravar no PDF o hash do próprio PDF — impossível por
construção. O carimbo passou a levar o hash do laudo renderizado *antes* do
carimbo; o hash do arquivo entregue (o que ela assina) vive no banco e no
manifesto.

**2. A prova de integridade é o PREFIXO, não um hash.** Assinar um PDF
**anexa** uma atualização incremental — não reescreve. Então o preparado tem
de continuar sendo prefixo exato do assinado. Isso detecta reimpressão e
exportação, que um hash simples não pegaria. O assinador de teste
(`sign_incrementally`) anexa de verdade, justamente para o teste não passar
com um fluxo que quebraria na vida real.

## 27. Testes desta etapa

`tests/test_m25_8_external_batch.py` — **20 testes, todos passando**
(58 somando com a M25.7).

Cobrem: carimbo lido de volta; hash do conteúdo distinto do hash do arquivo;
PDF sem carimbo; nome de arquivo sem nome de paciente e sem acento; ZIP com
três laudos e os dois manifestos; manifesto sem padrão de CPF e sem termo
clínico; MIR fora por padrão e em pasta separada quando pedido; lote vazio
recusado; instruções que não prometem lote; ZIP e PDFs soltos equivalentes;
**três enviados, dois válidos e um inválido, com liberação só dos válidos**;
reenvio do corrigido; PDF de outro laudo; versão antiga; documento reescrito
em vez de assinado; assinatura de outro certificado; certificado vinculado
correto; assinatura corrompida; arquivo de outro sistema; contadores.

## 28. O que FALTA para a médica usar

O núcleo está pronto e testado, mas **ainda não é alcançável pela tela**.
Falta, nesta ordem:

1. **Migration + colunas de rastreio**: `preparado_em`, `baixado_em` em
   `report_documents`, e o vínculo do certificado da médica
   (`icp_signer_subject`) em `physician_profiles`.
2. **Endpoints**: finalizar revisão (congela o PDF com kind
   `assinatura_pendente` e status homônimo — ambos já existem no schema, sem
   necessidade de migration), baixar lote (ZIP) e enviar assinados.
3. **Interface**: filtros por estado, caixas de seleção, contadores, botões
   de baixar e enviar, e resultado individual por arquivo.

Nada disso foi entregue nesta sessão. Preferi fechar o núcleo provado a
subir endpoints pela metade.
