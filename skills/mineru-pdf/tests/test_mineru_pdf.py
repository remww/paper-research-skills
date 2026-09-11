#!/usr/bin/env python3
"""Offline tests for the standalone MinerU PDF wrapper."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mineru_pdf.py"


FAKE_MINERU = '''#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
pdf = pathlib.Path(args[args.index("-p") + 1])
out = pathlib.Path(args[args.index("-o") + 1])
dest = out / pdf.stem / "hybrid_auto"
dest.mkdir(parents=True, exist_ok=True)
(dest / (pdf.stem + ".md")).write_text("# Parsed\\n", encoding="utf-8")
images = dest / "images"
images.mkdir(exist_ok=True)
(images / "figure.png").write_bytes(b"fixture")
'''


class MineruPdfTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mineru-pdf-")
        self.root = Path(self.temp.name)
        self.pdf = self.root / "paper.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\nfixture\n")
        self.output = self.root / "chosen output"
        self.fake = self.root / "fake-mineru"
        self.fake.write_text(FAKE_MINERU, encoding="utf-8")
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.temp.cleanup()

    def run_tool(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(self.pdf),
                "--output-dir", str(self.output),
                "--mineru-bin", str(self.fake),
                *extra,
            ],
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_uses_caller_output_and_writes_no_record_by_default(self):
        completed = self.run_tool()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_dir"], str(self.output.resolve()))
        self.assertEqual(result["image_count"], 1)
        self.assertEqual(len(result["markdown"]), 1)
        self.assertFalse(any(self.root.glob("*.json")))

    def test_record_is_opt_in_and_existing_output_is_reused(self):
        first = self.run_tool()
        self.assertEqual(first.returncode, 0)
        record = self.root / "records" / "conversion.json"
        second = self.run_tool("--record", str(record))
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        result = json.loads(second.stdout)
        self.assertTrue(result["reused"])
        self.assertEqual(result["record_path"], str(record.resolve()))
        self.assertTrue(record.is_file())

    def test_rejects_non_pdf(self):
        self.pdf.write_text("not a PDF", encoding="utf-8")
        completed = self.run_tool()
        self.assertEqual(completed.returncode, 2)
        self.assertIn("PDF header", completed.stderr)


if __name__ == "__main__":
    unittest.main()
