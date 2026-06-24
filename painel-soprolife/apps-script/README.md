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

### converter-lead-em-paciente.gs *(conversão manual — ação selecionada)*
- `converterLeadSelecionadoSoproLife` — converte o lead da linha selecionada na aba Leads para os CRMs corretos
  - **Não escaneia todos os leads automaticamente**: age somente na linha que o usuário selecionou
  - Roteamento por `servico_interesse`:
    - B2B (Clínicas / PCMSO): exibe instrução → usuário cadastra manualmente em CRM Clínicas (não vai para CRM Pacientes)
    - Pessoa física: cria CRM Pacientes (se não existir)
    - Espirometria: pergunta se o exame JÁ foi realizado → só então cria CRM Espirometria
    - Consulta/Teleconsulta: pergunta se a consulta JÁ foi realizada → só então cria CRM Consultas
  - Validação: nome obrigatório; alerta se telefone ausente
  - Checagem de duplicata por telefone antes de criar qualquer CRM
  - Acrescenta "Convertido a partir da aba Leads (LEAD-xxx · dd/MM/yyyy)" em `observacao_privada_minima`
  - Não inventa: data de exame, médica, laudo, status — deixa em branco para a equipe preencher
  - Registra no "Log Centro Comando"
  - Após conversão: rodar `sincronizarCRMPacientesSoproLife` para consolidar CRM Pacientes
  - Acionado pelo menu: **SoproLife → Leads → Converter lead selecionado → CRM**

### organizar-leads-operacionais.gs *(recomendado — use este para planilha nova ou migração)*
- `organizarLeadsOperacionaisSoproLife` — migra aba Leads para cabeçalho canônico de 14 colunas
  - Backup automático (`_Backup_Leads_Operacional_YYYYMMDD_HHMM`)
  - Detecção de colunas pelo nome (não por posição fixa) — seguro para qualquer estrutura atual
  - Migração inteligente: mapeia `data_entrada` → `data_contato`, `observacao_privada_minima` → `observacao`
  - Campos removidos (`preferencia_atendimento`, `valor_informado`, `consentimento_whatsapp`) são incorporados em `observacao` se tiverem conteúdo relevante
  - Remove apenas linhas com marcadores de dados demonstrativos
  - Aplica 5 dropdowns: `servico_interesse`, `etapa` (inclui "Convertido em clínica/parceiro"), `canal`, `origem`, `tem_pedido_medico`
  - Adiciona notas explicativas nos 14 cabeçalhos
  - Usa apenas `Logger.log` — sem alertas de UI na função principal
  - Acionado pelo menu: **SoproLife → Leads → Organizar Leads Operacionais**

### limpar-leads-e-manual-abas.gs *(compatibilidade — estrutura anterior de 10 colunas)*
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
3. `organizarLeadsOperacionaisSoproLife` — migra Leads para 14 colunas operacionais (recomendado)
4. `organizarLeadsEManualPlanilhasSoproLife` — cria/atualiza o Manual das Abas
5. Inserir dados reais (leads, clínicas) manualmente ou via importação
6. Para leads B2C prontos: marcar `etapa = Convertido em paciente` e usar o menu para converter
7. Para leads B2B prontos: marcar `etapa = Convertido em clínica/parceiro` e cadastrar em CRM Clínicas
8. `converterLeadSelecionadoSoproLife` — menu SoproLife → Leads → Converter lead selecionado → CRM
9. `sincronizarCRMPacientesSoproLife` — consolida CRM Pacientes (recorrente, via gatilho)
10. `atualizarResumoDashboardSoproLife` — atualiza painel (automático via onEdit)

## Campos compartilhados entre Leads e CRM

Estes campos são copiados na conversão manual Lead → CRM Pacientes → CRM Espirometria / Consultas:

| Campo em Leads | CRM Pacientes | CRM Espirometria | CRM Consultas |
|---|---|---|---|
| `nome` | `primeiro_nome` | `primeiro_nome` | `primeiro_nome` |
| `telefone_whatsapp` | `telefone` | `telefone` | `telefone` |
| `servico_interesse` | `ultimo_servico` | `servico` | `tipo_consulta` |
| `origem` | — | `origem` | `origem` |
| `canal` | `canal` | `canal` | `canal` |
| `responsavel` | `responsavel` | `responsavel` | `responsavel` |
| `data_proxima_acao` | `proximo_contato` | `proximo_contato` | `proximo_contato` |
| `proxima_acao` | `motivo_proximo_contato` | `motivo_proximo_contato` | `motivo_proximo_contato` |
| `observacao` | `observacao_privada_minima` (+ prefixo origem) | — | — |
| (hoje) | `data_cadastro` | `data_entrada` | `data_entrada` |

Campos exclusivos do CRM (não inventados — deixados em branco para a equipe preencher):
- `exame_id` / `consulta_id` / `paciente_id` — gerado automaticamente com timestamp
- `status_exame` / `status` — valor neutro "A confirmar" quando criado via converter
- `data_exame` / `data_consulta` / `medica` / `laudo` — preenchidos pela equipe após atendimento

Campos do Leads removidos na v2 (não copiados ao CRM):
- `preferencia_atendimento`, `valor_informado`, `consentimento_whatsapp` — absorvidos em `observacao` durante migração

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
