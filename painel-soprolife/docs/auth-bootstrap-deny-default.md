# Bootstrap de apresentação com negação por padrão

O servidor mantém a autoridade RBAC. Esta mudança impede a exposição transitória de menus, contextos e ações enquanto a identidade ainda não foi confirmada.

O HTML operacional fica em `template`, fora da árvore ativa. `boot-gate.js` consulta uma vez `/auth/me`, `/auth/sessao` e o manifesto versionado. Exige identidade e papéis coerentes entre as respostas, sessão/CSRF válidos, configuração habilitada e origem HTTPS ou loopback. Estados: `loading`, `ready`, `denied`. Falha mantém apenas a casca neutra com recuperação.

Antes de anexar o fragmento, o gate reutiliza a projeção de marcas de `LifeCommandCenter` e remove as seções fora do papel. A conta exclusivamente médica conserva apenas Laudos de espirometria; uma conta exclusivamente EEG não recebe a casca SoproLife. Os módulos são executados em ordem, com a casca autorizada ainda inerte/oculta. Núcleo e Laudos reutilizam sessão e manifesto resolvidos pelo bootstrap. Laudos inicia também a primeira carga clínica quando recebe uma sessão já restaurada; não depende de um evento anterior à inscrição do módulo. O painel é publicado inteiro somente após preparar os módulos, a navegação e as folhas de estilo. Leituras de conteúdo operacional podem continuar independentemente das permissões já resolvidas.

`SoproM15.can()` delega ao gate compartilhado e nega sem sessão/identidade. Central também nega na ausência do cliente. Logout/401 retiram imediatamente a casca protegida, antes da revogação/reentrada; troca de identidade exige novo bootstrap. O estado anterior não é restaurado visualmente por bfcache. O token avançado continua apenas em memória, limitado à mesma identidade e aos mesmos papéis já autorizados.

Não há mudança de permissões do backend, regras fiscais/econômicas, dados históricos, integrações ou site público.

## Validação

- `node painel-soprolife/scripts/test-auth-bootstrap.js`: HTML inerte e falha de bootstrap sem anexar conteúdo; identidade isolada não concede acesso durante loading.
- `M15_TEST_PYTHON=<venv>/bin/python PLAYWRIGHT_MODULE=<playwright> node painel-soprolife/scripts/test-auth-bootstrap-browser.cjs`: API/proxy reais e banco sintético descartável; Chromium e Firefox, 1440/800/390. MutationObserver anterior ao parsing e amostragem por frame verificam ausência de nós protegidos em loading, ausência de nós administrativos para médica e conjunto único de navegação após publicação. Login, refresh sem cache, URL direta, logout/login entre perfis, contexto salvo indevido, papéis EEG, respostas deliberadamente atrasadas, 401/403, indisponibilidade e identidade incoerente. `AUTH_BOOTSTRAP_SCREENSHOT_DIR` salva capturas sintéticas opcionais.
- Testes de sessão/RBAC/EEG com PostgreSQL 16 descartável, regressão de espirometria nos dois navegadores, quality gate e check-access.

## Publicação

Reutilizar `nucleo-m15/scripts/deploy-partner-shares-vps.sh <SHA-revisado> <HEAD-produtivo-conferido> <schema-conferido> frontend`, com os requisitos existentes de root/TTY e gates verificados. O modo frontend aceita somente arquivos de apresentação, testes/runbooks do painel e a própria extensão do script. Conserva backup PostgreSQL verificável, bundle Git, preflight, integração fast-forward, alembic check, fingerprints de todas as tabelas, smoke HTTP autenticado, check-access e health API/proxy/portal. Não aplica migrações nem reinicia API/proxy.

Depois, validar contas administrativas e médica existentes por sessões temporárias revogáveis, com bloqueio de métodos de escrita no navegador, sem cadastrar pacientes nem emitir NFS-e. Comparar as tabelas antes/depois, permitindo somente as sessões temporárias e sua auditoria de autenticação.
