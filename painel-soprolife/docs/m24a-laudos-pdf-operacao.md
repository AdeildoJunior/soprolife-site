# M24A/M24B — operação segura dos laudos PDF

Este runbook cobre somente configuração e governança do armazenamento de
laudos. Ele não autoriza implantação, acesso à VPS, uso de dados reais,
definição de conteúdo clínico ou contratação de assinatura digital.

M24C preserva integralmente estas proteções de arquivo e substitui o RBAC e
o ciclo clínico provisórios descritos originalmente por M24A. Para papel
`medico`, perfil profissional, atribuição, fila, composição e assinatura
fail-closed, o contrato vigente é
`m24c-medical-assignment-workflow.md`.

## Feature flag independente

M24A permanece desligado nesta entrega. O backend usa
`M15_REPORTS_ENABLED=false` por padrão e recusa todas as rotas `/laudos` antes
do parser multipart enquanto a flag estiver desligada. O frontend exige
separadamente `reports_enabled=true` em `data/m15-config.json`; o valor
versionado é `false`. `enabled=true` para o restante do Núcleo M15 não habilita
laudos.

O opt-in por `localStorage` existe somente em hostname loopback para E2E e
desenvolvimento isolado; não funciona em origem remota. Não altere nenhuma das
duas flags para produção enquanto os itens de NO-GO deste runbook estiverem
pendentes.

M24B adiciona um gate de implantação exclusivo, separado do gate geral M15.
Uma futura tentativa de habilitação só passa se, antes de qualquer mutação:

- `SOPROLIFE_REPORTS_GO_LIVE` for exatamente
  `AUTORIZO GO-LIVE DE LAUDOS`;
- o JSON versionado tiver `reports_enabled=true` e a configuração backend tiver
  explicitamente `M15_REPORTS_ENABLED=true`;
- `M15_REPORTS_STORAGE_DIR` apontar para raiz absoluta, privada, já existente,
  fora do Git, sem ancestral symlink, de `soprolife:soprolife` e modo `0700`;
- a configuração systemd **efetiva**, inclusive drop-ins, contiver a raiz exata
  em `ReadWritePaths`, sem aceitar `/`, `/opt/soprolife` ou outro pai gravável
  mais amplo;
- `SOPROLIFE_REPORTS_BACKUP_COORDINATED` for exatamente
  `POSTGRESQL_E_STORAGE_CONFIRMADOS`, após backup coordenado e restaurável;
- preflight HTTPS provar que workspace, flag frontend e estado da API concordam.

O postflight HTTPS repete a prova com `reports_enabled=true` servido e API
protegida por autenticação. `SOPROLIFE_M15_GO_LIVE=YES`, sozinho ou combinado
com a autorização geral do M15, é deliberadamente insuficiente. A unit atual
não contém a raiz e este marco não a provisiona: portanto, este release continua
incapaz de passar pelo ramo de habilitação fora de fixtures sintéticas.

M24C acrescenta o bloqueio incondicional
`m24c_signature_and_legal_approval_missing`: mesmo uma fixture que satisfaz
as precondições técnicas não autoriza produção enquanto o rodapé jurídico e
um provider de assinatura qualificada não forem aprovados em marco futuro.

## Configuração de `M15_REPORTS_STORAGE_DIR`

`M15_REPORTS_STORAGE_DIR` é obrigatório para enviar, compor, visualizar ou
baixar laudos. Sem ele, a API falha fechada com serviço indisponível apenas
nas operações de arquivo. O valor precisa:

- ser um caminho absoluto;
- ficar fora de qualquer worktree/repositório Git;
- não conter ancestral, raiz ou diretório interno que seja link simbólico;
- pertencer ao usuário e grupo do serviço;
- estar no mesmo filesystem em que os arquivos temporários e finais serão
  publicados por hard link atômico;
- ter espaço, inodes e monitoramento compatíveis com o volume aprovado.

Exemplo de caminho operacional — a escolha final pertence à infraestrutura:

```text
M15_REPORTS_STORAGE_DIR=/opt/soprolife/private/m15-reports
```

