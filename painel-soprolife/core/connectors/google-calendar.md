# Conector Google Calendar — Painel SoproLife

Status: não implementado. Este arquivo é documentação de contrato.

## Objetivo

Contar eventos por tipo (consulta, reunião, visita a clínica, exame) e exportar somente contagens seguras para o painel, sem expor nomes de pacientes ou dados clínicos.

## Dados permitidos exportar para o painel

- contagem de eventos por tipo por semana;
- próximo evento por tipo (data e hora apenas, sem nome de participante);
- contagem de eventos por status (confirmado, pendente, cancelado).

## Dados proibidos de exportar

- nome do paciente no título ou descrição do evento;
- e-mail de participante;
- localização que identifique o paciente;
- conteúdo da descrição do evento.

## Configuração esperada

Configuração real: manter somente em arquivo local privado fora do repositório.

Salvar em: `~/.config/soprolife/painel/google-calendar.local.json`
