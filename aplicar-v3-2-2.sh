#!/bin/bash
set -e
ROOT="/home/fedorasurf/soprolife-site"
cd "$ROOT"

echo ""
echo "✨ Patch v3.2.2 — Polish mobile FINAL (seletores reais)"
echo "======================================================="

# ---------- 1) BACKUP ----------
echo ""
echo "📦 [1/4] Backup..."
mkdir -p backup-pre-v3-2-2
TS=$(date +%Y%m%d-%H%M%S)
cp index.html                              "backup-pre-v3-2-2/index.html.bak.$TS"
cp assets/sl-v3.css                        "backup-pre-v3-2-2/sl-v3.css.bak.$TS" 2>/dev/null || true
cp assets/sopro-visual-system-v3.css       "backup-pre-v3-2-2/sopro-visual-system-v3.css.bak.$TS" 2>/dev/null || true
echo "   ✅ Backup em backup-pre-v3-2-2/"

# ---------- 2) Corrigir bugs no HTML (rgba malformados + telefone divergente) ----------
echo ""
echo "🐛 [2/4] Corrigindo bugs no index.html..."
python3 << 'PY_EOF'
import re
with open('index.html', 'r', encoding='utf-8') as f:
    html = f.read()
original = html

# Corrige rgba(r,g,b.NN) -> rgba(r,g,b,.NN)
html = re.sub(r'rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\.(\d+)\s*\)', r'rgba(\1,\2,\3,.\4)', html)
# Caso especial rgba(0,0,0.NN)
html = re.sub(r'rgba\(\s*0\s*,\s*0\s*,\s*0\.(\d+)\s*\)', r'rgba(0,0,0,.\1)', html)

# Telefone do hero estava divergente (5521999417192) -> padroniza com o resto (5521998901775)
html = html.replace('5521999417192', '5521998901775')

# Remove espaços fantasma antes de sl-links-footer
html = re.sub(r'(<br\s*/?>\s*){2,}(?=\s*<section[^>]*sl-links-footer)', '', html, flags=re.IGNORECASE)
html = re.sub(r'<p>\s*(&nbsp;|\s)*\s*</p>(?=\s*<section[^>]*sl-links-footer)', '', html, flags=re.IGNORECASE)
html = re.sub(r'<div[^>]*>\s*</div>\s*(?=<section[^>]*sl-links-footer)', '', html, flags=re.IGNORECASE)

if html != original:
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("   ✅ rgba() corrigidos, telefone padronizado, espaços fantasma removidos")
else:
    print("   ℹ️  Nada a corrigir")
PY_EOF

# ---------- 3) Polish CSS (seletores reais!) ----------
echo ""
echo "🎨 [3/4] Aplicando polish CSS..."

if grep -q "sl-v3-2-2-polish" assets/sl-v3.css 2>/dev/null; then
  echo "   ℹ️  Já aplicado, ignorando."
else
  cat >> assets/sl-v3.css << 'CSS_EOF'

/* ============================================================
   sl-v3-2-2-polish — Sopro Life mobile (seletores reais)
   Header: .sl-header / .sl-header-in / .sl-brand / .sl-main-nav
   WA header: .sl-nav-cta  |  Instagram: .sl-header-instagram
=========================================================== */

/* -------- 1) Botão WhatsApp do HEADER (.sl-nav-cta) — sem glow -------- */
.sl-header .sl-nav-cta,
header.sl-header .sl-nav-cta{
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  gap: 6px !important;
  padding: 8px 16px !important;
  border-radius: 999px !important;
  background: #25D366 !important;
  color: #fff !important;
  font-weight: 600 !important;
  font-size: .92rem !important;
  text-decoration: none !important;
  border: none !important;
  box-shadow: none !important;
  filter: none !important;
  transition: filter .18s, box-shadow .18s !important;
}
.sl-header .sl-nav-cta:hover{
  filter: brightness(.95);
  box-shadow: 0 3px 10px rgba(37,211,102,.22) !important;
}

/* Pills do WhatsApp (resetar sombras acumuladas) */
.sl-whatsapp-pill{
  box-shadow: none !important;
}
.sl-whatsapp-pill[data-sl="wa"]{
  box-shadow: none !important;
}
.sl-whatsapp-pill[data-sl="wa"]:hover{
  filter: brightness(.96);
  box-shadow: 0 2px 8px rgba(37,211,102,.18) !important;
}

