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

Execução multiaba FINAL (M15.6C — caminho único de escrita do formato
multiaba; CLI-only, admin-only, frase exata SEMPRE digitada interativamente,
nunca aceita por argumento ou variável de ambiente):

  python -m app.cli migracao preflight-execucao-multiaba --envelope env.json \
      --batch <dry_run_batch_id> --backup-evidencia ev.json --email admin@x
  python -m app.cli migracao plano-rollback-multiaba --envelope env.json \
      --batch <dry_run_batch_id> --email admin@x
  python -m app.cli migracao executar-multiaba --envelope env.json \
      --batch <dry_run_batch_id> --backup-evidencia ev.json --email admin@x
  python -m app.cli migracao reconciliar-multiaba --batch-execucao <id>
  python -m app.cli migracao rollback-multiaba --batch-execucao <id> \
      --email admin@x            # só quando provadamente seguro

Revisão multiaba em lote (M15.6C — só decisões append-only; nunca executa):

  python -m app.cli migracao revisar-multiaba-em-lote \
      --arquivo decisoes-m15.json --email admin@x --somente-preview
  python -m app.cli migracao revisar-multiaba-em-lote \
      --arquivo decisoes-m15.json --email admin@x

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


def _reviewer_id_por_email(db, email: str | None) -> str:
    if not email:
        raise ValueError("revisor autenticado obrigatório")
    user = db.execute(
        select(User).where(User.email == email.lower())
    ).scalar_one_or_none()
    if user is None:
        raise ValueError("revisor não encontrado")
    if not {role.name for role in user.roles}.intersection({"admin", "gestor"}):
        raise ValueError("papel de revisor insuficiente")
    return user.id


def _admin_id_por_email(db, email: str | None) -> str:
    if not email:
        raise ValueError("administrador obrigatório")
    user = db.execute(
        select(User).where(User.email == email.lower())
    ).scalar_one_or_none()
    if user is None:
        raise ValueError("administrador não encontrado")
    if not user.ativo:
        raise ValueError("administrador inativo")
    if "admin" not in {role.name for role in user.roles}:
        raise ValueError("papel admin obrigatório")
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


