# SoproLife — Contexto Mestre

> Memória técnica humana do projeto. Uma sessão nova deve ler `CLAUDE.md`, este
> arquivo e as skills pertinentes (§12) e conseguir operar sem abrir relatórios.
> Os relatórios antigos estão em `docs/archive/reports/` e são **evidência
> histórica**, não especificação. Quando código e este documento divergirem, o
> código vence — e este documento deve ser corrigido no mesmo commit.
>
> Sem PII, senhas, tokens, IPs de tailnet ou identificadores privados. Use
> placeholders (`<VPS_TAILSCALE_IP>`, `<TOKEN>`).

Última atualização: 2026-10-03 (base `1e36d4c`).

---

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
| Repo na VPS | `/opt/soprolife/soprolife-site` (pull como **root**; `.git/index` é root 0600) |
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
  `/opt/soprolife/venvs/marketing`), IntegraICP/VIDaaS (assinatura). WhatsApp é
  só link `wa.me` para revisão humana — **não existe** envio automático.

## 4. Command Center — módulos

| Seção (`data-section`) | O que faz |
|---|---|
| Painel Geral (`overview`) | KPIs, briefing do dia, gráfico "Evolução mensal" (de mai/2026 em diante; leads + agendamentos = etapa atual) |
| Central de Cadastros | Pessoas, novo atendimento (CPF+nascimento), edição cadastral sem tocar conteúdo clínico, prontidão NFS-e por campo |
| CRM | Clínicas/contatos B2B; origem ≠ etapa; pessoa ≠ clínica |
| Leads e Agendamentos | Funil; conversão só por `isLeadConvertido()` |
| Tarefas | Follow-ups com consentimento e "não contatar" |
| Laudos (`laudos-espirometria`) | Fluxo técnico → médica → assinatura → entrega; estatísticas read-only; entrada visível só com `reports_enabled` |
| Marketing & SEO | Search Console/GA4 com frescor operacional |
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

## 7. NFS-e (M27–M70)

**Arquitetura.** `FiscalDocument` (um por exame/ambiente — `uq_fiscal_exam_environment`)
→ `FiscalPreparation` (snapshot imutável com fingerprint, valor, tomador,
fonte do valor) → `FiscalIssuanceRequest` (pedido humano, TTL 15 min) →
`FiscalAttempt` (durável, gravada **antes** da fronteira) → `FiscalArtifact`
(DPS assinada, NFS-e XML, só caminho+hash no banco). Numeração de DPS
append-only (`DpsNumberAllocation`).

**Emissão:**
1. Gestor/admin clica **"Emitir NFS-e"** na aba Fiscal (sem lote em produção;
   endpoints mock/lote respondem 409 em produção).
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
- **Tomador = paciente** (CPF válido, nome fiscal completo) em todos os fluxos.
  Descrição fiscal canônica, sem "Pastore", "50%", "repasse".

**Anti-duplicidade:** notas emitidas fora do sistema entram por
`register_external_issuance` (`scripts/nfse_m61_import_manual_nfse.py`, status
`import/external_manual`) e nunca ganham botão. NFS-e históricas já protegidas:
5 manuais importadas + 3 emitidas pela automação (8 `issued` em 02/10/2026).
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
4. `git pull --ff-only origin painel-soprolife-v01` como root.
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

- **Certificado A1 vence em 2026-11-07.** O worker exige 30 dias de margem:
  renovar e reinstalar os `.cred` **antes de 2026-10-08**, senão a emissão para.
- **SERPRO:** contratar Consumer Key/Secret e configurar no `m15.env` para ativar.
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

## 15. Estado atual (2026-10-03)

| Item | Estado |
|---|---|
| `origin/painel-soprolife-v01` / VPS | `1e36d4c` (evolução mensal), worktree limpo, health 200 |
| `origin/main` (site) | `a7d8b03` |
| Alembic | `b70c4a2e9d15` (M70) |
| Serviços | `soprolife-m15-api`, `soprolife-painel-loopback` (:8765), `soprolife-portal-resultados`, `soprolife-update-data.timer` (10 min), `soprolife-operational-refresh.timer` (6 h), `soprolife-nfse-production-worker.path` (active) |
| A1 | instalado como credential cifrado; vence 2026-11-07 |
| SERPRO | integrado, desligado (sem credenciais) |
| NFS-e | DIRECT/HOME/PASTORE em produção por botão; 8 documentos `issued`; políticas DIRECT, HOME, PASTORE |

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
| Painel (out) | `498c216`, `6aad6db`, `1e36d4c` | Refino visual, estatísticas de laudos, evolução mensal real |

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
