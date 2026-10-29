# Centro de Comando Life — arquitetura e fronteiras

Estado: fundação de 18/10/2026, atualizada em 29/10/2026. A Fase 2A já
implementa operação EEG e autorização por admin no backend. A extensão desta
worktree acrescenta cadastros/atendimentos, razão compartilhado por vínculo
técnico e adaptação fiscal com classificação real ainda pendente. Ela não foi
implantada em produção. Os demais planos futuros abaixo não atestam deploy.
Contrato vigente: [EEGLife — cadastros e fiscal](EEGLIFE_CADASTROS_FISCAL.md).

## 1. Objetivo e organização

Evoluir incrementalmente o painel existente, reutilizando seus módulos e o
núcleo M15, sem reconstrução, duplicação de aplicações ou perda de histórico.

Existe uma pessoa jurídica: **Sopro Life Diagnósticos e Soluções em Saúde LTDA**,
CNPJ **63.544.026/0001-10**, que opera duas marcas comerciais:

| Identificador de marca | Marca | Operação |
|---|---|---|
| `soprolife` | SoproLife | Espirometria e saúde respiratória |
| `eeglife` | EEGLife | Eletroencefalografia e neurodiagnósticos |

Contextos da interface: `soprolife` e `eeglife` — **as duas marcas, e nada
mais**. O seletor superior é apresentado como **SoproLife | EEGLife**.

**Decisão revista em 18/10/2026 (Fase 1.3A): não há contexto "Life Geral".**
A versão anterior deste documento previa um terceiro contexto consolidado no
topo. Ele foi REVOGADO depois da homologação visual: o seletor mostra marcas,
e marcas são duas. `life_geral` não é mais valor válido em lugar nenhum — nem
no seletor, nem na navegação, nem como tela, nem como preferência persistida,
nem como padrão.

Isso **não** elimina a consolidação corporativa como conceito. Relatórios e
painéis consolidados entre as duas marcas poderão existir no futuro, mas como
recurso administrativo DENTRO de um módulo — nunca como uma terceira marca ou
um terceiro contexto no topo. Despesas corporativas continuam usando um escopo
compartilhado explícito, distinto de marca.

### Autorização do contexto (regra provisória da Fase 1.3A)

| Sessão | SoproLife | EEGLife |
|---|---|---|
| papéis efetivos contêm `admin` | sim | sim |
| qualquer outro papel (inclusive `medico`) | sim | **não** |

A decisão vem de `papeis_efetivos` em `GET /auth/me` — servidor, cookie de
sessão —, a mesma fonte que o `boot-gate.js` usa para podar o DOM por papel.
`admin` implica gestor/operacional/leitura e **nunca** `medico`
(`ROLE_IMPLIES`), de modo que uma conta exclusivamente clínica não alcança a
EEGLife sem que nome de pessoa alguma apareça no código.

Para quem não pode, a aba e o painel inteiro da EEGLife são **removidos do
DOM**, e `setContext("eeglife")` é recusado — localStorage e console não
forçam a entrada. O localStorage guarda apenas preferência entre contextos que
a sessão já pode acessar; ele nunca concede acesso.

**Alcance atual:** todas as rotas `/eeg/*` exigem `require_eeg_access` no
servidor. Documentos fiscais EEG também exigem admin nas rotas compartilhadas
por ID; esconder a marca no seletor não é o controle de autorização.

## 2. Base existente preservada

- Site público SoproLife: branch `main`; painel: `painel-soprolife-v01`.
  Não fundir as branches. A base deve ser o HEAD oficial após fetch, em
  worktree exclusivo; preservar correções integradas, inclusive DDI.
- Frontend estático sem build, em `painel-soprolife/index.html`, `js/` e `css/`.
- Servidor autenticado `scripts/command-center-local-server.py` em loopback
  `:8765`; proxy `/painel-soprolife/api/m15` para `/api/v1` do FastAPI
  `127.0.0.1:8015`; PostgreSQL local. Acesso remoto privado via HTTPS/Tailscale.
