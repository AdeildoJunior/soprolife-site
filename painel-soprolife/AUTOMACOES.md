# Fontes de Dados e Automações — Painel SoproLife

Este documento define como o Painel SoproLife poderá ser automatizado sem expor dados sensíveis no GitHub.

## Regra geral

O painel é privado e serve para controle interno, planejamento e expansão da SoproLife.

Dados reais devem ficar em fontes privadas, nunca versionados no repositório.

## Fontes futuras de dados

| Área | Fonte provável | Uso no painel | Observação de segurança |
|---|---|---|---|
| Leads | Google Sheets privado / WhatsApp Business | origem, status, serviço desejado, etapa do atendimento | não salvar telefone real no Git |
| Agenda | Google Calendar / Workspace | consultas, exames, reuniões, visitas a clínicas | não expor nomes de pacientes publicamente |
| E-mail | Gmail / Workspace | propostas, respostas de clínicas, pendências | evitar copiar conteúdo sensível para JSON |
| CRM Clínicas | Google Sheets / CRM privado | clínicas abordadas, estágio, retorno, proposta | pode usar nome de clínica; evitar dados pessoais desnecessários |
| Marketing | Search Console / GA4 | cliques, impressões, termos de busca, páginas | dados agregados são permitidos |
| Instagram | Meta / planilha editorial | posts publicados, calendário, desempenho | usar métricas agregadas |
| LinkedIn | LinkedIn / controle manual inicial | posts, conexões, parcerias | dados agregados |
| Financeiro | planilha privada / sistema futuro | receita, pendências, serviços | não expor dados identificáveis de pacientes |
| Documentos | arquivos institucionais | validade, status, renovação | só documentos da empresa |
| Tarefas | painel local / Sheets / automação | pendências operacionais e comerciais | não incluir dados clínicos identificáveis |

## Fase 1 — Manual seguro

- Painel visual com dados fictícios.
- Atualização manual dos JSONs demonstrativos.
- Nenhum dado real de paciente no GitHub.

## Fase 2 — Google Sheets privado

- Criar planilhas privadas para leads, clínicas, tarefas e financeiro.
- O painel deve consumir dados agregados ou anonimizados.
- A planilha real não deve ser enviada ao GitHub.

## Fase 3 — Integrações

Possíveis integrações futuras:

- Google Calendar para agenda;
- Gmail para propostas e respostas;
- Google Search Console para SEO;
- Google Analytics 4 para tráfego;
- Meta/Instagram para conteúdo;
- WhatsApp Business para origem de contatos;
- CRM privado ou banco de dados.

## Fase 4 — Painel com login

Quando houver dados reais, o painel deve evoluir para ambiente privado com autenticação.

Possíveis caminhos:
- Supabase;
- Firebase;
- Cloudflare Access;
- servidor privado;
- aplicação com login e permissões.

## O que nunca colocar no Git

- CPF;
- RG;
- telefone real de paciente;
- endereço de paciente;
- pedido médico;
- laudo;
- resultado de exame;
- histórico clínico;
- conversa de WhatsApp de paciente;
- dados financeiros identificáveis de pacientes;
- chaves de API;
- senhas;
- tokens;
- arquivos .env.

## Direção estratégica

O Painel SoproLife deve funcionar como um centro interno de comando para:

- acompanhar crescimento;
- organizar clínicas parceiras;
- controlar leads;
- planejar marketing;
- acompanhar SEO;
- controlar documentos;
- visualizar financeiro;
- priorizar tarefas;
- preparar futuras automações.
