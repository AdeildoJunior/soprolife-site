#!/bin/bash
set -e
ROOT="/home/fedorasurf/soprolife-site"
cd "$ROOT"

echo ""
echo "✨ Patch v3.3.2 — Polish Final (cirurgia exata)"
echo "==============================================="

# ---------- 1) BACKUP ----------
echo ""
echo "📦 [1/4] Backup..."
mkdir -p backup-pre-v3-3-2
TS=$(date +%Y%m%d-%H%M%S)
cp index.html                              "backup-pre-v3-3-2/index.html.bak.$TS"
cp assets/sl-v3.css                        "backup-pre-v3-3-2/sl-v3.css.bak.$TS" 2>/dev/null || true
cp assets/sopro-visual-system-v3.css       "backup-pre-v3-3-2/sopro-visual-system-v3.css.bak.$TS" 2>/dev/null || true
echo "   ✅ Backup em backup-pre-v3-3-2/"

# ---------- 2) PATCH NO HTML ----------
echo ""
echo "✏️  [2/4] Cirurgias no index.html..."

python3 << 'PY_EOF'
import re

with open('index.html', 'r', encoding='utf-8') as f:
    html = f.read()
original = html
mudancas = []

# ============================================================
# A) Padronização de textos (incluindo o que está no banner)
# ============================================================
replacements = [
    ('Consulta médica respiratória sem sair de casa', 'Teleconsulta Médica Respiratória'),
    ('Consulta online para avaliação respiratória e solicitação de espirometria quando indicada.',
     'Atendimento online com pneumologista — avaliação e orientação respiratória.'),
    ('Consulta online respiratória', 'Teleconsulta Médica Respiratória'),
    ('Consulta Online Respiratória', 'Teleconsulta Médica Respiratória'),
    ('Teleconsulta respiratória', 'Teleconsulta Médica Respiratória'),
    ('teleconsulta respiratória', 'Teleconsulta Médica Respiratória'),
    ('Telemedicina respiratória', 'Teleconsulta Médica Respiratória'),
    ('Consulta Médica Respiratória Online', 'Teleconsulta Médica Respiratória'),
]
for old, new in replacements:
    if old in html:
        c = html.count(old)
        html = html.replace(old, new)
        mudancas.append(f"   ✓ {c}× texto '{old[:50]}...' padronizado")

# ============================================================
# B) data-mobile-text no botão WA do header
# ============================================================
def add_mobile_text(match):
    tag = match.group(1)
    if 'data-mobile-text' in tag:
        return match.group(0)
    return tag[:-1] + ' data-mobile-text="WhatsApp">' + match.group(2) + match.group(3)

novo = re.sub(
    r'(<a[^>]*class="[^"]*sl-nav-cta[^"]*"[^>]*>)(\s*Falar no WhatsApp\s*)(</a>)',
    add_mobile_text, html, flags=re.IGNORECASE
)
if novo != html:
    mudancas.append("   ✓ data-mobile-text adicionado ao botão WA do header")
    html = novo

# ============================================================
# C) Remover box-shadow inline do botão "Ver médicos"
# Estratégia: localiza o style do <a> que contém "Ver médicos" em qualquer
# posição (mesmo com <svg> antes do fechamento), e limpa o box-shadow
# ============================================================
def limpar_a_ver_medicos(match):
    bloco = match.group(0)
    # Remove box-shadow:...; dentro do style
    novo = re.sub(
        r'(style="[^"]*?)box-shadow\s*:\s*[^;"]+;?\s*([^"]*")',
        r'\1\2', bloco
    )
    # Limpa style residual vazio ou só com ;
    novo = re.sub(r'style="\s*;*\s*"', '', novo)
    return novo

# Match em <a ...style="...">...Ver médicos...</a> (com .*? non-greedy)
padrao_ver_medicos = re.compile(
    r'<a[^>]*style="[^"]*box-shadow[^"]*"[^>]*>.*?Ver médicos.*?</a>',
    re.IGNORECASE | re.DOTALL
)
novo = padrao_ver_medicos.sub(limpar_a_ver_medicos, html)
if novo != html:
    mudancas.append("   ✓ box-shadow inline removido do botão 'Ver médicos'")
    html = novo

