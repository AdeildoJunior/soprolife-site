#!/usr/bin/env python3
"""
SoproLife — M25.27: a sessão exclusivamente clínica consegue BOOTAR o painel.

Defeito corrigido aqui
----------------------
A M25.23 passou a exigir papel administrativo para tudo sob
`painel-soprolife/data/`. `data/m15-config.json` caiu junto — mas ele não é
dado operacional: é o manifesto de BOOT que diz às telas que elas podem se
montar. Resultado medido em produção com a conta da médica:

- `report-workflow.js` lia o manifesto, tomava 403, caía num `return` mudo e a
  bancada ficava para sempre em "Carregando o fluxo seguro de laudos…";
- `m15-nucleo.js` fazia o mesmo e nunca chegava a revelar o "Sair" do
  cabeçalho — que foi exatamente o que o screenshot de produção mostrou.

Este teste sobe o servidor REAL (o mesmo `command-center-local-server.py` que
roda na VPS) e mede a resposta HTTP por papel. A identidade é fabricada: não há
banco, não há sessão real, não há dado de paciente.

O ponto mais importante do arquivo não é provar que o manifesto passou a
responder 200 — é provar que NADA MAIS passou junto. A isenção precisa valer
para um arquivo, jamais para o diretório.

Uso:
    python3 painel-soprolife/scripts/test-m25-27-area-medica.py
Exit: 0 = contrato preservado | 1 = regressão.
"""

import http.client
import importlib.util
import os
import sys
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
SERVIDOR = RAIZ / "painel-soprolife" / "scripts" / "command-center-local-server.py"

FALHAS = 0


def caso(rotulo: str, ok: bool, detalhe: str = "") -> None:
    global FALHAS
    if not ok:
        FALHAS += 1
    print(f"  {'PASS' if ok else 'FALHA'}: {rotulo}")
    if not ok and detalhe:
        print(f"        {detalhe}")


spec = importlib.util.spec_from_file_location("ccls", SERVIDOR)
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

# Identidades sintéticas escolhidas pelo cookie. Substituir `_session_identity`
# isola EXCLUSIVAMENTE a decisão de papel: nenhuma credencial, nenhum banco,
# nenhuma chamada ao Núcleo M15.
IDENTIDADES = {
    "papel=medico": {"id": "USR-MED-SINTETICA", "nome": "Médica Sintética",
                     "papeis_efetivos": ["medico"]},
    "papel=admin": {"id": "USR-ADM-SINTETICO", "nome": "Admin Sintético",
                    "papeis_efetivos": ["admin"]},
}


def _identidade(cookie_header):
    for marcador, identidade in IDENTIDADES.items():
        if cookie_header and marcador in cookie_header:
            return identidade
    return None


srv._session_identity = _identidade

os.chdir(RAIZ)
servidor = srv.http.server.ThreadingHTTPServer(("127.0.0.1", 0), srv._Handler)
PORTA = servidor.server_address[1]
threading.Thread(target=servidor.serve_forever, daemon=True).start()


def get(alvo: str, cookie: str | None) -> int:
    conexao = http.client.HTTPConnection("127.0.0.1", PORTA, timeout=10)
    try:
        conexao.request("GET", alvo, headers={"Cookie": cookie} if cookie else {})
        resposta = conexao.getresponse()
        resposta.read()
        return resposta.status
    finally:
        conexao.close()


MEDICA, ADMIN = "papel=medico", "papel=admin"

try:
    print("── O manifesto de boot chega às DUAS sessões ──")
    caso("médica lê data/m15-config.json (era 403 — a causa raiz)",
         get("/painel-soprolife/data/m15-config.json", MEDICA) == 200,
         f"status={get('/painel-soprolife/data/m15-config.json', MEDICA)}")
    caso("admin lê data/m15-config.json",
         get("/painel-soprolife/data/m15-config.json", ADMIN) == 200)

    print("── …mas continua exigindo sessão ──")
    caso("sem sessão, o manifesto responde 401",
         get("/painel-soprolife/data/m15-config.json", None) == 401,
         f"status={get('/painel-soprolife/data/m15-config.json', None)}")

    print("── A casca e os scripts da bancada chegam à médica ──")
    for alvo in (
        "/painel-soprolife/",
        "/painel-soprolife/index.html",
        "/painel-soprolife/js/boot-gate.js",
        "/painel-soprolife/js/m15-nucleo.js",
        "/painel-soprolife/js/report-workflow.js",
    ):
        caso(f"médica recebe 200 em {alvo}", get(alvo, MEDICA) == 200,
             f"status={get(alvo, MEDICA)}")

    print("── M25.23 INTACTA: dado operacional continua fechado à médica ──")
    # Se qualquer um destes virar 200 para a médica, a isenção do manifesto
    # vazou para o diretório e a M25.23 foi desfeita.
    for alvo in (
        "/painel-soprolife/data/resumo.json",
        "/painel-soprolife/data/leads.json",
        "/painel-soprolife/data/crm-clinicas.json",
        "/painel-soprolife/data/marketing.json",
        "/painel-soprolife/data/financeiro-summary.local.json",
    ):
        status = get(alvo, MEDICA)
        caso(f"médica NÃO lê {alvo} (403)", status == 403, f"status={status}")

    print("── Fonte privada e repositório seguem invisíveis para todos ──")
    for alvo in (
        "/painel-soprolife/data-private/leads.local.json",
        "/painel-soprolife/nucleo-m15/app/config.py",
        "/painel-soprolife/scripts/panel_access_gate.py",
        "/.git/config",
    ):
        for papel, cookie in (("médica", MEDICA), ("admin", ADMIN)):
            status = get(alvo, cookie)
            caso(f"{papel} recebe 404 em {alvo}", status == 404,
                 f"status={status}")

    print("── Sem sessão, o painel continua sendo só a porta de entrada ──")
    caso("sem sessão, /painel-soprolife/ devolve 200 (casca de login)",
         get("/painel-soprolife/", None) == 200)
    caso("sem sessão, o JS do painel responde 401",
         get("/painel-soprolife/js/report-workflow.js", None) == 401,
         f"status={get('/painel-soprolife/js/report-workflow.js', None)}")
finally:
    servidor.shutdown()

print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} regressão(ões) detectada(s).")
    sys.exit(1)
print("RESULTADO: área médica bootável e gate M25.23 preservado.")
sys.exit(0)
