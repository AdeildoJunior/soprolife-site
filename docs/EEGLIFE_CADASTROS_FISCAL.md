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

## NFS-e — uma pessoa jurídica, um motor

Prestador legal: **SOPRO LIFE DIAGNÓSTICO E SOLUÇÕES EM SAÚDE LTDA**,
**63.544.026/0001-10**. EEGLife é a marca de origem do fato e do documento.
O emitente e os tributos são referenciados da mesma versão corporativa; não há
segundo cadastro jurídico, provider, SEFIN, credencial, certificado ou série.

`FiscalDocument.kind='eeg'` aponta para `eeg_exam_id` com unicidade por ambiente.
Não cria `SpirometryExam` artificial nem itens respiratórios. CHECK de formato
exclui híbridos; gatilhos e guarda ORM protegem a identidade fiscal. A
preparação e tentativas usam as mesmas tabelas e auditoria corporativas.

O botão Conferir NFS-e prepara sem rede. Em produção, emissão exige o resumo
imutável e segundo clique com valor/frase/preparação; usa as rotas existentes
`/fiscal/producao/documentos/{id}/confirmar-emissao`, spool e worker. O worker
revalida a elegibilidade EEG, município, dinheiro, política e tomador; continua
com no máximo um POST, sem repetição cega. Reconciliação segue por GET. Pedidos
e estados são acompanhados na mesma infraestrutura. Não há cancelamento real
adicionado. Mock é simulação sem validade fiscal.

**Sem política EEG validada, transmissão bloqueada.** Nenhuma política real é
fornecida nesta entrega. Aprovação contábil exige nova `FiscalPolicy` imutável,
`service='eeg'`, `flow='EEG'`, ambiente/vigência e configuração completa:

- `national_service_code`, `municipal_service_code` e decisões tributárias
  efetivamente aprovados para EEG, sem defaults da espirometria;
- `eeg_national_configuration_id`: ID da versão corporativa validada, de mesmo
  ambiente e com vigência compatível, cujo perfil tributário foi revisado para EEG;
- `eeg_modalidades`: códigos explicitamente abrangidos pela aprovação;
- `eeg_nbs_code`: NBS aprovado ou string vazia quando sua ausência foi aprovada;
- `validation_reference`: referência institucional da aprovação, sem PII.

O CNPJ referenciado deve ser o corporativo. A resolução usa o perfil apontado
pela política e os códigos EEG aprovados; não muda a configuração ativa da
espirometria. Não criar versão global que substitua inadvertidamente a
classificação respiratória. Aprovar configuração é ato humano separado;
este código não fornece evidência de correção tributária de código algum.

Descrição fiscal vem da modalidade estruturada do EEG realizado. Nenhum
texto clínico, broncodilatador, repasse ou descrição de espirometria é copiado.
Tomador individual é a pessoa atendida; terceiros/grupos EEG não estão nesta
entrega. Valor vem da receita EEG recebida. Cadastro insuficiente, data futura,
exame não realizado, modalidade não aprovada ou município ausente bloqueiam.

## Arquivos, migrações e operação

PDF/XML são entregues autenticados pelo acervo fiscal privado, com integridade
por hash/tamanho, sem caminho no frontend. O proxy admite PDF/XML somente na
rota fiscal exata. A ausência de DANFSe é mostrada; não há download externo
automático nem geração artificial de PDF. Segredos nunca são enviados ao browser.

Migrations: `e9c4a1b72029` (vínculo/atendimento/razão) e `f1e4a2b72029`
(documento fiscal EEG), sobre `d7b2c4e86a91`. Sem classificação histórica ou
backfill financeiro. Downgrade só com os novos fatos ausentes; desativar a
funcionalidade é preferível a perder evidência. Migração real exige revisão,
backup privado validado e autorização específica. Nenhum comando de deploy
ou transmissão é parte desta homologação local.

Testes específicos: `test_eeg_cadastros_atendimentos.py`, `test_eeg_fiscal.py`
e `test_eeg_postgres.py`, somados às suítes corporativas. PostgreSQL deve ser
descartável, apontado por `M15_TEST_POSTGRES_URL`; nenhum fixture usa dados reais.
