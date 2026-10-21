# SoproLife — Contexto Mestre

> Memória técnica humana do projeto. Uma sessão nova deve ler `CLAUDE.md`, este
> arquivo e as skills pertinentes (§12) e conseguir operar sem abrir relatórios.
> Os relatórios antigos estão em `docs/archive/reports/` e são **evidência
> histórica**, não especificação. Quando código e este documento divergirem, o
> código vence — e este documento deve ser corrigido no mesmo commit.
>
> Sem PII, senhas, tokens, IPs de tailnet ou identificadores privados. Use
> placeholders (`<VPS_TAILSCALE_IP>`, `<TOKEN>`).

Última atualização: 2026-10-04 (base `140f0e3`).

---

## Fundação Centro de Comando Life (decisão de 18/10/2026)

Consultar [CENTRO_COMANDO_LIFE_ARCHITECTURE.md](CENTRO_COMANDO_LIFE_ARCHITECTURE.md)
para a evolução futura. Uma pessoa jurídica, duas marcas (`soprolife`,
`eeglife`) — e **nenhum terceiro contexto**: o "Life Geral" previsto antes foi
revogado em 18/10/2026, e consolidação entre marcas, quando existir, será
relatório administrativo dentro de um módulo. Acesso à EEGLife depende do
papel `admin` da sessão; demais papéis, inclusive `medico`, ficam na
SoproLife.
Paciente corporativo compartilhado, autorização clínica por capacidade e
operação, razão financeiro compartilhado e consolidação por entidades/eventos.
Fase 2A: `eeg_enrollments` vincula a pessoa corporativa; `eeg_exams`,
`eeg_technical_files`, `eeg_report_dispatches`, `eeg_report_documents` formam o
fluxo próprio EEG. Central, agenda, realização, referência técnica, envio manual
para empresa/médico e recebimento/publicação do PDF possuem interface operacional.
Todas as rotas `/eeg/*` exigem `admin` no servidor; o CRM SoproLife não resolve
códigos EEG/LEG. Laudos respiratórios e regras comerciais existentes são preservados.

Migrations aditivas `c3f0a7b12e45` e `e2a0b6c719af`: sem classificação histórica,
sem backfill, downgrade só com tabelas EEG vazias. O portal usa acessos/sessões
EEG próprios, nascimento, expiração, revogação e releitura validada do PDF final.
A página própria vive em `/p/v1/eeg/`; configurar `M15_PORTAL_EEG_PUBLIC_BASE_URL`
na API interna com a URL HTTPS dessa página. Aplicar os grants mínimos de
`scripts/sql/m26-4-portal-db-role.sql` (bloco Fase 2A) após o upgrade. Substituir o PDF revoga o
link anterior. Esta fase não afirma verificação criptográfica de assinatura.

Traçado bruto permanece no equipamento/plataforma externa; só metadados e
referência no painel. PDF final fica no filesystem privado `eeg/laudos/`.
Financeiro, Fiscal, repasses e Marketing EEG seguem fora do escopo.
Estado de deploy e evidências: relatório da Fase 2A em Downloads (fora do Git).

Inicialização: `start-local.sh` exige o proxy autenticado em loopback;
`start-tailscale.sh` recusa o servidor estático antigo. Usar o acesso HTTPS
privado já provisionado para operação remota. A fundação da Fase 1 não alterou serviços na VPS.
As seções históricas abaixo não atestam o estado atual do ambiente remoto.

## 1. O que é a SoproLife

- **Empresa:** SoproLife Diagnósticos e Soluções em Saúde LTDA, Rio de Janeiro.
  Foco em **espirometria** (com e sem broncodilatador), laudo médico, atendimento
  domiciliar, teleconsulta, parcerias com clínicas (hoje a principal é a
  **Pastore**, Ipanema) e prospecção B2B/PCMSO.
- **Site público:** `soprolife.com.br`, GitHub Pages servindo a branch **`main`**
  (páginas estáticas: home, espirometria por região, telemedicina, `resultados/`
  do portal do paciente). **`main` não contém o painel.**
- **Command Center (painel privado):** `painel-soprolife/` na branch
  **`painel-soprolife-v01`**. Front-end estático (HTML/CSS/JS sem build) + núcleo
  **M15** (FastAPI + PostgreSQL) em `painel-soprolife/nucleo-m15/`.
- **Arquitetura geral:**

```
Navegador ──Tailscale HTTPS──► command-center-local-server.py (:8765, portão de sessão + proxy same-origin)
                                   ├── estáticos do painel (lidos do disco a cada request)
                                   └── /api/v1/* ──► soprolife-m15-api (FastAPI, 127.0.0.1:8015) ──► PostgreSQL (loopback)
Paciente ──HTTPS público──► nginx ──► soprolife-portal-resultados (app.portal_serve, papel de banco restrito)
Timer 10 min ──► update-local-data.sh ──► snapshots data/*.local.json (gerados do PostgreSQL)
Botão NFS-e ──► spool ──► soprolife-nfse-production-worker (oneshot, A1 cifrado) ──► SEFIN Nacional
```

