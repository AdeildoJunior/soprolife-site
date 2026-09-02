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
--   * INSERIR na trilha de auditoria.
--
-- O que ele NÃO PODE, nem por engano de código:
--   * ler ou escrever em financial_entries, partners, users, leads,
--     followups, crm, report_documents, external_signature_batches…;
--   * LER audit_logs (só insere);
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

-- Auditoria: escreve, nunca lê. O portal registra o evento; quem investiga
-- lê pelo Command Center.
GRANT INSERT ON audit_logs TO soprolife_portal;
GRANT USAGE ON SEQUENCE audit_logs_id_seq TO soprolife_portal;

-- Um objeto criado no futuro não ganha permissão retroativa.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE ALL ON TABLES FROM soprolife_portal;
