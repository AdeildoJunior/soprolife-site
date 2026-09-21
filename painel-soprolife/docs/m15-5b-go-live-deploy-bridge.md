# M15.5B — Ponte de go-live controlado no deploy produtivo

O script oficial `painel-soprolife/nucleo-m15/scripts/deploy-producao-vps.sh`
recusava historicamente qualquer release com `enabled=true` no
`data/m15-config.json`. Esta ponte mantém essa recusa COMO PADRÃO e abre um
único caminho explícito, validado e fail-closed para implantar um release de
go-live (ex.: o M15.5A integrado). No branch isolado da ponte (M15.5B) o
release permanecia `enabled=false`; no release integrado M15.5C (ponte +
go-live M15.5A) o `data/m15-config.json` tem `enabled=true`, portanto o
deploy desse release SÓ é aceito com as variáveis de go-live abaixo — sem
elas, aborta fail-closed antes de qualquer mutação.

Nenhum hostname real de tailnet aparece neste documento ou no código — use
sempre o endereço comunicado por canal interno. Exemplos usam o placeholder
`painel-privado.exemplo.ts.net`.

## Interface exata do modo go-live

Um deploy de release com `enabled=true` só é aceito com as DUAS variáveis de
ambiente presentes:

```bash
SOPROLIFE_M15_GO_LIVE=YES
SOPROLIFE_M15_HTTPS_BASE_URL=https://painel-privado.exemplo.ts.net/
```

- A autorização deve ser exatamente `YES` (maiúsculo). São rejeitados:
  `yes`, `true`, `1`, `on`, valor vazio e variável ausente.
- A URL base deve ser HTTPS na raiz do site: esquema exatamente `https`,
  hostname válido, sem usuário/senha embutidos, sem querystring, sem
  fragmento, sem path além de `/`.
- Com `enabled=false` nada muda: o fluxo atual continua e nenhuma variável é
  exigida.

## O que o modo go-live valida (tudo fail-closed)

Antes de QUALQUER mutação produtiva:

1. Autorização explícita (as duas variáveis, `YES` exato).
2. Forma da URL base (regras acima).
3. Checagens estáticas do release alvo (checkout a implantar):
   - `painel-soprolife/js/m15-security.js` presente, com os marcadores de
     bloqueio de contexto inseguro (HTTP remoto bloqueado);
   - `m15-security.js` carregado ANTES de `m15-nucleo.js` no `index.html`;
   - `api_base` de mesma origem inalterado (`/painel-soprolife/api/m15`);
   - testes globais de segurança do go-live presentes
     (`painel-soprolife/scripts/test-m15-go-live.js`);
   - nenhuma persistência de token (`.setItem`) nos módulos M15;
   - nenhuma dependência externa de autenticação (sem script externo no
     `index.html`, sem URL absoluta nos módulos M15).
4. Probe HTTPS pré-deploy ANÔNIMO no endereço privado (M26.21): o painel
   responde HTTP 200 com a TELA DE LOGIN e sem nenhuma marcação do Command
   Center (`m15-nucleo.js`, `id="laudos-espirometria"`, `report-workflow.js`);
   o health M15 de mesma origem responde HTTP 200 com JSON `status` exatamente
   `"ok"`; e `data/m15-config.json` responde HTTP **401** — prova negativa de
   que o manifesto de boot continua protegido.

Após o deploy (além de TODOS os checks existentes de backup, ancestralidade,
migração, serviços, listeners, health direto/proxy, retry fail-closed e
rollback, que permanecem intactos):

5. Probe HTTPS pós-deploy (M26.21): tudo do item 4, mais
   - o release IMPLANTADO reaprovado nas checagens estáticas do item 3 (fonte
     local versionada do próprio host — os artefatos administrativos nunca
     saem por HTTPS);
   - o serviço em execução corresponde ao release: o health informa a MESMA
     versão declarada em `nucleo-m15/app/__init__.py`, com `ambiente="prod"` e
     `banco="ok"`;
   - os artefatos PÚBLICOS servidos são byte a byte iguais aos do release:
     `login.html` (o que o painel entrega sem sessão) e `m15-security.js`.

### Por que o postflight não lê mais `m15-config.json` por HTTPS (M26.21)

Até a M25.22 o painel era servido sem autenticação, e estes gates nasceram
nesse mundo: faziam GET anônimo do `index.html` administrativo e do
`m15-config.json` e exigiam HTTP 200 nos dois. A M25.23 fechou esse vazamento
(`painel-soprolife/scripts/panel_access_gate.py`): hoje o anônimo recebe
`login.html` no painel e 401 no manifesto. Os gates ficaram incompatíveis com
o próprio produto e o deploy do commit `f9c0761` abortou fail-closed em
`reports_https_workspace_markup_missing`.

Reabrir esses artefatos, ou dar uma credencial administrativa ao deploy, está
fora de questão. A prova foi partida em duas metades, e o conjunto é MAIS
estrito que o anterior:

