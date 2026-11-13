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

Ao criar exame sem contato, o atendimento continua disponível. A tela oferece
Registrar contato ou Continuar sem rastreamento. Se houver vários contatos
abertos da mesma pessoa, escolha explicitamente. Nunca associe por texto de
nome, telefone ou documento. Exames antigos não recebem contatos inventados.

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
