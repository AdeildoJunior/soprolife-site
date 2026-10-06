"""Hotfix — "Gerar acesso ao resultado" voltou a responder `portal_desabilitado`.

A causa não estava no portal, que seguia de pé e saudável: estava no deploy da
API interna. `deploy-producao-vps.sh` **reescreve** `/opt/soprolife/secrets/m15.env`
do zero a cada execução, e as três variáveis do portal não nascem ali — quem as
acrescenta ao mesmo arquivo é a etapa `segredos` de `deploy-portal-resultados.sh`:

    M15_PORTAL_ENABLED
    M15_PORTAL_PUBLIC_BASE_URL
    M15_PORTAL_TOKEN_KEY

Então todo deploy da API apagava as três, `portal_enabled` voltava ao default
`False` e `_exigir_portal()` passava a recusar a geração de link. Nada havia
sido desligado de propósito; a configuração morria por efeito colateral.

A chave de derivação é o que torna isto pior do que uma flag perdida:
`resolved_portal_token_key` não tem fallback efêmero *de propósito* — se ela
muda, todo link já entregue a paciente para de abrir, sem revogação nenhuma.
Por isso a correção COPIA as linhas em uso em vez de gerar outras, e o segundo
teste aqui prova que um estado parcial (M15_PORTAL_* sem a chave) faz a etapa
`segredos` ABORTAR em vez de "consertar" com uma chave nova.

Os testes rodam o código real dos scripts, extraído deles por marcador — não
uma cópia da lógica, que envelheceria sozinha.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

NUCLEO = Path(__file__).resolve().parents[1]
SCRIPTS = NUCLEO / "scripts"
DEPLOY_API = SCRIPTS / "deploy-producao-vps.sh"
DEPLOY_PORTAL = SCRIPTS / "deploy-portal-resultados.sh"

# Valores sintéticos. A "chave" é reconhecível para que o teste possa provar
# que ela foi copiada LETRA POR LETRA e que não apareceu em lugar nenhum além
# do arquivo de destino.
CHAVE = "0f" * 32
BASE_PUBLICA = "https://soprolife.com.br/resultados/"


def _extrair_funcao(texto: str, nome: str) -> str:
    """Recorta `nome() { ... }` do script, até o primeiro `}` na coluna 0."""

    inicio = texto.index(f"{nome}() {{")
    fim = texto.index("\n}\n", inicio) + len("\n}\n")
    return texto[inicio:fim]


def _env_interno(tmp_path: Path, *, com_portal: bool, com_chave: bool = True) -> Path:
    linhas = [
        "M15_ENV=prod",
        "M15_DATABASE_URL=postgresql+psycopg://u:p@127.0.0.1:5432/d",
        "M15_AUTH_SECRET=" + "a1" * 32,
        "M15_REPORTS_STORAGE_DIR=/opt/soprolife/laudos",
    ]
    if com_portal:
        linhas += [
            "M15_PORTAL_ENABLED=true",
            f"M15_PORTAL_PUBLIC_BASE_URL={BASE_PUBLICA}",
        ]
        if com_chave:
            linhas.append(f"M15_PORTAL_TOKEN_KEY={CHAVE}")
    alvo = tmp_path / "m15.env"
    alvo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return alvo


def _sudo_falso(tmp_path: Path) -> Path:
    """`sudo` que apenas executa o resto da linha — o script usa sudo para ler
    o EnvironmentFile, e o teste não tem (nem quer) privilégio nenhum."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    sudo = bin_dir / "sudo"
    sudo.write_text('#!/bin/bash\nexec "$@"\n', encoding="utf-8")
    sudo.chmod(0o755)
    return bin_dir


def _rodar_preservar(tmp_path: Path, env_file: Path) -> subprocess.CompletedProcess:
    funcao = _extrair_funcao(DEPLOY_API.read_text(encoding="utf-8"), "preservar_portal")
    script = tmp_path / "exercita.sh"
    script.write_text(
        "set -Eeuo pipefail\n"
        f'ENV_FILE="{env_file}"\n'
        f"{funcao}\n"
        "preservar_portal\n",
        encoding="utf-8",
    )
    return subprocess.run(
        ["/bin/bash", str(script)],
        capture_output=True,
        text=True,
        env={"PATH": f"{_sudo_falso(tmp_path)}:/usr/bin:/bin"},
        check=False,
    )


def test_preserva_as_tres_variaveis_do_portal_sem_alterar_a_chave(tmp_path):
    env_file = _env_interno(tmp_path, com_portal=True)
    r = _rodar_preservar(tmp_path, env_file)

    assert r.returncode == 0, r.stderr
    linhas = r.stdout.splitlines()
    assert linhas == [
        "M15_PORTAL_ENABLED=true",
        f"M15_PORTAL_PUBLIC_BASE_URL={BASE_PUBLICA}",
        f"M15_PORTAL_TOKEN_KEY={CHAVE}",
    ]


