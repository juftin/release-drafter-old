#!/usr/bin/env python3
"""
test_parse_commits.py: Test suite for commit parsing, SemVer resolution, and changelog mixing.
"""

from pathlib import Path
import sys
import unittest

# Add repo root to import path
REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT_DIR = REPO_ROOT
sys.path.insert(0, str(REPO_ROOT))

from scripts.parse_commits import (
    Commit,
    SemVer,
    VersionIncrement,
    categorize_commits,
    extract_co_authors,
    extract_github_login,
    format_commit_item,
    load_yaml_config,
    merge_commits_into_release_body,
    parse_yaml_fallback,
    resolve_commit_semver_increment,
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

    def test_semver_parsing_and_bumping(self):
        v = SemVer.parse("v1.2.3")
        self.assertEqual(v.major, 1)
        self.assertEqual(v.minor, 2)
        self.assertEqual(v.patch, 3)
        self.assertEqual(v.to_tag_string(), "v1.2.3")
        self.assertEqual(v.to_version_string(), "1.2.3")

        v_patch = v.bump(VersionIncrement.PATCH)
        self.assertEqual(v_patch.to_tag_string(), "v1.2.4")

        v_minor = v.bump(VersionIncrement.MINOR)
        self.assertEqual(v_minor.to_tag_string(), "v1.3.0")

        v_major = v.bump(VersionIncrement.MAJOR)
        self.assertEqual(v_major.to_tag_string(), "v2.0.0")

        # Comparisons
        self.assertTrue(v < v_patch)
        self.assertTrue(v_patch < v_minor)
        self.assertTrue(v_minor < v_major)

    def test_semver_resolution_from_commits(self):
        c_patch = Commit(
            sha="111", short_sha="111", author_name="alice", author_email="a@b.com", subject="fix: bugfix"
        )
        c_feat = Commit(
            sha="222", short_sha="222", author_name="bob", author_email="b@b.com", subject="feat: new feature"
        )
        c_breaking = Commit(
            sha="333", short_sha="333", author_name="carol", author_email="c@b.com", subject="feat!: breaking api"
        )

        inc1 = resolve_commit_semver_increment([c_patch], self.conventional_config)
        self.assertEqual(inc1, VersionIncrement.PATCH)

        inc2 = resolve_commit_semver_increment([c_patch, c_feat], self.conventional_config)
        self.assertEqual(inc2, VersionIncrement.MINOR)

        inc3 = resolve_commit_semver_increment([c_patch, c_feat, c_breaking], self.conventional_config)
        self.assertEqual(inc3, VersionIncrement.MAJOR)

    def test_author_login_and_co_authors(self):
        login1 = extract_github_login("12345+octocat@users.noreply.github.com", "Mona Lisa")
        self.assertEqual(login1, "octocat")

        login2 = extract_github_login("mona@example.com", "octocat")
        self.assertEqual(login2, "octocat")

        body = """Commit subject line

Co-authored-by: Alice Smith <111+alice@users.noreply.github.com>
Co-authored-by: Bob Jones <bob@example.com>
"""
        co_authors = extract_co_authors(body)
        self.assertEqual(co_authors, ["alice", "Bob Jones"])

        commit = Commit(
            sha="abc1234",
            short_sha="abc1234",
            author_name="octocat",
            author_email="octocat@github.com",
            subject="feat: multi author feature",
            author_login="octocat",
            co_authors=co_authors,
        )
        self.assertEqual(commit.author_handle, "@octocat (with @alice, @Bob Jones)")

    def test_format_commit_item_with_template(self):
        commit = Commit(
            sha="abcdef123456",
            short_sha="abcdef1",
            author_name="octocat",
            author_email="octocat@github.com",
            subject="feat: fancy button",
            author_login="octocat",
        )
        template = "- $TITLE (#$NUMBER) by @$AUTHOR in $HASH"
        formatted = format_commit_item(commit, template=template, repo_url="https://github.com/foo/bar")
        self.assertIn("feat: fancy button", formatted)
        self.assertIn("@octocat", formatted)
        self.assertIn("[abcdef1](https://github.com/foo/bar/commit/abcdef123456)", formatted)

    def test_merge_no_changes(self):
        body = "## What's Changed\n\n* No changes\n\n**Full Changelog**: https://..."
        commit = Commit(
            sha="1234567890",
            short_sha="1234567",
            author_name="alice",
            author_email="a@b.com",
            subject="feat: add feature",
            author_login="alice",
        )
        categorized = {"✨ Features & Improvements": [commit]}
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

        commit_feat = Commit(
            sha="aaaaaaa",
            short_sha="aaaaaaa",
            author_name="alice",
            author_email="a@b.com",
            subject="feat: direct commit",
            author_login="alice",
        )
        commit_ci = Commit(
            sha="bbbbbbb",
            short_sha="bbbbbbb",
            author_name="bob",
            author_email="b@b.com",
            subject="ci: update workflow",
            author_login="bob",
        )

        categorized = {
            "🚀 Features & Enhancements": [commit_feat],
            "🧰 Maintenance & Code Quality": [commit_ci],
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

    def test_load_yaml_config(self):
        config_path = str(REPO_ROOT / "configs" / "conventional-commits.yaml")
        cfg = load_yaml_config(config_path)
        self.assertIn("categories", cfg)
        self.assertIn("version-resolver", cfg)
        self.assertIn("autolabeler", cfg)
        self.assertGreaterEqual(len(cfg["categories"]), 5)


if __name__ == "__main__":
    unittest.main()
