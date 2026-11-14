# Funil Comercial — operação

No Centro de Comando, abra **Leads e Agendamentos → Funil Comercial** e clique
**+ Novo contato**. A ação também fica na Central de Cadastros. Procure a
pessoa existente antes de criar outra. Para pessoa nova, basta o nome no
cadastro mínimo; os dados opcionais continuam disponíveis no seletor usual.
O cadastro da pessoa e do contato ocorre na mesma transação.

Registre quatro escolhas: origem, canal, unidade e serviço. Se não souber,
use Não informado/Não definido. A hora do primeiro contato é automática.
Gestor pode informar horário conhecido ao criar o contato; nunca preencha o
passado por estimativa. Não copie mensagens de WhatsApp nem conteúdo clínico.

**Origem** responde como a pessoa conheceu a empresa. **Canal** responde por
onde entrou em contato. **Unidade** é onde deseja realizar. Exemplo: Google
Maps / WhatsApp / Barra. Site não vira Google; WhatsApp não é uma origem.
Pastore como origem declarada e unidade parceira são escolhas independentes.
O campo Empresa/PCMSO classifica aquisição de paciente; não ativa serviço
PCMSO nem cria prospecção B2B.

Em **Atualizar etapa**, use Registrar primeira resposta assim que a equipe
responder. O primeiro horário é preservado nos próximos cliques. Não há
leitura automática de WhatsApp. A interação de follow-up continua separada.

Para agendar espirometria, escolha Agendado e a data prevista. Isso cria o
próprio exame em Aguardando, na agenda existente. Não cadastre outra agenda.
Remarcado altera a data desse exame e guarda as datas anterior e nova.
Cancelado e Não compareceu são declarações humanas; o motivo é opcional.
Silêncio não encerra contato: use Aguardando retorno quando apropriado.
Perdido/encerrado exige ação explícita.

Quando o exame existente passa para Realizado, o contato avança junto. A ação
Registrar exame realizado na lista confirma o fato operacional. Os demais
campos clínicos e financeiros continuam nos fluxos existentes. Ao completar
um Novo atendimento da mesma pessoa/contato, a reserva pendente é reutilizada.
Reteste pode apontar para o mesmo contato e não gera outra entrada no funil.

Ao salvar exame/atendimento, nenhum modal de contato interrompe a operação.
Um único contato aberto elegível da mesma pessoa é vinculado automaticamente.
Sem contato ou com vários candidatos, salva sem vínculo e informa apenas
“Sem contato prospectivo vinculado.” no resultado. A complementação pode ser
feita depois; nunca associe por texto de nome, telefone ou documento. Exames
antigos não recebem contatos inventados nem horário de primeiro contato
copiado do exame.

Para complementar um vínculo depois, no Funil Comercial use **Vincular exame
existente** no contato real e informe o código ESP. O sistema confere pessoa
e datas, preserva o primeiro contato e não cria resposta/agendamento anterior.
Se a origem do próprio contato ainda for desconhecida, continua **Não informado**;
use **Corrigir origem/canal** quando obtiver a informação.

Na Pastore, parceiro/unidade ativos vêm do cadastro institucional existente.
Com unidade única, Pastore Ipanema é preenchida sem seleção. Modalidade,
local e origem operacional são derivados no servidor; produção e fechamento
mensal continuam identificando a parceria mesmo sem contato prospectivo.

| Campo no novo atendimento | SoproLife | Pastore |
| --- | --- | --- |
| Município | Rio/RJ do catálogo, editável | Mesma sugestão editável |
| Broncodilatador | Default do catálogo, editável | Mesmo default; preserva o tipo ao trocar de fluxo |
| Status | Realizado | Realizado |
| Técnico/responsável | Default operacional atual do formulário, editável | Mesma regra compartilhada |
| Equipamento/fonte | Não informado | Não informado |
| Data/observação | Sem valor presumido | Sem valor presumido |
| Próximo acompanhamento | Seis meses calendários, depois primeira segunda; respeita edição manual | Mesma regra |
| Parceiro/unidade/modalidade/origem/local | Contexto SoproLife editável | Derivados da parceria/unidade Pastore |
| Pagamento/valor | Defaults existentes, editáveis | Sem recebimento individual; fechamento mensal |

A troca inicial entre SoproLife/Pastore leva os dados comuns do mesmo exame.
Voltar restaura o rascunho daquela variante. As confirmações de duplicidade,
emissão fiscal e alterações clínicas permanecem. A NFS-e Pastore continua
pela fila Fiscal com confirmação humana e regra própria da parcela SoproLife.

Os filtros de período selecionam **a coorte de primeiro contato**, e os
indicadores acompanham suas etapas acumuladas até agora. Exames de outro
contato não entram no numerador. Agendamento→exame usa apenas os contatos
agendados dessa mesma coorte. Remarcação não cria outro contato nem aumenta
agendados. Cards de cancelado/falta/encerrado descrevem o desfecho atual;
remarcações contam eventos. A cronologia mostra transições anteriores.

As medianas representam o tempo até o registro operacional da etapa, em
minutos. Registrar depois do fato aumenta o tempo observado. A realização usa
o horário da confirmação no sistema, não presume a hora clínica do exame.
A disponibilidade informa quantos contatos têm ambos os horários.

A semana atual e semanas cortadas pelo filtro aparecem **PARCIAL**. Coortes
recentes tiveram menos tempo para converter. Não compare essas semanas como
se estivessem completas ou igualmente maduras.

Cobertura menor que 100% indica dado ausente: origem não informada, exame sem
contato ou reserva atual sem vínculo. Cobertura de produção usa data do exame,
separadamente da coorte de contatos. Filtros de aquisição/unidade restringem
essa cobertura a exames com contato; sem esses filtros ela inclui a produção
histórica sem rastreamento. Zero denominador aparece como travessão.

Antes da implantação, não há cobertura completa do pré-atendimento. Exames
antigos continuam em produção; nenhuma origem, resposta ou reserva foi
inventada para fazê-los aparecer no funil.