O limite padrão de upload é 25 MiB por PDF. Alterar
`M15_REPORTS_MAX_UPLOAD_BYTES` exige avaliação de capacidade, proxy, backup e
tempo de processamento; não é um atalho para aceitar arquivo inválido.

## Propriedade e permissões

Para o exemplo acima, o provisionamento autorizado deve produzir:

```text
/opt/soprolife/private/m15-reports  soprolife:soprolife  0700
diretórios internos                soprolife:soprolife  0700
arquivos PDF                       soprolife:soprolife  0600
/opt/soprolife/secrets/m15.env     root:soprolife       0640
```

O serviço já usa `UMask=0077`, mas a correção não depende dele: a camada cria
cada diretório ausente com modo efetivo `0700`, publica cada PDF `0600` e
verifica novamente contenção, symlinks e modos depois de criar. Raiz final ou
diretório interno preexistente com qualquer bit de grupo/outros é recusado; a
aplicação não tenta “consertá-lo” silenciosamente. Falha de `mkdir`, `chmod` ou
`stat` interrompe a operação e vira 503 sem caminho na resposta ou log.

`ProtectSystem=strict` torna o filesystem somente leitura para a unit. Portanto
o caminho escolhido deve aparecer, de forma exata e sem curinga amplo, em
`ReadWritePaths` na configuração systemd efetiva. A unit versionada ainda
libera apenas o diretório `var` do Núcleo; antes de qualquer go-live do M24A,
uma mudança de implantação separada e aprovada deve provisionar a raiz,
adicionar o mesmo caminho à unit e gravar a variável no EnvironmentFile.
Documentar a variável sem ajustar o sandbox não torna o diretório gravável.

Validações de pré-implantação devem conferir somente caminho, dono, grupo,
modo, espaço e configuração efetiva; não listar nomes ou conteúdo dos PDFs.
Depois de `daemon-reload`, confira a unit efetiva, inclusive drop-ins, e prove
que o processo `soprolife` consegue criar e remover um arquivo sintético no
ambiente de homologação. Não faça esse teste com PDF de paciente.

## Organização e metadados

O nome enviado pelo navegador é ignorado: não entra no banco, metadados da API,
auditoria ou workspace. A árvore interna usa somente UUIDs gerados pelo
servidor:

```text
<raiz>/laudos/<exam_uuid>/<document_uuid>/<version_uuid>.pdf
```

O banco guarda o caminho relativo, SHA-256, tamanho, páginas e ciclo de vida.
Antes de compor, preparar assinatura, criar corretiva, visualizar ou baixar, um caminho
único relê os bytes, valida novamente a estrutura e conteúdo ativo, recalcula
SHA-256/tamanho/páginas e exige igualdade com a linha. Arquivo ausente,
corrompido, substituído ou divergente falha fechado. Metadados de uma versão
nova são sempre calculados dos mesmos bytes publicados.

Cada versão composta também congela código, versão, texto exato e SHA-256 do
template. Editar `ReportTemplate` depois não muda a evidência do rascunho ou da
versão final. A API não devolve caminho de filesystem nem URL pública.
Preview/download usam sessão autenticada, geram `Blob` temporário e desativam
cache.

PDFs com `OpenAction`, `AA`, JavaScript, `EmbeddedFiles`, formulários, `Launch`,
RichMedia ou outras ações/conteúdo ativos são recusados por travessia cycle-safe
antes e depois da composição. Toda ação `/URI`, inclusive link manual em
anotação, árvore de nomes/ações ou objeto indireto, é tratada como não confiável
e recusada por padrão; a validação nunca resolve DNS nem faz chamada de rede.
Links não são removidos, reescritos ou “sanitizados” silenciosamente. Há um
ponto de extensão explícito para uma futura allowlist fechada e aprovada, mas a
allowlist de produção permanece vazia neste marco. Texto visível que apenas
contém caracteres de URL continua sendo texto.

## Área visível efetiva e composição

Para cada página, a única geometria usada é a interseção estrita de `MediaBox`,
`CropBox` quando presente e `TrimBox` quando presente. Array malformado,
coordenada não finita, caixa vazia/invertida ou caixas sem interseção produzem
422 seguro. A aplicação não normaliza, expande, substitui ou grava de volta
nenhuma caixa do PDF recebido.

