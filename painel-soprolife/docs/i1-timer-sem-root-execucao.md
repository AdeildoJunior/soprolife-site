# I1 — Timer sem root — Roteiro de EXECUÇÃO (janela na VPS)

> **Para a janela real, usar `i1-execucao-assistida-f1-f5.md`** (roteiro
> operacional com paradas GPT por fase) após o checklist
> `i1-go-no-go-checklist.md`. Este arquivo permanece como visão geral.
>
> Complementa `i1-timer-sem-root-planejamento.md`. Nada aqui foi executado.
> **TODOS os comandos abaixo são sugestões — NÃO EXECUTAR sem revisão GPT**
> e sem o usuário presente na janela. Placeholders sempre (`<VPS_TAILSCALE>`).
> Templates dos units: `painel-soprolife/systemd/*.example`.

## Visão geral das fases

| Fase | O quê | Executor | Reversível? |
|---|---|---|---|
| F0 | Pré-checks read-only | IA (permitido) | n/a |
| F1 | Usuário + diretórios | usuário/root na janela | sim (nada ativo usa) |
| F2 | Estratégia ADC + configs + venv | usuário na janela | sim |
| F3 | Teste manual como soprolife (2x) | usuário na janela | n/a (só leitura de estado) |
| F4 | Instalar units + 1 start manual | usuário na janela | **rollback abaixo** |
| F5 | Ativação do timer + monitoramento | usuário | rollback abaixo |

## F0 — Pré-checks (read-only, sem aprovação extra)

```
systemctl cat soprolife-update-data.service soprolife-update-data.timer
systemctl cat soprolife-painel.service          # confirmar o User= atual
systemctl list-timers --all --no-pager | grep -i sopro
id soprolife || echo "usuario nao existe — criar na F1 (fase aprovada)"
# grupos administrativos são checados via 'id' (o precheck faz isso sem sudo)
ls -la /var/lib/soprolife/ /var/lib/soprolife/.ssh/ 2>/dev/null   # chaves inesperadas?
ls -la /opt/soprolife/soprolife-site/painel-soprolife/data-private/
ls -la /opt/soprolife/soprolife-site/painel-soprolife/data/*.local.json
ls -la /root/.config/soprolife/painel/ /root/.config/gcloud/ 2>/dev/null  # só nomes
git -C /opt/soprolife/soprolife-site status --short && git -C /opt/soprolife/soprolife-site log --oneline -3
```

Critério para prosseguir: confirmado se o usuário `soprolife` existe (e,
existindo, sem sudo e sem chave estranha); repo limpo e no commit esperado;
localização de ADC/configs/venv confirmada.

## F1–F5 — roteiro operacional movido

> Os comandos fase a fase (com objetivo, checks, rollback por fase e
> paradas GPT) vivem SOMENTE em **`i1-execucao-assistida-f1-f5.md`** —
> fonte única, para não haver dois conjuntos de comandos divergentes.
> Resumo do desenho decidido:

- **F1** — usuário de SISTEMA `soprolife` (home `/var/lib/soprolife`,
  shell `/usr/sbin/nologin`), criado SOMENTE se `id soprolife` falhar;
  diretórios de config 700.
- **F2** — permissões mínimas SEM quebrar o painel: primeiro identificar
  o usuário real do `soprolife-painel.service`; grupo compartilhado
  750/640 se o painel tiver usuário próprio; `chmod 700 data-private`
  só se o painel rodar como root ou como o mesmo usuário do timer.
  Posse do repo: `safe.directory` (recomendado); `chown -R` no repo
  inteiro é NÃO RECOMENDADO e exige aprovação explícita do GPT.
- **F3** — configs copiadas UMA A UMA (`install -m 600`, só arquivos
  aprovados pelo GPT — sem `cp -a` cego); venv recriado; ADC próprio via
  device flow com `sudo -u soprolife HOME=/var/lib/soprolife` (sem shell
  de login); ADC do root NUNCA copiado sem aprovação explícita.
- **F4** — units instalados a partir dos `.example` com backup datado
  defensivo + `daemon-reload`, sem habilitar timer.
- **F5** — DUAS execuções manuais (`systemctl start`) verdes + parada
  GPT obrigatória → só então `systemctl enable --now` do timer;
  monitorar 2 ciclos + 24h. F6 (painel.service) é etapa separada.

## Rollback (escrever aberto ao lado durante a F4/F5)

```
cp /etc/systemd/system/soprolife-update-data.service.bak.<TIMESTAMP> \
   /etc/systemd/system/soprolife-update-data.service
cp /etc/systemd/system/soprolife-update-data.timer.bak.<TIMESTAMP> \
   /etc/systemd/system/soprolife-update-data.timer
systemctl daemon-reload
systemctl restart soprolife-update-data.timer
journalctl -u soprolife-update-data.service -n 40 --no-pager   # 0/SUCCESS como root
```

Não apagar nada do ambiente soprolife preparado (fica para a próxima
tentativa). Se os `chown` da F1 atrapalharem o unit antigo (root lê tudo —
não deveriam), reverter posse só de `data-private/` para root é o único
ajuste eventualmente necessário. Registrar a causa antes de tentar de novo.

## Pontos que exigem confirmação do GPT antes da execução

1. Posse do repo: `chown` vs `safe.directory` (F1) — impacta o deploy.
2. Linha exata de escopos do ADC + quota project (F2).
3. Resultado do `grep -rn "/root/"` nos scripts (F2) — precisa vir vazio.
4. `id soprolife` sem grupo administrativo e `~/.ssh` sem surpresas (F0).
5. Janela única (F1→F5) ou F1–F3 num dia e F4–F5 no seguinte.
6. `ProtectSystem=full` agora, `strict` só no endurecimento pós-estável.
