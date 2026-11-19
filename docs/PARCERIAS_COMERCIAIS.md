# Parcerias comerciais: Maclin e Alergocare

O bruto permanece em `FinancialEntry` como uma única receita. A regra comercial
é versionada em `PartnerCommercialRule`, ligada ao cadastro `Partnership` e à
unidade própria. Percentuais somam 100; cada nova partilha congela a versão
aplicada. A maior vigência inicial elegível vale para novos cadastros, com a
maior data de criação como desempate de versões intradiárias; uma nova
regra nunca recalcula partilha anterior. O bootstrap só configura as duas
parcerias autorizadas; o motor não depende de seus nomes ou de 30% fixos.

Novas regras/vigências também podem ser cadastradas por gestor na API existente
`POST /parcerias/{id}/regras-comerciais`, com percentuais somando 100 e unidade
da mesma parceria. Código comercial não pode mudar de parceria. Uma nova
vigência preserva o enquadramento fiscal já confirmado somente da mesma parceria;
sem enquadramento anterior, permanece bloqueada. Regra expirada não ressuscita
percentual antigo automaticamente.

Pastore continua no mecanismo anterior: recebimento contratual por exame,
fechamento mensal e uma receita agregada quando confirmado. Não foi convertida
para partilha individual. Alergo Life mantém seus cadastros e regras anteriores.

## Cadastro e acertos

Selecione o card Espirometria Maclin ou Espirometria Alergocare em Novo
atendimento: parceiro, unidade, origem operacional e modalidade Clínica parceira
vêm da configuração vigente. O município inicial é Rio de Janeiro/RJ.
Não há segunda seleção de parceria/local. Defaults comuns do exame e BD
permanecem. Informe bruto editável, forma de pagamento e recebedor.

Espirometria SoproLife fica para coworkings/domiciliar, com locais rápidos
Coworking Downtown e Coworking Nova América e opção manual. Escolher coworking
não presume SITE. Pastore conserva seu card e mecanismo anterior. Alergo Life
não é uma alternativa de Alergocare e não tem seus IDs/cadastros reaproveitados.

| Recebedor do bruto de R$100 | Clínica | SoproLife | Obrigação |
|---|---:|---:|---|
| SoproLife | 30 | 70 | 30 a pagar à clínica |
| Clínica | 30 | 70 | 70 a receber pela SoproLife |
| Cada parte recebeu sua parcela | 30 | 70 | Nenhuma |

Obrigações entre as partes só são afirmadas após pagamento integral confirmado.
Pagamento parcial sem valor liquidado conhecido permanece pendente de confirmação.
A UI não presume quem recebeu. No Financeiro, registrar **acerto** baixa o saldo
correto; **regularização da parcela própria** registra uma entrada em conta.
Ambos geram `FinancialEntry.tipo='movimentacao'`, sem somar receita ou despesa.
O vínculo guarda direção, conta, data/hora com fuso e responsável/origem.
Reenvio usa a mesma chave de idempotência; sobrepagamento e baixa duplicada são
recusados, inclusive em concorrência no PostgreSQL.

Os agregados de receita/status excluem movimentações. Caixa bancário da nova
área só inclui movimentos explicitamente registrados, nunca depósitos presumidos.
Quando a clínica recebe, a projeção distingue receita econômica de valor próprio
recebido: só um acerto de entrada comprova recebimento da parcela SoproLife.
Taxa de cartão usa o bruto; parcela do parceiro não sofre dedução duplicada.
Despesas operacionais reais continuam separadas no razão/sessão operacional.
Alergocare não herda R$10 de estacionamento/combustível nem aluguel de sala.

## Histórico e fiscal

Nenhum exame existente é convertido pela migração. Históricos Maclin líquidos
consolidados não admitem nova partilha nem modificação da base confirmada.
A confirmação administrativa de um exame existente exige exame, lançamento,
data e evidência individual, com plano/hash e transação, sem criar paciente
ou exame. Dados já existentes são preservados; só fatos novos são anexados.