A mesma caixa efetiva determina largura de quebra de linha, posicionamento da
interpretação, rodapé, validação de altura útil e verificação pós-composição.
Origens diferentes de zero são preservadas. Rotações `0`, `90`, `180` e `270`
usam coordenadas visuais verticais, com transformação inversa para o espaço do
PDF. O PDF composto é reaberto e revalidado; os baselines marcados de cada linha
e a extensão completa de todas as linhas do rodapé precisam continuar dentro
da interseção original em todas as páginas.

Texto nunca é truncado e nenhuma página é adicionada automaticamente. Se
interpretação, borda ou rodapé não couberem inteiros, a composição falha com
422. Os bytes originais não são mutados nem sobrescritos.

## Publicação transacional e arquivos órfãos

Cada gravação atômica devolve uma identidade exata da nova publicação
(raiz/relativo, dispositivo e inode), registrada antes de qualquer `flush` ou
`commit` posterior. Upload, composição, preparação para assinatura e corretiva
usam o mesmo
contrato. Em falha de flush, commit, integridade, concorrência ou outra exceção
pré-commit, a sessão sofre rollback e somente o arquivo regular criado por
aquela operação pode ser removido. A limpeza:

- repete contenção sob a raiz configurada e recusa symlink ou tipo não regular;
- compara dispositivo/inode, de modo que um arquivo preexistente ou substituído
  nunca seja removido;
- revalida imediatamente antes de `unlink` e faz `fsync` do diretório quando o
  filesystem suporta;
- jamais troca o erro transacional original por sucesso;
- registra falha somente com evento técnico e classe de erro, sem paciente,
  filename, template, conteúdo PDF ou caminho absoluto.

Falha abrupta do host fora do controle da transação ainda exige reconciliação;
não se apaga arquivo por inferência.

## Reconciliação storage ↔ banco

O comando administrativo é somente leitura por padrão e não cria a raiz:

```bash
.venv/bin/python -m app.cli reconciliar-laudos --json
```

Ele compara `report_document_versions` com arquivos regulares sob a raiz e
reporta apenas IDs técnicos, IDs opacos de achado e contagens agregadas. Detecta
linha sem arquivo, arquivo sem linha, divergência de SHA-256/tamanho/páginas,
permissões inseguras, symlink, tipo inesperado e PDF inválido. Nunca imprime
identidade do paciente, exame, texto de template, filename, bytes ou caminho
absoluto.

Não execute reconciliação destrutiva em produção sem janela aprovada, backup
coordenado e ensaio de restauração. Mesmo em ambiente autorizado, exclusão de
órfãos exige simultaneamente:

```bash
.venv/bin/python -m app.cli reconciliar-laudos \
  --delete-orphans \
  --backup-postgresql-e-storage-confirmado
```

e digitar exatamente `EXCLUIR APENAS PDFS ORFAOS CONFIRMADOS`. A frase não é
argumento nem variável de ambiente. Antes de cada remoção, banco, contenção,
tipo, dispositivo e inode são relidos; somente o órfão regular confirmado sob a
raiz exata é removido. Arquivo referenciado por qualquer linha — em especial
uma versão `finalizado` — nunca é apagado automaticamente. Divergências que não
sejam órfãos confirmados são preservadas para investigação.

Não copie a árvore para `data/`, `data-private/`, snapshots JSON públicos,
logs, tickets, mensagens ou artefatos do navegador. Nome de arquivo, PDF,
identificador de paciente e URL autenticada também não devem entrar nesses
meios.

## Backup coordenado

Banco e storage formam uma unidade de restauração: o banco aponta para cada
arquivo e registra seu hash. Um dump isolado ou uma cópia isolada dos PDFs não
é backup suficiente.

Procedimento esperado para uma janela autorizada:

1. bloquear novas escritas e parar a API por uma janela curta;
2. criar e validar o `pg_dump --format=custom` conforme o runbook M15;
3. copiar a raiz completa para mídia privada e criptografada, preservando
   proprietário, grupo, modos, ACLs e atributos;
