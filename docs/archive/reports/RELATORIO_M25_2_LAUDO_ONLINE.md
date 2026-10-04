# RELATÓRIO M25.2 — Laudo Online de Espirometria

## 1. Data e hora

Execução concluída em **05/08/2026**, madrugada (fuso America/Sao_Paulo).
Início dos trabalhos em 04/08/2026.

## 2. Diretório

```text
/home/fedorasurf/soprolife-worktrees/codex-m25a-search-console-reconciliation
```

## 3. Branch e commit inicial

- Branch: `codex-m25a-search-console-reconciliation`
- Commit inicial (HEAD antes do trabalho): `a7e0757 fix(marketing): reconcile Search Console snapshots`
- Branch principal do repositório: `main`

## 4. Estado inicial encontrado

- **Worktree limpo** (`git status --short` sem saída). Nenhuma alteração não
  commitada precisou ser preservada ou integrada.
- Não existe `AGENTS.md` neste repositório. As instruções vigentes estão em
  `CLAUDE.md` (raiz) e nas skills SoproLife carregadas na sessão.
- O módulo de laudos **já existia e estava avançado** — os marcos M24A,
  M24B, M24C e M24D já haviam sido implementados e commitados:
  - `dbeffd1 feat(m24): add secure spirometry report workflow`
  - `cb59470 feat(m24): complete secure report frontend`
  - `06ba4ac feat(m24): add doctor assignment and clinical queue`
  - `d9191d0 feat(m24): add controlled reports pilot` (+ correções)
- Relatórios/runbooks técnicos existentes lidos integralmente antes de
  qualquer edição: `docs/m24a-laudos-pdf-operacao.md`,
  `docs/m24c-medical-assignment-workflow.md`,
  `docs/m24c-signature-provider-decision.md`, `docs/m24d-reports-pilot.md`.
- **Não havia venv** neste worktree; foi criado em
  `painel-soprolife/nucleo-m15/.venv` a partir de `requirements-dev.txt`
  (o diretório é ignorado pelo Git).

**Decisão de continuidade:** o trabalho **continuou a implementação
existente**. Nenhum sistema paralelo foi criado, nenhum fluxo M24 foi
removido e nenhuma funcionalidade anterior foi desativada.

## 5. Arquitetura localizada

| Camada | O que é |
| --- | --- |
| Backend | FastAPI + SQLAlchemy 2 + Alembic (`painel-soprolife/nucleo-m15/`), PostgreSQL 16 em produção, SQLite em dev/teste |
| Autenticação | Token próprio (`app/security.py`), papéis explícitos; `medico` é papel isolado que **não** é herdado por admin/gestor/operacional |
| Autorização clínica | Exige simultaneamente papel `medico` explícito + conta ativa + perfil profissional ativo e verificado + atribuição ativa do documento |
| Armazenamento de PDFs | Raiz privada fora do Git (`M15_REPORTS_STORAGE_DIR`, `0700`), arquivos `0600`, árvore só de UUIDs, publicação atômica com revalidação (`app/services/report_storage.py`) |
| Geração de PDF anterior | `app/services/report_pdf.py` — **overlay** de texto sobre o próprio PDF da MIR (pypdf + reportlab) |
| Assinatura | `app/services/signature_provider.py` — adapter fail-closed; nenhum provedor ICP-Brasil conectado |
| Frontend | `painel-soprolife/js/report-workflow.js` + `css/report-workflow.css`, montados em `index.html`, consumindo a API via cliente autenticado `window.SoproM15` |
| Flags | Backend `M15_REPORTS_ENABLED`/`M15_REPORTS_MODE` (default off/disabled); frontend `data/m15-config.json` (piloto M24D já ativado no versionado) |

**Lacuna central identificada:** o fluxo existente compunha texto **sobre** o
PDF da MIR. O pedido M25.2 exige **dois documentos separados**. Foi essa a
diferença arquitetural que o marco resolveu.

## 6. Arquivos criados

