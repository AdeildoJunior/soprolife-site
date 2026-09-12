# M26.9 — Edição de cadastro e repasses médicos

Data da execução: 12/09/2026 (America/Sao_Paulo)  
Branch oficial: `painel-soprolife-v01`  
Commit da implementação: `36ef04804b263e0c4c036655bf232032c4d46f75`

## Resultado

A Central de Cadastros passou a oferecer a ação clara **Editar cadastro**. O
formulário altera somente nome completo, telefone, e-mail, data de nascimento
e sexo. IDs, CPF, estado do cadastro, observações, exames, laudos, autoria e
PDFs não fazem parte do contrato desse endpoint.

O Financeiro passou a ter a área compacta **Repasses médicos**, separada das
receitas SoproLife, dos recebimentos de pacientes e dos acertos Pastore. Ela
mostra a competência selecionada, total a pagar, total pago, tabela por médica
e histórico de competências.

Nenhum repasse real foi criado, nenhum pagamento foi marcado e nenhum cadastro
real foi alterado durante implementação, testes ou smoke.

## Edição administrativa do cadastro

- Endpoint dedicado: `PATCH /api/v1/pessoas/{id}/cadastro`.
- Campos permitidos: `nome_completo`, `telefone`, `email`,
  `data_nascimento` e `sexo`.
- O payload é estrito: IDs e conteúdo clínico não são aceitos.
- Cada campo realmente alterado gera uma linha append-only em `audit_logs`,
  com usuário (`user_id`), data/hora (`ts_utc`), nome do campo, valor anterior
  e valor novo.
- A conta `contato@soprolife.com.br` tem autorização explícita para a edição.
- Administradores também têm autorização. Em produção, tanto a conta
  institucional quanto Luiz possuem papel `admin`.
- O papel `medico` não recebe a permissão administrativa.
- Versões de documentos guardam o blob e o hash do PDF já produzido. A edição
  atua apenas em `people`/`person_contacts`; portanto PDFs assinados, laudos,
  exames, autoria e IDs existentes permanecem intactos. Somente documentos
  gerados no futuro consultam o cadastro corrigido.

## Regra dos repasses

O marco adotado é **laudo concluído**, medido exclusivamente por
`report_documents.released_at`:

- conta uma vez cada `ReportDocument` que tenha `released_at` na competência e
  `released_physician_profile_id` igual à médica responsável;
- a competência usa o mês de `released_at` no fuso configurado do painel
  (`America/Sao_Paulo`), com limites convertidos para UTC;
- não há filtro pelo status atual;
- atribuído, rascunho/em elaboração ou assinatura pendente sem `released_at`
  não contam;
- se o laudo concluído evoluir para assinado ou entregue, continua contado uma
  única vez na competência original;
- assinatura e entrega não mudam competência nem autoria do repasse.

O valor unitário não foi inferido nem pré-cadastrado. O gestor o informa no
fechamento da competência. O backend recalcula a quantidade elegível e calcula
`quantidade × valor unitário`; se a tela estiver desatualizada, o fechamento é
recusado para nova conferência.

## Persistência e proteções

A migration `d6a9f20c3e41` cria somente `physician_transfers`, sem backfill. O
registro guarda médica, competência, quantidade de laudos usada no cálculo,
valor unitário, total de referência, valor efetivamente pago, data do
pagamento, status, autor do fechamento e autor/horário do pagamento.

Há unicidade de médica + competência e constraints de quantidade/valores e de
coerência Pendente/Pago no banco. A API também responde conflito explícito em
duplicidades e impede registrar pagamento se a quantidade de laudos tiver
mudado depois do fechamento. O histórico conserva o snapshot de cada
competência.

## Testes e verificações

- Suíte focada M26.9: **8 passed**.
- Suítes afetadas de pessoas, Central, laudos e Financeiro: **193 passed**.
- Segurança: **20 passed**.
- Execução ampla em PostgreSQL 16: **1741 passed, 9 skipped** e quatro falhas
  restritas à bancada de migration histórica/head esperada. O teste histórico
  passou a inserir dados pelo schema daquela revisão, a head esperada foi
  atualizada e o rerun exato ficou **4 passed**.
- Upgrade/check/downgrade/re-upgrade em SQLite: passou, uma única head e sem
  drift.
- Upgrade/check/downgrade/re-upgrade em PostgreSQL 16: passou, uma única head e
  sem drift.
- `compileall`, `node --check` dos dois JavaScripts, `git diff --check` e
  `check-access.sh`: passaram.

As fixtures são sintéticas. Os testes cobrem sobrenome e auditoria antes/depois,
Luiz e admin, conta institucional, bloqueio da médica, imutabilidade de IDs e
PDF, limites de fuso, estados não elegíveis, status posterior ao `released_at`,
multiplicação, fechamento pendente, pagamento, duplicidade, histórico,
quantidade desatualizada e isolamento de `FinancialEntry`/`PartnerSettlement`.

## Integração e produção

- Worktree isolado:
  `/home/fedorasurf/soprolife-worktrees/codex-m26-9-cadastro-repasses`.
- Base limpa: `e2e04e8be5c2a0779ecf8b2e3dd6a1438a927e9b`.
- Integração da implementação: fast-forward de `origin/painel-soprolife-v01`
  para `36ef04804b263e0c4c036655bf232032c4d46f75`.
- Backup anterior ao pull/migration:
  `/opt/soprolife/backups/m26-9-pre-20260912-122550`.
- Dump PostgreSQL custom: 667.474 bytes, SHA-256
  `ee835188d8c33c81b96413ccf10ce8d87fcc014660cdfcda308dde6a76587b4c`;
  validado com `pg_restore --list`.
- Bundle do repositório anterior ao deploy validado com `git bundle verify`.
- Produção avançou por fast-forward e a migration passou de
  `c3a9e15f7d84` para `d6a9f20c3e41`; `alembic check` respondeu sem novas
  operações.
- Restart mínimo: somente `soprolife-m15-api.service`.
- Health direto `127.0.0.1:8015`, proxy loopback e painel/health via Tailscale:
  HTTP 200; quatro serviços relevantes ativos e zero warnings recentes da API.
- Smoke de proteção: as duas rotas novas sem autenticação responderam 401.
- Pós-migration: `physician_transfers=0` e auditorias de edição M26.9 = 0.
- Antes/depois: `financial_entries=20`, soma `6145.29`; `partner_settlements=4`,
  soma `2080.50`. Nenhuma alteração em receitas ou Pastore.
- Portal público de resultados permaneceu saudável via HTTPS (HTTP 200).