## 2. Repositórios e ambientes

| Item | Valor |
|---|---|
| GitHub | `AdeildoJunior/soprolife-site` |
| Branch produtiva do painel | `painel-soprolife-v01` (fast-forward only) |
| Branch do site público | `main` (Pages). Nunca fundir as duas. |
| Repo local | `~/soprolife-site` (hoje com checkout em `main`; o painel é trabalhado em worktree) |
| Worktrees | só em `~/soprolife-worktrees/<missao>`, criados de `origin/painel-soprolife-v01` |
| VPS | Hostinger, Ubuntu 24.04, host `soprolife-painel-01`, acesso **só via Tailscale** (`ssh root@<VPS_TAILSCALE_IP>`; o check do Tailscale expira em ~30 min — peça o clique antes) |
| Repo na VPS | `/opt/soprolife/soprolife-site` (pull como **`soprolife`**; `.git` inteiro de `soprolife` desde 21/10/2026 — nunca rodar git como root ali) |
| Venv da API | `/opt/soprolife/venvs/m15` (exige `PYTHONPATH`/cwd `nucleo-m15`) |
| Segredos | `/opt/soprolife/secrets/m15.env` (root 0600; nunca impresso) |
| Banco | PostgreSQL 16 em loopback (SQLite só em dev/teste); tabela de auditoria `audit_logs` (`ts_utc`) |
| Health | **`GET http://127.0.0.1:8015/api/v1/health`** → 200 + `{"status":"ok"}` |
| Painel | `:8765`, publicado no tailnet por `tailscale serve` (HTTPS). Sem sessão, os `.js` respondem **401** — é o portão, não erro |
| Portal do paciente | nginx 443 só nos IPs públicos (o `tailscaled` ocupa a 443 do tailnet) |
| PDFs de laudo | `/opt/soprolife/private/reports` (0600 `soprolife`) |
| Artefatos NFS-e | `/opt/soprolife/private/nfse-production/` (worker `soprolife-nfse`) |
| Backups | `/opt/soprolife/backups/<missao>/` (root 0700/0600: `pg_dump -Fc`, `git bundle`) |

Consulta read-only em produção: script por stdin para
`sudo -u soprolife bash -c "set -a; . /opt/soprolife/secrets/m15.env; set +a; /opt/soprolife/venvs/m15/bin/python -"`
com cwd `nucleo-m15`, `app.db.get_engine()` e `SET TRANSACTION READ ONLY`.

## 3. Fonte canônica de dados

- **PostgreSQL é a única fonte operacional** (M23, julho/2026). Google Sheets e
  Apps Script são histórico/migração/rollback; não recriar fluxo de escrita neles.
- **Snapshots:** `scripts/update-local-data.sh` (serviço `soprolife-update-data`,
  timer de 10 min) gera `painel-soprolife/data/*.local.json` a partir do
  PostgreSQL (`nucleo-m15/app/snapshots.py`). Se a API/banco falhar, **não**
  sobrescreve snapshot válido. Os `*.local.json` são gitignored e **não** são
  revertidos por `git reset` — conte com isso num rollback.
- **Contrato de auditoria dos snapshots:** `scripts/audit_summary_contract.py`,
  compartilhado entre o exportador e `scripts/check-access.sh`.
- **`data/`:** públicos/seguros e o manifesto de boot `m15-config.json`
  (`enabled`, `reports_enabled`, `api_base`). Todo `data/*.json` novo nasce
  protegido pelo portão.
- **`data-private/`:** gitignored inteiro; nunca commitar, copiar para docs ou
  exibir. O checkout local `~/soprolife-site/painel-soprolife/` guarda a cópia
  local real (não versionada) — não apagar.
- **Integrações externas legítimas:** SEFIN Nacional (NFS-e), SERPRO Consulta
  CPF v3 (preparada, desligada), Search Console/GA4 (Marketing, venv próprio
  `/opt/soprolife/venvs/marketing`), IntegraICP/VIDaaS (assinatura), Instagram
  Graph API (convite ao paciente, preparada e desligada). WhatsApp é
  só link `wa.me` para revisão humana — **não existe** envio automático.
  Scraping de rede social é proibido: o Instagram só é lido por API oficial.

## 4. Command Center — módulos

