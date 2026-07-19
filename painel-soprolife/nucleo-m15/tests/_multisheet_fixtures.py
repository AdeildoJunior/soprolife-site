"""Construtores exclusivamente sintéticos do envelope bruto M15.6B."""

import hashlib
import json

from app.migration.adapters import ADAPTERS, ADAPTERS_VERSION

RAW_ENVELOPE_SCHEMA = "m15.raw-envelope.1"
RAW_SHEET_SCHEMA = "m15.raw-sheet.1"


def headers_for(kind: str) -> list[str]:
    return [field.header for field in ADAPTERS[kind].campos]


def synthetic_row(kind: str, **values) -> dict[str, str]:
    row = {header: "" for header in headers_for(kind)}
    row.update({key: str(value) for key, value in values.items()})
    return row


def minimal_row(kind: str, suffix: str = "001") -> dict[str, str]:
    defaults = {
        "crm_pacientes": synthetic_row(
            kind, paciente_id=f"P-{suffix}", primeiro_nome=f"Pessoa Sintetica {suffix}"
        ),
        "crm_espirometria": synthetic_row(kind, exame_id=f"E-{suffix}"),
        "crm_consultas": synthetic_row(kind, consulta_id=f"C-{suffix}"),
        "leads": synthetic_row(kind, lead_id=f"L-{suffix}"),
        "crm_clinicas": synthetic_row(
            kind, clinica_id=f"CL-{suffix}", nome_clinica=f"Clinica Sintetica {suffix}"
        ),
        "contatos_b2b": synthetic_row(
            kind,
            contato_id=f"B-{suffix}",
            clinica_id=f"CL-{suffix}",
            nome_contato=f"Contato Sintetico {suffix}",
        ),
        "followup_whatsapp": synthetic_row(kind, followup_id=f"FW-{suffix}"),
        "financeiro_lancamentos": synthetic_row(
            kind, id_lancamento=f"F-{suffix}", tipo_movimento="receita"
        ),
        "pastore_config": synthetic_row(kind, unidade=f"Unidade Sintetica {suffix}"),
        "pastore_atendimentos": synthetic_row(
            kind, data_atendimento="01/07/2026", unidade=f"Unidade Sintetica {suffix}"
        ),
        "pastore_custos": synthetic_row(
            kind, data="01/07/2026", unidade=f"Unidade Sintetica {suffix}"
        ),
        "pcmso_historico": synthetic_row(
            kind, **{"Nome da clínica/empresa": f"Historico Sintetico {suffix}"}
        ),
    }
    return defaults[kind]


