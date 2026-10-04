# SoproLife Site e Painel — Instruções para IAs

Este repositório contém o site da SoproLife e o início do Painel SoproLife / SoproLife Command Center.

## Antes de alterar o sistema SoproLife

1. Ler `docs/SOPROLIFE_MASTER_CONTEXT.md` (estado vigente, invariantes e pendências).
2. Carregar as skills pertinentes de `.claude/skills/` (tabela no §12 do contexto mestre).
3. Verificar o HEAD remoto (`git fetch origin`; branch produtiva `painel-soprolife-v01`).
4. Preservar os invariantes do §13 do contexto mestre.
5. Ao concluir uma mudança arquitetural relevante, atualizar o contexto mestre no mesmo commit.

Os relatórios em `docs/archive/reports/` são histórico, não especificação.

## Contexto da empresa

A SoproLife é uma empresa de saúde focada em:
- espirometria;
- diagnósticos complementares;
- teleconsulta médica;
- atendimento domiciliar quando aplicável;
- parcerias com clínicas, consultórios e empresas/PCMSO;
- prospecção B2B;
- marketing digital local.

## Projeto atual

Estamos desenvolvendo um dashboard chamado:

Painel SoproLife / SoproLife Command Center

Objetivo:
Criar um painel visual, leve e interativo para acompanhar operação, CRM, parcerias, agendamentos, marketing, SEO e rotina comercial da SoproLife.

## Estrutura inicial

- painel-soprolife/index.html
- painel-soprolife/css/style.css
- painel-soprolife/js/app.js
- painel-soprolife/data/resumo.json
- painel-soprolife/data/crm-clinicas.json
- painel-soprolife/data/leads.json
- painel-soprolife/data/marketing.json

## MVP inicial

1. Painel Geral
2. CRM Clínicas
3. Leads e Agendamentos
4. Marketing & SEO

## Estilo visual

Usar aparência premium de empresa de saúde:
- navy profundo;
- teal/cinza claro/branco;
- cards limpos;
- menu lateral;
- gráficos simples;
- visual leve;
- layout responsivo;
- identidade profissional.

## Regras obrigatórias de segurança

Nesta fase, não inserir dados reais de pacientes.

Não usar:
- CPF de paciente;
- telefone real de paciente;
- pedido médico;
- dado clínico identificável;
- endereço de paciente;
- informação sensível de saúde.

Usar apenas:
- dados fictícios;
- dados anônimos;
- dados institucionais da empresa;
- exemplos genéricos como Lead 001, Clínica Exemplo, Paciente 001.

## Regra de trabalho

O ChatGPT está conduzindo a arquitetura e o passo a passo.
Antes de alterar arquivos, aguarde uma solicitação explícita do usuário ou do ChatGPT.