| Seção (`data-section`) | O que faz |
|---|---|
| Painel Geral (`overview`) | KPIs, briefing do dia, gráfico "Evolução mensal" (de mai/2026 em diante; leads + agendamentos = etapa atual) |
| Central de Cadastros | Pessoas, novo atendimento (CPF+nascimento), edição cadastral sem tocar conteúdo clínico, prontidão NFS-e por campo |
| CRM | Clínicas/contatos B2B; origem ≠ etapa; pessoa ≠ clínica |
| Leads e Agendamentos | Funil; conversão só por `isLeadConvertido()` |
| Tarefas | Follow-ups com consentimento e "não contatar" |
| Laudos (`laudos-espirometria`) | Fluxo técnico → médica → assinatura → entrega; estatísticas read-only; entrada visível só com `reports_enabled` |
| Marketing & SEO | Search Console e GA4 na **mesma janela móvel de 60 dias encerrada ontem** (America/Sao_Paulo; `lookbackDays` do config privado, padrão 60, faixa 7–480), frescor operacional, tendência diária de impressões |
| Financeiro | `FinancialEntry`, conciliação, repasses médicos por competência, gráficos |
| Fiscal | Fila de produção NFS-e (gestor/admin), modal de confirmação, histórico |
| Parcerias | Pastore: fechamentos mensais, recebimento, regra de valor por vigência |
| Custos e Investimentos | Rateio entre sócios por campos estruturados (nunca inferir de texto) |
| Documentos | Documentos institucionais/POP |
| Automação CRM / Fontes de dados (`automacoes`) | Estado das automações e do frescor de cada fonte |
| Últimos lançamentos | Timeline derivada dos snapshots |
| Núcleo administrativo | Usuários, papéis, sessões (M15.3A); só admin |

Front-end: `index.html` + `js/*.js` com cache-buster `?v=AAAAMMDDNN` —
**toda mudança de JS/CSS exige bump** no `index.html`, senão o deploy não chega.

## 5. Pessoas, pacientes e atendimentos

- **`Person`**: uma pessoa, muitos exames/consultas. Identidade estável por
  `id` (UUID) + `public_code`. Nome sozinho **nunca** liga registros; telefone é
  só candidato; ambiguidade exige decisão humana registrada.
- **CPF** (`people.cpf`, único, opcional): criar pessoa com CPF existente →
  `409 cpf_ja_cadastrado` (sem "criar mesmo assim").
- **Busca local:** `POST /api/v1/pessoas/busca-cpf` (CPF só no corpo, nunca em URL).
- **Fluxo CPF + nascimento:** `POST /api/v1/pessoas/identificacao-assistida` →
  local primeiro; depois SERPRO (se configurado); grava só comprovante HMAC
  (confirmado/alterado), nunca CPF/nome em auditoria.
- **SERPRO:** provider `app/services/serpro_cpf.py`, endpoints fixos de produção,
  timeout finito, limite por usuário. **Estado: pronto e desligado**
  (`M15_SERPRO_CPF_ENABLED=false`, sem Consumer Key/Secret contratados).
- **Convite de Instagram (M26.31/M26.32):** `GET /laudos/{id}/convite-instagram`
  monta a mensagem de relacionamento e o `wa.me` do paciente. O perfil
  (`patient_results.INSTAGRAM_URL`) é fonte única; a publicação mais recente vem
  de `app/services/instagram_feed.py` (Instagram API with Instagram Login, host
  fixo `graph.instagram.com`, token no cabeçalho, cache de 45 min em memória).
  Fail-**safe**, não fail-closed: sem credencial, com a Meta fora ou com token
  vencido o convite cai para o perfil e o operador não vê erro. **Estado: pronto
  e desligado** (`M15_INSTAGRAM_FEED_ENABLED=false`, sem token).
- Datas: preservar texto original e precisão (mês/ano com dia assumido marcado).
- Exames reais nunca são usados em teste, fixture, screenshot ou demo.

## 6. Espirometria

- **Fluxos/locais:** `DIRECT` (SoproLife direto), `HOME` (domiciliar),
  `PASTORE` (clínica parceira). Modalidade no banco:
  `residencial | cowork | clinica_parceira`; o local do laudo deriva do exame.
- **Broncodilatador** (`broncodilatador` bool/None): obrigatório para fatos
  fiscais Pastore; nunca entrou em cálculo financeiro DIRECT.
- **Equipamento:** espirômetro **MIR**; o PDF técnico da MIR é o "documento 1",
  intocado, baixado separadamente.
- **Laudo:** gerado pelo núcleo (`report_native_pdf.py`), conclusão da médica,
  selo "Assinatura digital" institucional; rótulo ICP-Brasil só com prova
  criptográfica.
- **Assinatura:** externa (médica baixa, assina no VIDaaS, devolve);
  aceite automático com guardas documentais (`signature_acceptance.py`).
- **Portal de resultados:** token HMAC no fragmento da URL; banco guarda só
  `sha256(token)`; papel PostgreSQL `soprolife_portal` com GRANT por coluna.
- **QR/verificação pública** do laudo e nomes de download determinísticos.

## 7. NFS-e (M27–M72, M26.34–M26.35)

**Arquitetura.** `FiscalDocument` → `FiscalPreparation` (snapshot imutável com
fingerprint, valor, tomador, fonte do valor) → `FiscalIssuanceRequest` (pedido
humano, TTL 15 min) → `FiscalAttempt` (durável, gravada **antes** da fronteira)
→ `FiscalArtifact` (DPS assinada, NFS-e XML, só caminho+hash no banco).
Numeração de DPS append-only (`DpsNumberAllocation`).

**Duas naturezas de documento (M26.34).** `FiscalDocument.kind`:
- `single` — UM exame, tomador derivado do paciente. `spirometry_exam_id`
  preenchido, `uq_fiscal_exam_environment` valendo, `recipient_person_id` NULL.
  É o fluxo de M27–M71, inalterado.
