# M15.6A — Prontidão de migração Google Sheets → PostgreSQL

Camada de governança sobre o importador nativo (`app/importer/csv_import.py`),
sem importador paralelo. Nenhum dado real foi importado nesta etapa; todo o
fluxo abaixo descreve o processo FUTURO com snapshots privados.

## Fluxo obrigatório

```
snapshot privado (CSV exportado do Sheets, fora do Git)
  → manifesto imutável (JSON, sem credenciais)
  → validação (migracao validar-manifesto)
  → registro (migracao registrar-snapshot)      identidade única
  → dry-run  (migracao dry-run)                 NUNCA grava registro operacional
  → revisão humana (UI aba Migração / relatório sanitizado)
  → aprovação explícita (migracao aprovar / API admin)
  → preflight (migracao preflight)              todos os portões
  → execução (migracao executar)                frase exata digitada
  → reconciliação (migracao reconciliar)
  → evidência de rollback (backup validado, local registrado)
```

## Diretório privado aprovado

Tudo (manifesto, CSV do snapshot, evidência de backup) vive SOMENTE em
`M15_IMPORT_PRIVATE_DIR` (padrão `data-private/import-snapshots`, fora do
Git). Nomes de arquivo são simples — qualquer separador de caminho, `..`,
caminho absoluto ou symlink que escape do diretório é rejeitado.

## Manifesto de snapshot (`m15.snapshot.1`)

```json
{
  "schema_version": "m15.snapshot.1",
  "source_type": "leads",
  "workbook_alias": "planilha-operacao",
  "sheet_alias": "Leads",
  "snapshot_ts_utc": "2026-07-19T12:00:00+00:00",
  "arquivo": "leads-2026-07-19.csv",
  "sha256": "<sha256 do arquivo>",
  "encoding": "utf-8",
  "delimiter": ",",
  "headers": ["id", "nome", "telefone", "data_primeiro_contato", "origem"],
  "row_count": 123,
  "linha_inicial_dados": 2,
  "mapping_version": "m15-6a.1",
  "colunas_extras_aprovadas": []
}
```

Rejeições fail-closed: checksum divergente/alterado; manifesto ausente;
`schema_version` desconhecida; identidade duplicada
(`workbook_alias + sheet_alias + sha256`); cabeçalho fora do mapeamento sem
`colunas_extras_aprovadas`; arquivo fora do diretório aprovado; QUALQUER
credencial, ID de planilha ou URL no manifesto (nunca vão para log);
`mapping_version` diferente da vigente; encoding/delimitador não permitidos;
`row_count`/cabeçalhos divergentes do arquivo real.

`workbook_alias`/`sheet_alias` são apelidos livres — nunca o ID da planilha,
hostname de tailnet ou credencial. `linha_inicial_dados` documenta a
referência de linha de origem: a linha N do relatório = linha N da aba.

## Registro de mapeamento (`m15-6a.1`)

| source_type | módulo alvo | execução |
|---|---|---|
| crm_pacientes | people (+contatos) | nativa |
| leads | leads (+people, followups) | nativa |
| crm_espirometria | spirometry_exams | nativa |
| crm_consultas | consultations | nativa |
| contatos_b2b | partners (+contatos) | nativa |
| crm_clinicas | partners (+unidades) | preparada (execução bloqueada) |
| consentimentos | consents / não-contatar | preparada |
| parcerias_encaminhamentos | partner_referrals/partnerships | preparada |
| financeiro_lancamentos | financial_entries | preparada |
| pcmso_historico | — (categoria histórica EXCLUÍDA) | nunca executável |

Fronteiras duras:
- **Financeiro_Lancamentos é a única fonte monetária.** Coluna monetária
  (`valor`, `preco`, `repasse`, `r$`, …) em qualquer outro domínio é erro
  mesmo com extra aprovada — nenhum valor é inferido do CRM.
- Descrições financeiras rejeitam PII e vocabulário clínico; vínculos
  clínico↔financeiro usam apenas IDs técnicos (`exame_id`, `consulta_id`,
  `encaminhamento_id`).
- A planilha Financeiro apagada NÃO é recriada.
- PCMSO só existe como categoria histórica excluída: registrável para
  arquivamento, todas as linhas classificadas como exclusão, aprovação e
  execução impossíveis.

## Dry-run (padrão absoluto)

`migracao dry-run` nunca grava registro operacional (garantia dupla:
`execute=False` + rollback). Persiste apenas o RESUMO do lote
(`import_batches` modo `dry_run`, staging sem PII) para revisão na UI.
O resumo traz: classificação por linha, entidade alvo proposta, relações
propostas, erros de validação, perfil de precisão de datas
(dia/mês/ano/desconhecida + dia assumido + avisos de parser, locale pt-BR),
duplicidades no arquivo, status de identidade/ambiguidade, motivo de
exclusão, contagem de avisos e rejeições. Detalhe bruto de linha permanece
no arquivo privado; logs/relatórios só carregam número de linha, hash e
motivo sanitizado. Replays são determinísticos: mesmo arquivo → mesmas
propostas.