| Arquivo | Papel |
| --- | --- |
| `nucleo-m15/app/services/report_conclusions.py` | Catálogo fechado de conclusões e complementos pós-BD; conversão abreviação → texto por extenso |
| `nucleo-m15/app/services/report_locations.py` | Resolução do local de realização como dado estruturado da unidade |
| `nucleo-m15/app/services/report_native_pdf.py` | Gerador nativo do PDF do laudo SoproLife (reportlab, do zero) |
| `nucleo-m15/app/services/native_report_builder.py` | Ponte entre domínio e gerador; código de validação, fuso, ativo de assinatura, adendos |
| `nucleo-m15/app/services/signature_asset.py` | Validação e leitura fail-closed do PNG de assinatura manuscrita |
| `nucleo-m15/migrations/versions/a3f1d7c25e90_m25_2_native_report_release.py` | Migration aditiva M25.2 |
| `nucleo-m15/tests/test_m25_2_native_report.py` | Suíte M25.2 (31 testes) |
| `docs/m25-2-laudo-online-espirometria.md` | Runbook do marco |

## 7. Arquivos alterados

| Arquivo | Alteração |
| --- | --- |
| `nucleo-m15/app/models.py` | Estado `liberado`; espécies `laudo_previa`/`laudo_liberado`/`laudo_adendo`; `signature_status` institucional; colunas de liberação e snapshots clínicos; tabelas `physician_signature_assets` e `report_addenda`; endereço em `partner_units`; `people.sexo`; `spirometry_exams.hora_exame`/`indicacao_clinica`; `physician_profiles.crm_display`/`especialidade` |
| `nucleo-m15/app/routers/reports.py` | Endpoints M25.2; `_store_new_version` estendido; correção passa a aceitar laudo liberado; nome de download distingue os dois documentos; filtro de fila inclui `liberado` |
| `nucleo-m15/app/schemas.py` | `ReportNativeDraft`, `ReportReleaseRequest`, `ReportAddendumCreate` |
| `nucleo-m15/app/serializers.py` | Campos M25.2 nas versões e no documento (`locked`, `released_at`, `validation_code`) |
| `nucleo-m15/app/config.py` | `reports_validation_base_url` (HTTPS obrigatório), `reports_signature_max_bytes` |
| `nucleo-m15/app/audit.py` | Allowlist estendida com as chaves técnicas M25.2 |
| `nucleo-m15/app/db.py` | `ReportAddendum` na guarda append-only da sessão |
| `nucleo-m15/app/services/report_storage.py` | Nomes públicos reutilizáveis (`read_private_file_bytes`, `assert_safe_storage_id`) |
| `nucleo-m15/app/services/signature_provider.py` | Constantes e evidência da liberação institucional (com `qualified_signature=False` explícito) |
| `js/report-workflow.js` | Workspace médico M25.2 completo |
| `css/report-workflow.css` | Estilos do novo fluxo + responsivo |
| `index.html` | Cache-busting dos assets (`v=2026080501`) |
| `scripts/test-m24a-report-workflow.js` | Duas asserções de string atualizadas |
| `nucleo-m15/tests/test_migrations.py` | Head fixada em `a3f1d7c25e90`; fixture de revisão antiga isolada do ORM |
| `nucleo-m15/tests/test_m24c_medical_workflow.py` | Código de erro renomeado |
| `nucleo-m15/tests/test_m24a_frontend_contract.py` | Conjunto exato de chaves da fila e código de erro |

## 8. Migrations

**`a3f1d7c25e90` — m25_2_native_report_release** (revises `c657f22bf857`).

Head única confirmada. Aditiva por construção: todas as colunas nascem
nullable e as CHECKs apenas **alargam** conjuntos permitidos, preservando
todo estado legado.

Conteúdo: endereço estruturado em `partner_units`; `people.sexo`;
`spirometry_exams.hora_exame`/`indicacao_clinica`;
`physician_profiles.crm_display`/`especialidade`; colunas e CHECKs de
liberação em `report_documents`; 16 colunas de snapshot em
`report_document_versions`; novas tabelas `physician_signature_assets` e
`report_addenda`; trigger append-only PostgreSQL para adendos.