/* -------- 2) Header desktop: alinhamento -------- */
.sl-header .sl-header-in{
  display: flex !important;
  align-items: center !important;
  gap: 14px !important;
}
.sl-header .sl-main-nav{
  display: flex !important;
  align-items: center !important;
  gap: 14px !important;
  flex-wrap: wrap;
}
.sl-header .sl-header-instagram{
  display: inline-grid !important;
  place-items: center !important;
  width: 38px !important;
  height: 38px !important;
  border-radius: 50% !important;
  border: 1px solid rgba(11,34,57,.10) !important;
  background: #fff !important;
  color: var(--sl-navy, #0b2239) !important;
  transition: border-color .18s, transform .18s;
}
.sl-header .sl-header-instagram svg{
  width: 18px;
  height: 18px;
}
.sl-header .sl-header-instagram:hover{
  border-color: var(--sl-teal, #1eb5ab);
  transform: translateY(-1px);
}

/* -------- 3) HEADER MOBILE: enxuto, organizado -------- */
@media (max-width: 880px){
  .sl-header{
    padding: 8px 14px !important;
  }
  .sl-header .sl-header-in{
    flex-wrap: wrap !important;
    gap: 8px !important;
    align-items: center !important;
    justify-content: space-between !important;
  }
  .sl-header .sl-brand{
    flex: 0 0 auto;
    order: 1;
  }
  .sl-header .sl-brand img{
    max-height: 38px !important;
    width: auto !important;
  }
  /* Menu principal vai para baixo, em linha rolável */
  .sl-header .sl-main-nav{
    order: 3 !important;
    flex: 1 1 100% !important;
    gap: 4px !important;
    overflow-x: auto !important;
    -webkit-overflow-scrolling: touch;
    padding: 6px 0 2px !important;
    margin-top: 4px !important;
    scrollbar-width: thin;
  }
  .sl-header .sl-main-nav::-webkit-scrollbar{ height: 4px; }
  .sl-header .sl-nav-link,
  .sl-header .sl-main-nav > a{
    flex: 0 0 auto !important;
    white-space: nowrap !important;
    padding: 6px 10px !important;
    font-size: .88rem !important;
    border-radius: 8px !important;
  }
  /* Instagram + WhatsApp ficam à direita, na primeira linha */
  .sl-header .sl-header-instagram{
    order: 2 !important;
    width: 36px !important;
    height: 36px !important;
  }
  .sl-header .sl-nav-cta{
    order: 2 !important;
    padding: 7px 13px !important;
    font-size: .82rem !important;
  }
  /* Submenu Contato — desktop hover não funciona no mobile */
  .sl-header .sl-has-sub{
    flex: 0 0 auto;
  }
  .sl-header .sl-submenu{
    display: none !important;
  }
}

@media (max-width: 480px){
  .sl-header .sl-nav-cta{
    font-size: .78rem !important;
    padding: 7px 11px !important;
  }
}

/* -------- 4) Scroll margin para âncoras -------- */
section[id],
[id="preparo"],
[id="agendamento"],
[id="links-rodape"],
[id="contato"],
[id="tipos-espirometria"]{
  scroll-margin-top: 90px;
}

/* -------- 5) Seção espirometria — empilhar mobile -------- */
@media (max-width: 820px){
  .sl-section .sl-grid,
  .sl-section [style*="grid-template-columns"],
  .sl-section > .container > div[class*="grid"],
  .sl-section > .sl-wrap > div[class*="grid"]{
    grid-template-columns: 1fr !important;
    gap: 18px !important;
  }
  .sl-section aside,
  .sl-section .sl-card--aside,
  .sl-section [class*="aside"]{
    width: 100% !important;
    max-width: 100% !important;
    margin: 0 !important;
  }
  .sl-section ul{ padding-left: 22px; }
  .sl-section li{ margin-bottom: 8px; line-height: 1.55; }
}

/* -------- 6) Seção "Como se preparar" -------- */
@media (max-width: 760px){
  section[id*="prep" i],
  .sl-section[class*="prep" i]{
    padding-top: 36px !important;
    padding-bottom: 32px !important;
  }
  .sl-kicker,
  [class*="kicker"]{
    display: inline-block !important;
    margin-bottom: 10px !important;
  }
  section[id*="prep" i] [class*="grid"],
  section[id*="prep" i] [class*="cards"],
  .sl-prep-grid{
    grid-template-columns: 1fr !important;
    gap: 12px !important;
  }
}

/* -------- 7) Eliminar espaços fantasma -------- */
section:empty,
.sl-section:empty,
div.container:empty,
div.sl-wrap:empty{
  display: none !important;
}
@media (max-width: 760px){
  .sl-tele-banner{ min-height: 0 !important; }
  .sl-section + .sl-section{ margin-top: 0 !important; }
}

/* -------- 8) Links úteis + redes sociais (mobile) -------- */
@media (max-width: 760px){
  .sl-links-footer{
    padding: 28px 16px !important;
  }
  .sl-links-footer-grid{
    display: grid !important;
    grid-template-columns: 1fr !important;
    gap: 24px !important;
  }
  .sl-useful-links-block h3,
  .sl-links-footer h3{
    margin: 0 0 12px !important;
    padding: 0 !important;
    text-align: left !important;
    font-size: 1.15rem !important;
    line-height: 1.3 !important;
  }
  .sl-links-list,
  .sl-social-list{
    margin: 0 !important;
    padding: 0 !important;
    list-style: none !important;
  }
  .sl-links-list li{ margin-bottom: 10px !important; line-height: 1.5 !important; }
  .sl-links-list a{ display: inline-block; padding: 4px 0; }
  .sl-social-list li{ margin-bottom: 10px !important; }
  .sl-social-list a{
    display: inline-flex !important;
    align-items: center !important;
    gap: 10px !important;
    padding: 6px 0 !important;
    text-decoration: none !important;
  }
  .sl-social-icon{
    width: 34px !important;
    height: 34px !important;
    border-radius: 50% !important;
    display: inline-grid !important;
    place-items: center !important;
    font-weight: 700 !important;
    font-size: .78rem !important;
    flex: 0 0 auto !important;
  }
}

/* -------- 9) Barra fixa WhatsApp inferior -------- */
@media (max-width: 760px){
  body{
    padding-bottom: 92px !important;
  }
  .sl-whatsapp-bar{
    box-shadow: 0 -6px 22px rgba(11,34,57,.12) !important;
  }
  .sl-whatsapp-bar a{
    padding: 12px 14px !important;
    font-size: .92rem !important;
    line-height: 1.3 !important;
  }
}

/* -------- 10) Mobile geral -------- */
@media (max-width: 760px){
  .sl-section{
    padding-left: 16px !important;
    padding-right: 16px !important;
  }
  h2.sl-section-title,
  .sl-section h2{
    font-size: clamp(1.35rem, 5vw, 1.7rem) !important;
    line-height: 1.25 !important;
    margin-bottom: 14px !important;
  }
}
CSS_EOF
  echo "   ✅ Polish v3.2.2 adicionado"
fi

# ---------- 4) Reforço no sopro-visual-system-v3.css ----------
echo ""
echo "🎨 [4/4] Reforçando neutralização de glow..."
if [ -f assets/sopro-visual-system-v3.css ] && ! grep -q "sl-v3-2-2-header-fix" assets/sopro-visual-system-v3.css; then
  cat >> assets/sopro-visual-system-v3.css << 'CSS2_EOF'

/* sl-v3-2-2-header-fix */
.sl-header .sl-nav-cta,
.sl-header .sl-cta-btn--wa{ box-shadow: none !important; }
CSS2_EOF
  echo "   ✅ Reforço aplicado"
else
  echo "   ℹ️  Já aplicado ou arquivo não existe"
fi

echo ""
echo "🎉 Patch v3.2.2 concluído!"
echo "======================================================="
echo ""
echo "🔎 Diff:           git diff --stat"
echo "▶️  Servidor:      python3 -m http.server 8165"
echo "🌐 Desktop:        http://localhost:8165/?v=v3-2-2"
echo "📱 Android:        http://\$(hostname -I | awk '{print \$1}'):8165/?v=v3-2-2"
echo ""
echo "↩️  Reverter:"
echo "   cp backup-pre-v3-2-2/index.html.bak.* index.html"
echo "   cp backup-pre-v3-2-2/sl-v3.css.bak.* assets/sl-v3.css"
echo "   cp backup-pre-v3-2-2/sopro-visual-system-v3.css.bak.* assets/sopro-visual-system-v3.css"
echo ""