4. gerar checksums do dump, do arquivo de backup do storage e de um manifesto
   técnico sem PII;
5. registrar horário UTC, revisão Alembic, responsáveis, destino, tamanho,
   contagens agregadas e resultado da validação;
6. reiniciar a API somente depois de os dois artefatos estarem íntegros.

O destino de backup deve ser `root:root` `0700`, com artefatos `0600`, acesso
por necessidade, criptografia em trânsito e em repouso e cópia fora do domínio
de falha primário. O manifesto não deve conter código de exame, nome de
arquivo, UUID de documento ou qualquer conteúdo clínico.

O script existente `backup-postgresql-m15.sh` cobre o dump PostgreSQL, mas não
copia os PDFs. A etapa de storage e a coordenação da janela continuam
obrigatórias; não anuncie um backup M24A completo se apenas o dump existir.

## Restauração e ensaio

Restaure primeiro em ambiente isolado, nunca sobre produção:

1. valide checksum, propriedade e modo dos artefatos antes de abrir;
2. restaure o dump em banco novo;
3. restaure a árvore em raiz nova, preservando modos e sem symlinks;
4. aponte uma configuração isolada para o novo banco e a nova raiz;
5. confirme a revisão Alembic esperada e exatamente um head;
6. reconcilie contagens de versões e arquivos, existência, tamanho e SHA-256;
7. exercite com conta sintética a autenticação, preview inline e download;
8. registre o ensaio sem PII e só então aprove eventual troca de conexão.

Arquivo ausente, extra, hash divergente, modo permissivo ou referência que
escape da raiz interrompe a restauração. Não “corrija” divergência apagando
linha ou PDF. Preserve a evidência e investigue.

## Retenção e exclusão controlada

O comportamento atual aprovado pelo owner é preservação conservadora. A
política de retenção de negócio continua pendente, sem prazo inventado:

- não há expiração automática de PDF ou linha de banco;
- não há endpoint de exclusão de laudo;
- não há job de purge;
- documento finalizado/assinado e suas versões permanecem preservados;
- correção cria outro documento e conserva o anterior;
- backup não aplica rotação destrutiva por suposição;
- reconciliação só pode remover arquivo regular que seja órfão de banco
  confirmado, usando todas as guardas e autorizações existentes.

Uma futura exclusão controlada precisa definir base legal, prazo, exceções por
litígio/auditoria, alcance sobre backups, dupla autorização, ordem transacional,
evidência append-only e forma de reconciliação. Ela não pode ser implementada
como `rm`, edição manual no banco ou deleção silenciosa. Até esse processo
existir, qualquer pedido de eliminação deve ser encaminhado ao responsável por
privacidade e ao responsável clínico, com acesso ao conteúdo restrito durante
a análise.

## LGPD, acesso, rastreabilidade e auditoria

Aplicam-se minimização, necessidade, menor privilégio, finalidade clínica
aprovada e resposta a incidente. A matriz técnica atual é:

| Papel explícito/efetivo | Acesso no fluxo M24C |
| --- | --- |
| `leitura` | nenhum acesso a laudo |
| `operacional` | localizar exame, enviar/atribuir, reatribuir antes do rascunho e acompanhar estado técnico |
| `gestor` | não recebe autoria, edição clínica ou assinatura por hierarquia |
| `admin` | administra conta/perfil médico e revisões de template; não recebe autoria |
| `medico` ativo, verificado e atribuído | fila própria, PDF atribuído, composição, prévia e preparação para assinatura |

A trilha append-only registra upload/atribuição, reatribuição, composição,
preparação para assinatura, corretiva, administração de perfil/template e
cada entrega inline ou download bem-sucedida. O evento de entrega contém
somente IDs técnicos da
versão/documento, modo e estado institucional; nunca paciente, filename, texto,
caminho, bytes ou URL autenticada. A gravação é confirmada antes de servir o
PDF. Access log com URL não é substituto aceitável.