- `grouped` — VÁRIOS exames (`FiscalDocumentItem`) e tomador **escolhido por
  humano**, que pode não ser paciente de nenhum deles. `spirometry_exam_id`
  NULL por construção; `recipient_person_id` obrigatório. Total = **soma dos
  itens** em `Decimal` (`amount_source='grouped_items.sum'`). CHECK
  `fiscal_document_shape` torna as duas formas mutuamente exclusivas.

**Ocupação de exame (M26.34).** "Este exame já foi faturado neste ambiente?" é
respondido por **`uq_fiscal_item_exam_environment`** em `fiscal_document_items`,
não mais pela coluna de `fiscal_documents` (NULL nos agrupados). Todo caminho
que cria documento reclama a ocupação — `nfse.prepare`, `prepare_group` e
`register_external_issuance` —, e a migration criou item de ocupação (valor
NULL, de propósito) para cada documento histórico.

**Emissão:**
1. Gestor/admin clica **"Emitir NFS-e"** na aba Fiscal (sem lote em produção;
   endpoints mock/lote respondem 409 em produção). Para o documento
   **agrupado (M26.35)** o botão fica no cartão do documento preparado e abre
   duas confirmações (o que será emitido → a consequência fiscal); depois disso
   é **o mesmo caminho**: `GET /producao/documentos/{id}/confirmacao`,
   `POST …/confirmar-emissao`, mesma campainha, mesmo worker. Não existe rota
   nem provider próprio do agrupado. Na prontidão, "Provedor no processo web:
   unavailable" é o processo web (nunca tem A1); quem emite é o executor.
2. Modal mostra dados com CPF mascarado; o segundo botão diz exatamente
   **"Confirmar emissão de R$ X"** — o servidor exige a mesma preparação, valor,
   frase e hash do tomador.
3. A API grava o pedido e cria a campainha `var/nfse-production-spool/<uuid>.issue`.
   **O processo web não tem certificado, senha nem gate de rede.**
4. `soprolife-nfse-production-worker.path` → `.service` **oneshot**, usuário
   `soprolife-nfse`, A1 e senha via **`LoadCredentialEncrypted=`**
   (`/etc/credstore.encrypted/*.cred`, chave do host).
5. Worker apaga a campainha, reivindica o pedido (UPDATE condicional + índice
   único), revalida tudo, faz **no máximo 1 POST**. Resultado incerto ⇒
   **reconciliação só por GET**. Rejeição é final (zero retry). Worker morto ⇒
   `interrupted`, conta como enviado, só reconcilia.

**Guard M69:** o credential do systemd chega com ACL POSIX (aparenta 0440); o
guard de permissão reconhece exatamente esse formato. `--self-check` abre o A1
sem rede e exige ≥ 30 dias de validade.

**Séries:** automação usa **série `00001`** com allocator durável. A série
**`70000`** é a do emissor manual do Portal e **nunca** é usada pela automação.

**Fluxos:**
- **DIRECT/HOME:** valor = exatamente um `FinancialEntry` de receita própria de
  espirometria, Recebido, BRL, > 0. Nunca o repasse médico.
- **PASTORE:** valor = parcela SoproLife **R$ 109,50**, lida só por
  `partner_pricing.resolve_regra_fiscal_por_exame` (por dia de serviço; regra
  mais recente vigente; empate ⇒ bloqueio). Só **com broncodilatador**.
  Política `SOPROLIFE-PRODUCTION-PASTORE-v1` **prospectiva desde 2026-10-02**:
  exames anteriores ficam bloqueados ("conferir nota manual"). Nenhum
  `FinancialEntry` é criado e nenhum settlement é alterado.
- **Tomador = paciente** (CPF válido, nome fiscal completo) em todo documento
  `single`. Descrição fiscal canônica, sem "Pastore", "50%", "repasse".
- **Tomador ≠ paciente** só em documento `grouped`: a identidade fiscal exigida
  passa a ser a do **tomador** (os bloqueios de identidade do paciente não se
  aplicam — `evaluate(recipient_identity=False)`), e o paciente segue tendo de
  existir e não estar arquivado. **PASTORE não agrupa**: ali o valor é repasse
  de parceria e o tomador é a clínica. Grupo exige coerência de data,
  município, política, fluxo e broncodilatador; incoerente é **recusado sem
  escrever nada**, porque o item ocupa o exame. A política fiscal continua
  declarando `recipient: 'service_person'` — descreve o fluxo individual e não
  foi alterada; terceiro pagador é decisão humana registrada no documento.

**Anti-duplicidade:** notas emitidas fora do sistema entram por
`register_external_issuance` (`scripts/nfse_m61_import_manual_nfse.py`, status
`import/external_manual`) e nunca ganham botão. NFS-e históricas já protegidas:
5 manuais importadas + 3 emitidas pela automação (8 `issued` em 02/10/2026);
em 12/10/2026, 12 documentos de produção `issued` e nenhum `pending` (a nº 15 de
ESP-000063 importada pelo M71). Em 21/10/2026: **20 documentos de produção
`issued`** (19 `single` + 1 `grouped`; 14 emitidos pela automação + 6
importados), nenhum `pending`, última DPS automática nº 14.

