#!/usr/bin/env python3
"""Launchers reais em árvore temporária; Python/Tailscale substituídos por stubs.

Sem servidor, rede, credencial, banco ou dados reais. Prova que nenhum fallback
estático abre o painel e que o launcher local preserva o servidor oficial.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent


class SafeLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="life launchers ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scripts = self.root / "painel-soprolife/scripts"
        self.scripts.mkdir(parents=True)
        for name in ("start-local.sh", "start-tailscale.sh"):
            shutil.copyfile(SCRIPTS / name, self.scripts / name)
        self.proxy = self.scripts / "command-center-local-server.py"
        self.proxy.write_text("# Synthetic placeholder; never executed.\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.marker = self.root / "calls"
        python = self.bin / "python3"
        python.write_text(
            '#!/bin/bash\n'
            'printf "%s\\n" "$PWD" "$SOPROLIFE_PANEL_HOST" '
            '"${SOPROLIFE_PANEL_PORT:-8765}" "$@" > "$LAUNCHER_TEST_MARKER"\n'
            'exit "${LAUNCHER_TEST_EXIT:-0}"\n'
        )
        python.chmod(0o700)
        tailscale = self.bin / "tailscale"
        tailscale.write_text('#!/bin/bash\necho forbidden > "$LAUNCHER_TEST_MARKER"\nexit 99\n')
        tailscale.chmod(0o700)
        # Lista mínima; não herdar configurações de serviços/credenciais reais.
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LAUNCHER_TEST_MARKER": str(self.marker),
        }

    def run_launcher(self, name):
        return subprocess.run(
            ["bash", str(self.scripts / name)], cwd=self.root.parent,
            env=self.env, capture_output=True, text=True, timeout=5,
        )

    def test_local_uses_official_proxy_from_any_directory(self):
        result = self.run_launcher("start-local.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.marker.read_text().splitlines(), [
            str(self.root), "127.0.0.1", "8765",
            "painel-soprolife/scripts/command-center-local-server.py",
        ])

    def test_local_missing_proxy_refuses_without_starting_python(self):
        self.proxy.unlink()
        result = self.run_launcher("start-local.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("servidor autenticado", result.stderr)
        self.assertFalse(self.marker.exists())

    def test_local_forces_loopback_even_with_remote_environment(self):
        self.env["SOPROLIFE_PANEL_HOST"] = "0.0.0.0"
        result = self.run_launcher("start-local.sh")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.marker.read_text().splitlines()[1], "127.0.0.1")

    def test_local_preserves_configured_port(self):
        self.env["SOPROLIFE_PANEL_PORT"] = "8876"
        result = self.run_launcher("start-local.sh")
        self.assertEqual(result.returncode, 0)
        self.assertIn("127.0.0.1:8876/", result.stdout)
        self.assertEqual(self.marker.read_text().splitlines()[2], "8876")

    def test_local_propagates_proxy_failure_without_fallback(self):
        self.env["LAUNCHER_TEST_EXIT"] = "17"
        result = self.run_launcher("start-local.sh")
        self.assertEqual(result.returncode, 17)
        self.assertEqual(len(self.marker.read_text().splitlines()), 4)

    def test_legacy_tailscale_refuses_without_any_external_command(self):
        result = self.run_launcher("start-tailscale.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("desabilitado", result.stderr)
        self.assertIn("HTTPS privado", result.stderr)
        self.assertFalse(self.marker.exists())

    def test_legacy_tailscale_refuses_even_without_proxy(self):
        self.proxy.unlink()
        self.assertEqual(self.run_launcher("start-tailscale.sh").returncode, 1)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
