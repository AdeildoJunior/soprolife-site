# RELATÓRIO M25.3 — Finalização do Laudo Online de Espirometria

## 1. Data e hora

Execução em **05/08/2026**, das 09h20 às 10h20 (America/Sao_Paulo).

## 2. Diretório e branch

```text
Diretório : /home/adeildo/soprolife-site
Branch    : codex-m25a-search-console-reconciliation
Branch base do repositório: main
```

> Observação: a M25.2 foi executada em outra worktree
> (`/home/fedorasurf/soprolife-worktrees/...`). Esta sessão rodou no
> diretório principal, que **não tinha o ambiente provisionado**.

## 3. Commit inicial

```text
3df1717 docs(m25.2): add milestone report
0579e87 feat(m25.2): add native SoproLife spirometry report
```

Árvore limpa no início (`git status --short` sem saída).

## 4. Estado encontrado na M25.2

A implementação estava **substancialmente completa e correta**. Nada foi
reimplementado. Confirmei, lendo o código:

| Item | Estado encontrado |
| --- | --- |
| Catálogo de 17 conclusões + Personalizado | Pronto e travado por teste |
| Complementos pós-BD (5) e supressão quando não há fase pós-BD | Pronto |
| Prévia nativa, liberação consciente, bloqueio, adendo, versão corretiva | Pronto |
| Dois documentos separados (MIR intacto + laudo nativo) | Pronto |
| Endpoints M25.2 no backend | Todos presentes |
| Frontend chamando todos os endpoints M25.2 | Presente e ligado |
| Navegação do Centro de Comando | Entradas já existentes em `index.html` |
| Suíte `test_m25_2_native_report.py` | 31 testes, todos passando |

**O que faltava era o marco não estar utilizável neste ambiente**, mais
quatro defeitos reais que só apareciam ao rodar o fluxo de ponta a ponta.

## 5. Problemas identificados

### P1 — Ambiente local inexistente (bloqueador de tudo)

- venv em `nucleo-m15/.venv` **sem `reportlab` nem `pillow`** — qualquer
  geração de PDF nativo falhava com `ModuleNotFoundError`.
- Sem `.env`, sem banco migrado, sem raiz privada de storage provisionada.
- Sem nenhum cenário de teste: banco vazio, nenhum paciente, exame ou laudo.

### P2 — `especialidade` e `crm_display` eram graváveis por nenhuma rota

A M25.2 criou as colunas e o gerador do PDF **já as lia**
(`native_report_builder.py:187-188`), mas `PhysicianProfileAdminUpdate` é um
`StrictModel` e **não aceitava os dois campos**. Nenhum outro caminho os
escrevia.

Consequência direta: **o requisito 14 era inalcançável pelo cadastro real**.
O laudo saía sem "Médica Pneumologista" e com o CRM em dígitos crus
(`52623075`) em vez de `52.62307-5`.

Além disso, o comentário em `models.py:1153` prometia uma validação em
`normalize.crm_display_matches` que **não existia no código**.

### P3 — A tela médica não mostrava o local de realização

`GET /laudos/{id}` devolvia paciente, código e data do exame — mas **não o
local de realização estruturado**, nem hora, nem indicação clínica. A tela
exibia apenas o código técnico da origem (`clinica_parceira`). O local só
aparecia dentro do PDF, ou seja, a médica não conseguia conferir o cabeçalho
**antes** de assinar. Isso é exatamente o requisito 5.

### P4 — Todo laudo liberado saía com uma segunda página quase vazia

A prévia cabia em 1 página, mas a versão **liberada** (que acrescenta o bloco
de validação e a área de assinatura) estourava por poucos pontos e empurrava
a assinatura sozinha para a página 2. Medido:

| Cenário | Antes | Depois |
| --- | --- | --- |
| Liberado simples | 1 pág. (+21,6 pt de folga) | 1 pág. (+71,6 pt) |
| Liberado + faixa do piloto | **2 págs.** (−11,4 pt) | **1 pág.** (+41,6 pt) |
| Liberado + piloto + QR | **2 págs.** (−37,4 pt) | **1 pág.** (+15,6 pt) |

Como o piloto é o modo em operação, **100% dos laudos liberados** saíam com
uma página 2 desperdiçada — e pioraria ao configurar a URL de validação.

### P5 — Sobreposição no cabeçalho do PDF

A régua do cabeçalho era desenhada em `top − 34` e a base do logo caía
exatamente em `top − 34`: a linha **cortava a tagline** "DIAGNÓSTICOS E
SOLUÇÕES EM SAÚDE" da marca. Confirmado por ampliação a 400 dpi.

### P6 — A suíte de testes lia o `.env` do desenvolvedor

Ao criar o `.env` local, **7 testes passaram a falhar**
(`test_m24a_feature_flag.py`, `test_m24d_reports_pilot.py` e outros). Causa:
`Settings` carrega `.env` do diretório corrente, então as asserções que
provam os **padrões fail-closed** (`reports_enabled` nascer `false`,
`reports_mode` nascer `disabled`) passavam a ler a configuração local.