**Backfill:** apenas o endereço institucional da Pastore Ipanema, e somente
quando a unidade **já existe** e ainda está sem logradouro. A migration
nunca cria parceiro, unidade, médico, paciente ou laudo.

**Downgrade:** falha fechado se existir qualquer laudo liberado, versão de
laudo nativo, adendo, ativo de assinatura ou assinatura institucional.
Exercitado com sucesso em SQLite (upgrade → downgrade → upgrade).

## 9. Funcionalidades implementadas

1. **Dois documentos separados.** O PDF da MIR permanece na versão
   `original`, intacto byte a byte, sem assinatura ou sobreposição. O laudo
   SoproLife é gerado nativamente. `GET /laudos/{id}/documentos` devolve os
   dois com caminhos de download distintos e nomes de arquivo diferentes.
2. **Catálogo fechado** de 17 conclusões + `PERSONALIZADO` e 5 complementos
   pós-BD, com textos exatamente conforme especificado.
3. **Prévia nativa** idêntica ao documento final (muda apenas a tarja de
   estado, a ausência de código de validação e a ausência da assinatura).
4. **Assinar e liberar** com confirmação consciente e verificação do hash do
   conteúdo conferido.
5. **Bloqueio pós-liberação**, com adendo append-only e documento corretivo.
6. **Ativo de assinatura manuscrita** administrativo e privado, opcional.
7. **Código e QR de validação** + endpoint autenticado de verificação.
8. **Local estruturado** por unidade parceira vinculada ao exame.
9. **Interface médica completa**, responsiva, no padrão visual do painel.

## 10. Fluxo completo da médica

1. Entra com usuário individual e senha própria (papel `medico` explícito).
2. Vê **“Meus laudos”** — somente os documentos atribuídos a ela.
3. Filtra por **Pendente / Em elaboração / Liberado / Assinatura pendente /
   Assinado**; a fila marca visualmente `corrigido` e `liberado`.
4. Abre o exame e visualiza o **PDF técnico original da MIR** em visualizador
   autenticado (Blob temporário, `private, no-store`).
5. Vê os dados do paciente e do exame (identidade só existe dentro do
   documento atribuído — nunca na fila, na URL ou na auditoria).
6. Seleciona a conclusão por **botões curtos**.
7. Seleciona o complemento **pós-broncodilatador**, oferecido apenas quando o
   exame tem essa fase.
8. **Edita livremente** o texto completo. Editar invalida a prévia conferida.
9. Clica em **“Gerar prévia do laudo”** e vê a prévia exata do PDF final.
10. Clica em **“Assinar e liberar laudo”** (CTA destacado).
11. O sistema abre a **confirmação consciente**, explicando que o conteúdo
    será congelado e que correções posteriores virão como adendo ou versão
    corretiva. Só o botão “Sim, assinar e liberar laudo” executa a ação.
12. O PDF final é gerado, hasheado e **bloqueado**: novas prévias são
    recusadas (`laudo_bloqueado_para_edicao`) e nova liberação também
    (`laudo_ja_liberado`).
13. Correção posterior cria **adendo** (versão nova, anterior preservada) ou
    **documento corretivo** separado.
14. Exame técnico e laudo médico são baixados **separadamente**.
15. O fluxo reutiliza integralmente pacientes, exames, clínicas, usuários,
    permissões e atribuições já existentes — nenhum cadastro duplicado.

## 11. Catálogo de conclusões

Implementado em `app/services/report_conclusions.py` e travado por teste
(`test_catalogo_do_modulo_bate_com_o_texto_exigido`).

