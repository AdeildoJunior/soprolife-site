#!/bin/bash
set -e
ROOT="/home/fedorasurf/soprolife-site"
cd "$ROOT"

echo ""
echo "🩹 Aplicando patch v3.1 (ajustes finos)..."
echo "================================================"

# ---------- 1) BACKUP ----------
echo ""
echo "📦 [1/4] Backup..."
mkdir -p backup-pre-v3-1
TS=$(date +%Y%m%d-%H%M%S)
cp index.html "backup-pre-v3-1/index.html.bak.$TS"
cp assets/sl-v3.css "backup-pre-v3-1/sl-v3.css.bak.$TS" 2>/dev/null || true
echo "   ✅ Backup salvo em backup-pre-v3-1/"

# ---------- 2) PATCH NO HTML ----------
echo ""
echo "✏️  [2/4] Editando index.html (texto e estrutura)..."

python3 << 'PY_EOF'
import re

with open('index.html', 'r', encoding='utf-8') as f:
    html = f.read()

original = html

# ---- 1. Trocar "Telemedicina respiratória" por "Teleconsulta respiratória"
html = html.replace('Telemedicina respiratória', 'Teleconsulta respiratória')
html = html.replace('Telemedicina médica', 'Teleconsulta respiratória')

# ---- 2. Remover a pílula "Saúde respiratória no Rio de Janeiro" do hero
html = re.sub(
    r'<span class="sl-hero-v3__kicker">\s*Saúde respiratória no Rio de Janeiro\s*</span>\s*',
    '',
    html
)

# ---- 3. Substituir o título do hero pelo novo texto
html = re.sub(
    r'<h1 class="sl-hero-v3__title">.*?</h1>',
    '<h1 class="sl-hero-v3__title">Espirometria <span>ambulatorial ou domiciliar</span> e teleconsulta com médicos especialistas.</h1>',
    html,
    flags=re.DOTALL
)

# ---- 4. Remover/simplificar o lead redundante
html = re.sub(
    r'<p class="sl-hero-v3__lead">.*?</p>',
    '<p class="sl-hero-v3__lead">Exame respiratório com laudo por pneumologista e consulta online quando você precisar.</p>',
    html,
    flags=re.DOTALL
)

# ---- 5. Botões comerciais no hero
html = html.replace('Agendar pelo WhatsApp', 'Falar no WhatsApp')
html = html.replace('>Ver agenda<', '>Ver agendamento<')

# ---- 6. Trocar título do primeiro card oferta
html = re.sub(
    r'(<div class="sl-offer__body">\s*<h3>)Espirometria(</h3>\s*<p>)[^<]*(</p>)',
    r'\1Agendar espirometria\2Simples, com broncodilatador ou prova de função pulmonar — domiciliar ou em clínica parceira.\3',
    html
)

# Card teleconsulta - texto simplificado
html = re.sub(
    r'(<h3>Teleconsulta respiratória</h3>\s*<p>)[^<]*(</p>)',
    r'\1Consulta online para avaliação respiratória e solicitação de espirometria quando indicada.\2',
    html
)

# ---- 7. Banner telemedicina - texto simplificado
html = re.sub(
    r'(<h3>Consulta médica respiratória sem sair de casa</h3>\s*<p>)[^<]*(</p>)',
    r'\1Consulta online para avaliação respiratória e solicitação de espirometria quando indicada.\2',
    html
)
html = html.replace('>Ver telemedicina <svg', '>Ver médicos <svg')
html = html.replace('>Ver telemedicina<', '>Ver médicos<')

# ---- 8. Remover link "onde fazer espirometria no Rio de Janeiro" dentro de parágrafos
# Remove a tag <a> mantendo apenas se houver, mas tira o link inteiro com seu texto se for esse
html = re.sub(
    r'<a[^>]*>\s*onde fazer espirometria no Rio de Janeiro\s*</a>',
    '',
    html,
    flags=re.IGNORECASE
)
# Também remove se aparecer como texto solto (sem tag)
html = re.sub(
    r',?\s*onde fazer espirometria no Rio de Janeiro\s*\.?',
    '.',
    html,
    flags=re.IGNORECASE
)
# Limpa pontuação duplicada que pode sobrar (..  →  .)
html = re.sub(r'\.\s*\.', '.', html)
html = re.sub(r',\s*\.', '.', html)

