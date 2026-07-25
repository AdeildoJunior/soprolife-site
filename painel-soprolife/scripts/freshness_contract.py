#!/usr/bin/env python3
"""
SoproLife — Contrato de Frescor Operacional (M14.3A.1).

Biblioteca compartilhada pelos conectores/sincronizadores do painel.
Implementa o contrato definido em core/contracts/freshness-contract.json:

  - estados de frescor (fresh/stale/unavailable/error/authentication_required/
    publication_pending/unknown);
  - exit codes padronizados;
  - classificação de erro em código de catálogo (mensagem SEGURA, nunca a
    exceção crua);
  - avaliação de frescor com relógio injetável (testável offline);
  - escrita atômica de JSON (tmp no mesmo diretório + validação + rename),
    que nunca substitui um snapshot válido por um inválido.

Offline por construção: este módulo não faz nenhuma chamada de rede.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

# ── Estados de frescor ──────────────────────────────────────────────────────
FRESH = "fresh"
STALE = "stale"
UNAVAILABLE = "unavailable"
ERROR = "error"
AUTH_REQUIRED_STATE = "authentication_required"
# M21 — credencial durável presente, concessão de acesso pendente na
# propriedade do Google. Distinto de authentication_required (ADC pessoal
# vencido), que não deve mais aparecer em operação normal.
CREDENTIAL_PENDING = "credential_pending"
PUBLICATION_PENDING = "publication_pending"
UNKNOWN = "unknown"

ESTADOS_VALIDOS = {
    FRESH, STALE, UNAVAILABLE, ERROR,
    AUTH_REQUIRED_STATE, CREDENTIAL_PENDING, PUBLICATION_PENDING, UNKNOWN,
}

# ── Exit codes padronizados ─────────────────────────────────────────────────
EXIT_FRESH = 0
EXIT_STALE = 10
EXIT_AUTH_REQUIRED = 11
EXIT_SCHEMA_INVALID = 12
EXIT_UNAVAILABLE = 13
EXIT_ERROR = 14
EXIT_UNKNOWN = 15

EXIT_CREDENTIAL_PENDING = 16

EXIT_POR_ESTADO = {
    FRESH: EXIT_FRESH,
    STALE: EXIT_STALE,
    AUTH_REQUIRED_STATE: EXIT_AUTH_REQUIRED,
    CREDENTIAL_PENDING: EXIT_CREDENTIAL_PENDING,
    UNAVAILABLE: EXIT_UNAVAILABLE,
    ERROR: EXIT_ERROR,
    PUBLICATION_PENDING: EXIT_STALE,
    UNKNOWN: EXIT_UNKNOWN,
}

# Ordem de severidade para agregar várias fontes (pior estado vence).
_SEVERIDADE = [FRESH, STALE, PUBLICATION_PENDING, UNKNOWN,
               UNAVAILABLE, ERROR, CREDENTIAL_PENDING, AUTH_REQUIRED_STATE]


def pior_estado(estados) -> str:
    """Agrega estados de várias fontes: o mais severo define o conjunto."""
    pior = FRESH
    for e in estados:
        if e not in ESTADOS_VALIDOS:
            e = UNKNOWN
        if _SEVERIDADE.index(e) > _SEVERIDADE.index(pior):
            pior = e
    return pior


# ── Catálogo de erros (mensagens fixas e seguras) ──────────────────────────
CATALOGO_ERROS = {
    "AUTH_REQUIRED": "Reautenticação necessária. Execute a renovação do ADC manualmente.",
    # M21 — a credencial durável (conta de serviço) existe e funciona, mas a
    # propriedade do Google ainda não concedeu acesso de leitura a ela. É uma
    # pendência de configuração humana pontual, NÃO um login expirado.
    "CREDENTIAL_PENDING": (
        "Credencial de serviço configurada, aguardando concessão de acesso de "
        "leitura na propriedade do Google."
    ),
    "PERMISSION_DENIED": "Acesso negado pela API. Verifique permissões da conta.",
    "SOURCE_NOT_FOUND": "Fonte não encontrada. Verifique a configuração local.",
    "DEPENDENCY_MISSING": "Dependência local ausente. Instale os requisitos do conector.",
    "NOT_CONFIGURED": "Fonte não configurada neste ambiente.",
    "NETWORK_BLOCKED": "Execução sem rede — sincronização não tentada.",
    "SCHEMA_INVALID": "Arquivo de dados não cumpre o contrato — snapshot anterior preservado.",
    "SYNC_FAILED": "Falha de sincronização. Snapshot anterior preservado.",
}

ESTADO_POR_ERRO = {
    "AUTH_REQUIRED": AUTH_REQUIRED_STATE,
    "CREDENTIAL_PENDING": CREDENTIAL_PENDING,
    "PERMISSION_DENIED": AUTH_REQUIRED_STATE,
    "SOURCE_NOT_FOUND": UNAVAILABLE,
    "DEPENDENCY_MISSING": UNAVAILABLE,
    "NOT_CONFIGURED": UNAVAILABLE,
    "NETWORK_BLOCKED": UNAVAILABLE,
    "SCHEMA_INVALID": ERROR,
    "SYNC_FAILED": ERROR,
}

# Padrões (case-insensitive) → código de erro. Ordem importa: autenticação
# primeiro, porque erros de auth chegam embrulhados em 403/503 genéricos.
_PADROES_ERRO = [
    (r"reauthentication is needed", "AUTH_REQUIRED"),
    (r"invalid_grant", "AUTH_REQUIRED"),
    (r"token has been expired or revoked", "AUTH_REQUIRED"),
    (r"access_token_scope_insufficient", "AUTH_REQUIRED"),
    (r"insufficient permission", "AUTH_REQUIRED"),
    (r"quota project", "AUTH_REQUIRED"),
    (r"application.default.credentials", "AUTH_REQUIRED"),
    (r"permission_denied", "PERMISSION_DENIED"),
    (r"\b403\b", "PERMISSION_DENIED"),
    (r"\b404\b", "SOURCE_NOT_FOUND"),
    (r"not found", "SOURCE_NOT_FOUND"),
    (r"no module named", "DEPENDENCY_MISSING"),
    (r"importerror", "DEPENDENCY_MISSING"),
]


def classificar_erro(mensagem_crua) -> str:
    """Mapeia uma mensagem técnica crua para um código do catálogo.

    A mensagem crua NUNCA deve ser persistida nem exibida — só o código e a
    mensagem fixa do catálogo saem daqui.
    """
    texto = str(mensagem_crua or "").lower()
    for padrao, codigo in _PADROES_ERRO:
        if re.search(padrao, texto):
            return codigo
    return "SYNC_FAILED"


def mensagem_segura(error_code: str) -> str:
    return CATALOGO_ERROS.get(error_code, CATALOGO_ERROS["SYNC_FAILED"])


# ── Relógio e tempo ─────────────────────────────────────────────────────────
def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None = None) -> str:
    return (dt or agora_utc()).isoformat(timespec="seconds")


def _parse_iso(valor):
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(str(valor))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def idade_segundos(timestamp_iso, agora: datetime | None = None):
    """Idade em segundos de um timestamp ISO; None se não parseável."""
    dt = _parse_iso(timestamp_iso)
    if dt is None:
        return None
    return max(0, int(((agora or agora_utc()) - dt).total_seconds()))


# ── Status de fonte (bloco do contrato) ─────────────────────────────────────
def status_fonte(source_id: str, source_name: str, *,
                 last_success_at=None, last_attempt_at=None,
                 source_data_through=None, error_code=None,
                 publication_required=False, warnings=None) -> dict:
    """Monta o bloco de status de UMA fonte, conforme o contrato."""
    err = error_code if error_code in CATALOGO_ERROS else (
        "SYNC_FAILED" if error_code else None)
    return {
        "sourceId": source_id,
        "sourceName": source_name,
        "schemaVersion": SCHEMA_VERSION,
        "lastSuccessAt": last_success_at,
        "lastAttemptAt": last_attempt_at,
        "sourceDataThrough": source_data_through,
        "status": "ok" if err is None else "failed",
        "errorCode": err,
        "errorMessageSafe": mensagem_segura(err) if err else None,
        "sourceAvailable": err is None,
        "authenticationRequired": ESTADO_POR_ERRO.get(err) == AUTH_REQUIRED_STATE if err else False,
        # M21 — pendência de concessão de acesso, não de login expirado.
        "credentialPending": ESTADO_POR_ERRO.get(err) == CREDENTIAL_PENDING if err else False,
        "publicationRequired": bool(publication_required),
        "warnings": list(warnings or []),
    }


def avaliar_frescor(fonte: dict, stale_after_hours, agora: datetime | None = None) -> dict:
    """Avalia o estado de frescor de um bloco de fonte do contrato.

    Retorna {"freshnessStatus", "ageSeconds"} calculados NA LEITURA —
    ageSeconds nunca é persistido. `agora` é injetável para testes.
    """
    agora = agora or agora_utc()
    err = fonte.get("errorCode")
    last_success = fonte.get("lastSuccessAt")
    age = idade_segundos(last_success, agora)

    if err and ESTADO_POR_ERRO.get(err) in (AUTH_REQUIRED_STATE, CREDENTIAL_PENDING):
        estado = ESTADO_POR_ERRO[err]
    elif err in ("NOT_CONFIGURED", "DEPENDENCY_MISSING", "SOURCE_NOT_FOUND", "NETWORK_BLOCKED"):
        estado = UNAVAILABLE
    elif fonte.get("publicationRequired"):
        estado = PUBLICATION_PENDING
    elif last_success is None:
        estado = UNKNOWN if not err else ERROR
    elif err:
        # Houve falha na última tentativa, mas existe snapshot válido antigo.
        estado = ERROR
    elif age is None:
        estado = UNKNOWN
    else:
        try:
            limite = float(stale_after_hours) * 3600.0
        except (TypeError, ValueError):
            limite = None
        estado = FRESH if (limite is None or age <= limite) else STALE

    # Mesmo em erro/auth, se o snapshot preservado também venceu, o pior
    # estado já é o do erro — mas fresh nunca deve mascarar vencimento.
    if estado == FRESH and age is not None and stale_after_hours is not None:
        try:
            if age > float(stale_after_hours) * 3600.0:
                estado = STALE
        except (TypeError, ValueError):
            pass

    return {"freshnessStatus": estado, "ageSeconds": age}


def exit_code_para(estado: str) -> int:
    return EXIT_POR_ESTADO.get(estado, EXIT_UNKNOWN)


# ── Sanitização de saída ────────────────────────────────────────────────────
_PADROES_PROIBIDOS = re.compile(
    r"(token|credential|client_secret|private_key|api[_-]?key|refresh_token|"
    r"access_token|/home/[a-z0-9_.-]+|~/\.config|application[-_]default)",
    re.IGNORECASE,
)


def contem_segredo(texto: str) -> bool:
    """True se o texto contém padrão proibido (segredo, path privado)."""
    return bool(_PADROES_PROIBIDOS.search(str(texto or "")))


# ── Escrita atômica ─────────────────────────────────────────────────────────
def escrever_json_atomico(destino, payload, *, validador=None, mode=0o600) -> None:
    """Grava JSON de forma atômica: tmp no MESMO diretório + validação + rename.

    - `validador(payload)` opcional: deve levantar exceção para vetar a escrita
      (o arquivo anterior permanece intacto).
    - O tmp é sempre removido em falha; nunca deixa lixo parcial no destino.
    """
    destino = Path(destino)
    if validador is not None:
        validador(payload)

    texto = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    # Round-trip: garante que o que vai ao disco é JSON válido.
    json.loads(texto)

    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=destino.name + ".", suffix=".tmp",
                                    dir=str(destino.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(texto)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, destino)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def validar_snapshot_marketing(payload) -> None:
    """Validação mínima de contrato do snapshot marketing-seo (schema v2).

    Levanta ValueError em violação — usada como `validador` da escrita
    atômica e pelo modo --check do sincronizador.
    """
    if not isinstance(payload, dict):
        raise ValueError("snapshot deve ser um objeto JSON")
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("snapshot sem bloco meta")
    if meta.get("safeToDisplay") is not True:
        raise ValueError("meta.safeToDisplay deve ser true")
    if meta.get("containsPersonalData") is not False:
        raise ValueError("meta.containsPersonalData deve ser false")
    if meta.get("configured") is True and "generatedAt" not in meta:
        raise ValueError("meta.generatedAt obrigatório quando configured=true")
    src_status = meta.get("sourceStatus")
    if src_status is not None:
        if not isinstance(src_status, dict):
            raise ValueError("meta.sourceStatus deve ser um objeto")
        for nome, bloco in src_status.items():
            if not isinstance(bloco, dict) or "sourceId" not in bloco:
                raise ValueError(f"sourceStatus.{nome} sem sourceId")
            if bloco.get("errorCode") not in (None, *CATALOGO_ERROS):
                raise ValueError(f"sourceStatus.{nome}.errorCode fora do catálogo")
            if contem_segredo(json.dumps(bloco.get("warnings", []), ensure_ascii=False)):
                raise ValueError(f"sourceStatus.{nome}.warnings contém padrão proibido")
    if contem_segredo(json.dumps(payload.get("warnings", []), ensure_ascii=False)):
        raise ValueError("warnings contém padrão proibido")