| Botão | Texto no PDF |
| --- | --- |
| Normal | Espirometria dentro dos limites da normalidade. |
| DVO Leve | Distúrbio ventilatório obstrutivo leve. |
| DVO Moderado | Distúrbio ventilatório obstrutivo moderado. |
| DVO Mod. grave | Distúrbio ventilatório obstrutivo moderadamente grave. |
| DVO Grave | Distúrbio ventilatório obstrutivo grave. |
| DVO Muito grave | Distúrbio ventilatório obstrutivo muito grave. |
| DVR sug. Leve | Padrão sugestivo de distúrbio ventilatório restritivo leve. |
| DVR sug. Moderado | Padrão sugestivo de distúrbio ventilatório restritivo moderado. |
| DVR sug. Mod. grave | Padrão sugestivo de distúrbio ventilatório restritivo moderadamente grave. |
| DVR sug. Grave | Padrão sugestivo de distúrbio ventilatório restritivo grave. |
| DVR sug. Muito grave | Padrão sugestivo de distúrbio ventilatório restritivo muito grave. |
| DVM sug. Leve | Padrão sugestivo de distúrbio ventilatório misto leve. |
| DVM sug. Moderado | Padrão sugestivo de distúrbio ventilatório misto moderado. |
| DVM sug. Mod. grave | Padrão sugestivo de distúrbio ventilatório misto moderadamente grave. |
| DVM sug. Grave | Padrão sugestivo de distúrbio ventilatório misto grave. |
| DVM sug. Muito grave | Padrão sugestivo de distúrbio ventilatório misto muito grave. |
| DVI | Padrão sugestivo de distúrbio ventilatório inespecífico. |
| Personalizado | Caixa de texto livre (obrigatória) |

Complementos pós-broncodilatador:

| Botão | Texto no PDF |
| --- | --- |
| RBD+ | Com resposta significativa ao broncodilatador. |
| RBD− | Sem resposta significativa ao broncodilatador. |
| REV completa | Reversibilidade completa após broncodilatador. |
| REV parcial | Reversibilidade parcial após broncodilatador. |
| BD não realizado | *(não acrescenta frase)* |

**Nenhum grau é calculado ou pré-selecionado.** A decisão é integralmente da
médica, e o texto final é livremente editável antes da assinatura.

## 12. Medidas de segurança

- **RBAC:** liberar exige papel `medico` explícito + conta ativa + perfil
  ativo/verificado + atribuição ativa. Testado que admin, gestor,
  operacional e leitura recebem 403, e que outra médica recebe 404 (sem
  servir de oráculo de existência).
- **Sessão individual:** a médica só assina na própria sessão autenticada.
- **Sem assinatura automática:** confirmação textual obrigatória
  (`ASSINAR E LIBERAR`) + `expected_version_id` + `expected_text_sha256`.
  Prévia trocada por concorrência ou texto divergente são recusados.
- **Auditoria:** usuário, médica, data/hora (UTC, apresentada em
  America/Sao_Paulo), ID do exame, ID e versão do laudo, hash do texto
  assinado, hash SHA-256 do PDF final, código de validação. Eventos de
  prévia, liberação, adendo, cadastro e revogação de assinatura.
- **Minimização:** a allowlist recursiva de `app/audit.py` continua sendo a
  fronteira. Nenhum registro carrega paciente, texto clínico, filename,
  caminho absoluto ou bytes. Testado explicitamente.
- **Imutabilidade:** versões e adendos são append-only (trigger PostgreSQL +
  guarda de sessão SQLAlchemy). Versões anteriores sempre preservadas.
- **Arquivos privados:** raiz `0700` fora do Git, arquivos `0600`, árvore só
  de UUIDs, sem caminho previsível, sem URL pública. Toda leitura revalida
  contenção, symlink, tipo, modo, hash, tamanho e páginas.
- **Upload:** tipo, tamanho e integridade validados; PDF hostil recusado
  pelo validador M24B existente; PNG de assinatura validado por magic bytes,
  decodificação completa, dimensões e proporção.
- **Usuário comum não substitui** o PDF original nem a assinatura: o ativo é
  admin-only e a própria médica não cadastra o próprio ativo.
- **Validação:** endpoint autenticado, resposta idêntica para código
  inexistente e não liberado, sem paciente nem conclusão.

## 13. Tratamento da assinatura

