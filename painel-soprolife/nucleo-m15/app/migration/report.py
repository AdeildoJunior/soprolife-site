"""Relatórios sanitizados de migração (JSON/Markdown/CSV) — sem PII.

Todo valor textual exportado para CSV é NEUTRALIZADO contra injeção de
fórmula (Excel/Sheets): célula iniciada por = + - @ TAB ou CR recebe
apóstrofo de prefixo. Nenhuma fórmula da fonte é executada nem reproduzida.
"""

import csv
import io
import json
import pathlib
from datetime import datetime, timezone

_DANGEROUS_FIRST = ("=", "+", "-", "@", "\t", "\r")


def neutralize_cell(value) -> str:
    """Prefixa apóstrofo em conteúdo que um editor interpretaria como fórmula."""
    text = "" if value is None else str(value)
    if text and text[0] in _DANGEROUS_FIRST:
        return "'" + text
    return text


def _flatten(prefix: str, obj, out: list[tuple[str, str]]) -> None:
    if isinstance(obj, dict):
        for key in sorted(obj):
            _flatten(f"{prefix}{key}." if prefix else f"{key}.", obj[key], out)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _flatten(f"{prefix}{i}.", item, out)
    else:
        out.append((prefix.rstrip("."), "" if obj is None else str(obj)))


def render_csv(report: dict) -> str:
    """CSV chave/valor achatado, determinístico e neutralizado."""
    pairs: list[tuple[str, str]] = []
    _flatten("", report, pairs)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["chave", "valor"])
    for key, value in pairs:
        writer.writerow([neutralize_cell(key), neutralize_cell(value)])
    return buffer.getvalue()


def render_markdown(report: dict, titulo: str) -> str:
    lines = [f"# {titulo}", ""]
    for key in ("snapshot_id", "batch_id", "source_type", "sha256",
                "status_snapshot", "total", "validas", "rejeitadas",
                "excluidas", "ambiguas", "criticos", "avisos",
                "candidatos_identidade"):
        if key in report:
            lines.append(f"- {key}: `{report[key]}`")
    motivos = report.get("motivos") or {}
    if motivos:
        lines += ["", "## Motivos (contagem)", ""]
        for motivo in sorted(motivos):
            lines.append(f"- {motivo}: {motivos[motivo]}")
    amostra = report.get("rejeicoes_amostra") or []
    if amostra:
        lines += ["", "## Rejeições (amostra, sem conteúdo bruto)", ""]
        for r in amostra:
            lines.append(f"- linha {r['linha']}: {r['motivo']}")
    if report.get("aviso"):
        lines += ["", f"> {report['aviso']}"]
    lines.append("")
    return "\n".join(lines)


def write_sanitized_reports(
    report: dict, out_dir: str | pathlib.Path, base_name: str | None = None
) -> dict:
    """Grava JSON, Markdown e CSV sanitizados (diretório var/, fora do Git)."""
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = base_name or f"migracao-{report.get('source_type', 'snapshot')}-{stamp}"
    json_path = out / f"{base}.json"
    md_path = out / f"{base}.md"
    csv_path = out / f"{base}.csv"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    md_path.write_text(
        render_markdown(report, f"Migração — {report.get('source_type', '')}"),
        encoding="utf-8",
    )
    csv_path.write_text(render_csv(report), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path), "csv": str(csv_path)}
