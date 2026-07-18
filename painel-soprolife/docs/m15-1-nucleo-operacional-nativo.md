# M15.1 — Núcleo Operacional Nativo SoproLife

Data: 17/07/2026 · Status: checkpoint técnico validado; ativação e dados reais
continuam sujeitos a validação humana ·
Branch: `fable-m15-native-core` (worktree isolado, base `1731d5a`)

## 1. Arquitetura

### Antes (até M14)

```
Centro de Comando (HTML/JS estático)
  → JSONs locais (data/ e data-private/)
  → sincronizadores Python (ADC)
  → Google Sheets + Apps Script  ← backend operacional de fato
```

### Depois (M15, coexistindo)

```
Centro de Comando
  ├─ telas antigas  → JSONs/Sheets (INTACTAS)
  └─ Núcleo M15 (feature flag) → proxy de mesma origem (:8765)
                                  → API SoproLife (127.0.0.1:8015)
                                  → PostgreSQL 16 (SQLite em dev/teste)
```

O Sheets deixa de ser o backend definitivo. Planilhas e CSVs antigos
permanecem como origem histórica, fonte de migração, backup, conferência e
rollback. Nenhuma integração antiga foi apagada ou modificada.

### Decisões de negócio aplicadas

- **PCMSO fora da operação ativa**: sem funis, filtros, modalidades ou
  indicadores PCMSO no núcleo novo; o importador rejeita linhas PCMSO com
  motivo explícito (`pcmso_fora_da_operacao`) e o histórico permanece nas
  fontes legadas.
- **Modalidades ativas**: residencial/domiciliar, cowork, clínica parceira
  (+ teleconsulta como modalidade de consulta).
- **Cadastro central**: uma pessoa = um registro canônico (`people`),
  independente de ser lead, paciente, ambos, desistente ou encaminhada.
- **Datas incompletas**: `06/2026 → 2026-06-01`, `dezembro/2026 → 2026-12-01`,
  `2026-08 → 2026-08-01`, `2026 → 2026-01-01`, sempre preservando
  `*_original`, `*_precisao` (`dia|mes|ano|desconhecida`) e `*_dia_assumido`.
- **IDs em duas camadas**: UUID interno (autoridade: servidor) + código
  público sequencial `PES-000001`, `ESP-000001`, `CON-`, `CLI-`, `UNI-`,
  `CTT-`, `PAR-`, `ENC-`, `INT-`, `FUP-`, `LAN-`, `LEA-` (emitidos por
  `code_sequences` com lock de linha). IDs legados preservados em
  `legacy_source`/`legacy_id`/`import_batch_id` + tabela `legacy_aliases`.
- **Identidade**: telefone é candidato de correspondência, nunca prova;
  nome nunca funde; toda ambiguidade vira `identity_candidates` pendente de
  decisão humana (`migration_decisions`).

## 2. Modelo de dados (27 tabelas de domínio)

- **Núcleo**: `users`, `roles`, `user_roles`, `audit_logs`, `people`,
  `person_contacts`, `consents` (histórico rastreável por canal),
  `legacy_aliases`, `code_sequences`.
- **Operação**: `leads`, `spirometry_exams`, `consultations`,
  `interactions`, `followups` (índice parcial único: 1 pendente por
  pessoa/tipo/origem).
- **Parceiros**: `partners`, `partner_units`, `partner_contacts`,
  `partnerships`, `partner_referrals` (Pessoa↔Encaminhamento↔Parceiro↔
  Unidade↔Atendimento, com laudo, valores e repasse), `partner_settlements`.
- **Financeiro**: `financial_entries` (fonte canônica, SEM PII — só IDs
  técnicos de exame/consulta/encaminhamento), `payment_allocations`,
  `partner_transfers`.
- **Migração**: `import_batches`, `import_rows`, `identity_candidates`,
  `migration_decisions`.

