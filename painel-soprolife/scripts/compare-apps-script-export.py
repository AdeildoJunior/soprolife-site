#!/usr/bin/env python3
"""SoproLife — M14.3A.2 — comparador: exportação remota × manifesto Git.

Compara um diretório com a exportação MANUAL do projeto Apps Script remoto
(contrato: core/contracts/apps-script-remote-export.schema.json) contra o
manifesto canônico gerado do Git. 100% offline: nunca acessa Google, rede,
credenciais ou data-private.

USO:
  python3 compare-apps-script-export.py \
    --remote-dir DIRETORIO \
    [--manifest ARQUIVO] [--report ARQUIVO] [--json-report ARQUIVO] \
    [--strict] [--allow-extra NOME]... [--no-write]

RESULTADO:
  READY   — os 9 canônicos idênticos byte a byte, nada bloqueante.
  BLOCKED — qualquer achado bloqueante (ausente, divergente, extra não
            permitido, global duplicado, símbolo proibido, operação
            destrutiva, segredo suspeito, nome antigo, .gs.gs).

EXIT CODES:
  0 = READY (sem --strict, avisos são tolerados)
  1 = erro de uso/entrada/validação (argumentos inválidos, --remote-dir ou
      --manifest inexistentes, remote-inventory.json que não passa no
      contrato core/contracts/apps-script-remote-export.schema.json)
  2 = BLOCKED (divergência real remoto × Git)
  3 = READY com avisos, mas --strict ativo (qualquer divergência falha)
"""

import argparse
import json
import os
import sys

sys.dont_write_bytecode = True  # nenhum bytecode no repositório
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apps_script_parity as parity  # noqa: E402

RAIZ_REPO = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
MANIFESTO_PADRAO = os.path.join(
    RAIZ_REPO, "painel-soprolife", "core", "contracts",
    "apps-script-publication-manifest.json",
)
SCHEMA_REMOTO_PADRAO = os.path.join(
    RAIZ_REPO, "painel-soprolife", "core", "contracts",
    "apps-script-remote-export.schema.json",
)