É um defeito real de setup: qualquer desenvolvedor com `.env` ficava sem
suíte confiável e — pior — **um padrão inseguro poderia ser mascarado por um
`.env` permissivo**.

### P7 — Asserção de cache-bust fixada em string

`test-m24a-report-workflow.js` fixava `v=2026080501`. O próprio comentário
dizia que a intenção era provar que os dois assets têm **o mesmo selo**, não
qual selo. Fixar o número só obrigava a editar o teste a cada release.

## 6. Correções realizadas

| # | Correção | Arquivos |
| --- | --- | --- |
| C1 | `reportlab`, `pillow`, `pypdf`, `pytest` instalados no venv conforme `requirements.lock` | (ambiente, não versionado) |
| C2 | `.env` local, raiz privada `0700` fora do Git, `alembic upgrade head` | (não versionado) |
| C3 | `crm_display` e `especialidade` graváveis pela rota admin, entrando na identidade profissional (mudá-los reabre a verificação) | `app/schemas.py`, `app/routers/reports.py` |
| C4 | `crm_display_matches()` criada — a formatação precisa ter **exatamente** os mesmos dígitos do CRM canônico; divergência → 422 `crm_display_divergente` | `app/normalize.py`, `app/routers/reports.py` |
| C5 | Os dois campos passam a ser devolvidos pelo serializer (dado institucional, nunca do paciente) | `app/serializers.py` |
| C6 | `GET /laudos/{id}` passa a devolver `location` estruturado + hora, fase pós-BD e indicação clínica do exame | `app/routers/reports.py` |
| C7 | Tela médica ganhou o bloco "Exame / Local de realização", responsivo | `js/report-workflow.js`, `css/report-workflow.css` |
| C8 | Ritmo vertical do PDF ajustado (só espaçamento — nenhum tamanho de fonte mudou) para o laudo liberado típico caber em 1 página | `app/services/report_native_pdf.py` |
| C9 | Régua do cabeçalho movida para `top − 40`, liberando a tagline do logo | `app/services/report_native_pdf.py` |
| C10 | Suíte deixa de ler o `.env` do desenvolvedor | `tests/conftest.py` |
| C11 | Asserção de cache-bust passa a provar o selo comum em vez do número | `scripts/test-m24a-report-workflow.js` |
| C12 | Cache-bust atualizado (`v=2026080502`), pois CSS e JS mudaram | `index.html` |
| C13 | 4 testes de regressão M25.3 | `tests/test_m25_2_native_report.py` |
| C14 | Seed fictício e verificador de fluxo ponta a ponta | `scripts/seed_m25_3_laudo_demo.py`, `scripts/test_m25_3_fluxo_completo.py` |

**Nenhuma funcionalidade anterior foi removida ou desativada.** O caminho
M24C (`/compor`, `/preparar-assinatura`) segue intacto.

## 7. Arquivos alterados

```text
 painel-soprolife/css/report-workflow.css                       |  63 ++++++
 painel-soprolife/index.html                                    |   4 +-
 painel-soprolife/js/report-workflow.js                         |  47 ++++
 painel-soprolife/nucleo-m15/app/normalize.py                   |  18 ++
 painel-soprolife/nucleo-m15/app/routers/reports.py             |  30 ++-
 painel-soprolife/nucleo-m15/app/schemas.py                     |  18 ++
 painel-soprolife/nucleo-m15/app/serializers.py                 |   4 +
 painel-soprolife/nucleo-m15/app/services/report_native_pdf.py  |  35 +--
 painel-soprolife/nucleo-m15/tests/conftest.py                  |  14 +
 painel-soprolife/nucleo-m15/tests/test_m25_2_native_report.py  | 145 ++++++++++
 painel-soprolife/scripts/test-m24a-report-workflow.js          |  11 +-
```

Novos (não rastreados até o commit):

```text
 painel-soprolife/nucleo-m15/scripts/seed_m25_3_laudo_demo.py
 painel-soprolife/nucleo-m15/scripts/test_m25_3_fluxo_completo.py
```

Não versionados por desenho: `nucleo-m15/.env`, `nucleo-m15/var/`,
`nucleo-m15/.venv/` e a raiz privada `/home/adeildo/.soprolife-private/`.

## 8. Integração com os cadastros existentes

O cenário de teste **não cria nenhum sistema paralelo**. Tudo entra nas
tabelas canônicas, pelos endpoints reais:

| Cadastro | Tabela | Como foi criado |
| --- | --- | --- |
| Paciente | `people` | `POST /api/v1/pessoas` |
| Espirometria | `spirometry_exams` | `POST /api/v1/atendimentos` |
| Clínica e unidade | `partners`, `partner_units` | ORM + `allocate_public_code` |
| Usuários e permissões | `users`, `user_roles`, `roles` | `ensure_roles_exist` + papéis reais |
| Perfil médico | `physician_profiles` | `PATCH /api/v1/laudos/admin/medicos/{id}` |
| Laudo + PDF da MIR | `report_documents`, `report_document_versions` | `POST /api/v1/laudos` |
| Atribuição | `report_assignments` | criada pelo próprio upload |