- PostgreSQL/M15 é a fonte operacional. Sheets/Apps Script são legado para
  migração/forense explícita, bloqueados na esteira normal desde M23.
- Snapshots agregados em `data/*.local.json` são gerados, protegidos e ignorados
  pelo Git. Dados ausentes não autorizam substituir realidade por demonstração.
- Exceção atual a preservar: Custos & Investimentos usa livro-caixa privado
  `data-private/custos-investimentos.local.json`, com resumo gerado por script.
  Não presumir que já foi migrado para PostgreSQL ou somá-lo cegamente ao razão.
- Laudos respiratórios, portal, financeiro, Pastore, NFS-e, autenticação e
  integrações existentes permanecem intactos. Configuração de integração não
  significa ativação; não anunciar integração EEG sem evidência.

Referências: [contexto mestre](SOPROLIFE_MASTER_CONTEXT.md),
[operação local](../painel-soprolife/README.md),
[segurança](../painel-soprolife/SECURITY.md).

## 3. Paciente corporativo e registros por operação

`Person` permanece identidade corporativa compartilhada. Uma pessoa pode
realizar espirometria e EEG sem dois cadastros independentes. Preservar UUID,
código público, vínculos, consentimentos, histórico e regras de identificação.
Nome não prova identidade; ambiguidade exige decisão auditável.

Atendimentos, leads, exames, laudos e agendamentos deverão ter pertencimento
operacional explícito a `soprolife` ou `eeglife`. Um parceiro poderá atender
ambas as marcas mediante vínculos comerciais específicos, sem duplicação
obrigatória do cadastro institucional.

Identidade compartilhada **não implica acesso clínico global**, nem consentimento
de marketing automaticamente extensível à outra marca. A API deverá filtrar
vínculos, timelines, documentos e campos conforme a autorização. O alcance de
consentimentos corporativos/por marca deverá ser definido antes da migração.

EEG não será representado artificialmente como `SpirometryExam`, nem herdará
templates, regras fiscais ou conclusões respiratórias. O modelo clínico EEG
é trabalho futuro específico; agenda/CRM administrativos podem compartilhar
componentes sem compartilhar semântica clínica indevida.

## 4. Isolamento, autorização e contexto

Futuramente a API resolverá um contexto autorizado por requisição, validado
contra o usuário. O frontend não será a fronteira de segurança.

- Listagem, busca, detalhe por ID, escrita, download, exportação, indicadores,
  snapshots, filas e automações deverão aplicar o mesmo escopo autorizado.
- Validar relações cruzadas: um registro não poderá referenciar exame,
  lançamento, laudo ou agendamento de outra marca indevidamente.
- Ausência de contexto em clientes antigos precisará de compatibilidade
  explícita com SoproLife, nunca acesso implícito a todas as marcas.
- Relatórios consolidados (quando existirem, dentro de módulos
  administrativos) consultarão apenas marcas autorizadas. Cobertura parcial
  deverá ser visível; consolidar não amplia privilégio.
- Escritas operacionais exigirão marca concreta. Operações corporativas terão
  autorização própria.
- O gate de arquivos estáticos hoje protege por sessão/papel, não por marca.
  Não basta filtrar a API e deixar snapshots de outras marcas acessíveis por
  URL. Definir entrega autenticada por contexto antes de ativar a segregação.
- Troca futura de contexto limpará estado de telas/gráficos, invalidará caches
  por marca e descartará respostas atrasadas; formulários não poderão trocar
  silenciosamente a marca de uma gravação em andamento.

Papel administrativo e papel médico são distintos. Administração **não**
confere autoria clínica. Médico autorizado para laudos respiratórios **não**
herda autorização EEG. A direção futura combina **papel + módulo/capacidade
clínica + marca/operação**, além de atribuição do caso quando exigida. Nenhuma
permissão, papel ou sessão real é alterada nesta fundação.