class _ArgumentParserSaida1(argparse.ArgumentParser):
    """argparse sai com 2 por padrão em erro de uso — aqui 2 é BLOCKED.

    Sobrescreve para sair com 1 (erro de uso/entrada/validação), conforme
    o contrato de exit codes deste script.
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: erro: {message}\n")

BLOQUEIO = "bloqueio"
AVISO = "aviso"
INFO = "info"


def achado(achados, codigo, severidade, arquivo, detalhe):
    achados.append({
        "codigo": codigo,
        "severidade": severidade,
        "arquivo": arquivo,
        "detalhe": detalhe,
    })


def carregar_remoto(remote_dir, achados):
    """Lê o diretório exportado. Retorna dict nome→{bytes, analise...}."""
    arquivos = {}
    extras_nao_codigo = {}
    for nome in sorted(os.listdir(remote_dir)):
        caminho = os.path.join(remote_dir, nome)
        if os.path.isdir(caminho):
            achado(achados, "SUBDIRETORIO_IGNORADO", AVISO, nome,
                   "subdiretório ignorado — a exportação deve ser plana")
            continue
        if nome in ("appsscript.json", "remote-inventory.json"):
            extras_nao_codigo[nome] = caminho
            continue
        if not (nome.endswith(".gs") or nome.endswith(".js")):
            achado(achados, "ARQUIVO_IGNORADO", AVISO, nome,
                   "não é .gs/.js nem appsscript.json/remote-inventory.json")
            continue
        with open(caminho, "rb") as f:
            dados = f.read()
        try:
            analise = parity.analisar_fonte(dados)
        except UnicodeDecodeError:
            achado(achados, "ARQUIVO_NAO_UTF8", BLOQUEIO, nome,
                   "arquivo de código não é UTF-8 válido")
            continue
        logico = parity.nome_logico_remoto(nome)
        arquivos[nome] = {
            "dados": dados,
            "analise": analise,
            "nome_logico": logico,
        }
        if logico.endswith(".gs") or logico.endswith(".js"):
            achado(achados, "EXTENSAO_GS_GS", BLOQUEIO, nome,
                   f"extensão dupla — nome lógico resultante '{logico}' "
                   "indica arquivo antigo/duplicado")
    return arquivos, extras_nao_codigo


def validar_remote_inventory(caminho, schema_remoto):
    """Valida remote-inventory.json contra o contrato ANTES de qualquer uso.

    Nunca deve ser possível chegar a READY (ou BLOCKED) com um inventário
    que não passou nesta validação. Retorna (inventario, erros):
      - inválido  -> (None, [mensagens de erro])
      - válido    -> (dict, [])
    As mensagens de erro citam apenas caminho/campo/tipo — nunca o valor
    real do dado, para não reproduzir conteúdo potencialmente sensível.
    """
    try:
        with open(caminho, encoding="utf-8") as f:
            texto = f.read()
    except UnicodeDecodeError:
        return None, ["arquivo não é UTF-8 válido"]
    try:
        inventario = json.loads(texto)
    except json.JSONDecodeError as e:
        return None, [f"JSON inválido (linha {e.lineno}, coluna {e.colno})"]
    schema_inventario = schema_remoto.get("properties", {}).get("remote_inventory", {})
    erros = parity.validar_schema(inventario, schema_inventario, "remote_inventory")
    if parity.varrer_segredos(texto):
        erros.append("conteúdo casa padrão de segredo/credencial/URL privada — "
                      "proibido neste arquivo")
    if erros:
        return None, erros
    return inventario, []


def comparar(manifesto, remote_dir, allow_extra, inventario_validado=None):
    achados = []
    arquivos_remotos, extras_nao_codigo = carregar_remoto(remote_dir, achados)

    canonicos = {a["nome_apps_script"]: a for a in manifesto["arquivos"]}
    logico_para_canonico = {
        parity.nome_logico_remoto(n): n for n in canonicos
    }
    regras = manifesto["regras_publicacao"]

    identicos = []
    ausentes = []
    divergentes = []
    extras = []

    # Mapeia remoto por nome lógico (aceita .gs e .js).
    remoto_por_logico = {}
    for nome, info in arquivos_remotos.items():
        remoto_por_logico.setdefault(info["nome_logico"], []).append(nome)

    for logico, nomes in sorted(remoto_por_logico.items()):
        if len(nomes) > 1:
            achado(achados, "NOME_LOGICO_DUPLICADO", BLOQUEIO, ", ".join(nomes),
                   f"mais de um arquivo remoto com o nome lógico '{logico}'")

    # 1) Canônicos: presença e hash byte a byte.
    for nome_canonico in [a["nome_apps_script"] for a in manifesto["arquivos"]]:
        logico = parity.nome_logico_remoto(nome_canonico)
        nomes_remotos = remoto_por_logico.get(logico, [])
        if not nomes_remotos:
            ausentes.append(nome_canonico)
            achado(achados, "CANONICO_AUSENTE", BLOQUEIO, nome_canonico,
                   "arquivo canônico não encontrado na exportação remota")
            continue
        nome_remoto = nomes_remotos[0]
        info = arquivos_remotos[nome_remoto]
        sha_remoto = info["analise"]["sha256"]
        if sha_remoto == canonicos[nome_canonico]["sha256"]:
            identicos.append(nome_canonico)
            achado(achados, "ARQUIVO_IDENTICO", INFO, nome_remoto,
                   f"idêntico ao Git (sha256 {sha_remoto[:12]}…)")
        else:
            divergentes.append(nome_canonico)
            detalhe = (f"sha256 remoto {sha_remoto[:12]}… ≠ "
                       f"Git {canonicos[nome_canonico]['sha256'][:12]}…")
            normalizado = info["dados"].replace(b"\r\n", b"\n")
            if parity.sha256_hex(normalizado) == canonicos[nome_canonico]["sha256"]:
                detalhe += " (difere APENAS em quebras de linha CRLF)"
            achado(achados, "HASH_DIVERGENTE", BLOQUEIO, nome_remoto, detalhe)

    # 2) Extras: todo arquivo remoto cujo nome lógico não é canônico.
    for nome, info in sorted(arquivos_remotos.items()):
        if info["nome_logico"] in logico_para_canonico:
            continue
        extras.append(nome)
        permitido = nome in allow_extra or info["nome_logico"] in allow_extra
        proibidos = parity.nome_arquivo_proibido(nome)
        if proibidos:
            achado(achados, "NOME_PROIBIDO", BLOQUEIO, nome,
                   "nome casa padrão de arquivo antigo/backup/cópia: "
                   + ", ".join(proibidos))
        elif permitido:
            achado(achados, "EXTRA_PERMITIDO", AVISO, nome,
                   "extra permitido via --allow-extra (ainda assim comparar "
                   "e remover antes da publicação final)")
        else:
            achado(achados, "EXTRA_NAO_PERMITIDO", BLOQUEIO, nome,
                   "arquivo remoto fora da lista canônica — exportar, "
                   "comparar e aprovar remoção antes de publicar")

    # 3) Globals de TODOS os arquivos remotos: duplicidade.
    donos = {}
    for nome, info in sorted(arquivos_remotos.items()):
        for simbolo in info["analise"]["globais"]:
            donos.setdefault(simbolo["nome"], []).append(nome)
    for simbolo, arquivos in sorted(donos.items()):
        if len(arquivos) < 2:
            continue
        codigo = ("ENTRYPOINT_DUPLICADO"
                  if simbolo in regras["entrypoints_unicos"]
                  else "GLOBAL_DUPLICADO")
        achado(achados, codigo, BLOQUEIO, ", ".join(arquivos),
               f"símbolo global '{simbolo}' definido em mais de um arquivo")

    # 4) Símbolos proibidos (implementações antigas) em qualquer arquivo.
    for nome, info in sorted(arquivos_remotos.items()):
        presentes = sorted(
            set(regras["simbolos_proibidos"]) & info["analise"]["identificadores"]
        )
        for s in presentes:
            achado(achados, "SIMBOLO_PROIBIDO", BLOQUEIO, nome,
                   f"símbolo de implementação antiga presente: {s}")

    # 5) Funções bloqueadas: definidas sem o marcador de bloqueio = reativação.
    for nome, info in sorted(arquivos_remotos.items()):
        definidos = set(s["nome"] for s in info["analise"]["globais"])
        idents = info["analise"]["identificadores"]
        for funcao, marcador in sorted(regras["funcoes_bloqueadas"].items()):
            if funcao in definidos and marcador not in idents:
                achado(achados, "FUNCAO_BLOQUEADA_REATIVADA", BLOQUEIO, nome,
                       f"'{funcao}' definida sem o marcador de bloqueio "
                       f"{marcador} — versão antiga/ativa")

    # 6) Operações de escrita em arquivos NÃO idênticos ao Git.
    identicos_remotos = set()
    for nome, info in arquivos_remotos.items():
        canonico = logico_para_canonico.get(info["nome_logico"])
        if canonico and info["analise"]["sha256"] == canonicos[canonico]["sha256"]:
            identicos_remotos.add(nome)
    for nome, info in sorted(arquivos_remotos.items()):
        if nome in identicos_remotos:
            continue
        ops = info["analise"]["operacoes_escrita"]
        destrutivas = sorted(set(ops) & set(regras["operacoes_destrutivas"]))
        outras = sorted(set(ops) - set(regras["operacoes_destrutivas"]))
        if destrutivas:
            achado(achados, "OPERACAO_DESTRUTIVA", BLOQUEIO, nome,
                   "operações destrutivas em arquivo não aprovado: "
                   + ", ".join(f"{o}×{ops[o]}" for o in destrutivas))
        if outras:
            achado(achados, "OPERACAO_ESCRITA_EXTRA", AVISO, nome,
                   "operações de escrita em arquivo não aprovado: "
                   + ", ".join(f"{o}×{ops[o]}" for o in outras))

    # 7) Segredos/credenciais em qualquer arquivo do diretório exportado.
    # O conteúdo interno de literais regex é mascarado antes da varredura
    # (mascarar_literais_regex) para que texto com cara de segredo dentro
    # de um regex (ex.: /apiToken="x"/, usado em validação de formato) não
    # dispare falso positivo — uma atribuição real (const apiToken = "x")
    # continua detectável, pois strings fora de regex não são mascaradas.
    for nome, info in sorted(arquivos_remotos.items()):
        texto_para_varredura = parity.mascarar_literais_regex(
            info["dados"].decode("utf-8")
        )
        padroes = parity.varrer_segredos(texto_para_varredura)
        if padroes:
            achado(achados, "SEGREDO_SUSPEITO", BLOQUEIO, nome,
                   f"{len(padroes)} padrão(ões) com cara de segredo/URL "
                   "privada — nunca trazer para Git/pacote")

    # 8) appsscript.json (informativo) e remote-inventory.json (já validado
    # contra o contrato em main() antes desta função ser chamada — nunca é
    # reanalisado/reparseado aqui).
    inventario = inventario_validado
    if "appsscript.json" in extras_nao_codigo:
        try:
            with open(extras_nao_codigo["appsscript.json"], encoding="utf-8") as f:
                aj = json.load(f)
            resumo = (f"timeZone={aj.get('timeZone', '?')}, "
                      f"runtime={aj.get('runtimeVersion', '?')}, "
                      f"webapp={'sim' if 'webapp' in aj else 'não'}")
            achado(achados, "APPSSCRIPT_JSON", INFO, "appsscript.json", resumo)
            if parity.varrer_segredos(json.dumps(aj)):
                achado(achados, "SEGREDO_SUSPEITO", BLOQUEIO,
                       "appsscript.json", "conteúdo com cara de segredo")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            achado(achados, "APPSSCRIPT_JSON_INVALIDO", AVISO,
                   "appsscript.json", f"JSON inválido: {e}")
    if "remote-inventory.json" in extras_nao_codigo:
        # Já validado contra o contrato antes de comparar() ser chamada —
        # se chegou aqui, inventario é garantidamente um dict válido.
        achado(achados, "REMOTE_INVENTORY", INFO, "remote-inventory.json",
               f"triggers={len(inventario.get('triggers', []))}, "
               f"propriedades={len(inventario.get('propriedades_do_script_nomes', []))}, "
               f"implantacao={'sim' if inventario.get('implantacao') else 'não'}")

    bloqueios = [a for a in achados if a["severidade"] == BLOQUEIO]
    avisos = [a for a in achados if a["severidade"] == AVISO]
    resultado = "READY" if not bloqueios else "BLOCKED"

    risco_coexistencia = any(
        a["codigo"] in ("GLOBAL_DUPLICADO", "ENTRYPOINT_DUPLICADO",
                        "NOME_PROIBIDO", "EXTENSAO_GS_GS",
                        "NOME_LOGICO_DUPLICADO", "SIMBOLO_PROIBIDO",
                        "FUNCAO_BLOQUEADA_REATIVADA")
        for a in achados
    )

    relatorio = {
        "relatorio": "compare-apps-script-export",
        "resultado": resultado,
        "risco_coexistencia": risco_coexistencia,
        "contagens": {
            "remotos_analisados": len(arquivos_remotos),
            "identicos": len(identicos),
            "ausentes": len(ausentes),
            "divergentes": len(divergentes),
            "extras": len(extras),
            "bloqueios": len(bloqueios),
            "avisos": len(avisos),
        },
        "identicos": identicos,
        "ausentes": ausentes,
        "divergentes": divergentes,
        "extras": extras,
        "globais_remotos": {
            nome: sorted(s["nome"] for s in info["analise"]["globais"])
            for nome, info in sorted(arquivos_remotos.items())
        },
        "entrypoints_remotos": {
            nome: sorted(e["nome"] for e in info["analise"]["entrypoints"])
            for nome, info in sorted(arquivos_remotos.items())
            if info["analise"]["entrypoints"]
        },
        "remote_inventory_presente": inventario is not None,
        "achados": achados,
    }
    return relatorio


def formatar_texto(relatorio):
    linhas = []
    add = linhas.append
    add("══ Comparação Apps Script remoto × manifesto Git (M14.3A.2) ══")
    add(f"RESULTADO: {relatorio['resultado']}")
    add(f"Risco de coexistência (duplicidade/arquivo antigo): "
        f"{'SIM' if relatorio['risco_coexistencia'] else 'não'}")
    c = relatorio["contagens"]
    add(f"Arquivos remotos analisados: {c['remotos_analisados']} | "
        f"idênticos: {c['identicos']}/9 | ausentes: {c['ausentes']} | "
        f"divergentes: {c['divergentes']} | extras: {c['extras']}")
    add("")
    for titulo, chave in (("Idênticos ao Git", "identicos"),
                          ("Canônicos AUSENTES", "ausentes"),
                          ("Hash DIVERGENTE", "divergentes"),
                          ("Extras remotos", "extras")):
        itens = relatorio[chave]
        add(f"— {titulo}: {', '.join(itens) if itens else '(nenhum)'}")
    add("")
    add("— Achados:")
    if not relatorio["achados"]:
        add("  (nenhum)")
    for a in relatorio["achados"]:
        add(f"  [{a['severidade'].upper():8s}] {a['codigo']:26s} "
            f"{a['arquivo']}: {a['detalhe']}")
    add("")
    add("— Entrypoints remotos:")
    for nome, eps in relatorio["entrypoints_remotos"].items():
        add(f"  {nome}: {', '.join(eps)}")
    if relatorio["resultado"] == "BLOCKED":
        add("")
        add("BLOCKED: NÃO montar pacote nem publicar. Resolver cada achado")
        add("de bloqueio (exportar/backup do remoto antes de remover extras).")
    else:
        add("")
        add("READY: paridade byte a byte dos 9 canônicos confirmada nesta")
        add("exportação. Conferir também triggers/propriedades/implantação")
        add("(remote-inventory.json) antes de publicar.")
    return "\n".join(linhas) + "\n"


def main():
    ap = _ArgumentParserSaida1(description=__doc__.splitlines()[0])
    ap.add_argument("--remote-dir", required=True,
                    help="diretório da exportação manual do projeto remoto")
    ap.add_argument("--manifest", default=MANIFESTO_PADRAO,
                    help="manifesto canônico (padrão: core/contracts)")
    ap.add_argument("--report", help="grava relatório texto neste arquivo")
    ap.add_argument("--json-report", help="grava relatório JSON neste arquivo")
    ap.add_argument("--strict", action="store_true",
                    help="qualquer divergência (inclusive aviso) ⇒ exit ≠ 0")
    ap.add_argument("--allow-extra", action="append", default=[],
                    metavar="NOME",
                    help="tolera arquivo remoto extra (repetível)")
    ap.add_argument("--no-write", action="store_true",
                    help="não grava --report/--json-report; só stdout")
    args = ap.parse_args()

    if not os.path.isdir(args.remote_dir):
        print(f"ERRO: --remote-dir não é diretório: {args.remote_dir}",
              file=sys.stderr)
        return 1
    if not os.path.isfile(args.manifest):
        print(f"ERRO: manifesto não encontrado: {args.manifest}",
              file=sys.stderr)
        return 1
    with open(args.manifest, encoding="utf-8") as f:
        manifesto = json.load(f)

    # remote-inventory.json é validado contra o contrato ANTES de qualquer
    # comparação — um inventário inválido nunca pode resultar em READY nem
    # em BLOCKED, só em erro de uso/validação (exit 1).
    inventario_validado = None
    caminho_inventario = os.path.join(args.remote_dir, "remote-inventory.json")
    if os.path.isfile(caminho_inventario):
        if not os.path.isfile(SCHEMA_REMOTO_PADRAO):
            print(f"ERRO: contrato não encontrado: {SCHEMA_REMOTO_PADRAO}",
                  file=sys.stderr)
            return 1
        with open(SCHEMA_REMOTO_PADRAO, encoding="utf-8") as f:
            schema_remoto = json.load(f)
        inventario_validado, erros = validar_remote_inventory(
            caminho_inventario, schema_remoto)
        if erros:
            print("ERRO: remote-inventory.json não passa no contrato "
                  "(core/contracts/apps-script-remote-export.schema.json):",
                  file=sys.stderr)
            for e in erros:
                print(f"  - {e}", file=sys.stderr)
            print("Nenhum relatório foi gerado. Corrija o arquivo e rode "
                  "novamente.", file=sys.stderr)
            return 1

    relatorio = comparar(manifesto, args.remote_dir, set(args.allow_extra),
                          inventario_validado)
    texto = formatar_texto(relatorio)
    print(texto, end="")

    if not args.no_write:
        if args.report:
            with open(args.report, "w", encoding="utf-8") as f:
                f.write(texto)
            print(f"(relatório texto: {args.report})")
        if args.json_report:
            with open(args.json_report, "w", encoding="utf-8") as f:
                json.dump(relatorio, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print(f"(relatório JSON: {args.json_report})")

    if relatorio["resultado"] == "BLOCKED":
        return 2
    if args.strict and relatorio["contagens"]["avisos"] > 0:
        print("--strict: avisos presentes ⇒ falha.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
