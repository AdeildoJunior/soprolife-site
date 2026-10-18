# Painel SoproLife — base do Centro de Comando Life

O painel existente combina frontend HTML/CSS/JS, servidor autenticado/proxy e
núcleo M15 (FastAPI + PostgreSQL). Não é somente um protótipo estático.
A fundação multimarcas é documental; não cria operação EEG nem seletor.

Arquitetura futura e matriz de módulos:
[Centro de Comando Life](../docs/CENTRO_COMANDO_LIFE_ARCHITECTURE.md).
Estado operacional e invariantes: [contexto mestre](../docs/SOPROLIFE_MASTER_CONTEXT.md).

## Fonte de dados e módulos

PostgreSQL é a fonte operacional canônica desde M23. Sheets/Apps Script são
legado de migração/forense; não são fallback ou destino de escrita normal.
O painel reúne cadastros, CRM, leads/agendamentos, financeiro, parcerias,
laudos respiratórios, fiscal e indicadores. Marketing usa Search Console/GA4.
A existência de código de integração não comprova credenciais ou ativação.

Há componentes históricos/demonstrativos. A aba Tarefas ainda lê conteúdo
estático; Custos & Investimentos tem livro-caixa JSON privado e resumo gerado.
Não confundir esses estados com migração completa de todos os módulos ao banco.

## Desenvolvimento local seguro

Trabalhar em worktree exclusivo da branch do painel, baseado no HEAD oficial
após fetch. Não trocar o checkout principal nem copiar dados privados para
um worktree de desenvolvimento. Usar dados sintéticos.

O backend precisa de ambiente de desenvolvimento isolado, sem configuração de
produção; consultar o [README do M15](nucleo-m15/README.md). Não criar usuário,
seed ou schema em banco real para testar a interface.

Com a API local preparada, executar na raiz do worktree:

```bash
bash painel-soprolife/scripts/start-local.sh
```

Abrir `http://127.0.0.1:8765/painel-soprolife/`. O launcher força loopback e
chama `scripts/command-center-local-server.py`, preservando o portão de sessão
original. Sem API/sessão válida, o painel continua fechado. Se o servidor
oficial estiver ausente, a abertura é recusada; não existe fallback estático.
A API fica em `127.0.0.1:8015`, acessada pelo proxy `/painel-soprolife/api/m15`.

**Não usar `python3 -m http.server`, `file://` ou servidor estático genérico.**
Eles não executam autenticação, bloqueio de diretórios privados ou proxy M15.
`start-tailscale.sh` é legado desabilitado: só explica a recusa e retorna erro.
Para operação remota, usar o HTTPS privado já provisionado, com o servidor
oficial e Tailscale. Este guia não autoriza configurar rede ou fazer deploy.
Dados clínicos reais exigem HTTPS; HTTP loopback destina-se a desenvolvimento.

`data/m15-config.json` contém as flags versionadas atuais. Flags de interface
não substituem controles do backend e não devem ser ativadas incidentalmente.

## Verificações offline

```bash
node --check painel-soprolife/js/app.js
python3 -B painel-soprolife/scripts/test-safe-launchers.py
python3 -B painel-soprolife/scripts/test-panel-access-gate.py
python3 -B painel-soprolife/scripts/test_command_center_m15_proxy.py
bash painel-soprolife/scripts/quality-gate-safe.sh
```

O quality gate inclui testes sintéticos e pode gerar um resumo de saúde local
ignorado pelo Git: executá-lo em worktree isolado. Registrar falhas herdadas
sem anunciar que o gate passou. Nenhum teste substitui homologação de produção.

## Segurança

Não versionar dados pessoais/clínicos, credenciais, bancos, backups ou artefatos
fiscais. Manter arquivos privados e configurações fora da pasta servida sempre
que possível; `.gitignore` não é controle de acesso HTTP. Consulte
[SECURITY.md](SECURITY.md) e [CONFIG_LOCAL.md](CONFIG_LOCAL.md).
