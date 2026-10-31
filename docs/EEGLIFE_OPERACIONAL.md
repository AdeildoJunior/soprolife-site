# EEGLife — contrato operacional integrado

Implementação de 29/10/2026, incremental sobre os commits `5b042f3` e `2d5ca4d`.
Este documento descreve o código; o relatório externo da missão registra os
HEADs efetivamente integrados/deployados e seus gates. Não atesta ativação de
serviço clínico, classificação contábil, assinatura criptográfica ou API Nano.

## Infraestrutura e identidade

Uma pessoa jurídica: SOPRO LIFE DIAGNÓSTICO E SOLUÇÕES EM SAÚDE LTDA,
CNPJ 63.544.026/0001-10. Duas marcas, `soprolife` e `eeglife`; sem terceiro
contexto. People, sessão/CSRF, PostgreSQL, razão, armazenamento e auditoria são
corporativos. `EegEnrollment` vincula a mesma pessoa sem duplicação. A Central
oferece busca no corpo POST por nome/CPF/telefone/PES, vínculo idempotente,
cadastro novo com recusa de CPF duplicado, edição cadastral e histórico EEG.
Correção de identidade usa o serviço corporativo existente; não altera PDFs.
Consentimento operacional EEG é próprio do vínculo, sem transferir marketing
ou permissão respiratória. WhatsApp é link humano, somente quando consentido e
sem `nao_contatar`. Nada é enviado automaticamente.

## Papéis

| Papel | Capacidades EEG |
|---|---|
| admin | Administração e operação, atribuição, conferência e financeiro; sem autoria clínica implícita |
| eeg_operador | Central, CRM, leads, agenda, tarefas, atribuição, recebimento documental, publicação/entrega, parceiros/documentos |
| eeg_tecnico | Fila técnica, realização, upload, versões e conferência de traçado |
| medical_eeg | Apenas exames atribuídos ao profissional ativo da conta; redação e conclusão próprias |
| eeg_financeiro | Receita/pagamento EEG; interface fiscal EEG e custos da marca |

Papéis EEG não herdam `leitura`, `operacional` ou `medico` respiratório. Uma
conta exclusivamente EEG entra somente na marca EEG. Concessões acontecem no
Núcleo administrativo corporativo; a migration cria o catálogo, **não concede
papéis a usuários reais**. `EegProfessional` registra neurologista interno,
parceiro ou serviço de telelaudo com CRM/UF, RQE quando informado, serviço e
referência da conferência. Conta interna exige `medical_eeg` e verificador
diferente da própria conta. Não há laudador padrão. UI adapta ações; a API
valida capacidade e atribuição, inclusive nos downloads por ID.

## Atendimento, CRM e agenda

Ajuste iniciado em 29/10 e retomado em 30/10/2026, integrado localmente com
MIR/FET e ainda sem publicação/deploy. A Central abre **Novo Atendimento EEG**, com
três passos: paciente (busca corporativa ou novo cadastro visível), modalidade
em cards e dados operacionais/financeiros. Busca exige seleção humana; o
cadastro/vínculo avulso fica em ações auxiliares. Datas reutilizam o calendário
brasileiro corporativo, sem copiar campos clínicos respiratórios.

`POST /eeg/atendimentos` aceita exatamente uma fonte de identidade:
`enrollment_id` (compatível com clientes anteriores), `person_id` ou
`nova_pessoa`. Deduplicação, contatos, consentimento EEG, vínculo, exame e
receita pertencem à mesma transação/savepoint idempotente. Erro no atendimento
reverte inclusive a pessoa nova. Retry de payload anterior preserva seu
fingerprint; reutilizar chave com conteúdo diferente continua retornando 409.
Uma corrida que conclui entre a leitura da chave e a verificação de identidade
também reaproveita o resultado após rollback do savepoint, sem duplicar pessoa.
O status inicial admite agendado, confirmado ou realizado (data não futura),
sem criar laudo, assinatura, arquivo ou documento fiscal. Consentimento omitido
em pessoa existente preserva o registro EEG; nunca altera o consentimento
corporativo respiratório.

