# Apps Script — Resumo Dashboard Automático

Este diretório contém o Apps Script que atualiza automaticamente a aba
`Resumo Dashboard` da planilha privada da SoproLife com indicadores agregados.

## Arquivo

| Arquivo | Finalidade |
|---|---|
| `resumo-dashboard-auto.gs` | função principal de atualização |

## Como usar

### 1. Abrir o Apps Script da planilha

1. Abra a planilha **"SoproLife - Painel Interno - Dados Privados"** no Google Sheets.
2. No menu: **Extensões → Apps Script**.
3. Não apague scripts existentes da planilha.
4. No Apps Script, crie um novo arquivo chamado `resumo-dashboard-auto.gs` ou cole a função no final de um arquivo existente.
5. Cole o conteúdo de `resumo-dashboard-auto.gs`.
6. Salve (Ctrl+S ou ⌘+S).

### 2. Executar a função

1. No seletor de função (topo da tela), escolha:
   `atualizarResumoDashboardAutomaticoSoproLife`
2. Clique em **Executar**.
3. Na primeira execução, o Google pedirá permissão para acessar a planilha — clique em **Autorizar**.

### 3. Atualizar o painel local

Após a execução do Apps Script, rode no terminal:

```bash
painel-soprolife/scripts/update-local-data.sh
```

Isso lê a aba `Resumo Dashboard` (agora atualizada) e sincroniza o JSON seguro com o painel.

## O que o script faz

- Lê somente **contagens e somatórios** de cada aba privada.
- Grava **17 indicadores numéricos** na aba `Resumo Dashboard` no formato:

  | key | label | value |
  |---|---|---|
  | totalLeads | Total de leads | 5 |
  | pacientesEmAcompanhamento | Pacientes em acompanhamento | 12 |
  | ... | ... | ... |

- Não grava nomes, telefones, CPF ou dados clínicos individuais.
- Se uma aba não existir, usa 0 para os indicadores correspondentes.
- Se uma coluna não existir, usa 0.

## Indicadores gerados

| Chave | Origem | Cálculo |
|---|---|---|
| `totalLeads` | Leads | total de linhas preenchidas |
| `leadsNovos` | Leads | etapa contém "novo" |
| `leadsAgendados` | Leads | etapa contém "agendado" |
| `leadsConcluidos` | Leads | etapa contém "concluído" ou "finalizado" |
| `clinicasCadastradas` | CRM Clinicas | total de linhas preenchidas |
| `tarefasPendentes` | Tarefas | status não é "concluído" nem "feito" |
| `receitaPrevista` | Financeiro | soma de `valor_estimado` |
| `receitaRecebida` | Financeiro | soma de `valor_recebido` |
| `conteudosPlanejados` | Marketing Conteudo | status ou etapa contém "planejado" |
| `eventosAgendados` | Agenda Operacional | status contém "agendado" |
| `pacientesEmAcompanhamento` | CRM Pacientes | `status_relacionamento` contém "acompanhamento" |
| `examesEspirometriaRealizados` | CRM Espirometria | `status_exame` contém "realizado" |
| `teleconsultasRealizadas` | CRM Consultas | status contém "realizada" ou "realizado" |
| `followupsPendentes` | Follow-up WhatsApp | status contém "pendente" |
| `lembretesWhatsAppPendentes` | Follow-up WhatsApp | status "pendente" e canal "WhatsApp" |
| `recorrenciasAtivas` | CRM Espirometria | `proximo_contato` preenchido |
| `consultasPrevistas` | CRM Consultas | status contém "prevista", "agendada" ou "pendente" |

## Segurança

Os indicadores de atendimento/CRM (`pacientesEmAcompanhamento`,
`examesEspirometriaRealizados`, `teleconsultasRealizadas`, etc.) são calculados
contando linhas e usando somente colunas operacionais necessárias. Ele **nunca grava nem envia ao painel** nomes, telefones, CPF ou laudos.

Os dados privados ficam:
- nas abas internas da planilha (CRM Pacientes, CRM Espirometria, CRM Consultas,
  Follow-up WhatsApp);
- nunca no repositório Git;
- nunca no JSON público do painel.

O script `check-access.sh` valida automaticamente que o JSON gerado contém apenas
indicadores agregados numéricos antes de servir os dados ao painel.

## Automatizar com gatilho (opcional)

Para executar automaticamente a cada hora:

1. No Apps Script: **Acionadores → Adicionar acionador**.
2. Função: `atualizarResumoDashboardAutomaticoSoproLife`
3. Tipo de evento: **Baseado no tempo → Acionador por hora**.
4. Salvar.

Após cada execução automática, rode `update-local-data.sh` no terminal para
sincronizar o painel local.
