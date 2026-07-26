#!/usr/bin/env python3
"""Painel SoproLife — contrato de segurança do resumo de auditoria (M23).

Fonte ÚNICA da verdade para o schema seguro de eventos de auditoria
exportados em painel-soprolife/data/auditoria-summary.local.json.

Usado por:
  * scripts/check-access.sh (validate_auditoria) — auditoria de segurança
    que roda antes de qualquer compartilhamento do painel;
  * nucleo-m15/app/snapshots.py (build_auditoria_summary) — gerador que
    exporta a trilha de auditoria do PostgreSQL;
  * nucleo-m15/tests/test_m23_postgres_only.py — regressão real de
    contrato (roda o exportador de verdade e valida com ESTAS mesmas
    regras, não uma cópia fixture-only do allowlist).

Este módulo existe porque o M23 quebrou exatamente aqui: o exportador
novo e o allowlist real de check-access.sh divergiram em silêncio, e os
testes automatizados validavam contra um schema fixture que não era o
schema real aceito em produção. Qualquer mudança neste contrato precisa
valer para os três consumidores ao mesmo tempo — por isso ele vive em um
único módulo, nunca duplicado.

Política de identificadores (2º incidente do M23, 25/07/2026): o resumo
público de auditoria NÃO exporta nenhum identificador de registro. Só
saem rótulos de domínio fechado — ação, tipo de entidade, papel do
operador, resultado — e o timestamp. Ver ALLOWED_EVENT_KEYS.
"""

from __future__ import annotations

import json
import re

#: Único conjunto de chaves permitido em cada evento de
#: ``ultimos_eventos``. Nenhum consumidor deve manter uma cópia própria
#: desta lista.
#:
#: ``entidade_id`` foi REMOVIDO da allowlist no segundo incidente do M23
#: (25/07/2026). Ele carregava o identificador bruto de linha do banco
#: (UUID de pessoa, lead, exame, lançamento...) para dentro de um arquivo
#: servido ao navegador. Duas razões independentes, ambas suficientes:
#:
#:  1. Segurança: um identificador de linha é referência direta a um
#:     registro que pode ser de paciente. O painel não precisa dele para
#:     nada, e o que não é exportado não vaza.
#:  2. Fato observado em produção: ~2,5% dos UUIDv4 casam com ``_FONE_RE``
#:     por coincidência de dígitos. Em
#:     "3f688837-5450-491e-b949-623b90cf145f" o trecho "688837-5450" tem a
#:     forma DD DDDD-DDDD de um telefone fixo. Isso derrubou a geração
#:     INTEIRA de snapshots, porque a escrita é all-or-nothing. Afrouxar o
#:     detector de telefone para acomodar o UUID seria enfraquecer a guarda
#:     de PII para exportar um dado que nunca deveria sair — a correção
#:     certa é não exportar.
#:
#: A contrapartida analítica está preservada em ``stats``: contagens por
#: ação, por tipo de entidade, por papel de operador e por resultado.
ALLOWED_EVENT_KEYS = frozenset({
    "timestamp", "acao", "entidade_tipo", "operador", "resultado",
})

#: Termos que nunca podem aparecer em nenhum lugar do payload (chave ou
#: valor) — o mesmo texto, em minúsculas, do payload inteiro é escaneado.
FORBIDDEN_TERMS: tuple[str, ...] = (
    "valor_anterior", "valor_novo", "derivado_de", "request_id",
    "telefone", "whatsapp", "observacao", "observação",
    "paciente_nome", "primeiro_nome", "nome completo", "laudo",
    "pedido médico", "pedido medico", "cpf",
    "access_token", "private_key", "client_secret",
    "https://docs.google.com", "/spreadsheets/d/", "spreadsheet_id",
)

#: Chaves que já existiram na allowlist e foram removidas por decisão de
#: segurança. Um snapshot antigo no disco pode ainda trazê-las; nesse caso
#: a mensagem precisa dizer POR QUE, em vez de um genérico "fora da
#: allowlist" que faria alguém tentar recolocá-las.
REMOVED_EVENT_KEYS: dict[str, str] = {
    "entidade_id": (
        "identificador bruto de linha do banco não é exportável para o "
        "navegador (M23, 2º incidente) — regenere o snapshot"
    ),
}

_CPF_RE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")


def validate_auditoria_payload(data: dict) -> list[str]:
    """Valida um payload já carregado de ``auditoria-summary.local.json``.

    Função pura: não faz I/O, não imprime, não chama ``sys.exit``. Retorna
    a lista de violações encontradas (lista vazia = payload seguro). Quem
    chama decide o que fazer com o resultado — abortar a escrita, falhar
    um teste ou reportar num script de linha de comando.
    """
    errors: list[str] = []

    source = data.get("source", {})
    if source.get("safeToDisplay") is not True:
        errors.append("auditoria-summary não marcado como safeToDisplay=true.")
    if source.get("containsPersonalData") is not False:
        errors.append("auditoria-summary pode conter dado pessoal.")

    text = json.dumps(data, ensure_ascii=False).lower()
    for termo in FORBIDDEN_TERMS:
        if termo in text:
            errors.append(f"termo proibido '{termo}' detectado em auditoria-summary.")
    if _CPF_RE.search(text):
        errors.append("padrão de CPF detectado em auditoria-summary.")

    eventos = data.get("ultimos_eventos", [])
    for i, evt in enumerate(eventos):
        extras = set(evt.keys()) - ALLOWED_EVENT_KEYS
        for chave in sorted(extras & REMOVED_EVENT_KEYS.keys()):
            errors.append(
                f"campo removido '{chave}' em evento [{i}]: {REMOVED_EVENT_KEYS[chave]}."
            )
        extras -= REMOVED_EVENT_KEYS.keys()
        if extras:
            errors.append(f"campo(s) fora da allowlist em evento [{i}]: {sorted(extras)}.")
        for k, v in evt.items():
            if k == "timestamp":
                continue
            if _FONE_RE.search(str(v)):
                errors.append(f"padrão de telefone no campo '{k}' do evento [{i}].")

    return errors