Defaults editáveis preservam a política vigente: Rio no catálogo IBGE, origem
Direto, pagamento pendente e forma não informada. SITE é escolha explícita.
A prontidão local orienta o preenchimento; não consulta/prepara/emite NFS-e e
mantém a indicação de classificação fiscal EEG pendente. Modalidades do
catálogo permanecem tecnicamente pendentes e especiais inativas.

Novo Atendimento EEG é a entrada principal, sem Lead obrigatório. Seleção da
pessoa é explícita. Modalidade, data/hora com fuso, local textual, município
IBGE e origem são independentes. Município vem de catálogo estruturado
corporativo (Rio de Janeiro e Niterói no limite atual do motor), com Rio
editável sugerido só no formulário. Não se infere município de local/endereço.

A transação cria EEG e uma única receita `FinancialEntry.eeg_exam_id` com
chave/fingerprint idempotentes. Reteste técnico declarado exige valor zero e
não cria receita; o motivo fica no caso EEG. Financeiro respiratório exclui
`eeg_exam_id`, inclusive busca/snapshots. Forma de pagamento e recebimento
são explícitos; atualização financeira é auditada e respeita congelamento
fiscal. Repasses não têm valor/regra inventados e aguardam configuração futura.

`EegRelation` guarda próximo contato **manual**; nenhuma regra de seis meses
é aplicada. Histórico e agendar/reagendar usam o EEG real. `EegActivity`
persiste oportunidades e tarefas. Conversão de lead liga pessoa e depois EEG,
idempotentemente. Identidade nova/existente continua passando pela Central.
Tarefas têm categorias operacionais fechadas e responsável validado por papel;
não criam atribuição ou conteúdo clínico automaticamente.

## Exame, traçado e armazenamento

A máquina de exame anterior permanece com seus códigos estáveis. `EegCase`
separa a produção do documento da agenda/realização. Rótulos operacionais:

1. Aguardando traçado.
2. Aguardando conferência técnica (arquivo importado).
3. Pronto para laudo.
4. Aguardando laudo (atribuído).
5. Laudo em elaboração.
6. Aguardando assinatura (concluído/PDF final).
7. Assinatura recebida (`recebido_assinado`, declaração operacional).
8. Pronto para entrega.
9. Entregue.

Nano EEG iCelera usa **exportação + importação manual**, sem API presumida.
O adapter é um contrato local de validação do envelope de arquivo, sem
interpretação clínica. Upload exige sessão/CSRF, papel técnico/admin e
confirmação do código do paciente exibido. EDF (envelope compatível), PDF
estruturalmente validado e ZIP são aceitos, até **64 MiB por arquivo**. ZIP
não é extraído: nomes absolutos/traversal, symlinks, conteúdo executável
conhecido, criptografia e limites de expansão são recusados. Vídeo e formatos
não aprovados não são aceitos. Não há alegação de antivírus ou validação de
qualidade clínica de traçado.

Bytes ficam no filesystem **privado Life**, `eeg/tracados/<UUID>/<UUID>`,
modo 0600 e diretórios internos 0700. Metadados/hash/tamanho/MIME ficam em
`EegTechnicalFile`. Nenhum caminho vai para o navegador. Download autenticado
revalida contenção, symlink, permissões, tamanho e SHA-256, com `no-store` e
`nosniff`. Proxy aceita binário somente nas rotas exatas, com limites finitos.
A listagem mostra todas as versões. Substituição preserva a anterior e exige
nova conferência/atribuição. Após conclusão do laudo, substituição técnica é
recusada; correção exige atendimento corretivo/reteste explícito.

## Laudo e resultado

Atribuição exige arquivo privado conferido e profissional EEG verificado.
Redação própria, sem templates/conclusões respiratórios, contém indicação
somente quando fornecida, técnica, descrição, conclusão e observações. Cada
salvamento cria `EegCaseRevision` append-only com autoria e snapshot profissional.
Admin não pode redigir/concluir por hierarquia. Conclusão médica gera PDF final
com os dados daquele EEG e médico; não gera interpretação automática.

Serviço externo pode devolver PDF elaborado fora do painel, sob atribuição
registrada. O retorno assinado exige conferência humana de paciente/código,
médico e documento. `recebido_assinado` **não afirma validação criptográfica
ICP-Brasil**. A liberação exige PDF assinado recebido e íntegro. A ação antiga
de disponibilização passa pela mesma guarda; não há bypass pela URL anterior.
Substituição de PDF assinado cria nova versão e revoga links/sessões anteriores.