# ============================================================
# D) Atenuar a sombra pesada do .sl-header DENTRO do <style> interno
# Substitui declarações '0 8px 24px rgba(15,35,52,.035)' por sombra leve
# ============================================================
novo = re.sub(
    r'box-shadow:\s*0\s+8px\s+24px\s+rgba\(15,\s*35,\s*52,\s*\.035\)\s*!important;?',
    'box-shadow: 0 1px 0 rgba(11,34,57,.06), 0 1px 4px rgba(11,34,57,.04) !important;',
    html
)
if novo != html:
    mudancas.append("   ✓ box-shadow pesado do .sl-header (8px 24px) suavizado")
    html = novo

if html != original:
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"   ✅ index.html atualizado:")
    for m in mudancas:
        print(m)
else:
    print("   ℹ️  Nada a alterar no HTML")
PY_EOF

# ---------- 3) PATCH NO CSS ----------
echo ""
echo "🎨 [3/4] Aplicando polish CSS..."

if grep -q "sl-v3-3-2-polish" assets/sl-v3.css 2>/dev/null; then
  echo "   ℹ️  Patch v3.3.2 já aplicado, ignorando."
else
  cat >> assets/sl-v3.css << 'CSS_EOF'

/* ============================================================
   sl-v3-3-2-polish — Sopro Life
   Foco: alinhamento, header suave, botão Ver médicos, padronização
=========================================================== */

/* -------- Variáveis -------- */
:root{
  --sl-container-max: 1120px;
  --sl-container-pad: 20px;
}
@media (max-width: 760px){
  :root{ --sl-container-pad: 16px; }
}

/* -------- 1) Containers padronizados em 1120px -------- */
.sl-wrap,
.sl-section > .container,
.sl-section .container,
.sl-cta-box,
.sl-links-footer-grid,
.sl-booking-wrap{
  max-width: var(--sl-container-max) !important;
  margin-left: auto !important;
  margin-right: auto !important;
  padding-left: var(--sl-container-pad) !important;
  padding-right: var(--sl-container-pad) !important;
}

/* -------- 2) HEADER: sombra suave (sobrescreve tudo) -------- */
html body header.sl-header,
html body .sl-header{
  box-shadow: 0 1px 0 rgba(11,34,57,.06), 0 1px 4px rgba(11,34,57,.04) !important;
  border-bottom: 1px solid rgba(11,34,57,.05) !important;
}

/* -------- 3) HEADER MOBILE: compactar -------- */
@media (max-width: 880px){
  .sl-header{
    padding: 6px 12px !important;
  }
  .sl-header .sl-header-in{
    gap: 6px !important;
  }
  .sl-header .sl-brand img{
    max-height: 34px !important;
  }
  .sl-header .sl-main-nav{
    gap: 2px !important;
    padding: 4px 0 0 !important;
    margin-top: 2px !important;
  }
  .sl-header .sl-nav-link,
  .sl-header .sl-main-nav > a{
    padding: 5px 9px !important;
    font-size: .84rem !important;
  }
  .sl-header .sl-header-instagram{
    width: 34px !important;
    height: 34px !important;
  }
}

/* -------- 4) Botão WhatsApp do HEADER -------- */
.sl-header .sl-nav-cta{
  box-shadow: none !important;
  padding: 8px 16px !important;
  font-size: .92rem !important;
  white-space: nowrap !important;
  background: #25D366 !important;
  color: #fff !important;
  border-radius: 999px !important;
}
.sl-header .sl-nav-cta:hover{
  filter: brightness(.95);
  box-shadow: 0 3px 10px rgba(37,211,102,.22) !important;
}
@media (max-width: 880px){
  .sl-header .sl-nav-cta[data-mobile-text]{
    font-size: 0 !important;
    padding: 7px 13px !important;
    line-height: 1 !important;
  }
  .sl-header .sl-nav-cta[data-mobile-text]::before{
    content: attr(data-mobile-text);
    font-size: .82rem !important;
    font-weight: 600;
    color: #fff;
    white-space: nowrap;
  }
}
@media (max-width: 380px){
  .sl-header .sl-nav-cta[data-mobile-text]::before{
    font-size: .78rem !important;
  }
  .sl-header .sl-nav-cta[data-mobile-text]{
    padding: 7px 10px !important;
  }
}

