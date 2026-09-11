#!/usr/bin/env python3
"""Static regression tests for the adaptive deep-reading skill contract."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"
PROTOCOL = ROOT / "references" / "adaptive-note-protocol.md"


class PaperDeepReadSkillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = SKILL.read_text(encoding="utf-8")
        self.protocol = PROTOCOL.read_text(encoding="utf-8")
        self.all_text = self.skill + "\n" + self.protocol
        self.normalized = re.sub(r"\s+", " ", self.all_text)

    def test_agent_skill_frontmatter(self) -> None:
        match = re.match(r"^---\n(?P<body>.*?)\n---\n", self.skill, re.S)
        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertRegex(body, r"(?m)^name: paper-deep-read$")
        description = re.search(r"(?m)^description: (.+)$", body)
        self.assertIsNotNone(description)
        self.assertLessEqual(len(description.group(1)), 1024)

    def test_contract_is_adaptive_and_evidence_linked(self) -> None:
        required = (
            "3-7 focus questions",
            "Do not copy a fixed domain template",
            "exact evidence locator",
            "what the paper does not establish",
            "downstream research implication",
            "Separate stable facts from current assessment",
            "actual read depth",
            "negative evidence",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.normalized)

    def test_no_project_specific_research_direction_is_embedded(self) -> None:
        fragments = (
            "T" + "004",
            "V" + "LA",
            "C" + "oT",
            "C" + "oC",
            "RESEARCH" + "_SPEC",
            "kb" + "/ACTIVE",
        )
        for fragment in fragments:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, self.all_text)

    def test_no_fixed_storage_layout_or_format(self) -> None:
        self.assertIn("do not invent a repository layout", self.normalized)
        self.assertIn("Do not require Markdown", self.normalized)
        self.assertNotIn("papers/", self.all_text)
        self.assertNotIn("notes/", self.all_text)


if __name__ == "__main__":
    unittest.main()