**Nenhuma duplicação de paciente ou exame.** A interface principal já
consumia a fonte real (`window.SoproM15`) — não havia dado demonstrativo fixo
a substituir.

Uma decisão de domínio a registrar: o exame fictício é do tipo
`espirometria_soprolife` e **não** carrega `partner_id`/`partner_unit_id`,
porque o domínio reserva esse vínculo ao tipo `espirometria_pastore`
(rateio/fechamento financeiro). O local de realização vem de
`report_documents.origin_partner_unit_id`, que é a **prioridade nº 1**
documentada em `report_locations.py`. A invariante do M22/M23 permanece
intacta.

## 9. Fluxo funcional validado

`scripts/test_m25_3_fluxo_completo.py` — **49 verificações, 0 falhas**:

```text
[1]  fila médica: laudo pendente aparece                          OK
[2]  abertura pela área médica: paciente + local estruturado      OK
[3]  catálogo: 18 conclusões exatas + 5 complementos pós-BD       OK
[4]  DVO Leve + RBD+ convertidos para texto por extenso           OK
[5]  edição livre do texto + hash conferido                       OK
[6]  confirmação consciente: recusa sem frase exata (422)         OK
     recusa conteúdo divergente do conferido                      OK
[7]  liberação: status `liberado`, código de validação alocado    OK
[8/9] PDF gerado, hash SHA-256 e versão congelados                OK
[10] nova prévia recusada após liberação                          OK
     nova liberação recusada                                      OK
[11] adendo publicado; versão liberada anterior intacta           OK
[12] dois documentos em chaves separadas, baixados separadamente  OK
     hash de cada arquivo confere com o registrado                OK
     PDF da MIR sem assinatura nem CRM por cima                   OK
     laudo com identificação, local, conclusão e adendo           OK
[13] validação por código não expõe paciente nem conclusão        OK
```

### Checagem dos 20 requisitos

| # | Requisito | Estado |
| --- | --- | --- |
| 1 | Acesso pela navegação real | OK — entradas no menu e no hub |
| 2 | Fila de pendentes | OK |
| 3 | Tela médica do exame | OK |
| 4 | PDF técnico da MIR acessível | OK — visualizador autenticado |
| 5 | Paciente, exame e local | **Corrigido (C6/C7)** |
| 6 | Botões abreviados | OK |
| 7 | Siglas → texto por extenso | OK |
| 8 | Complementos pós-BD | OK |
| 9 | Conclusão editável | OK |
| 10 | Prévia do laudo | OK |
| 11 | "Assinar e liberar laudo" | OK |
| 12 | Confirmação consciente | OK |
| 13 | PDF final | OK |
| 14 | Identificação completa da médica | **Corrigido (C3/C4/C5)** |
| 15 | Área exclusiva de assinatura sem sobreposição | **Verificado + C9** |
| 16 | Bloqueio pós-assinatura | OK |
| 17 | Download separado dos dois PDFs | OK |
| 18 | Código, versão, hash e validação | OK |
| 19 | Correção/adendo preservando versão | OK |
| 20 | Trilha de auditoria | OK — sem dado clínico |

Catálogo conferido item a item contra a lista pedida: as 18 opções e os 5
complementos batem exatamente. **Nenhum grau é escolhido pelo sistema** — o
servidor só converte o código escolhido em texto, e a médica pode reescrever
tudo antes de assinar.

## 10. PDF gerado e localização do exemplo fictício

```text
/tmp/m25-3-laudo/laudo_soprolife.pdf     laudo médico SoproLife
/tmp/m25-3-laudo/tecnico_mir.pdf         PDF técnico (substituto sintético)
/tmp/m25-3-laudo/final.pdf               exemplo liberado com QR, 1 página
/tmp/m25-3-laudo/full-1.png              renderização para conferência
```

Os arquivos servidos pela aplicação ficam na raiz privada, fora do Git:

```text
/home/adeildo/.soprolife-private/m25-reports/laudos/…   (0700 / 0600)
```

## 11. Resultado da verificação visual

**PDF** — renderizado a 100 dpi e o cabeçalho ampliado a 400 dpi:

- Logo oficial da SoproLife íntegro, **tagline não mais cortada** (C9).
- Cabeçalho com nome institucional, código do laudo e versão.
- Blocos de paciente, exame e local legíveis, sem sobreposição.
- Conclusão em destaque com barra teal.
- Aviso de que o PDF da MIR é documento separado.
- Bloco de identificação e validação com código, versão, data e QR.
- **Área de assinatura exclusiva**, com faixa reservada limpa, linha, nome,
  especialidade, CRM e RQE — nada desenhado por cima.
- Declaração honesta de que a liberação não é ICP-Brasil.
- **1 página** no caso típico liberado (antes: 2).

**Telas do painel** — verificadas estruturalmente e pelo contrato HTTP:

- Entradas de navegação presentes e desbloqueadas pela config do piloto.
- Bloco novo "Exame / Local de realização" em grade de 2 colunas no desktop,
  colapsando para 1 coluna em ≤ 720 px, com rótulo acima do valor para o
  endereço da clínica não espremer.
- `node --check` limpo em todo o JS do painel.

**Limitação honesta:** não consegui capturar screenshots do painel neste
notebook. O harness do projeto (`scripts/test-m24a-browser-e2e.js`) dirige
**Google Chrome via CDP**, e esta máquina só tem Firefox instalado. A
conferência visual das telas depende de você abrir no navegador — o roteiro
está na seção 16.

## 12. Testes e resultados

| Verificação | Resultado |
| --- | --- |
| `pytest tests/test_m25_2_native_report.py` | **35 passaram** (31 originais + 4 novos) |
| `pytest` flags/piloto (4 arquivos) | **70 passaram**, 1 skipped |
| `pytest` laudos + migrations (4 arquivos) | **76 passaram** |
| `pytest` suíte completa do Núcleo M15 | **929 passaram**, 22 skipped, **0 falhas** |
| `scripts/test_m25_3_fluxo_completo.py` | **49 verificações, 0 falhas** |
| `node --check` em todo o JS do painel | OK |
| `scripts/test-m24a-report-workflow.js` | Todos os casos passaram |
| `scripts/test-entrada-dados-ux.js` | Todos passaram |
| `scripts/test-espirometria-financeiro.js` | Todos passaram |
| `git diff --check` | OK |
| `alembic upgrade head` | `a3f1d7c25e90 (head)` — head única |

### Suíte completa

```text
929 passed, 22 skipped, 0 failed  (15m29s)
```

**A suíte inteira está verde.** Duas observações:

- A execução anterior acusara 7 falhas, **todas causadas pelo `.env` local** —
  eliminadas por C10.
- As **12 falhas de `test_live_multisheet_reader.py`** registradas no relatório
  M25.2 (`ModuleNotFoundError: googleapiclient`) **não ocorrem neste
  ambiente**, porque este venv tem `google-api-python-client` instalado. Era
  uma lacuna de ambiente da worktree anterior, não um defeito de código.

Portanto o resultado aqui é melhor que a linha de base da M25.2 (913 passaram
com 12 falhas).

### Falha pré-existente que permanece aberta

`quality-gate-safe.sh` acusa **1 check com problema**:

```text
test-marketing credencial durável (M21)   FALHOU
  FAIL: chave inválida vira credential_pending (nunca ADC pessoal)
        — kind=none erro=DEPENDENCY_MISSING
```

**Não é causada por este trabalho.** Provado por `git stash` de todas as
minhas alterações: o gate falha **identicamente** no commit `3df1717`.

Causa raiz: o gate invoca o teste com o `python3` do sistema, que não tem
`google-api-python-client`/`google-auth`; o conector então responde
`DEPENDENCY_MISSING` em vez de `credential_pending`. O mesmo teste **passa**
quando executado com o venv do `nucleo-m15`. É assunto de Marketing, fora do
escopo do laudo — não mexi.

## 13. Tratamento da assinatura

**Procurei o ativo autorizado** no repositório inteiro e na raiz privada
configurada. **Não existe** nenhum ativo de assinatura da Dra. Ana Cristina.
**Nada foi inventado, desenhado ou simulado.**

O sistema está **plenamente funcional sem o ativo**: o laudo é liberado
normalmente e sai com o bloco identificador completo, com a área de
assinatura reservada, limpa e do mesmo tamanho — confirmado visualmente e
por teste.

**Onde cadastrar quando houver autorização:**

```text
POST /api/v1/laudos/admin/medicos/{physician_profile_id}/assinatura
  multipart/form-data:
    arquivo     = <PNG com fundo transparente, até 2 MiB,
                   proporção entre 0,8:1 e 12:1>
    confirmacao = "ATIVO DE ASSINATURA AUTORIZADO"
  autorização: papel admin (a própria médica NÃO cadastra o próprio ativo)
```

Destino físico, fora do Git:

```text
/home/adeildo/.soprolife-private/m25-reports/assinaturas/<physician_profile_id>/<asset_id>.png
  (raiz 0700, arquivo 0600)
```

Em produção o caminho é `<M15_REPORTS_STORAGE_DIR>/assinaturas/...`.

Garantias mantidas: a imagem nunca é versionada, nunca é devolvida por API,
nunca entra em JavaScript, log, fixture ou URL pública; a auditoria guarda
apenas hash e dimensões; a prévia nunca a carrega; novo cadastro revoga o
anterior sem apagá-lo; ativo ilegível ou com hash divergente **falha
fechado**.

**A imagem de assinatura não é, e não é tratada como, certificado
ICP-Brasil.** O estado `liberado`/`liberada_institucional` continua
deliberadamente distinto de `assinado`/`assinada`, e o PDF declara isso em
texto.