/* -------- 5) Barra fixa WA inferior: -20% no mobile -------- */
@media (max-width: 760px){
  .sl-whatsapp-bar{
    box-shadow: 0 -4px 16px rgba(11,34,57,.10) !important;
  }
  .sl-whatsapp-bar a{
    padding: 9px 12px !important;
    font-size: .82rem !important;
    line-height: 1.25 !important;
    gap: 8px !important;
  }
  .sl-whatsapp-bar a > span:first-child{
    font-size: 1rem !important;
  }
  body{
    padding-bottom: 72px !important;
  }
}

/* -------- 6) Banner Telemedicina: gradient mais discreto -------- */
.sl-tele-banner{
  background:
    radial-gradient(600px 280px at 90% 50%, rgba(30,181,171,.10), transparent 70%),
    linear-gradient(135deg, #0b2239 0%, #143852 100%) !important;
}

/* -------- 7) Botão "Ver médicos" — sombra LEVE (override total) -------- */
html body .sl-tele-banner a.sl-cta-btn,
html body .sl-tele-banner a[href*="telemedicina"],
html body a.sl-cta-btn[href*="telemedicina/"]{
  box-shadow: 0 2px 8px rgba(30,181,171,.18) !important;
  background: #1eb5ab !important;
  transition: box-shadow .2s, transform .2s, filter .2s !important;
}
html body .sl-tele-banner a.sl-cta-btn:hover,
html body .sl-tele-banner a[href*="telemedicina"]:hover{
  box-shadow: 0 4px 14px rgba(30,181,171,.28) !important;
  transform: translateY(-1px);
  filter: brightness(1.04);
}

/* -------- 8) Espaçamentos consistentes entre seções -------- */
.sl-section{
  padding-top: clamp(40px, 6vw, 72px) !important;
  padding-bottom: clamp(40px, 6vw, 72px) !important;
}
@media (max-width: 760px){
  .sl-section{
    padding-top: 36px !important;
    padding-bottom: 36px !important;
  }
}

/* -------- 9) Cards: suavização extra sem quebrar variáveis existentes -------- */
.sl-offer,
.sl-feature,
.sl-card{
  transition: transform .2s, box-shadow .2s !important;
}
CSS_EOF
  echo "   ✅ Polish v3.3.2 adicionado"
fi

# ---------- 4) Reforço no visual-system ----------
echo ""
echo "🎨 [4/4] Reforço com especificidade máxima..."
if [ -f assets/sopro-visual-system-v3.css ] && ! grep -q "sl-v3-3-2-final" assets/sopro-visual-system-v3.css; then
  cat >> assets/sopro-visual-system-v3.css << 'CSS2_EOF'

/* sl-v3-3-2-final — última palavra em cascade */
html body header.sl-header,
html body .sl-header{
  box-shadow: 0 1px 0 rgba(11,34,57,.06), 0 1px 4px rgba(11,34,57,.04) !important;
}
html body .sl-tele-banner{
  background:
    radial-gradient(600px 280px at 90% 50%, rgba(30,181,171,.10), transparent 70%),
    linear-gradient(135deg, #0b2239 0%, #143852 100%) !important;
}
html body .sl-tele-banner a.sl-cta-btn,
html body .sl-tele-banner a[href*="telemedicina"]{
  box-shadow: 0 2px 8px rgba(30,181,171,.18) !important;
}
CSS2_EOF
  echo "   ✅ Reforço aplicado"
else
  echo "   ℹ️  Já aplicado"
fi

echo ""
echo "🎉 Patch v3.3.2 concluído!"
echo "==============================================="
echo ""
echo "🔎 Diff:           git diff --stat"
echo "▶️  Servidor:      python3 -m http.server 8165"
echo ""
echo "↩️  Reverter:"
echo "   cp backup-pre-v3-3-2/index.html.bak.* index.html"
echo "   cp backup-pre-v3-3-2/sl-v3.css.bak.* assets/sl-v3.css"
echo "   cp backup-pre-v3-3-2/sopro-visual-system-v3.css.bak.* assets/sopro-visual-system-v3.css"
echo ""