Preservar cookie HttpOnly/Secure/SameSite, CSRF, RBAC no servidor, trilha
append-only, validação de requisições, proteção de PDFs e logs sem dados
pessoais. API e banco continuam em loopback; dados clínicos reais exigem HTTPS.
Segredos, dados privados, backups e artefatos fiscais ficam fora do Git.

## 5. Matriz de módulos — classificação documental

A = corporativo/compartilhado; B = futuramente escopado por marca;
C = clínico específico SoproLife; D = futuro clínico EEGLife;
E = relatório consolidado entre marcas (recurso administrativo dentro de um
módulo, não contexto do topo). Classes combinadas indicam fronteiras dentro de
um módulo existente, não duplicação do módulo. D é planejado, não existente.

| Módulo atual/futuro | Classe | Fronteira e direção |
|---|---|---|
| Autenticação | A | Uma identidade/sessão; contexto nunca substitui login |
| Usuários | A | Cadastro corporativo; concessões de acesso poderão ser B/C/D |
| Central de Cadastros | A + B | Pessoa única; atendimento e vínculos por marca |
| CRM | B | Relação, oportunidade, contato e timeline contextualizados sobre pessoas compartilhadas |
| Leads | B | Cada oportunidade tem marca; sem copiar lead ao trocar contexto |
| Agenda | B + E | Compromissos por operação; visão conjunta sem duplicar eventos/recursos |
| Tarefas | B + A + E | Tarefas operacionais por marca e corporativas explícitas; consolidar por ID; aba estática atual não é CRUD persistente |
| Laudos de espirometria | C | Preservar MIR, versões, atribuição, assinatura e regras respiratórias |
| Laudos EEG | D | Novo domínio clínico futuro, sem herança automática de autorização C |
| Portal de resultados | A + C; D futuro | Canal compartilhável; hoje fluxo respiratório. Futuramente autorização por documento/operação; não modificar agora |
| Financeiro | A + B + E | Razão compartilhado, atribuição de marca e rateio; não clonar razão |
| Fiscal | A + B; C atual | Mesma pessoa jurídica; políticas por serviço/operação, sem copiar política de espirometria para EEG |
| Custos e investimentos | A + B + E | Livro-caixa privado atual preservado; exclusivos/compartilhados/rateados no futuro |
| Parceiros e Pastore | A + B | Cadastro compartilhável; vínculos e regras por operação; preservar fechamentos Pastore |
| Documentos | A + B + C; D futuro | Institucionais corporativos; documentos de operação e clínicos com escopo próprio |
| Marketing/SEO | B + E | Propriedades, domínio, credencial, fila e frescor por marca; comparação compatível |
| Fontes de dados | A + B | Infraestrutura comum e conectores/configurações por marca; status real de cada fonte |
| Indicadores/Painel Geral | B + E | Dados base por marca; consolidação de entidades/eventos, não soma de cards |
| Núcleo administrativo | A | Governança e concessões; não é autorização médica |
| Automação CRM/follow-ups | B | Respeitar marca, consentimento e não contatar; WhatsApp permanece ação humana |
| Briefing e últimos lançamentos | B + E | Derivações sem duplicar fatos; separar exemplos de dados reais |

## 6. Financeiro e consolidação

Manter o razão compartilhado e a autoridade de `FinancialEntry`, com a exceção
de custos legados explicitada acima. Não clonar o financeiro por marca.
Planejar receita SoproLife, receita EEGLife, despesas exclusivas de cada marca,
despesas corporativas compartilhadas e rateios entre marcas.

O lançamento original é único. Um rateio distribui esse valor para análise,
não cria uma segunda despesa corporativa. A soma das parcelas deve reconciliar
com o original, com política de arredondamento, competência e vigência explícita.
Correções precisam manter histórico auditável; não sobrescrever valores para
fazer consolidações fecharem. Recebimento, repasse e nota fiscal não são três
receitas. Preservar regras de Pastore e a idempotência fiscal.

