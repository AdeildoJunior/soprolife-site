#!/bin/bash
# ================================================================
#  SOPRO LIFE — Aplicador automático do Visual v3
# ================================================================
set -e

ROOT="/home/fedorasurf/soprolife-site"
cd "$ROOT"

echo ""
echo "🚀 Iniciando aplicação do Visual v3..."
echo "================================================"

# ---------- 1) BACKUP ----------
echo ""
echo "📦 [1/6] Criando backup..."
mkdir -p backup-pre-v3
cp index.html "backup-pre-v3/index.html.bak.$(date +%Y%m%d-%H%M%S)"
echo "   ✅ Backup salvo em backup-pre-v3/"

# ---------- 2) CSS v3 ----------
echo ""
echo "🎨 [2/6] Criando assets/sl-v3.css..."
mkdir -p assets
cat > assets/sl-v3.css << 'CSS_EOF'
:root{
  --sl-navy:#0b2239; --sl-navy-2:#143852;
  --sl-teal:#1eb5ab; --sl-teal-dark:#07857d; --sl-teal-soft:#e8faf8;
  --sl-bg:#f7fbfc; --sl-line:rgba(11,34,57,.09); --sl-muted:#5f7485;
  --sl-wa:#25D366;
  --sl-radius-md:18px; --sl-radius-lg:24px; --sl-radius-xl:32px;
  --sl-shadow-2:0 10px 28px rgba(11,34,57,.07);
  --sl-shadow-3:0 22px 55px rgba(11,34,57,.10);
}
.sl-hero-v3{position:relative;padding:56px 0 36px;background:radial-gradient(900px 420px at 8% -10%,rgba(30,181,171,.10),transparent 60%),radial-gradient(700px 380px at 100% 0%,rgba(11,34,57,.05),transparent 60%),linear-gradient(180deg,#fff 0%,var(--sl-bg) 100%);overflow:hidden}
.sl-hero-v3__wrap{max-width:1120px;margin:0 auto;padding:0 24px;display:grid;grid-template-columns:1.05fr .95fr;gap:48px;align-items:center}
.sl-hero-v3__kicker{display:inline-flex;align-items:center;gap:8px;padding:7px 14px;border-radius:999px;background:rgba(30,181,171,.12);color:var(--sl-teal-dark);font-size:.76rem;font-weight:800;letter-spacing:.14em;text-transform:uppercase}
.sl-hero-v3__kicker::before{content:"";width:6px;height:6px;border-radius:999px;background:var(--sl-teal);box-shadow:0 0 0 4px rgba(30,181,171,.18)}
.sl-hero-v3__title{margin:18px 0 14px;font-size:clamp(2.2rem,4.8vw,3.6rem);line-height:1.04;letter-spacing:-.035em;color:var(--sl-navy);font-weight:750}
.sl-hero-v3__title span{color:var(--sl-teal)}
.sl-hero-v3__lead{max-width:560px;color:var(--sl-muted);font-size:1.05rem;line-height:1.6;margin:0 0 28px}
.sl-trust-row{display:flex;flex-wrap:wrap;gap:10px 18px;margin-top:22px;color:var(--sl-muted);font-size:.9rem}
.sl-trust-row span{display:inline-flex;align-items:center;gap:8px}
.sl-trust-row svg{width:18px;height:18px;color:var(--sl-teal);flex:0 0 18px}
.sl-offer-stack{display:grid;gap:16px}
.sl-offer{position:relative;display:grid;grid-template-columns:56px 1fr auto;gap:16px;align-items:center;padding:22px;border-radius:var(--sl-radius-lg);background:#fff;border:1px solid var(--sl-line);box-shadow:var(--sl-shadow-2);text-decoration:none;color:var(--sl-navy);transition:transform .18s,box-shadow .18s,border-color .18s}
.sl-offer:hover{transform:translateY(-2px);box-shadow:var(--sl-shadow-3);border-color:rgba(30,181,171,.30)}
.sl-offer__icon{width:56px;height:56px;border-radius:16px;display:grid;place-items:center;background:linear-gradient(135deg,var(--sl-teal-soft),#fff);border:1px solid rgba(30,181,171,.22);color:var(--sl-teal-dark)}
.sl-offer__icon svg{width:28px;height:28px}
.sl-offer__body h3{margin:0 0 4px;font-size:1.12rem;font-weight:750;letter-spacing:-.015em;color:var(--sl-navy)}
.sl-offer__body p{margin:0;font-size:.92rem;line-height:1.45;color:var(--sl-muted)}
.sl-offer__arrow{width:38px;height:38px;border-radius:999px;display:grid;place-items:center;background:var(--sl-teal-soft);color:var(--sl-teal-dark);transition:background .18s,transform .18s}
.sl-offer:hover .sl-offer__arrow{background:var(--sl-teal);color:#fff;transform:translateX(2px)}
.sl-offer__arrow svg{width:18px;height:18px}
.sl-offer--primary{background:linear-gradient(135deg,#0b2239 0%,#143852 100%);color:#fff;border-color:transparent}
.sl-offer--primary .sl-offer__body h3{color:#fff}
.sl-offer--primary .sl-offer__body p{color:rgba(255,255,255,.78)}
.sl-offer--primary .sl-offer__icon{background:rgba(30,181,171,.18);border-color:rgba(30,181,171,.35);color:#6ee0d6}
.sl-offer--primary .sl-offer__arrow{background:var(--sl-teal);color:#fff}
.sl-cta-row{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px}
.sl-cta-btn{display:inline-flex;align-items:center;justify-content:center;gap:10px;padding:14px 24px;border-radius:999px;font-weight:750;font-size:.98rem;text-decoration:none;border:1px solid transparent;transition:transform .15s,box-shadow .15s,background .15s;cursor:pointer}
.sl-cta-btn--wa{background:var(--sl-wa);color:#fff;box-shadow:0 12px 24px rgba(37,211,102,.22)}
.sl-cta-btn--wa:hover{transform:translateY(-1px);box-shadow:0 16px 30px rgba(37,211,102,.30)}
.sl-cta-btn--ghost{background:#fff;color:var(--sl-navy);border-color:var(--sl-line)}
.sl-cta-btn--ghost:hover{border-color:rgba(30,181,171,.40);background:var(--sl-teal-soft)}
.sl-features{padding:64px 0;background:#fff}
.sl-features__wrap{max-width:1120px;margin:0 auto;padding:0 24px}
.sl-features__head{max-width:640px;margin:0 auto 36px;text-align:center}
.sl-features__head h2{margin:12px 0 10px;font-size:clamp(1.7rem,2.6vw,2.3rem);letter-spacing:-.03em;color:var(--sl-navy);font-weight:720}
.sl-features__head p{color:var(--sl-muted);line-height:1.6;margin:0}
.sl-features__grid{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}
.sl-feature{padding:24px 22px;border-radius:var(--sl-radius-md);background:var(--sl-bg);border:1px solid var(--sl-line);transition:transform .2s,box-shadow .2s}
.sl-feature:hover{transform:translateY(-3px);box-shadow:var(--sl-shadow-2)}
.sl-feature__icon{width:44px;height:44px;border-radius:12px;display:grid;place-items:center;background:#fff;border:1px solid rgba(30,181,171,.20);color:var(--sl-teal-dark);margin-bottom:14px}
.sl-feature__icon svg{width:22px;height:22px}
.sl-feature h4{margin:0 0 6px;font-size:1.02rem;color:var(--sl-navy);font-weight:720;letter-spacing:-.015em}
.sl-feature p{margin:0;font-size:.92rem;line-height:1.5;color:var(--sl-muted)}
.sl-tele-banner{margin:24px auto;max-width:1120px;padding:36px;border-radius:var(--sl-radius-xl);background:radial-gradient(600px 280px at 90% 50%,rgba(30,181,171,.20),transparent 70%),linear-gradient(135deg,#0b2239 0%,#143852 100%);color:#fff;display:grid;grid-template-columns:1.4fr 1fr;gap:32px;align-items:center;overflow:hidden;position:relative}
.sl-tele-banner__kicker{display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border-radius:999px;background:rgba(30,181,171,.18);color:#6ee0d6;font-size:.74rem;font-weight:800;letter-spacing:.14em;text-transform:uppercase}
.sl-tele-banner h3{margin:14px 0 10px;font-size:clamp(1.6rem,2.6vw,2.2rem);line-height:1.1;letter-spacing:-.03em;font-weight:750}
.sl-tele-banner p{margin:0 0 22px;color:rgba(255,255,255,.82);line-height:1.6;max-width:520px}
.sl-tele-banner__visual{display:grid;place-items:center}
.sl-tele-banner__visual svg{width:100%;max-width:280px;height:auto;opacity:.92}
@media (max-width:900px){
  .sl-hero-v3{padding:32px 0 20px}
  .sl-hero-v3__wrap{grid-template-columns:1fr;gap:28px}
  .sl-features__grid{grid-template-columns:repeat(2,1fr)}
  .sl-tele-banner{grid-template-columns:1fr;padding:28px 22px;border-radius:var(--sl-radius-lg);margin:24px}
  .sl-tele-banner__visual{display:none}
}
@media (max-width:760px){
  .sl-hero-v3__lead{font-size:.98rem;margin-bottom:22px}
  .sl-offer--primary{order:-1}
}
@media (max-width:560px){
  .sl-features__grid{grid-template-columns:1fr}
  .sl-offer{grid-template-columns:48px 1fr;padding:18px}
  .sl-offer__icon{width:48px;height:48px}
  .sl-offer__arrow{display:none}
  .sl-cta-btn{width:100%}
  .sl-trust-row{font-size:.85rem}
}
CSS_EOF
echo "   ✅ CSS criado: assets/sl-v3.css"

# ---------- 3) LINK DO CSS NO <head> ----------
echo ""
echo "🔗 [3/6] Linkando CSS v3 no <head>..."
if grep -q "sl-v3.css" index.html; then
  echo "   ℹ️  CSS já estava linkado, pulando."
else
  sed -i 's|</head>|  <link rel="stylesheet" href="assets/sl-v3.css?v=1">\n</head>|' index.html
  echo "   ✅ Link inserido antes de </head>"
fi

# ---------- 4) GERAR BLOCOS HTML ----------
echo ""
echo "🧩 [4/6] Gerando blocos HTML temporários..."
mkdir -p patches

cat > patches/01-hero-v3.html << 'HERO_EOF'
<!-- ============== HERO V3 — Espirometria + Telemedicina ============== -->
<section class="sl-hero-v3">
  <div class="sl-hero-v3__wrap">
    <div>
      <span class="sl-hero-v3__kicker">Saúde respiratória no Rio de Janeiro</span>
      <h1 class="sl-hero-v3__title">Cuidando da sua <span>respiração</span> com tecnologia e acolhimento</h1>
      <p class="sl-hero-v3__lead">Espirometria com laudo por pneumologista e teleconsulta respiratória. Atendimento domiciliar, em parceria com clínicas e online — você escolhe.</p>
      <div class="sl-cta-row">
        <a class="sl-cta-btn sl-cta-btn--wa" href="https://wa.me/5521999417192?text=Ol%C3%A1%2C%20gostaria%20de%20agendar%20um%20atendimento%20na%20Sopro%20Life." target="_blank" rel="noopener">
          <svg viewBox="0 0 24 24" fill="currentColor" width="18" height="18" aria-hidden="true"><path d="M17.6 6.3A7.85 7.85 0 0 0 12 4a7.94 7.94 0 0 0-6.8 12L4 20l4.1-1.1A7.94 7.94 0 0 0 20 12a7.85 7.85 0 0 0-2.4-5.7zM12 18.5a6.6 6.6 0 0 1-3.4-.9l-.2-.1-2.4.6.6-2.3-.2-.3a6.5 6.5 0 0 1 10.1-8 6.5 6.5 0 0 1-4.5 11zm3.6-4.9c-.2-.1-1.2-.6-1.4-.6s-.3 0-.5.2-.5.6-.6.8-.2.1-.4 0a5.4 5.4 0 0 1-1.6-1 6 6 0 0 1-1.1-1.4c-.1-.2 0-.3.1-.4l.3-.4.2-.3v-.3l-.6-1.4c-.2-.3-.3-.3-.4-.3h-.4a.7.7 0 0 0-.5.3 2.2 2.2 0 0 0-.7 1.7 3.9 3.9 0 0 0 .8 2 8.7 8.7 0 0 0 3.5 3.1c1.2.5 1.7.5 2.3.4a2 2 0 0 0 1.3-.9 1.6 1.6 0 0 0 .1-.9c-.1-.1-.2-.2-.4-.3z"/></svg>
          Agendar pelo WhatsApp
        </a>
        <a class="sl-cta-btn sl-cta-btn--ghost" href="#agendamento">Ver agenda</a>
      </div>
      <div class="sl-trust-row">
        <span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg> Laudo por pneumologista</span>
        <span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg> Atendimento domiciliar</span>
        <span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg> Resultados ágeis</span>
      </div>
    </div>
    <div class="sl-offer-stack" aria-label="Serviços principais">
      <a class="sl-offer sl-offer--primary" href="#agendamento">
        <div class="sl-offer__icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v8"/><path d="M8 8c-3 0-5 3-5 7 0 3 2 5 4 5 1.5 0 2-1 2-3v-9"/><path d="M16 8c3 0 5 3 5 7 0 3-2 5-4 5-1.5 0-2-1-2-3v-9"/></svg></div>
        <div class="sl-offer__body"><h3>Espirometria</h3><p>Simples, com broncodilatador ou prova de função pulmonar — agenda rápida e domiciliar.</p></div>
        <span class="sl-offer__arrow" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg></span>
      </a>
      <a class="sl-offer" href="telemedicina/">
        <div class="sl-offer__icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3z"/></svg></div>
        <div class="sl-offer__body"><h3>Telemedicina respiratória</h3><p>Consulta online com avaliação clínica e solicitação de exames quando houver indicação.</p></div>
        <span class="sl-offer__arrow" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg></span>
      </a>
    </div>
  </div>
</section>
<!-- ============== BANNER TELEMEDICINA ============== -->
<aside class="sl-tele-banner">
  <div>
    <span class="sl-tele-banner__kicker">Novidade</span>
    <h3>Consulta médica respiratória sem sair de casa</h3>
    <p>Avaliação online com médicas parceiras. Indicado para sintomas respiratórios, orientação clínica e solicitação de espirometria quando houver indicação.</p>
    <a class="sl-cta-btn sl-cta-btn--wa" href="telemedicina/" style="background:#1eb5ab;box-shadow:0 12px 24px rgba(30,181,171,.30);">Ver telemedicina <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><path d="M5 12h14M13 6l6 6-6 6"/></svg></a>
  </div>
  <div class="sl-tele-banner__visual" aria-hidden="true">
    <svg viewBox="0 0 200 200" fill="none"><circle cx="100" cy="100" r="80" stroke="rgba(110,224,214,.30)" stroke-width="1.5"/><circle cx="100" cy="100" r="55" stroke="rgba(110,224,214,.50)" stroke-width="1.5"/><rect x="65" y="78" width="70" height="44" rx="6" stroke="#6ee0d6" stroke-width="2"/><path d="M135 92l18-8v32l-18-8z" stroke="#6ee0d6" stroke-width="2" fill="rgba(110,224,214,.15)"/><circle cx="100" cy="100" r="4" fill="#6ee0d6"/></svg>
  </div>
</aside>
<!-- ============== DIFERENCIAIS ============== -->
<section class="sl-features">
  <div class="sl-features__wrap">
    <div class="sl-features__head">
      <span class="sl-hero-v3__kicker">Por que a Sopro Life</span>
      <h2>Cuidado respiratório do começo ao fim</h2>
      <p>Da avaliação clínica ao laudo, tudo em um só lugar — com agilidade e atendimento humanizado.</p>
    </div>
    <div class="sl-features__grid">
      <div class="sl-feature"><div class="sl-feature__icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3-9 4 18 3-9h4"/></svg></div><h4>Espirometria completa</h4><p>Simples, com broncodilatador ou prova de função pulmonar.</p></div>
      <div class="sl-feature"><div class="sl-feature__icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10l9-7 9 7v10a2 2 0 0 1-2 2h-4v-7H9v7H5a2 2 0 0 1-2-2z"/></svg></div><h4>Atendimento domiciliar</h4><p>Nossa equipe vai até você, com todo o conforto e privacidade.</p></div>
      <div class="sl-feature"><div class="sl-feature__icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3z"/></svg></div><h4>Teleconsulta</h4><p>Consulta online com médicos parceiros para avaliação clínica.</p></div>
      <div class="sl-feature"><div class="sl-feature__icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h4"/></svg></div><h4>Laudo ágil</h4><p>Laudo emitido por pneumologista, entregue rapidamente.</p></div>
    </div>
  </div>
</section>
HERO_EOF
echo "   ✅ Bloco HTML gerado"

# ---------- 5) SUBSTITUIR HERO ANTIGO POR NOVO ----------
echo ""
echo "✏️  [5/6] Substituindo HERO antigo pelo novo no index.html..."

if grep -q "sl-hero-v3" index.html; then
  echo "   ℹ️  HERO v3 já estava aplicado, pulando substituição."
else
  python3 << 'PY_EOF'
import re

with open('index.html', 'r', encoding='utf-8') as f:
    html = f.read()

with open('patches/01-hero-v3.html', 'r', encoding='utf-8') as f:
    novo = f.read()

# Procura <section class="sl-hero"...> ... </section> (primeira ocorrência, não-guloso)
pattern = re.compile(
    r'<section[^>]*class="[^"]*\bsl-hero\b[^"]*"[^>]*>.*?</section>',
    re.DOTALL
)

match = pattern.search(html)
if match:
    html_novo = html[:match.start()] + novo + html[match.end():]
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html_novo)
    print("   ✅ HERO antigo substituído com sucesso!")
else:
    # Se não achar sl-hero, insere logo depois do <body> ou após </header>
    header_close = re.search(r'</header>', html)
    if header_close:
        idx = header_close.end()
        html_novo = html[:idx] + "\n" + novo + "\n" + html[idx:]
        with open('index.html', 'w', encoding='utf-8') as f:
            f.write(html_novo)
        print("   ✅ Bloco inserido após </header> (sl-hero não encontrado).")
    else:
        body_open = re.search(r'<body[^>]*>', html)
        if body_open:
            idx = body_open.end()
            html_novo = html[:idx] + "\n" + novo + "\n" + html[idx:]
            with open('index.html', 'w', encoding='utf-8') as f:
                f.write(html_novo)
            print("   ✅ Bloco inserido após <body> (sl-hero e header não encontrados).")
        else:
            print("   ❌ Não foi possível inserir o bloco. Edite manualmente.")
PY_EOF
fi

# ---------- 6) RESULTADO ----------
echo ""
echo "🎉 [6/6] CONCLUÍDO!"
echo "================================================"
echo ""
echo "📂 Arquivos modificados:"
echo "   • assets/sl-v3.css     (novo)"
echo "   • index.html           (atualizado)"
echo "   • backup-pre-v3/       (backup do original)"
echo ""
echo "▶️  Para testar localmente, rode:"
echo "   python3 -m http.server 8165"
echo ""
echo "   E abra no navegador:"
echo "   http://localhost:8165/?v=visual-v3"
echo ""
echo "↩️  Se quiser desfazer:"
echo "   cp backup-pre-v3/index.html.bak.* index.html"
echo ""
