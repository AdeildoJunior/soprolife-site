/* =========================================================================
   Sopro Life — Definição dos funis (templates)
   -------------------------------------------------------------------------
   Cada funil é { accent, accentDark, tagline, estimatedSteps, steps:[...] }.
   Para criar um novo funil: duplique um bloco, troque o nome da chave e edite.
   Depois crie a pasta /funil/NOME/index.html com data-flow="NOME".

   Tipos de etapa (step.type):
     "options" (padrão) — botões de escolha. Cada opção:
        { icon, label, desc?, set?:{chave:valor}, next:"idDaProximaEtapa" }
     "form"   — coleta de dados. Tem fields:[...] e next.
     "final"  — tela de conclusão com resumo + botão WhatsApp.
   ========================================================================= */

/* Campos do formulário reaproveitados entre funis */
var CAMPOS_BASE = [
  { name: "nome", label: "Nome", type: "text", required: true, placeholder: "Seu nome completo", autocomplete: "name" },
  { name: "whatsapp", label: "WhatsApp", type: "tel", required: true, placeholder: "(21) 99999-9999", autocomplete: "tel" },
  { name: "bairro", label: "Bairro / região", type: "text", placeholder: "Ex.: Barra, Recreio, Jacarepaguá...", autocomplete: "address-level2" },
  {
    name: "melhorHorario", label: "Melhor horário para contato", type: "select",
    options: ["Manhã", "Tarde", "Noite", "Qualquer horário"]
  },
  {
    name: "observacao", label: "Observação (opcional)", type: "textarea",
    placeholder: "Ex.: quero saber valores e disponibilidade. Evite informar dados clínicos sensíveis."
  }
];

