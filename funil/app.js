const WHATSAPP_NUMBER = "5521999417192";

// Opcional: para salvar em Google Sheets via Apps Script.
// Deixe vazio para funcionar só com WhatsApp.
// Exemplo: const LEAD_ENDPOINT = "https://script.google.com/macros/s/SEU_ID/exec";
const LEAD_ENDPOINT = "";

const state = {
  objetivo: "",
  pedido: "",
  local: "",
  perfil: "",
  nome: "",
  whatsapp: "",
  bairro: "",
  melhorHorario: "",
  observacao: ""
};

const steps = [
  {
    id: "inicio",
    progress: 8,
    render: () => `
      <p class="eyebrow">Agendamento orientado</p>
      <h1>Vamos encontrar o melhor caminho para seu atendimento na Sopro Life?</h1>
      <p class="lead">Responda algumas perguntas rápidas. No final, você será direcionado ao WhatsApp com a mensagem pronta.</p>
      <div class="options">
        <button class="option" data-next="objetivo" data-set="perfil:paciente">Sou paciente e quero atendimento</button>
        <button class="option" data-next="empresa" data-set="perfil:empresa">Sou clínica, empresa ou academia</button>
        <button class="option" data-next="medico" data-set="perfil:profissional">Sou médico/profissional de saúde</button>
      </div>
    `
  },
  {
    id: "objetivo",
    progress: 22,
    render: () => `
      <p class="eyebrow">Tipo de atendimento</p>
      <h1>O que você precisa?</h1>
      <p class="lead">Escolha a opção mais próxima. Não precisa informar diagnóstico aqui.</p>
      <div class="options">
        <button class="option" data-next="pedido" data-set="objetivo:Espirometria">Agendar espirometria</button>
        <button class="option" data-next="teleconsulta" data-set="objetivo:Teleconsulta">Preciso de consulta/pedido médico</button>
        <button class="option" data-next="local" data-set="objetivo:Atendimento domiciliar">Atendimento domiciliar</button>
        <button class="option" data-next="contato" data-set="objetivo:Tirar dúvidas">Quero tirar dúvidas</button>
      </div>
    `
  },
  {
    id: "pedido",
    progress: 38,
    render: () => `
      <p class="eyebrow">Pedido médico</p>
      <h1>Você já tem pedido médico para o exame?</h1>
      <p class="lead">Para espirometria, com ou sem broncodilatador, é necessário pedido médico.</p>
      <div class="options">
        <button class="option" data-next="local" data-set="pedido:Sim, já tenho pedido médico">Sim, já tenho pedido médico</button>
        <button class="option" data-next="teleconsulta" data-set="pedido:Não tenho pedido médico">Não tenho pedido médico</button>
        <button class="option" data-next="contato" data-set="pedido:Tenho dúvida sobre o pedido">Tenho dúvida sobre o pedido</button>
      </div>
    `
  },
  {
    id: "teleconsulta",
    progress: 52,
    render: () => `
      <p class="eyebrow">Teleconsulta</p>
      <h1>Podemos direcionar você para avaliação médica antes do exame.</h1>
      <p class="lead">A equipe orienta pelo WhatsApp sobre disponibilidade, valores e próximos passos.</p>
      <div class="options">
        <button class="option" data-next="contato" data-set="objetivo:Teleconsulta para avaliação/pedido médico">Quero seguir para teleconsulta</button>
        <button class="option" data-next="local" data-set="objetivo:Espirometria; pedido médico pendente">Prefiro falar com a equipe primeiro</button>
      </div>
    `
  },
  {
    id: "local",
    progress: 66,
    render: () => `
      <p class="eyebrow">Local de atendimento</p>
      <h1>Onde você prefere ser atendido?</h1>
      <p class="lead">Isso ajuda a equipe a orientar disponibilidade e deslocamento.</p>
      <div class="options">
        <button class="option" data-next="contato" data-set="local:Barra da Tijuca">Barra da Tijuca</button>
        <button class="option" data-next="contato" data-set="local:Atendimento domiciliar">Atendimento domiciliar</button>
        <button class="option" data-next="contato" data-set="local:Ainda não sei">Ainda não sei</button>
      </div>
    `
  },
  {
    id: "empresa",
    progress: 45,
    render: () => `
      <p class="eyebrow">Parcerias</p>
      <h1>Você quer atendimento para empresa, clínica ou ação externa?</h1>
      <p class="lead">A Sopro Life pode organizar atendimento, exames e soluções em saúde conforme a demanda.</p>
      <div class="options">
        <button class="option" data-next="contato" data-set="objetivo:Parceria com clínica">Clínica ou consultório</button>
        <button class="option" data-next="contato" data-set="objetivo:Atendimento para empresa">Empresa / medicina ocupacional</button>
        <button class="option" data-next="contato" data-set="objetivo:Ação em academia ou evento">Academia / evento / ação externa</button>
      </div>
    `
  },
  {
    id: "medico",
    progress: 45,
    render: () => `
      <p class="eyebrow">Profissionais de saúde</p>
      <h1>Como podemos ajudar seus pacientes?</h1>
      <p class="lead">Você pode encaminhar pacientes para exames, laudos, teleconsulta ou atendimento parceiro.</p>
      <div class="options">
        <button class="option" data-next="contato" data-set="objetivo:Encaminhar paciente para espirometria">Encaminhar paciente para espirometria</button>
        <button class="option" data-next="contato" data-set="objetivo:Solicitar parceria profissional">Quero conversar sobre parceria</button>
      </div>
    `
  },
  {
    id: "contato",
    progress: 84,
    render: () => `
      <p class="eyebrow">Quase pronto</p>
      <h1>Agora deixe seus dados para a equipe concluir pelo WhatsApp.</h1>
      <p class="lead">Coletamos apenas o necessário para contato e organização do atendimento.</p>

      <form class="form" id="leadForm">
        <label>Nome
          <input name="nome" autocomplete="name" required placeholder="Seu nome" />
        </label>

        <label>WhatsApp
          <input name="whatsapp" inputmode="tel" autocomplete="tel" required placeholder="(21) 99999-9999" />
        </label>

        <label>Bairro / região
          <input name="bairro" autocomplete="address-level2" placeholder="Ex.: Barra, Recreio, Jacarepaguá..." />
        </label>

        <label>Melhor horário para contato
          <select name="melhorHorario">
            <option value="">Selecione</option>
            <option>Manhã</option>
            <option>Tarde</option>
            <option>Noite</option>
            <option>Qualquer horário</option>
          </select>
        </label>

        <label>Observação opcional
          <textarea name="observacao" placeholder="Ex.: quero saber valores, disponibilidade ou atendimento domiciliar. Evite informar dados clínicos sensíveis."></textarea>
        </label>

        <div id="formError" class="error" hidden>Preencha nome e WhatsApp para continuar.</div>

        <div class="actions">
          <button class="btn btn-primary" type="submit">Enviar pelo WhatsApp</button>
          <button class="btn btn-secondary" type="button" data-back>Voltar</button>
        </div>
      </form>
    `
  },
  {
    id: "fim",
    progress: 100,
    render: () => `
      <p class="eyebrow">Mensagem pronta</p>
      <h1>Perfeito. Sua mensagem para a Sopro Life está pronta.</h1>
      <p class="lead">Clique no botão abaixo para abrir o WhatsApp e finalizar o atendimento.</p>
      <div class="summary">
        <strong>Resumo:</strong><br>
        Interesse: ${escapeHTML(state.objetivo || "Não informado")}<br>
        Pedido médico: ${escapeHTML(state.pedido || "Não informado")}<br>
        Local: ${escapeHTML(state.local || "Não informado")}<br>
        Bairro/região: ${escapeHTML(state.bairro || "Não informado")}
      </div>
      <div class="actions">
        <a class="btn btn-primary" id="whatsLink" href="${buildWhatsAppLink()}" target="_blank" rel="noopener">Abrir WhatsApp</a>
        <button class="btn btn-secondary" type="button" data-restart>Começar de novo</button>
      </div>
    `
  }
];

