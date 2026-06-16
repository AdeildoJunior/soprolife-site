# Painel SoproLife

Dashboard operacional da SoproLife.

## Fase atual

Versão 0.1 — protótipo visual estático com dados fictícios/anônimos.

## Objetivo

Acompanhar:
- visão geral da operação;
- CRM de clínicas;
- leads e agendamentos;
- marketing e SEO;
- futuras integrações com planilhas, Search Console, Instagram, WhatsApp e CRM.

## Como rodar localmente

A partir da raiz do repositório, suba um servidor estático:

```bash
python3 -m http.server 8765
```

Depois acesse:

```text
http://127.0.0.1:8765/painel-soprolife/
```

> Evite abrir o `index.html` diretamente via `file://`, porque o painel carrega os JSONs com `fetch()`.

## Testes rápidos

Valide a sintaxe do JavaScript:

```bash
node --check painel-soprolife/js/app.js
```

Valide todos os JSONs do painel:

```bash
for f in painel-soprolife/data/*.json; do python3 -m json.tool "$f" >/dev/null || exit 1; echo "OK $f"; done
```

Verifique se a rota do painel responde:

```bash
curl -I http://127.0.0.1:8765/painel-soprolife/
```

## Segurança

Não inserir dados reais de pacientes nesta fase.
Não armazenar CPF, telefone real de paciente, pedido médico ou dados clínicos identificáveis nos arquivos do painel.