## 14. Pendências reais

Continuam abertas (nenhuma é bloqueio de engenharia deste marco):

1. **Aprovação clínica e jurídica** do texto do laudo e do rodapé.
2. **Decisão jurídica** sobre a suficiência da liberação institucional, ou
   contratação de assinatura qualificada (PAdES/ICP-Brasil).
3. **Ativo de assinatura manuscrita** da Dra. Ana Cristina (seção 13).
4. **`M15_REPORTS_VALIDATION_BASE_URL`** não definida: o laudo sai com o
   código textual, sem QR. Nenhuma URL foi inventada. *(O layout já foi
   ajustado para caber em 1 página **com** o QR quando ela for definida.)*
5. **Validação pública anônima** não implementada — decisão de privacidade
   em aberto; o endpoint exige sessão autenticada.
6. **Sem telas de cadastro** para `hora_exame`, `indicacao_clinica` e
   `people.sexo`. As colunas existem e o laudo as imprime; hoje o seed as
   preenche via ORM e, sem elas, o laudo imprime "não informada".
7. **Raiz privada de produção** não provisionada (o `ReadWritePaths` da unit
   systemd não contém o caminho); backup coordenado banco+storage e ensaio
   de restauração pendentes.
8. **Política de retenção** de negócio.
9. **Entrega ao paciente**: não existe fluxo. Os dois documentos ficaram
   prontos para integração via `GET /laudos/{id}/documentos`.
10. **Falha pré-existente** do gate de Marketing (seção 12).
11. **Screenshots automatizados do painel** indisponíveis nesta máquina
    (harness é Chrome-only; só há Firefox).

**Nada aqui autoriza uso em produção.** As flags versionadas continuam
`false`/`disabled` no código; o `.env` que liga o piloto é local e não
versionado.

## 15. Commit criado e git status final

```text
fix(m25.3): make the online spirometry report usable end to end
```

É o commit mais recente da branch — confirme com `git log --oneline -1`.
(O hash não é citado aqui de propósito: este relatório entra no próprio
commit, então qualquer hash escrito ficaria desatualizado.)

Commit único, somente com os arquivos desta etapa.
**Sem push, sem merge, sem deploy.**

`git status --short` ao final: árvore limpa (sem saída). O `.env`, o banco
local, o venv e a raiz privada de PDFs ficam fora do Git por `.gitignore`.

## 16. Como iniciar e testar no navegador

### Comando completo para iniciar

Dois terminais.

**Terminal 1 — API (loopback :8015):**

```bash
cd /home/adeildo/soprolife-site/painel-soprolife/nucleo-m15
M15_AUTH_SECRET="m25-3-seed-local-somente-dev-0123456789" .venv/bin/python -m app.serve
```

**Terminal 2 — painel + proxy (:8765):**

```bash
cd /home/adeildo/soprolife-site
python3 painel-soprolife/scripts/command-center-local-server.py
```

### URL local

```text
http://127.0.0.1:8765/painel-soprolife/
```

### Credenciais FICTÍCIAS de teste local

| Papel | E-mail | Senha |
| --- | --- | --- |
| Médica | `medica.teste@soprolife.local` | `teste-medica-m25-3` |
| Admin | `admin.teste@soprolife.local` | `teste-admin-m25-3` |
| Operacional | `operacional.teste@soprolife.local` | `teste-operacional-m25-3` |

São contas locais e descartáveis. Não usar fora deste notebook.

### Onde clicar

1. Entre com a **médica**.
2. No menu lateral, clique em **"Laudos de espirometria"** (também há um
   cartão com o mesmo nome no hub da página inicial).
3. Em **"Meus laudos"**, clique no laudo com status **Atribuído**
   (o mais recente, do paciente João da Silva Teste).

### Roteiro exato de teste

1. Confira o bloco **Paciente** (João da Silva Teste) e o novo bloco
   **Exame / Local de realização** — deve mostrar
   `Clínica Pastore — Unidade Ipanema`, `Rua Teixeira de Melo, 54 — Ipanema,
   Rio de Janeiro — RJ` e `Central: (21) 2508-9001`.
2. Veja o **PDF técnico (MIR)** no painel da esquerda.
3. Clique no botão curto **"DVO Leve"**.
4. Clique no complemento **"RBD+"** (só aparece porque o exame tem fase
   pós-BD).
5. Confira que o texto virou por extenso: *"Distúrbio ventilatório
   obstrutivo leve."* + *"Com resposta significativa ao broncodilatador."*
6. **Edite** o texto livremente na caixa.
7. Clique em **"Gerar prévia do laudo"** e confira o PDF à direita.
8. Clique em **"Assinar e liberar laudo"** → aparece a **confirmação
   consciente** em destaque âmbar. Só o botão de confirmação executa.
9. Confirme e veja o status virar **Liberado**, com **código de validação**.
10. Tente gerar nova prévia: deve ser **recusada** (conteúdo bloqueado).
11. Publique um **adendo** e confirme que a versão anterior é preservada.
12. No painel de documentos, baixe **separadamente** o *PDF técnico da MIR* e
    o *laudo SoproLife*.
