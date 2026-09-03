#!/usr/bin/env python3
"""M26.5 — qual é o IPv4 PÚBLICO desta VPS, e qual definitivamente não é.

O registro DNS `resultados-api.soprolife.com.br` precisa apontar para o IPv4
público da máquina. A máquina tem mais de um endereço IPv4, e o mais visível
deles — o que aparece primeiro em `hostname -I`, o que `tailscale ip -4`
imprime, o que o operador já decorou de tanto usar em `ssh` — é o endereço
do **tailnet**: `100.87.98.100`.

Publicar esse endereço num registro A seria errado de duas maneiras ao mesmo
tempo:

* **não funciona.** 100.64.0.0/10 é CGNAT. Ninguém fora do tailnet roteia
  para lá; o Let's Encrypt não completaria o desafio HTTP-01 e o paciente
  não abriria o link;
* **conta o que não devia.** Um registro DNS é público e permanente em
  cache e em log passivo. Ele passaria a anunciar, para qualquer um, o
  endereço interno de administração do Command Center.

Por isso a escolha do IP público não é um `head -n 1`: é uma eliminação
explícita, e ela falha fechada quando sobra mais de um candidato. Adivinhar
qual dos dois é o certo não é trabalho de script.

Uso:
    rede_publica.py                       # lê `ip -4 -o addr show scope global`
    rede_publica.py --verificar 1.2.3.4   # 0 se for público, 2 se não for
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import shutil
import subprocess
import sys

# Prefixos de interface que nunca carregam o endereço público de uma VPS.
INTERFACES_IGNORADAS = ("lo", "tailscale", "docker", "br-", "veth", "virbr", "wg")


class SemIPPublico(Exception):
    """Falha fechada: preferimos não responder a responder o endereço errado."""


def motivo_de_descarte(interface: str, endereco: str) -> str | None:
    """`None` quando o endereço serve como IPv4 público; senão, o porquê."""

    for prefixo in INTERFACES_IGNORADAS:
        if interface == prefixo or interface.startswith(prefixo):
            return f"interface {interface} (nunca carrega o endereço público)"
    try:
        ip = ipaddress.IPv4Address(endereco)
    except ipaddress.AddressValueError:
        return f"{endereco} não é um IPv4"
    # Este caso vem primeiro porque merece o nome próprio: é o erro que esta
    # função existe para impedir.
    if ip in ipaddress.IPv4Network("100.64.0.0/10"):
        return f"{ip} está em 100.64.0.0/10 — faixa CGNAT, é o endereço do Tailscale"
    if ip.is_loopback:
        return f"{ip} é loopback"
    if ip.is_link_local:
        return f"{ip} é link-local"
    if ip.is_private:
        return f"{ip} é endereço privado (RFC 1918)"
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return f"{ip} não é endereço unicast roteável"
    return None


def escolher_ipv4_publico(entradas: list[tuple[str, str]]) -> str:
    """Recebe [(interface, endereço)] e devolve o único IPv4 público.

    Zero candidatos ou mais de um: erro. Um script de deploy que "escolhe"
    entre dois endereços públicos está adivinhando.
    """

    aprovados: list[tuple[str, str]] = []
    descartados: list[str] = []
    for interface, endereco in entradas:
        motivo = motivo_de_descarte(interface, endereco)
        if motivo is None:
            aprovados.append((interface, endereco))
        else:
            descartados.append(f"{interface} {endereco}: {motivo}")

    if not aprovados:
        raise SemIPPublico(
            "nenhum IPv4 público nesta máquina. Descartados:\n  "
            + "\n  ".join(descartados or ["(nada foi examinado)"])
        )
    distintos = sorted({endereco for _, endereco in aprovados})
    if len(distintos) > 1:
        raise SemIPPublico(
            "mais de um IPv4 público — informe qual deve ir para o DNS:\n  "
            + "\n  ".join(f"{iface} {end}" for iface, end in aprovados)
        )
    return distintos[0]


def entradas_do_sistema() -> list[tuple[str, str]]:
    """Lê os endereços IPv4 de escopo global desta máquina."""

    executavel = shutil.which("ip")
    if not executavel:
        raise SemIPPublico("comando `ip` indisponível")
    saida = subprocess.run(
        [executavel, "-4", "-o", "addr", "show", "scope", "global"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return analisar_ip_addr(saida)


def analisar_ip_addr(saida: str) -> list[tuple[str, str]]:
    """Extrai [(interface, endereço)] da saída de `ip -4 -o addr`."""

    entradas: list[tuple[str, str]] = []
    for linha in saida.splitlines():
        achado = re.match(r"^\d+:\s+(\S+)\s+inet\s+([0-9.]+)/", linha.strip())
        if achado:
            entradas.append((achado.group(1), achado.group(2)))
    return entradas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verificar",
        metavar="IP",
        help="não escolhe nada: só diz se o IP dado serve como IPv4 público",
    )
    parser.add_argument(
        "--entrada",
        metavar="ARQUIVO",
        help="saída de `ip -4 -o addr` gravada em arquivo (usado nos testes)",
    )
    args = parser.parse_args(argv)

    if args.verificar:
        motivo = motivo_de_descarte("desconhecida", args.verificar)
        if motivo:
            print(f"ERRO: {args.verificar} NÃO serve como IPv4 público: {motivo}", file=sys.stderr)
            return 2
        print(args.verificar)
        return 0

    try:
        if args.entrada:
            with open(args.entrada, encoding="utf-8") as arquivo:
                entradas = analisar_ip_addr(arquivo.read())
        else:
            entradas = entradas_do_sistema()
        print(escolher_ipv4_publico(entradas))
    except SemIPPublico as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - entrada de linha de comando
    raise SystemExit(main())
