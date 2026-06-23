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

## Funções usadas na planilha privada

- setupSoproLifeSheetsLite
- setupValidacoesSoproLife
- inserirDadosFicticiosSoproLife
- atualizarResumoDashboardSoproLife
- sincronizarCRMPacientesSoproLife

## Objetivo

Criar e manter uma planilha privada com:
- abas padronizadas;
- cabeçalhos;
- listas suspensas;
- dados fictícios para teste;
- resumo agregado para futura leitura pelo painel.

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
