"""Audita metadados, JSON-LD, links originais e arquivos fora do escopo."""
from pathlib import Path
from html.parser import HTMLParser
import subprocess
import json
ROOT = Path(__file__).resolve().parents[1]
BASE = 'f866c75'
class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(); self.metadata=[]; self.links=[]; self.capture=None; self.feed(text)
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='meta' or (tag=='link' and a.get('rel') in ['canonical','alternate']): self.metadata.append((tag,attrs))
        if tag=='a' and a.get('href'): self.links.append(a['href'])
        if tag=='title' or (tag=='script' and a.get('type')=='application/ld+json'): self.capture=tag
    def handle_data(self, data):
        if self.capture: self.metadata.append((self.capture,data.strip()))
    def handle_endtag(self, tag):
        if tag==self.capture: self.capture=None
checked=[]
for p in ROOT.rglob('*.html'):
    rel=str(p.relative_to(ROOT))
    before=subprocess.run(['git','show',f'{BASE}:{rel}'],cwd=ROOT,capture_output=True,text=True)
    if before.returncode: continue
    old,new=Page(before.stdout),Page(p.read_text())
    assert old.metadata==new.metadata, f'Metadados alterados: {rel}'
    assert set(old.links)<=set(new.links), f'Links removidos: {rel}'
    checked.append(rel)
for rel in ['sitemap.xml','robots.txt','resultados/index.html','sw.js','CNAME','404.html','espirometria.html','servicos.html','conheca.html']:
    original=subprocess.check_output(['git','show',f'{BASE}:{rel}'],cwd=ROOT)
    assert original==(ROOT/rel).read_bytes(), rel
print(f'PASS: {len(checked)} páginas, metadados/JSON-LD e links preservados; sitemap, robots, portal, redirects, SW e CNAME intactos.')