def write_raw_envelope(
    base,
    sheets: list[dict],
    *,
    mapping_version: str = ADAPTERS_VERSION,
    envelope_mutator=None,
    sheet_mutator=None,
    envelope_name: str = "snapshot-envelope.json",
) -> str:
    refs = []
    for index, spec in enumerate(sheets, start=1):
        kind = spec["kind"]
        alias = spec.get("alias", f"Aba Sintetica {index:02d}")
        headers = list(spec.get("headers", headers_for(kind)))
        rows = spec.get("rows", [])
        raw_rows = []
        for offset, row in enumerate(rows, start=2):
            if isinstance(row, dict):
                values = [str(row.get(header, "")) for header in headers]
            else:
                values = [str(value) for value in row]
            raw_rows.append({"linha": offset, "valores": values})
        payload = {
            "schema_version": RAW_SHEET_SCHEMA,
            "sheet_alias": alias,
            "headers": headers,
            "linha_inicial_dados": 2,
            "rows": raw_rows,
        }
        if sheet_mutator:
            sheet_mutator(index, payload)
        content = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        filename = f"sheet-{index:02d}.json"
        (base / filename).write_bytes(content)
        refs.append({
            "sheet_alias": alias,
            "sheet_kind": kind,
            "arquivo": filename,
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    envelope = {
        "schema_version": RAW_ENVELOPE_SCHEMA,
        "workbook_alias": "workbook-sintetico",
        "snapshot_ts_utc": "2026-07-01T12:00:00+00:00",
        "locale": "pt-BR",
        "timezone": "America/Sao_Paulo",
        "mapping_version": mapping_version,
        "sheets": refs,
    }
    if envelope_mutator:
        envelope_mutator(envelope)
    (base / envelope_name).write_text(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return envelope_name


def representative_sheets() -> list[dict]:
    phone_a = "(21) 0000-9101"
    return [
        {
            "kind": "financeiro_lancamentos",
            "alias": "Financeiro Sintetico",
            "rows": [
                synthetic_row(
                    "financeiro_lancamentos",
                    id_lancamento="F-001",
                    id_atendimento="E-001",
                    tipo_movimento="receita",
                    valor_cobrado="100,00",
                ),
                synthetic_row(
                    "financeiro_lancamentos",
                    id_lancamento="F-002",
                    id_atendimento="E-INEXISTENTE",
                    tipo_movimento="receita",
                    valor_cobrado="80,00",
                ),
            ],
        },
        {
            "kind": "contatos_b2b",
            "alias": "Contatos Sinteticos",
            "rows": [minimal_row("contatos_b2b")],
        },
        {
            "kind": "crm_clinicas",
            "alias": "Clinicas Sinteticas",
            "rows": [minimal_row("crm_clinicas")],
        },
        {
            "kind": "crm_pacientes",
            "alias": "Pacientes Sinteticos",
            "rows": [
                synthetic_row(
                    "crm_pacientes",
                    paciente_id="P-001",
                    primeiro_nome="Pessoa Sintetica Alpha",
                    telefone=phone_a,
                    consentimento_whatsapp="sim",
                    data_cadastro="01/07/2026",
                ),
                synthetic_row(
                    "crm_pacientes",
                    paciente_id="P-002",
                    primeiro_nome="Pessoa Sintetica Beta",
                    telefone="(21) 0000-9102",
                    consentimento_whatsapp="nao contatar",
                ),
            ],
        },
        {
            "kind": "leads",
            "alias": "Leads Sinteticos",
            "rows": [
                synthetic_row(
                    "leads", lead_id="L-001", nome="Lead Sintetico",
                    entidade_id="P-001",
                ),
                synthetic_row(
                    "leads", lead_id="L-002", nome="Outro Lead",
                    telefone_whatsapp=phone_a,
                ),
                synthetic_row(
                    "leads", lead_id="L-003", nome="Pessoa Sintetica Alpha"
                ),
            ],
        },
        {
            "kind": "crm_espirometria",
            "alias": "Espirometrias Sinteticas",
            "rows": [
                synthetic_row(
                    "crm_espirometria", exame_id="E-001",
                    primeiro_nome="Pessoa Sintetica Alpha", telefone=phone_a,
                    data_exame="07/2026",
                ),
                synthetic_row(
                    "crm_espirometria", exame_id="E-002",
                    primeiro_nome="Pessoa Sem Vinculo", telefone="(21) 0000-9199",
                ),
            ],
        },
        {
            "kind": "crm_consultas",
            "alias": "Consultas Sinteticas",
            "rows": [
                synthetic_row(
                    "crm_consultas", consulta_id="C-001",
                    paciente_id="P-001", data_consulta="2026-07",
                ),
                synthetic_row(
                    "crm_consultas", consulta_id="C-002",
                    primeiro_nome="Consulta Sem Vinculo",
                ),
            ],
        },
        {
            "kind": "followup_whatsapp",
            "alias": "Followups Sinteticos",
            "rows": [
                synthetic_row(
                    "followup_whatsapp", followup_id="FW-001",
                    primeiro_nome="Pessoa Sintetica Alpha", telefone=phone_a,
                )
            ],
        },
        {
            "kind": "pastore_config",
            "alias": "Pastore Config Sintetica",
            "rows": [
                synthetic_row(
                    "pastore_config", unidade="Unidade Sintetica 001",
                    status="ativo", valor_exame_sem_bd="999,00",
                )
            ],
        },
        {
            "kind": "pastore_atendimentos",
            "alias": "Pastore Atendimentos Sinteticos",
            "rows": [
                synthetic_row(
                    "pastore_atendimentos", data_atendimento="01/07/2026",
                    unidade="Unidade Sintetica 001",
                    paciente_nome="Pessoa Sintetica Alpha",
                    paciente_whatsapp=phone_a, valor_cobrado="999,00",
                    consentimento_contato_futuro="sim",
                )
            ],
        },
        {
            "kind": "pastore_custos",
            "alias": "Pastore Custos Sinteticos",
            "rows": [
                synthetic_row(
                    "pastore_custos", data="01/07/2026",
                    unidade="Unidade Sintetica 001", valor="50,00",
                )
            ],
        },
        {
            "kind": "pcmso_historico",
            "alias": "Historico Sintetico",
            "rows": [
                synthetic_row(
                    "pcmso_historico",
                    **{
                        "Nome da clínica/empresa": "Empresa Historica Sintetica",
                        "Data do próximo passo": "data-invalida",
                    },
                )
            ],
        },
    ]
