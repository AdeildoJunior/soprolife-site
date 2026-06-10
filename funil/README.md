# Funis Sopro Life v2

Funis de agendamento estáticos (GitHub Pages), com visual premium, vários
templates prontos, rastreio de métricas (GA4 + Meta Pixel) com consentimento
LGPD e integrações fáceis.

## Funis disponíveis (URLs)

| Funil | URL | Uso sugerido |
|-------|-----|--------------|
| Geral (triagem) | `/funil/` | tráfego geral, link na bio |
| Espirometria | `/funil/espirometria/` | anúncios de espirometria |
| Domiciliar | `/funil/domiciliar/` | anúncios de atendimento em casa |
| Parcerias | `/funil/parcerias/` | clínicas, empresas, academias |

## Arquitetura (como tudo se conecta)

- `/assets/analytics.js` — **IDs do GA4 / Meta Pixel + banner de consentimento.**
  Carregado em TODAS as páginas do site (não só no funil). É aqui que se ativa
  o rastreamento. Nada carrega antes do "Aceitar" no banner de cookies.
- `config.js` — configs do funil (WhatsApp, Google Sheets). **Não tem mais IDs.**
- `flows.js` — define as perguntas/etapas de cada funil (templates).
- `engine.js` — motor genérico que desenha qualquer funil. Não precisa editar.
- `style.css` — visual compartilhado por todos os funis.
- `index.html` + subpastas — cada página só escolhe o funil via `data-flow`.

## Eventos enviados ao GA4 (somente após consentimento)

Todos sem dados pessoais — só os parâmetros abaixo:

| Evento | Quando dispara | Parâmetros |
|--------|----------------|------------|
| `page_view` | toda página (padrão GA4) | — |
| `funnel_start` | abertura do funil | `funnel` |
| `funnel_step` | a cada etapa exibida | `funnel`, `step`, `index` |
| `funnel_complete` | envio do formulário | `funnel` |
| `whatsapp_click` | clique em "Abrir WhatsApp" | `funnel` |

`funnel` = qual template (geral/espirometria/domiciliar/parcerias).
**Nunca** enviamos nome, telefone, bairro, observações, pedido médico ou
respostas do funil para o GA4/Meta. Esses dados vão apenas para o WhatsApp e,
se configurado, para a sua planilha do Google Sheets.

## Marcar `funnel_complete` como conversão (evento-chave) no GA4

A conversão é configurada no painel do GA4 (não dá para fazer pelo código).
Passo a passo (GA4 atual chama de **Evento-chave / Key event**):

1. Acesse [analytics.google.com](https://analytics.google.com) e selecione a
   propriedade do site (ID `G-Y6K78ZF191`).
2. Engrenagem **Administrador** (canto inferior esquerdo).
3. Na coluna **Exibição de dados**, clique em **Eventos-chave**
   (em contas antigas: **Conversões**).
4. Clique em **Novo evento-chave** e digite exatamente:
   ```
   funnel_complete
   ```
   e salve. (Alternativa: se `funnel_complete` já aparece na lista de
   **Eventos**, basta ligar a chave **"Marcar como evento-chave"** na linha dele.)
5. Pronto. A partir daí o GA4 conta cada conclusão de funil como conversão.

Dica: você pode marcar também `whatsapp_click` como evento-chave, se quiser
contar o clique no WhatsApp como conversão secundária.

Observações:
- O evento precisa ter sido recebido ao menos uma vez para aparecer na lista
  de Eventos. Se ainda não apareceu, use o botão **Novo evento-chave** e digite
  o nome manualmente (funciona daí em diante).
- Relatórios padrão podem levar até ~24h para consolidar; o **Tempo real**
  mostra os eventos em segundos para você validar.

## Ligar / trocar IDs de rastreamento

Edite **apenas** `/assets/analytics.js`:

```js
window.SITE_ANALYTICS = {
  GA4_ID: "G-Y6K78ZF191",            // GA4 real (ativo)
  META_PIXEL_ID: "000000000000000",  // placeholder — troque para ativar o Pixel
  consentKey: "sl_consent_v1"
};
```

- Enquanto um ID estiver com o placeholder, ele não é usado.
- O Meta Pixel passa a respeitar o mesmo consentimento automaticamente quando
  você colar o ID real.

## Google Sheets (salvar leads numa planilha — opcional)

1. Crie uma planilha no Google Sheets.
2. Extensões → Apps Script, cole um Web App que faça `e.postData` → `appendRow`.
3. Implantar → Novo deploy → tipo "App da Web" → acesso "Qualquer pessoa".
4. Cole a URL gerada em `leadEndpoint` (dentro de `config.js`).

Cada lead chega com: `origem`, `criadoEm` e os campos respondidos. Esse é o
seu banco de leads — é onde nome/telefone devem ir (não vão para GA4/Meta).

## Criar um funil novo (template)

1. Em `flows.js`, duplique um bloco (ex.: `espirometria`) e dê um nome novo.
2. Edite as etapas (perguntas, ícones, cor `accent`).
3. Crie `/funil/NOME/index.html` copiando uma subpasta existente e troque
   `data-flow="NOME"`.

Pronto — o motor, o visual e o rastreamento já funcionam.

## Consentimento / LGPD

- O banner de cookies bloqueia o rastreamento até o "Aceitar"; "Recusar" é
  respeitado e lembrado (localStorage `sl_consent_v1`).
- Para reabrir o banner (ex.: link "Gerenciar cookies"):
  `window.SITE_ANALYTICS.openConsent()`.
- Não peça diagnóstico, CPF, documentos ou detalhes clínicos sensíveis nos
  funis. Use apenas para triagem comercial e organização do atendimento.