13. Reduza a janela para largura de celular (~390 px) e confira que não há
    corte nem rolagem horizontal.

### Recriar o cenário

Se você liberar o laudo e quiser repetir o fluxo do zero:

```bash
cd /home/adeildo/soprolife-site/painel-soprolife/nucleo-m15
.venv/bin/python scripts/seed_m25_3_laudo_demo.py --confirmar --novo-laudo
```

Isso cria um exame e um laudo novos para o mesmo paciente fictício.

## 17. Instruções para continuar em outra sessão

**Ponto de partida:**

```bash
cd /home/adeildo/soprolife-site
git branch --show-current    # codex-m25a-search-console-reconciliation
git log --oneline -3
git status --short
```

**Reprovisionar o ambiente do ZERO (outra máquina):**

Quatro coisas NÃO vêm pelo Git e precisam ser recriadas: o `.venv`, o
`.env`, a raiz privada de PDFs e o banco local com o cenário fictício.

```bash
cd painel-soprolife/nucleo-m15

# 1) venv (requirements-dev.txt já inclui reportlab e pillow)
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# 2) raiz privada dos PDFs — absoluta, 0700, FORA do repositório.
#    Ajuste o caminho para o usuário da máquina em questão.
install -d -m 700 "$HOME/.soprolife-private/m25-reports"

# 3) .env local (gitignored). Conteúdo COMPLETO abaixo.
cat > .env <<ENV
M15_DATABASE_URL=sqlite:///./var/m15_nucleo.db
M15_ENV=dev
M15_API_HOST=127.0.0.1
M15_API_PORT=8015
M15_CORS_ORIGINS=["http://127.0.0.1:8765","http://localhost:8765"]
M15_DISPLAY_TIMEZONE=America/Sao_Paulo
M15_SESSION_COOKIE_NAME=soprolife_m15_sessao
M15_SESSION_COOKIE_PATH=/painel-soprolife/api/m15
M15_SESSION_COOKIE_SECURE=false
M15_SESSION_TTL_MINUTES=720
M15_MARKETING_REFRESH_QUEUE=./var/marketing-refresh-request.json
# Laudos: habilitado APENAS neste ambiente local de teste.
# O padrão versionado em app/config.py continua false/disabled.
M15_REPORTS_ENABLED=true
M15_REPORTS_MODE=pilot
M15_REPORTS_STORAGE_DIR=$HOME/.soprolife-private/m25-reports
# QR/validação deliberadamente NÃO definida — sem ela o laudo sai só com
# o código textual, e nenhuma URL é inventada.
# M15_REPORTS_VALIDATION_BASE_URL=
ENV

# 4) banco + cenário fictício
mkdir -p var
.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_m25_3_laudo_demo.py --confirmar
```

> O `.env` acima **não contém segredo nenhum**: em `M15_ENV=dev` o
> `M15_AUTH_SECRET` é opcional e gerado em memória. Por isso ele pode ser
> reproduzido em texto aqui. Em produção o segredo é obrigatório e **nunca**
> entra em documento ou repositório.

**O que NÃO se transporta entre máquinas:** o ativo de assinatura (quando
existir) é um arquivo privado fora do Git — precisa ser cadastrado de novo
pela tela admin em cada ambiente, a partir do PNG autorizado original.

**Verificar tudo de novo:**

```bash
.venv/bin/python -m pytest tests/test_m25_2_native_report.py -q -p no:randomly
.venv/bin/python scripts/test_m25_3_fluxo_completo.py
cd ../.. && M15_TEST_PYTHON="$PWD/painel-soprolife/nucleo-m15/.venv/bin/python" \
  bash painel-soprolife/scripts/quality-gate-safe.sh
```

**Leia antes de mexer:** `painel-soprolife/docs/m25-2-laudo-online-espirometria.md`
e os runbooks M24A/M24C/M24D.

**Mapa do código M25.3** (o mapa M25.2 continua valendo):

- identidade profissional gravável: `app/schemas.py::PhysicianProfileAdminUpdate`,
  `app/routers/reports.py::update_physician_account`, `app/normalize.py::crm_display_matches`
- local na tela médica: `app/routers/reports.py::get_report_document`,
  `js/report-workflow.js::renderExamAndLocation`
- ritmo vertical e cabeçalho do PDF: `app/services/report_native_pdf.py`
  (`_draw_header`, `draw_section_heading`, `draw_field_grid`,
  `_signature_block_height`)
- isolamento do `.env` nos testes: `tests/conftest.py`

**Próximos passos sugeridos, em ordem:**

1. Telas de cadastro para `hora_exame`, `indicacao_clinica` e `sexo`.
2. Cadastrar o ativo de assinatura quando autorizado (seção 13).
3. Definir `M15_REPORTS_VALIDATION_BASE_URL` e decidir se a validação será
   pública anônima.