**Busca pelo ativo autorizado:** procurei em todo o repositório e no
armazenamento configurado. **Não existe** nenhum ativo de assinatura da Dra.
Ana Cristina — nem no Git, nem em `assets/`, nem em qualquer raiz privada
provisionada (a raiz de laudos sequer está provisionada neste ambiente).
**Nada foi inventado, redesenhado ou simulado.**

**Estado atual do sistema:** plenamente funcional **sem** o ativo. Sem imagem
cadastrada, o laudo é liberado normalmente e sai apenas com o bloco
identificador da médica, com a área de assinatura reservada, limpa e do
mesmo tamanho. Testado (`test_laudo_funciona_sem_ativo_de_assinatura_cadastrado`).

**Onde cadastrar o arquivo autorizado, quando existir:**

```text
POST /api/v1/laudos/admin/medicos/{physician_profile_id}/assinatura
  multipart/form-data:
    arquivo     = <PNG com fundo transparente>
    confirmacao = "ATIVO DE ASSINATURA AUTORIZADO"
  autorização: papel admin
```

Destino físico (fora do Git, permissões `0600` sob raiz `0700`):

```text
<M15_REPORTS_STORAGE_DIR>/assinaturas/<physician_profile_id>/<asset_id>.png
```

**Garantias:** a imagem nunca é versionada, nunca é devolvida por API, nunca
entra em JavaScript, nunca ganha URL pública ou permanente, nunca aparece em
log (a auditoria guarda apenas hash e dimensões) e nunca entra em fixture ou
teste — a suíte gera um PNG geométrico sintético.

A assinatura só é desenhada **depois** da ação “Assinar e liberar”: a prévia
nunca a carrega (testado). Novo cadastro **revoga** o anterior sem apagá-lo,
para que laudos já liberados continuem apontando para o hash que usaram.
Ativo ilegível ou com hash divergente **falha fechado** e interrompe a
liberação (testado).

**Natureza jurídica declarada com honestidade:** o estado `liberado` /
`liberada_institucional` é deliberadamente distinto de `assinado` /
`assinada`. O PDF afirma textualmente que a liberação *“não constitui, por si
só, assinatura digital qualificada ICP-Brasil”*, e a evidência técnica grava
`qualified_signature: False`. O caminho PAdES/ICP-Brasil permanece intacto,
com `get_signature_provider()` ainda devolvendo o provedor nulo — a evolução
futura para certificado digital está estruturada e não está bloqueada.

**Bloco médico impresso** (dados fornecidos, cadastrados no perfil):

```text
Dra. Ana Cristina do Nascimento Cunha
Médica Pneumologista
CRM-RJ 52.62307-5   •   RQE 58224
```

`crm_display` guarda a apresentação humana; `crm_number` permanece
normalizado em dígitos, preservando a invariante e o trigger do M24C.

## 14. Testes executados e resultados

| Verificação | Resultado |
| --- | --- |
| `pytest` suíte completa (Núcleo M15) | **913 passaram**, 22 skipped, 12 falhas pré-existentes |
| `pytest tests/test_m25_2_native_report.py` | **31 passaram** |
| `pytest tests/test_migrations.py` | **11 passaram** |
| Quality gate `quality-gate-safe.sh` | **PASSOU — todos os checks OK** |
| `node --check` em todo o JS do painel | OK |
| `test-m24a-report-workflow.js` (M24C) | Todos os casos passaram |
| `git diff --check` | OK |
| Migration upgrade → downgrade → upgrade (SQLite) | OK |

**Cobertura da suíte M25.2 (31 testes):** catálogo fechado e conversão para
texto por extenso; exame com e sem pós-BD; complementos incompatíveis
recusados; conclusão personalizada; conclusão fora do catálogo; prévia
nativa separada do PDF da MIR; edição livre do texto; conteúdo obrigatório do
PDF; confirmação consciente; recusa de conteúdo divergente e de prévia
desatualizada; congelamento de hash/versão/código e bloqueio de edição;
declaração honesta sobre ICP-Brasil; PDF da MIR intacto byte a byte;
permissões (4 papéis + outra médica + perfil suspenso); adendo preservando a
versão anterior e sequência 2; adendo exigindo laudo liberado; correção após
liberação; validação por código sem expor paciente; laudo sem ativo de
assinatura; ativo admin-only e nunca devolvido em bytes; ativo inválido;
assinatura só após liberação; revogação preservando histórico; ativo
corrompido no disco falhando fechado; local Pastore Ipanema; local genérico
sem endereço inventado; dados ausentes do paciente; auditoria sem dado
clínico.