Um relatório consolidado entre marcas soma **entidades/eventos identificáveis
no backend**, não `cards(SoproLife) + cards(EEGLife)`. Indicadores expostos podem ser agregados
sem transportar identificadores pessoais ao navegador.

| Medida | Regra futura |
|---|---|
| Pacientes | Distintos por identidade corporativa no período autorizado |
| Receita/despesa | Lançamentos únicos; mesma moeda e regime; rateios não somados novamente ao original |
| Tarefas/agenda | Eventos únicos por ID; distinguir tarefa de follow-up derivado |
| Exames/resultados | Separar EEG e espirometria; versões/assinaturas/downloads não contam como novos exames |
| Taxas e ticket | Recalcular numerador/denominador compatíveis; não média simples de percentuais |
| SEO/marketing | Mesma janela, fuso, propriedade e definição; CTR por cliques/impressões; posição média não se soma |
| Usuários entre sites | Não somar como pessoas únicas sem método explícito de deduplicação |
| Fonte indisponível | Exibir ausência/frescor/cobertura parcial; não fabricar zero nem usar demo |

Cada indicador deverá declarar fonte, unidade, período, fuso, evento contado,
regime financeiro e regra de deduplicação. Revisar antes da consolidação as
contagens históricas/mensais e rótulos atuais de `app/snapshots.py`; esta fase
não altera seus resultados.

## 7. Identidade visual — referência, não implementação

SoproLife mantém os tokens existentes de `painel-soprolife/css/style.css`:
navy `#0c1f3d`, navy secundário `#17355a`, teal `#17b5a5`, teal escuro
`#0e9d8e`, fundo `#eaeff7`, superfície `#ffffff`, texto `#132033` e texto
secundário `#6e80a0`. Não mudar CSS ou site nesta fase; conferir contraste
nas combinações efetivamente usadas antes de implementar o seletor.

EEGLife: fonte real consultada **somente em leitura** em
`~/eeglife-site/public/assets/css/eeglife.css`, tokens `:root` e modo escuro.
Referência local do projeto do site, não confirmação de conteúdo publicado.

| Uso | Token/valor EEGLife |
|---|---|
| Principais | `--dourado: #c48a32`; `--grafite: #222c34` |
| Secundárias | `--dourado-suave: #d9ad61`; `--grafite-claro: #2e3a44` |
| Fundo | `--areia`/`--fundo: #f8f5ee` |
| Superfícies | `#ffffff`; `--areia-funda: #f1ebde` |
| Texto | `--tinta: #222c34`; `--tinta-suave: #4a5560` |
| Destaque textual sobre claro | `--dourado-tinta: #865b1d` |
| Escuro | Fundo `#1a232a`; superfície `#202b33`; superfície funda `#182127` |
| Texto/acentos sobre escuro | `#eee9df`, secundário `#b9c0c7`, dourado textual `#d9ad61` |
| Tipografia | Fraunces e Manrope, fontes locais do site |

Contrastes calculados a partir dos hexadecimais, por luminância relativa sRGB:

| Texto / fundo | Razão aproximada |
|---|---|
| Grafite / areia | 13,05:1 |
| Texto secundário / areia | 6,99:1 |
| Dourado-tinta / areia | 5,47:1 |
| Dourado-tinta / branco | 5,95:1 |
| Dourado-tinta / areia funda | 5,01:1 |
| Dourado / areia | 2,74:1 |
| Branco / dourado | 2,99:1 |
| Grafite / dourado | 4,76:1 |
| Dourado suave / grafite | 6,83:1 |
| Texto claro / fundo escuro | 13,17:1 |