def cmd_migracao_dry_run_multiaba(args) -> int:
    from .migration.multisheet import MultiSheetError, run_multi_sheet_dry_run

    db = _session()
    try:
        user_id = _user_id_por_email(db, args.email)
        result = run_multi_sheet_dry_run(
            db, args.envelope, user_id=user_id
        )
        db.commit()  # apenas resumo sanitizado; zero registros operacionais
        _emit(result, args.json)
        print(
            "DRY-RUN MULTIABA: execução real bloqueada; nada operacional foi gravado.",
            file=sys.stderr,
        )
        return 0
    except (MultiSheetError, ValueError) as exc:
        db.rollback()
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_status_multiaba(args) -> int:
    from .migration.multisheet import (
        MultiSheetError,
        list_multi_sheet_status,
        multi_sheet_status,
    )

    db = _session()
    try:
        result = (
            multi_sheet_status(db, args.batch)
            if args.batch else list_multi_sheet_status(db)
        )
        _emit(result, args.json)
        return 0
    except (MultiSheetError, ValueError) as exc:
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_revisar_multiaba(args) -> int:
    from .migration.multisheet import MultiSheetError, decide_multi_sheet_review
    from .migration.review_batch import _read_secure_private_file

    db = _session()
    try:
        user_id = (
            _reviewer_id_por_email(db, args.email) if args.email else None
        )
        guardian_override = None
        # Nome/telefone reais só entram via arquivo privado confinado
        # (M15_IMPORT_PRIVATE_DIR, 0600) — nunca por argumento de linha de
        # comando (shell history/ps expõem argumentos, nunca conteúdo de
        # arquivo). Mesma cautela já aplicada à senha de usuário.
        if args.responsavel_arquivo:
            _path, conteudo, _dev, _ino = _read_secure_private_file(
                args.responsavel_arquivo
            )
            try:
                guardian_override = json.loads(conteudo.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MultiSheetError(
                    "arquivo_responsavel_json_invalido"
                ) from exc
        result = decide_multi_sheet_review(
            db,
            args.batch,
            args.referencia,
            args.decisao,
            args.mapping_version,
            user_id,
            guardian_override=guardian_override,
        )
        db.commit()
        _emit(result, args.json)
        return 0
    except (MultiSheetError, ValueError) as exc:
        db.rollback()
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_revisar_multiaba_em_lote(args) -> int:
    """Preview + uma confirmação TTY + commit único de decisões append-only."""
    from .migration.multisheet import MultiSheetError
    from .migration.review_batch import (
        _locked_batch,
        apply_review_batch,
        confirmation_phrase,
        load_private_review_batch,
        prepare_review_batch,
        serialize_review_batch,
    )

    db = None
    try:
        loaded = load_private_review_batch(args.arquivo)
        # Preview em sessão própria: nenhum SELECT FOR UPDATE permanece
        # aberto durante o tempo humano de leitura/confirmacao.
        db = _session()
        user_id = _admin_id_por_email(db, args.email)
        preview = prepare_review_batch(db, loaded, user_id)
        db.rollback()
        db.close()
        db = None
        if args.somente_preview:
            _emit(preview, args.json)
            return 0

        if args.json:
            # Mantém stdout como uma única linha machine-readable:
            # o preview antecede a confirmação pelo canal stderr.
            print(
                json.dumps(preview, ensure_ascii=False, sort_keys=True),
                file=sys.stderr,
            )
        else:
            _emit(preview, False)

        if not sys.stdin.isatty():
            raise MultiSheetError("confirmacao_exige_tty_interativo")
        expected = confirmation_phrase(
            loaded.document["batch_id"], loaded.fingerprint
        )
        print(
            f"Confirmação única. Digite exatamente: {expected}",
            file=sys.stderr,
        )
        try:
            supplied = sys.stdin.readline()
        except (EOFError, OSError) as exc:
            raise MultiSheetError(
                "confirmacao_interativa_indisponivel"
            ) from exc
        if supplied == "":
            raise MultiSheetError("confirmacao_interativa_eof")
        if supplied.rstrip("\r\n") != expected:
            raise MultiSheetError("frase_de_confirmacao_incorreta")

        # Somente depois da frase nasce a transação protegida. O lote é
        # travado antes de reler arquivo e fila, que são revalidados ao vivo.
        with serialize_review_batch(loaded.document["batch_id"]):
            db = _session()
            try:
                user_id = _admin_id_por_email(db, args.email)
                _locked_batch(db, loaded.document["batch_id"])
                reloaded = load_private_review_batch(args.arquivo)
                if (
                    reloaded.path != loaded.path
                    or reloaded.fingerprint != loaded.fingerprint
                    or reloaded.document != loaded.document
                    or reloaded.device != loaded.device
                    or reloaded.inode != loaded.inode
                ):
                    raise MultiSheetError(
                        "arquivo_revisao_modificado_apos_preview"
                    )
                receipt = apply_review_batch(
                    db, reloaded, user_id, batch_already_locked=True
                )
                db.commit()
            except Exception:
                # O rollback acontece ANTES de liberar a serialização.
                db.rollback()
                raise
        _emit(receipt, args.json)
        return 0
    except (MultiSheetError, ValueError) as exc:
        if db is not None:
            db.rollback()
        return _migration_error(exc, args.json)
    except Exception:
        if db is not None:
            db.rollback()
        return _migration_error(
            MultiSheetError("revisao_multiaba_lote_falhou"), args.json
        )
    finally:
        if db is not None:
            db.close()


def cmd_migracao_preflight_execucao_multiaba(args) -> int:
    from .migration.executor import preflight_multi_sheet_execution
    from .migration.multisheet import MultiSheetError

    db = _session()
    try:
        result = preflight_multi_sheet_execution(
            db, args.envelope, args.batch, args.backup_evidencia, args.email)
        _emit(result, args.json)
        return 0 if result["ok"] else 1
    except (MultiSheetError, ValueError) as exc:
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_plano_rollback_multiaba(args) -> int:
    from .migration.executor import (
        generate_rollback_plan,
        resolve_admin,
        rollback_plan_report,
    )
    from .migration.multisheet import MultiSheetError

    db = _session()
    try:
        if args.batch_execucao:
            # pós-execução: manifesto REAL a partir da proveniência
            result = rollback_plan_report(db, args.batch_execucao)
            _emit(result, args.json)
            return 0
        if not (args.envelope and args.batch):
            raise ValueError(
                "informe --envelope e --batch (pré-execução) ou "
                "--batch-execucao (pós-execução)")
        admin = resolve_admin(db, args.email)
        result = generate_rollback_plan(
            db, args.envelope, args.batch, user_id=admin.id)
        db.commit()  # persiste apenas o plano sanitizado no lote de dry-run
        _emit(result, args.json)
        return 0
    except (MultiSheetError, ValueError) as exc:
        db.rollback()
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_executar_multiaba(args) -> int:
    """Execução REAL multiaba: portões completos + frase exata interativa.

    A frase NUNCA é aceita por argumento de linha de comando nem por
    variável de ambiente — somente digitada na hora, após portões verdes.
    """
    from .migration.executor import (
        confirmation_phrase_multiaba,
        execute_multi_sheet,
        preflight_multi_sheet_execution,
    )
    from .migration.multisheet import MultiSheetError

    db = _session()
    try:
        pf = preflight_multi_sheet_execution(
            db, args.envelope, args.batch, args.backup_evidencia, args.email)
        if not pf["ok"]:
            reprovados = sorted(
                nome for nome, g in pf["gates"].items() if not g["ok"])
            return _migration_error(
                MultiSheetError("portoes_reprovados", reprovados), args.json)
        frase_esperada = confirmation_phrase_multiaba(args.batch)
        print(
            f"Confirmação final. Digite exatamente: {frase_esperada}",
            file=sys.stderr,
        )
        try:
            frase = input("> ")
        except EOFError:
            frase = ""
        result = execute_multi_sheet(
            db, args.envelope, args.batch, frase, args.backup_evidencia,
            admin_email=args.email,
        )
        db.commit()  # transação ÚNICA: tudo ou nada
        _emit(result, args.json)
        return 0
    except (MultiSheetError, ValueError) as exc:
        db.rollback()
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_reconciliar_multiaba(args) -> int:
    from .migration.executor import (
        reconcile_multi_sheet_execution,
        resolve_admin,
    )
    from .migration.multisheet import MultiSheetError

    db = _session()
    try:
        admin = resolve_admin(db, args.email)
        result = reconcile_multi_sheet_execution(
            db, args.batch_execucao, user_id=admin.id)
        db.commit()  # registra o fechamento (ou a divergência) auditável
        _emit(result, args.json)
        return 0 if result["ok"] else 1
    except (MultiSheetError, ValueError) as exc:
        db.rollback()
        return _migration_error(exc, args.json)
    finally:
        db.close()


def cmd_migracao_rollback_multiaba(args) -> int:
    """Rollback seletivo — só quando provadamente seguro; frase interativa."""
    from .migration.executor import (
        rollback_multi_sheet_execution,
        rollback_phrase_multiaba,
    )
    from .migration.multisheet import MultiSheetError

    db = _session()
    try:
        frase_esperada = rollback_phrase_multiaba(args.batch_execucao)
        print(
            f"Confirmação final. Digite exatamente: {frase_esperada}",
            file=sys.stderr,
        )
        try:
            frase = input("> ")
        except EOFError:
            frase = ""
        result = rollback_multi_sheet_execution(
            db, args.batch_execucao, frase, admin_email=args.email)
        db.commit()
        _emit(result, args.json)
        return 0
    except (MultiSheetError, ValueError) as exc:
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


# ------------------------------------------- consolidação de parceiro (M20)

FRASE_CONSOLIDACAO = "CONSOLIDAR PARCEIRO DUPLICADO"


def _partner_snapshot(db, partner) -> dict:
    """Mapa técnico do parceiro — sem nenhum dado de paciente."""
    from sqlalchemy import select as _select

    from .models import (
        Followup as _F,
        PartnerContact as _C,
        PartnerReferral as _R,
        PartnerUnit as _U,
        PartnerUnitConfig as _Cfg,
        Partnership as _P,
        SpirometryExam as _E,
    )

    unidades = db.execute(_select(_U).where(_U.partner_id == partner.id)).scalars().all()
    unit_ids = [u.id for u in unidades]
    agendas = (
        db.execute(_select(_Cfg).where(_Cfg.partner_unit_id.in_(unit_ids))).scalars().all()
        if unit_ids else []
    )
    exames = db.execute(_select(_E).where(_E.partner_id == partner.id)).scalars().all()
    return {
        "public_code": partner.public_code,
        "nome": partner.nome,
        "arquivado": partner.arquivado,
        "merged_into_partner_id": partner.merged_into_partner_id,
        "unidades": sorted(f"{u.public_code}:{u.nome}:{'ativa' if u.ativo else 'inativa'}"
                           for u in unidades),
        "agendas": sorted(f"{c.dia_semana} {c.horario_inicio}-{c.horario_fim}"
                          for c in agendas),
        "exames": sorted(f"{e.public_code}@{e.partner_unit_id}" for e in exames),
        "contatos": sorted(
            c.public_code
            for c in db.execute(_select(_C).where(_C.partner_id == partner.id)).scalars().all()
        ),
        "parcerias": sorted(
            p.public_code
            for p in db.execute(_select(_P).where(_P.partner_id == partner.id)).scalars().all()
        ),
        "encaminhamentos": len(
            db.execute(_select(_R).where(_R.partner_id == partner.id)).scalars().all()
        ),
        "followups": len(
            db.execute(_select(_F).where(_F.partner_id == partner.id)).scalars().all()
        ),
    }


def _totais_globais(db) -> dict:
    from sqlalchemy import func as _func, select as _select

    from .models import (
        Consultation as _Con,
        FinancialEntry as _Fin,
        Followup as _Fup,
        Person as _Pes,
        SpirometryExam as _Esp,
    )

    return {
        "pessoas": db.execute(_select(_func.count()).select_from(_Pes)).scalar_one(),
        "exames": db.execute(_select(_func.count()).select_from(_Esp)).scalar_one(),
        "consultas": db.execute(_select(_func.count()).select_from(_Con)).scalar_one(),
        "followups": db.execute(_select(_func.count()).select_from(_Fup)).scalar_one(),
        "lancamentos": db.execute(_select(_func.count()).select_from(_Fin)).scalar_one(),
        "soma_lancamentos": str(
            db.execute(_select(_func.coalesce(_func.sum(_Fin.valor), 0))).scalar_one()
        ),
        "exames_com_unidade": db.execute(
            _select(_func.count()).select_from(_Esp).where(_Esp.partner_unit_id.is_not(None))
        ).scalar_one(),
    }


def cmd_consolidar_parceiro(args) -> int:
    """Consolida uma duplicata de parceiro no canônico — dry-run por padrão.

    Fail-closed: verifica TODOS os relacionamentos depois da migração e
    antes do commit; qualquer divergência desfaz a transação inteira.
    """
    from .services.partner_merge import (
        PartnerMergeError,
        merge_partner,
        resolve_partner,
    )

    db = _session()
    try:
        canonical, _ = resolve_partner(db, args.canonico)
        duplicate, _ = resolve_partner(db, args.duplicata)
        if canonical is None or duplicate is None:
            print("ERRO: parceiro canônico ou duplicata não encontrado.", file=sys.stderr)
            return 1
        if canonical.id == duplicate.id:
            print("ERRO: canônico e duplicata são o mesmo parceiro.", file=sys.stderr)
            return 1

        antes = {
            "duplicata": _partner_snapshot(db, duplicate),
            "canonico": _partner_snapshot(db, canonical),
            "totais": _totais_globais(db),
        }

        # Portão de segurança: o canônico precisa ser mesmo o que carrega a
        # operação real (unidade + exames). Sem isso, nada é executado.
        if not antes["canonico"]["unidades"]:
            print("ERRO: o parceiro canônico não tem unidade — abortado.", file=sys.stderr)
            return 2
        if len(antes["canonico"]["exames"]) < len(antes["duplicata"]["exames"]):
            print("ERRO: a duplicata tem mais exames que o canônico — abortado.",
                  file=sys.stderr)
            return 2

        if not args.executar:
            print(json.dumps({"modo": "dry_run", "antes": antes},
                             ensure_ascii=False, indent=2))
            return 0

        frase = input(f"Digite exatamente '{FRASE_CONSOLIDACAO}' para confirmar: ")
        if frase.strip() != FRASE_CONSOLIDACAO:
            print("ERRO: frase de confirmação incorreta — nada foi alterado.",
                  file=sys.stderr)
            return 2

        resultado = merge_partner(db, duplicate, canonical)
        depois = {
            "duplicata": _partner_snapshot(db, duplicate),
            "canonico": _partner_snapshot(db, canonical),
            "totais": _totais_globais(db),
        }

        problemas = _verificar_consolidacao(db, antes, depois, duplicate, canonical)
        if problemas:
            db.rollback()
            print(json.dumps({"modo": "abortado", "problemas": problemas,
                              "antes": antes}, ensure_ascii=False, indent=2),
                  file=sys.stderr)
            return 3

        db.commit()
        print(json.dumps({"modo": "executado", "antes": antes, "depois": depois,
                          "migracao": resultado}, ensure_ascii=False, indent=2))
        return 0
    except PartnerMergeError as exc:
        db.rollback()
        print(f"ERRO de consolidação: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        db.rollback()
        print(f"ERRO inesperado: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def _verificar_consolidacao(db, antes, depois, duplicate, canonical) -> list[str]:
    """Portões pós-migração. Lista vazia = seguro para commit."""
    from .services.partner_merge import resolve_partner

    problemas: list[str] = []

    exames_antes = set(antes["duplicata"]["exames"]) | set(antes["canonico"]["exames"])
    if set(depois["canonico"]["exames"]) != exames_antes:
        problemas.append("exames do canônico não conferem com a soma anterior")
    if depois["duplicata"]["exames"]:
        problemas.append("a duplicata ainda tem exame vinculado")
    if any("@None" in e for e in depois["canonico"]["exames"]):
        problemas.append("algum exame perdeu a unidade")
    if depois["canonico"]["agendas"] != sorted(
        set(antes["canonico"]["agendas"]) | set(antes["duplicata"]["agendas"])
    ):
        problemas.append("agendas da unidade mudaram")
    if antes["totais"] != depois["totais"]:
        problemas.append("totais globais (pessoas/exames/consultas/financeiro) mudaram")
    if not duplicate.arquivado or duplicate.merged_into_partner_id != canonical.id:
        problemas.append("a duplicata não ficou arquivada apontando para o canônico")
    if canonical.arquivado:
        problemas.append("o canônico ficou arquivado")

    resolvido, _ = resolve_partner(db, antes["duplicata"]["public_code"])
    if resolvido is None or resolvido.id != canonical.id:
        problemas.append("o código antigo não resolve para o canônico")

    contatos_esperados = len(
        set(antes["canonico"]["contatos"]) | set(antes["duplicata"]["contatos"])
    )
    if len(depois["canonico"]["contatos"]) > contatos_esperados:
        problemas.append("contatos duplicados no canônico")
    if depois["duplicata"]["contatos"]:
        problemas.append("a duplicata ainda tem contato vinculado")
    if set(depois["canonico"]["parcerias"]) != set(
        antes["canonico"]["parcerias"]) | set(antes["duplicata"]["parcerias"]
    ):
        problemas.append("parcerias não foram integralmente migradas")
    return problemas


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

    mp = _mig_parser(
        "dry-run-multiaba",
        "Dry-run do envelope bruto versionado; execução real sempre bloqueada",
        cmd_migracao_dry_run_multiaba,
    )
    mp.add_argument(
        "--envelope", required=True,
        help="Nome simples do envelope no diretório privado aprovado",
    )

    mp = _mig_parser(
        "status-multiaba",
        "Status, bloqueios, revisão e reconciliação prévia multiaba",
        cmd_migracao_status_multiaba,
    )
    mp.add_argument("--batch", default=None)

    mp = _mig_parser(
        "revisar-multiaba",
        "Registra decisão humana por token privado (não executa nem mescla)",
        cmd_migracao_revisar_multiaba,
    )
    mp.add_argument("--batch", required=True)
    mp.add_argument("--referencia", required=True)
    mp.add_argument(
        "--decisao", required=True,
        choices=("resolvido", "excluido", "adiado",
                 "vincular_candidato", "criar_pessoa",
                 "create_minor_patient_with_guardian",
                 "manter_primeira", "manter_segunda", "manter_ambas"),
    )
    mp.add_argument("--mapping-version", required=True)
    mp.add_argument(
        "--responsavel-arquivo",
        default=None,
        help=(
            "Só com --decisao create_minor_patient_with_guardian: caminho "
            "de um JSON PRIVADO (dentro de M15_IMPORT_PRIVATE_DIR, 0600) "
            "com minor_name/guardian_name/guardian_phone/relationship_type "
            "quando a fonte real não separa os dois em colunas próprias. "
            "Nunca informar nome/telefone diretamente na linha de comando."
        ),
    )

    mp = _mig_parser(
        "revisar-multiaba-em-lote",
        "Preview consolidado e gravação atômica/idempotente de decisões; "
        "nunca executa a migração operacional",
        cmd_migracao_revisar_multiaba_em_lote,
    )
    mp.add_argument(
        "--arquivo",
        required=True,
        help="JSON privado dentro de M15_IMPORT_PRIVATE_DIR",
    )
    mp.add_argument(
        "--somente-preview",
        action="store_true",
        help="Valida e exibe o preview sem solicitar confirmação nem escrever",
    )

    mp = _mig_parser(
        "preflight-execucao-multiaba",
        "Avalia TODOS os portões da execução multiaba (não escreve nada)",
        cmd_migracao_preflight_execucao_multiaba,
    )
    mp.add_argument("--envelope", required=True)
    mp.add_argument("--batch", required=True,
                    help="ID exato do lote de dry-run multiaba")
    mp.add_argument("--backup-evidencia", default=None)

    mp = _mig_parser(
        "plano-rollback-multiaba",
        "Gera o plano de rollback: planejado (pré-execução, obrigatório "
        "antes do execute) ou real (pós-execução, via proveniência)",
        cmd_migracao_plano_rollback_multiaba,
    )
    mp.add_argument("--envelope", default=None)
    mp.add_argument("--batch", default=None,
                    help="ID do lote de dry-run (modo pré-execução)")
    mp.add_argument("--batch-execucao", default=None,
                    help="ID do lote executado (modo pós-execução)")

    mp = _mig_parser(
        "executar-multiaba",
        "Execução REAL multiaba — admin-only, portões completos, frase "
        "exata digitada interativamente (nunca por argumento/variável)",
        cmd_migracao_executar_multiaba,
    )
    mp.add_argument("--envelope", required=True)
    mp.add_argument("--batch", required=True,
                    help="ID exato do lote de dry-run multiaba revisado")
    mp.add_argument("--backup-evidencia", required=True)

    mp = _mig_parser(
        "reconciliar-multiaba",
        "Fechamento exato pós-execução; o lote só conclui se tudo fechar",
        cmd_migracao_reconciliar_multiaba,
    )
    mp.add_argument("--batch-execucao", required=True)

    mp = _mig_parser(
        "rollback-multiaba",
        "Rollback seletivo do lote executado — só quando provadamente "
        "seguro; frase exata digitada interativamente",
        cmd_migracao_rollback_multiaba,
    )
    mp.add_argument("--batch-execucao", required=True)

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

    p = sub.add_parser(
        "consolidar-parceiro",
        help="Consolida duplicata de parceiro no canônico (dry-run por padrão)",
    )
    p.add_argument("--duplicata", required=True,
                   help="Código público, id ou id legado do parceiro obsoleto")
    p.add_argument("--canonico", required=True,
                   help="Código público, id ou id legado do parceiro canônico")
    p.add_argument("--executar", action="store_true",
                   help="Executa de verdade (pede a frase exata interativamente)")
    p.set_defaults(func=cmd_consolidar_parceiro)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
