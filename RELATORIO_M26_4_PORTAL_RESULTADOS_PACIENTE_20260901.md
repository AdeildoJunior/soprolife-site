# M26.4 — Portal de Resultados do Paciente + Entrega Automatizada

> Relatório vivo. Atualizado durante a execução da missão.
> Worktree: `/home/fedorasurf/soprolife-worktrees/claude-m26-4-portal-resultados`
> Branch: `claude-m26-4-portal-resultados-paciente`
> Base: `origin/painel-soprolife-v01` = `5e1345c`

---

## 1. Auditoria da arquitetura (feita ANTES de escrever código)

### 1.1 Como `soprolife.com.br` está hospedado hoje

```
$ dig +short soprolife.com.br
185.199.111.153   185.199.109.153   185.199.108.153   185.199.110.153
$ dig +short www.soprolife.com.br CNAME
adeildojunior.github.io.
$ dig +short soprolife.com.br NS
f.sec.dns.br.   e.sec.dns.br.
```

**Site institucional = GitHub Pages** (os quatro IPs `185.199.108-111.153` são
os do Pages), servindo a raiz deste próprio repositório (há `CNAME` com
`soprolife.com.br` na raiz). **DNS autoritativo no Registro.br** — painel
externo, sem credencial de automação disponível nesta sessão.

Consequência de projeto: **o site NÃO migra**. A página
`https://soprolife.com.br/resultados` nasce como arquivo estático neste mesmo
repositório (`resultados/index.html`) e sobe pelo mesmo Pages. Ela não tem
backend próprio — fala com uma API pública mínima, separada, em subdomínio
técnico.

### 1.2 Onde vive o Command Center hoje

- VPS Ubuntu 24.04 (`soprolife-painel-01`), **acesso exclusivamente por
  Tailscale**. Nenhum serviço do painel escuta fora de loopback.
- `soprolife-m15-api.service` → `127.0.0.1:8015`, health em `/api/v1/health`.
- `soprolife-painel-loopback.service` → `:8765`, servidor estático do painel.
- `app/config.py` recusa, em `M15_ENV=prod`, qualquer bind fora de loopback
  (`_regras_de_prod`). Isso é uma trava real e **não foi afrouxada**.

### 1.3 O gatilho clínico que já existe (M25.29H)

`app/routers/reports.py`:

- `POST /laudos/assinatura-externa/enviar` — a médica devolve o PDF assinado.
  As guardas documentais (`avaliar_guardas_documentais`) rodam **sobre os
  bytes**, antes de o arquivo virar versão. Aprovado ⇒ nasce
  `ExternalSignedDocument(status="recebido_assinado")` (`ASSINADO_ACEITO`).
  Recusado ⇒ `recusado`, nada é criado.
- `POST /laudos/assinatura-externa/confirmar` — caminho de compatibilidade
  para lotes abertos antes da M25.29H; reaplica as mesmas guardas.

**Estes são os dois — e únicos — pontos onde `recebido_assinado` nasce.** São
exatamente os dois pontos onde o acesso do paciente passa a ser criado.

### 1.4 Onde estão os dois documentos que o paciente recebe

| Documento | Origem exata |
|---|---|
| Laudo assinado final | `ExternalSignedDocument.report_document_version_id` |
| Exame técnico (MIR) | versão `kind == "original"` do MESMO `ReportDocument` |

`source_version_id` (o PDF final que a médica levou para assinar) e
`current_version_id` **não** são usados para entrega ao paciente.

### 1.5 Integração WhatsApp existente

```
$ grep -rn "wa.me|graph.facebook|Cloud API" app/
app/services/followup.py:226:    return f"https://wa.me/{phone}?text={quote(message)}"
```

**Não existe integração oficial Meta WhatsApp Business / Cloud API** no
projeto nem nas configurações. Só o construtor de URL `wa.me` para revisão
humana, usado por CRM e follow-up, com a regra explícita no README:
"monta URL para revisão humana; **nunca** dispara envio automático".

⇒ Conforme a missão: **V1 manual**. Nada de bot, WhatsApp Web automatizado,
Selenium ou serviço não oficial. A arquitetura fica preparada para a Cloud
API entrar depois sem redesenhar o portal (ver §3.6).

---

*(seções 2 em diante preenchidas ao longo da execução)*
