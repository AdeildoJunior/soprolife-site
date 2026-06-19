# Segurança do Painel SoproLife

Este painel é destinado ao controle interno da SoproLife.

## Regra principal

Não inserir dados reais de pacientes no repositório.

Não versionar:
- CPF;
- RG;
- telefone real de paciente;
- endereço de paciente;
- pedido médico;
- resultado de exame;
- dado clínico identificável;
- informação de saúde;
- conversas de WhatsApp;
- dados financeiros identificáveis de pacientes.

## Dados permitidos no Git

Apenas:
- dados fictícios;
- dados anônimos;
- exemplos genéricos;
- dados institucionais da empresa;
- documentação operacional;
- indicadores agregados numéricos gerados pelo fluxo seguro
  (ex: `pacientesEmAcompanhamento`, `examesEspirometriaRealizados`,
  `teleconsultasRealizadas`, `followupsPendentes`, `lembretesWhatsAppPendentes`,
  `recorrenciasAtivas`, `consultasPrevistas`).

Esses indicadores de CRM/atendimento são totais numéricos calculados pelo Apps Script
a partir das abas privadas. Nunca contêm nome, telefone, CPF ou dado clínico individual.
O script `check-access.sh` valida que os valores são números e que nenhum dado sensível
chegou ao JSON público.

## Dados privados

Dados reais, quando existirem, devem ficar fora do Git, em fontes privadas como:
- Google Sheets privado;
- CRM com autenticação;
- banco de dados privado;
- arquivos locais ignorados pelo Git.

## Pastas e arquivos ignorados

A pasta abaixo é reservada para dados locais e não deve ser enviada ao GitHub:

painel-soprolife/data-private/

Também são ignorados arquivos:
- *.local.json
- *.private.json
- *.secret.json
- .env