let current = "inicio";
const historyStack = [];

function stepById(id){
  return steps.find(s => s.id === id);
}

function render(id){
  current = id;
  const step = stepById(id);
  document.getElementById("progressBar").style.width = `${step.progress}%`;
  document.getElementById("quiz").innerHTML = step.render();
  bindEvents();
}

function bindEvents(){
  document.querySelectorAll("[data-next]").forEach(btn => {
    btn.addEventListener("click", () => {
      const set = btn.getAttribute("data-set");
      if(set){
        const [key, ...rest] = set.split(":");
        state[key] = rest.join(":");
      }
      historyStack.push(current);
      render(btn.getAttribute("data-next"));
    });
  });

  const back = document.querySelector("[data-back]");
  if(back){
    back.addEventListener("click", () => {
      const prev = historyStack.pop() || "inicio";
      render(prev);
    });
  }

  const restart = document.querySelector("[data-restart]");
  if(restart){
    restart.addEventListener("click", () => {
      Object.keys(state).forEach(k => state[k] = "");
      historyStack.length = 0;
      render("inicio");
    });
  }

  const form = document.getElementById("leadForm");
  if(form){
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = new FormData(form);
      state.nome = data.get("nome").trim();
      state.whatsapp = data.get("whatsapp").trim();
      state.bairro = data.get("bairro").trim();
      state.melhorHorario = data.get("melhorHorario").trim();
      state.observacao = data.get("observacao").trim();

      const error = document.getElementById("formError");
      if(!state.nome || !state.whatsapp){
        error.hidden = false;
        return;
      }
      error.hidden = true;

      await saveLead();
      historyStack.push(current);
      render("fim");
    });
  }
}

async function saveLead(){
  if(!LEAD_ENDPOINT) return;
  try{
    await fetch(LEAD_ENDPOINT, {
      method: "POST",
      mode: "no-cors",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({
        origem: "funil-soprolife",
        criadoEm: new Date().toISOString(),
        ...state
      })
    });
  }catch(err){
    console.warn("Lead não salvo no endpoint:", err);
  }
}

function buildWhatsAppLink(){
  const msg = [
    "Olá, Sopro Life! Vim pelo funil de agendamento.",
    "",
    `Nome: ${state.nome || ""}`,
    `WhatsApp: ${state.whatsapp || ""}`,
    `Perfil: ${state.perfil || ""}`,
    `Interesse: ${state.objetivo || ""}`,
    `Pedido médico: ${state.pedido || ""}`,
    `Local preferido: ${state.local || ""}`,
    `Bairro/região: ${state.bairro || ""}`,
    `Melhor horário: ${state.melhorHorario || ""}`,
    state.observacao ? `Observação: ${state.observacao}` : "",
    "",
    "Gostaria de orientação para concluir o atendimento."
  ].filter(Boolean).join("\n");

  return `https://wa.me/${WHATSAPP_NUMBER}?text=${encodeURIComponent(msg)}`;
}

function escapeHTML(str){
  return String(str)
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}

render("inicio");
