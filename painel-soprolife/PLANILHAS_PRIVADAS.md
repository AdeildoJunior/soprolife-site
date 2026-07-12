# Estrutura das Planilhas Privadas — Painel SoproLife

Este documento define as planilhas privadas que futuramente alimentarão o Painel SoproLife.

> **M14.3** — a arquitetura canônica das abas (entidades, IDs, datas com
> precisão, enums, Pastore, matriz de simplificação e reconciliação
> histórica) está definida em `docs/arquitetura-canonica-abas.md` e nos
> contratos `core/contracts/abas-manifest.json`, `enums-canonicos.json` e
> `ids-canonicos.json`. Em caso de divergência com este documento, os
> contratos canônicos prevalecem. O Manual das Abas passou a ser gerado a
> partir do manifesto (`scripts/generate-manual-abas-gs.py`).

## Regra principal

As planilhas reais devem ficar fora do GitHub.

O repositório pode conter apenas:
- modelos vazios;
- cabeçalhos;
- exemplos fictícios;
- documentação operacional.

Nunca inserir no GitHub:
- dados reais de pacientes;
- CPF;
- telefone real de paciente;
- pedido médico;
- laudo;
- resultado de exame;
- dados clínicos identificáveis;
- conversas de WhatsApp;
- chaves de API;
- tokens;
- senhas.

## Planilhas iniciais

### 1. Leads

Finalidade: registrar contatos interessados **antes** do primeiro atendimento (pré-atendimento / funil de interesse).

Um lead só migra para **CRM Pacientes** quando realiza o primeiro atendimento.
Leads B2B (Clínicas / PCMSO) migram para **CRM Clínicas**, não para CRM Pacientes.
Enquanto não convertido, o lead permanece na aba Leads.

Campos (14 colunas — cabeçalho canônico):
- `lead_id` — identificador único (ex.: LEAD-20260601-001)
- `data_contato` — data do primeiro contato (dd/MM/yyyy)
- `nome` — primeiro nome ou apelido (nunca nome completo em arquivos commitáveis)
- `telefone_whatsapp` — **privado**: apenas em planilha e `data-private/leads.local.json` (gitignored)
- `servico_interesse` — dropdown padronizado (ver abaixo); define roteamento B2B vs. pessoa física
- `origem` — Google, Instagram, Indicação, etc.
- `canal` — WhatsApp, Site, E-mail, etc.
- `bairro_regiao` — bairro ou região (sem endereço completo)
- `tem_pedido_medico` — dropdown padronizado
- `etapa` — dropdown padronizado (estágio no funil)
- `responsavel` — membro da equipe responsável
- `proxima_acao` — ação concreta planejada
- `data_proxima_acao` — data prevista para a próxima ação (dd/MM/yyyy)
- `observacao` — **privado**: observação operacional livre (sem CPF, laudo, diagnóstico, pedido médico)

Dropdowns `servico_interesse`: Espirometria, Espirometria domiciliar, Teleconsulta respiratória, Consulta pneumologista, Clínicas, PCMSO / empresa

Dropdowns `etapa`: Novo contato, Em conversa, Aguardando retorno, Agendado, Não respondeu, Desistiu, Convertido em paciente, Convertido em clínica/parceiro

Dropdowns `canal`: WhatsApp, Site, Google, Instagram, E-mail, Telefone, Indicação, Presencial, Outro

Dropdowns `origem`: Google, WhatsApp, Instagram, Site, Indicação, Clínica parceira, Tráfego pago, Outro

Dropdowns `tem_pedido_medico`: Sim, Não, Não informado, Não se aplica

Arquivos locais relacionados:
- `data-private/leads.local.json` — dados completos com telefone e observacao (gitignored)
- `data/leads-summary.local.json` — resumo seguro sem telefone (gitignored)
- `data/leads.json` — dados demonstrativos commitáveis (sem telefone real, sem observacao)

Campos removidos na v2 (dados absorvidos em `observacao` se existentes na migração):
- `preferencia_atendimento` — agora vai para observacao se preenchido
- `valor_informado` — agora vai para observacao se preenchido
- `consentimento_whatsapp` — agora vai para observacao se preenchido

Observação: nunca usar telefone real nem nome completo de paciente em arquivos commitáveis.

### 2. CRM Clínicas

Finalidade: controlar clínicas abordadas, parcerias e propostas.

Campos:
- clinica_id
- nome_clinica
- bairro
- regiao
- tipo_clinica
- etapa
- ultima_interacao
- proxima_acao
- responsavel
- prioridade
- observacao

Observação: dados comerciais de clínicas podem existir na planilha privada, mas não devem ser expostos em arquivos públicos.

### 3. Tarefas

Finalidade: organizar pendências comerciais, operacionais, marketing e documentos.

Campos:
- tarefa_id
- area
- titulo
- prioridade
- status
- responsavel
- prazo
- origem
- observacao

### 4. Financeiro

Finalidade: acompanhar receitas, pendências e previsões.

Campos:
- lancamento_id
- data
- tipo
- categoria
- servico
- origem
- valor_estimado
- valor_recebido
- status
- forma_pagamento
- observacao_anonima

Observação: não identificar pacientes por pagamento.

### 5. Marketing e Conteúdo

Finalidade: controlar posts, campanhas, SEO e publicações.

Campos:
- conteudo_id
- canal
- tema
- formato
- etapa
- data_planejada
- data_publicacao
- cta
- status
- metrica_agregada
- observacao

