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
- documentação operacional.

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