Maclin e Alergocare usam a mesma regra fiscal DIRECT, com NFS-e emitida pela
SoproLife sobre o bruto confirmado, pelas mesmas políticas e pelo mesmo motor
existentes. A divisão 70/30 é somente econômica, nunca reduz a base da nota.
Autorização explícita de 18/11/2026 habilita Alergocare por nova versão imutável
da regra existente: mesmos parceiro, unidade, parceria, percentuais e vigência.
Partilhas anteriores conservam o rule_id original; não são reinterpretadas.
Emissão sempre depende de confirmação humana no módulo Fiscal. O cadastro e os
testes nunca disparam NFS-e real. Notas importadas/emitidas não são modificadas.

## Implantação e administração

1. Base atualizada de `origin/painel-soprolife-v01`, revisão do diff e gates
   sintéticos/efêmeros, incluindo PostgreSQL, browsers e `check-access.sh`.
2. Commit/publicação fast-forward; preflight de HEAD, worktree, health e worker
   fiscal sem pedido ativo. Usar `scripts/deploy-partner-shares-vps.sh` com HEAD
   anterior/alvo conferidos e `SOPROLIFE_PARTNER_GATES_VERIFIED=YES`. Terceiro
   argumento informa o schema inicial esperado (`u181126ux` nesta correção).
3. Backup privado validado com `pg_restore`, bundle Git verificado e hashes.
   Migração `w141126wa -> p151126share -> u181126ux -> a191126direct`; `alembic check`; comparar fingerprints
   de todas as tabelas preexistentes, sem mudanças de fatos nessa etapa.
4. Reiniciar somente a API; health API/proxy/portal, GETs autenticados de
   partilhas, Pastore, financeiro, fiscal, Funil, WhatsApp, EEGLife e Marketing.
5. Configuração/caso real são etapas separadas via `scripts/partner_share_admin.py`.
   Exige admin ativo, datas explícitas e dry-run; `--apply --plan-sha256` só
   executa o plano validado. Ambiguidade interrompe; unidade existente é
   reutilizada sem mudar endereço. Endereço desconhecido fica NULL.
6. Verificar preservação de todos os registros anteriores, hashes de arquivos,
   logs, timer de dados e portão de acesso. Relatório real fora do Git.

`u181126ux` apenas troca a constraint de soma por conferência em centavos
inteiros; não altera partilhas. Evita a imprecisão binária da soma SQLite dev
para R$123,45 e preserva o resultado PostgreSQL (37,04 + 86,41).
Sem configuração anterior, nesta missão UX a vigência começa em 18/11/2026;
não executar `confirm-existing`, regularização antiga ou backfill.

Correção fiscal Alergocare: `partner_share_admin.py approve-alergocare-direct
--actor <ADMIN_ID>` produz dry-run/hash. Aplicar somente esse plano com
`--apply --plan-sha256 <HASH>`. Não executar `configure` para criar entidades.
Anexa uma versão intradiária DIRECT e uma auditoria; não altera nem exclui
regras existentes e não prepara/confirma/emite NFS-e. O lock da parceria
serializa essa aprovação com a criação administrativa de versões. Migração
`a191126direct` só amplia a unicidade para incluir `created_at`; gatilhos de
imutabilidade continuam ativos. Downgrade com versões intradiárias é recusado.

O setup de configuração é idempotente e não renomeia parceiros. Nomes com
sufixo institucional ALERGOCARE (...) só resolvem essa entidade; Alergo Life
nunca é candidata. Unidades/parcerias ambíguas exigem identificação explícita.
Não cadastrar os exames futuros anunciados sem os pacientes reais.

## Rollback

Reverter código por commit revisado/fast-forward, preservando schema e evidência.
Não restaurar dump sobre escritas novas nem apagar fatos. Downgrade só é aceito
com as três tabelas novas vazias; após configurar regras ou lançar acertos, o
schema deve permanecer. A versão anterior não compreende `movimentacao` em
qualquer UI genérica: preferir correção progressiva ou desativar acesso à nova
área enquanto se mantém a classificação de receita protegida. Reversão monetária
exige novo fato revisado, nunca edição/deleção do movimento consolidado.
