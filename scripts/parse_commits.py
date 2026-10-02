#!/usr/bin/env python3
"""
parse_commits.py: Parse direct Git commits and seamlessly mix them with PR release notes.

When pull requests and/or direct Git commits exist, this script:
1. Detects all merged pull requests and their associated commits to prevent duplication.
2. Analyzes direct commits between the previous release tag and HEAD.
3. Categorizes direct commits according to the active Release Drafter preset
   (Conventional Commits, Gitmoji, or Hybrid).
4. Seamlessly merges direct commits into existing release categories alongside PR items,
   or replaces '* No changes' when direct commits are the only changes.
5. Updates the GitHub draft release notes via the GitHub REST API.
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple


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


def get_merged_prs_via_graphql(repo: str, token: str) -> Tuple[Set[str], Set[str]]:
    """Query GitHub GraphQL API to fetch merged PR numbers and all associated commit SHAs."""
    if not (repo and token and "/" in repo):
        return set(), set()

    owner, repo_name = repo.split("/", 1)
    query = """query FetchMergedPRs($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) {
    pullRequests(states: MERGED, last: 100) {
      nodes {
        number
        mergeCommit { oid }
        commits(first: 100) {
          nodes {
            commit { oid }
          }
        }
      }
    }
  }
}"""
    payload = json.dumps({"query": query, "variables": {"owner": owner, "repo": repo_name}}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "User-Agent": "juftin-release-drafter",
            "Content-Type": "application/json",
        },
    )

    pr_numbers: Set[str] = set()
    pr_commit_shas: Set[str] = set()

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            nodes = data.get("data", {}).get("repository", {}).get("pullRequests", {}).get("nodes", [])
            for node in nodes:
                pr_num = str(node.get("number", ""))
                if pr_num:
                    pr_numbers.add(pr_num)
                merge_oid = node.get("mergeCommit", {}).get("oid")
                if merge_oid:
                    pr_commit_shas.add(merge_oid.lower())
                commits = node.get("commits", {}).get("nodes", [])
                for c in commits:
                    oid = c.get("commit", {}).get("oid")
                    if oid:
                        pr_commit_shas.add(oid.lower())
    except Exception as e:
        print(f"Notice: GraphQL PR query skipped: {e}")

    return pr_numbers, pr_commit_shas


def get_commits(
    commit_range: str,
    existing_body: str = "",
    pr_numbers: Optional[Set[str]] = None,
    pr_commit_shas: Optional[Set[str]] = None,
) -> List[Dict[str, str]]:
    """Retrieve direct commits in the given range, filtering out PR and merge commits."""
    if pr_numbers is None:
        pr_numbers = set()
    if pr_commit_shas is None:
        pr_commit_shas = set()

    # Extract any PR numbers already mentioned in the release body (#123 or /pull/123)
    body_pr_matches = re.findall(r"(?:#|/pull/)(\d+)", existing_body)
    all_pr_numbers = pr_numbers.union(body_pr_matches)

    fmt = "%H%x1f%h%x1f%an%x1f%s"
    cmd = ["git", "log", "--no-merges", f"--format={fmt}"]
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

            # 1. Skip standard merge commits
            if subject.startswith("Merge branch ") or subject.startswith("Merge pull request "):
                continue

            # 2. Skip commits already listed in the draft release body
            if short_sha in existing_body or sha in existing_body:
                continue

            # 3. Skip commits associated with a merged PR by SHA
            if sha.lower() in pr_commit_shas or short_sha.lower() in pr_commit_shas:
                continue

            # 4. Skip squash commits matching PR number, e.g. "feat: foo (#12)"
            pr_match = re.search(r"\(#(\d+)\)", subject)
            if pr_match and pr_match.group(1) in all_pr_numbers:
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
) -> Tuple[Dict[str, List[Dict[str, str]]], List[str]]:
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

        # 3. Fuzzy match: match keywords appearing in category title
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
        if "📦 Other Changes" not in category_order:
            category_order.append("📦 Other Changes")

    return result, category_order


def format_commit_item(commit: Dict[str, str], repo_url: Optional[str] = None) -> str:
    """Format a single commit item matching Release Drafter style."""
    subj = commit["subject"]
    short_sha = commit["short_sha"]
    author = commit["author"]
    if repo_url:
        commit_link = f"[{short_sha}]({repo_url}/commit/{commit['sha']})"
    else:
        commit_link = short_sha
    return f"* {subj} ({commit_link}) @{author}"


def merge_commits_into_release_body(
    body: str,
    categorized_commits: Dict[str, List[Dict[str, str]]],
    category_order: List[str],
    repo_url: Optional[str] = None,
) -> str:
    """Seamlessly merge categorized direct commits into the existing release notes."""
    # Convert categorized commits to formatted markdown bullet strings
    commit_strings: Dict[str, List[str]] = {}
    for cat, items in categorized_commits.items():
        if items:
            commit_strings[cat] = [format_commit_item(c, repo_url=repo_url) for c in items]

    if not commit_strings:
        return body

    # Case 1: * No changes present -> replace with all categorized commits
    if "* No changes" in body:
        out: List[str] = []
        for cat in category_order:
            items = commit_strings.get(cat, [])
            if items:
                out.append(f"### {cat}")
                out.extend(items)
                out.append("")
        for cat, items in commit_strings.items():
            if cat not in category_order and items:
                out.append(f"### {cat}")
                out.extend(items)
                out.append("")
        return body.replace("* No changes", "\n".join(out).strip())

    # Case 2: Parse existing Markdown categories and merge direct commits alongside PRs
    pattern = r"(^###\s+.*$)"
    parts = re.split(pattern, body, flags=re.MULTILINE)

    if len(parts) == 1:
        # No category subheadings exist in the body.
        out = []
        for cat in category_order:
            items = commit_strings.get(cat, [])
            if items:
                out.append(f"### {cat}")
                out.extend(items)
                out.append("")
        addition = "\n".join(out).strip()
        if "**Full Changelog**" in body:
            pre, post = body.split("**Full Changelog**", 1)
            return f"{pre.rstrip()}\n\n{addition}\n\n**Full Changelog**{post}"
        return f"{body.rstrip()}\n\n{addition}\n"

    header_prefix = parts[0]
    existing_categories: Dict[str, List[str]] = OrderedDict()
    footer_lines: List[str] = []

    idx = 1
    while idx < len(parts):
        cat_header = parts[idx].strip()
        cat_title = cat_header[3:].strip()  # remove '###'
        content = parts[idx + 1] if idx + 1 < len(parts) else ""

        lines = content.strip().splitlines()
        item_lines: List[str] = []
        in_items = True

        for line in lines:
            if in_items and (line.startswith("* ") or line.startswith("- ") or line.startswith("  ")):
                item_lines.append(line)
            elif in_items and not line.strip():
                continue
            else:
                in_items = False
                footer_lines.append(line)

        existing_categories[cat_title] = item_lines
        idx += 2

    # Append direct commits to existing categories or register new category
    for cat_title, items in commit_strings.items():
        if cat_title not in existing_categories:
            existing_categories[cat_title] = []
        for it in items:
            existing_categories[cat_title].append(it)

    # Order categories according to config category_order
    sorted_categories: Dict[str, List[str]] = OrderedDict()
    for ord_title in category_order:
        if ord_title in existing_categories:
            sorted_categories[ord_title] = existing_categories[ord_title]
    for rem_title, rem_items in existing_categories.items():
        if rem_title not in sorted_categories:
            sorted_categories[rem_title] = rem_items

    # Reconstruct release body
    out = []
    out.append(header_prefix.rstrip())
    out.append("")
    for c_title, c_items in sorted_categories.items():
        if c_items:
            out.append(f"### {c_title}")
            out.extend(c_items)
            out.append("")
    if footer_lines:
        out.append("\n".join(footer_lines).strip())
        out.append("")

    return "\n".join(out).strip()


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
    parser = argparse.ArgumentParser(description="Populate release notes from direct commits alongside PRs.")
    parser.add_argument("--config", required=True, help="Path to Release Drafter YAML config file.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="owner/repo")
    parser.add_argument("--release-id", default="", help="GitHub Release ID to update.")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""), help="GitHub Token.")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without modifying release.")
    parser.add_argument("--force", action="store_true", help="Force commit changelog generation even if already present.")

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

    # Fetch current release body from GitHub
    current_body = ""
    if args.repo and args.release_id and args.token:
        fetched_body = get_release_body(args.repo, args.release_id, args.token)
        if fetched_body is not None:
            current_body = fetched_body

    # Query merged PRs and commit SHAs to prevent duplicates
    pr_numbers, pr_commit_shas = set(), set()
    if args.repo and args.token:
        pr_numbers, pr_commit_shas = get_merged_prs_via_graphql(args.repo, args.token)

    # Determine commit range
    prev_tag, commit_range = get_git_commit_range()
    print(f"Inspecting commits in range: {commit_range} (previous tag: {prev_tag or 'None'})")

    # Retrieve direct commits, excluding PR commits and already-recorded commits
    commits = get_commits(
        commit_range,
        existing_body=current_body,
        pr_numbers=pr_numbers,
        pr_commit_shas=pr_commit_shas,
    )

    if not commits and not args.force:
        print("All direct commits are already recorded or belong to merged pull requests. No update needed.")
        return

    print(f"Found {len(commits)} direct commits to merge.")

    # Categorize direct commits
    categorized, category_order = categorize_commits(commits, config)
    if not categorized and not args.force:
        print("No direct commits matched configured categories.")
        return

    repo_url = f"https://github.com/{args.repo}" if args.repo else None

    # Merge direct commits into the release body
    if current_body:
        updated_body = merge_commits_into_release_body(
            current_body,
            categorized,
            category_order,
            repo_url=repo_url,
        )
    else:
        # Standalone changelog
        out = []
        for cat in category_order:
            if cat in categorized:
                out.append(f"### {cat}")
                for c in categorized[cat]:
                    out.append(format_commit_item(c, repo_url=repo_url))
                out.append("")
        updated_body = f"## What's Changed\n\n" + "\n".join(out).strip()

    print("\n--- Merged Release Notes Preview ---")
    print(updated_body)
    print("------------------------------------\n")

    if args.dry_run:
        print("Dry run active: not modifying release.")
        return

    if not (args.repo and args.release_id and args.token):
        print("Release ID, repository, or token omitted; generated preview output above.")
        return

    if updated_body == current_body:
        print("Release body is already identical. No update necessary.")
        return

    print(f"Updating release {args.release_id} on {args.repo}...")
    success = update_release_notes(args.repo, args.release_id, updated_body, args.token)
    if success:
        print("✅ Release notes successfully updated with direct commits mixed alongside PRs!")
    else:
        print("❌ Failed to update release notes.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