window.FUNNEL_FLOWS = {

  /* ====================== FUNIL GERAL (triagem) ====================== */
  geral: {
    accent: "#1eb5ab",
    accentDark: "#0f8f86",
    tagline: "Agendamento orientado",
    estimatedSteps: 5,
    steps: [
      {
        id: "inicio",
        eyebrow: "Agendamento orientado",
        title: "Vamos encontrar o melhor caminho para o seu atendimento?",
        lead: "Responda algumas perguntas rápidas. No final, abrimos o WhatsApp com a sua mensagem já pronta.",
        options: [
          { icon: "🫁", label: "Sou paciente e quero atendimento", desc: "Exames, consulta ou domiciliar", set: { perfil: "Paciente" }, next: "objetivo" },
          { icon: "🏢", label: "Sou clínica, empresa ou academia", desc: "Parcerias e ações externas", set: { perfil: "Empresa/Instituição" }, next: "empresa" },
          { icon: "🩺", label: "Sou médico ou profissional de saúde", desc: "Encaminhar pacientes / parceria", set: { perfil: "Profissional de saúde" }, next: "medico" }
        ]
      },
      {
        id: "objetivo",
        eyebrow: "Tipo de atendimento",
        title: "O que você precisa?",
        lead: "Escolha a opção mais próxima. Não precisa informar diagnóstico aqui.",
        options: [
          { icon: "💨", label: "Agendar espirometria", set: { objetivo: "Espirometria" }, next: "pedido" },
          { icon: "💻", label: "Preciso de consulta / pedido médico", set: { objetivo: "Teleconsulta" }, next: "teleconsulta" },
          { icon: "🏠", label: "Atendimento domiciliar", set: { objetivo: "Atendimento domiciliar" }, next: "local" },
          { icon: "💬", label: "Quero tirar dúvidas", set: { objetivo: "Tirar dúvidas" }, next: "contato" }
        ]
      },
      {
        id: "pedido",
        eyebrow: "Pedido médico",
        title: "Você já tem pedido médico para o exame?",
        lead: "A espirometria, com ou sem broncodilatador, precisa de pedido médico.",
        options: [
          { icon: "✅", label: "Sim, já tenho pedido médico", set: { pedido: "Sim, já tem pedido" }, next: "local" },
          { icon: "📝", label: "Não tenho pedido médico", desc: "Podemos direcionar para teleconsulta", set: { pedido: "Não tem pedido" }, next: "teleconsulta" },
          { icon: "❓", label: "Tenho dúvida sobre o pedido", set: { pedido: "Dúvida sobre o pedido" }, next: "contato" }
        ]
      },
      {
        id: "teleconsulta",
        eyebrow: "Teleconsulta",
        title: "Podemos te direcionar para avaliação médica antes do exame.",
        lead: "A equipe orienta pelo WhatsApp sobre disponibilidade, valores e próximos passos.",
        options: [
          { icon: "💻", label: "Quero seguir para teleconsulta", set: { objetivo: "Teleconsulta para avaliação/pedido médico" }, next: "contato" },
          { icon: "🗣️", label: "Prefiro falar com a equipe primeiro", set: { objetivo: "Espirometria (pedido pendente)" }, next: "local" }
        ]
      },
      {
        id: "local",
        eyebrow: "Local de atendimento",
        title: "Onde você prefere ser atendido?",
        lead: "Isso ajuda a equipe a orientar disponibilidade e deslocamento.",
        options: [
          { icon: "📍", label: "Barra da Tijuca", set: { local: "Barra da Tijuca" }, next: "contato" },
          { icon: "🏠", label: "Atendimento domiciliar", set: { local: "Atendimento domiciliar" }, next: "contato" },
          { icon: "🤔", label: "Ainda não sei", set: { local: "A definir" }, next: "contato" }
        ]
      },
      {
        id: "empresa",
        eyebrow: "Parcerias",
        title: "Atendimento para empresa, clínica ou ação externa?",
        lead: "A Sopro Life organiza atendimento, exames e soluções em saúde conforme a demanda.",
        options: [
          { icon: "🏥", label: "Clínica ou consultório", set: { objetivo: "Parceria com clínica" }, next: "contato" },
          { icon: "🏭", label: "Empresa / medicina ocupacional", set: { objetivo: "Atendimento para empresa" }, next: "contato" },
          { icon: "🎽", label: "Academia / evento / ação externa", set: { objetivo: "Ação em academia ou evento" }, next: "contato" }
        ]
      },
      {
        id: "medico",
        eyebrow: "Profissionais de saúde",
        title: "Como podemos ajudar seus pacientes?",
        lead: "Encaminhe pacientes para exames, laudos, teleconsulta ou atendimento parceiro.",
        options: [
          { icon: "💨", label: "Encaminhar paciente para espirometria", set: { objetivo: "Encaminhar paciente para espirometria" }, next: "contato" },
          { icon: "🤝", label: "Quero conversar sobre parceria", set: { objetivo: "Parceria profissional" }, next: "contato" }
        ]
      },
      {
        id: "contato",
        type: "form",
        eyebrow: "Quase pronto",
        title: "Deixe seus dados para a equipe concluir pelo WhatsApp.",
        lead: "Coletamos apenas o necessário para contato e organização do atendimento.",
        fields: CAMPOS_BASE,
        submitLabel: "Enviar pelo WhatsApp",
        next: "fim"
      },
      {
        id: "fim",
        type: "final",
        eyebrow: "Tudo certo!",
        title: "Sua mensagem para a Sopro Life está pronta.",
        lead: "Clique abaixo para abrir o WhatsApp e finalizar o atendimento com a nossa equipe.",
        summaryKeys: ["objetivo", "pedido", "local", "bairro", "melhorHorario"]
      }
    ]
  },

  /* ====================== FUNIL ESPIROMETRIA ====================== */
  espirometria: {
    accent: "#1eb5ab",
    accentDark: "#0f8f86",
    tagline: "Espirometria",
    estimatedSteps: 4,
    steps: [
      {
        id: "inicio",
        eyebrow: "Espirometria",
        title: "Agende sua espirometria com a Sopro Life",
        lead: "Exame de função pulmonar com equipe especializada. Responda 4 perguntas rápidas e fale com a gente.",
        options: [
          { icon: "💨", label: "Espirometria simples", set: { objetivo: "Espirometria simples" }, next: "pedido" },
          { icon: "🌬️", label: "Espirometria com broncodilatador", set: { objetivo: "Espirometria com broncodilatador" }, next: "pedido" },
          { icon: "❓", label: "Não sei qual preciso", set: { objetivo: "Espirometria (a definir)" }, next: "pedido" }
        ]
      },
      {
        id: "pedido",
        eyebrow: "Pedido médico",
        title: "Você já tem o pedido médico?",
        lead: "O exame precisa de pedido médico. Sem pedido, podemos te direcionar para uma teleconsulta.",
        options: [
          { icon: "✅", label: "Sim, já tenho", set: { pedido: "Sim, já tem pedido" }, next: "local" },
          { icon: "💻", label: "Não tenho (quero teleconsulta)", set: { pedido: "Não tem — quer teleconsulta" }, next: "local" },
          { icon: "❓", label: "Tenho dúvidas", set: { pedido: "Dúvida sobre o pedido" }, next: "local" }
        ]
      },
      {
        id: "local",
        eyebrow: "Local",
        title: "Onde você prefere fazer o exame?",
        lead: "Atendemos em consultório e também na sua casa.",
        options: [
          { icon: "📍", label: "Na clínica (Barra da Tijuca)", set: { local: "Clínica — Barra da Tijuca" }, next: "contato" },
          { icon: "🏠", label: "Em casa (domiciliar)", set: { local: "Domiciliar" }, next: "contato" },
          { icon: "🤔", label: "Tanto faz / não sei", set: { local: "A definir" }, next: "contato" }
        ]
      },
      {
        id: "contato",
        type: "form",
        eyebrow: "Quase pronto",
        title: "Deixe seus dados para confirmarmos o agendamento.",
        lead: "Coletamos apenas o necessário para contato e organização do exame.",
        fields: CAMPOS_BASE,
        submitLabel: "Agendar pelo WhatsApp",
        next: "fim"
      },
      {
        id: "fim",
        type: "final",
        eyebrow: "Tudo certo!",
        title: "Sua solicitação de espirometria está pronta.",
        lead: "Clique abaixo para abrir o WhatsApp e confirmar o agendamento com a equipe.",
        summaryKeys: ["objetivo", "pedido", "local", "bairro", "melhorHorario"]
      }
    ]
  },

  /* ====================== FUNIL DOMICILIAR ====================== */
  domiciliar: {
    accent: "#2a8fd6",
    accentDark: "#1f6fa8",
    tagline: "Atendimento domiciliar",
    estimatedSteps: 4,
    steps: [
      {
        id: "inicio",
        eyebrow: "Atendimento domiciliar",
        title: "Atendimento de saúde no conforto da sua casa",
        lead: "Exames e cuidados sem você precisar se deslocar. Conte pra gente o que precisa.",
        options: [
          { icon: "💨", label: "Espirometria em casa", set: { objetivo: "Espirometria domiciliar" }, next: "paciente" },
          { icon: "🩺", label: "Avaliação / acompanhamento", set: { objetivo: "Avaliação domiciliar" }, next: "paciente" },
          { icon: "💬", label: "Quero entender o que oferecem", set: { objetivo: "Dúvidas sobre domiciliar" }, next: "paciente" }
        ]
      },
      {
        id: "paciente",
        eyebrow: "Para quem é",
        title: "O atendimento é para você ou para outra pessoa?",
        lead: "Isso ajuda a equipe a se preparar melhor.",
        options: [
          { icon: "🙂", label: "Para mim", set: { perfil: "Paciente (próprio)" }, next: "regiao" },
          { icon: "👵", label: "Para um familiar / idoso", set: { perfil: "Familiar / idoso" }, next: "regiao" },
          { icon: "🧑‍⚕️", label: "Sou cuidador(a) ou responsável", set: { perfil: "Cuidador(a)/responsável" }, next: "regiao" }
        ]
      },
      {
        id: "regiao",
        eyebrow: "Região",
        title: "Em qual região é o atendimento?",
        lead: "Atendemos a Zona Oeste do Rio e arredores. Confirmamos a cobertura no contato.",
        options: [
          { icon: "📍", label: "Barra / Recreio / Jacarepaguá", set: { local: "Barra/Recreio/Jacarepaguá" }, next: "contato" },
          { icon: "🗺️", label: "Outra região do Rio", set: { local: "Outra região do RJ" }, next: "contato" },
          { icon: "🤔", label: "Não tenho certeza", set: { local: "A confirmar" }, next: "contato" }
        ]
      },
      {
        id: "contato",
        type: "form",
        eyebrow: "Quase pronto",
        title: "Deixe seus dados para organizarmos a visita.",
        lead: "Coletamos apenas o necessário para contato e logística do atendimento.",
        fields: CAMPOS_BASE,
        submitLabel: "Solicitar pelo WhatsApp",
        next: "fim"
      },
      {
        id: "fim",
        type: "final",
        eyebrow: "Tudo certo!",
        title: "Sua solicitação de atendimento domiciliar está pronta.",
        lead: "Clique abaixo para abrir o WhatsApp e combinar os detalhes com a equipe.",
        summaryKeys: ["objetivo", "perfil", "local", "bairro", "melhorHorario"]
      }
    ]
  },

  /* ====================== FUNIL PARCERIAS ====================== */
  parcerias: {
    accent: "#0b2239",
    accentDark: "#1eb5ab",
    tagline: "Parcerias",
    estimatedSteps: 4,
    steps: [
      {
        id: "inicio",
        eyebrow: "Parcerias Sopro Life",
        title: "Soluções em saúde para a sua instituição",
        lead: "Exames, laudos e atendimento sob demanda para clínicas, empresas e ações externas.",
        options: [
          { icon: "🏥", label: "Clínica ou consultório", set: { perfil: "Clínica/consultório" }, next: "interesse" },
          { icon: "🏭", label: "Empresa / medicina ocupacional", set: { perfil: "Empresa" }, next: "interesse" },
          { icon: "🎽", label: "Academia / evento / ação externa", set: { perfil: "Academia/evento" }, next: "interesse" }
        ]
      },
      {
        id: "interesse",
        eyebrow: "Interesse",
        title: "O que você procura?",
        lead: "Escolha a opção mais próxima — ajustamos os detalhes na conversa.",
        options: [
          { icon: "💨", label: "Espirometria em volume / recorrente", set: { objetivo: "Espirometria em volume" }, next: "volume" },
          { icon: "🩺", label: "Atendimento / exames para colaboradores", set: { objetivo: "Atendimento para colaboradores" }, next: "volume" },
          { icon: "📋", label: "Laudos e parecer especializado", set: { objetivo: "Laudos especializados" }, next: "volume" },
          { icon: "🤝", label: "Quero apresentar uma proposta", set: { objetivo: "Proposta de parceria" }, next: "volume" }
        ]
      },
      {
        id: "volume",
        eyebrow: "Escala",
        title: "Qual o volume aproximado?",
        lead: "Estimativa serve só para dimensionar a proposta.",
        options: [
          { icon: "🔵", label: "Até 20 atendimentos / mês", set: { local: "Até 20/mês" }, next: "contato" },
          { icon: "🟢", label: "20 a 100 / mês", set: { local: "20–100/mês" }, next: "contato" },
          { icon: "🟣", label: "Mais de 100 / mês", set: { local: "100+/mês" }, next: "contato" },
          { icon: "❓", label: "Ainda não sei", set: { local: "A definir" }, next: "contato" }
        ]
      },
      {
        id: "contato",
        type: "form",
        eyebrow: "Quase pronto",
        title: "Deixe seus dados para nossa equipe comercial.",
        lead: "Retornamos com as condições e próximos passos da parceria.",
        fields: [
          { name: "nome", label: "Seu nome", type: "text", required: true, placeholder: "Nome do responsável", autocomplete: "name" },
          { name: "empresa", label: "Empresa / instituição", type: "text", placeholder: "Nome da clínica, empresa ou academia" },
          { name: "whatsapp", label: "WhatsApp", type: "tel", required: true, placeholder: "(21) 99999-9999", autocomplete: "tel" },
          { name: "bairro", label: "Cidade / região", type: "text", placeholder: "Ex.: Rio de Janeiro - Barra", autocomplete: "address-level2" },
          {
            name: "melhorHorario", label: "Melhor horário para contato", type: "select",
            options: ["Manhã", "Tarde", "Noite", "Qualquer horário"]
          },
          { name: "observacao", label: "Mensagem (opcional)", type: "textarea", placeholder: "Conte um pouco sobre a sua demanda." }
        ],
        submitLabel: "Falar com o comercial",
        next: "fim"
      },
      {
        id: "fim",
        type: "final",
        eyebrow: "Recebido!",
        title: "Sua solicitação de parceria está pronta.",
        lead: "Clique abaixo para abrir o WhatsApp e falar com a nossa equipe comercial.",
        summaryKeys: ["perfil", "objetivo", "local", "empresa", "melhorHorario"]
      }
    ]
  }

};
