# Integração EEGLife + MIR/FET — contrato de publicação

Preparação local de 30/10/2026. **Este documento não autoriza push, integração
na branch oficial, deploy, restore ou emissão real de NFS-e.**

## Combinação revisada

Base oficial `bb7566a7455f27ee3e920fc675151552929d1d1d` contém
`fa728d000512252c79ec92812d8b9cfd166fdf65` (MIR/FET) e a correção de cache.
A branch isolada de integração incorpora
`d6faf57eba823b62e30759039aa8275db93b3c40` (Central/atendimento EEG) por merge
local, preservando os três commits originais como ancestrais.

Não houve conflito textual. A revisão semântica exige conservar:

- `schemas.py`: `equipamento_fonte` só nos contratos de espirometria;
  seleção de identidade e status inicial no contrato EEG.
- `index.html`: cache de `central-cadastros.js` e `eeg-operacao.js` em
  `2026103001`; CSS EEGLife em `2026102911`.
- Contexto mestre: a nota MIR/FET e o contrato inline EEG.
- People/UUID/PES compartilhados; fluxos clínicos, documentos e receitas
  segregados por seus vínculos técnicos. Nenhuma inferência de equipamento.

Head única do banco: `d7b2f3a9c1e0`, filha de `a291029eeg01`. A migration MIR
adiciona apenas `spirometry_exams.equipamento_fonte` nullable e CHECK; sem
backfill. Esta integração não acrescenta migration. Banco anterior a MIR
precisa dessa coluna; banco já em MIR não deve reaplicar alteração manual.

O motor fiscal, emissor, spool e worker são corporativos. Prestador:
SOPRO LIFE DIAGNÓSTICO E SOLUÇÕES EM SAÚDE LTDA,
CNPJ **63.544.026/0001-10**. EEG exige política tributária própria aprovada;
a publicação desta interface não aprova classificação nem habilita emissão.

## Gates antes de solicitar publicação

Executar na worktree isolada com dados sintéticos:

1. `scripts/test-postgres-efemero.sh` do núcleo, com venv M15 instalado:
   PostgreSQL 16, upgrade/check/downgrade/upgrade e suíte completa.
2. Casos específicos MIR/FET, integração People/financeiro e fiscal EEG,
   autenticação/RBAC, cadastros e atendimentos. Concorrência EEG deve preservar
   uma pessoa, vínculo, exame e receita por chave. Os casos legados de assinatura
   exigem também `M25_14_TEST_DATABASE_URL` apontando para PostgreSQL descartável.
3. Navegador via proxy real: Chromium/Firefox em 390/768/1440, Central SoproLife
   com fonte MIR, Central EEG inline, CRM, fiscal bloqueado, arquivos/laudos,
   assinatura e portal. Nenhum campo pulmonar deve entrar no formulário EEG.
4. Quality gate, check-access, sintaxe, diff completo e allowlist de staging.
5. Fetch final; a referência oficial deve continuar sendo ancestral do candidato.
   Se avançar em outra frente, combinar/testar novamente antes de publicar.

As evidências e o relatório final ficam fora do Git. O SHA final aprovado deve
ser registrado no relatório; aprovação de uma árvore diferente exige revisão.

## Plano de publicação — somente após autorização explícita

### Preparar a referência oficial

- Congelar o SHA candidato revisado e obter autorização para push/integrar/deploy.
- Fetch; conferir branch oficial, estado limpo e avanço esperado. Fazer somente
  fast-forward da oficial para o candidato revisado; sem force push.
- Não aplicar cherry-pick avulso em produção nem substituir arquivos manualmente.
- Não usar uma worktree local da oficial que contenha trabalho de outra frente.
  Preservar seus arquivos; publicar o candidato a partir da branch de integração
  com guard de SHA/fast-forward remoto, ou usar clone limpo autorizado. Não
  limpar, resetar nem adicionar arquivos alheios para satisfazer um gate.

### Preflight de produção, somente leitura

- Conferir o HEAD real `B`, branch, worktree limpa, dono de `.git` e schema real
  `S`; não presumir que a referência Git prova deploy. Todo Git na VPS como
  `soprolife`, nunca root. Não tocar em `.env`, usuários, papéis ou units.
- Reservar janela com os operadores: não iniciar novas emissões durante a troca;
  se não for possível assegurar essa condição, não prosseguir. Conferir também
  a presença das fontes privadas exigidas pelo script (incluindo custos), sem
  imprimir seu conteúdo.