| O que provar | Como, agora |
| --- | --- |
| tela de login servida sem sessão | GET anônimo de `/painel-soprolife/` |
| Command Center não vaza | ausência dos marcadores administrativos no mesmo GET |
| manifesto protegido | GET anônimo de `data/m15-config.json` → 401 |
| API viva e saudável | health anônimo 200 `status="ok"` |
| release correto | `check-source` no checkout implantado |
| serviço == release | `versao` do health == `__version__` do checkout |
| bytes servidos == release | `login.html` e `m15-security.js` byte a byte |

A CLI passou a refletir isso: `check-https-pos` recebe DOIS argumentos
(`<base-url> <repo-root>`), e chamá-la sem o repo root é erro de uso — nunca
validação parcial.

Garantias de rede do gate (`go_live_https_gate.py`): verificação de
certificado TLS sempre ativa (recusa executar se estiver desligada), opener
sem handler de HTTP puro, redirects aceitos somente para HTTPS no mesmo
hostname (sem downgrade), timeout de conexão e prazo total finitos, somente
stdlib (nenhuma flag insegura). A ponte NÃO configura Tailscale Serve,
Funnel, certificados, firewall ou ACLs — isso permanece operação humana.

## Release em dois estágios

Nota M15.5C: o branch integrado (`fable-m15-5c-integrated-go-live`) contém a
ponte E o go-live no mesmo release, com `enabled=true` — seu deploy é
diretamente o Estágio 2. O Estágio 1 descreve o deploy de qualquer release
com `enabled=false` (fluxo histórico e rollback), que segue sem exigir
variável nenhuma.

### Estágio 1 — implantar a ponte (enabled=false)

1. Integrar este branch (`fable-m15-5b-go-live-deploy-bridge`) à branch de
   produção pelo fluxo padrão de revisão.
2. Deploy normal na VPS, SEM variáveis de go-live:

   ```bash
   cd /opt/soprolife/soprolife-site
   bash painel-soprolife/nucleo-m15/scripts/deploy-producao-vps.sh \
     <commit-40-hex-da-ponte> <branch-producao> [ip-tailscale]
   ```

3. Comportamento idêntico ao atual: flag `false`, mesmas confirmações,
   mesmos backups e checks. A saída final confirma
   "feature flag permanece false".

### Estágio 2 — go-live controlado (release enabled=true)

1. Validar o HTTPS privado (operação humana, fora desta ponte): o endereço
   `https://painel-privado.exemplo.ts.net/` deve servir o painel com
   certificado válido.
2. Integrar o M15.5A (`fable-m15-5a-go-live`) à branch de produção — o
   release resultante tem `enabled=true` e passa nas checagens estáticas.
3. Na VPS, executar o deploy com as DUAS variáveis exatas:

   ```bash
   cd /opt/soprolife/soprolife-site
   SOPROLIFE_M15_GO_LIVE=YES \
   SOPROLIFE_M15_HTTPS_BASE_URL=https://painel-privado.exemplo.ts.net/ \
   bash painel-soprolife/nucleo-m15/scripts/deploy-producao-vps.sh \
     <commit-40-hex-do-go-live> <branch-producao> [ip-tailscale]
   ```

   Qualquer validação reprovada aborta ANTES da primeira mutação; falha
   pós-deploy aborta com backup preservado, como hoje.
4. Smoke test seguro pelo endereço HTTPS privado, sem dado real, seguindo
   `painel-soprolife/docs/m15-5a-go-live-controlado.md` (selo "Acesso seguro
   (HTTPS)", login, registro sintético, auditoria).
5. Rollback: release VERSIONADO com `enabled=false` (1 linha no
   `data/m15-config.json`, commit + publicação) e, se necessário, o rollback
   completo de `painel-soprolife/docs/m15-2-proxy-seguro-deploy-vps.md`.
   Um release com `enabled=false` volta a dispensar as variáveis de go-live.

## Testes da ponte

- `bash painel-soprolife/nucleo-m15/scripts/test-deploy-go-live.sh` —
  matriz de autorização, leitura fail-closed da flag, formas de URL, fiação
  do deploy e flag da ponte em `false`.
- `python3 painel-soprolife/nucleo-m15/scripts/test_go_live_https_gate.py` —
  URL, certificado inviolável, timeouts finitos, redirect sem downgrade,
  probes pré/pós com rede mockada e checagens estáticas do release alvo.
- `python3 painel-soprolife/nucleo-m15/scripts/test_m26_21_go_live_auth_compat.py`
  — prova, no MESMO teste, que o servidor continua fechado para quem não tem
  sessão e que os gates provam o release sem depender disso. É o teste que
  impede os dois lados de divergirem de novo em silêncio.
- Os três rodam no quality gate seguro (seção 8d), 100% offline.