Usar dourado como acento; para texto sobre claro, respeitar o derivado
dourado-tinta. Evitar branco sobre dourado em texto pequeno. Os números não
certificam a acessibilidade da interface futura: validar também foco, estados,
tamanho, componentes, responsividade e não depender somente de cor.

A EEGLife usa os **mesmos componentes** do painel SoproLife (`.app-shell`,
`.sidebar`, `.main`, `.topbar`, `.panel`, `.stat-card`, `.table-wrap`,
`.empty-state`) e troca apenas a identidade, redefinindo os tokens do `:root`
dentro de `#lifePanelEegLife`. Muda a cor, não o padrão de produto: as duas
marcas são irmãs dentro do mesmo Centro de Comando, e não dois produtos
diferentes.

Relatórios consolidados futuros, se tiverem identidade própria, usarão tema
institucional neutro — sem logo nem identidade de terceira marca comercial.

## 8. Estratégia incremental e critérios

| Fase | Escopo | Dependência/aceite |
|---|---|---|
| 1 — Fundação | Documentação, matriz, referência visual e inicializadores seguros | Sem alteração de banco, dados, permissões, UI operacional ou produção |
| 2 — Modelo e plano de classificação | Desenhar marca/vínculos/escopo corporativo; implementação somente após nova autorização | Decidir consentimento, acesso e custos compartilhados; simulação e preservação de IDs |
| 3 — API/autorização | Aplicar marca autorizada a todas as operações e relações | Testes de acesso por ID e cruzamento de marcas; compatibilidade SoproLife |
| 4 — Snapshots/indicadores | Entrega protegida e consolidação por fatos identificáveis | Reconciliar período, deduplicação e frescor; nunca demo operacional |
| 5 — Contextos visuais | Seletor e componentes compartilhados | Depende da proteção de backend/snapshots; troca sem vazamento de estado |
| 6 — CRM/agenda | Operações por marca sobre identidade compartilhada | Não representar EEG como espirometria; agenda e recursos com semântica própria |
| 7 — Tarefas | Persistência e vínculo com automações | Não transformar tarefas demo em reais; deduplicar follow-ups |
| 8 — Marketing | Configuração de propriedades e filas por marca | Acesso real às fontes, janela comparável e estado vazio honesto |
| 9 — Financeiro/custos | Atribuição e rateios, preservando razão | Reconciliar a fonte privada de custos antes de consolidar despesas |
| 10 — Homologação local | Regressão SoproLife e isolamento EEG | Dados sintéticos; nenhum deploy implícito |

Ativação clínica EEG, autorização de médicos, portal e fiscal EEG exigem
escopos próprios revisados. Não inferir aprovação dessas mudanças da criação
de uma marca comercial ou de uma tela administrativa.

Migração real será ação separada: backup privado validado, plano de mapeamento,
simulação, lista de ambiguidades e reconciliação de contagens/valores/vínculos.
Não atribuir tudo automaticamente a SoproLife se houver evidência ambígua ou
custo compartilhado. Registros clínicos/fiscais imutáveis não serão reescritos
para introduzir marca; avaliar vínculos laterais/derivação segura no desenho.
Desativação de funcionalidade é preferível a downgrade destrutivo; testar
recuperação em ambiente isolado. Nenhuma migração é executada na fase 1.

## 9. Inicialização segura e limites desta fundação

`start-local.sh` chama exclusivamente o servidor autenticado em loopback e
recusa ausência do proxy. `start-tailscale.sh` é mantido como entrypoint de
recusa explicativa: seu servidor estático antigo ignorava o gate. Não usar
servidor HTTP genérico, `file://` ou launcher antigo para operar o painel.
As units oficiais, o proxy e a semântica de autenticação permanecem intactos.

Nenhuma mudança desta fundação configura Tailscale, DNS, Cloudflare,
hospedagem, certificado ou VPS. Nenhum site público é alterado. Relatórios de
missão ficam fora do repositório; este arquivo é documentação arquitetural
durável e não substitui a autorização de cada fase.
