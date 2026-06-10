# Funis Sopro Life v2

Funis de agendamento estáticos (GitHub Pages), com visual premium, vários
templates prontos, rastreio de métricas (GA4 + Meta Pixel) e integrações fáceis.

## Funis disponíveis (URLs)

| Funil | URL | Uso sugerido |
|-------|-----|--------------|
| Geral (triagem) | `/funil/` | tráfego geral, link na bio |
| Espirometria | `/funil/espirometria/` | anúncios de espirometria |
| Domiciliar | `/funil/domiciliar/` | anúncios de atendimento em casa |
| Parcerias | `/funil/parcerias/` | clínicas, empresas, academias |

## Arquitetura (como tudo se conecta)

- `config.js` — **único arquivo que você edita para ligar integrações.**
- `flows.js` — define as perguntas/etapas de cada funil (templates).
- `engine.js` — motor genérico que desenha qualquer funil. Não precisa editar.
- `style.css` — visual compartilhado por todos os funis.
- `index.html` + subpastas — cada página só escolhe o funil via `data-flow`.

## Ligar as integrações (passo a passo)

Abra `config.js` e preencha o que quiser usar. Tudo é opcional — se deixar
em branco, o funil funciona normalmente só com WhatsApp.

```js
window.FUNNEL_CONFIG = {
  whatsapp: "5521999417192",   // já configurado
  leadEndpoint: "",            // Google Sheets (ver abaixo)
  ga4Id: "",                   // ex.: "G-XXXXXXXXXX"
  metaPixelId: "",             // ex.: "123456789012345"
  phoneDisplay: "(21) 99941-7192"
};
```

### 1. Google Analytics 4 (métricas reais e gratuitas)
1. Crie uma propriedade GA4 em analytics.google.com e copie o ID `G-XXXXXXXXXX`.
2. Cole em `ga4Id`.
Eventos enviados automaticamente: `funnel_start`, `funnel_step`,
`funnel_lead`, `funnel_whatsapp_click`, `funnel_restart`.
Veja em **Relatórios → Engajamento → Eventos**. Marque `funnel_lead` como
conversão para acompanhar a taxa de conversão.

### 2. Meta Pixel (Facebook/Instagram Ads)
1. Pegue o ID do Pixel no Gerenciador de Eventos da Meta.
2. Cole em `metaPixelId`.
Eventos mapeados: envio do formulário → **Lead**; clique no WhatsApp →
**Contact**; início → evento custom `FunnelStart`.

### 3. Google Sheets (salvar leads numa planilha)
1. Crie uma planilha no Google Sheets.
2. Extensões → Apps Script, cole um Web App que faça `e.postData` → `appendRow`.
3. Implantar → Novo deploy → tipo "App da Web" → acesso "Qualquer pessoa".
4. Cole a URL gerada em `leadEndpoint`.
Cada lead chega com: `origem`, `criadoEm` e todos os campos respondidos.

## Métricas que você terá prontas

- **Início de funil** por template (`funnel_start`)
- **Abandono por etapa** (`funnel_step` com `step` e `index`)
- **Leads enviados** (`funnel_lead`) → marque como conversão no GA4
- **Cliques no WhatsApp** (`funnel_whatsapp_click`)
- Tudo separado por funil (parâmetro `funnel`).

## Criar um funil novo (template)

1. Em `flows.js`, duplique um bloco (ex.: `espirometria`) e dê um nome novo.
2. Edite as etapas (perguntas, ícones, cor `accent`).
3. Crie `/funil/NOME/index.html` copiando uma subpasta existente e troque
   `data-flow="NOME"`.
Pronto — o motor e o visual já funcionam.

## Observação LGPD / saúde

Não peça diagnóstico, CPF, documentos ou detalhes clínicos sensíveis.
Use os funis apenas para triagem comercial e organização do atendimento.
