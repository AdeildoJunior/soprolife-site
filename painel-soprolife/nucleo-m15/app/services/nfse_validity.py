"""M58 — the single source of truth for ``fiscal_validity``.

THE QUESTION THIS MODULE ANSWERS, and the two it refuses to be confused with:

    fiscal_validity = "may the company treat THIS document as a fiscally
                       valid NFS-e for its real operation and accounting?"

That is a THIRD question, distinct from both of the ones the queue already
answers:

  - ``state == 'issued'``   -> "did a real provider really emit this?"
  - ``environment``         -> "against which authority environment?"

M57 separated ``issued`` from ``simulated`` and was careful to say that the
first was not a synonym for the third. This module is where that stays true.
The trap it exists to prevent is the one-line version of itself::

    fiscal_validity = (doc.state == 'issued')   # WRONG

which would declare DPS #10 — a genuine NFS-e, really issued by SEFIN, under
``tpAmb=2`` in Produção Restrita — usable for accounting. It is not. An
homologation document is a real document in a practice environment: it proves
the pipeline works, it proves nothing about the company's books.

WHY THIS IS A PER-DOCUMENT EVIDENCE QUESTION, NEVER A SETTINGS QUESTION

``production_gates.py`` answers a different, forward-looking question: "may we
turn production issuance on?" It reads mutable runtime configuration — network
gates, certificate margins, a human's out-of-band authorization.

``fiscal_validity`` must not read any of that. A document legitimately issued
in production last month cannot stop being a valid fiscal document because
someone toggled a network flag off today. So this contract is a pure function
of the document and the evidence already recorded about it: durable,
reproducible, and identical whoever asks and whenever.

FAIL CLOSED

Every requirement is conjunctive and the default is ``False``. Anything
missing, ambiguous, mutually inconsistent or simply unrecognized yields
``False``. There is deliberately no branch anywhere that can return ``True``
without ALL of the evidence below, and no configuration flag that can shortcut
one.

REACHABILITY TODAY

``True`` is currently unreachable, and that is the correct state of the world,
not a gap in this module:

  - ``get_provider()`` refuses the production environment outright
    (``production_provider_not_implemented`` — a structural absence);
  - ``nfse_national.config`` types the environment field as
    ``tp_amb: Literal[2]`` and ``dps_builder`` raises on anything else, so the
    system cannot even BUILD a tpAmb=1 DPS;
  - therefore no ``fiscal_documents`` row with ``environment='production'``
    can exist yet, and none does.

The contract is written and tested now, against synthetic fixtures, precisely
so that it is already in place — and already pinned by tests — on the day
production wiring is authorized, rather than being improvised then.

COST

The two cheap checks (plain attribute reads) come first and the three queries
only after them. That ordering is deliberate: ``serialize_document`` is called
per row by the paginated document list, and today every document fails on
environment or state before a single extra query is issued. Even once
production exists, the queries are bounded by the page size.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FiscalArtifact, FiscalAttempt, FiscalDocument
from .nfse_national.identifiers import NFSE_ACCESS_KEY_PATTERN


# The ONE environment whose documents can ever be fiscally valid. Restricted
# is excluded on purpose and permanently: it is homologation (tpAmb=2), where
# SEFIN issues real, well-formed documents that carry no fiscal effect.
FISCALLY_VALID_ENVIRONMENT = 'production'

# The operations that can establish an issuance. 'cancel' is absent: a
# cancellation is the opposite of the fact we are looking for.
ISSUING_OPERATIONS = ('issue', 'reconcile')

# The artifact that must exist: the NFS-e document the authority itself
# returned. Our own DPS (signed or not) is what we SENT — it is not evidence
# that anything came back.
REQUIRED_ARTIFACT_KIND = 'nfse_xml'


def _recorded_access_keys(db: Session, document_id: str) -> set[str]:
    """Every distinct access key any completed attempt ever recorded for this
    document. More than one means the document's fiscal identity is disputed
    and nothing about it may be asserted."""
    rows = db.execute(
        select(FiscalAttempt.external_id).where(
            FiscalAttempt.document_id == document_id,
            FiscalAttempt.phase == 'completed',
            FiscalAttempt.external_id.is_not(None),
        )
    ).scalars().all()
    return {key for key in rows if key}


def _conclusive_issuance(db: Session, document: FiscalDocument) -> FiscalAttempt | None:
    """The completed, real-provider attempt that establishes the issuance, or
    ``None``.

    Every clause is a requirement in its own right; together they say: a real
    production provider, in the production environment, reported a successful
    issuance, was not left needing reconciliation, and named the document.
    """
    return db.execute(
        select(FiscalAttempt).where(
            FiscalAttempt.document_id == document.id,
            FiscalAttempt.phase == 'completed',
            FiscalAttempt.operation.in_(ISSUING_OPERATIONS),
            # The outcome must be the M57 success word. A production attempt
            # can only ever have been written after M57 (production has never
            # been wired), so unlike the restricted history there is no
            # legacy 'simulated' vocabulary to accommodate here — and
            # accommodating it would be exactly the hole this module closes.
            FiscalAttempt.outcome == 'issued',
            # Provider and environment must BOTH be production and must agree
            # with the document. A row where they disagree is incoherent
            # evidence, not weak evidence.
            FiscalAttempt.provider == FISCALLY_VALID_ENVIRONMENT,
            FiscalAttempt.environment == FISCALLY_VALID_ENVIRONMENT,
            FiscalAttempt.reconciliation_required.is_(False),
            FiscalAttempt.external_id.is_not(None),
        ).order_by(FiscalAttempt.number.desc()).limit(1)
    ).scalars().first()


def fiscal_validity(db: Session, document: FiscalDocument) -> bool:
    """Whether this document may be treated as a fiscally valid NFS-e.

    The single source of truth. ``serialize_document`` calls it; nothing else
    may compute its own answer, because two answers to this question is worse
    than either of them.
    """
    # 1. Production only. Mock invents identifiers; restricted (tpAmb=2)
    #    issues real documents with no fiscal effect. Neither can qualify,
    #    whatever else they carry.
    if document.environment != FISCALLY_VALID_ENVIRONMENT:
        return False

    # 2. The document's own terminal state must be the real success state.
    #    This one equality carries three separate requirements, which is why
    #    it is worth spelling them out: it excludes the in-flight states
    #    ('issuing', 'reconciling') and 'uncertain', so a document still
    #    needing reconciliation can never qualify; it excludes 'cancelled',
    #    because a cancelled NFS-e is not a valid one; and it excludes
    #    'simulated', the mock's success state. Each of those exclusions is
    #    pinned by its own case in test_nfse_m58_fiscal_validity.py rather
    #    than by a defensive branch here that could never run.
    if document.state != 'issued':
        return False

    # 3. A conclusive, coherent, real-provider issuance must be on record.
    #    This is where the ATTEMPT's own reconciliation_required is checked —
    #    the meaningful version of that requirement, since it records what the
    #    provider actually left unresolved rather than restating the state.
    attempt = _conclusive_issuance(db, document)
    if attempt is None:
        return False

    # 4. The access key must be a government TSIdNFSe, not a mock id and not
    #    anything else shaped loosely like one.
    if not NFSE_ACCESS_KEY_PATTERN.fullmatch(attempt.external_id or ''):
        return False

    # 5. The document's fiscal identity must be undisputed: exactly one access
    #    key was ever recorded, and it is this one. M30 already refuses to
    #    overwrite a key with a conflicting one and drives the document to
    #    'uncertain' when that happens; this re-checks the end state rather
    #    than trusting that the transition was always taken.
    keys = _recorded_access_keys(db, document.id)
    if keys != {attempt.external_id}:
        return False

    # 6. The NFS-e the authority returned must be stored. Without it we have a
    #    claim about a document rather than the document.
    has_nfse_xml = db.scalar(
        select(FiscalArtifact.id).where(
            FiscalArtifact.document_id == document.id,
            FiscalArtifact.kind == REQUIRED_ARTIFACT_KIND,
        ).limit(1)
    )
    if not has_nfse_xml:
        return False

    return True
