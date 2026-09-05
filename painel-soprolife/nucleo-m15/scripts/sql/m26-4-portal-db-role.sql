-- M26.4 — papel de banco do PORTAL PÚBLICO, com alcance mínimo.
--
-- O processo do portal está na internet. Se ele for comprometido, a
-- pergunta que importa é: *o que o atacante consegue ler?* Rodá-lo com a
-- credencial da API interna responderia "tudo" — CRM, financeiro, usuários,
-- parceiros, a trilha de auditoria inteira. Este papel responde bem menos.
--
-- O que ele PODE:
--   * ler as colunas de `people` que a tela do paciente usa — e SOMENTE
--     elas: `cpf`, `observacao` e o resto do cadastro ficam ilegíveis;
--   * ler a data do exame;
--   * ler as versões de PDF (para localizar e conferir o arquivo);
--   * ler e atualizar as tabelas do próprio portal;
--   * INSERIR na trilha de auditoria — e ler de volta APENAS o `id` da
--     linha que acabou de inserir, porque `RETURNING` exige isso.
--
-- O que ele NÃO PODE, nem por engano de código:
--   * ler ou escrever em financial_entries, partners, users, leads,
--     followups, crm, report_documents, external_signature_batches…;
--   * ler o CONTEÚDO de audit_logs — `acao`, `entidade`, `detalhes`,
--     `user_id`, `ts_utc` são ilegíveis; só o `id` é visível;
--   * apagar nada, em lugar nenhum;
--   * criar tabela, alterar esquema ou conceder permissão.
--
-- Rode como superusuário do PostgreSQL, no banco soprolife_m15, DEPOIS de
-- aplicar a migration c3a9e15f7d84. Idempotente.
--
-- Uso:
--   sudo -u postgres psql -d soprolife_m15 \
--     -v senha="'<senha-gerada>'" -f m26-4-portal-db-role.sql

\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'soprolife_portal') THEN
    CREATE ROLE soprolife_portal LOGIN;
  END IF;
END
$$;

ALTER ROLE soprolife_portal WITH PASSWORD :senha NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOINHERIT NOREPLICATION CONNECTION LIMIT 20;

-- Nada por padrão. Cada permissão abaixo é uma decisão explícita.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM soprolife_portal;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM soprolife_portal;
REVOKE ALL ON SCHEMA public FROM soprolife_portal;
REVOKE ALL ON DATABASE soprolife_m15 FROM soprolife_portal;

GRANT CONNECT ON DATABASE soprolife_m15 TO soprolife_portal;
GRANT USAGE ON SCHEMA public TO soprolife_portal;

-- Leitura por COLUNA. O portal carrega dataclasses estreitas
-- (app/services/patient_results.py) exatamente para caber aqui.
GRANT SELECT (id, nome_completo, data_nascimento, arquivado)
  ON people TO soprolife_portal;
GRANT SELECT (id, person_id, data_exame)
  ON spirometry_exams TO soprolife_portal;
GRANT SELECT (id, report_document_id, kind, version_number, storage_path,
              sha256, size_bytes, page_count)
  ON report_document_versions TO soprolife_portal;

-- Tabelas do próprio portal.
GRANT SELECT ON patient_result_accesses TO soprolife_portal;
GRANT UPDATE (status, sent_at, first_access_at, last_access_at,
              last_download_at, download_count, failed_attempts, locked_until)
  ON patient_result_accesses TO soprolife_portal;
GRANT SELECT, INSERT ON patient_result_sessions TO soprolife_portal;
GRANT UPDATE (revoked_at) ON patient_result_sessions TO soprolife_portal;

-- Auditoria: escreve, nunca lê o CONTEÚDO. O portal registra o evento; quem
-- investiga lê pelo Command Center.
--
-- M26.7 — o `GRANT INSERT` sozinho não bastava, e o teste real do portal
-- pagou para descobrir. O ORM emite
--
--     INSERT INTO audit_logs (...) VALUES (...) RETURNING audit_logs.id
--
-- e no PostgreSQL a cláusula `RETURNING` é uma LEITURA: exige `SELECT` na
-- coluna retornada, mesmo que a linha lida seja a que você acabou de
-- escrever. Sem ela, toda autenticação de paciente morria com
-- `permission denied for table audit_logs`, o portal devolvia 500 e o
-- paciente lia "verifique sua internet".
--
-- `SELECT (id)` é o mínimo que faz o `RETURNING` passar, e é inofensivo: o
-- id é um contador. As colunas que contam a história — `acao`, `entidade`,
-- `entidade_id`, `user_id`, `detalhes`, `ts_utc` — continuam ILEGÍVEIS para
-- este papel. Um portal comprometido não consegue ler a trilha de auditoria
-- do sistema, que é a garantia que a M26.4 quis dar e que segue de pé.
--
-- O INSERT é por COLUNA, e não da tabela inteira, pela mesma razão: são
-- exatamente as sete colunas que `app/errors.py` e os serviços escrevem.
-- `id` fica de fora de propósito — quem o gera é a sequência, e ninguém
-- deve poder escolher o número da própria linha de auditoria.
GRANT INSERT (ts_utc, request_id, user_id, acao, entidade, entidade_id,
              detalhes)
  ON audit_logs TO soprolife_portal;
GRANT SELECT (id) ON audit_logs TO soprolife_portal;
GRANT USAGE ON SEQUENCE audit_logs_id_seq TO soprolife_portal;

-- Um objeto criado no futuro não ganha permissão retroativa.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE ALL ON TABLES FROM soprolife_portal;
