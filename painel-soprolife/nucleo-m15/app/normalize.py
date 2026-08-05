"""Normalização de nome e telefone para busca e candidatos de identidade.

Telefone é CANDIDATO de correspondência, nunca prova. Nome nunca funde nada.
"""

import re
import unicodedata


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize_phone(phone: str | None) -> str:
    """Só dígitos; prefixa 55 quando parece nacional (10-11 dígitos)."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) in (10, 11) and not digits.startswith("55"):
        digits = "55" + digits
    return digits


CPF_PATTERN = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
PHONE_PATTERN = re.compile(r"\b(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?9?\d{4}[-\s]?\d{4}\b")
EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Duas ou mais palavras capitalizadas adjacentes ~ nome próprio ("Maria Silva").
# Conservador de propósito: financeiro é fail-closed para qualquer coisa
# que se pareça com identificação pessoal.
NAME_LIKE_PATTERN = re.compile(
    r"\b[A-ZÀ-Ý][a-zà-ÿ]{2,}\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ý][a-zà-ÿ]{2,}\b"
)
# Vocabulário clínico que nunca pode aparecer em payload financeiro.
CLINICAL_KEYWORDS = (
    "diagnostico", "diagnóstico", "cid-", "cid ", "asma", "dpoc", "tosse",
    "dispneia", "bronquite", "enfisema", "tuberculose", "fibrose",
    "sintoma", "doença", "doenca", "laudo clinico", "laudo clínico",
    "prontuario", "prontuário",
)


def contains_pii_like(text: str | None) -> bool:
    """Detecção de PII em texto livre: CPF, telefone, e-mail ou nome próprio."""
    if not text:
        return False
    return bool(
        CPF_PATTERN.search(text)
        or PHONE_PATTERN.search(text)
        or EMAIL_PATTERN.search(text)
        or NAME_LIKE_PATTERN.search(text)
    )


def contains_clinical_info(text: str | None) -> bool:
    """Detecção de informação clínica em texto livre (financeiro fail-closed)."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in CLINICAL_KEYWORDS)


def crm_display_matches(display: str | None, crm_number: str | None) -> bool:
    """M25.2/M25.3 — a apresentação humana do CRM só pode reformatar o número.

    `crm_number` é o armazenamento canônico (dígitos, com zero à esquerda
    preservado). `crm_display` existe apenas para imprimir "52.62307-5" no
    lugar de "52623075" — separadores e espaços são livres, mas os DÍGITOS
    precisam ser exatamente os mesmos, na mesma ordem. Assim a formatação
    nunca vira um caminho lateral para alterar o registro profissional.

    O CHECK correspondente no banco (`ck_physician_profiles_crm_display_digits`)
    só garante não-vazio: extrair dígitos de forma portátil entre SQLite e
    PostgreSQL não cabe num CHECK, então a igualdade é validada aqui.
    """
    if display is None:
        return True
    return re.sub(r"\D", "", display) == re.sub(r"\D", "", crm_number or "")