# ---- 9. Verificar/garantir item "Agendamento" no menu
# Procura por <nav> ou menu principal; se "Agendamento" não estiver, tenta inserir
menu_match = re.search(r'(<nav[^>]*>.*?</nav>)', html, re.DOTALL | re.IGNORECASE)
if menu_match:
    menu_html = menu_match.group(1)
    if 'agendamento' not in menu_html.lower() and '#agendamento' not in menu_html.lower():
        # Insere item Agendamento antes de Telemedicina, ou no fim da lista
        novo_item = '<a href="#agendamento">Agendamento</a>'
        if 'telemedicina' in menu_html.lower():
            menu_novo = re.sub(
                r'(<a[^>]*href="[^"]*telemedicina[^"]*"[^>]*>)',
                novo_item + r'\1',
                menu_html,
                count=1,
                flags=re.IGNORECASE
            )
        else:
            # insere antes do </nav>
            menu_novo = menu_html.replace('</nav>', novo_item + '</nav>')
        html = html.replace(menu_html, menu_novo)
        print("   ✅ Item 'Agendamento' adicionado ao menu")
    else:
        print("   ℹ️  Item 'Agendamento' já presente no menu")

# ---- Salvar
if html != original:
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("   ✅ index.html atualizado")
else:
    print("   ℹ️  Nenhuma mudança necessária no HTML")
PY_EOF

# ---------- 3) PATCH NO CSS (espaço para barra WhatsApp + ajustes mobile) ----------
echo ""
echo "🎨 [3/4] Adicionando ajustes de CSS (barra WhatsApp mobile)..."

if grep -q "sl-v3-1-patch" assets/sl-v3.css 2>/dev/null; then
  echo "   ℹ️  Patch CSS já estava aplicado, pulando."
else
  cat >> assets/sl-v3.css << 'CSS_EOF'

/* ============================================================
   sl-v3-1-patch — Ajustes finos (mobile + barra WhatsApp)
=========================================================== */

/* Espaço extra no rodapé do <body> em mobile para a barra fixa do WhatsApp não cobrir conteúdo */
@media (max-width: 760px){
  body{
    padding-bottom: 88px !important;
  }
  /* Se houver footer fixo, garantir que ele também respeite */
  footer{
    margin-bottom: 0;
  }
}

/* Hero mais enxuto no mobile (sem a pílula e com título menor) */
@media (max-width: 760px){
  .sl-hero-v3{
    padding: 24px 0 16px;
  }
  .sl-hero-v3__title{
    font-size: clamp(1.7rem, 7vw, 2.3rem);
    margin: 4px 0 12px;
    line-height: 1.1;
  }
  .sl-hero-v3__lead{
    font-size: .95rem;
    margin-bottom: 18px;
  }
  .sl-cta-row{
    flex-direction: column;
    gap: 10px;
  }
  .sl-cta-btn{
    width: 100%;
  }
  .sl-trust-row{
    margin-top: 16px;
    font-size: .85rem;
  }
}

/* Garantir que a barra fixa do WhatsApp tenha z-index correto mas não cubra cliques */
.sl-whatsapp-float,
.whatsapp-float,
.wa-float,
a[href*="wa.me"][class*="float"]{
  bottom: 16px !important;
  z-index: 50;
}
CSS_EOF
  echo "   ✅ Ajustes CSS adicionados"
fi

# ---------- 4) RESULTADO ----------
echo ""
echo "🎉 [4/4] CONCLUÍDO!"
echo "================================================"
echo ""
echo "📂 Arquivos modificados:"
echo "   • index.html        (textos ajustados)"
echo "   • assets/sl-v3.css  (mobile + barra WhatsApp)"
echo "   • backup-pre-v3-1/  (backup com timestamp)"
echo ""
echo "▶️  Para testar:  python3 -m http.server 8165"
echo "↩️  Para reverter: cp backup-pre-v3-1/index.html.bak.* index.html"
echo ""
