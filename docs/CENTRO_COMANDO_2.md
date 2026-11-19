# Centro de Comando 2.0

Implementação de 19/11/2026 sobre `aecc09c`. Estado efetivo de publicação no
relatório externo `~/SoproLife_Programacao/RELATORIOS/CENTRO_COMANDO_2_20261119.md`.

## Arquitetura e fontes

A seção `overview` existente e a visão EEG passam a consumir uma projeção curta.
Nenhum dashboard paralelo, banco de notificações, backfill ou novo fluxo de
escrita. `command_center.py` deriva trabalho atual; `command_search.py` resolve
busca e destinos. Os módulos existentes continuam responsáveis pelas ações.

| API nova | Contrato |
|---|---|
| GET `/api/v1/comando/resumo?marca=soprolife` | Hoje, mês, capacidades, prévia de seis pendências e finanças somente se autorizadas |
| GET `/api/v1/comando/pendencias` | Marca, categoria, página, limite máximo 50; `hoje` abre os exames do dia |
| POST `/api/v1/comando/busca` | Corpo com marca, termo (3–80), limite até 12; quatro itens por grupo |
| POST `/api/v1/comando/registro` | Marca/tipo/UUID revalidados; resolve o destino de um resultado ou bookmark |

As respostas têm `private, no-store`. Autenticação e CSRF permanecem no middleware
existente; não há emissão fiscal nem escrita de fatos nesses quatro endpoints.

Fontes reutilizadas: exames/pessoas, precisão da data, fila de entrega e seu
resolvedor de assinatura, perfil/atribuição médica, `funnel.metrics`, estados
explícitos dos leads, classificação de follow-ups e contato legal, posição de
partilhas, fechamentos Pastore, `profit_projection`, `FinancialEntry`, documentos
fiscais persistidos e escopo de visibilidade EEG. A seleção de entrega e a seleção
do assinado atual foram extraídas do router existente; a query EEG compartilhada
mantém exatamente os mesmos papéis/atribuições. Não há segundo motor de regras.

## Homes e pendências

Gestão: exames de hoje, próximos sete dias, realizados sem documento, trabalho de
laudo ainda não entregue, documentos fiscais preparados não terminais, contatos
em estados abertos, follow-ups vencidos/hoje e acertos pendentes/parciais. Datas
com dia assumido não viram agenda exata. Arquivados e não contatar são respeitados.
Pendências são fatos tipados com destino autorizado; páginas/filtros não geram
novas notificações. Uma pessoa pode ter duas tarefas reais diferentes.

O mês apresenta realizados e a coorte de contatos iniciados no mês, com as etapas
acumuladas existentes. Finanças consomem a projeção canônica: bruto, parcela
própria e do parceiro respeitam a base econômica histórica; custo de parceiro
já descontado não é descontado novamente. Resultado é o lucro gerencial **dos
exames**, com custos confirmados. Despesas registradas no razão são exibidas
separadamente. Ausentes continuam nulos; totais parciais mostram cobertura.

Fiscal conta **documentos de produção já preparados**, como a fonte fiscal
persistida existente. Não chama `production_queue()`: essa rotina expira pedidos.
Elegibilidade de novos exames permanece no módulo Fiscal. Abrir registro fiscal
reutiliza `nfse.serialize_document`, somente leitura e sem confirmação/emissão.

Laudador SoproLife mantém sua fila clínica atribuída existente como home: prioridade,
data, código e acesso ao documento. O contador de pendências usa a própria fila
ativa já recebida, sem request extra nem segunda regra clínica. Não recebe resumo administrativo e não baixa
seus módulos. EEG usa somente capacidades e exames visíveis no contexto próprio;
receitas só vêm do vínculo técnico EEG. Perfis EEG sem acesso à visão geral
consultam apenas a projeção de pendências para o contador do header. Sem base explícita para despesas por marca
ou resultado EEG, esses valores ficam a confirmar.

Atalhos usam os cadastros e filas existentes. Links `#cc/marca/tipo/UUID` são
revalidados no backend no refresh; mudar a marca elimina bookmark da marca anterior.
Pessoas abrem a linha do tempo; exames/laudos abrem o fluxo existente; acertos
focam a linha existente; fiscal/parceiro/unidade mostram o registro selecionado no
módulo correspondente. Nenhum modal bloqueante novo de autorização.

## Busca, privacidade e RBAC

Hierarquia e papéis são os de `user_effective_roles`, `can_access_eeg`,
`can_edit_patient_registration` e dos escopos existentes de laudos/EEG. Valores
financeiros não são serializados para leitura/operação. Fiscal na home/busca é
restrito à gestão SoproLife ou admin EEG. Médicos precisam de perfil ativo
verificado e atribuição ativa; outro exame da mesma pessoa continua inacessível.
Não encontrado e ID fora do escopo retornam 404; contexto/área negada retorna 403;
anônimo permanece 401. Backend é a autoridade.

