# M25.29F — O proxy trocava PDF bom por 502

**Data:** 18/08/2026 · **Branch:** `claude-m25-29f-hotfix-proxy-502`
**Commit:** `778635b` · **Tipo:** hotfix operacional (operação parada)

---

## 1. O incidente

A médica não conseguia trabalhar e o sócio não conseguia baixar exame nem
laudo. No navegador aparecia:

> **Resposta inválida da API. (http_502)**

Nenhum download começava. Ao mesmo tempo, a API respondia perfeitamente:

```
GET http://127.0.0.1:8015/api/v1/health  →  200  {"status":"ok","banco":"ok"}
```

Essa contradição — API viva, navegador quebrado — é o que localiza a falha:
ela está **entre** os dois.

---

## 2. Causa raiz

O proxy do painel (`painel-soprolife/scripts/command-center-local-server.py`)
não repassa qualquer conteúdo que venha do upstream. Ele exige que a rota
esteja numa allowlist antes de deixar passar binário — e essa intenção está
certa: é o que impede o proxy de virar um túnel para qualquer coisa.

O defeito estava no **conteúdo da lista**. Ela conhecia uma rota só:

```python
_M15_REPORT_CONTENT_RE = re.compile(
    r"^/laudos/<uuid>/versoes/<uuid>/conteudo$"
)
```

Ficavam de fora **quatro** rotas que também devolvem binário:

| rota | tipo | reconhecida antes? |
| --- | --- | --- |
| `GET /laudos/<uuid>/versoes/<uuid>/conteudo` | PDF | ✅ |
| `GET /laudos/<uuid>/exame-tecnico/conteudo` | PDF | ❌ |
| `GET /laudos/<uuid>/assinado/conteudo` | PDF | ❌ |
| `POST /laudos/assinatura-externa/baixar` | PDF ou ZIP | ❌ |
| `POST /laudos/lote/baixar` | ZIP | ❌ |

Sem casar, o fluxo caía no ramo seguinte, que exige `application/json`. Um
`application/pdf` legítimo não é JSON — e o proxy **substituía a resposta boa
da API por 502**:

```python
if not is_pdf:
    if not content_type.lower().startswith("application/json"):
        self._m15_error(502, "Resposta inválida da API.")
```

O gate também exigia `method == "GET"`, então os dois downloads em lote
falhavam por dois motivos ao mesmo tempo, e ZIP nem era um tipo previsto.

### Um bug, três sintomas

Este defeito explica de uma vez os três problemas relatados em dias
diferentes:

1. **`conteúdo 5.jsold`** (M25.29E) — a âncora `<a download>` salvava o corpo
   deste 502 como arquivo. O nome vinha do último segmento da URL,
   `/conteudo`, e a extensão era adivinhada pelo tipo recebido.
2. **`http_502` na tela** — apareceu quando a M25.29E trocou "baixar o erro"
   por "mostrar o erro". A M25.29E **não causou** o 502; ela o tornou visível.
3. **Lotes de auditoria repetidos** — cada tentativa frustrada abria um `BAT`
   novo, porque o lote é registrado pela API antes de o proxy descartar a
   resposta. Daí `BAT-000013` e `BAT-000014` nascerem com 0,8s de diferença.

---

## 3. A correção

Cada rota declara os tipos que pode legitimamente devolver:

```python
def _tipos_binarios_esperados(method: str, suffix: str) -> frozenset:
    if method == "GET":
        if _M15_REPORT_CONTENT_RE.fullmatch(suffix):   return _M15_TIPOS_PDF
        if _M15_REPORT_DOWNLOAD_RE.fullmatch(suffix):  return _M15_TIPOS_PDF
    elif method == "POST" and suffix in _M15_LOTE_DOWNLOAD_PATHS:
        return _M15_TIPOS_PDF_OU_ZIP
    return frozenset()
```

Devolver vazio significa "esta rota só fala JSON". **O gate continua
fechado** — ele não passou a aceitar binário em qualquer lugar; passou a
conhecer as rotas que sempre precisaram dele.

