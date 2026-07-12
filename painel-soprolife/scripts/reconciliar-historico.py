#!/usr/bin/env python3
"""
SoproLife OS Local Core — Reconciliação histórica (M14.3) — SOMENTE LEITURA.

Audita a consistência entre CRM Pacientes, CRM Espirometria, CRM Consultas,
Financeiro_Lancamentos e Parceria Pastore - Atendimentos, e propõe (dry-run)
o plano de reconciliação. Esta ferramenta NUNCA escreve na planilha, NUNCA
funde registros e NUNCA inventa valores — a aplicação real do plano é uma
etapa futura que exige autorização explícita.

Premissas de negócio (confirmadas pelo usuário — ver
docs/arquitetura-canonica-abas.md):
  - TODOS os registros de CRM Espirometria são exames reais. ID legado,
    data incompleta ou falta de lançamento financeiro NÃO tornam um
    registro teste.
  - Exames históricos sem lançamento financeiro precisam de backfill futuro,
    sem inventar valores.
  - Lançamentos sem vínculo por ID são "órfãos a reconciliar" — nunca
    apagados automaticamente.

Modos:
  --audit                Diagnóstico completo (padrão). Terminal protegido:
                         nomes/telefones aparecem só como hash.
  --dry-run              Auditoria + plano de ações proposto (sem gravar nada).
  --plan ARQUIVO.json    Grava o plano detalhado (PRIVADO — grave apenas em
                         painel-soprolife/data-private/, chmod 600).
  --export-safe-report ARQUIVO.txt
                         Relatório commitável: só contagens, IDs técnicos e
                         hashes — nunca nome, telefone, e-mail, CPF ou
                         observação.

Fontes de dados (uma é obrigatória):
  --fixtures DIR         Diretório com JSONs sintéticos (testes/simulação):
                         crm_espirometria.json, crm_consultas.json,
                         crm_pacientes.json, financeiro_lancamentos.json,
                         pastore_atendimentos.json — cada um lista de objetos.
  --from-adc             Lê as abas reais via ADC somente-leitura (mesmo padrão
                         dos conectores read-*-adc.py). Requer autorização do
                         operador; nada é gravado.

Exemplos:
  python3 painel-soprolife/scripts/reconciliar-historico.py --fixtures dir/ --audit
  python3 painel-soprolife/scripts/reconciliar-historico.py --from-adc --dry-run
  python3 painel-soprolife/scripts/reconciliar-historico.py --from-adc \
      --export-safe-report relatorio-reconciliacao.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from soprolife_normalizacao import (  # noqa: E402
    PRECISAO_DIA,
    chave_paciente,
    classificar_id,
    hash_protegido,
    nomes_compativeis,
    norm_telefone,
    norm_texto,
    normalizar_enum,
    parse_data_flex,
)

_CONFIG_PATH = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

ABAS_ADC = {
    "espirometria": "CRM Espirometria",
    "consultas": "CRM Consultas",
    "pacientes": "CRM Pacientes",
    "financeiro": "Financeiro_Lancamentos",
    "pastore": "Parceria Pastore - Atendimentos",
}

FIXTURE_FILES = {
    "espirometria": "crm_espirometria.json",
    "consultas": "crm_consultas.json",
    "pacientes": "crm_pacientes.json",
    "financeiro": "financeiro_lancamentos.json",
    "pastore": "pastore_atendimentos.json",
}

# Campos obrigatórios mínimos por fonte (ausência vira achado, nunca erro fatal).
CAMPOS_OBRIGATORIOS = {
    "espirometria": ["primeiro_nome", "status_exame"],
    "consultas": ["primeiro_nome", "status"],
    "pacientes": ["primeiro_nome"],
    "financeiro": ["data_exame", "valor_cobrado", "status_pagamento"],
}

# Padrões que NUNCA podem aparecer no relatório seguro (redundância final).
_CPF_RE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Carregamento de dados
# ---------------------------------------------------------------------------


def carregar_fixtures(diretorio: Path) -> dict:
    dados = {}
    for chave, nome in FIXTURE_FILES.items():
        caminho = diretorio / nome
        if caminho.exists():
            payload = json.loads(caminho.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError(f"{caminho}: esperado uma LISTA de objetos.")
            dados[chave] = payload
        else:
            dados[chave] = []
    return dados


def _rows_para_objetos(rows: list) -> list[dict]:
    """Linha 0 = cabeçalho → lista de dicts com chaves normalizadas
    (snake_case sem acento), igual aos conectores read-*-adc.py."""
    if not rows:
        return []
    headers = [norm_texto(h).replace(" ", "_") for h in rows[0]]
    objetos = []
    for row in rows[1:]:
        if not any(str(c).strip() for c in row):
            continue
        obj = {}
        for i, h in enumerate(headers):
            if h:
                obj[h] = str(row[i]).strip() if i < len(row) else ""
        objetos.append(obj)
    return objetos


def carregar_adc() -> dict:
    try:
        from googleapiclient.discovery import build
        from google.auth import default as auth_default
    except ImportError as exc:
        print(f"ERRO: dependências Google não instaladas — {exc}")
        sys.exit(1)
    if not _CONFIG_PATH.exists():
        print(f"ERRO: config não encontrada em {_CONFIG_PATH}")
        sys.exit(1)
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    sid = str(cfg.get("spreadsheet_id", "")).strip()
    if not sid:
        print("ERRO: spreadsheet_id ausente na configuração.")
        sys.exit(1)
    creds, _ = auth_default(scopes=[SHEETS_SCOPE])
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    dados = {}
    for chave, aba in ABAS_ADC.items():
        try:
            result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=sid, range=f"'{aba}'!A:Z")
                .execute()
            )
            dados[chave] = _rows_para_objetos(result.get("values", []))
            print(f"  lida: {aba!r} ({len(dados[chave])} registro(s))")
        except Exception as exc:  # aba ausente não é fatal
            msg = str(exc)
            if "404" in msg or "Unable to parse range" in msg:
                print(f"  aviso: aba {aba!r} não encontrada — tratada como vazia.")
                dados[chave] = []
            else:
                print(f"ERRO ao ler {aba!r}: {exc}")
                sys.exit(1)
    return dados


# ---------------------------------------------------------------------------
# Auditoria — funções puras sobre os dados carregados
# ---------------------------------------------------------------------------


def _campo(reg: dict, *nomes: str) -> str:
    for n in nomes:
        v = str(reg.get(n, "") or "").strip()
        if v:
            return v
    return ""


# Salt EFÊMERO por execução (M14.3A/MED-02): os hashes desta ferramenta só
# servem para correlacionar registros DENTRO do mesmo relatório. Hash de nome
# (baixa entropia) com salt fixo seria vulnerável a dicionário — com salt
# efêmero, o valor não é reproduzível fora desta execução. Hash de nome NUNCA
# é anonimização forte; por isso o relatório também avisa isso em texto.
_SALT_EXECUCAO = __import__("secrets").token_hex(16)


def _hp(valor, contexto: str = "reconciliacao") -> str:
    return hash_protegido(valor, f"{contexto}|{_SALT_EXECUCAO}")


def _id_protegido(rid) -> str:
    """NUNCA exibe um ID completo em relatório/plano (M14.3A, 2ª rodada):
    mesmo IDs reconhecidos (ESP/ESM/PAC/CON/FIN) podem carregar nome ou
    texto livre embutido (ex.: ESM-<nome>). Sai apenas a CATEGORIA do
    formato + prefixo + hash efêmero — suficiente para correlacionar dentro
    do mesmo relatório e localizar na planilha via busca autorizada."""
    v = str(rid or "").strip()
    if not v:
        return "(sem id)"
    info = classificar_id(v)
    prefixo = f"({info.prefixo})" if info.prefixo else ""
    return f"{info.formato}{prefixo} {_hp(v, 'id')}"


def _rotulo_protegido(reg: dict) -> str:
    """Identifica o registro sem expor PII: id protegido (categoria+hash) +
    hash efêmero do nome."""
    rid = _campo(reg, "exame_id", "consulta_id", "paciente_id", "id_lancamento", "lead_id")
    h = _hp(_campo(reg, "primeiro_nome", "nome", "paciente_nome"))
    return f"{_id_protegido(rid)} {h}"


def auditar(dados: dict) -> dict:
    """Retorna o resultado completo da auditoria. Nenhum campo do retorno
    contém nome/telefone em claro — apenas hashes, contagens e IDs técnicos."""
    espi = dados.get("espirometria", [])
    cons = dados.get("consultas", [])
    pacs = dados.get("pacientes", [])
    fin = dados.get("financeiro", [])
    pastore = dados.get("pastore", [])

    achados: dict = {
        "gerado_em": _agora_iso(),
        "contagens": {
            "exames_crm": len(espi),
            "consultas_crm": len(cons),
            "pacientes_crm": len(pacs),
            "lancamentos_financeiros": len(fin),
            "atendimentos_pastore": len(pastore),
        },
    }

    # ── Índices de pacientes: LISTAS de candidatos (nunca sets) ─────────────
    # Regras M14.3A (auditoria independente, BLOQ-03):
    #   - nome sozinho NUNCA vincula deterministicamente;
    #   - telefone gera apenas CANDIDATO (nunca prova absoluta);
    #   - telefone presente em mais de um cadastro = ambiguous;
    #   - candidato ainda não confirmado = pending;
    #   - sem informação suficiente = unmatchable;
    #   - linked SÓ por paciente_id explícito válido;
    #   - nenhuma linha é eliminada; nenhum merge é automático.
    por_tel: dict[str, list[dict]] = defaultdict(list)
    por_nome: dict[str, list[dict]] = defaultdict(list)
    ids_pacientes_existentes: set[str] = set()
    for p in pacs:
        tel = norm_telefone(_campo(p, "telefone", "telefone_whatsapp"))
        if tel:
            por_tel[tel].append(p)
        ch_nome = chave_paciente("", _campo(p, "primeiro_nome", "nome"))
        if ch_nome:
            por_nome[ch_nome].append(p)
        pid = _campo(p, "paciente_id")
        if pid:
            ids_pacientes_existentes.add(pid)

    def _classificar_evento(r: dict) -> dict:
        """Estado de reconciliação de um exame/consulta contra CRM Pacientes."""
        item: dict = {"registro": _rotulo_protegido(r)}
        pid = _campo(r, "paciente_id")
        if pid and pid in ids_pacientes_existentes:
            item.update(estado="linked",
                        motivo="paciente_id explícito encontrado em CRM Pacientes")
            return item
        if pid and pid not in ids_pacientes_existentes:
            # Vínculo ÓRFÃO: um ID explícito que não existe no cadastro é um
            # problema de integridade a INVESTIGAR — nunca "sem identificador"
            # (unmatchable) e nunca vínculo automático.
            item.update(
                estado="pending",
                vinculo="orphan_link",
                motivo="paciente_id informado não existe em CRM Pacientes — investigar vínculo órfão (decisão humana)")
            return item

        tel = norm_telefone(_campo(r, "telefone", "telefone_whatsapp"))
        ch_nome = chave_paciente("", _campo(r, "primeiro_nome", "nome"))

        if tel:
            candidatos = por_tel.get(tel, [])
            if len(candidatos) > 1:
                item.update(
                    estado="ambiguous",
                    motivo="telefone presente em mais de um cadastro — decisão humana",
                    candidatos=[_rotulo_protegido(c) for c in candidatos])
                return item
            if len(candidatos) == 1:
                item.update(
                    estado="pending",
                    motivo="candidato único por telefone — confirmar antes de vincular (telefone não é prova absoluta)",
                    candidatos=[_rotulo_protegido(candidatos[0])])
                return item
            # telefone sem correspondente: nome pode indicar candidato, mas
            # nome NUNCA vincula — só gera pendência de decisão humana.
            candidatos_nome = por_nome.get(ch_nome, []) if ch_nome else []
            if len(candidatos_nome) > 1:
                item.update(
                    estado="ambiguous",
                    motivo="sem telefone correspondente; nome compatível com mais de um cadastro",
                    candidatos=[_rotulo_protegido(c) for c in candidatos_nome])
                return item
            if len(candidatos_nome) == 1:
                item.update(
                    estado="pending",
                    motivo="nome bate com cadastro existente, mas o telefone não — completar telefone OU confirmar que é outra pessoa",
                    candidatos=[_rotulo_protegido(candidatos_nome[0])])
                return item
            item.update(estado="pending",
                        motivo="nenhum cadastro correspondente — avaliar criação (decisão humana)")
            return item

        if not ch_nome:
            item.update(estado="unmatchable",
                        motivo="sem telefone e sem nome — informação insuficiente para qualquer vínculo")
            return item

        candidatos_nome = por_nome.get(ch_nome, [])
        if len(candidatos_nome) > 1:
            item.update(
                estado="ambiguous",
                motivo="só nome disponível e compatível com mais de um cadastro (possíveis homônimos)",
                candidatos=[_rotulo_protegido(c) for c in candidatos_nome])
            return item
        if len(candidatos_nome) == 1:
            item.update(
                estado="pending",
                motivo="só nome disponível — nome nunca vincula sozinho; confirmar com dado adicional",
                candidatos=[_rotulo_protegido(candidatos_nome[0])])
            return item
        item.update(estado="pending",
                    motivo="nenhum cadastro correspondente (só nome disponível) — avaliar criação")
        return item

    def _cobertura(registros: list[dict]) -> dict:
        estados = {"linked": [], "pending": [], "ambiguous": [], "unmatchable": []}
        for r in registros:
            item = _classificar_evento(r)
            estados[item["estado"]].append(item)
        return estados

    achados["cobertura_exames"] = _cobertura(espi)
    achados["cobertura_consultas"] = _cobertura(cons)

    # 2) Possíveis duplicidades em CRM Pacientes:
    #    a) mesmo telefone em linhas diferentes; b) nomes compatíveis.
    duplicidades = []
    por_tel: dict[str, list[dict]] = defaultdict(list)
    for p in pacs:
        tel = norm_telefone(_campo(p, "telefone", "telefone_whatsapp"))
        if tel:
            por_tel[tel].append(p)
    for tel, grupo in por_tel.items():
        if len(grupo) > 1:
            duplicidades.append({
                "tipo": "mesmo_telefone",
                "telefone_hash": _hp(tel, "telefone"),
                "registros": [_rotulo_protegido(p) for p in grupo],
                "decisao": "humana — fusão nunca é automática",
            })
    for i in range(len(pacs)):
        for j in range(i + 1, len(pacs)):
            a, b = pacs[i], pacs[j]
            tel_a = norm_telefone(_campo(a, "telefone", "telefone_whatsapp"))
            tel_b = norm_telefone(_campo(b, "telefone", "telefone_whatsapp"))
            if tel_a and tel_b and tel_a == tel_b:
                continue  # já coberto acima
            if nomes_compativeis(_campo(a, "primeiro_nome", "nome"), _campo(b, "primeiro_nome", "nome")):
                duplicidades.append({
                    "tipo": "nome_semelhante",
                    "registros": [_rotulo_protegido(a), _rotulo_protegido(b)],
                    "decisao": "humana — nome parecido NÃO prova mesma pessoa",
                })
    achados["possiveis_duplicidades_pacientes"] = duplicidades

    # 3/4) Registros sem paciente_id (coluna futura — hoje é esperado faltar).
    achados["exames_sem_paciente_id"] = sum(1 for r in espi if not _campo(r, "paciente_id"))
    achados["consultas_sem_paciente_id"] = sum(1 for r in cons if not _campo(r, "paciente_id"))

    # 5/6/7) Cruzamento exame ↔ lançamento por id_atendimento/exame_id.
    ids_exames = {_campo(r, "exame_id", "id_atendimento") for r in espi} - {""}
    fin_por_id: dict[str, list[dict]] = defaultdict(list)
    fin_sem_id = []
    for l in fin:
        ida = _campo(l, "id_atendimento")
        if ida:
            fin_por_id[ida].append(l)
        else:
            fin_sem_id.append(l)

    achados["exames_sem_lancamento"] = [{
        "exame": _rotulo_protegido(r),
        "acao_futura": "backfill autorizado — nunca inventar valor",
    } for r in espi if _campo(r, "exame_id", "id_atendimento") not in fin_por_id]

    orfaos = [{
        "lancamento": _id_protegido(_campo(l, "id_lancamento")),
        "id_atendimento": _id_protegido(_campo(l, "id_atendimento")) if _campo(l, "id_atendimento") else "(vazio)",
        "data_exame": _campo(l, "data_exame") or "(vazia)",
        "estado": "orfao_a_reconciliar",
        "acao_futura": "vínculo assistido por data/valor — nunca apagar automaticamente",
    } for l in fin_sem_id]
    orfaos += [{
        "lancamento": _id_protegido(_campo(l, "id_lancamento")),
        "id_atendimento": _id_protegido(ida),
        "data_exame": _campo(l, "data_exame") or "(vazia)",
        "estado": "orfao_a_reconciliar",
        "acao_futura": "id_atendimento não existe no CRM — investigar correspondência",
    } for ida, ls in fin_por_id.items() if ida not in ids_exames for l in ls]
    achados["lancamentos_orfaos"] = orfaos

    achados["lancamentos_duplicados"] = [{
        "id_atendimento": _id_protegido(ida),
        "quantidade": len(ls),
        "nota": "upsert deveria impedir — verificar linhas manuais",
    } for ida, ls in fin_por_id.items() if len(ls) > 1]

    # 8) IDs: classificação por formato (contagem) + irregulares/ausentes.
    def _ids_stats(registros: list[dict], campos_id: tuple[str, ...]) -> dict:
        stats: dict[str, int] = defaultdict(int)
        irregulares = []
        for r in registros:
            info = classificar_id(_campo(r, *campos_id))
            stats[info.formato] += 1
            if info.formato in ("irregular", "ausente"):
                irregulares.append(_rotulo_protegido(r))
        return {"por_formato": dict(stats), "atencao": irregulares}

    achados["ids_exames"] = _ids_stats(espi, ("exame_id", "id_atendimento"))
    achados["ids_consultas"] = _ids_stats(cons, ("consulta_id",))
    achados["ids_pacientes"] = _ids_stats(pacs, ("paciente_id",))
    achados["ids_lancamentos"] = _ids_stats(fin, ("id_lancamento",))

    # 9) Datas: incompletas (precisão < dia) e inválidas — sem imprimir valores.
    def _datas_stats(registros: list[dict], campo: str) -> dict:
        incompletas, invalidas = [], []
        for r in registros:
            bruto = _campo(r, campo)
            if not bruto:
                continue
            d = parse_data_flex(bruto)
            if not d.valida:
                invalidas.append({"registro": _rotulo_protegido(r), "campo": campo})
            elif d.precisao != PRECISAO_DIA:
                incompletas.append({
                    "registro": _rotulo_protegido(r),
                    "campo": campo,
                    "precisao": d.precisao,
                    "acao_futura": f"registrar data_precisao={d.precisao}; nunca inventar o dia",
                })
        return {"incompletas": incompletas, "invalidas": invalidas}

    achados["datas_exames"] = _datas_stats(espi, "data_exame")
    achados["datas_consultas"] = _datas_stats(cons, "data_consulta")
    achados["datas_lancamentos"] = _datas_stats(fin, "data_exame")

    # 10) Enums despadronizados: valor equivalente por alias (sugestão) ou
    #     completamente desconhecido (decisão humana).
    def _enum_stats(registros: list[dict], campo: str, dominio: str) -> list[dict]:
        problemas = []
        for r in registros:
            bruto = _campo(r, campo)
            if not bruto:
                continue
            res = normalizar_enum(dominio, bruto)
            if res.via == "exato":
                continue
            if res.via == "alias":
                sugestao, decisao = res.canonico, "migração autorizada (equivalência lexical)"
            elif res.via == "decisao_manual":
                # Mudança de significado/estágio/local/consentimento/resultado:
                # o candidato NUNCA vira sugestão automática de lote.
                sugestao, decisao = None, f"humana — candidato provável: {res.canonico} (nunca aplicar em lote)"
            else:
                sugestao, decisao = None, "humana — valor desconhecido"
            problemas.append({
                "registro": _rotulo_protegido(r),
                "campo": campo,
                "valor_hash": _hp(bruto, "enum"),
                "sugestao": sugestao,
                "decisao": decisao,
            })
        return problemas

    achados["enums_despadronizados"] = (
        _enum_stats(espi, "status_exame", "status_exame")
        + _enum_stats(espi, "consentimento_whatsapp", "consentimento_whatsapp")
        + _enum_stats(fin, "status_pagamento", "status_pagamento")
        + _enum_stats(fin, "status_exame", "status_exame")
        + _enum_stats(fin, "local_atendimento", "local_atendimento")
        + _enum_stats(pacs, "consentimento_whatsapp", "consentimento_whatsapp")
    )

    # 11) Pastore: um atendimento de staging só é "integrado" quando tem
    #     id_atendimento presente no histórico central. Nome/telefone
    #     compatível NUNCA é cobertura — vira apenas CANDIDATO anotado para
    #     decisão humana (2ª auditoria, MÉDIO-02).
    fora_do_historico = []
    for i, r in enumerate(pastore):
        ida = _campo(r, "id_atendimento")
        if ida and ida in ids_exames:
            continue  # vínculo explícito confirmado
        tel = norm_telefone(_campo(r, "paciente_whatsapp"))
        ch_nome = chave_paciente("", _campo(r, "paciente_nome"))
        candidatos = []
        for e in espi:
            tel_e = norm_telefone(_campo(e, "telefone"))
            if tel and tel_e and tel == tel_e:
                candidatos.append({"exame": _rotulo_protegido(e), "por": "telefone"})
            elif ch_nome and chave_paciente("", _campo(e, "primeiro_nome")) == ch_nome:
                candidatos.append({"exame": _rotulo_protegido(e), "por": "nome (nunca prova)"})
        fora_do_historico.append({
            "atendimento": f"linha-{i + 2}",  # +2: cabeçalho + índice 1-based
            "data_atendimento": _campo(r, "data_atendimento") or "(vazia)",
            "paciente_hash": _hp(_campo(r, "paciente_nome")),
            "candidatos": candidatos,
            "acao_futura": "criar registro canônico em CRM Espirometria (local=Parceiro, parceiro=Pastore) via migração autorizada; candidatos exigem confirmação humana",
        })
    achados["pastore_fora_do_historico"] = fora_do_historico

    # 12) Campos obrigatórios ausentes.
    faltantes = []
    for chave_fonte, campos in CAMPOS_OBRIGATORIOS.items():
        for r in dados.get(chave_fonte, []):
            vazios = [c for c in campos if not _campo(r, c)]
            if vazios:
                faltantes.append({
                    "fonte": chave_fonte,
                    "registro": _rotulo_protegido(r),
                    "campos": vazios,
                })
    achados["campos_obrigatorios_ausentes"] = faltantes

    # 13) Valores financeiros ausentes (nunca preencher — só sinalizar).
    achados["valores_financeiros_ausentes"] = [{
        "lancamento": _id_protegido(_campo(l, "id_lancamento")),
        "campo": campo,
    } for l in fin for campo in ("valor_cobrado", "valor_recebido")
        if str(l.get(campo, "") or "").strip() == ""]

    # 14) Divergência CRM × financeiro para o mesmo id_atendimento.
    espi_por_id = {_campo(r, "exame_id", "id_atendimento"): r for r in espi if _campo(r, "exame_id", "id_atendimento")}
    divergencias = []
    for ida, ls in fin_por_id.items():
        r = espi_por_id.get(ida)
        if not r:
            continue
        for l in ls:
            se_crm = norm_texto(_campo(r, "status_exame"))
            se_fin = norm_texto(_campo(l, "status_exame"))
            if se_crm and se_fin and se_crm != se_fin:
                divergencias.append({
                    "id_atendimento": _id_protegido(ida),
                    "campo": "status_exame",
                    "nota": "CRM e financeiro discordam — conferência humana",
                })
            de_crm = parse_data_flex(_campo(r, "data_exame"))
            de_fin = parse_data_flex(_campo(l, "data_exame"))
            if de_crm.iso and de_fin.iso and de_crm.iso != de_fin.iso:
                divergencias.append({
                    "id_atendimento": _id_protegido(ida),
                    "campo": "data_exame",
                    "nota": "datas divergem entre CRM e financeiro",
                })
    achados["divergencias_crm_financeiro"] = divergencias

    return achados


# ---------------------------------------------------------------------------
# Plano dry-run — propostas, nunca ações
# ---------------------------------------------------------------------------


def montar_plano(achados: dict) -> dict:
    """Transforma os achados em um plano proposto. Toda ação nasce com
    aplicar=false e exige autorização explícita futura por item."""
    acoes = []

    # Cobertura de pacientes — estados explícitos, nenhum paciente criado e
    # nenhum vínculo aplicado automaticamente:
    #   pending com candidato  → confirmar_vinculo_paciente (decisão humana);
    #   pending sem candidato  → avaliar_criacao_paciente (decisão humana);
    #   ambiguous              → resolver_ambiguidade (decisão humana);
    #   unmatchable            → coletar_dados_minimos (fora do sistema);
    #   linked                 → nenhuma ação.
    for fonte in ("cobertura_exames", "cobertura_consultas"):
        cobertura = achados.get(fonte, {})
        for item in cobertura.get("pending", []):
            if item.get("candidatos"):
                acoes.append({
                    "acao": "confirmar_vinculo_paciente",
                    "origem": item.get("registro"),
                    "candidatos": item.get("candidatos"),
                    "detalhe": item.get("motivo"),
                    "aplicar": False,
                    "requer": "decisão humana — candidato nunca vira vínculo sozinho",
                })
            else:
                acoes.append({
                    "acao": "avaliar_criacao_paciente",
                    "origem": item.get("registro"),
                    "detalhe": "nenhum cadastro correspondente — avaliar criação em CRM Pacientes com os dados reais",
                    "aplicar": False,
                    "requer": "decisão humana",
                })
        for item in cobertura.get("ambiguous", []):
            acoes.append({
                "acao": "resolver_ambiguidade",
                "origem": item.get("registro"),
                "candidatos": item.get("candidatos"),
                "detalhe": item.get("motivo"),
                "aplicar": False,
                "requer": "decisão humana — mais de um candidato possível",
            })
        for item in cobertura.get("unmatchable", []):
            acoes.append({
                "acao": "coletar_dados_minimos",
                "origem": item.get("registro"),
                "detalhe": "sem telefone e sem nome — completar dados na origem antes de qualquer vínculo",
                "aplicar": False,
                "requer": "decisão humana",
            })

    for dup in achados.get("possiveis_duplicidades_pacientes", []):
        acoes.append({
            "acao": "avaliar_fusao_pacientes",
            "registros": dup.get("registros"),
            "tipo": dup.get("tipo"),
            "aplicar": False,
            "requer": "decisão humana — fusão nunca é automática",
        })

    for item in achados.get("exames_sem_lancamento", []):
        acoes.append({
            "acao": "backfill_financeiro",
            "exame": item.get("exame"),
            "detalhe": "criar lançamento futuro com valores REAIS informados pelo usuário — nunca inventar",
            "aplicar": False,
            "requer": "valores confirmados pelo usuário",
        })

    for item in achados.get("lancamentos_orfaos", []):
        acoes.append({
            "acao": "reconciliar_lancamento_orfao",
            "lancamento": item.get("lancamento"),
            "detalhe": "propor vínculo assistido por data/valor com exame candidato; nunca apagar",
            "aplicar": False,
            "requer": "confirmação humana do vínculo",
        })

    for item in achados.get("enums_despadronizados", []):
        if item.get("sugestao"):
            acoes.append({
                "acao": "padronizar_enum",
                "registro": item.get("registro"),
                "campo": item.get("campo"),
                "para": item.get("sugestao"),
                "aplicar": False,
                "requer": "migração autorizada em lote",
            })

    for fonte in ("datas_exames", "datas_consultas", "datas_lancamentos"):
        for item in achados.get(fonte, {}).get("incompletas", []):
            acoes.append({
                "acao": "registrar_precisao_data",
                "registro": item.get("registro"),
                "campo": item.get("campo"),
                "precisao": item.get("precisao"),
                "aplicar": False,
                "requer": "coluna data_precisao criada na migração",
            })

    for item in achados.get("pastore_fora_do_historico", []):
        acoes.append({
            "acao": "integrar_pastore_ao_historico",
            "atendimento": item.get("atendimento"),
            "detalhe": "gerar id_atendimento + linha canônica em CRM Espirometria + lançamento em Financeiro_Lancamentos",
            "aplicar": False,
            "requer": "migração autorizada",
        })

    return {
        "gerado_em": _agora_iso(),
        "modo": "dry-run — NENHUMA ação foi aplicada",
        "total_acoes_propostas": len(acoes),
        "acoes": acoes,
    }


# ---------------------------------------------------------------------------
# Saídas
# ---------------------------------------------------------------------------


def _linha(rotulo: str, valor) -> str:
    return f"  {rotulo:<46s} {valor}"


def render_relatorio(achados: dict, plano: dict | None) -> str:
    c = achados["contagens"]
    linhas = [
        "SoproLife — Reconciliação histórica (somente leitura)",
        f"Gerado em: {achados['gerado_em']}",
        "",
        "── Contagens ───────────────────────────────────────────────",
        _linha("Exames (CRM Espirometria):", c["exames_crm"]),
        _linha("Consultas (CRM Consultas):", c["consultas_crm"]),
        _linha("Pacientes (CRM Pacientes):", c["pacientes_crm"]),
        _linha("Lançamentos (Financeiro_Lancamentos):", c["lancamentos_financeiros"]),
        _linha("Atendimentos (Pastore - staging):", c["atendimentos_pastore"]),
        "",
        "── Cobertura de pacientes (estados de reconciliação) ───────",
        _linha("Exames — linked / pending / ambiguous / unmatchable:",
               " / ".join(str(len(achados["cobertura_exames"][e]))
                          for e in ("linked", "pending", "ambiguous", "unmatchable"))),
        _linha("Consultas — linked / pending / ambiguous / unmatchable:",
               " / ".join(str(len(achados["cobertura_consultas"][e]))
                          for e in ("linked", "pending", "ambiguous", "unmatchable"))),
        _linha("Possíveis duplicidades de paciente:", len(achados["possiveis_duplicidades_pacientes"])),
        _linha("Exames sem paciente_id (coluna futura):", achados["exames_sem_paciente_id"]),
        _linha("Consultas sem paciente_id (coluna futura):", achados["consultas_sem_paciente_id"]),
        "",
        "── CRM × Financeiro ────────────────────────────────────────",
        _linha("Exames sem lançamento (backfill futuro):", len(achados["exames_sem_lancamento"])),
        _linha("Lançamentos órfãos (nunca apagar):", len(achados["lancamentos_orfaos"])),
        _linha("Lançamentos duplicados por id:", len(achados["lancamentos_duplicados"])),
        _linha("Divergências CRM × financeiro:", len(achados["divergencias_crm_financeiro"])),
        _linha("Valores financeiros ausentes:", len(achados["valores_financeiros_ausentes"])),
        "",
        "── Padronização ────────────────────────────────────────────",
        _linha("IDs de exame por formato:", json.dumps(achados["ids_exames"]["por_formato"], ensure_ascii=False)),
        _linha("IDs irregulares/ausentes (exames):", len(achados["ids_exames"]["atencao"])),
        _linha("Datas incompletas (exames):", len(achados["datas_exames"]["incompletas"])),
        _linha("Datas inválidas (exames):", len(achados["datas_exames"]["invalidas"])),
        _linha("Enums fora do padrão canônico:", len(achados["enums_despadronizados"])),
        _linha("Campos obrigatórios ausentes:", len(achados["campos_obrigatorios_ausentes"])),
        "",
        "── Pastore ─────────────────────────────────────────────────",
        _linha("Atendimentos fora do histórico central:", len(achados["pastore_fora_do_historico"])),
    ]
    if plano is not None:
        linhas += [
            "",
            "── Plano proposto (dry-run — nada foi aplicado) ────────────",
            _linha("Total de ações propostas:", plano["total_acoes_propostas"]),
        ]
        por_acao: dict[str, int] = defaultdict(int)
        for a in plano["acoes"]:
            por_acao[a["acao"]] += 1
        for acao, qtd in sorted(por_acao.items()):
            linhas.append(_linha(f"  {acao}:", qtd))
    linhas += [
        "",
        "Privacidade: nomes/telefones aparecem apenas como hash (h:...) com",
        "salt EFÊMERO desta execução — hash de nome não é anonimização forte;",
        "a correlação só vale dentro deste relatório.",
        "Nenhum dado foi gravado ou alterado por esta execução.",
    ]
    return "\n".join(linhas)


# Tokens TÉCNICOS de formato fechado que os scans de PII podem confundir com
# telefone/CPF (dígitos de UUID, hash hex, chave de idempotência do
# navegador). São redigidos ANTES do scan — um telefone/CPF real não casa com
# nenhum destes formatos e continua sendo detectado.
_TOKENS_TECNICOS_RE = re.compile(
    r"h:[0-9a-f]{6,}"                                              # hash protegido
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # UUID
    r"|\b[A-Z]{3,4}-\d{8}-\d{6}-[A-Z0-9]{4,8}\b"                   # chave navegador
)


# ID cru com TEXTO LIVRE embutido (ex.: ESM-PESSOA-SINTETICA): um segmento
# de 4+ letras depois de um prefixo tipo-ID sugere nome/observação dentro do
# identificador — nunca pode sair em claro (os relatórios só emitem IDs
# protegidos por categoria+hash).
_ID_COM_TEXTO_RE = re.compile(r"\b[A-Z]{2,6}-[A-Za-z0-9-]*[A-Za-zÀ-ÿ]{4,}")


def validar_saida_segura(texto: str) -> list[str]:
    limpo = _TOKENS_TECNICOS_RE.sub("[token-tecnico]", texto)
    problemas = []
    if _CPF_RE.search(limpo):
        problemas.append("padrão de CPF na saída")
    if _FONE_RE.search(limpo):
        problemas.append("padrão de telefone na saída")
    if _EMAIL_RE.search(limpo):
        problemas.append("padrão de e-mail na saída")
    if _ID_COM_TEXTO_RE.search(limpo):
        problemas.append("ID com possível texto/nome embutido na saída (IDs devem sair protegidos)")
    return problemas


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconciliação histórica SoproLife (somente leitura) — M14.3")
    fonte = parser.add_mutually_exclusive_group(required=True)
    fonte.add_argument("--fixtures", metavar="DIR", help="diretório com JSONs sintéticos")
    fonte.add_argument("--from-adc", action="store_true", help="lê as abas reais via ADC somente-leitura")
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--audit", action="store_true", help="diagnóstico completo (padrão)")
    modo.add_argument("--dry-run", action="store_true", help="auditoria + plano proposto")
    modo.add_argument("--plan", metavar="ARQ.json", help="grava o plano detalhado (arquivo PRIVADO)")
    modo.add_argument("--export-safe-report", metavar="ARQ.txt", help="relatório commitável sem PII")
    args = parser.parse_args()

    print("SoproLife — Reconciliação histórica (M14.3)")
    print("Garantia: esta ferramenta NUNCA escreve na planilha nem altera dados.")
    print()

    if args.fixtures:
        dados = carregar_fixtures(Path(args.fixtures))
        print(f"Fonte: fixtures em {args.fixtures}")
    else:
        print("Fonte: Google Sheets via ADC (somente leitura)")
        dados = carregar_adc()
    print()

    achados = auditar(dados)
    precisa_plano = args.dry_run or args.plan or args.export_safe_report
    plano = montar_plano(achados) if precisa_plano else None

    if args.plan:
        # M14.3A/MED-02 — o plano inteiro passa pela guarda de PII ANTES de
        # qualquer escrita: mesmo sendo arquivo privado (600), nenhum nome,
        # telefone, e-mail ou CPF pode chegar ao disco.
        conteudo_plano = json.dumps({"achados": achados, "plano": plano},
                                    ensure_ascii=False, indent=2) + "\n"
        problemas_plano = validar_saida_segura(conteudo_plano)
        if problemas_plano:
            print("ERRO: o plano reprovou na checagem de PII — NADA foi gravado:")
            for p in problemas_plano:
                print(f"  - {p}")
            return 1
        destino = Path(args.plan)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(conteudo_plano, encoding="utf-8")
        destino.chmod(0o600)
        print(f"Plano gravado (600): {destino}")
        print("Lembrete: mantenha este arquivo em painel-soprolife/data-private/ (nunca commitar).")
        print("Hashes usam salt efêmero desta execução — não são anonimização forte; "
              "a correlação só vale dentro deste arquivo.")
        return 0

    relatorio = render_relatorio(achados, plano)
    problemas = validar_saida_segura(relatorio)
    if problemas:
        print("ERRO: a saída reprovou na checagem de PII — nada foi exportado:")
        for p in problemas:
            print(f"  - {p}")
        return 1

    if args.export_safe_report:
        destino = Path(args.export_safe_report)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(relatorio + "\n", encoding="utf-8")
        print(f"Relatório seguro gravado: {destino}")
        return 0

    print(relatorio)
    return 0


if __name__ == "__main__":
    sys.exit(main())
