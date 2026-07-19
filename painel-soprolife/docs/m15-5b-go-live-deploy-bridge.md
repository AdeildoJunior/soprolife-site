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
4. Probe HTTPS pré-deploy no endereço privado: painel responde HTTP 200 e o
   health M15 de mesma origem responde HTTP 200 com JSON `status` exatamente
   `"ok"`.

Após o deploy (além de TODOS os checks existentes de backup, ancestralidade,
migração, serviços, listeners, health direto/proxy, retry fail-closed e
rollback, que permanecem intactos):

5. Probe HTTPS pós-deploy: painel 200; health 200 `status="ok"`;
   `m15-config.json` servido com `enabled=true` e `api_base` inalterado;
   `m15-security.js` servido com HTTP 200; ordem correta dos scripts no
   `index.html` publicado.

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
- Ambos rodam no quality gate seguro (seção 8d), 100% offline.