**Primeira NFS-e agrupada (21/10/2026, M26.35):** documento `38aa9324`,
ESP-000077 + ESP-000078 (R$ 250,00 cada), tomadora terceira pagadora (não é
paciente de nenhum item), serviço em 20/10/2026, total **R$ 500,00**. Emitida
por clique humano no cartão do documento agrupado: pedido `bcc3e0a9`,
**1 POST / 0 GET**, DPS nº 14 série `00001`, artefatos `dps_signed_xml` +
`nfse_xml`, `fiscal_validity=True`. Os dois exames ficaram com 0 documento
individual e 1 item cada (ocupação pelo agrupado). Prova de que o agrupado
usa a mesma rota, o mesmo pedido e o mesmo worker das notas individuais.
**Exceção M71 (nota manual depois de recusa pré-envio):** se o botão já criou o
documento de produção e o worker recusou **antes** da fronteira, a importação
reaproveita esse documento `pending` (mesmo id, sem segundo documento) — só se
`never_sent_blockers` provar que nunca saiu: zero attempt/artifact/número de DPS,
todo pedido `kind=issue` em `refused`/`expired` com 0 POST e 0 GET, sem
`operation_id`/`external_id`, sem campainha no spool nem `post-intent.json` no
runtime do worker (diretório ilegível ou não configurado = recusa). Os pedidos
recusados ficam intactos; entra uma `FiscalPreparation` `EXTERNAL` nova + a
attempt `import`. O M61 aceita `issued_on` por nota (data real de emissão;
sem ele, cai na data do serviço).
**Nunca emitir no cadastro** — o cadastro só mostra prontidão (M67).

## 8. Financeiro

- `FinancialEntry` (`LAN-…`) é a **única fonte monetária**. CRM, clínico e
  parcerias referenciam só por IDs técnicos; sem PII em descrição/summary.
- **DIRECT/HOME:** `POST /atendimentos` cria a receita na mesma transação do
  exame com o valor digitado (não há tabela de preço).
- **PASTORE:** exame Pastore com bloco financeiro ⇒ 422
  `pagamento_direto_pastore_proibido`. Receita entra só por
  `POST /pastore/fechamentos/{id}/receber` (fechamento mensal → recibo).
- **Recebimento ≠ repasse:** `partnerships.modelo_recebimento /
  valor_recebido_por_exame / vigencia_inicio` = dinheiro que entra (único leitor:
  `partner_pricing.py`, há teste). `modelo_repasse / percentual_repasse /
  valor_repasse_fixo` = dinheiro que sai. Mudança de valor = **nova vigência**.
- **Repasses médicos** por competência, baseados em laudos efetivos (M26.9/M26.10).
- **Settlements** (`PartnerSettlement`) com sequência por competência.
- Correção monetária = novo lançamento auditável; nunca sobrescrever valor.
- Competência exibida usa `today_local()` (fuso de Brasília), nunca UTC.

### 8.1 Custos & Investimentos / rateio dos sócios (M26.21)

- Fonte: `painel-soprolife/data-private/custos-investimentos.local.json` —
  **livro-caixa único por item** (`movimentos`), gitignored, fora do PostgreSQL.
- O resumo que o painel lê (`data/custos-investimentos-summary.local.json`) é
  **gerado**, nunca editado à mão:
  `python3 painel-soprolife/scripts/generate-custos-investimentos.py`.
  Todo agregado (totais, rateio por sócio, rateio por item, parcelamentos) é
  **derivado** dos movimentos. Mudou um movimento ⇒ rodar o gerador.
- Status de movimento: `pago` (saída de caixa confirmada), `pendente`,
  `vencido_aguardando_confirmacao` (o vencimento passou e **ninguém confirmou o
  débito** — nunca "pago" por ter chegado o dia do vencimento) e
  `baixada_por_desconto` (posição liquidada **sem** saída de caixa).
- `responsavel` = quem administra o item; **quem pagou** vem do `pagador` de
  cada movimento. Nunca inferir pagador do responsável nem de texto livre.
- Bonificação/desconto **não é pagamento**: vai para
  `desconto_ou_baixa_sem_saida_caixa`, derivado do `valor_nominal` dos
  movimentos `baixada_por_desconto` (cuja saída de caixa é 0,00).
- Item com `historico_confirmado: false` emite `null` em `pago` **e** em
  `pendente` — o painel mostra "—". `R$ 0,00` só quando o zero é fato confirmado.
- Os dois `.local.json` são gitignored: o deploy do Git **não** os atualiza. Na
  VPS, rodar o reconciliador/gerador lá (ambos versionados) depois do pull.

## 9. Laudos

- **Fluxo:** técnico registra exame + PDF MIR → atribuição à médica → médica
  escreve conclusões (conclusões rápidas + texto livre) → laudo liberado →
  assinatura externa → aceite → pronto para entrega → entregue / portal.
