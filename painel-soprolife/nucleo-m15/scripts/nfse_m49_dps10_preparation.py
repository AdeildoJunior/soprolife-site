"""M49 — offline preparation of synthetic DPS #10, after the E0008 fix.

DPS #9 is FINAL REJECTED (Sefin E0008: emission datetime read as later than
processing). It is never reissued and its number is never reused — this
builds a brand-new synthetic case that takes the next durable number, 10.

Same validated fiscal profile as DPS #9: service date 2026-09-15, whose
competence resolves to SOPROLIFE-M43-GOLDEN-v2 (carrying every M41
correction plus tribFed/piscofins CST=00 / tpRetPisCofins=0). Using
2026-09-16 instead would silently resolve a DIFFERENT configuration
(SOPROLIFE-M43-GOLDEN-v1, effective_from 2026-09-16), so the date is
deliberate, not incidental.

Runs entirely against the isolated M30 restricted-lab SQLite database,
through the SAME reviewed service-layer functions (attendances
._create_attendance / nfse.prepare) instead of raw INSERTs. No HTTP, no
certificate, no password, no network. Reuses the same synthetic person as
DPS #2-#9 (PES-000007).

Adds the M49 check the previous preparations could not make: the built
dhEmi must be written in Brasília civil time with a -03:00 offset, and must
represent the same instant as the UTC value handed in.
"""
import json
import types
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Person, User
from app.routers import attendances
from app.schemas import (
    AtendimentoCreate,
    AtendimentoEspirometria,
    AtendimentoFinanceiro,
    AtendimentoFinanceiroExame,
)
from app.services import nfse
from app.services.nfse_national import fiscal_config, preflight as nfse_preflight
from app.services.nfse_national.dps_builder import (
    EMISSION_TIMEZONE,
    DpsInput,
    Recipient,
    build_dps_element,
    serialize_dps,
)
from app.services.nfse_national.identifiers import DpsIdComponents
from app.services.nfse_national.xsd_validation import validate_dps_xml

EXISTING_SYNTHETIC_PERSON_ID = "5be5fd4e-1262-449c-9b7b-4270e08ce9c1"  # PES-000007
LAB_ACTOR_USER_ID = "a7dc2c74-009b-40bc-a95e-93a0bd797672"
NFSE_NS = "http://www.sped.fazenda.gov.br/nfse"
REJECTED_DPS9_DOCUMENT_ID = "9be02a64-d41d-44f0-a2d4-483a79ef288a"
EXPECTED_DPS_NUMBER = 10


def _q(tag):
    return f"{{{NFSE_NS}}}{tag}"


