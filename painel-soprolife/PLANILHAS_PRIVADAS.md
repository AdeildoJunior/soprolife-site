# Estrutura das Planilhas Privadas — Painel SoproLife

Este documento define as planilhas privadas que futuramente alimentarão o Painel SoproLife.

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

Uma linha por exame realizado. Alimenta automaticamente o CRM Pacientes.

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

Uma linha por consulta ou teleconsulta realizada. Alimenta automaticamente o CRM Pacientes.

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
| **CRM Espirometria** | Histórico | Uma linha por exame realizado; alimenta o CRM Pacientes automaticamente |
| **CRM Consultas** | Histórico | Uma linha por consulta realizada; alimenta o CRM Pacientes automaticamente |
| **Follow-up WhatsApp** | Ações futuras | Fila de mensagens e contatos planejados; não é cadastro permanente |

Um lead se torna paciente quando realiza o primeiro atendimento.
Nesse momento ele deve ser registrado em CRM Espirometria ou CRM Consultas,
e a sincronização cuida de consolidá-lo em CRM Pacientes.

## Automação: organizarLeadsOperacionaisSoproLife (recomendada)

O arquivo `painel-soprolife/apps-script/organizar-leads-operacionais.gs` contém a função
`organizarLeadsOperacionaisSoproLife()`.

O que ela faz:
1. Cria backup da aba `Leads` com nome `_Backup_Leads_Operacional_YYYYMMDD_HHMM`.
2. Detecta o cabeçalho existente pelo nome das colunas (não por posição fixa).
3. Migra dados existentes para o cabeçalho canônico de 17 colunas, preservando tudo.
4. Remove apenas linhas com marcadores explícitos de dados demonstrativos.
5. Aplica dropdowns em todas as 7 colunas com lista padronizada.
6. Adiciona notas explicativas nos cabeçalhos.
7. Usa apenas `Logger.log` — sem alertas de UI na função principal.

## Automação: organizarLeadsEManualPlanilhasSoproLife (compatibilidade)

O arquivo `painel-soprolife/apps-script/limpar-leads-e-manual-abas.gs` mantém a função
`organizarLeadsEManualPlanilhasSoproLife()` para compatibilidade com a estrutura anterior
de 10 colunas. Também cria/atualiza a aba `Manual das Abas`.

## Automação: sincronizarCRMPacientesSoproLife

O arquivo `painel-soprolife/apps-script/sync-crm-pacientes.gs` contém a função
`sincronizarCRMPacientesSoproLife()`, que consolida CRM Espirometria e CRM Consultas
em CRM Pacientes de forma segura.

Regras de deduplicação:
1. Telefone normalizado tem prioridade como chave.
2. Se não houver telefone, o nome normalizado é usado.
3. Pacientes existentes são preservados; nenhuma informação é perdida.

## Integração futura

A ordem recomendada de automação é:

1. Google Sheets privado;
2. leitura segura pelo painel;
3. dados agregados no dashboard;
4. autenticação/login;
5. banco privado ou CRM completo.
