# EEGLife — contrato operacional de cadastros, atendimentos e fiscal

Implementação local de 29/10/2026. Este documento não confirma deploy, ativação
fiscal ou capacidade técnica do equipamento. PostgreSQL/M15 é a fonte canônica.
Somente os contextos SoproLife e EEGLife existem; não há terceira marca.

## Identidade e autorização

`Person` é corporativo. A Central EEG lista vínculos `EegEnrollment`; busca
`POST /eeg/pessoas/busca` recebe nome, CPF, telefone ou PES no corpo. A opção
`corporativo=true` exige termo com ao menos três caracteres e permite procurar
pessoa existente para vínculo explícito. Nome/telefone nunca fazem fusão.
CPF continua único e validado no serviço corporativo. Candidato semelhante
exige decisão humana; confirmação não contorna CPF duplicado. Cadastro e
vínculo não geram exame, laudo ou nota automaticamente.

Todas as rotas EEG exigem admin no servidor, inclusive pesquisa corporativa.
O vínculo não altera nem apaga os registros SoproLife. Sexo, nascimento,
telefone e e-mail opcional usam os contratos atuais. Consentimento operacional
de WhatsApp EEG fica no vínculo (`desconhecido`, `concedido`, `revogado`) e na
trilha append-only de auditoria. Não muda os consentimentos da SoproLife nem
concede marketing entre marcas. Vincular pessoa existente não transfere seu
consentimento para EEG. Não há envio automático de mensagens.

## Atendimento e razão

`POST /eeg/atendimentos` cria, na mesma transação, um `EegExam` com código
operacional e exatamente uma receita em `FinancialEntry`, ligada por
`eeg_exam_id`. Chave idempotente e fingerprint recusam reutilização com dados
diferentes. Valor positivo, BRL, pagamento explícito e origem estruturada.
A competência inicial usa o dia agendado em America/Sao_Paulo; a competência
fiscal usa a realização efetiva, nunca a data sugerida ou de agendamento.
O pagamento pode ser atualizado na área EEG, com data real de recebimento;
receita com pedido fiscal ativo/tentativa de produção fica congelada.

A marca da receita é derivada do vínculo técnico. Listagem, busca, edição e
snapshots financeiros SoproLife excluem `eeg_exam_id IS NOT NULL`. O razão é
único; não há receita espelhada, backfill ou consolidação por soma de cards.
O banco garante unicidade da receita EEG e impede vínculos híbridos com
espirometria, consulta, encaminhamento ou fechamento.

Local textual e município IBGE são dados distintos e explícitos. Não há
inferência por endereço do paciente ou nome do parceiro. O motor suporta Rio
e Niterói na configuração atual; outros municípios bloqueiam o fiscal.

Valor/pagamento não entram nas tabelas ou respostas clínicas EEG. Notas de
operação não devem receber dados clínicos. Traçado permanece externo; somente
sua referência técnica é registrada no fluxo já existente. Novo Atendimento
não cria laudo nem NFS-e. Fluxo médico respiratório não foi alterado.

## Modalidades

Códigos anteriores preservados: vigília/rotina, fotoestimulação, prolongado
30–60 minutos, prolongado 2 horas, digital por hora, mapeamento e mapeamento
com fotoestimulação. Acrescentados infantil, com sono, privação de sono e
ambulatorial, todos explicitamente pendentes de validação Nano/iCelera.
Hospitalar/beira-leito, vídeo-EEG e especial estão inativos. A indicação de
cadastro ativo permite classificação operacional; não certifica capacidade.
Especial não tem descrição fiscal genérica liberada. Não há morte encefálica.

## Próxima etapa

A adaptação fiscal é separada. Esta etapa não libera NFS-e EEG.