- **Estado:** `report_documents.status` **fica `liberado` para sempre**; rotule
  por `estado_entrega` (`_estado_de_entrega_do_documento`). Fila ativa da médica
  = não superado e sem `_tem_assinado_vigente()`.
- **Versões:** corretivas criam nova versão; a anterior vira "superada";
  substituir PDF técnico gera versão vigente nova (M26.13–M26.16).
- **Downloads:** nomes determinísticos; proxy do painel serve PDF com
  `Content-Disposition` correto.
- **Permissões:** papel `medico` vê só a própria fila; admin/gestor conferem;
  texto clínico não vai para logs nem para o navegador fora da bancada.

## 10. Segurança

- **Autenticação:** sessão do núcleo (M21/M25.23); portão em
  `scripts/panel_access_gate.py` — `.md`, `.py`, `.env`, `.pfx`, `nucleo-m15/`,
  `data-private/`, `scripts/` e `systemd/` nunca saem por HTTP.
- **RBAC:** `admin ⊃ gestor ⊃ operacional ⊃ leitura`; `medico` é separado e
  admin **não** implica médico (autoria clínica exige a linha explícita de papel médico).
  UI escondida não é autorização; o servidor decide.
- **Rede:** API e banco só em loopback; acesso remoto só pelo tailnet; dado
  clínico real só sob HTTPS.
- **PII:** guardas tudo-ou-nada nos snapshots; `audit.py::ALLOWED_KEYS` descarta
  chave fora do vocabulário e trunca valores (~128 chars).
- **Credenciais:** A1 só como credential cifrado do systemd; senha digitada
  pelo humano; SERPRO/IntegraICP via `SecretStr` no `m15.env`. Nunca em Git,
  argv, log ou relatório.
- **Backups:** privados, validados (`pg_restore -l`, `git bundle verify`), fora do Git.
- **Fail-closed** em tudo: credencial ausente ⇒ recusa; health exige 200 + `status=ok`
  com espera finita; ambiguidade ⇒ bloqueio, não palpite.

## 11. Deploy

1. Implementar em worktree próprio; gates da área tocada
   (`pytest`, `node --check`, `scripts/quality-gate-safe.sh`, `scripts/check-access.sh`).
2. `git fetch origin`; o commit deve estar **diretamente por cima** de
   `origin/painel-soprolife-v01`; push **fast-forward** apenas. Nunca
   `--force`, nunca `reset --hard` destrutivo, nunca reescrever histórico publicado.
3. Na VPS (com autorização explícita): conferir HEAD e worktree limpo, health,
   estado do worker/spool; **backup** em `/opt/soprolife/backups/<missao>/`
   (`pg_dump -Fc` + `git bundle`).
4. `git pull --ff-only origin painel-soprolife-v01` como `soprolife`, depois de
   conferir `find .git ! -user soprolife | wc -l` = 0 e escrita nos diretórios tocados.
5. Se houver migration: `alembic upgrade head` como `soprolife` com `m15.env`.
6. **Reiniciar `soprolife-m15-api` só se mudou Python/modelo/migration.**
   Front-end puro **não** precisa de restart (o servidor `:8765` lê do disco),
   basta o cache-buster. O worker NFS-e é oneshot: carrega o código novo sozinho.
7. Verificar `/api/v1/health` (200, `status=ok`), journal limpo, e o conteúdo
   servido (CSS ou arquivo em disco, já que `.js` dá 401 sem sessão).

## 12. Skills

Skills do repositório (`.claude/skills/`, permanentes — não apagar/renomear):

| Skill | Finalidade | Quando usar |
|---|---|---|
| `soprolife-painel-command-center` | Autoridade de arquitetura, dados, segurança, migração e deploy do painel/M15 | Qualquer mudança de backend, modelo, identidade, finanças, auth ou deploy |
| `soprolife-ux-premium` | Qualidade visual e de interação (grids, formulários, acessibilidade, datas) | Redesign/CSS/layout; combine com a anterior se tocar lógica |
| `soprolife-etapa-segura` | Protocolo de etapa única: diagnóstico, escopo fechado, checks, diff, parar | Toda tarefa técnica executada por IA |
| `soprolife-safe-dev` | O que nunca commitar, separação site/painel, checks obrigatórios | Antes de considerar qualquer mudança pronta |
| `soprolife-audit-patterns` | Auditoria append-only, uma linha por ação de negócio | Funcionalidade que escreve dados/trilha |
| `soprolife-finance-costs` | Custos & Investimentos, rateio entre sócios por campos estruturados | Telas/cálculos de custos e rateio |
| `soprolife-b2b-pcmso-crm` | Funil B2B: origem vs etapa, lead ativo vs convertido, pessoa vs clínica | CRM, leads, contatos B2B |
| `soprolife-marketing-seo` | SEO, Search Console, GA4, páginas locais | Marketing e site público |
| `soprolife-medical-docs-pop` | POPs, protocolos, textos médicos institucionais | Documentos operacionais/médicos |
| `soprolife-sheets-sync` | Sheets/Apps Script/ADC com dry-run e sem PII | Só tarefas legadas com Google Sheets |
| `soprolife-systemd-safe` | Criar/alterar units e timers com backup e rollback | Qualquer unit/timer local ou da VPS |
| `soprolife-vps-safe` | O que roda sem pedir (read-only) na VPS e o que exige aprovação | Qualquer acesso à VPS |
| `soprolife-vps-deploy-safe` | Deploy seguro via Tailscale, conferindo estado antes | Deploy na VPS |
| `soprolife-review-pack` | Pacote de revisão para o GPT em `~/Documents/SoproLife/_REVISOES_GPT` | Ao fechar etapa que vai para revisão externa |
| `soprolife-ai-orchestrator` | Divisão de papéis entre IAs; nunca duas IAs no mesmo diff | Ao decidir qual IA executa/revisa |

