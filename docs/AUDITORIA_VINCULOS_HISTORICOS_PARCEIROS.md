# Vínculos históricos de parceiros

A Central usa IDs canônicos. Local/origem livres são evidência para revisão,
nunca um vínculo automático de consulta. Corrigir um histórico não autoriza
reconstruir preço, base bruto/líquido ou partilha.

`nucleo-m15/scripts/link-historical-partner-exams.py` executa lote privado e
explicitamente revisado. Padrão somente leitura/dry-run, sem endpoint web,
migração automática ou backfill. Reutiliza o catálogo canônico existente;
exige unidade única e evidência de local/origem sem conflito (incluindo a
separação de Alergo Life). Não sobrescreve IDs de outro parceiro/unidade.

1. Auditar exames, laudos, financeiro, bases de receita e fechamentos, sem PII.
   Registrar conflitos e deixar esses exames fora do lote. Não inferir exames
   ausentes nem corrigir data/status/modalidade para fazê-los entrar em um card.
2. Fora do Git, preparar JSON `{"requests":[{"exam_code":"ESP-...",
   "partner_code":"maclin"}]}` com os registros inequívocos autorizados.
3. Gerar plano com `--requests <PRIVATE_REQUESTS> --output <NEW_PRIVATE_PLAN>`.
   Conferir todos os IDs e o hash retornado. Cada item congela todos os demais
   campos do exame em hash irreversível; não exporta dado clínico.
4. Backup/preflight pelo runbook vigente; validar dump, parse integral,
   bundle e SHA256SUMS. Sem operações fiscais em andamento.
5. Aplicar com `--plan <PRIVATE_PLAN> --expected-sha256 <REVIEWED_HASH>
   --actor-id <ACTIVE_ADMIN_ID> --backup-dir <VERIFIED_BACKUP_DIR>
   --output <NEW_PRIVATE_AUDIT> --apply`.
6. Conferir conclusão/commit da transação pelo exit 0 e recibo stdout, além
   do artefato. Falha deixa o lote sem confirmação; não confiar somente na
   existência do arquivo escrito durante a transação. Refazer auditoria antes
   de qualquer tentativa sobre fatos alterados.
7. Validar Central por períodos reais e módulos financeiro/fiscal/Funil, RBAC
   e health. Registrar pendências, não substituir desconhecido por zero.

Apply exige PostgreSQL e administrador ativo. Verifica checksums de dump e
bundle, transação serializável, locks por exame, hash completo do plano e
campos protegidos. Só escreve `partner_id` e `partner_unit_id`, preservando
inclusive `updated_at`. Não chama o PATCH normal do exame nem hooks de partilha,
fechamento ou fiscal. Uma mudança gera um evento append-only com IDs antes/depois;
repetição idêntica é no-op sem evento extra.

Dentro da mesma transação, fingerprints de todas as tabelas e de todos os
campos protegidos dos exames recusam qualquer escrita fora dos vínculos
aprovados e do evento de auditoria. Falha desfaz o lote inteiro. Não cria
PartnerShare, base de receita, Partnership, unidade, receita, repasse ou
obrigação. Não associa regra comercial atual a atendimentos antigos.

`SpirometryExam` não tem `partnership_id`; a aplicação de regra comercial mora
na partilha, não deve ser fabricada retroativamente. Campos opcionais de
fechamentos antigos permanecem intactos se não forem necessários ao vínculo.

Relatórios/mapeamentos reais, hashes de evidência e backups ficam fora do Git.
Testes usam exclusivamente registros sintéticos, incluindo centavos, base
líquida/bruta preexistente, conflito Maclin/Alergocare, Alergo Life, NFS-e sem
emissão, laudos, Pastore, stale-plan, rollback e idempotência.