4. Corrigir a dependência do gate de Marketing (instalar
   `requirements-google.txt` no python do gate, ou fazer o gate usar o venv).
5. Provisionar raiz privada + `ReadWritePaths` + backup coordenado antes de
   qualquer cogitação de go-live.

**Regras que continuam valendo:** não alterar flags de laudo sem autorização
explícita; não usar dado real de paciente; não afirmar que a liberação
institucional é ICP-Brasil; não commitar ativo de assinatura; não fazer
push/merge/deploy sem autorização separada.

---

# ADENDO M25.4 — Enxugamento visual, selo institucional e assinatura

Executado em **05/08/2026**, 11h20–14h00 (America/Sao_Paulo), na mesma
branch, a partir do retorno visual sobre as telas e o PDF.

## A. Pedido

1. Deixar menos poluído o painel **e** o laudo gerado.
2. Explicar (ou criar) a área de upload do exame.
3. Visual mais premium, com selos.
4. Colocar a imagem da assinatura.

## B. Arquivos que NÃO chegaram

Dois itens do pedido dependiam de arquivos que **não estavam disponíveis**:

- **modelo do Mais Laudos** — não veio na conversa nem está no disco;
- **foto da assinatura da médica** — idem.

Procurei em `~/Downloads`, no repositório e na raiz privada. Os únicos PDFs
em Downloads eram os dois que eu mesmo havia gerado.

**Não inventei nem desenhei assinatura.** O que fiz foi construir o caminho
completo para ela entrar em um passo, e provar que o caminho funciona (§F).

Sobre os "selos": o próprio escopo da M25.2 manda inspirar-se apenas na
**organização** do Mais Laudos, sem copiá-lo. Projetei um selo de identidade
própria da SoproLife — o que seria o correto mesmo com o modelo em mãos.

## C. Enxugamento do laudo

Saiu redundância, não informação:

| O que saiu | Por quê |
| --- | --- |
| `Documento:` e `Versão:` do bloco de validação | Já constam do cabeçalho **e** do rodapé de toda página — eram a terceira repetição |
| Cabeçalhos soltos de seção | Viraram título embutido no cartão (`draw_data_card`), removendo uma camada visual por bloco |
| Eco de rótulo ("PACIENTE › PACIENTE") | Virou "PACIENTE › NOME" e "EXAME › CÓDIGO" |
| Caixa da nota da MIR | Virou nota de rodapé: continua obrigatória, parou de competir com a conclusão |
| 2 linhas da declaração de liberação | Encurtada mantendo as três afirmações exigidas |

## D. Selo institucional

`draw_verification_seal()` — selo circular próprio: dois anéis, um "visto"
desenhado com retas e o texto do estado.

Regras que ele respeita:

- **Só em documento liberado.** Numa prévia seria mentira visual (testado).
- Fica **fora** da faixa reservada da assinatura — nunca por cima.
- Diz "LIBERAÇÃO INSTITUCIONAL", nunca "assinado digitalmente".

Um detalhe que custou uma iteração: as posições verticais do texto precisam
ser conferidas contra a **corda do anel interno** — num círculo a largura cai
conforme se afasta do centro, e "INSTITUCIONAL" vazou para fora na primeira
tentativa.

## E. Enxugamento do painel

| O que saiu | Por quê |
| --- | --- |
| Faixa de identidade separada | Repetia paciente/exame que o bloco de contexto já mostrava |
| Campo "Origem" com código técnico | `clinica_parceira · pastore-ipanema` não diz nada à médica; o local completo já aparece |
| Chips "Liberado" + "Conteúdo bloqueado" | Repetiam o chip de status — liberado **já implica** bloqueado |
| Flag "liberado" duplicada na fila | Mesmo motivo |
| Caixa âmbar de assinatura qualificada | Virou `<details>` colapsado; competia com o CTA de liberação |
| Placeholder de PDF com 420px de vazio | Reduzido a 96px — espaço reservado para conteúdo inexistente era a maior fonte de poluição |

O contexto agora é **um bloco de três colunas**: Paciente · Exame · Local.

## F. Assinatura — caminho completo, pronto para o arquivo real

### Interface criada (não existia)

Os endpoints existiam desde a M25.2, mas **não havia tela**: na prática não
dava para cadastrar a assinatura sem chamar a API à mão.

**Administração → Contas médicas → selecione a médica → "Assinatura
manuscrita (imagem)"**. A tela mostra se existe ativo (hash e dimensões),
recebe o PNG com confirmação consciente, e revoga o atual.

Não há preview da imagem **de propósito**: a API nunca devolve os bytes nem
o caminho. A ausência de preview é a garantia funcionando.

### Defeito encontrado e corrigido

O botão "Revogar" recebia **405 do próprio proxy**: `_M15_METHODS` só
permitia GET/POST/PATCH. Liberei **apenas** `DELETE` (PUT e HEAD seguem
bloqueados), com teste travando os dois comportamentos.

### Prova de que funciona

