# SoproLife OS Local Core

Documento de arquitetura do núcleo local do Painel SoproLife.

Versão: 0.1 — rascunho de arquitetura (dados fictícios, sem backend real).

---

## O que é o SoproLife OS Local Core

É o conjunto de camadas, contratos e scripts que vão unir progressivamente:

- Painel visual (dashboard);
- CRM de clínicas;
- Agenda operacional;
- Leads e atendimentos;
- Marketing e SEO;
- Financeiro interno;
- Automações e notificações;
- Integrações com Google Workspace, WhatsApp Business e outros sistemas.

O objetivo é construir um **centro de comando local** da SoproLife que funcione sem depender de infraestrutura externa, mas que possa se conectar a fontes reais de forma segura e controlada.

---

## Sistemas que serão unidos

| Sistema | Fonte futura | Camada de entrada |
|---|---|---|
| Painel Geral | dados locais seguros | scripts locais |
| CRM Clínicas | Google Sheets privado | conector Sheets |
| Leads | Google Sheets / WhatsApp Business | conector Sheets / conector WA |
| Agenda | Google Calendar | conector Calendar |
| Marketing e SEO | Search Console / GA4 / planilha editorial | conector Search Console |
| Financeiro | planilha privada / sistema futuro | conector Sheets |
| Tarefas | planilha privada | conector Sheets |
| Documentos | arquivos institucionais | importação manual |
| Automações | Apps Script / scripts locais | camada automação |
| IA operacional | futura | camada IA |

---

## Arquitetura em camadas

```
┌─────────────────────────────────────────────────────────────┐
│  Camada 5 — IA / Automação (futura)                         │
│  Resumos automáticos, alertas, lead scoring, sugestões      │
├─────────────────────────────────────────────────────────────┤
│  Camada 4 — Conectores (futura)                             │
│  Google Sheets, Calendar, Gmail, Search Console, WA Biz     │
├─────────────────────────────────────────────────────────────┤
│  Camada 3 — Dados Seguros Gerados (local, git-ignorados)    │
│  *.local.json — produzidos pelos scripts, nunca commitados  │
├─────────────────────────────────────────────────────────────┤
│  Camada 2 — Scripts Locais (versionados, sem segredos)      │
│  scripts/*.sh — ponte entre config privada e painel         │
├─────────────────────────────────────────────────────────────┤
│  Camada 1 — Painel Estático (versionado, dados fictícios)   │
│  index.html + css/ + js/ + data/*.json públicos             │
└─────────────────────────────────────────────────────────────┘
```

### Camada 1 — Painel Estático

**Status:** implementada.

Arquivos:

```
painel-soprolife/
├── index.html
├── css/style.css
├── js/app.js
└── data/
    ├── resumo.json          (fictício, público)
    ├── crm-clinicas.json    (fictício, público)
    ├── leads.json           (fictício, público)
    ├── marketing.json       (fictício, público)
    ├── financeiro.json      (fictício, público)
    ├── tarefas.json         (fictício, público)
    ├── documentos.json      (fictício, público)
    └── automacoes.json      (fictício, público)
```

Regra: só dados fictícios ou anônimos. Nenhum dado real. Nenhum segredo.

### Camada 2 — Scripts Locais

**Status:** implementada.

Scripts que leem configuração privada externa (`~/.config/soprolife/painel/`) e produzem arquivos locais seguros para o painel, sem copiar segredos ou dados identificáveis.

```
painel-soprolife/scripts/
├── update-local-data.sh        (orquestrador principal)
├── generate-runtime-status.sh  (verifica config de fontes)
├── sync-dashboard-summary.sh   (sincroniza resumo JSON privado)
├── import-summary-csv.sh       (importa resumo via CSV)
├── check-access.sh             (verifica segurança de acesso)
├── start-local.sh              (sobe servidor local)
└── start-tailscale.sh          (sobe via Tailscale)
```

Regra: scripts são versionados. Não devem conter URL, ID, token ou senha embutidos.

### Camada 3 — Dados Seguros Gerados

**Status:** implementada, git-ignorada.