Debounce 350 ms, cancelamento/epoch contra respostas antigas, limite 12, paginação
restrita, termos somente no corpo POST, sem cache persistente nem auditoria de
termos. Telefone/CPF apenas completos/exatos; CPF só com permissão de cadastro
institucional já existente. Contatos devolvidos são mascarados, nunca originais.
Rate limit de 30 buscas/minuto por usuário **por processo** (implantação atual de
um processo); expansão futura a múltiplos workers exige rate limit compartilhado.
Não há limitação cliente usada como autorização. Sem caminho para exportar o banco
inteiro pelo frontend. Identificadores de documento fiscal são exatos.

PostgreSQL usa FTS `simple` sobre nome normalizado (AND de prefixos), códigos e
contatos indexados existentes, prefixos de parceiro/unidade e ID fiscal externo.
Migração `c191126cc2`, pai `a191126direct`, cria quatro índices e só isso. Não exige
extensão. Downgrade remove apenas índices; hashes dos fatos permanecem iguais.
SQLite de desenvolvimento tem fallback textual limitado.

## Bootstrap, estados e performance

Mantido template protegido inerte, deny by default, usuário/sessão/CSRF/contexto
confirmados antes da montagem, publicação atômica depois da home/fila inicial.
Mensagem neutra “Carregando…”. Home usa `loading/ready/empty/error/unauthorized`;
nenhum zero é mostrado durante loading/erro. Zero confirmado vem da resposta.
Erro de uma fonte explicitamente indisponível não vira zero no total confirmado.

Scripts são carregados por grupo sob demanda com promessa única. Configuração e
RBAC são compartilhados; a API não é consultada por módulos escondidos no início.
Módulos legados ainda carregam seus snapshots quando usados; esse custo foi
retirado do primeiro acesso à home, sem alegar reescrita interna de todos eles.
Gráficos históricos são sob demanda. Briefings/filas antigas heurísticas ficam em
template inerte, sem segunda fila operacional nem atalhos redundantes na home.
Cache de projeção só em memória, até 30 s por marca; voltar à home e cadastro
recarregam. Eventos/contexto cancelam resultados de busca e navegação obsoletos.

Medição local a frio, Chromium 1440, mesmo harness com API/proxy/SQLite sintéticos:
base extraída por `git archive aecc09c`, sem mexer na worktree operacional. Recursos
medidos por URL completa, com query; janela de estabilização 1,5 s após publicação.

| Perfil/contexto | Recursos antes → depois | JS antes → depois | API antes → depois | Publicação antes → depois |
|---|---:|---:|---:|---:|
| Admin SoproLife | 89 → 25 | 30 → 7 | 32 → 3 | 1501 → 968 ms |
| Admin EEGLife | 78 → 26 | 30 → 8 | 33 → 3 | 1116 → 663 ms |
| Médico SoproLife | 51 → 29 | 30 → 8 | 6 → 6 | 846 → 787 ms |

Não foram observadas URLs completas idênticas duplicadas em nenhuma medição.
Consultas de fila ativa/histórica têm parâmetros diferentes. Tempos são evidência
local, não SLA nem medida de latência produtiva; a redução estrutural é de requests
e assets iniciais. A home fica pronta de uma vez após autorização/dados iniciais.

## Validação e publicação

`tests/test_command_center.py`: HTTP/RBAC, zeros reais, precisão de datas, scopes,
lookup direto, máscara, limite, enumeração, finanças parciais/centavos e marcas.
`test_command_center_postgres.py`: PostgreSQL 16 real, upgrade/check/downgrade,
hashes iguais dos fatos, EXPLAIN com GIN sobre 1500 pessoas sintéticas, limite e
transação READ ONLY. Regressões de segurança/sessão/laudos/EEG/fiscal, parceiros,
funil e lucro fazem parte da suíte de entrega.

`test-command-center-browser.cjs`: API/proxy/banco isolado, Chromium e Firefox reais,
1440/1024/800/390, loading retardado e erro, nenhum nó proibido observado,
menus atômicos, busca/teclado/debounce, destinos/refresh/hash, logout/login,
admin/médico/operacional, marcas e ausência de valores não autorizados.
A suíte anterior `test-auth-bootstrap-browser.cjs` também cobre EEG técnico,
outages de autenticação/config/script e 401/403. Cadastro de parceiros preservado
na suíte real própria, com R$123,45, entidades distintas, receita única e centavos.
Quality gate inclui sintaxe dos novos arquivos; gates reais de API/PG/browser são
executados separadamente no ambiente completo antes do commit.

Deploy pelo runbook `deploy-partner-shares-vps.sh`, modo **full**, com HEAD anterior
e schema verificados, gates verdes, árvore limpa, backup privado validado, bundle,
fingerprints, fast-forward exclusivo, migração/check, restart API e health de
API/proxy/portal, smoke/check-access/logs. Não há configuração comercial/fiscal nova.
Validar usuários existentes com sessões temporárias revogadas, sem salvar fatos
fictícios e sem emitir NFS-e. Nenhuma publicação pode ser atestada sem SSH/preflight
e health reais. Rollback de código/schema somente pelo runbook e backup verificado,
sem reset destrutivo nem reinterpretação de histórico.