Não habilite access log detalhado nem registre querystring, corpo multipart,
nome original, caminho, identificador de paciente ou conteúdo PDF. A revisão
periódica deve cobrir contas ativas, papéis efetivos, downloads excepcionais,
ações irreversíveis, falhas de autenticação, integridade do storage e ensaios
de restauração. Suspeita de acesso indevido exige preservação de evidência,
revogação de sessão/conta conforme o runbook e acionamento do processo de
incidente LGPD.

## Pré-requisitos de implantação

M24A é **NO-GO** enquanto qualquer item abaixo estiver pendente:

- PostgreSQL 16, backup coordenado e restauração ensaiada;
- Alembic em `4c9e2f7a6b31` com exatamente um head;
- raiz privada provisionada, não symlink, `soprolife:soprolife` `0700`;
- `M15_REPORTS_STORAGE_DIR` no EnvironmentFile e o mesmo caminho na allowlist
  `ReadWritePaths` efetiva, de forma exata e sem pai gravável mais amplo;
- gate específico de laudos, autorização humana exclusiva, atestado de backup
  coordenado e preflight/postflight HTTPS aprovados;
- capacidade/monitoramento de disco e alerta de falha de backup;
- proxy da mesma origem com limites dedicados ao upload e à resposta PDF;
- `M15_REPORTS_ENABLED=false` e `reports_enabled=false` preservados até a
  autorização explícita de feature enablement;
- HTTPS privado, cookie seguro, CSRF e RBAC revisados;
- usuários mínimos, papel médico explícito, perfil verificado e atribuição
  revisados;
- templates clínicos aprovados cadastrados por admin — nunca semeados por
  esta entrega;
- aceite explícito de que o runtime chega somente a
  **assinatura qualificada pendente** enquanto não houver provedor real;
- plano de incidente, retenção/exclusão e governança da auditoria de leitura
  aprovados;
- suíte SQLite, PostgreSQL 16 efêmero, migrações, Chrome E2E e quality gate
  verdes no commit candidato.

O deploy técnico não deve criar template, usuário, PDF ou dado clínico, nem
ativar a funcionalidade por conta própria.

## Rollback

Rollback de interface/código não é autorização para apagar banco ou storage.

1. interrompa novas ações no fluxo e preserve os logs seguros;
2. preserve/desative primeiro as duas flags exclusivas de laudos; não é
   necessário desligar o restante do Núcleo M15;
3. reverta código e unit pelo procedimento Git/systemd aprovado;
4. preserve tabelas M24A, PDFs, backups e o EnvironmentFile;
5. valide o sistema anterior antes de reabrir acesso.

Não execute downgrade Alembic em produção com laudos. Os downgrades
`4c9e2f7a6b31`, `8d4b1a2c9f70` e `5f0aea639d3d` são exercitados apenas em
banco efêmero; com dados, a
estratégia segura é restaurar o par coordenado banco+storage em ambiente novo
e promover somente após reconciliação. Remover `ReadWritePaths` ou a variável
enquanto a versão antiga ainda precisa servir PDFs também quebra
preview/download e não constitui rollback íntegro.

## Bloqueios clínicos, legais e de produto ainda abertos

Nenhuma das decisões abaixo foi tomada por esta implementação:

1. templates clínicos reais de produção, conteúdo, nomenclatura, aprovação e
   governança;
2. aprovação jurídica do uso da identidade profissional congelada no laudo;
3. piloto formal e desidentificado de compatibilidade com PDFs dos
   equipamentos;
4. política de retenção de negócio e exclusão, inclusive backups, sem alterar
   a preservação conservadora atual;
5. redação jurídica final do rodapé do laudo;
6. provedor ICP-Brasil, certificado, custódia, renovação, revogação e
   governança.

Até essas decisões serem formalmente aprovadas, os seis templates provisórios
ficam bloqueados, nenhum médico/CRM é inferido, nenhuma assinatura é alegada,
nenhum prazo de retenção é inventado e nenhum rodapé legal é tratado como
definitivo.

M24B fecha somente hardening técnico pré-habilitação. Não conclui nenhum desses
bloqueios e **não significa que laudos estejam prontos para habilitar**.