**Todos os pacientes, médicos, CRMs, exames e PDFs dos testes são
marcadamente sintéticos** (`TESTE APAGAR`, `Paciente Exemplo 001`). Nenhum
dado real foi usado.

### Falhas pré-existentes (não causadas por este trabalho)

1. **`tests/test_live_multisheet_reader.py` — 12 falhas.** Causa:
   `ModuleNotFoundError: No module named 'googleapiclient'`. A dependência
   está em `requirements-google.txt`, que não faz parte do venv do
   `nucleo-m15`. **Confirmado idêntico** rodando a suíte em cópia pristina do
   commit `a7e0757`.
2. **`scripts/test-m24a-browser-e2e.js` — falha no passo “Default-off real”**
   (`entries:3, hidden:false`) e, em seguida, no passo de administração.
   **Confirmado idêntico** em cópia pristina de `a7e0757`. É um problema
   ambiental/pré-existente do harness Chrome, não uma regressão.

Nenhuma regressão foi introduzida. As 4 asserções de teste que precisaram de
atualização foram todas de *string pinning* de contrato, alteradas junto com
a mudança intencional que as motivou (head da migration, conjunto exato de
chaves da fila, código de erro renomeado, versão de cache-busting).

## 15. Verificação visual

**PDF do laudo** — gerado e renderizado em imagem (`pdftoppm`), inspecionado
visualmente em três variantes:

- **liberado** (Pastore Ipanema, com pós-BD, observações, QR): 1 página,
  ~125 KB. Layout limpo, logo no cabeçalho, blocos de paciente e exame,
  conclusão em destaque, aviso do PDF da MIR, bloco de identificação e
  validação com QR legível, e **área de assinatura exclusiva e limpa**, sem
  qualquer sobreposição.
- **prévia**: tarja “PRÉVIA — DOCUMENTO NÃO LIBERADO”, sem código de
  validação, sem assinatura.
- **genérico sem unidade e sem pós-BD**: imprime “não informada/não
  informado” onde não há dado, sem inventar endereço.

Ajustes feitos a partir da inspeção: logo reamostrado e embutido uma única
vez (de 434 KB para ~125 KB por documento); ritmo vertical ajustado para o
laudo típico caber em **uma página**; bloco de assinatura movido para fechar
o documento; tipografia corrigida para preservar travessões, aspas e bullets
(WinAnsi) em vez de rebaixá-los.

**Telas do painel** — renderizadas em Chrome headless a 1100px e 390px:

- Desktop: cartões de documentos, 18 botões curtos de conclusão em grade,
  complementos pós-BD, textarea do texto final, CTA destacado, painel de
  confirmação consciente em destaque âmbar, formulário de adendo e chips de
  estado.
- Mobile (390px): grade de 2 colunas para os botões, botões full-width,
  alvos de toque ≥ 40px, **sem overflow horizontal**.

Ajuste feito a partir da inspeção: `m15-btn-primary` **não tinha estilo em
nenhum CSS do projeto**; foi definido com escopo restrito ao workspace de
laudos (para não alterar outros módulos), e o CTA “Assinar e liberar laudo”
ganhou destaque explícito.

## 16. Pendências reais

1. **Aprovação clínica e jurídica** do texto do laudo, do bloco de
   identificação e da declaração de liberação. Não é decisão de engenharia.
2. **Decisão jurídica** sobre a suficiência da liberação institucional para
   entrega ao paciente, ou contratação de assinatura qualificada
   (PAdES/ICP-Brasil) conforme `docs/m24c-signature-provider-decision.md`.
