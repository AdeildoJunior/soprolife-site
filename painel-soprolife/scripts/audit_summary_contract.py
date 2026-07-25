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
"""

from __future__ import annotations

import json
import re

#: Único conjunto de chaves permitido em cada evento de
#: ``ultimos_eventos``. Nenhum consumidor deve manter uma cópia própria
#: desta lista.
ALLOWED_EVENT_KEYS = frozenset({
    "timestamp", "acao", "entidade_tipo", "entidade_id", "operador", "resultado",
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
        if extras:
            errors.append(f"campo(s) fora da allowlist em evento [{i}]: {sorted(extras)}.")
        for k, v in evt.items():
            if k == "timestamp":
                continue
            if _FONE_RE.search(str(v)):
                errors.append(f"padrão de telefone no campo '{k}' do evento [{i}].")

    return errors