Arquivos produzidos pelos scripts a partir de fontes privadas externas. Ficam dentro do projeto mas são ignorados pelo Git.

```
painel-soprolife/data/
├── runtime-status.local.json       (git-ignorado)
└── resumo-dashboard.local.json     (git-ignorado)
```

Regra: só indicadores agregados. Sem CPF, telefone, nome de paciente, pedido médico, laudo ou dado clínico identificável.

### Camada 4 — Conectores (futura)

**Status:** não implementada. Documentação de contratos em `core/connectors/`.

Cada conector será um script ou serviço que:

1. lê de uma fonte real autenticada;
2. filtra e agrega dados;
3. salva somente indicadores seguros em `*.local.json`;
4. não expõe segredos no painel nem no Git.

Conectores planejados:

| Conector | Fonte | Dados exportados para o painel |
|---|---|---|
| `google-sheets` | Google Sheets API v4 | indicadores agregados por aba |
| `google-calendar` | Google Calendar API | contagem de eventos por tipo |
| `gmail` | Gmail API | contagem de e-mails pendentes por categoria |
| `search-console` | Search Console API | cliques, impressões, posição média |
| `whatsapp-business` | WhatsApp Business API | contagem de mensagens por status |

### Camada 5 — IA e Automação (futura)

**Status:** não implementada. Contratos em `core/contracts/`.

Possibilidades:

- resumo automático do dia;
- alerta de tarefas atrasadas;
- sugestão de próxima ação para lead parado;
- análise de tendência de receita;
- rascunho de proposta para clínica.

Regra: nenhuma IA deve receber dados identificáveis de pacientes.

---

## Arquivos seguros — podem ir ao Git

```
painel-soprolife/
├── index.html
├── css/
├── js/
├── data/*.json                    (apenas fictícios/anônimos)
├── data/README.md
├── data/.gitignore
├── scripts/                       (sem segredos embutidos)
├── apps-script/                   (templates sem ID/URL real)
├── modelos-planilhas/             (CSV com dados fictícios)
├── core/                          (contratos e documentação)
├── README.md
├── SECURITY.md
├── AUTOMACOES.md
├── CONFIG_LOCAL.md
├── PLANILHAS_PRIVADAS.md
└── SOPROLIFE_OS_LOCAL_CORE.md
```

---

## Arquivos que NUNCA entram no Git

### Por padrão de nome (cobertos pelo .gitignore)

```
*.local.json
*.private.json
*.secret.json
.env
.env.*
```

### Por pasta

```
painel-soprolife/data-private/      (pasta bloqueada)
~/.config/soprolife/                (fora do repositório)
```

### Por conteúdo proibido

Qualquer arquivo que contenha:

- CPF, RG;
- telefone real de paciente;
- endereço de paciente;
- nome completo de paciente;
- pedido médico, laudo, resultado de exame;
- dado clínico identificável;
- conversa de WhatsApp de paciente;
- URL real de planilha privada;
- ID real de planilha;
- token de API;
- chave de serviço (service account);
- senha ou credencial de qualquer tipo.

---

## Como os scripts atuais se encaixam

```
Planilha privada (Google Sheets)
        │
        │ exportação manual como CSV
        ▼
~/.config/soprolife/painel/resumo-dashboard.csv
        │
        │ scripts/import-summary-csv.sh
        ▼
~/.config/soprolife/painel/resumo-dashboard.json   (privado, fora do Git)
        │
        │ scripts/sync-dashboard-summary.sh
        ▼
painel-soprolife/data/resumo-dashboard.local.json  (git-ignorado)
        │
        │ js/app.js (fetch local)
        ▼
Painel exibe indicadores agregados seguros
```

O script `update-local-data.sh` orquestra todo esse fluxo em um único comando:

```bash
painel-soprolife/scripts/update-local-data.sh
```

---

## Como evoluir para backend privado com login

### Estágio 1 (atual): painel estático local

- HTML/CSS/JS servido por `python3 -m http.server`;
- dados locais atualizados manualmente via scripts;
- acesso via `localhost` ou Tailscale;
- sem autenticação (proteção pelo isolamento de rede).

### Estágio 2: painel estático com dados reais locais