### 6. Agenda Operacional

Finalidade: organizar exames, consultas, reuniões e visitas sem expor dados sensíveis no GitHub.

Campos:
- evento_id
- data
- hora
- tipo_evento
- local
- responsavel
- status
- observacao_anonima

## CRM de pacientes — arquitetura de três abas

A carteira de pacientes é dividida em três abas com papéis distintos:

### CRM Pacientes (carteira-mãe)

Uma linha por pessoa. Representa o relacionamento geral com o paciente, não um atendimento específico.

Campos:
- paciente_id (ex.: PAC-20250615-001)
- data_cadastro
- primeiro_nome
- telefone
- ultimo_servico (Espirometria / Consulta / Espirometria + Teleconsulta)
- status_relacionamento
- proximo_contato
- motivo_proximo_contato
- canal
- responsavel
- consentimento_whatsapp
- observacao_privada_minima

### CRM Espirometria (histórico de exames)

Uma linha por exame realizado. A consolidação automática em CRM Pacientes está BLOQUEADA (M14.3A) — vínculo por paciente_id e reconciliação com decisão humana.

Campos principais:
- exame_id
- data_entrada
- primeiro_nome
- telefone
- servico
- status_exame
- data_exame
- proximo_contato
- motivo_proximo_contato
- canal
- responsavel
- consentimento_whatsapp

### CRM Consultas (histórico de consultas)

Uma linha por consulta ou teleconsulta realizada. A consolidação automática em CRM Pacientes está BLOQUEADA (M14.3A).

Campos principais:
- consulta_id
- data_entrada
- primeiro_nome
- telefone
- tipo_consulta
- status
- medica
- data_consulta
- proximo_contato
- motivo_proximo_contato
- canal
- responsavel
- consentimento_whatsapp

### Follow-up WhatsApp (fila de contatos)

Fila de mensagens futuras a serem enviadas por WhatsApp. Gerenciada separadamente do CRM Pacientes.

Campos principais:
- followup_id
- data_criacao
- primeiro_nome
- telefone
- tipo_mensagem
- data_prevista
- status
- canal
- responsavel
- template_usado
- consentimento

## Conceito central: Leads vs. CRM Pacientes

A distinção mais importante para a operação correta da planilha:

| Aba | Momento | Quem está aqui |
|---|---|---|
| **Leads** | Pré-atendimento | Contatos que demonstraram interesse mas ainda NÃO realizaram nenhum atendimento |
| **CRM Pacientes** | Pós-atendimento | Pessoas que já realizaram ao menos um atendimento (carteira-mãe) |
| **CRM Espirometria** | Histórico | Uma linha por exame realizado; vínculo com CRM Pacientes via paciente_id (reconciliação com decisão humana) |
| **CRM Consultas** | Histórico | Uma linha por consulta realizada; vínculo com CRM Pacientes via paciente_id (reconciliação com decisão humana) |
| **Follow-up WhatsApp** | Ações futuras | Fila de mensagens e contatos planejados; não é cadastro permanente |

Um lead se torna paciente quando realiza o primeiro atendimento.
Nesse momento ele deve ser registrado em CRM Espirometria ou CRM Consultas
(e em CRM Pacientes pela própria conversão) — a antiga sincronização em massa
está bloqueada; a reconciliação do histórico é auditada em modo read-only.

## Automação: organizarLeadsOperacionaisSoproLife — BLOQUEADA (M14.3A)

A migração de cabeçalho da aba Leads foi BLOQUEADA (a função lança erro): o
fluxo antigo limpava e reconstruía a aba inteira — migração destrutiva exige
backup validado, manifesto, dry-run comparado e aprovação explícita (M14.3B).

## Automação: organizarLeadsEManualPlanilhasSoproLife (aditiva)

O arquivo `painel-soprolife/apps-script/limpar-leads-e-manual-abas.gs` mantém a
função `organizarLeadsEManualPlanilhasSoproLife()` em modo ADITIVO: backup com
nome único, dropdowns, notas de cabeçalho e Manual das Abas (via gerador do
manifesto). Desde a M14.3A (2ª rodada) NENHUMA linha é removida automaticamente
— a antiga limpeza de "dados demo" (deleteRow por substring) foi removida por
risco de falso positivo.

## Sincronização de CRM Pacientes — BLOQUEADA (M14.3A)

A antiga `sincronizarCRMPacientesSoproLife()` (sync-crm-pacientes.gs) está
**bloqueada e lança erro**: ela apagava e reconstruía a aba inteira e
deduplicava por telefone/primeiro nome — comportamento reprovado em auditoria.

Regras vigentes:
1. CRM Pacientes é **mestre persistente** — nenhuma reescrita integral,
   nenhuma linha eliminada, nenhum ID recalculado.
2. **Nome sozinho nunca vincula**; telefone gera apenas **candidato**;
   telefone em mais de um cadastro = **ambiguous**; candidato não confirmado
   = **pending**; sem informação suficiente = **unmatchable**.
3. Nenhuma fusão é automática — toda decisão é humana.
4. Auditoria read-only: `scripts/reconciliar-historico.py --audit`.
5. A migração incremental real (M14.3B) depende de backup e autorização.

## Integração futura

A ordem recomendada de automação é:

1. Google Sheets privado;
2. leitura segura pelo painel;
3. dados agregados no dashboard;
4. autenticação/login;
5. banco privado ou CRM completo.