- Confirmar API/proxy/portal saudáveis, PostgreSQL disponível, worker fiscal sem
  execução e zero pedidos `authorized/running`. Pausar se houver emissão ativa.
- Conferir, por consulta read-only e sem conteúdo de paciente nos logs, que não
  existe política EEG de produção validada inesperada e que o perfil jurídico
  continua corporativo. A ausência de classificação continua um bloqueio fiscal.
- Comparar `B..candidato`, migrations e processos realmente afetados. Se houver
  outra migration/HEAD não prevista, revisar antes de qualquer escrita.

### Backup e aplicação

Usar o caminho versionado
`painel-soprolife/nucleo-m15/scripts/deploy-eeglife-operacional-vps.sh`, lendo a
versão do SHA candidato revisado. Exige TTY e argumentos explícitos de
candidato, HEAD anterior e schema anterior, além dos gates verificados.
Não executar o script nesta missão de preparação.

Antes de upgrade ou troca do código:

1. Backup PostgreSQL privado pelo script `backup-postgresql-m15.sh` versionado.
2. Validar `pg_restore --list` e leitura integral do dump; registrar caminho,
   bytes e SHA-256. Criar/verificar bundle Git e guardar HEAD/schema anteriores.
3. Preservar os backups antigos e os diretórios de storage privado. O dump do
   banco não substitui o acervo de PDFs/traçados; registrar os mounts e o backup
   privado vigente desse acervo. Nenhum caminho clínico vira endpoint público.
4. Somente então fazer fast-forward produtivo como `soprolife` e executar
   `alembic upgrade head` se houver migration ainda não aplicada; `alembic check`.

O deploy script seleciona reinícios pelo diff: API para mudança de contrato/
router; portal apenas se houver mudança em modelos/módulos que ele utiliza;
proxy apenas se seu código mudar. Assets estáticos são lidos do disco. Não
reiniciar o worker fiscal nem outros serviços por conveniência. Anunciar a
breve indisponibilidade dos processos que precisarem ser reiniciados.

### Health e aceitação após publicação

- Helper finito `soprolife_wait_health_ok` de `lib-deploy-hardening.sh`:
  API, proxy e portal com HTTP 200 + JSON `status=ok`; PostgreSQL `banco=ok`.
- Painel HTTP 200; rotas clínicas sem autenticação continuam 401/403.
- `check-access.sh` como usuário da aplicação; journal sem erro crítico e
  `.git` pertencendo a `soprolife`; worktree limpa e SHA exatamente aprovado.
- Metadados read-only: head Alembic, equipamento MIR opcional, escopos e
  prontidão fiscal EEG bloqueada. Validar endpoints sem capturar PII/PDF real.
- Regressão SoproLife/EEG pela versão testada localmente e probes read-only.
  Não criar paciente sintético em produção, não regenerar laudo histórico,
  não iniciar emissão nem chamar provedor para fazer health.

## Plano de rollback — preparar, não executar

O retorno preferido é **de código por novo commit revisado**, mantendo banco,
receitas, vínculos, arquivos e auditoria. Não usar reset --hard, force push,
downgrade automático ou restore sobre escritas posteriores.

- Se a produção antes da janela já era MIR/FET, o baseline natural é `B`
  (como `bb7566a`): mantém o campo e a migration. Reverter a parte EEG em uma
  branch isolada por commit novo, com revisão de compatibilidade e testes,
  preservando os commits oficiais. Publicar por fast-forward autorizado.
- Se a janela também aplicou MIR, preferir corrigir para frente ou manter
  código compatível com `d7b2f3a9c1e0`. O downgrade MIR remove a coluna e perde
  a fonte que tiver sido registrada; ele não é um rollback operacional seguro.
- O script de deploy recusa alvo que deixe schema divergente no `alembic check`;
  não contornar esse gate. Revisar o candidato de recuperação antes do uso.
- Suspender novas operações afetadas durante a recuperação; anunciar impacto,
  conservar sessões/dados e orientar recarga de interface após troca de assets.
- Restore de desastre é um procedimento separado: autorização específica,
  restauração primeiro em alvo isolado, conferência de dump/hash/acervo e plano
  para escritas posteriores. Nunca sobrescrever produção automaticamente.
- Repetir os mesmos health checks, controles de acesso e confirmação de
  bloqueio EEG após a recuperação. Nenhuma ação de rollback emite NFS-e.
