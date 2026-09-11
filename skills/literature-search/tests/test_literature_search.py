#!/usr/bin/env python3
"""Offline tests for the standalone literature-search skill."""

from __future__ import annotations

import contextlib
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
litsearch = importlib.import_module("litsearch")
litfetch = importlib.import_module("litfetch")
litscreen = importlib.import_module("litscreen")


class LiteratureSearchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="literature-search-")
        self.workspace = Path(self.temp.name) / "custom workspace"
        litsearch._active_root[0] = None

    def tearDown(self):
        litsearch._active_root[0] = None
        self.temp.cleanup()

    def test_search_writes_to_selected_workspace(self):
        record = {
            "paperId": "fixture-paper",
            "arxivId": "2501.00001",
            "doi": None,
            "title": "Fixture Paper",
            "abstract": "A bounded fixture.",
            "venue": "",
            "year": 2025,
            "publicationDate": "2025-01-01",
            "citationCount": 0,
        }
        argv = [
            "litsearch.py", "search", "fixture paper", "--limit", "1",
            "--workspace", str(self.workspace),
        ]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            litsearch, "ch_search", return_value=([record], {"endpoint": "fixture"})
        ), contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(litsearch.main())

        result_files = list((self.workspace / "searches").glob("*.json"))
        self.assertEqual(len(result_files), 1)
        result = json.loads(result_files[0].read_text(encoding="utf-8"))
        self.assertEqual(result["channel"], "search")
        self.assertEqual(result["results"][0]["paperId"], "fixture-paper")
        self.assertFalse((self.workspace / "kb").exists())

        entries, files, added, bad, _ = litscreen.sync(self.workspace, litsearch._key)
        self.assertEqual((files, added, bad), (1, 1, []))
        ledger = litscreen.save(self.workspace, entries)
        self.assertEqual(ledger, self.workspace / "screening" / "ledger.jsonl")

    def test_fetch_has_no_pdf_converter_dependency_or_artifact(self):
        html = b'<html><body><article class="ltx_document"><h1>Fixture</h1><p>Hello.</p></article></body></html>'
        responses = [
            (200, html, "text/html", "https://arxiv.org/html/2501.00001"),
            (200, b"%PDF-1.4\nfixture", "application/pdf", "https://arxiv.org/pdf/2501.00001"),
        ]
        with mock.patch.object(litfetch, "_get", side_effect=responses):
            result = litfetch.fetch_one(self.workspace, "2501.00001")

        self.assertEqual(result["source_status"], "obtained")
        self.assertNotIn("mineru", json.dumps(result).lower())
        self.assertTrue(any(a["kind"] == "markdown" for a in result["artifacts"]))
        self.assertTrue(any(a["kind"] == "pdf" for a in result["artifacts"]))
        self.assertTrue((self.workspace / result["_record_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
