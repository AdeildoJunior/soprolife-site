# Segurança do Painel SoproLife

Este painel é destinado ao controle interno da SoproLife.

## Acesso e inicialização atuais

Usar somente `scripts/command-center-local-server.py`, diretamente ou pelo
`start-local.sh` (loopback), e as units oficiais já provisionadas. O launcher
local recusa ausência do proxy; `start-tailscale.sh` é mantido como recusa do
antigo servidor estático. Nunca servir o painel com `python3 -m http.server`.
O isolamento Tailscale não substitui sessão, permissões, CSRF ou HTTPS clínico.

O gate estático bloqueia fontes privadas/código e protege snapshots por
sessão/papel. `.gitignore` impede versionamento acidental, não exposição HTTP.
A API aplica RBAC; ocultar um botão não é autorização. Papel administrativo
não concede autoria médica. As decisões futuras de escopo por marca e
capacidade clínica estão na [arquitetura Life](../docs/CENTRO_COMANDO_LIFE_ARCHITECTURE.md);
esta fase não altera permissões reais nem a semântica de autenticação.

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

Esses indicadores de CRM/atendimento são totais numéricos gerados pelo
exportador M15 a partir do PostgreSQL. O cálculo via Apps Script e abas
privadas pertence ao fluxo histórico desativado. Nunca contêm nome, telefone,
CPF ou dado clínico individual.
O script `check-access.sh` valida que os valores são números e que nenhum dado sensível
chegou ao snapshot protegido.

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