Cadastrei um **PNG geométrico sintético** (zigue-zague, sem semelhança com
assinatura de ninguém), liberei um laudo e confirmei a imagem impressa acima
da linha, ao lado do selo, sem sobreposição. Em seguida **revoguei o ativo e
apaguei o PNG** — deixar uma assinatura falsa vinculada ao nome real da
médica seria arriscado. O histórico foi preservado (`active: false`,
`revoked_at` gravado), como manda o desenho.

### Como cadastrar a assinatura real

1. Peça à Dra. Ana Cristina um **PNG com fundo transparente** (até 2 MiB,
   proporção entre 0,8:1 e 12:1).
2. Entre no painel como **admin**.
3. Administração → Contas médicas → selecione a médica.
4. Em "Assinatura manuscrita (imagem)": escolha o arquivo, marque a
   confirmação, clique em **Cadastrar assinatura**.

O arquivo vai para
`/home/adeildo/.soprolife-private/m25-reports/assinaturas/<perfil>/<id>.png`
(0600 sob raiz 0700), **fora do Git**. Nunca entra em repositório, log,
fixture, JavaScript ou URL pública.

### Sobre "assinar digitalmente"

Uma imagem de assinatura **não é** assinatura digital. Hoje o fluxo produz
**liberação institucional**: prova quem liberou, com autenticação individual,
qual texto, qual hash e quando. É honesto e rastreável — e o PDF diz
textualmente que não é ICP-Brasil.

Para assinatura **com validade jurídica plena**, o caminho é PAdES/ICP-Brasil
com provedor real (VIDaaS, BirdID, ou certificado A1/A3 da médica). A
arquitetura já está preparada: `get_signature_provider()` é o ponto de
extensão e continua devolvendo o provedor nulo.

> Vi um PDF assinado via **D4Sign** em `~/Downloads`. Se a SoproLife já usa
> D4Sign, ele é um candidato natural a provedor — mas isso é uma decisão
> comercial/jurídica sua, não de engenharia. Não abri o arquivo.

## G. Área de upload do exame — onde fica

**Ela já existia** e não foi encontrada porque só aparece para o papel
**operacional** — você estava logado como médica. A médica **não** faz
upload por desenho: quem recebe e atribui é a operação.

Caminho: entre como `operacional.teste@soprolife.local` →
**Laudos de espirometria** → painel **"Recebimento e atribuição — Novo PDF
original"**.

Fluxo: digite o código do exame (`ESP-000009`) → **Localizar exame** →
escolha a médica, a origem, **a unidade parceira** e o PDF → **Enviar e
atribuir**. O laudo aparece na fila da médica.

**Melhoria feita:** o campo de unidade era um **input de UUID digitado à
mão** — na prática ficava vazio e o laudo saía sem endereço. Agora é um
seletor real alimentado por `GET /unidades`.

## H. Testes

| Verificação | Resultado |
| --- | --- |
| `pytest` suíte completa | **931 passaram**, 22 skipped, **0 falhas** |
| `test_m25_2_native_report.py` | **37 passaram** (2 novos testes M25.4) |
| `test_m25_3_fluxo_completo.py` | **49 OK, 0 falhas** |
| `test_command_center_m15_proxy.py` | **46 passaram** (inclui o novo teste de DELETE) |
| `test-m24a-report-workflow.js` | Todos passaram |
| `node --check` em todo o JS | OK |
| `git diff --check` | OK |

### Duas regressões que meu próprio enxugamento causou

A suíte pegou o que a inspeção visual não pegaria: ao encurtar os textos eu
removi **ênfase com significado**, e os testes existentes estavam certos.

1. **`SEPARADO` em caixa alta** — que o PDF da MIR é documento separado é
   ponto clínico/legal, não formatação. Ênfase restaurada.
2. **`"Esta liberação não constitui, por si só, ..."`** — eu havia reescrito
   a abertura da frase legal. Restaurada ao texto exato.

Ambas foram corrigidas restaurando o conteúdo, **não afrouxando o teste**.

### Duas asserções desatualizadas

Estas sim eram do teste, e ambas apontavam defeitos reais:

1. **`"UI não promete liberação ou assinatura visual"`** exigia a frase
   *"Nenhum documento deste fluxo é assinado ou liberado nesta versão"*. Essa
   frase nasceu no M24C e **virou falsa na M25.2**: a interface afirmava que
   nada era liberado enquanto liberava. A asserção agora verifica a intenção
   real — a UI nunca pode vender a liberação como ICP-Brasil.
2. A verificação da ressalva ICP-Brasil no PDF era sensível a maiúsculas.

## I. Pendências desta etapa

1. **PNG da assinatura autorizada** — a tela está pronta e provada (§F).
2. **Modelo do Mais Laudos** — se você ainda quiser comparar organização,
   precisa enviá-lo; o selo atual é identidade própria.
3. **Decisão sobre assinatura qualificada** (PAdES/ICP-Brasil, D4Sign?).
4. **Screenshots automatizados do painel** seguem indisponíveis (harness é
   Chrome-only; a máquina só tem Firefox).
