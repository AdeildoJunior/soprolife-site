#!/usr/bin/env python3
"""M25.29H — paridade administrativa entre os sócios da SoproLife.

Um sócio com papel `gestor` recebia "Permissão insuficiente. (http_403)" em
funções que a conta principal executa normalmente. Isso não é uma decisão de
segurança: é uma conta que ficou com menos poder do que a pessoa tem na
empresa. O script iguala o CONJUNTO ADMINISTRATIVO das duas contas.

**O que ele nunca faz, e é o ponto inteiro.** Ele não concede papel médico.
`admin` deliberadamente não implica `medico` na hierarquia (`app/security.py`),
e nenhum administrador ganha autoria clínica por herança. Interpretar exame,
editar conclusão e concluir laudo continuam exigindo o papel `medico`
explícito, o perfil profissional e a atribuição do laudo — três coisas que
este script não toca. "Administrador total" administra o sistema; não vira
médico.

Também não reseta senha, não altera e-mail, não desativa conta, não revoga
sessão e não apaga nada. A única mudança é o conjunto de papéis
administrativos, e ela é idempotente: rodar de novo numa conta já promovida
não escreve nada.

Nenhuma senha, hash, token, cookie ou segredo é lido ou impresso.

Uso:
    # dry-run (padrão)
    python scripts/promover_admin_soprolife.py --usuario socio@empresa

    # escrita, explícita
    python scripts/promover_admin_soprolife.py --usuario socio@empresa --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.audit import audit  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import build_engine  # noqa: E402
from app.models import User  # noqa: E402
from app.security import (  # noqa: E402
    ADMINISTRATIVE_ROLES,
    ROLE_ADMIN,
    ROLE_MEDICO,
    ensure_roles_exist,
    get_role,
)


def _linha(rotulo: str, valor) -> None:
    print(f"  {rotulo:.<40} {valor if valor is not None else '—'}")


def _encontrar(db: Session, identificador: str) -> User | None:
    """Aceita id ou e-mail. Nunca busca por nome: nome se repete."""

    alvo = identificador.strip()
    usuario = db.get(User, alvo)
    if usuario is not None:
        return usuario
    return db.execute(
        select(User).where(User.email == alvo.lower())
    ).scalar_one_or_none()


class ContaAmbigua(LookupError):
    """Mais de uma conta (ou nenhuma) responde ao critério pedido."""


def _unica_conta_do_papel(db: Session, papel: str) -> User:
    """A ÚNICA conta ativa cujo papel administrativo é exatamente `papel`.

    Existe para o caso operacional em que se conhece a situação — "o sócio
    é o único gestor" — mas não se tem o e-mail à mão. Se houver duas
    contas assim, o script para: escolher uma delas seria adivinhar, e
    adivinhar quem recebe poder administrativo é exatamente o que não se
    pode fazer. Nome de pessoa continua fora do critério.
    """

    candidatos = []
    for usuario in db.execute(select(User)).scalars().all():
        if not usuario.ativo:
            continue
        administrativos = {
            p.name for p in usuario.roles if p.name in ADMINISTRATIVE_ROLES
        }
        if administrativos == {papel}:
            candidatos.append(usuario)
    if len(candidatos) != 1:
        raise ContaAmbigua(
            f"{len(candidatos)} conta(s) ativa(s) com papel administrativo "
            f"exatamente '{papel}'. Informe --usuario explicitamente."
        )
    return candidatos[0]


def promover(
    db: Session,
    *,
    identificador: str | None = None,
    papel_atual: str | None = None,
    apply: bool = False,
) -> int:
    if identificador:
        usuario = _encontrar(db, identificador)
        if usuario is None:
            print(f"\n  Usuário não encontrado: {identificador}\n")
            return 2
    else:
        try:
            usuario = _unica_conta_do_papel(db, papel_atual or "")
        except ContaAmbigua as erro:
            print(f"\n  PARE: {erro}\n")
            return 2

    papeis_antes = sorted(p.name for p in usuario.roles)
    administrativos = [p for p in papeis_antes if p in ADMINISTRATIVE_ROLES]
    clinicos = [p for p in papeis_antes if p not in ADMINISTRATIVE_ROLES]

    print("\n=== PROMOÇÃO ADMINISTRATIVA ===")
    _linha("modo", "APLICANDO ESCRITA" if apply else "DRY-RUN (não escreve)")
    _linha("user_id", usuario.id)
    _linha("email", usuario.email)
    _linha("nome", usuario.nome)
    _linha("ativo", usuario.ativo)
    _linha("papéis atuais", ", ".join(papeis_antes) or "NENHUM")
    _linha("administrativos atuais", ", ".join(administrativos) or "NENHUM")
    _linha("preservados (não administrativos)", ", ".join(clinicos) or "nenhum")

    if not usuario.ativo:
        _linha("decisão", "PARADO — conta inativa. Reative antes de promover.")
        print("")
        return 3

    if ROLE_MEDICO in clinicos:
        # Não é erro: uma pessoa pode ser as duas coisas. Mas precisa ficar
        # dito em voz alta, porque este script não é o caminho para conceder
        # autoria clínica — e não a concede.
        _linha("ATENÇÃO", "conta já possui papel médico; ele é PRESERVADO")

    if administrativos == [ROLE_ADMIN]:
        _linha("decisão", "NADA A FAZER — já é admin (idempotente)")
        print("")
        return 0

    _linha(
        "decisão",
        f"administrativos {administrativos or ['nenhum']} → ['{ROLE_ADMIN}']",
    )
    _linha("papel médico concedido?", "NÃO — nunca, por desenho")

    if not apply:
        print("\n  DRY-RUN: nada foi gravado. Repita com --apply para aplicar.")
        print("")
        return 0

    ensure_roles_exist(db)
    # Remove só os papéis ADMINISTRATIVOS; `admin` engloba todos eles pela
    # hierarquia. Qualquer papel fora desse conjunto fica exatamente onde
    # estava — inclusive o clínico, que este script não administra.
    usuario.roles[:] = [
        papel for papel in usuario.roles
        if papel.name not in ADMINISTRATIVE_ROLES
    ]
    usuario.roles.append(get_role(db, ROLE_ADMIN))
    db.flush()

    audit(
        db,
        "usuario_promovido_a_admin",
        entidade="users",
        entidade_id=usuario.id,
        user_id=None,
        detalhes={
            "target_user_id": usuario.id,
            "papel": ROLE_ADMIN,
            "campos": administrativos or ["nenhum"],
            "physician_role": False,
        },
    )
    db.commit()

    _linha("papéis depois", ", ".join(sorted(p.name for p in usuario.roles)))
    print("")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Iguala o conjunto administrativo de uma conta ao da "
                    "conta principal, sem conceder papel médico."
    )
    parser.add_argument(
        "--usuario",
        help="user_id ou e-mail da conta a promover.",
    )
    parser.add_argument(
        "--do-papel",
        help=(
            "Alternativa a --usuario: a ÚNICA conta ativa cujo papel "
            "administrativo é exatamente este. Para se houver mais de uma."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Grava a mudança. Sem esta opção, nada é escrito.",
    )
    args = parser.parse_args()
    if not args.usuario and not args.do_papel:
        parser.error("informe --usuario ou --do-papel")

    settings = get_settings()
    engine = build_engine(settings.database_url)
    with Session(engine) as db:
        return promover(
            db,
            identificador=args.usuario,
            papel_atual=args.do_papel,
            apply=args.apply,
        )


if __name__ == "__main__":
    raise SystemExit(main())