- scripts locais consomem APIs do Google Workspace;
- dados agregados gerados automaticamente em `*.local.json`;
- painel continua estático;
- acesso ainda via localhost/Tailscale.

### Estágio 3: backend leve privado

Opções avaliadas:

| Opção | Complexidade | Custo | Autenticação |
|---|---|---|---|
| Cloudflare Access + Pages | baixa | gratuito | e-mail/SSO |
| Supabase + Row Level Security | média | gratuito (tier) | usuário/senha |
| Firebase + Auth | média | gratuito (tier) | Google login |
| Servidor privado (VPS) | alta | pago | customizável |

Recomendação inicial: **Cloudflare Access** protegendo o painel estático em Pages, com autenticação por e-mail institucional. Sem banco de dados ainda — dados reais ficam no Google Workspace.

### Estágio 4: CRM e agenda integrados com login

- backend com autenticação real;
- painel consome dados via API interna;
- dados reais ficam em banco privado ou Google Workspace;
- nenhum dado de paciente exposto no frontend sem autenticação.

---

## Como conectar fontes reais sem vazar dados

### Google Sheets

1. Criar uma **conta de serviço** no Google Cloud Console.
2. Compartilhar somente as planilhas necessárias com o e-mail da conta de serviço.
3. Salvar o arquivo JSON da conta de serviço em `~/.config/soprolife/painel/service-account.json` (fora do Git, permissão 600).
4. O script lê a planilha, agrega os dados e salva somente indicadores em `*.local.json`.
5. Nenhuma URL, ID ou token vai para o painel ou para o Git.

### Gmail

1. Mesma conta de serviço com escopo `gmail.readonly`.
2. Script conta e-mails por label (ex: "Proposta enviada", "Aguardando retorno").
3. Painel recebe apenas contagens, sem assunto ou remetente.

### Google Calendar

1. Escopo `calendar.readonly` na conta de serviço.
2. Script conta eventos por tipo (consulta, reunião, visita) por semana.
3. Painel recebe somente contagens e datas, sem nomes de pacientes.

### Google Search Console

1. Conta de serviço com acesso à propriedade no Search Console.
2. Script busca métricas agregadas: cliques, impressões, CTR, posição.
3. Nenhum dado de usuário individual é exportado.

### WhatsApp Business

1. Integração via **WhatsApp Business API** (Meta) ou via agregador como Twilio ou Z-API.
2. Webhook recebe eventos, script local filtra e conta por status (novo, respondido, encerrado).
3. Painel recebe somente contagens. Nenhuma conversa ou número é armazenado no painel.
4. Credenciais ficam em `~/.config/soprolife/painel/whatsapp.local.json` (fora do Git).

---

## Pasta `core/` — proposta de estrutura futura

Pasta para contratos de interface e documentação técnica dos conectores. Não contém backend, código de produção ou segredos.

```
painel-soprolife/core/
├── README.md
├── contracts/
│   ├── schema-resumo.json          (contrato JSON do resumo do painel)
│   ├── schema-crm-clinicas.json    (contrato JSON do CRM)
│   ├── schema-leads.json           (contrato JSON de leads)
│   ├── schema-financeiro.json      (contrato JSON financeiro)
│   ├── schema-marketing.json       (contrato JSON de marketing)
│   └── schema-agenda.json          (contrato JSON da agenda)
└── connectors/
    ├── google-sheets.md            (como integrar sem vazar dados)
    ├── google-calendar.md
    ├── gmail.md
    ├── search-console.md
    └── whatsapp-business.md
```

Os contratos definem quais campos o painel aceita de cada fonte, garantindo que conectores futuros não exponham dados proibidos.

---

## Regras permanentes

1. Nenhum dado real de paciente entra no Git em nenhuma fase.
2. Segredos ficam sempre fora do repositório, em `~/.config/soprolife/`.
3. O painel só exibe dados agregados vindos de `*.local.json` git-ignorados.
4. Scripts são versionados sem segredos embutidos.
5. Conectores futuros só exportam indicadores, nunca registros individuais identificáveis.
6. Backend com autenticação real só é implementado com autorização explícita.
