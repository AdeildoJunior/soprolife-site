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

import copy
import json
import re
from pathlib import Path


def _load_pii_guard():
    """Carrega a guarda de PII vizinha, em scripts/, pelo caminho.

    Importação por CAMINHO e não por ``import pii_guard`` porque os dois
    módulos são carregados assim por nucleo-m15/app/snapshots.py, fora do
    pacote da API e sem scripts/ no sys.path.

    A direção é só esta: pii_guard não importa nada local (é a folha), então
    não há ciclo. Ausência é ERRO, nunca permissão.
    """
    import importlib.util

    caminho = Path(__file__).resolve().parent / "pii_guard.py"
    if not caminho.is_file():
        raise RuntimeError(f"Guarda de PII não encontrada em {caminho}")
    spec = importlib.util.spec_from_file_location("_audit_contract_pii_guard", caminho)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Guarda de PII ilegível em {caminho}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

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

#: Mapas de ``stats`` cujas CHAVES são rótulos de vocabulário fechado
#: (nome da ação auditada, nome da tabela, papel do operador, resultado).
VOCABULARY_STAT_MAPS: tuple[str, ...] = (
    "por_acao", "por_entidade", "por_operador", "por_resultado",
)

#: Campos de cada evento que carregam esse mesmo vocabulário fechado.
VOCABULARY_EVENT_FIELDS: tuple[str, ...] = (
    "acao", "entidade_tipo", "operador", "resultado",
)

#: Forma exigida de um rótulo de vocabulário: minúsculas, dígitos, ``_`` e
#: ``.`` — o formato que o código emite ("auth.token_emitido",
#: "laudo_conteudo_entregue"). Um rótulo fora desta forma não é vocabulário
#: e volta a ser varrido como texto livre.
#:
#: M25.29C — a definição passou a viver em scripts/pii_guard.py e é IMPORTADA
#: aqui. Os dois módulos validam o mesmo arquivo (auditoria-summary) nas duas
#: pontas; manter duas cópias desta forma seria manter duas fechaduras
#: diferentes na mesma porta, e a divergência entre as pontas já quebrou o
#: primeiro deploy do M23.
_SLUG_RE = _load_pii_guard().VOCABULARY_SLUG_RE

#: Texto que substitui um rótulo de vocabulário na varredura de termos
#: proibidos. Neutro de propósito: não casa com nenhum FORBIDDEN_TERMS.
_VOCAB_PLACEHOLDER = "vocabulario"


def _mask_vocabulary(data: dict) -> tuple[dict, list[str]]:
    """Devolve (payload mascarado, erros de forma) para a varredura textual.

    ``FORBIDDEN_TERMS`` existe para impedir que CONTEÚDO vaze no resumo —
    o texto de um laudo, uma observação, um telefone. Ele varre o payload
    inteiro como uma única string, e por isso passou a acusar o próprio
    NOME das operações auditadas assim que a operação de laudos entrou em
    produção (09/08/2026): ``laudo_conteudo_entregue`` contém "laudo".
    Resultado — nenhum snapshot do painel foi mais gerado, porque a
    validação é tudo-ou-nada (M25.28).

    Um rótulo de vocabulário não é conteúdo: é o nome da operação, emitido
    pelo próprio código, com um inteiro por valor. Aqui ele é validado pela
    FORMA (``_SLUG_RE``) e então mascarado antes da varredura textual. O
    que não tiver forma de rótulo continua sendo varrido normalmente — a
    dispensa não cria um esconderijo para texto livre.
    """
    erros: list[str] = []
    mascarado = copy.deepcopy(data)

    stats = mascarado.get("stats")
    if isinstance(stats, dict):
        for mapa in VOCABULARY_STAT_MAPS:
            contagens = stats.get(mapa)
            if not isinstance(contagens, dict):
                continue
            limpo = {}
            for i, (rotulo, total) in enumerate(contagens.items()):
                if _SLUG_RE.fullmatch(str(rotulo)):
                    limpo[f"{_VOCAB_PLACEHOLDER}_{i}"] = total
                else:
                    # Fora da forma de rótulo: mantém o texto original, que
                    # segue exposto à varredura de termos proibidos.
                    erros.append(
                        f"rotulo fora do formato de vocabulario em stats.{mapa}."
                    )
                    limpo[rotulo] = total
            stats[mapa] = limpo

    eventos = mascarado.get("ultimos_eventos")
    if isinstance(eventos, list):
        for evt in eventos:
            if not isinstance(evt, dict):
                continue
            for campo in VOCABULARY_EVENT_FIELDS:
                valor = evt.get(campo)
                if valor is None:
                    continue
                if _SLUG_RE.fullmatch(str(valor)):
                    evt[campo] = _VOCAB_PLACEHOLDER

    return mascarado, erros


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

    # Rótulos de vocabulário fechado saem da varredura de texto livre — ver
    # _mask_vocabulary. O CPF continua sendo procurado no payload ORIGINAL:
    # nenhuma máscara pode esconder um documento.
    mascarado, erros_forma = _mask_vocabulary(data)
    errors.extend(erros_forma)

    text = json.dumps(mascarado, ensure_ascii=False).lower()
    for termo in FORBIDDEN_TERMS:
        if termo in text:
            errors.append(f"termo proibido '{termo}' detectado em auditoria-summary.")
    if _CPF_RE.search(json.dumps(data, ensure_ascii=False).lower()):
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
