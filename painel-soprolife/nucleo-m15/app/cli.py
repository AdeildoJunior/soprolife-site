"""CLI administrativa do Núcleo M15.

Uso (a partir de painel-soprolife/nucleo-m15, com o venv ativo):

  python -m app.cli criar-usuario --email a@b.c --nome "Nome" --papel admin
  python -m app.cli emitir-token --email a@b.c
  python -m app.cli importar --tipo leads --arquivo caminho.csv           # dry-run
  python -m app.cli importar --tipo leads --arquivo caminho.csv --execute
  python -m app.cli seed-demo                # dados 100% sintéticos
  python -m app.cli seed-institucional --arquivo data-private/parceiros.json

Senha do usuário: variável de ambiente M15_NOVA_SENHA (nunca argumento de
linha de comando, para não vazar em histórico/ps).
"""

import argparse
import getpass
import json
import os
import pathlib
import sys

from sqlalchemy import select

from .db import get_sessionmaker
from .importer.csv_import import IMPORT_TYPES, run_import, write_reports
from .models import User
from .security import ensure_roles_exist, get_role, hash_password, issue_token


def _session():
    return get_sessionmaker()()


def cmd_criar_usuario(args) -> int:
    senha = os.environ.get("M15_NOVA_SENHA") or getpass.getpass("Senha do novo usuário: ")
    if len(senha) < 8:
        print("ERRO: senha precisa de ao menos 8 caracteres.", file=sys.stderr)
        return 1
    db = _session()
    try:
        ensure_roles_exist(db)
        existing = db.execute(
            select(User).where(User.email == args.email.lower())
        ).scalar_one_or_none()
        if existing:
            print(f"ERRO: já existe usuário com e-mail {args.email}", file=sys.stderr)
            return 1
        user = User(
            email=args.email.lower(),
            nome=args.nome,
            password_hash=hash_password(senha),
        )
        user.roles.append(get_role(db, args.papel))
        db.add(user)
        db.commit()
        print(f"Usuário criado: {user.id} ({user.email}, papel={args.papel})")
        return 0
    finally:
        db.close()


def cmd_emitir_token(args) -> int:
    db = _session()
    try:
        user = db.execute(
            select(User).where(User.email == args.email.lower())
        ).scalar_one_or_none()
        if not user:
            print("ERRO: usuário não encontrado.", file=sys.stderr)
            return 1
        print(issue_token(user.id))
        return 0
    finally:
        db.close()


def cmd_importar(args) -> int:
    from .importer.csv_import import MAX_UPLOAD_BYTES

    path = pathlib.Path(args.arquivo)
    if not path.is_file():
        print(f"ERRO: arquivo não encontrado: {path}", file=sys.stderr)
        return 1
    # mesmo limite da API, verificado ANTES de carregar o arquivo
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        print(
            f"ERRO: arquivo excede {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            file=sys.stderr,
        )
        return 1
    content = path.read_bytes()
    db = _session()
    try:
        report = run_import(
            db,
            source_type=args.tipo,
            source_name=path.name,
            content=content,
            execute=args.execute,
        )
        if args.execute:
            db.commit()
        else:
            db.rollback()  # garantia extra: dry-run nunca persiste nada
        json_path, md_path = write_reports(report, args.saida)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\nRelatórios: {json_path} | {md_path}", file=sys.stderr)
        if not args.execute:
            print("DRY-RUN: nada foi gravado. Use --execute para gravar.", file=sys.stderr)
        return 0
    except Exception as exc:
        db.rollback()
        print(f"ERRO na importação — rollback completo do lote: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def cmd_seed_demo(_args) -> int:
    from .seed import seed_demo

    db = _session()
    try:
        result = seed_demo(db)
        db.commit()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        db.rollback()
        print(f"ERRO no seed: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def cmd_seed_institucional(args) -> int:
    """Cadastra parceiros institucionais REAIS a partir de arquivo privado.

    O arquivo NUNCA vai para o Git (data-private/). Idempotente: reexecutar
    não duplica. Só campos institucionais confirmados; nada é inventado.

    Formato esperado (JSON):
    {
      "parceiros": [{
        "nome": "...", "tipo": "clinica", "status_parceria": "ativa",
        "cidade": null,
        "unidades": [{"nome": "..."}],
        "contatos": [{"nome": "...", "cargo": "...", "principal": true,
                       "telefone": null, "email": null}]
      }]
    }
    """
    path = pathlib.Path(args.arquivo)
    if not path.is_file():
        print(f"ERRO: arquivo privado não encontrado: {path}", file=sys.stderr)
        print("Crie-o localmente (fora do Git). Veja o formato no --help.", file=sys.stderr)
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    db = _session()
    try:
        from .seed import seed_institutional

        result = seed_institutional(db, data)
        db.commit()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        db.rollback()
        print(f"ERRO no seed institucional: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="m15", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("criar-usuario", help="Cria usuário interno")
    p.add_argument("--email", required=True)
    p.add_argument("--nome", required=True)
    p.add_argument("--papel", default="operacional",
                   choices=["admin", "gestor", "operacional", "leitura"])
    p.set_defaults(func=cmd_criar_usuario)

    p = sub.add_parser("emitir-token", help="Emite token de acesso")
    p.add_argument("--email", required=True)
    p.set_defaults(func=cmd_emitir_token)

    p = sub.add_parser("importar", help="Importa CSV legado (dry-run por padrão)")
    p.add_argument("--tipo", required=True, choices=sorted(IMPORT_TYPES))
    p.add_argument("--arquivo", required=True)
    p.add_argument("--execute", action="store_true",
                   help="Grava de verdade (sem isso é dry-run)")
    p.add_argument("--saida", default="var/relatorios-importacao",
                   help="Diretório dos relatórios JSON/MD")
    p.set_defaults(func=cmd_importar)

    p = sub.add_parser("seed-demo", help="Dados sintéticos de demonstração")
    p.set_defaults(func=cmd_seed_demo)

    p = sub.add_parser("seed-institucional",
                       help="Parceiros reais via arquivo privado (idempotente)")
    p.add_argument("--arquivo", required=True)
    p.set_defaults(func=cmd_seed_institucional)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