Nenhuma mudança na API, no banco, em laudo, exame, paciente, financeiro ou
Pastore. Uma migration: nenhuma.

---

## 4. Provas

Os testes sobem o **proxy de verdade** contra um upstream falso e conferem o
que chega do outro lado — que é o caminho do navegador.

| prova | resultado |
| --- | --- |
| Teste reprova SEM a correção | ✅ `assert 502 == 200`, corpo `{"ok": false, "error": "Resposta inválida da API."}` — a frase exata da tela |
| `exame-tecnico/conteudo` pelo proxy | ✅ `200`, `application/pdf`, `%PDF`, `filename .pdf`, `nosniff`, `no-store` |
| `assinado/conteudo` pelo proxy | ✅ idem |
| Lote em PDF | ✅ atravessa |
| Lote em ZIP | ✅ atravessa |
| Rota por versão (que já funcionava) | ✅ intacta |
| JSON comum | ✅ inalterado |
| Gate ainda recusa | ✅ `text/html` numa rota de download; `application/zip` fora das rotas de lote |
| Suíte na VPS, **arquivo implantado** | ✅ 15/15 |
| Regressão relacionada (M25.18 + M25.29E) | ✅ 73 passed |

Suíte completa não foi executada: a operação estava parada, e a orientação
foi hotfix focado.

---

## 5. Deploy

```
local        778635b  →  push da branch M25.29F
integração   2a47799..778635b  ff-only em painel-soprolife-v01 (sem force)
VPS          2a47799  →  778635b  (git merge --ff-only, árvore limpa)
restart      soprolife-painel-loopback + soprolife-painel  (root)
```

Só o proxy foi reiniciado. A API **não** foi tocada.

### Como se prova que um restart aconteceu

O `systemctl` pode responder sem erro e o processo continuar o mesmo. O que
prova é a data de início do processo, porque o Python lê o fonte uma vez, no
start:

```
antes:  pid 4025883  Thu Aug 13 13:36:48 2026   (5 dias no ar, código antigo)
depois: pid 4169146  Tue Aug 18 23:33:06 2026   (código corrigido)
```

Foi assim que se detectou que a primeira tentativa de restart não havia
surtido efeito — o arquivo em disco já estava corrigido (`23:16:34`) e os
processos ainda eram de 13/08.

---

## 6. Verificação pelo navegador real

O critério da missão era explícito: não basta `curl` na API. Após o restart,
o sócio clicou em **"Baixar laudo assinado"** na fila administrativa e **o
download aconteceu**, pelo mesmo caminho do navegador.

Health `HTTP 200`, `banco: ok`, `ambiente: prod`. Serviços API, Painel e
Loopback `active`. Gate M25.23 devolvendo `401` em `/laudos` e
`/laudos/entrega` através do proxy.

> **Observação registrada, não escondida:** eu não tenho permissão para ler o
> journal dessas units como usuário `soprolife`. A contagem de "0 erros 502"
> que apareceu numa verificação intermediária era **falta de acesso**, não
> ausência de erro, e por isso não foi usada como evidência.

---

## 7. O `404` em `https://hostname/api/v1/health` não é bug

O caminho externo do painel é `/painel-soprolife/api/m15/...`, que o proxy
traduz para `127.0.0.1:8015/api/v1/...`. `/api/v1/` **não existe** do lado de
fora, e é correto que não exista: a API nunca esteve publicada diretamente.

---

## 8. O que este hotfix NÃO resolve

O download passou a funcionar — e foi justamente por isso que se descobriu
que o PDF assinado do **LAU-000014 é uma prévia assinada**. Esse é um
problema de **dado**, não de transporte, e é tratado na **M25.29G**.

Nenhuma PII e nenhum segredo constam deste relatório.

---

**M25.29F — PROXY ESTÁVEL E DOWNLOADS DE LAUDO/EXAME FUNCIONANDO SEM HTTP 502**