def test_nao_carrega_nada_que_nao_seja_do_portal(tmp_path):
    """O segredo administrativo e a URL do banco não podem vir de carona."""

    env_file = _env_interno(tmp_path, com_portal=True)
    r = _rodar_preservar(tmp_path, env_file)

    assert r.returncode == 0, r.stderr
    for nome in ("M15_AUTH_SECRET", "M15_DATABASE_URL", "M15_ENV="):
        assert nome not in r.stdout
    assert all(linha.startswith("M15_PORTAL_") for linha in r.stdout.splitlines())


def test_sem_portal_configurado_a_copia_e_vazia_e_nao_falha(tmp_path):
    """Instalação sem portal segue funcionando — e o deploy não aborta."""

    env_file = _env_interno(tmp_path, com_portal=False)
    r = _rodar_preservar(tmp_path, env_file)

    assert r.returncode == 0, r.stderr
    assert r.stdout == ""


def test_primeiro_deploy_sem_environmentfile_nao_aborta(tmp_path):
    r = _rodar_preservar(tmp_path, tmp_path / "ainda-nao-existe.env")

    assert r.returncode == 0, r.stderr
    assert r.stdout == ""


def test_a_copia_entra_no_arquivo_escrito_e_antes_da_instalacao():
    """Função existir não basta: ela tem de ser CHAMADA dentro do grupo que
    escreve o TEMP_ENV, e esse grupo tem de fechar antes do `install` — se a
    chamada caísse depois, leria o arquivo já sobrescrito e copiaria nada."""

    texto = DEPLOY_API.read_text(encoding="utf-8")
    grupo = texto[texto.index('TEMP_ENV="$(mktemp') : texto.index('} >"$TEMP_ENV"')]
    assert re.search(r"^  preservar_portal$", grupo, re.MULTILINE), (
        "preservar_portal não é chamada dentro do bloco que gera o EnvironmentFile"
    )
    assert texto.index('} >"$TEMP_ENV"') < texto.index('"$TEMP_ENV" "$ENV_FILE"')


def test_o_valor_da_chave_nunca_vai_para_o_log_do_deploy():
    """O deploy loga NOME de variável, nunca valor: o journal da VPS e o
    terminal de quem faz deploy não são lugar para a chave de derivação."""

    texto = DEPLOY_API.read_text(encoding="utf-8")
    ecos = [
        linha.strip()
        for linha in texto.splitlines()
        if "PORTAL_PRESERVADO" in linha and linha.strip().startswith("echo")
    ]
    assert ecos, "nenhum log sobre as variáveis preservadas"
    # O que é ecoado vem de `cut -d= -f1`: nomes, sem o lado direito do '='.
    assert "cut -d= -f1" in texto
    assert 'preservar_portal | cut -d= -f1' in texto


# ----------------------------------------------- guarda do estado parcial


def _rodar_guarda(tmp_path: Path, env_file: Path) -> subprocess.CompletedProcess:
    """Executa o bloco real da guarda recortado de `etapa_segredos`."""

    texto = DEPLOY_PORTAL.read_text(encoding="utf-8")
    inicio = texto.index("  # Estado PARCIAL é armadilha")
    fim = texto.index("\n  fi\n", inicio) + len("\n  fi\n")
    bloco = texto[inicio:fim]

    script = tmp_path / "guarda.sh"
    script.write_text(
        "set -Eeuo pipefail\n"
        'fail() { echo "ERRO: $*" >&2; exit 1; }\n'
        f'ENV_INTERNO="{env_file}"\n'
        f"{bloco}\n"
        'echo "SEGUIU"\n',
        encoding="utf-8",
    )
    return subprocess.run(
        ["/bin/bash", str(script)], capture_output=True, text=True, check=False
    )


def test_estado_parcial_aborta_em_vez_de_gerar_chave_nova(tmp_path):
    env_file = _env_interno(tmp_path, com_portal=True, com_chave=False)
    r = _rodar_guarda(tmp_path, env_file)

    assert r.returncode == 1
    assert "estado parcial" in r.stderr
    assert "M15_PORTAL_TOKEN_KEY" in r.stderr
    assert "SEGUIU" not in r.stdout


@pytest.mark.parametrize("com_portal", [True, False])
def test_estado_consistente_nao_e_barrado(tmp_path, com_portal):
    """Portal completo e instalação sem portal são os dois estados legítimos."""

    env_file = _env_interno(tmp_path, com_portal=com_portal)
    r = _rodar_guarda(tmp_path, env_file)

    assert r.returncode == 0, r.stderr
    assert "SEGUIU" in r.stdout


def test_a_guarda_vem_antes_da_geracao_da_chave():
    texto = DEPLOY_PORTAL.read_text(encoding="utf-8")
    assert texto.index("Estado PARCIAL é armadilha") < texto.index(
        'echo "M15_PORTAL_TOKEN_KEY=$(segredo)"'
    )