Na branch `main` (site) existe também `soprolife-site-html-repair`, para
reparo estrutural de HTML do site público.

Skills de usuário (`~/.claude/skills/`, fora do repo): `soprolife-router`,
`soprolife-painel-command-center`, `soprolife-ux-premium`,
`soprolife-ai-operacao-segura`, `soprolife-orquestracao-ias`,
`soprolife-google-sheets-seguro`, `terminal-first-safe-agent`.

## 13. Invariantes — não quebrar

1. PostgreSQL é a fonte operacional única; `FinancialEntry` é a fonte monetária única.
2. Nunca PII, segredo, `data-private/`, dump, `.env`, `.pfx` ou banco no Git.
3. API e banco só em loopback; painel só via tailnet; dado clínico só em HTTPS.
4. Push só fast-forward em `painel-soprolife-v01`; `main` (site) e painel nunca se fundem.
5. NFS-e: só por clique humano com confirmação exata; no máximo 1 POST por
   documento; incerto ⇒ GET; rejeição é final; nunca emitir no cadastro; nunca lote em produção.
6. O processo web nunca tem A1, senha nem gate de rede.
7. Série automática `00001`; série `70000` é manual e intocável.
8. Pastore: valor fiscal só da regra da parceria (R$ 109,50 vigente), só com BD,
   tomador = paciente, política prospectiva desde 2026-10-02.
9. Notas importadas/manuais nunca ganham botão; NFS-e emitida nunca volta a `pending`.
10. Recebimento ≠ repasse; mudança de valor = nova vigência.
11. Nome não liga pessoas; CPF duplicado não é criado.
12. `report_documents.status` não descreve a etapa — use `estado_entrega`.
13. Auditoria append-only com vocabulário fechado; nunca CPF/nome/token nela.
14. Migrations com downgrade que recusa apagar evidência fiscal; backup antes de upgrade em produção.
15. Fail-closed: credencial ausente, regra ambígua ou fato mudado ⇒ bloqueio.
16. Mudança de JS/CSS ⇒ bump de cache-buster.

## 14. Pendências atuais

- **Certificado A1 vence em 2026-11-07.** A margem do worker foi reduzida para
  **2 dias, temporariamente** (M72, 14/10/2026: drop-in
  `certificate-margin.conf` + `/opt/soprolife/secrets/nfse-certificate-margin.env`,
  fora do `m15.env` de propósito). A emissão **para em 2026-11-05**: renovar o A1
  e reinstalar os `.cred` antes disso; depois, voltar a margem para 30 dias.
- **`.git` da VPS com objetos do root:** em 21/10/2026, 640 objetos `root:root`
  0400 (fetches como root em 19/10) impediram o `git fetch` como `soprolife`;
  corrigido com `chown -R soprolife:soprolife .git`. Antes de cada deploy,
  `find .git ! -user soprolife | wc -l` deve dar 0. Git na VPS roda como
  `soprolife` (§2, §11).
- **SERPRO:** contratar Consumer Key/Secret e configurar no `m15.env` para ativar.
- **Instagram (M26.32):** conectar a conta `@soprolife` como profissional a um app
  Business da Meta e gerar o token de longa duração (escopo
  `instagram_business_basic`); configurar `M15_INSTAGRAM_ACCESS_TOKEN` +
  `M15_INSTAGRAM_FEED_ENABLED=true` no `m15.env`. O token vence em 60 dias e
  precisa de renovação (`/refresh_access_token`) — vencido, o convite volta ao
  perfil sozinho, sem erro na tela.
- **Primeiro clique Pastore:** aguarda o primeiro exame Pastore elegível
  (≥ 2026-10-02, com BD, CPF e município) e autorização humana.
- **NFS-e manual nº 3 (18/08, Pastore):** não importada; conferir o tomador e
  registrar via `nfse_m61_import_manual_nfse.py`.
- **Leads:** o Núcleo não recebe lead novo desde 25/07/2026; não há fonte do
  evento de agendamento.
- **Falhas herdadas do quality gate:** `test-m21-auth-crm-nav` (cache-buster de
  Marketing) e `test-m25-26-fluxo-espirometria`; pytest
  `test_rubrica_real_nao_esta_versionada` (falso positivo) e
  `test_live_multisheet_reader` (sem `googleapiclient` no venv).
