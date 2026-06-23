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

Finalidade: acompanhar contatos recebidos e etapa do atendimento.

Campos:
- lead_id
- data_entrada
- origem
- canal
- servico_interesse
- etapa
- responsavel
- proxima_acao
- data_proxima_acao
- observacao_anonima

Observação: não usar telefone real de paciente no GitHub.

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