Portal EEG reaproveita nascimento, token derivado/HMAC, hash, expiração,
revogação e releitura protegida existentes. Só documento vigente do exame e
pessoa autorizados é entregue. Nenhum traçado bruto é publicado. Portais e
acervos EEG/espirometria permanecem separados; as consultas do papel PostgreSQL
do portal continuam usando as projeções restritas existentes.

## Gestão, fontes e indicadores

Parceiro e unidade são corporativos; `EegPartnerScope` registra a operação sem
copiar instituição existente. Profissional laudador permanece entidade distinta.
Documentos institucionais EEG usam armazenamento privado/versionado, hash e
categorias fechadas (contratos, equipamento, ANVISA, calibração, treinamento,
neurologistas, parceiros, protocolos, fiscal, institucional).

Preparos não herdam automaticamente textos do site. `EegPreparation` exige
aprovação explícita de médico EEG e referência documental por modalidade.
Ausência de aprovação é mostrada; nenhuma privação de sono genérica é inserida.
Templates administráveis de laudo permanecem extensão futura, sem IA clínica.

Custos continuam no livro-caixa privado existente, gerados pelo script
corporativo por marca. Nano R$ 24.010,00 + frete R$ 280,00 = R$ 24.290,00;
responsabilidade planejada Adeildo R$ 12.000,00 / Faustino R$ 12.290,00.
Pagamento efetivo/parcelas vêm exclusivamente dos movimentos, sem inferências.
Nenhuma cópia de custo ou receita entra no resumo SoproLife.

Marketing identifica `eeglife.com.br` e mostra **Aguardando integração** para
GA4/Search Console; não usa métricas SoproLife. Fontes distinguem banco
verificado, storage acessível, portal configurado e conectores pendentes.
Nano é importação manual. Dashboard usa fatos PostgreSQL, contagens atuais e
receita por competência no mês até hoje em Brasília; não inventa gráficos.
Feed de auditoria mostra eventos/IDs/horários, sem texto clínico ou contatos.

## Fiscal, segurança e operação

O motor corporativo dos dois commits originais é preservado. **Emissão real
EEG permanece fail-closed** sem política `service=eeg` / `flow=EEG` aprovada,
vigência, ambiente, modalidades, códigos, NBS e referência ao perfil tributário
corporativo revisado. Nenhuma política/credencial/série nova é instalada.
Configuração respiratória não é copiada nem ativada para EEG. A tela informa
“Configuração fiscal EEG pendente de aprovação”.

Migration aditiva `a291029eeg01` sobre `f1e4a2b72029`: oito tabelas novas,
nenhum backfill, nenhum usuário real alterado. Gatilhos PostgreSQL/SQLite e
guarda ORM tornam revisões clínicas, preparos aprovados, arquivos técnicos e
documentos institucionais append-only. Downgrade recusa apagar fatos novos.
A engine oculta parâmetros SQL em exceções, preservando PII/texto clínico.
O carregamento do logging Alembic preserva loggers existentes, evitando que
uma migration executada no processo de teste silencie o diagnóstico do portal.

Deploy usa `scripts/deploy-eeglife-operacional-vps.sh`: HEAD/base/schema/owner e
worker conferidos; backup PostgreSQL pelo script versionado, `pg_restore
--list` e leitura integral, bundle verificado e hashes; só então fast-forward
e migrations. Reinicia somente processos afetados pelo diff. O portal também é
recarregado quando muda um módulo compartilhado que ele usa (como `db.py`),
ou por recarga explícita revisada; o worker continua oneshot e não é iniciado.
Nenhum teste escreve paciente em produção.

Testes: `test_eeg_operational_complete.py`, `test_eeg_workflow_postgres.py`,
suítes EEG/corporativas e `test-eeg-operational-browser.mjs` (Chromium/Firefox,
390/768/1440, fluxo completo e RBAC), além do quality gate e check-access.
Evidências ficam fora do Git e fora de Downloads; ali vai somente o relatório.