- **Backup cifrado (`age`) dos artefatos NFS-e** na VPS: planejado, não implementado.
- **Local:** worktrees com trabalho não integrado listados em §17.

## 15. Estado atual (2026-10-21)

| Item | Estado |
|---|---|
| `origin/painel-soprolife-v01` / VPS | VPS em `a51bd74` (M26.35 — botão "Emitir NFS-e" do documento agrupado), worktree limpo, `.git` 100% `soprolife`, health 200 |
| `origin/main` (site) | `ac0a1a8` |
| Alembic | `b5e2f7a91c38` (M26.34) |
| Serviços | `soprolife-m15-api`, `soprolife-painel-loopback` (:8765), `soprolife-portal-resultados`, `soprolife-update-data.timer` (10 min), `soprolife-nfse-production-worker.path` ativos; `soprolife-operational-refresh.timer` **inativo** na conferência de 21/10 (não investigado) |
| A1 | instalado como credential cifrado; vence 2026-11-07; margem temporária de 2 dias (M72) ⇒ emissão até 2026-11-05 |
| SERPRO | integrado, desligado (sem credenciais) |
| Instagram (último post) | integrado, desligado (sem token Meta) — convite usa o perfil |
| NFS-e | DIRECT/HOME/PASTORE e **agrupada** em produção por botão; 20 documentos `issued` (19 single + 1 grouped), 0 `pending`; última DPS nº 14 |

## 16. Histórico arquitetural resumido

| Marco | Commit | O que mudou |
|---|---|---|
| M15.1–M15.3B (jul) | `692a80e`, `14da85b`, `2f87cf8` | Núcleo FastAPI+PostgreSQL, admin/RBAC, hardening loopback |
| M15.5–M15.7 (jul) | `6159916`, `7346c31` | Go-live controlado, migração multiaba Sheets → PostgreSQL |
| M20 (jul) | `7c32222` | Atendimento único, canônico Pastore |
| M23 (jul) | `6e18fff`…`9a1d4e5` | PostgreSQL como fonte exclusiva; contrato de auditoria único |
| M24–M25 (ago) | — | Laudos online, assinatura externa, portão de autenticação (M25.23), regularização histórica |
| M26.1–M26.5 (ago–set) | `6e965db`, `bb90b06`, `0ea720d`, `89a7a36` | Selo de assinatura, financeiro automático, regra Pastore, portal do paciente + hardening |
| M26.9–M26.29 (set) | `31ccded`, `fa3de29`, `f7b159c`, `d0709c9`, `1f8801e` | Repasses médicos, versões de laudo, status pós-assinatura |
| M27–M62 (set) | merge `72546e6` | Subsistema NFS-e: restrito, DPS, mTLS, numeração durável, `fiscal_validity` |
| M64–M65 (23/09) | `55c7bb7`, `d280aad` | Primeira NFS-e real de produção (1 POST, série 00001 nº 1) |
| M66–M69 (set) | `3ce038c`, `dd8c65a`, `13e474a`, `e087615` | Botão no Command Center + worker oneshot, prontidão no cadastro, CPF/SERPRO, guard ACL do A1 |
| M70 (02/10) | `1a35d4a` | NFS-e Pastore (parcela R$ 109,50), migration `b70c4a2e9d15` |
| M71 (12/10) | `65c0092` | Importação da NFS-e manual nº 15 (ESP-000063) sobre documento `pending` provadamente nunca enviado |
| M72 (14/10) | — (só VPS) | Margem do A1 reduzida para 2 dias, temporária, por drop-in fora do Git |
| M26.34 (21/10) | `c5fb870` | NFS-e agrupada: `kind='grouped'`, `fiscal_document_items`, tomador próprio, total = soma dos itens, migration `b5e2f7a91c38` |
| M26.35 (21/10) | `a51bd74` | Botão "Emitir NFS-e" no documento agrupado com confirmação dupla, pelo mesmo endpoint/worker; primeira nota agrupada emitida (R$ 500,00, DPS nº 14) |
| Painel (out) | `498c216`, `6aad6db`, `1e36d4c` | Refino visual, estatísticas de laudos, evolução mensal real |
| Marketing (04/10) | `140f0e3` | Search Console + GA4 de 28 para 60 dias, subtítulo da tendência pelo período real |

## 17. Organização do ambiente (registro de 2026-10-03)

- Relatórios versionados: 51 `RELATORIO_*.md` saíram da raiz para
  `docs/archive/reports/` (ver o README de lá).
- Pasta humana `~/SoproLife_Programacao/`: `README.md`, `RELATORIOS/`
  (`INDEX.md`, `ULTIMO_RELATORIO.md`, `CRITICOS/`), `ARQUIVO/` (tar.gz +
  `SHA256SUMS` de `~/soprolife-relatorios`), `ARQUIVO_LEGADO/` (dados únicos de
  worktrees removidos). Relatórios versionados são lidos pelo Git
  (`git -C ~/soprolife-site show origin/painel-soprolife-v01:docs/archive/reports/<arquivo>`),
  sem checkout de referência.
- Worktrees que ficaram e por quê: ver `~/SoproLife_Programacao/README.md`.
