# Apps Script — Google Sheets privado do Painel SoproLife

Esta pasta documenta os scripts usados na planilha privada do Painel SoproLife.

## Importante

Não colocar aqui:
- URL da planilha real;
- ID da planilha real;
- tokens;
- chaves de API;
- dados reais de pacientes;
- CPF;
- telefone real;
- pedido médico;
- laudo;
- resultado de exame.

## Funções disponíveis

### soprolife-sheets-template.gs
- `setupSoproLifeSheetsLite` — cria a estrutura de abas e cabeçalhos
- `setupValidacoesSoproLife` — aplica dropdowns e formatações de data/moeda
- `atualizarResumoDashboardSoproLife` — recalcula indicadores na aba Resumo Dashboard
- `onOpen` / `onEdit` — menu e gatilho automático de atualização

### sync-crm-pacientes.gs
- `sincronizarCRMPacientesSoproLife` — consolida CRM Espirometria e CRM Consultas em CRM Pacientes
- `formatarDataBRSoproLife(valor)` — helper compartilhado de normalização de datas

### limpar-leads-e-manual-abas.gs
- `organizarLeadsEManualPlanilhasSoproLife` — limpeza de dados demo, padronização de dropdowns e criação do Manual das Abas

## Conceito central: Leads vs. CRM Pacientes

A distinção mais importante para usar a planilha corretamente:

| Aba | Momento | O que registra |
|---|---|---|
| **Leads** | Pré-atendimento | Contatos interessados que ainda NÃO realizaram atendimento |
| **CRM Pacientes** | Pós-atendimento | Carteira-mãe — uma linha por pessoa, pós primeiro atendimento |
| **CRM Espirometria** | Histórico | Uma linha por exame realizado — alimenta CRM Pacientes |
| **CRM Consultas** | Histórico | Uma linha por consulta realizada — alimenta CRM Pacientes |
| **Follow-up WhatsApp** | Ações futuras | Fila de contatos planejados, não cadastro permanente |

Um lead migra para CRM Pacientes somente após o primeiro atendimento.

## Ordem de execução recomendada (planilha nova)

1. `setupSoproLifeSheetsLite` — cria abas e cabeçalhos
2. `setupValidacoesSoproLife` — aplica dropdowns iniciais
3. `organizarLeadsEManualPlanilhasSoproLife` — padroniza Leads e cria Manual das Abas
4. Inserir dados reais (pacientes, leads, clínicas) manualmente ou via importação
5. `sincronizarCRMPacientesSoproLife` — sincronizar CRM Pacientes (recorrente, via gatilho)
6. `atualizarResumoDashboardSoproLife` — atualizar painel (automático via onEdit)

## Arquitetura das abas de CRM de pacientes

| Aba | Papel |
|---|---|
| CRM Pacientes | Carteira-mãe: uma linha por pessoa, relacionamento geral |
| CRM Espirometria | Histórico de exames realizados (uma linha por exame) |
| CRM Consultas | Histórico de consultas/teleconsultas (uma linha por consulta) |
| Follow-up WhatsApp | Fila de contatos futuros a enviar por WhatsApp |

A função `sincronizarCRMPacientesSoproLife` (arquivo `sync-crm-pacientes.gs`)
consolida as duas abas de histórico em CRM Pacientes automaticamente.

Regras de deduplicação:
1. Dois registros com o mesmo telefone normalizado são o mesmo paciente.
2. Se não houver telefone, o nome normalizado é usado como chave.
3. Informações existentes em CRM Pacientes são preservadas; nada é apagado sem necessidade.

Formato de datas em CRM Pacientes:
- Todas as datas gravadas na aba `CRM Pacientes` usam o formato `dd/MM/yyyy` (ex.: `18/06/2026`).
- O helper `formatarDataBRSoproLife(valor)` normaliza Date objects, strings ISO, strings
  no formato `MM/yyyy` e nomes de mês em português (ex.: `dezembro/2026` → `01/12/2026`).
- Timezone: `America/Sao_Paulo`.