Erros CRÍTICOS (bloqueiam aprovação/execução) ≠ exclusões deliberadas
(PCMSO), que não bloqueiam.

## Portões de execução (todos obrigatórios)

1. `snapshot_registrado`
2. `checksum_inalterado` (arquivo relido e conferido na hora)
3. `dry_run_realizado` (lote de dry-run do MESMO sha256)
4. `sem_erros_criticos` (criticos == 0)
5. `mapping_version_revisada` (igual à vigente)
6. `execucao_disponivel` (só source_types nativos; PCMSO/preparados nunca)
7. `backup_validado` (evidência JSON + arquivo de backup com sha256 conferido)
8. `evidencia_rollback_disponivel` (local do artefato registrado)
9. `aprovacao_humana` (decisão append-only com sha256, mapping_version e
   lote de dry-run EXATOS; revogável; nunca automática)
10. `idempotencia` (mesmo arquivo jamais executa duas vezes — portão +
    índice único + advisory lock PG)

Execução real: SOMENTE `python -m app.cli migracao executar --snapshot <id>
--batch <dry_run_batch_id> --backup-evidencia <ev.json>`, que pede a frase
exata `EXECUTAR IMPORTACAO <snapshot_id>` interativamente (única interação
do fluxo; a frase nunca é aceita por argumento). A API não executa e a UI
mantém execução desabilitada exibindo os pré-requisitos pendentes.
Refresh/repetição de requisição não duplica nada (aprovação repetida → 409;
reexecução → portão de idempotência).

## Identidade e datas

Regras herdadas do núcleo e verificadas nos testes: uma pessoa pode ter
vários exames/consultas; ID explícito é autoritativo; telefone/e-mail são
apenas candidatos; nome NUNCA vincula sozinho; ambiguidade vira
`identity_candidates` pendente até decisão humana; aliases de origem e
número de linha preservados; nenhuma fusão silenciosa. Datas preservam
texto original, valor normalizado, precisão (dia/mês/ano/desconhecida),
marcador de dia assumido e aviso de parser — parcial nunca vira exata em
silêncio.

## Reconciliação e rollback

`migracao reconciliar` produz releitura determinística: aceitas/rejeitadas
da fonte, inseridos por entidade alvo, atualizados (enriquecidos),
inalterados, identidades não resolvidas, relações criadas (aliases),
comparação de checksum, contagem de tabelas antes/depois e local do
artefato de rollback. A evidência de backup (`m15.backup-evidencia.1`)
referencia um backup validado por sha256 no diretório privado (gerado por
`scripts/backup-postgresql-m15.sh` no ambiente real).

## Segurança

Auditoria append-only para registro/dry-run/aprovação/execução/
reconciliação; sem PII bruta, credencial ou ID de planilha em log; leitura
da aba restrita a gestor+, aprovação restrita a admin, execução restrita à
CLI local; limites de tamanho/linhas/colunas herdados do importador;
parsing fail-closed (UTF-8 estrito, NUL, colunas duplicadas); células de
fórmula (`= + - @`) rejeitadas na entrada e NEUTRALIZADAS com apóstrofo em
todo CSV exportado; nenhum caminho arbitrário; nenhuma busca em rede a
partir de manifesto.

## Comandos

```bash
python -m app.cli migracao validar-manifesto --manifesto snap.manifest.json
python -m app.cli migracao registrar-snapshot --manifesto snap.manifest.json
python -m app.cli migracao dry-run --snapshot <id>
python -m app.cli migracao relatorio --snapshot <id> --saida var/relatorios-importacao
python -m app.cli migracao preflight --snapshot <id> --backup-evidencia ev.json
python -m app.cli migracao aprovar --snapshot <id> --sha256 <h> \
    --mapping-version m15-6a.1 --batch <dry_run_batch_id>
python -m app.cli migracao revogar-aprovacao --snapshot <id>
python -m app.cli migracao executar --snapshot <id> --batch <id> --backup-evidencia ev.json
python -m app.cli migracao reconciliar --snapshot <id>
python -m app.cli migracao status [--snapshot <id>]
```

Todos aceitam `--json` (machine-readable), têm `--help`, retornam exit != 0
em falha e nunca executam nada por padrão.

## Banco

Nova tabela `import_snapshots` (revisão Alembic `b4f8a2c15d31`, upgrade e
downgrade cobertos por teste em SQLite e PostgreSQL 16). Aprovações,
revogações e execuções são linhas append-only em `migration_decisions`.