Migração Alembic única: `migrations/versions/f2efc6e45b12_m15_nucleo_inicial.py`.
As 27 tabelas de domínio, mais `alembic_version`, têm paridade com os modelos;
upgrade, `alembic check`, downgrade e novo upgrade foram validados em
PostgreSQL 16 real e SQLite.

## 3. Segurança

- Tokens HMAC assinados com validade; senhas PBKDF2-HMAC-SHA256 (210k iterações);
- papéis hierárquicos: admin > gestor > operacional > leitura;
- fail-closed: `M15_ENV=prod` sem `M15_AUTH_SECRET`, bind inseguro ou origem
  CORS HTTPS válida não sobe; payloads com campos extras são rejeitados
  (`extra=forbid`); login não diferencia usuário inexistente de senha errada
  e limita tentativas por origem/identidade;
- financeiro rejeita campos nome/telefone/CPF e detecta padrões de
  CPF/telefone em texto livre;
- auditoria append-only (`audit_logs`) na aplicação e por trigger PostgreSQL,
  com allowlist sem PII, `request_id` validado/limitado em toda resposta,
  timestamps UTC + exibição America/Sao_Paulo;
- API somente em loopback (`127.0.0.1:8015`), CORS restrito ao painel local,
  `/docs` desativado em prod;
- nenhuma credencial em código; `.env.example` sem segredos.

## 4. Follow-up semiautomático

Regras:

- exame realizado → vencimento = data do exame + 6 meses (meses de
  calendário, com ajuste de fim de mês);
- consulta realizada → data da consulta + 6 meses;
- lead sem atendimento → data de retomada manual (precedência) ou data do
  primeiro contato;
- encaminhamento de parceiro → data agendada/encaminhamento, com
  `controlado_por_parceiro` quando o parceiro controla o follow-up e bloqueio
  quando `autorizacao_contato_soprolife=false`.

Filas (`GET /followups/fila`): **Atrasado**, **Retomar hoje**, **Retomar
nesta semana**, **Aguardando data**, **Concluído** — e **Não contatar**
nunca aparece (contado apenas como "ocultos"). Sem consentimento de WhatsApp
o item aparece com aviso. Duplicação é impedida por índice parcial único +
verificação de serviço. Conclusão registra autor/UTC; nova tentativa
incrementa contador e reagenda.

WhatsApp: somente follow-up pendente, consentimento explicitamente concedido
e autorização SoproLife permitem que `GET /followups/{id}/whatsapp-url` monte
`https://wa.me/<numero>?text=…` para **revisão humana** (nada é enviado); a
interação só é registrada após `POST /followups/{id}/whatsapp-confirmacao`.
Consentimento desconhecido, bloqueado ou revogado falha fechado; itens
controlados pelo parceiro sem autorização SoproLife não geram link.

## 5. API (prefixo /api/v1)

`/health` · `/auth/token` · `/pessoas` (+contatos, +consentimentos) ·
`/leads` · `/espirometrias` · `/consultas` · `/parceiros` · `/unidades` ·
`/contatos-parceiros` · `/parcerias` · `/encaminhamentos` ·
`/acertos-parceiros` · `/interacoes` · `/followups` (+fila, +concluir,
+nova-tentativa, +whatsapp-url, +whatsapp-confirmacao) · `/lancamentos` ·
`/repasses` · `/importacoes` (dry-run via API; execução real só por CLI) ·
`/identidade/candidatos` · `/auditoria`.

As listagens públicas operacionais usam paginação (`pagina`/`tamanho`≤100),
filtros e respostas estruturadas; filas internas de manutenção possuem limite
explícito. Criações com `idempotency_key` onde faz sentido
(exames, consultas, lançamentos) — repetir a chave devolve o mesmo registro.

## 6. Importador (CLI, dry-run padrão)

Ver `nucleo-m15/README.md`. Garantias: SHA-256 por fonte; contagens
total/válidas/rejeitadas/ambíguas; dry-run com **zero escrita**; transação
por lote com rollback completo em erro; reexecução idempotente (arquivo já
executado → `ja_importado`; linhas já importadas → `ja_existente`);
relatórios JSON+Markdown; candidatos de identidade sem fusão silenciosa;
dados reais nunca em fixtures/Git (fixtures são 100% sintéticas).

