# SoproLife Master Context

Use esta skill quando estiver trabalhando no SoproLife Command Center.

## Estado

Projeto em andamento, branch `painel-soprolife-v01`.

Arquitetura:
Painel estático → proxy Python local → Apps Script → Google Sheets → scripts ADC → summaries seguros → painel.

## Regras

- Não reinventar módulos existentes.
- Não commitar arquivos privados.
- Não expor dados de pacientes.
- Sempre mostrar diff antes de commit/deploy.
- Apps Script precisa ser publicado como Nova versão depois de colar código.
- Próximo foco: Auditoria M1 Etapa 2.

## Infra

VPS via Tailscale:
- IP: 100.87.98.100
- URL painel: http://100.87.98.100:8765/painel-soprolife/
- Serviço: soprolife-painel.service
- Repo VPS: /opt/soprolife/soprolife-site

## Google

Usamos:
- Google Sheets
- Apps Script
- Search Console
- GA4
- ADC na VPS

Se aparecer `Reauthentication is needed`, `ACCESS_TOKEN_SCOPE_INSUFFICIENT` ou `acesso negado`, reautenticar ADC com escopos explícitos para Sheets, Drive, Search Console e GA4.

## IAs

- GPT: arquitetura, coordenação, revisão e prompts.
- Fable: estratégia, produto, roadmap, riscos.
- Sonnet: implementação via Claude Code.
