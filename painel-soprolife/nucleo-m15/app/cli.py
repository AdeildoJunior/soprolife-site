"""CLI administrativa do Núcleo M15.

Uso (a partir de painel-soprolife/nucleo-m15, com o venv ativo):

  python -m app.cli criar-usuario --email a@b.c --nome "Nome" --papel admin
  python -m app.cli emitir-token --email a@b.c
  python -m app.cli importar --tipo leads --arquivo caminho.csv           # dry-run
  python -m app.cli importar --tipo leads --arquivo caminho.csv --execute
  python -m app.cli seed-demo                # dados 100% sintéticos
  python -m app.cli seed-institucional --arquivo data-private/parceiros.json

Migração governada por snapshot (M15.6A — manifesto privado, portões e
reconciliação; dry-run é SEMPRE o padrão, nunca há execução implícita):

  python -m app.cli migracao validar-manifesto --manifesto snap.manifest.json
  python -m app.cli migracao registrar-snapshot --manifesto snap.manifest.json
  python -m app.cli migracao dry-run --snapshot <id>
  python -m app.cli migracao relatorio --snapshot <id> --saida var/relatorios
  python -m app.cli migracao preflight --snapshot <id> --backup-evidencia ev.json
  python -m app.cli migracao aprovar --snapshot <id> --sha256 <h> \
      --mapping-version <v> --batch <dry_run_batch_id>
  python -m app.cli migracao executar --snapshot <id> --batch <id> \
      --backup-evidencia ev.json      # pede a frase exata interativamente
  python -m app.cli migracao reconciliar --snapshot <id>
  python -m app.cli migracao status [--snapshot <id>]

Todos os subcomandos de migração aceitam --json (saída machine-readable) e
retornam exit != 0 em falha. Arquivos de manifesto/evidência vivem SOMENTE
no diretório privado aprovado (M15_IMPORT_PRIVATE_DIR).

Senha do usuário: solicitada com getpass (preferencial) ou pela variável
temporária M15_NOVA_SENHA; nunca por argumento de linha de comando.
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
        print(issue_token(user.id, user.password_hash))
        return 0
    finally:
        db.close()


def cmd_definir_usuario_ativo(args, ativo: bool) -> int:
    """Ativa/inativa usuário; tokens de usuário inativo falham imediatamente."""
    db = _session()
    try:
        user = db.execute(
            select(User).where(User.email == args.email.lower())
        ).scalar_one_or_none()
        if not user:
            print("ERRO: usuário não encontrado.", file=sys.stderr)
            return 1
        user.ativo = ativo
        db.commit()
        print("Usuário ativado." if ativo else "Usuário inativado; tokens revogados.")
        return 0
    finally:
        db.close()


def cmd_desativar_usuario(args) -> int:
    return cmd_definir_usuario_ativo(args, False)


def cmd_ativar_usuario(args) -> int:
    return cmd_definir_usuario_ativo(args, True)


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


# -------------------------------------------------------- migração (M15.6A)

def _emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def _migration_error(exc, as_json: bool) -> int:
    payload = exc.as_dict() if hasattr(exc, "as_dict") else {
        "ok": False, "codigo": str(exc)}
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    return 1


def _user_id_por_email(db, email: str | None) -> str | None:
    if not email:
        return None
    user = db.execute(
        select(User).where(User.email == email.lower())
    ).scalar_one_or_none()
    if user is None:
        raise ValueError(f"usuário não encontrado: {email}")
    return user.id


def cmd_migracao_validar_manifesto(args) -> int:
    from .migration.manifest import load_and_validate_manifest

    result = load_and_validate_manifest(args.manifesto)
    _emit(result.as_dict(), args.json)
    return 0 if result.ok else 1


def cmd_migracao_registrar_snapshot(args) -> int:
    from .migration.service import MigrationError, register_snapshot

    db = _session()
    try:
        user_id = _user_id_por_email(db, args.email)
        result = register_snapshot(db, args.manifesto, user_id=user_id)
        db.commit()
        _emit(result, args.json)
        return 0
    except (MigrationError, ValueError) as exc:
        db.rollback()
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_dry_run(args) -> int:
    from .migration.service import MigrationError, dry_run_snapshot

    db = _session()
    try:
        user_id = _user_id_por_email(db, args.email)
        result = dry_run_snapshot(db, args.snapshot, user_id=user_id)
        db.commit()  # persiste apenas o resumo do lote (staging sem PII)
        _emit(result, args.json)
        print("DRY-RUN: nenhum registro operacional foi gravado.", file=sys.stderr)
        return 0
    except (MigrationError, ValueError) as exc:
        db.rollback()
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_relatorio(args) -> int:
    from .migration.report import write_sanitized_reports
    from .migration.service import MigrationError, snapshot_status

    db = _session()
    try:
        status = snapshot_status(db, args.snapshot)
        dry = status.get("dry_run") or {}
        report = {
            "snapshot_id": status["snapshot_id"],
            "source_type": status["source_type"],
            "sha256": status["sha256"],
            "status_snapshot": status["status"],
            "mapping_version": status["mapping_version"],
            **(dry.get("resumo") or {}),
        }
        recon = (status.get("execucao_batch") or {}).get("reconciliacao")
        if recon:
            report["reconciliacao"] = recon
        paths = write_sanitized_reports(report, args.saida)
        _emit({"ok": True, **paths}, args.json)
        return 0
    except (MigrationError, ValueError) as exc:
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_preflight(args) -> int:
    from .migration.service import MigrationError, preflight

    db = _session()
    try:
        result = preflight(db, args.snapshot, args.backup_evidencia)
        _emit(result, args.json)
        return 0 if result["ok"] else 1
    except (MigrationError, ValueError) as exc:
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_aprovar(args) -> int:
    from .migration.service import MigrationError, approve_snapshot

    db = _session()
    try:
        user_id = _user_id_por_email(db, args.email)
        result = approve_snapshot(
            db, args.snapshot, args.sha256, args.mapping_version, args.batch,
            user_id=user_id, observacao=args.observacao,
        )
        db.commit()
        _emit(result, args.json)
        return 0
    except (MigrationError, ValueError) as exc:
        db.rollback()
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_revogar_aprovacao(args) -> int:
    from .migration.service import MigrationError, revoke_approval

    db = _session()
    try:
        user_id = _user_id_por_email(db, args.email)
        result = revoke_approval(
            db, args.snapshot, user_id=user_id, observacao=args.observacao)
        db.commit()
        _emit(result, args.json)
        return 0
    except (MigrationError, ValueError) as exc:
        db.rollback()
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_executar(args) -> int:
    """Execução real: preflight completo + frase exata digitada na hora.

    A frase NUNCA é aceita por argumento de linha de comando — é a única
    interação prevista do fluxo (confirmação final explícita).
    """
    from .migration.service import (
        MigrationError,
        confirmation_phrase,
        execute_snapshot,
        preflight,
    )

    db = _session()
    try:
        user_id = _user_id_por_email(db, args.email)
        pf = preflight(db, args.snapshot, args.backup_evidencia)
        if not pf["ok"]:
            reprovados = sorted(
                nome for nome, g in pf["gates"].items() if not g["ok"])
            return _migration_error(
                MigrationError("portoes_reprovados", reprovados), args.json)
        frase_esperada = confirmation_phrase(args.snapshot)
        print(
            f"Confirmação final. Digite exatamente: {frase_esperada}",
            file=sys.stderr,
        )
        try:
            frase = input("> ")
        except EOFError:
            frase = ""
        result = execute_snapshot(
            db, args.snapshot, args.batch, frase, args.backup_evidencia,
            user_id=user_id,
        )
        db.commit()
        _emit(result, args.json)
        return 0
    except (MigrationError, ValueError) as exc:
        db.rollback()
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_reconciliar(args) -> int:
    from .migration.service import MigrationError, reconcile_snapshot

    db = _session()
    try:
        user_id = _user_id_por_email(db, args.email)
        result = reconcile_snapshot(db, args.snapshot, user_id=user_id)
        db.commit()
        _emit(result, args.json)
        return 0
    except (MigrationError, ValueError) as exc:
        db.rollback()
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_status(args) -> int:
    from .migration.service import (
        MigrationError,
        list_snapshot_status,
        snapshot_status,
    )

    db = _session()
    try:
        if args.snapshot:
            result = snapshot_status(db, args.snapshot)
        else:
            result = {"snapshots": list_snapshot_status(db)}
        _emit(result, args.json)
        return 0
    except (MigrationError, ValueError) as exc:
        return _migration_error(exc, args.json)
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

    p = sub.add_parser("desativar-usuario", help="Inativa usuário e revoga seus tokens")
    p.add_argument("--email", required=True)
    p.set_defaults(func=cmd_desativar_usuario)

    p = sub.add_parser("ativar-usuario", help="Reativa usuário")
    p.add_argument("--email", required=True)
    p.set_defaults(func=cmd_ativar_usuario)

    p = sub.add_parser("importar", help="Importa CSV legado (dry-run por padrão)")
    p.add_argument("--tipo", required=True, choices=sorted(IMPORT_TYPES))
    p.add_argument("--arquivo", required=True)
    p.add_argument("--execute", action="store_true",
                   help="Grava de verdade (sem isso é dry-run)")
    p.add_argument("--saida", default="var/relatorios-importacao",
                   help="Diretório dos relatórios JSON/MD")
    p.set_defaults(func=cmd_importar)

    mig = sub.add_parser(
        "migracao",
        help="Migração governada por snapshot (M15.6A): manifesto, dry-run, "
             "portões, execução explícita e reconciliação",
    )
    mig_sub = mig.add_subparsers(dest="mig_cmd", required=True)

    def _mig_parser(nome: str, ajuda: str, func):
        mp = mig_sub.add_parser(nome, help=ajuda)
        mp.add_argument("--json", action="store_true",
                        help="Saída machine-readable (uma linha JSON)")
        mp.add_argument("--email", default=None,
                        help="E-mail de usuário existente para atribuição "
                             "(auditoria); nunca cria usuário")
        mp.set_defaults(func=func)
        return mp

    mp = _mig_parser("validar-manifesto",
                     "Valida manifesto+arquivo no diretório privado aprovado",
                     cmd_migracao_validar_manifesto)
    mp.add_argument("--manifesto", required=True,
                    help="Nome do manifesto (sem caminho) no diretório privado")

    mp = _mig_parser("registrar-snapshot",
                     "Registra snapshot imutável (identidade única)",
                     cmd_migracao_registrar_snapshot)
    mp.add_argument("--manifesto", required=True)

    mp = _mig_parser("dry-run",
                     "Dry-run do snapshot (padrão; nunca grava registro "
                     "operacional)", cmd_migracao_dry_run)
    mp.add_argument("--snapshot", required=True, help="ID do snapshot")

    mp = _mig_parser("relatorio",
                     "Gera relatório sanitizado (JSON/MD/CSV neutralizado)",
                     cmd_migracao_relatorio)
    mp.add_argument("--snapshot", required=True)
    mp.add_argument("--saida", default="var/relatorios-importacao")

    mp = _mig_parser("preflight",
                     "Avalia todos os portões de execução (não escreve nada)",
                     cmd_migracao_preflight)
    mp.add_argument("--snapshot", required=True)
    mp.add_argument("--backup-evidencia", default=None,
                    help="Nome do JSON de evidência de backup no diretório privado")

    mp = _mig_parser("aprovar",
                     "Aprovação humana explícita (todos os IDs digitados)",
                     cmd_migracao_aprovar)
    mp.add_argument("--snapshot", required=True)
    mp.add_argument("--sha256", required=True,
                    help="SHA-256 exato do snapshot aprovado")
    mp.add_argument("--mapping-version", required=True)
    mp.add_argument("--batch", required=True,
                    help="ID exato do lote de dry-run revisado")
    mp.add_argument("--observacao", default=None)

    mp = _mig_parser("revogar-aprovacao", "Revoga a aprovação ativa",
                     cmd_migracao_revogar_aprovacao)
    mp.add_argument("--snapshot", required=True)
    mp.add_argument("--observacao", default=None)

    mp = _mig_parser("executar",
                     "Execução REAL — exige todos os portões verdes, lote "
                     "exato, evidência de backup e frase exata digitada",
                     cmd_migracao_executar)
    mp.add_argument("--snapshot", required=True)
    mp.add_argument("--batch", required=True,
                    help="ID exato do lote de dry-run aprovado")
    mp.add_argument("--backup-evidencia", required=True)

    mp = _mig_parser("reconciliar",
                     "Reconciliação determinística pós-execução",
                     cmd_migracao_reconciliar)
    mp.add_argument("--snapshot", required=True)

    mp = _mig_parser("status", "Status de um snapshot ou de todos",
                     cmd_migracao_status)
    mp.add_argument("--snapshot", default=None)

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
