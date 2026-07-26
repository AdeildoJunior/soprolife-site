# M24A — operação segura dos laudos PDF

Este runbook cobre somente configuração e governança do armazenamento de
laudos. Ele não autoriza implantação, acesso à VPS, uso de dados reais,
definição de conteúdo clínico ou contratação de assinatura digital.

## Configuração de `M15_REPORTS_STORAGE_DIR`

`M15_REPORTS_STORAGE_DIR` é obrigatório para enviar, compor, visualizar ou
baixar laudos. Sem ele, a API falha fechada com serviço indisponível apenas
nas operações de arquivo. O valor precisa:

- ser um caminho absoluto;
- ficar fora de qualquer worktree/repositório Git;
- não ser, nem resolver para, um link simbólico;
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

O serviço já usa `UMask=0077`, e a camada de armazenamento reaplica `0700` e
`0600`. ACLs que concedam leitura a outros usuários, diretório world-readable,
symlink, volume público ou montagem compartilhada sem controle são condição
de **NO-GO**.

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

O nome enviado pelo navegador nunca compõe o caminho. A árvore interna usa
somente UUIDs gerados pelo servidor:

```text
<raiz>/laudos/<exam_uuid>/<document_uuid>/<version_uuid>.pdf
```

O banco guarda o caminho relativo, SHA-256, tamanho, páginas e ciclo de vida.
A API não devolve caminho de filesystem nem URL pública de documento. Preview
e download passam por sessão autenticada e viram `Blob` temporário no
navegador, com cache privado desativado.

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

O período de retenção ainda não foi decidido. Até existir decisão clínica,
jurídica e de privacidade aprovada:

- não há expiração automática;
- não há endpoint de exclusão de laudo;
- documento finalizado e suas versões permanecem imutáveis;
- correção cria outro documento e conserva o anterior;
- backup não deve aplicar rotação destrutiva por suposição.

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

| Papel efetivo | Acesso no fluxo |
| --- | --- |
| `leitura` | listar metadados, visualizar e baixar PDF autenticado |
| `operacional` | capacidades de leitura, upload, composição, revisão e corretiva |
| `gestor` | capacidades operacionais e finalização irreversível |
| `admin` | hierarquia completa e administração de templates |

`gestor` é o papel privilegiado já existente no contrato técnico. Isso não
define quem é o médico responsável, quem pode assinar legalmente nem qual CRM
deve constar no laudo.

A trilha append-only registra upload, composição, submissão, finalização,
corretiva e administração de templates com usuário, horário e IDs técnicos,
sem PDF ou PII nos detalhes. Preview e download são autenticados e submetidos
a RBAC, mas esta etapa não criou evento append-only para cada leitura. Antes
de go-live, segurança, privacidade e área clínica devem aprovar a exigência,
granularidade e retenção da auditoria de leitura; access log com URL não é
substituto aceitável.

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
- Alembic em `5f0aea639d3d` com exatamente um head;
- raiz privada provisionada, não symlink, `soprolife:soprolife` `0700`;
- `M15_REPORTS_STORAGE_DIR` no EnvironmentFile e o mesmo caminho na allowlist
  `ReadWritePaths` efetiva;
- capacidade/monitoramento de disco e alerta de falha de backup;
- proxy da mesma origem com limites dedicados ao upload e à resposta PDF;
- HTTPS privado, cookie seguro, CSRF e RBAC revisados;
- usuários mínimos e revisão de papéis concluídos;
- templates clínicos aprovados cadastrados por admin — nunca semeados por
  esta entrega;
- aceite explícito de que toda finalização mostrará
  **assinatura digital pendente** enquanto não houver provedor real;
- plano de incidente, retenção/exclusão e expectativa de auditoria de leitura
  aprovados;
- suíte SQLite, PostgreSQL 16 efêmero, migrações, Chrome E2E e quality gate
  verdes no commit candidato.

O deploy técnico não deve criar template, usuário, PDF ou dado clínico, nem
ativar a funcionalidade por conta própria.

## Rollback

Rollback de interface/código não é autorização para apagar banco ou storage.

1. interrompa novas ações no fluxo e preserve os logs seguros;
2. se necessário, desative o M15 pelo controle existente, ciente de que a flag
   é ampla e oculta também outras funções do Núcleo;
3. reverta código e unit pelo procedimento Git/systemd aprovado;
4. preserve tabelas M24A, PDFs, backups e o EnvironmentFile;
5. valide o sistema anterior antes de reabrir acesso.

Não execute downgrade Alembic em produção com laudos. O downgrade
`5f0aea639d3d` é exercitado apenas em banco efêmero/vazio; com dados, a
estratégia segura é restaurar o par coordenado banco+storage em ambiente novo
e promover somente após reconciliação. Remover `ReadWritePaths` ou a variável
enquanto a versão antiga ainda precisa servir PDFs também quebra
preview/download e não constitui rollback íntegro.

## Decisões de produto e clínicas ainda abertas

Nenhuma das decisões abaixo foi tomada por esta implementação:

1. conteúdo, nomenclatura, aprovação e governança dos templates clínicos;
2. identidade do médico, CRM/UF, responsabilidade e autorização de
   finalização/assinatura;
3. provedor ICP-Brasil, integração, política de certificado, custódia da chave,
   renovação, revogação e resposta a comprometimento;
4. período de retenção e alcance da exclusão em backups;
5. redação jurídica final do rodapé do laudo.

Até essas decisões serem formalmente aprovadas, os templates de produção
permanecem vazios, nenhum médico/CRM é inferido, nenhuma assinatura é alegada,
nenhum prazo de retenção é inventado e nenhum rodapé legal é tratado como
definitivo.
