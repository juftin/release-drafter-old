#!/usr/bin/env python3
"""
parse_commits.py: Parse direct Git commits and enrich Release Drafter release notes.

When Release Drafter finds no merged pull requests (* No changes) or when direct commits
exist, this script analyzes commits between the previous release tag and HEAD,
categorizes them according to the active Release Drafter configuration preset
(Conventional Commits, Gitmoji, or Hybrid), formats them into Markdown, and updates
the GitHub draft release.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


# Standard Conventional Commits fallback label mapping
CONVENTIONAL_PREFIX_RULES = [
    (re.compile(r"^([a-z]+(\([^\)]+\))?!:|BREAKING CHANGE)", re.IGNORECASE), "breaking"),
    (re.compile(r"^(feat|feature)(\([^\)]+\))?:", re.IGNORECASE), "feat"),
    (re.compile(r"^(fix|bugfix)(\([^\)]+\))?:", re.IGNORECASE), "fix"),
    (re.compile(r"^(perf|performance)(\([^\)]+\))?:", re.IGNORECASE), "perf"),
    (re.compile(r"^(docs|documentation)(\([^\)]+\))?:", re.IGNORECASE), "docs"),
    (re.compile(r"^(deps|dependencies)(\([^\)]+\))?:", re.IGNORECASE), "dependencies"),
    (re.compile(r"^(refactor|style)(\([^\)]+\))?:", re.IGNORECASE), "refactor"),
    (re.compile(r"^(build|ci|chore|tooling)(\([^\)]+\))?:", re.IGNORECASE), "ci"),
    (re.compile(r"^test(\([^\)]+\))?:", re.IGNORECASE), "test"),
    (re.compile(r"^revert(\([^\)]+\))?:", re.IGNORECASE), "revert"),
]


def parse_yaml_fallback(content: str) -> Dict[str, Any]:
    """Lightweight pure-Python parser for Release Drafter YAML configs."""
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(content)
        if isinstance(loaded, dict):
            return loaded
    except Exception:
        pass

    result: Dict[str, Any] = {}
    current_section: Optional[str] = None
    current_list: Optional[List[Any]] = None
    current_item: Optional[Dict[str, Any]] = None
    current_key: Optional[str] = None

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())

        # Top-level keys: categories:, autolabeler:, etc.
        if indent == 0 and stripped.endswith(":"):
            current_section = stripped[:-1].strip()
            current_list = []
            result[current_section] = current_list
            current_item = None
            current_key = None
            continue

        if current_section in ("categories", "autolabeler") and current_list is not None:
            if indent == 2 and stripped.startswith("- "):
                current_item = {}
                current_list.append(current_item)
                current_key = None
                rest = stripped[2:].strip()
                if ":" in rest:
                    k, v = rest.split(":", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    current_item[k] = v if v else []
                    current_key = k if not v else None
            elif indent >= 4 and current_item is not None:
                if stripped.startswith("- ") and current_key:
                    val = stripped[2:].strip().strip("'\"")
                    if isinstance(current_item.get(current_key), list):
                        current_item[current_key].append(val)
                elif ":" in stripped:
                    k, v = stripped.split(":", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if v:
                        current_item[k] = v
                        current_key = None
                    else:
                        current_key = k
                        current_item[k] = []

    return result


def ensure_full_git_history() -> None:
    """Ensure the Git repository has full history and tags for accurate commit parsing."""
    try:
        is_shallow = subprocess.check_output(
            ["git", "rev-parse", "--is-shallow-repository"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if is_shallow == "true":
            print("Shallow repository detected; fetching full history...")
            subprocess.run(["git", "fetch", "--unshallow"], check=False, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "fetch", "--tags"], check=False, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def get_git_commit_range() -> Tuple[Optional[str], str]:
    """Determine the previous tag and commit range to inspect."""
    ensure_full_git_history()
    try:
        cmd = ["git", "describe", "--tags", "--abbrev=0"]
        prev_tag = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
        commit_range = f"{prev_tag}..HEAD"
        return prev_tag, commit_range
    except subprocess.CalledProcessError:
        return None, "HEAD"


def get_commits(commit_range: str) -> List[Dict[str, str]]:
    """Retrieve commits in the given range as a list of dicts."""
    fmt = "%H%x1f%h%x1f%an%x1f%s"
    cmd = ["git", "log", f"--format={fmt}"]
    if ".." in commit_range:
        cmd.append(commit_range)
    else:
        cmd.extend(["-n", "100", "HEAD"])

    try:
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
    except subprocess.CalledProcessError:
        return []

    commits: List[Dict[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.strip().split("\x1f")
        if len(parts) >= 4:
            sha, short_sha, author, subject = parts[0], parts[1], parts[2], parts[3]
            if subject.startswith("Merge branch ") or subject.startswith("Merge pull request "):
                continue
            commits.append({
                "sha": sha,
                "short_sha": short_sha,
                "author": author,
                "subject": subject,
            })
    return commits


def parse_regex(pattern_str: str) -> Optional[re.Pattern]:
    """Parse a JS-style regex string like '/^feat/i' into a Python Pattern."""
    if not (pattern_str.startswith("/") and pattern_str.rfind("/") > 0):
        try:
            return re.compile(pattern_str)
        except re.error:
            return None

    last_slash = pattern_str.rfind("/")
    regex_body = pattern_str[1:last_slash]
    flags_str = pattern_str[last_slash + 1 :]

    flags = 0
    if "i" in flags_str:
        flags |= re.IGNORECASE
    if "m" in flags_str:
        flags |= re.MULTILINE
    if "s" in flags_str:
        flags |= re.DOTALL

    try:
        return re.compile(regex_body, flags)
    except re.error:
        return None


def categorize_commits(
    commits: List[Dict[str, str]], config: Dict[str, Any]
) -> Dict[str, List[Dict[str, str]]]:
    """Categorize commits according to autolabeler rules and category mappings."""
    categories_cfg = config.get("categories", [])
    autolabeler_cfg = config.get("autolabeler", [])

    label_to_category: Dict[str, str] = {}
    category_order: List[str] = []

    for cat in categories_cfg:
        title = cat.get("title", "")
        if not title:
            continue
        category_order.append(title)
        labels = cat.get("labels", [])
        if isinstance(labels, str):
            labels = [labels]
        for lbl in labels:
            label_to_category[lbl.lower()] = title

    # Pre-compile config autolabeler rules
    compiled_rules: List[Tuple[re.Pattern, str]] = []
    for rule in autolabeler_cfg:
        lbl = rule.get("label", "")
        title_patterns = rule.get("title", [])
        if isinstance(title_patterns, str):
            title_patterns = [title_patterns]
        for pat in title_patterns:
            compiled = parse_regex(pat)
            if compiled:
                compiled_rules.append((compiled, lbl.lower()))

    categorized: Dict[str, List[Dict[str, str]]] = {title: [] for title in category_order}
    uncategorized: List[Dict[str, str]] = []

    for commit in commits:
        subject = commit["subject"]
        matched_category: Optional[str] = None

        # 1. Match against configured autolabeler patterns
        for pattern, label in compiled_rules:
            if pattern.search(subject):
                if label in label_to_category:
                    matched_category = label_to_category[label]
                    break

        # 2. Fallback: match against standard conventional commit prefixes
        if not matched_category:
            for pattern, conv_label in CONVENTIONAL_PREFIX_RULES:
                if pattern.search(subject):
                    if conv_label in label_to_category:
                        matched_category = label_to_category[conv_label]
                        break

        # 3. Fuzzy match: match label appearing as word in category title
        if not matched_category:
            lower_subj = subject.lower()
            for cat_title in category_order:
                title_lower = cat_title.lower()
                if "feature" in lower_subj or "feat:" in lower_subj:
                    if "feature" in title_lower or "improvement" in title_lower:
                        matched_category = cat_title
                        break
                elif "fix:" in lower_subj or "bug:" in lower_subj:
                    if "fix" in title_lower or "bug" in title_lower:
                        matched_category = cat_title
                        break
                elif "refactor:" in lower_subj:
                    if "refactor" in title_lower or "code" in title_lower or "quality" in title_lower:
                        matched_category = cat_title
                        break
                elif "ci:" in lower_subj or "build:" in lower_subj or "chore:" in lower_subj:
                    if "ci" in title_lower or "maintenance" in title_lower or "tooling" in title_lower:
                        matched_category = cat_title
                        break

        if matched_category:
            categorized[matched_category].append(commit)
        else:
            uncategorized.append(commit)

    result = {cat: items for cat, items in categorized.items() if items}
    if uncategorized:
        result["📦 Other Changes"] = uncategorized

    return result


def build_changelog_markdown(
    categorized_commits: Dict[str, List[Dict[str, str]]],
    repo_url: Optional[str] = None,
) -> str:
    """Format categorized commits into Markdown notes."""
    lines: List[str] = []

    for category, commits in categorized_commits.items():
        lines.append(f"### {category}")
        for commit in commits:
            subj = commit["subject"]
            short_sha = commit["short_sha"]
            author = commit["author"]
            if repo_url:
                commit_link = f"[{short_sha}]({repo_url}/commit/{commit['sha']})"
            else:
                commit_link = short_sha
            lines.append(f"* {subj} ({commit_link}) @{author}")
        lines.append("")

    return "\n".join(lines).strip()


def update_release_notes(
    repo: str, release_id: str, new_body: str, token: str
) -> bool:
    """Update GitHub release body using GitHub REST API."""
    url = f"https://api.github.com/repos/{repo}/releases/{release_id}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "juftin-release-drafter",
    }
    payload = json.dumps({"body": new_body}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers, method="PATCH")

    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print(f"Error updating release notes: HTTP {e.code} - {e.read().decode('utf-8')}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error updating release notes: {e}", file=sys.stderr)
        return False


def get_release_body(repo: str, release_id: str, token: str) -> Optional[str]:
    """Retrieve current body of the release from GitHub REST API."""
    url = f"https://api.github.com/repos/{repo}/releases/{release_id}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "juftin-release-drafter",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("body", "")
    except Exception as e:
        print(f"Error fetching release: {e}", file=sys.stderr)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Populate release notes from direct commits.")
    parser.add_argument("--config", required=True, help="Path to Release Drafter YAML config file.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="owner/repo")
    parser.add_argument("--release-id", default="", help="GitHub Release ID to update.")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""), help="GitHub Token.")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without modifying release.")
    parser.add_argument("--force", action="store_true", help="Force commit changelog generation even if PRs exist.")

    args = parser.parse_args()

    if not os.path.isfile(args.config):
        print(f"Error: Config file not found at {args.config}", file=sys.stderr)
        sys.exit(1)

    with open(args.config, "r", encoding="utf-8") as f:
        config_content = f.read()

    config = parse_yaml_fallback(config_content)
    if not config:
        print("Error: Failed to parse configuration file.", file=sys.stderr)
        sys.exit(1)

    prev_tag, commit_range = get_git_commit_range()
    print(f"Inspecting commits in range: {commit_range} (previous tag: {prev_tag or 'None'})")

    commits = get_commits(commit_range)
    if not commits:
        print("No commits found in range.")
        return

    print(f"Found {len(commits)} commits to evaluate.")

    categorized = categorize_commits(commits, config)
    if not categorized:
        print("No commits matched configured categories.")
        return

    repo_url = f"https://github.com/{args.repo}" if args.repo else None
    changelog_md = build_changelog_markdown(categorized, repo_url=repo_url)

    print("\n--- Generated Commit Changelog ---")
    print(changelog_md)
    print("----------------------------------\n")

    if args.dry_run:
        print("Dry run active: not updating release.")
        return

    if not (args.repo and args.release_id and args.token):
        print("Release ID, repository, or token omitted; generated changelog output above.")
        return

    current_body = get_release_body(args.repo, args.release_id, args.token)
    if current_body is None:
        print("Could not retrieve existing release body.", file=sys.stderr)
        sys.exit(1)

    if "* No changes" in current_body:
        updated_body = current_body.replace("* No changes", changelog_md)
    elif args.force:
        updated_body = f"## What's Changed\n\n{changelog_md}\n\n{current_body}"
    else:
        print("Release body already contains changes and --force is not specified. Skipping.")
        return

    print(f"Updating release {args.release_id} on {args.repo}...")
    success = update_release_notes(args.repo, args.release_id, updated_body, args.token)
    if success:
        print("✅ Release notes successfully updated with direct commits!")
    else:
        print("❌ Failed to update release notes.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
