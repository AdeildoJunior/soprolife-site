"""M23 — política de identificadores no resumo público de auditoria.

Regressão do SEGUNDO incidente de produção do M23 (25/07/2026).

O que aconteceu: ``AuditLog.entidade_id`` guarda o UUID da linha auditada.
O exportador copiava esse UUID cru para ``auditoria-summary.local.json``,
que é servido ao navegador. O contrato de segurança varre todo valor de
evento com um detector de telefone (``\\(?\\d{2}\\)?\\s?\\d{4,5}-?\\d{4}``)
e ~2,5% dos UUIDv4 casam com ele por coincidência de dígitos — por
exemplo ``3f688837-5450-491e-b949-623b90cf145f``. Como ``export_snapshots``
é all-or-nothing, 8 eventos assim impediram a gravação de TODOS os
snapshots do painel.

A correção não relaxou o detector: o resumo público deixou de exportar
qualquer identificador de registro. Estes testes provam as duas metades —
que o identificador não sai, e que a falha de produção não volta.

Tudo roda com o exportador REAL, o contrato REAL, a guarda de PII REAL e
o ``check-access.sh`` REAL. Nada de allowlist duplicada em fixture: foi
exatamente uma cópia divergente que causou o PRIMEIRO incidente do M23.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import snapshots
from app.models import AuditLog

REPO_ROOT = Path(__file__).resolve().parents[3]
PAINEL = REPO_ROOT / "painel-soprolife"

sys.path.insert(0, str(PAINEL / "scripts"))
from audit_summary_contract import (  # noqa: E402
    ALLOWED_EVENT_KEYS,
    REMOVED_EVENT_KEYS,
    _FONE_RE,
    validate_auditoria_payload,
)


# Valores com a FORMA do que existe (ou poderia existir) na coluna
# entidade_id em produção. Cada um é sintético; nenhum vem do banco real.
#
# O primeiro é o caso que derrubou o deploy: um UUIDv4 legítimo cujo
# recorte de dígitos casa com o detector de telefone. Ele é verificado
# abaixo — se um dia deixar de casar, o teste falha alto em vez de passar
# provando nada.
UUID_QUE_PARECE_TELEFONE = "3f688837-5450-491e-b949-623b90cf145f"

IDENTIFICADORES_PRODUCAO = [
    ("uuid_que_parece_telefone", UUID_QUE_PARECE_TELEFONE),
    ("uuid_comum", "0b9d5f2a-8c14-4e77-9a3b-1d6e0f4c8b25"),
    ("celular_br", "(21) 98877-6655"),
    ("celular_br_sem_mascara", "21988776655"),
    ("fixo_br", "(21) 3456-7890"),
    ("cpf_com_mascara", "123.456.789-09"),
    ("cpf_sem_mascara", "12345678909"),
    ("email", "paciente.teste@exemplo.com.br"),
    ("codigo_institucional", "PES-000123"),
    ("id_numerico_longo", "20260725221652"),
]


def _povoar_auditoria(db, *, com_falhas: bool = True) -> None:
    """Uma trilha com todas as formas de identificador de uma vez."""
    base = datetime.now(timezone.utc)
    for i, (rotulo, valor) in enumerate(IDENTIFICADORES_PRODUCAO):
        db.add(AuditLog(
            acao="lead.criado", entidade="leads", entidade_id=valor,
            ts_utc=base - timedelta(minutes=i),
            detalhes={"codigo": rotulo},
        ))
    # Identificador ausente: a coluna é nullable e eventos de auth não a preenchem.
    db.add(AuditLog(acao="auth.token_emitido", entidade="users", entidade_id=None,
                    ts_utc=base - timedelta(minutes=50)))
    db.add(AuditLog(acao="pessoa.criada", entidade=None, entidade_id=None,
                    ts_utc=base - timedelta(minutes=51)))
    if com_falhas:
        # Ações de falha/rejeição, para provar que 'resultado' continua real.
        db.add(AuditLog(acao="auth.falha", ts_utc=base - timedelta(minutes=60)))
        db.add(AuditLog(acao="pcmso.rejeitado", entidade="attendances",
                        entidade_id=UUID_QUE_PARECE_TELEFONE,
                        ts_utc=base - timedelta(minutes=61)))
    db.commit()


@pytest.fixture()
def auditoria_producao(db):
    _povoar_auditoria(db)
    return db


# ------------------------------------------------------- a causa exata da falha

def test_o_uuid_da_producao_realmente_casa_com_o_detector_de_telefone():
    """Âncora do incidente: sem esta premissa os testes abaixo não provam
    nada. O UUID é um identificador legítimo do banco e mesmo assim casa
    com o detector — é por isso que exportá-lo era insustentável."""
    assert _FONE_RE.search(UUID_QUE_PARECE_TELEFONE), (
        "o UUID de referência do incidente parou de casar com o detector; "
        "escolha outro valor de âncora antes de confiar nesta suíte"
    )


def test_exportador_gera_auditoria_com_identificadores_que_pareciam_telefone(
    auditoria_producao, tmp_path
):
    """A falha de produção, reproduzida: com esses entidade_id no banco, o
    exportador ANTES abortava a gravação inteira. Agora completa."""
    resultado = snapshots.export_snapshots(auditoria_producao, tmp_path, write=True)

    assert resultado["modo"] == "write"
    gerados = {item["arquivo"] for item in resultado["gerados"]}
    assert gerados == set(snapshots.SNAPSHOT_FILES)
    for nome in snapshots.SNAPSHOT_FILES:
        assert (tmp_path / nome).is_file(), f"{nome} não foi gravado"

    payload = json.loads((tmp_path / "auditoria-summary.local.json").read_text("utf-8"))
    assert payload["stats"]["total_eventos"] == len(IDENTIFICADORES_PRODUCAO) + 4
    assert validate_auditoria_payload(payload) == []


def test_nenhum_identificador_bruto_aparece_no_texto_serializado(
    auditoria_producao, tmp_path
):
    """Não basta a chave sumir: o VALOR não pode aparecer em lugar nenhum
    do arquivo — nem em stats, nem em source, nem por acidente."""
    snapshots.export_snapshots(auditoria_producao, tmp_path, write=True)
    texto = (tmp_path / "auditoria-summary.local.json").read_text("utf-8")

    for rotulo, valor in IDENTIFICADORES_PRODUCAO:
        assert valor not in texto, f"identificador '{rotulo}' vazou no snapshot"
    # E também não vaza pelos OUTROS snapshots gravados na mesma operação.
    for nome in snapshots.SNAPSHOT_FILES:
        conteudo = (tmp_path / nome).read_text("utf-8")
        for rotulo, valor in IDENTIFICADORES_PRODUCAO:
            assert valor not in conteudo, f"'{rotulo}' vazou em {nome}"


def test_nenhum_evento_exporta_chave_de_identificador(auditoria_producao):
    payload = snapshots.build_auditoria_summary(auditoria_producao)
    for evento in payload["ultimos_eventos"]:
        assert "entidade_id" not in evento
        assert "user_id" not in evento
        assert "detalhes" not in evento
        assert set(evento) <= ALLOWED_EVENT_KEYS, set(evento) - ALLOWED_EVENT_KEYS


def test_entidade_id_saiu_da_allowlist_do_contrato_compartilhado():
    """O contrato é fonte única: se alguém recolocar entidade_id na
    allowlist, este teste cai antes de o dado voltar a ser exportado."""
    assert "entidade_id" not in ALLOWED_EVENT_KEYS
    assert ALLOWED_EVENT_KEYS == frozenset({
        "timestamp", "acao", "entidade_tipo", "operador", "resultado",
    })
    assert "entidade_id" in REMOVED_EVENT_KEYS


def test_contrato_rejeita_snapshot_antigo_que_ainda_traz_entidade_id():
    """Um auditoria-summary gerado antes desta correção continua no disco
    até a próxima geração. Ele precisa ser REJEITADO, com mensagem que
    explique o motivo em vez de convidar a recolocar a chave."""
    payload = {
        "source": {"safeToDisplay": True, "containsPersonalData": False},
        "ultimos_eventos": [
            {"timestamp": "2026-07-25T22:29:18+00:00", "acao": "lead.criado",
             "entidade_tipo": "leads", "entidade_id": "l-1",
             "operador": "gestor", "resultado": "ok"},
        ],
    }
    erros = validate_auditoria_payload(payload)
    assert erros
    assert any("entidade_id" in e and "campo removido" in e for e in erros)


def test_contrato_continua_rejeitando_chave_extra_qualquer():
    """A remoção de entidade_id não abriu exceção para nada: qualquer
    chave fora da allowlist segue derrubando o payload."""
    payload = {
        "source": {"safeToDisplay": True, "containsPersonalData": False},
        "ultimos_eventos": [
            {"timestamp": "2026-07-25T22:29:18+00:00", "acao": "lead.criado",
             "entidade_tipo": "leads", "operador": "gestor", "resultado": "ok",
             "referencia_interna": "qualquer coisa"},
        ],
    }
    erros = validate_auditoria_payload(payload)
    assert any("referencia_interna" in e for e in erros)


def test_guarda_de_pii_nao_declara_mais_entidade_id_como_institucional():
    """A guarda de PII tinha entidade_id na lista de campos institucionais.
    Deixá-lo ali seria manter aberta a porta para reexportá-lo sem alarme."""
    import pii_guard

    institucionais = pii_guard._FILE_RULESETS[snapshots.PII_RULESET]["campos_institucionais"]
    assert "entidade_id" not in institucionais
    assert "entidade_tipo" in institucionais


# ------------------------------------------------------------------ agregados

def test_estatisticas_uteis_sobrevivem_a_remocao_do_identificador(auditoria_producao):
    """O identificador saiu; a capacidade de ler a trilha, não. Contagem
    por ação, tipo de entidade, papel de operador e resultado continuam."""
    stats = snapshots.build_auditoria_summary(auditoria_producao)["stats"]

    assert stats["por_acao"]["lead.criado"] == len(IDENTIFICADORES_PRODUCAO)
    assert stats["por_entidade"]["leads"] == len(IDENTIFICADORES_PRODUCAO)
    assert stats["por_entidade"]["users"] == 1
    assert stats["por_entidade"]["attendances"] == 1
    # Evento sem entidade não inventa um tipo.
    assert sum(stats["por_entidade"].values()) == stats["total_eventos"] - 2
    assert stats["por_resultado"] == {"ok": 12, "falha": 2}
    assert stats["erros"] == 2
    assert "por_operador" in stats


def test_resultado_continua_derivado_da_acao_real(db):
    _povoar_auditoria(db)
    eventos = snapshots.build_auditoria_summary(db)["ultimos_eventos"]
    por_acao = {e["acao"]: e["resultado"] for e in eventos}
    assert por_acao["auth.falha"] == "falha"
    assert por_acao["pcmso.rejeitado"] == "falha"
    assert por_acao["lead.criado"] == "ok"


def test_entidade_tipo_continua_disponivel_para_o_painel(auditoria_producao):
    """O frontend trocou entidade_id por entidade_tipo no detalhe do evento;
    o campo precisa continuar saindo, e continuar sendo nome de tabela."""
    eventos = snapshots.build_auditoria_summary(auditoria_producao)["ultimos_eventos"]
    tipos = {e.get("entidade_tipo") for e in eventos}
    assert "leads" in tipos
    for tipo in tipos:
        if tipo is not None:
            assert tipo.replace("_", "").isalnum(), tipo


# ------------------------------------------------ atomicidade / fail-closed

def test_snapshot_valido_anterior_sobrevive_a_uma_geracao_insegura(
    auditoria_producao, tmp_path, monkeypatch
):
    """A escrita é all-or-nothing: se qualquer payload for inseguro, NADA é
    gravado e o snapshot válido que já estava no disco permanece intacto."""
    snapshots.export_snapshots(auditoria_producao, tmp_path, write=True)
    alvo = tmp_path / "auditoria-summary.local.json"
    conteudo_valido = alvo.read_text("utf-8")
    assinaturas = {
        nome: (tmp_path / nome).read_text("utf-8") for nome in snapshots.SNAPSHOT_FILES
    }

    # Um builder passa a devolver um evento com identificador cru — a
    # regressão exata que estamos impedindo.
    original = snapshots.build_auditoria_summary

    def inseguro(db):
        payload = original(db)
        payload["ultimos_eventos"][0]["entidade_id"] = UUID_QUE_PARECE_TELEFONE
        return payload

    monkeypatch.setitem(snapshots.BUILDERS, "auditoria-summary.local.json", inseguro)

    with pytest.raises(ValueError) as exc:
        snapshots.export_snapshots(auditoria_producao, tmp_path, write=True)
    assert "entidade_id" in str(exc.value)

    assert alvo.read_text("utf-8") == conteudo_valido
    for nome, texto in assinaturas.items():
        assert (tmp_path / nome).read_text("utf-8") == texto, f"{nome} foi alterado"
    assert not list(tmp_path.glob(".*.tmp")), "arquivo temporário ficou para trás"


# --------------------------------------------- check-access.sh REAL, isolado

def _montar_arvore_isolada(raiz: Path) -> Path:
    """Réplica mínima do repositório, com Git próprio, para rodar o
    check-access.sh REAL sem tocar nos dados locais do desenvolvedor."""
    painel = raiz / "painel-soprolife"
    painel.mkdir(parents=True)
    for sub in ("scripts", "core"):
        shutil.copytree(PAINEL / sub, painel / sub,
                        ignore=shutil.ignore_patterns("__pycache__"))
    (painel / "data").mkdir()
    (painel / "data-private").mkdir()
    (raiz / ".gitignore").write_text(
        "painel-soprolife/data/*.local.json\npainel-soprolife/data-private/\n",
        encoding="utf-8",
    )
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    subprocess.run(["git", "init", "-q", "."], cwd=raiz, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=raiz, check=True, env=env,
                   stdout=subprocess.DEVNULL)
    return painel


def _rodar_check_access(raiz: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "painel-soprolife/scripts/check-access.sh"],
        cwd=raiz, capture_output=True, text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
             "GIT_CONFIG_SYSTEM": "/dev/null"},
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash indisponível")
def test_check_access_real_aprova_a_saida_real_do_exportador(
    auditoria_producao, tmp_path
):
    """Ponta a ponta com as ferramentas de produção: o exportador REAL grava,
    o check-access.sh REAL valida. Se o exportador e o contrato voltarem a
    divergir (1º incidente) ou o identificador voltar (2º incidente), este
    teste falha."""
    raiz = tmp_path / "repo"
    painel = _montar_arvore_isolada(raiz)

    snapshots.export_snapshots(auditoria_producao, painel / "data", write=True)
    assert (painel / "data" / "auditoria-summary.local.json").is_file()

    proc = _rodar_check_access(raiz)
    saida = proc.stdout + proc.stderr
    assert proc.returncode == 0, saida
    assert "auditoria-summary seguro" in saida, saida
    assert "padrão de telefone" not in saida, saida
    assert "ERRO" not in saida, saida


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash indisponível")
def test_check_access_real_reprova_snapshot_com_identificador(
    auditoria_producao, tmp_path
):
    """Contraprova: se alguém reintroduzir o identificador por fora do
    exportador, o check-access.sh REAL derruba a verificação."""
    raiz = tmp_path / "repo"
    painel = _montar_arvore_isolada(raiz)

    snapshots.export_snapshots(auditoria_producao, painel / "data", write=True)
    alvo = painel / "data" / "auditoria-summary.local.json"
    payload = json.loads(alvo.read_text("utf-8"))
    payload["ultimos_eventos"][0]["entidade_id"] = UUID_QUE_PARECE_TELEFONE
    alvo.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    proc = _rodar_check_access(raiz)
    saida = proc.stdout + proc.stderr
    assert proc.returncode != 0, saida
    assert "entidade_id" in saida, saida


@pytest.mark.skipif(shutil.which("git") is None, reason="git indisponível")
def test_nenhum_snapshot_gerado_entra_no_git(auditoria_producao, tmp_path):
    """O .gitignore do repositório real precisa cobrir TODOS os arquivos que
    o exportador grava — um snapshot operacional commitado é vazamento."""
    snapshots.export_snapshots(auditoria_producao, tmp_path, write=True)
    for nome in snapshots.SNAPSHOT_FILES:
        alvo = f"painel-soprolife/data/{nome}"
        proc = subprocess.run(["git", "check-ignore", "-q", "--no-index", alvo],
                              cwd=REPO_ROOT, capture_output=True)
        assert proc.returncode == 0, f"{alvo} NÃO está gitignored"
