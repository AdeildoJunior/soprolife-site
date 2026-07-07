# SoproLife Audit Patterns

Use esta skill para qualquer funcionalidade que escreva em Google Sheets via painel.

## Princípios

- Auditoria append-only.
- Uma linha por ação de negócio, não por célula.
- Logar transição de estado, não conteúdo sensível.
- Allowlist default-fechada para valor_anterior/valor_novo.
- Campo fora da allowlist vira `[campo privado]`.
- Falha de auditoria nunca derruba operação principal.
- Summary público só com agregados e eventos saneados.

## Proibido no log/summary

- telefone;
- WhatsApp;
- CPF;
- nome de paciente;
- observação livre;
- laudo;
- pedido médico;
- dado clínico.

## Campos normalmente seguros

- etapa;
- status;
- prioridade;
- responsavel;
- datas;
- contagens;
- nome de clínica/empresa;
- entidade_id.

## Auditoria M1

Etapa 1 concluída:
- _logAudit;
- Log Auditoria;
- allowlist;
- request_id;
- duration_ms;
- build_version;
- [campo privado].

Próximo foco:
Auditoria M1 Etapa 2:
- _updateLeadStage;
- _updateCrmClinicaEtapa;
- _mirrorEtapaParaPcmso derivado.
