# M24C — decisão de integração de assinatura qualificada

## Decisão atual

O alvo legal é uma assinatura eletrônica qualificada do médico, baseada em
ICP-Brasil, incorporada ao PDF e verificável independentemente. O provider de
produção permanece `unconfigured`.

Nenhum fornecedor, credencial, tenant, certificado ou modelo de custódia foi
selecionado ou autorizado. VIDaaS, BirdID, certificado A1 e outras opções são
somente candidatos futuros. M24C não instala SDK, não faz requisição externa,
não aceita imagem de assinatura e não gera QR code visual como substituto
criptográfico.

O estado máximo alcançável pelo runtime é `assinatura_pendente`. Falha,
timeout ou ausência de provider nunca produz `assinado` nem documento
liberável.

## Informações obrigatórias antes de escolher um provider

Uma decisão futura precisa obter, revisar e aprovar:

1. documentação oficial de integração e API;
2. criação de tenant, credencial e ambientes de homologação/produção;
3. cerimônia de consentimento, autenticação e presença do signatário;
4. mapeamento verificável entre identidade do certificado e perfil do médico
   atribuído;
5. compatibilidade PAdES e perfil de assinatura usado no PDF;
6. requisitos de autoridade de carimbo do tempo;
7. verificação de cadeia, expiração e revogação do certificado;
8. validação independente em verificador compatível com ITI;
9. autenticação, anti-replay, idempotência e sigilo de webhook/polling;
10. custódia do certificado e chave, inclusive A1 ou custódia em nuvem;
11. procedimentos de incidente, suspensão, renovação e revogação;
12. SLAs, recuperação, trilha técnica e política de falha fechada.

## Contrato esperado do adapter futuro

O adapter deve receber somente a versão imutável preparada e o ID técnico do
perfil atribuído. Antes de aceitar sucesso, deverá provar:

- bytes assinados exatamente correspondentes ao SHA-256 preparado;
- assinatura PAdES válida no próprio PDF;
- cadeia ICP-Brasil válida na data relevante;
- identidade do signatário correspondente ao médico atribuído;
- carimbo do tempo conforme decisão jurídica/técnica;
- estado de revogação verificado;
- resposta externa autenticada e vinculada à solicitação idempotente.

Os bytes assinados devem ser publicados como uma nova versão imutável. Nunca
se sobrescreve o original, rascunho ou versão preparada. Metadado visual,
status isolado do provider, callback não autenticado ou `200 OK` sem validação
criptográfica não constituem sucesso.

## Evidências e segredos

Auditoria pode guardar IDs técnicos, provider, referência externa opaca,
status fechado, timestamps e resultado técnico de validação. Não pode guardar
PIN, senha, token, chave privada, certificado secreto, challenge de
autenticação ou conteúdo clínico.

Credenciais futuras devem ficar em mecanismo de segredo aprovado, fora de
Git, banco, frontend e logs. A custódia e o acesso emergencial precisam de
responsável e procedimento formal antes da habilitação.

## Critério de saída do bloqueio

Somente uma decisão jurídica e técnica explícita, acompanhada de homologação
com documentos sintéticos, validação ITI compatível, testes de revogação,
incidente, concorrência e falha, pode substituir `unconfigured`. Essa futura
mudança exige outro marco; não faz parte de M24C.