def main():
    settings = get_settings()
    assert settings.nfse_environment == "restricted", settings.nfse_environment
    assert settings.nfse_enabled is True
    assert settings.nfse_restricted_network_enabled is False, "network gate must stay closed"

    with get_sessionmaker()() as db:
        # --- DPS #9 must stay exactly as Sefin left it -------------------
        from app.models import FiscalDocument  # noqa: PLC0415
        dps9 = db.get(FiscalDocument, REJECTED_DPS9_DOCUMENT_ID)
        assert dps9 is not None
        assert dps9.state == "failed", f"DPS #9 must stay failed, found {dps9.state}"

        person = db.get(Person, EXISTING_SYNTHETIC_PERSON_ID)
        assert person is not None and not person.arquivado
        user = db.get(User, LAB_ACTOR_USER_ID)
        assert user is not None

        payload = AtendimentoCreate(
            tipo="espirometria_soprolife",
            espirometria=AtendimentoEspirometria(
                data_exame="15/09/2026",
                status="Realizado",
                broncodilatador=False,
                modalidade="cowork",          # -> DIRECT flow, never PASTORE
                municipio_atendimento_ibge="3304557",  # Rio de Janeiro
                observacao="Sintetico M49 - DPS #10 - pos correcao E0008 (dhEmi em horario civil).",
            ),
            financeiro=AtendimentoFinanceiro(
                espirometria=AtendimentoFinanceiroExame(
                    valor="440.00",
                    status="Recebido",
                ),
            ),
            idempotency_key="m49-dps10-synthetic-preparation-20260916",
            person_id=person.id,
        )

        fake_request = types.SimpleNamespace(
            state=types.SimpleNamespace(request_id="m49-dps10-preparation")
        )

        def resolver(_db):
            return person

        resultado = attendances._create_attendance(payload, resolver, fake_request, db, user)
        exam_id = resultado["espirometria"]["id"]

        doc = nfse.prepare(db, exam_id, settings, actor=user.id, request_id="m49-dps10-preparation")

        assert doc.id != REJECTED_DPS9_DOCUMENT_ID, "must be a NEW document, never DPS #9"

        print("== M49 PHASE I (document created) ==")
        print(json.dumps({
            "exam_public_code": resultado["espirometria"]["public_code"],
            "exam_id": exam_id,
            "financial_entries": resultado["lancamentos"],
            "document_id": doc.id,
            "eligibility": doc.eligibility,
            "state": doc.state,
            "blocking_reasons": doc.blocking_reasons,
        }, indent=2, ensure_ascii=False))

        # ---- independent unsigned-XML build + explicit invariant checks ----
        preparation = nfse.latest_preparation(db, doc.id)
        national_config = fiscal_config.resolve_active_configuration(
            db, environment=doc.environment, as_of=preparation.competence)
        assert national_config is not None
        assert national_config.version == "SOPROLIFE-M43-GOLDEN-v2", national_config.version
        assert national_config.pis_cofins_cst == "00"
        assert national_config.pis_cofins_tp_ret == 0

        recipient_person = db.get(Person, preparation.recipient_person_id)
        recipient = Recipient(nome=recipient_person.nome_completo, cpf=recipient_person.cpf)

        # The durable number for THIS document — allocated once, keyed by
        # document id, never derived from an attempt counter (M36).
        from app.services.nfse_national import dps_numbering  # noqa: PLC0415
        dps_number = dps_numbering.allocate_dps_number(
            db, document_id=doc.id,
            codigo_municipio=national_config.issuer_municipio_ibge,
            tipo_inscricao_federal=2,
            inscricao_federal=national_config.issuer_cnpj,
            serie_dps="00001",
        )
        assert dps_number == EXPECTED_DPS_NUMBER, (
            f"expected durable DPS number {EXPECTED_DPS_NUMBER}, got {dps_number}")
        db.commit()

        dps_id = DpsIdComponents(
            codigo_municipio=national_config.issuer_municipio_ibge, tipo_inscricao_federal=2,
            inscricao_federal=national_config.issuer_cnpj, serie_dps="00001",
            numero_dps=str(dps_number).rjust(15, "0"),
        )
        # Captured as UTC, exactly like the real issuance path does; the
        # builder is responsible for writing it in civil time (M49).
        dh_emi_utc = datetime.now(timezone.utc)
        data = DpsInput(
            # M59 — ambiente explícito; estes scripts sempre emitiram em Restrita.
            environment="restricted",
            config=national_config, dps_id=dps_id, dh_emi=dh_emi_utc,
            ver_aplic="m49-dps10-check", numero_dps_display=str(dps_number),
            serie_dps_display="1",
            competencia=preparation.competence, tomador=recipient,
            descricao_servico=preparation.description,
            valor_servico=Decimal(str(preparation.amount_snapshot)),
            municipio_prestacao_ibge=preparation.service_municipio_ibge,
        )
        root = build_dps_element(data)
        unsigned_xml = serialize_dps(root)
        validate_dps_xml(unsigned_xml)

        inf = root.find(_q("infDPS"))
        prest = inf.find(_q("prest"))
        reg_trib = prest.find(_q("regTrib"))
        trib = inf.find(_q("valores")).find(_q("trib"))
        trib_fed = trib.find(_q("tribFed"))
        piscofins = trib_fed.find(_q("piscofins")) if trib_fed is not None else None

        dh_emi_text = inf.find(_q("dhEmi")).text
        dh_emi_parsed = datetime.fromisoformat(dh_emi_text)
        expected_local = dh_emi_utc.astimezone(EMISSION_TIMEZONE)

        checks = {
            # --- M49: the fix this preparation exists to prove -----------
            "dhEmi_offset_is_minus_0300": dh_emi_text.endswith("-03:00"),
            "dhEmi_not_utc_labelled": not dh_emi_text.endswith("+00:00"),
            "dhEmi_same_instant_as_utc_input": dh_emi_parsed == dh_emi_utc.replace(
                microsecond=0),
            "dhEmi_matches_brasilia_wall_clock": (
                dh_emi_text[:19] == expected_local.strftime("%Y-%m-%dT%H:%M:%S")),
            "dhEmi_not_after_now": dh_emi_parsed <= datetime.now(ZoneInfo("America/Sao_Paulo")),
            "dhEmi_second_precision": "." not in dh_emi_text,
            # --- carried forward from M41/M43 ----------------------------
            "tpEmit_is_1": inf.find(_q("tpEmit")).text == "1",
            "prest_CNPJ_present": prest.find(_q("CNPJ")) is not None,
            "prest_xNome_absent": prest.find(_q("xNome")) is None,
            "prest_address_absent": prest.find(_q("end")) is None,
            "opSimpNac_is_3": reg_trib.find(_q("opSimpNac")).text == "3",
            "regApTribSN_present_and_1": reg_trib.find(_q("regApTribSN")).text == "1",
            "regEspTrib_is_0": reg_trib.find(_q("regEspTrib")).text == "0",
            "totTrib_uses_pTotTribSN_not_indTotTrib": (
                trib.find(_q("totTrib")).find(_q("indTotTrib")) is None
                and trib.find(_q("totTrib")).find(_q("pTotTribSN")) is not None
            ),
            "tribFed_present": trib_fed is not None,
            "piscofins_CST_is_00": piscofins is not None and piscofins.find(_q("CST")).text == "00",
            "piscofins_tpRetPisCofins_is_0": (
                piscofins is not None and piscofins.find(_q("tpRetPisCofins")).text == "0"
            ),
            "trib_child_order_correct": [c.tag for c in trib] == [
                _q("tribMun"), _q("tribFed"), _q("totTrib")],
            "nDPS_is_10": inf.find(_q("nDPS")).text == str(EXPECTED_DPS_NUMBER),
            "dCompet_is_service_date": inf.find(_q("dCompet")).text == "2026-09-15",
            "cLocPrestacao_is_rio": "3304557" in serialize_dps(root).decode(),
            "xsd_valid": True,  # validate_dps_xml above already raised if not
        }
        print("== M49 PHASE I (unsigned XML invariants, incl. E0008 fix) ==")
        print(json.dumps({**checks, "dhEmi_text": dh_emi_text}, indent=2))
        assert all(checks.values()), "one or more DPS #10 invariants failed"

        # ---- offline preflight WITHOUT a certificate ----
        result = nfse_preflight.run_offline_preflight(
            db, doc.id, settings, actor=user.id, certificate=None,
        )
        print("== M49 PHASE I (offline preflight, no certificate) ==")
        print(json.dumps({
            "document_id": result.document_id,
            "status": result.status,
            "stage_reached": str(result.stage_reached),
            "blockers": result.blockers,
            "request_fingerprint": result.request_fingerprint,
            "staged_artifacts": result.staged_artifacts,
        }, indent=2, ensure_ascii=False))

        print("== M49 PHASE I (final) ==")
        print(json.dumps({
            "dps10_document_id": doc.id,
            "dps_number": dps_number,
            "state": doc.state,
            "eligibility": doc.eligibility,
            "blocking_reasons": doc.blocking_reasons,
            "dps9_still_failed": dps9.state == "failed",
        }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