## 7. Interface M15 (experimental)

Feature flag em `data/m15-config.json` (`enabled:false` por padrão — painel
intocado) ou `localStorage.soproM15='on'`. `api_base` usa a rota relativa de
mesma origem `/painel-soprolife/api/m15`. O token de sessão existe somente em
memória e nunca é salvo no `localStorage`. Arquivos novos: `js/m15-nucleo.js`,
`css/m15.css` (prefixo `m15-`, sem tocar no cascade existente); única mudança
em arquivo existente: 1 tag `<script>` no `index.html`.

Áreas: Visão geral, Pessoas, Leads, Espirometrias, Consultas, Clínicas e
Parceiros, Pacientes de Parceiros, Follow-up, Financeiro, Migração,
Auditoria — com cards de resumo, filtros, estados vazios, formulários
essenciais (pessoa, parceiro), fila de follow-up com botão WhatsApp
(revisão humana + confirmação), datas com "(dia assumido)", selo
"M15 experimental" e identificação de dados sintéticos.

## 8. Backup e rollback

- O núcleo escreve apenas em seu próprio banco (`nucleo-m15/var/` no dev;
  PostgreSQL dedicado depois). Sheets/JSONs legados intocados = rollback
  natural: desligar a flag e parar a API devolve o painel ao estado M14.
- Backup dev: copiar `var/m15_nucleo.db`; PostgreSQL: `pg_dump` do banco
  `soprolife_m15`.
- Importações: cada lote registrado com SHA-256; reverter = restaurar dump
  anterior ao lote (fase experimental) — nunca apagar linhas manualmente.

## 9. Kit de publicação futura na VPS (M15.2)

O M15.2 versiona o proxy seguro, unit systemd, deploy fail-closed, backup e
runbook do primeiro usuário. Produção não foi implantada. O procedimento usa
PostgreSQL apt local, sem Docker/Podman; mantém a API exclusivamente em
`127.0.0.1:8015` e o navegador acessa pelo proxy da porta 8765. Consulte
`m15-2-proxy-seguro-deploy-vps.md`. Seed/importação real continuam fora do
deploy e exigem mudança separada.

## 10. Substituição gradual do Sheets

Fase A (atual): M15 experimental com dados sintéticos; Sheets segue dono da
operação. Fase B: importação real (dry-run → execute) e operação em paralelo
(dupla digitação assistida ou conferência semanal). Fase C: M15 vira fonte
primária de pessoas/atendimentos/follow-up; Sheets em modo leitura/backup.
Fase D: telas antigas apontam para a API (adapters), Apps Script congelado.
Cada fase exige validação humana explícita antes da próxima.

## 11. Validação técnica e pendências conhecidas

- Em 17/07/2026, o script oficial confirmou PostgreSQL major 16, executou
  upgrade/check/downgrade/upgrade e concluiu 153 testes (incluindo os 6
  exclusivos de PostgreSQL); o container efêmero foi removido ao final;
- SQLite concluiu 147 testes e ignorou apenas os 6 marcados como PostgreSQL;
- decisão humana dos `identity_candidates` existe na API e registra
  `migration_decisions` sem merge automático; ainda não há tela dedicada;
- endpoints de settlements/allocations são de leitura/registro básico —
  fechamento de período de repasse ainda é manual;
- formulários da UI cobrem pessoa e parceiro; exame/consulta/encaminhamento
  nascem via API/importador nesta fase;
- autenticação é para usuários internos via CLI; sem UI de gestão de usuários;
- CSVs reais não estavam no worktree — importador validado com fixtures
  sintéticas; fornecer os arquivos privados via `data-private/` e rodar
  dry-run antes de qualquer `--execute`;
- implantação, operação em produção e importação de arquivos reais não foram
  executadas nesta etapa; a feature flag permanece desligada por padrão.