3. **Ativo de assinatura manuscrita** da Dra. Ana Cristina (ver §13).
4. **Raiz privada de storage** não está provisionada neste ambiente;
   `ReadWritePaths` da unit systemd não contém o caminho.
5. **Backup coordenado banco+storage** e ensaio de restauração.
6. **Política de retenção** de negócio (permanece preservação conservadora).
7. **Validação pública anônima** não implementada — o endpoint exige sessão
   autenticada. Expor verificação anônima é uma decisão de privacidade que
   não tomei por conta própria.
8. **`M15_REPORTS_VALIDATION_BASE_URL`** não definida: sem ela o laudo sai
   apenas com o código textual, sem QR. Nenhuma URL foi inventada.
9. **Falhas pré-existentes** do §14 (Sheets e browser E2E) continuam abertas.
10. **Entrega ao paciente:** a arquitetura atual **não possui** fluxo de
    entrega ao paciente. Os dois documentos foram deixados prontos para
    integração (`GET /laudos/{id}/documentos`), sem duplicar cadastros.

## 17. Dados ou ativos ainda necessários

- PNG da assinatura manuscrita autorizada (fundo transparente, proporção
  entre 0,8:1 e 12:1, até 2 MiB).
- Confirmação do texto exato da especialidade e do CRM formatado para o
  perfil da médica (`especialidade`, `crm_display`).
- Endereço/telefone institucionais das demais unidades, quando houver
  exames fora da Pastore Ipanema.
- Definição de `M15_REPORTS_VALIDATION_BASE_URL` (HTTPS) para habilitar QR.
- Indicação clínica e hora do exame passam a ser campos preenchíveis
  (`spirometry_exams.indicacao_clinica`, `hora_exame`); hoje ainda não há
  tela de cadastro para eles — o laudo imprime “não informada”.
- `people.sexo` idem: coluna criada, sem tela de preenchimento ainda.

## 18. Commit final

```text
0579e87 feat(m25.2): add native SoproLife spirometry report
```

Commit único, apenas com os arquivos deste trabalho.
**Sem push, sem merge, sem deploy** — conforme instruído.

## 19. Git status final

```text
$ git status --short
(vazio — árvore limpa)

$ git log --oneline -3
0579e87 feat(m25.2): add native SoproLife spirometry report
a7e0757 fix(marketing): reconcile Search Console snapshots
9b8ae96 fix(m24): harden reports pilot activation
```

## 20. Instruções exatas para iniciar e testar localmente

```bash
cd /home/fedorasurf/soprolife-worktrees/codex-m25a-search-console-reconciliation/painel-soprolife/nucleo-m15

# 1) venv (já criado nesta sessão; recrie se necessário)
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# 2) suíte M25.2
.venv/bin/python -m pytest tests/test_m25_2_native_report.py -q -p no:randomly

# 3) suíte completa (ignore o módulo de Sheets, que exige requirements-google)
.venv/bin/python -m pytest -q -p no:randomly --ignore=tests/test_live_multisheet_reader.py

# 4) migrations
rm -f /tmp/m25.db
M15_DATABASE_URL="sqlite:////tmp/m25.db" .venv/bin/alembic upgrade head
M15_DATABASE_URL="sqlite:////tmp/m25.db" .venv/bin/alembic current   # a3f1d7c25e90 (head)

# 5) quality gate (offline, sem rede/VPS)
cd ../..
M15_TEST_PYTHON="$PWD/painel-soprolife/nucleo-m15/.venv/bin/python" \
  bash painel-soprolife/scripts/quality-gate-safe.sh
```

Gerar um PDF de laudo de exemplo e olhar o resultado:

