#!/usr/bin/env python3
"""
test_parse_commits.py: Test direct commit parsing and PR mixing logic.
"""

import sys
import unittest
from pathlib import Path

# Add repo root to import path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.parse_commits import (
    categorize_commits,
    format_commit_item,
    merge_commits_into_release_body,
    parse_yaml_fallback,
)


class TestParseCommits(unittest.TestCase):
    def setUp(self):
        with open(REPO_ROOT / "configs" / "gitmoji.yaml", "r", encoding="utf-8") as f:
            self.gitmoji_config = parse_yaml_fallback(f.read())
        with open(REPO_ROOT / "configs" / "conventional-commits.yaml", "r", encoding="utf-8") as f:
            self.conventional_config = parse_yaml_fallback(f.read())

    def test_yaml_parsing(self):
        self.assertGreater(len(self.gitmoji_config.get("categories", [])), 0)
        self.assertGreater(len(self.gitmoji_config.get("autolabeler", [])), 0)
        self.assertGreater(len(self.conventional_config.get("categories", [])), 0)
        self.assertGreater(len(self.conventional_config.get("autolabeler", [])), 0)

    def test_merge_no_changes(self):
        body = "## What's Changed\n\n* No changes\n\n**Full Changelog**: https://..."
        categorized = {
            "✨ Features & Improvements": [
                {"subject": "feat: add feature", "sha": "1234567890", "short_sha": "1234567", "author": "alice"}
            ]
        }
        res = merge_commits_into_release_body(body, categorized, ["✨ Features & Improvements"])
        self.assertNotIn("* No changes", res)
        self.assertIn("### ✨ Features & Improvements", res)
        self.assertIn("* feat: add feature (1234567) @alice", res)
        self.assertIn("**Full Changelog**: https://...", res)

    def test_merge_alongside_prs(self):
        body = """## What's Changed

### 🚀 Features & Enhancements
* Add PR feature by @octocat in https://github.com/foo/bar/pull/1

### 🐛 Bug Fixes
* Fix PR bug by @mona in https://github.com/foo/bar/pull/2

**Full Changelog**: https://github.com/foo/bar/compare/v1.0.0...v1.1.0"""

        categorized = {
            "🚀 Features & Enhancements": [
                {"subject": "feat: direct commit", "sha": "aaaaaaa", "short_sha": "aaaaaaa", "author": "alice"}
            ],
            "🧰 Maintenance & Code Quality": [
                {"subject": "ci: update workflow", "sha": "bbbbbbb", "short_sha": "bbbbbbb", "author": "bob"}
            ],
        }
        order = ["🚀 Features & Enhancements", "🐛 Bug Fixes", "🧰 Maintenance & Code Quality"]

        res = merge_commits_into_release_body(body, categorized, order)

        # PR items preserved
        self.assertIn("* Add PR feature by @octocat in https://github.com/foo/bar/pull/1", res)
        self.assertIn("* Fix PR bug by @mona in https://github.com/foo/bar/pull/2", res)

        # Direct commit mixed into existing category
        self.assertIn("* feat: direct commit (aaaaaaa) @alice", res)

        # New category created with direct commit
        self.assertIn("### 🧰 Maintenance & Code Quality", res)
        self.assertIn("* ci: update workflow (bbbbbbb) @bob", res)

        # Footer preserved
        self.assertIn("**Full Changelog**: https://github.com/foo/bar/compare/v1.0.0...v1.1.0", res)

    def test_merge_flat_body(self):
        body = """## What's Changed
* PR #1 by @alice
* PR #2 by @bob

**Full Changelog**: https://..."""

        categorized = {
            "✨ Features": [{"subject": "feat: flat test", "sha": "ccccccc", "short_sha": "ccccccc", "author": "charlie"}]
        }
        res = merge_commits_into_release_body(body, categorized, ["✨ Features"])
        self.assertIn("### ✨ Features", res)
        self.assertIn("* feat: flat test (ccccccc) @charlie", res)
        self.assertIn("**Full Changelog**: https://...", res)


if __name__ == "__main__":
    unittest.main()
