"""M60 — the minimum fiscal identity a PRODUCTION tomador must have.

WHAT WAS ALREADY THERE, AND WHY IT IS NOT ENOUGH FOR PRODUCTION

``identifiers.assert_cpf`` checks the SHAPE only — eleven digits (TSCPF).
That is the right check for the wire format and it is what the restricted
cycle ran on. It accepts ``00000000000`` and ``11111111111``, which even pass
the check-digit algorithm, and it accepts any eleven digits a typo produces.

For homologation that is fine: tpAmb=2 exists precisely so that made-up data
can be exercised against the government's validator. For production it is
not, because the tomador's CPF ends up on a real fiscal document, in the
company's books and in the recipient's name.

WHAT THIS ADDS, AND ONLY FOR PRODUCTION

Applied at ``dispatch``, for ``environment='production'`` alone. Produção
Restrita keeps its existing rule unchanged and on purpose — weakening or
tightening the homologation path would invalidate the only end-to-end
evidence this system has (DPS #10), and homologation is where fake data is
supposed to live.

  - CPF present, textual, eleven digits (the existing contract, reused);
  - not a repeated-digit sequence — these pass the check-digit algorithm, so
    excluding them has to be explicit or ``00000000000`` sails through;
  - correct check digits (módulo 11), which a typo almost never satisfies;
  - a name that looks like a legal name: at least two words of two or more
    characters, within the TSNomeRazaoSocial length;
  - no placeholder marker in the name — test, teste, mock, exemplo, dummy,
    fulano and the rest. A stand-in record must not become a real invoice.

WHAT THIS DELIBERATELY DOES NOT DO

It does not decide whether the CPF belongs to a real living person. That is
a Receita Federal lookup: it needs the network, it is out of scope for an
offline mission, and pretending otherwise would be worse than saying so. A
structurally valid CPF that belongs to nobody still passes here.

It does not blocklist specific numbers. A list of "known test CPFs" would be
arbitrary, would silently rot, and would mostly serve to break this
project's own fixtures rather than to protect anybody — and the mission's
own instruction was not to turn test fixtures into production rules.

It does not touch PJ recipients. ``Recipient`` already supports ``cnpj`` and
``sem_nif_motivo``; ``dispatch`` has always required a CPF before either
could be reached, so no behaviour changes for them and none is invented
here. If a PJ tomador becomes real, this is the module that should grow.

PRIVACY

No function here logs, returns or embeds a CPF in any message. Every failure
raises a stable code with no payload, so an error reaching a log, an audit
row or an HTTP response can never carry the identifier that caused it. The
codes are the API; the values never are.
"""
from __future__ import annotations

import re
import unicodedata

from .identifiers import CPF_PATTERN, InvalidIdentifierError

# Markers that say "this record is a stand-in". Matched against the name with
# accents folded and case ignored, as whole words where the marker is a word,
# so a surname like "Testa" or "Mockford" is not caught by "test"/"mock".
PLACEHOLDER_MARKERS = (
    "test", "teste", "mock", "exemplo", "example", "sample", "dummy",
    "placeholder", "ficticio", "fake", "lorem", "ipsum",
    "fulano", "sicrano", "beltrano", "sintetico", "sintetica", "synthetic",
    "paciente 001", "nome do paciente", "xxx", "aaa", "zzz",
)

MAX_NAME_LENGTH = 300   # TSNomeRazaoSocial


class RecipientIdentityError(InvalidIdentifierError):
    """A production tomador whose identity may not back a real NFS-e.

    Subclasses ``InvalidIdentifierError`` so existing fail-closed handlers
    that already catch identifier problems keep catching this one.
    """

    def __init__(self, code: str):
        # The code IS the message. Never the CPF, never the name.
        super().__init__(code)
        self.code = code


def _fold(text: str) -> str:
    """Lowercase and strip accents, so 'Exemplo' and 'exémplo' fold together."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


def cpf_check_digits_valid(cpf: str) -> bool:
    """Módulo 11, the standard CPF algorithm. Shape is assumed checked."""
    digits = [int(c) for c in cpf]
    for length in (9, 10):
        total = sum(digits[i] * ((length + 1) - i) for i in range(length))
        remainder = (total * 10) % 11
        if remainder == 10:
            remainder = 0
        if remainder != digits[length]:
            return False
    return True


def assert_production_cpf(cpf: object) -> str:
    """The CPF contract for a production tomador. Returns it unchanged."""
    if not isinstance(cpf, str) or not CPF_PATTERN.fullmatch(cpf):
        raise RecipientIdentityError("recipient_cpf_malformed")
    if len(set(cpf)) == 1:
        # 00000000000, 11111111111, ... — all of them satisfy módulo 11, so
        # this has to be its own rule rather than a consequence of the next.
        raise RecipientIdentityError("recipient_cpf_repeated_digits")
    if not cpf_check_digits_valid(cpf):
        raise RecipientIdentityError("recipient_cpf_check_digits_invalid")
    return cpf


def assert_production_name(nome: object) -> str:
    """The name contract for a production tomador. Returns it unchanged."""
    if not isinstance(nome, str):
        raise RecipientIdentityError("recipient_name_missing")
    stripped = nome.strip()
    if not stripped:
        raise RecipientIdentityError("recipient_name_missing")
    if len(stripped) > MAX_NAME_LENGTH:
        raise RecipientIdentityError("recipient_name_too_long")

    folded = _fold(stripped)
    words = [w for w in re.split(r"[^0-9a-z]+", folded) if w]
    if len([w for w in words if len(w) >= 2]) < 2:
        # A legal name on a fiscal document carries at least a given name and
        # a surname. One word is a handle or a stub, not a tomador.
        raise RecipientIdentityError("recipient_name_not_a_full_name")

    word_set = set(words)
    for marker in PLACEHOLDER_MARKERS:
        marker_words = [w for w in re.split(r"[^0-9a-z]+", marker) if w]
        if len(marker_words) == 1:
            if marker_words[0] in word_set:
                raise RecipientIdentityError("recipient_name_looks_like_placeholder")
        elif marker in folded:
            raise RecipientIdentityError("recipient_name_looks_like_placeholder")
    return stripped


def assert_production_recipient(*, nome: object, cpf: object) -> tuple[str, str]:
    """Both halves. Name first, so a bad record fails on the part that is
    safe to put in a message before the part that never is."""
    return assert_production_name(nome), assert_production_cpf(cpf)