```bash
cd painel-soprolife/nucleo-m15
.venv/bin/python - <<'PY'
from datetime import date, datetime
from app.services.report_native_pdf import *
c = NativeReportContent(
    document_code="LAU-000123", version_number=2,
    patient=PatientBlock("Paciente Exemplo 001", date(1975,3,14), "feminino", "PES-000045"),
    exam=ExamBlock("ESP-000078", date(2026,7,30), "09:20", "dia", True,
                   "Tosse crônica e dispneia aos esforços."),
    location=LocationBlock("Clínica Pastore — Unidade Ipanema",
                           "Rua Teixeira de Melo, 54 — Ipanema, Rio de Janeiro — RJ",
                           "Central: (21) 2508-9001"),
    physician=PhysicianBlock("Dra. Ana Cristina do Nascimento Cunha",
                             "Médica Pneumologista", "52.62307-5", "RJ", "58224"),
    conclusion_text="Distúrbio ventilatório obstrutivo moderado.\nCom resposta significativa ao broncodilatador.",
    observations="Exame tecnicamente aceitável.",
    issued_at_local=datetime(2026,8,5,10,15), released=True,
    released_at_local=datetime(2026,8,5,10,15),
    validation_code="SL7K4M2Q9XB1",
    validation_url="https://painel.soprolife.local/validar/SL7K4M2Q9XB1",
)
open("/tmp/laudo-exemplo.pdf","wb").write(build_native_report_pdf(c))
print("gerado: /tmp/laudo-exemplo.pdf")
PY
pdftoppm -png -r 110 /tmp/laudo-exemplo.pdf /tmp/laudo-exemplo   # visualizar
```

Subir o painel completo em loopback (fluxo do projeto):

```bash
cd painel-soprolife/nucleo-m15
cp .env.example .env
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli criar-usuario --email voce@soprolife.local --nome "Seu Nome" --papel admin
.venv/bin/python -m app.serve            # API loopback :8015
# em outro terminal:
python3 painel-soprolife/scripts/command-center-local-server.py   # proxy :8765
```

Para exercitar laudos localmente o ambiente precisa de:
`M15_REPORTS_ENABLED=true`, `M15_REPORTS_MODE=pilot` e
`M15_REPORTS_STORAGE_DIR=<raiz absoluta privada 0700, fora do Git>`.

## 21. Instruções para continuar em outra sessão

**Ponto de partida:**

```bash
cd /home/fedorasurf/soprolife-worktrees/codex-m25a-search-console-reconciliation
git log --oneline -1          # deve mostrar 0579e87
git status --short            # deve estar vazio
```

**Leia antes de mexer:** `painel-soprolife/docs/m25-2-laudo-online-espirometria.md`
(contrato deste marco), mais os runbooks M24A/M24C/M24D já existentes.

**Mapa rápido do código M25.2:**

- catálogo: `app/services/report_conclusions.py`
- PDF nativo: `app/services/report_native_pdf.py`
- ponte domínio↔PDF: `app/services/native_report_builder.py`
- local: `app/services/report_locations.py`
- assinatura manuscrita: `app/services/signature_asset.py`
- endpoints: bloco “M25.2” ao final de `app/routers/reports.py`
- frontend: funções `renderConclusionPicker`, `renderNativeReportForm`,
  `renderReleaseAction`, `renderAddendumForm`, `renderDocumentsPanel` em
  `js/report-workflow.js`

**Próximos passos sugeridos, em ordem:**

1. Telas de cadastro para `indicacao_clinica`, `hora_exame` e `sexo`
   (colunas já existem; hoje o laudo imprime “não informada”).
2. Preencher `especialidade` e `crm_display` no perfil da médica.
3. Cadastrar o ativo de assinatura quando autorizado (§13).
4. Definir `M15_REPORTS_VALIDATION_BASE_URL` e decidir se a validação será
   pública anônima ou permanecerá autenticada.
5. Integrar os dois documentos ao fluxo de entrega ao paciente, quando ele
   existir — usar `GET /laudos/{id}/documentos`, sem duplicar cadastros.
6. Provisionar a raiz privada + `ReadWritePaths` + backup coordenado antes
   de qualquer cogitação de go-live.

**Regras que continuam valendo:** não alterar as flags de laudo sem
autorização explícita; não usar dado real de paciente; não afirmar que a
liberação institucional é ICP-Brasil; não commitar ativo de assinatura; não
fazer push/merge/deploy sem autorização na mesma tarefa.
