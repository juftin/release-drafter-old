#!/usr/bin/env python3
"""
parse_commits.py: Enterprise-grade Git commit parser and changelog enricher.

Seamlessly mixes direct Git commits alongside pull requests in GitHub Releases:
1. Paginates GitHub GraphQL API to identify all merged pull requests and constituent commits.
2. Extracts direct commits with strongly-typed dataclasses, co-authors, and author logins.
3. Categorizes commits using the active Release Drafter preset with conventional & gitmoji fallbacks.
4. Intelligently determines SemVer version bumps (major, minor, patch) from commit semantics.
5. Merges direct commits directly into matching Markdown categories alongside PR items.
6. Updates GitHub draft release notes, tag, and name, and exports action outputs.
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple


class VersionIncrement(str, Enum):
    NONE = "none"
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"

    def __ge__(self, other: VersionIncrement) -> bool:
        order = [VersionIncrement.NONE, VersionIncrement.PATCH, VersionIncrement.MINOR, VersionIncrement.MAJOR]
        return order.index(self) >= order.index(other)

    def __gt__(self, other: VersionIncrement) -> bool:
        order = [VersionIncrement.NONE, VersionIncrement.PATCH, VersionIncrement.MINOR, VersionIncrement.MAJOR]
        return order.index(self) > order.index(other)


@dataclass(frozen=True)
class SemVer:
    major: int = 0
    minor: int = 0
    patch: int = 0
    prefix: str = "v"

    @classmethod
    def parse(cls, tag_or_version: Optional[str]) -> SemVer:
        if not tag_or_version:
            return cls(0, 0, 0, "v")
        m = re.match(r"^(v?)(\d+)\.(\d+)\.(\d+)", tag_or_version.strip())
        if m:
            prefix = m.group(1) or "v"
            return cls(int(m.group(2)), int(m.group(3)), int(m.group(4)), prefix)
        return cls(0, 0, 0, "v")

    def bump(self, increment: VersionIncrement) -> SemVer:
        if increment == VersionIncrement.MAJOR:
            return SemVer(self.major + 1, 0, 0, self.prefix)
        if increment == VersionIncrement.MINOR:
            return SemVer(self.major, self.minor + 1, 0, self.prefix)
        if increment == VersionIncrement.PATCH:
            return SemVer(self.major, self.minor, self.patch + 1, self.prefix)
        return self

    def to_version_string(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def to_tag_string(self) -> str:
        return f"{self.prefix}{self.major}.{self.minor}.{self.patch}"

    def __lt__(self, other: SemVer) -> bool:
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)


@dataclass
class Commit:
    sha: str
    short_sha: str
    author_name: str
    author_email: str
    subject: str
    body: str = ""
    author_login: Optional[str] = None
    co_authors: List[str] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)

    @property
    def author_handle(self) -> str:
        """Formatted GitHub author string, e.g. '@octocat' or '@Justin Flammia'."""
        login = self.author_login or self.author_name
        base = f"@{login}" if not login.startswith("@") else login
        if not self.co_authors:
            return base
        co_str = ", ".join(f"@{c}" if not c.startswith("@") else c for c in self.co_authors)
        return f"{base} (with {co_str})"


# Conventional Commits fallback pattern & label mapping
CONVENTIONAL_PREFIX_RULES: List[Tuple[re.Pattern, str]] = [
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


def set_action_output(name: str, value: str) -> None:
    """Set GitHub Action output in $GITHUB_OUTPUT if available."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path and os.path.exists(output_path):
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def parse_yaml_fallback(content: str) -> Dict[str, Any]:
    """Pure Python YAML parser for Release Drafter configs."""
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

    # Specific trackers for version-resolver
    in_vr = False
    current_vr_key: Optional[str] = None
    in_vr_labels = False

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())

        # Top-level sections
        if indent == 0:
            in_vr = False
            current_section = None
            current_list = None
            current_item = None
            current_key = None

            if stripped == "version-resolver:":
                in_vr = True
                result["version-resolver"] = {
                    "major": {"labels": []},
                    "minor": {"labels": []},
                    "patch": {"labels": []},
                }
                continue
            elif stripped.endswith(":"):
                current_section = stripped[:-1].strip()
                current_list = []
                result[current_section] = current_list
                continue
            elif ":" in stripped:
                k, v = stripped.split(":", 1)
                result[k.strip()] = v.strip().strip("'\"")
                continue

        if in_vr:
            if indent == 2:
                if ":" in stripped:
                    k, v = stripped.split(":", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    current_vr_key = k
                    if v:
                        result["version-resolver"][k] = v
                    in_vr_labels = False
            elif indent == 4 and current_vr_key:
                if stripped == "labels:":
                    in_vr_labels = True
            elif indent == 6 and current_vr_key and in_vr_labels:
                if stripped.startswith("- "):
                    val = stripped[2:].strip().strip("'\"")
                    if current_vr_key in result["version-resolver"] and isinstance(
                        result["version-resolver"][current_vr_key], dict
                    ):
                        result["version-resolver"][current_vr_key]["labels"].append(val)
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
        elif indent == 0 and ":" in stripped:
            k, v = stripped.split(":", 1)
            result[k.strip()] = v.strip().strip("'\"")

    return result


def ensure_full_git_history() -> None:
    """Ensure git repository is not shallow so all commits and tags are available."""
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
    """Retrieve previous tag and commit range for analysis."""
    ensure_full_git_history()
    try:
        cmd = ["git", "describe", "--tags", "--abbrev=0"]
        prev_tag = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
        commit_range = f"{prev_tag}..HEAD"
        return prev_tag, commit_range
    except subprocess.CalledProcessError:
        return None, "HEAD"


def get_merged_prs_via_graphql(repo: str, token: str, max_pr_count: int = 500) -> Tuple[Set[str], Set[str]]:
    """Paginate GitHub GraphQL API to fetch merged PR numbers and all constituent commit SHAs."""
    if not (repo and token and "/" in repo):
        return set(), set()

    owner, repo_name = repo.split("/", 1)
    pr_numbers: Set[str] = set()
    pr_commit_shas: Set[str] = set()

    cursor: Optional[str] = None
    has_next_page = True

    while has_next_page and len(pr_numbers) < max_pr_count:
        query = """query FetchMergedPRs($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(states: MERGED, first: 100, after: $cursor) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        number
        mergeCommit { oid }
        commits(first: 50) {
          nodes {
            commit { oid }
          }
        }
      }
    }
  }
}"""
        payload = json.dumps({"query": query, "variables": {"owner": owner, "repo": repo_name, "cursor": cursor}}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=payload,
            headers={
                "Authorization": f"bearer {token}",
                "User-Agent": "juftin-release-drafter",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                pr_data = data.get("data", {}).get("repository", {}).get("pullRequests", {})
                nodes = pr_data.get("nodes", [])
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

                page_info = pr_data.get("pageInfo", {})
                has_next_page = page_info.get("hasNextPage", False)
                cursor = page_info.get("endCursor")
                if not cursor or not nodes:
                    break
        except Exception as e:
            print(f"Notice: GraphQL PR query notice: {e}")
            break

    return pr_numbers, pr_commit_shas


def extract_github_login(email: str, author_name: str) -> Optional[str]:
    """Extract GitHub username from email or sanitized author name."""
    noreply_match = re.match(r"^(?:\d+\+)?([a-zA-Z0-9-]+)@users\.noreply\.github\.com$", email)
    if noreply_match:
        return noreply_match.group(1)
    if " " not in author_name and re.match(r"^[a-zA-Z0-9-]+$", author_name):
        return author_name
    return None


def extract_co_authors(body: str) -> List[str]:
    """Parse Co-authored-by trailers from commit message body."""
    co_authors: List[str] = []
    for line in body.splitlines():
        match = re.match(r"^[Cc]o-[Aa]uthored-[Bb]y:\s*(.*?)\s*<([^>]+)>", line.strip())
        if match:
            name, email = match.group(1), match.group(2)
            login = extract_github_login(email, name)
            co_authors.append(login or name)
    return co_authors


def get_commits(
    commit_range: str,
    existing_body: str = "",
    pr_numbers: Optional[Set[str]] = None,
    pr_commit_shas: Optional[Set[str]] = None,
) -> List[Commit]:
    """Retrieve direct commits in the given range, filtering out PRs and merge commits."""
    if pr_numbers is None:
        pr_numbers = set()
    if pr_commit_shas is None:
        pr_commit_shas = set()

    body_pr_matches = re.findall(r"(?:#|/pull/)(\d+)", existing_body)
    all_pr_numbers = pr_numbers.union(body_pr_matches)

    fmt = "%H%x1f%h%x1f%an%x1f%ae%x1f%s%x1f%b%x1e"
    cmd = ["git", "log", "--no-merges", f"--format={fmt}"]
    if ".." in commit_range:
        cmd.append(commit_range)
    else:
        cmd.extend(["-n", "100", "HEAD"])

    try:
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
    except subprocess.CalledProcessError:
        return []

    commits: List[Commit] = []
    for record in output.split("\x1e"):
        if not record.strip():
            continue
        parts = record.strip().split("\x1f")
        if len(parts) >= 5:
            sha, short_sha, author_name, author_email, subject = parts[0], parts[1], parts[2], parts[3], parts[4]
            body = parts[5] if len(parts) > 5 else ""

            # 1. Skip automated merge commits
            if subject.startswith("Merge branch ") or subject.startswith("Merge pull request "):
                continue

            # 2. Skip commits already listed in the draft release body
            if short_sha in existing_body or sha in existing_body:
                continue

            # 3. Skip commits belonging to a merged pull request
            if sha.lower() in pr_commit_shas or short_sha.lower() in pr_commit_shas:
                continue

            # 4. Skip squash commits matching PR number, e.g. "feat: foo (#12)"
            pr_match = re.search(r"\(#(\d+)\)", subject)
            if pr_match and pr_match.group(1) in all_pr_numbers:
                continue

            login = extract_github_login(author_email, author_name)
            co_authors = extract_co_authors(body)

            commits.append(
                Commit(
                    sha=sha,
                    short_sha=short_sha,
                    author_name=author_name,
                    author_email=author_email,
                    subject=subject,
                    body=body,
                    author_login=login,
                    co_authors=co_authors,
                )
            )
    return commits


def parse_regex(pattern_str: str) -> Optional[re.Pattern]:
    """Parse JS-style regex string into Python Pattern."""
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
    commits: List[Commit], config: Dict[str, Any]
) -> Tuple[Dict[str, List[Commit]], List[str]]:
    """Categorize direct commits and assign matched labels."""
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
        if not labels and isinstance(cat.get("when"), dict):
            labels = cat["when"].get("labels", [])
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

    categorized: Dict[str, List[Commit]] = {title: [] for title in category_order}
    uncategorized: List[Commit] = []

    for commit in commits:
        subject = commit.subject
        matched_category: Optional[str] = None
        matched_label: Optional[str] = None

        # 1. Match against autolabeler rules
        for pattern, label in compiled_rules:
            if pattern.search(subject):
                matched_label = label
                if label in label_to_category:
                    matched_category = label_to_category[label]
                    break

        # 2. Match against conventional commit prefixes
        if not matched_category:
            for pattern, conv_label in CONVENTIONAL_PREFIX_RULES:
                if pattern.search(subject):
                    matched_label = conv_label
                    if conv_label in label_to_category:
                        matched_category = label_to_category[conv_label]
                        break

        # 3. Fuzzy match category titles
        if not matched_category:
            lower_subj = subject.lower()
            for cat_title in category_order:
                title_lower = cat_title.lower()
                if "feature" in lower_subj or "feat:" in lower_subj:
                    if "feature" in title_lower or "improvement" in title_lower:
                        matched_category = cat_title
                        matched_label = "feat"
                        break
                elif "fix:" in lower_subj or "bug:" in lower_subj:
                    if "fix" in title_lower or "bug" in title_lower:
                        matched_category = cat_title
                        matched_label = "fix"
                        break
                elif "refactor:" in lower_subj:
                    if "refactor" in title_lower or "code" in title_lower or "quality" in title_lower:
                        matched_category = cat_title
                        matched_label = "refactor"
                        break
                elif "ci:" in lower_subj or "build:" in lower_subj or "chore:" in lower_subj:
                    if "ci" in title_lower or "maintenance" in title_lower or "tooling" in title_lower:
                        matched_category = cat_title
                        matched_label = "ci"
                        break

        if matched_label:
            commit.labels.append(matched_label)

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


def resolve_commit_semver_increment(
    commits: List[Commit], config: Dict[str, Any]
) -> VersionIncrement:
    """Determine highest required SemVer increment based on commits and version-resolver config."""
    vr = config.get("version-resolver", {})
    major_labels = set(lbl.lower() for lbl in vr.get("major", {}).get("labels", ["boom", "breaking", "breaking-change"]))
    minor_labels = set(lbl.lower() for lbl in vr.get("minor", {}).get("labels", ["sparkles", "feat", "feature"]))

    highest_increment = VersionIncrement.NONE

    for commit in commits:
        is_breaking = False
        if "BREAKING CHANGE:" in commit.subject or "BREAKING CHANGE:" in commit.body:
            is_breaking = True
        if re.search(r"^[a-z]+(\([^\)]+\))?!:", commit.subject):
            is_breaking = True
        for lbl in commit.labels:
            if lbl.lower() in major_labels:
                is_breaking = True

        if is_breaking:
            return VersionIncrement.MAJOR

        is_minor = False
        if re.search(r"^(feat|feature)(\([^\)]+\))?:", commit.subject, re.IGNORECASE):
            is_minor = True
        for lbl in commit.labels:
            if lbl.lower() in minor_labels:
                is_minor = True

        if is_minor:
            highest_increment = VersionIncrement.MINOR
        elif highest_increment == VersionIncrement.NONE:
            highest_increment = VersionIncrement.PATCH

    return highest_increment


def format_commit_item(
    commit: Commit, template: Optional[str] = None, repo_url: Optional[str] = None
) -> str:
    """Format single commit item matching Release Drafter template variables."""
    subj = commit.subject
    short_sha = commit.short_sha
    author_str = commit.author_handle
    commit_url = f"{repo_url}/commit/{commit.sha}" if repo_url else ""
    hash_link = f"[{short_sha}]({commit_url})" if commit_url else short_sha

    if template:
        res = template.replace("$TITLE", subj)
        res = res.replace("$HASH", hash_link)
        res = res.replace("$SHA", short_sha)
        res = res.replace("$AUTHOR", author_str.lstrip("@"))
        res = res.replace("$URL", commit_url)
        res = res.replace("(#$NUMBER)", "")
        res = res.replace("$NUMBER", "")
        res = res.replace("in   by", "by")
        res = re.sub(r"\s+", " ", res).strip()
        return f"* {res}" if not res.startswith("* ") and not res.startswith("- ") else res

    return f"* {subj} ({hash_link}) {author_str}"


def merge_commits_into_release_body(
    body: str,
    categorized_commits: Dict[str, List[Commit]],
    category_order: List[str],
    template: Optional[str] = None,
    repo_url: Optional[str] = None,
) -> str:
    """Seamlessly merge categorized direct commits into existing release markdown."""
    commit_strings: Dict[str, List[str]] = {}
    for cat, items in categorized_commits.items():
        if items:
            commit_strings[cat] = [format_commit_item(c, template=template, repo_url=repo_url) for c in items]

    if not commit_strings:
        return body

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

    pattern = r"(^###\s+.*$)"
    parts = re.split(pattern, body, flags=re.MULTILINE)

    if len(parts) == 1:
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
        cat_title = cat_header[3:].strip()
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

    for cat_title, items in commit_strings.items():
        if cat_title not in existing_categories:
            existing_categories[cat_title] = []
        for it in items:
            existing_categories[cat_title].append(it)

    sorted_categories: Dict[str, List[str]] = OrderedDict()
    for ord_title in category_order:
        if ord_title in existing_categories:
            sorted_categories[ord_title] = existing_categories[ord_title]
    for rem_title, rem_items in existing_categories.items():
        if rem_title not in sorted_categories:
            sorted_categories[rem_title] = rem_items

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


def get_release_info(repo: str, release_id: str, token: str) -> Optional[Dict[str, Any]]:
    """Retrieve current details of the release from GitHub REST API."""
    url = f"https://api.github.com/repos/{repo}/releases/{release_id}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "juftin-release-drafter",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Error fetching release: {e}", file=sys.stderr)
        return None


def update_release_notes(
    repo: str,
    release_id: str,
    token: str,
    new_body: str,
    new_tag: Optional[str] = None,
    new_name: Optional[str] = None,
) -> bool:
    """Update GitHub release body, tag_name, and name using GitHub REST API."""
    url = f"https://api.github.com/repos/{repo}/releases/{release_id}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "juftin-release-drafter",
    }
    payload_dict: Dict[str, Any] = {"body": new_body}
    if new_tag:
        payload_dict["tag_name"] = new_tag
    if new_name:
        payload_dict["name"] = new_name

    payload = json.dumps(payload_dict).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers, method="PATCH")

    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print(f"Error updating release: HTTP {e.code} - {e.read().decode('utf-8')}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error updating release: {e}", file=sys.stderr)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Flawlessly parse direct commits, resolve SemVer, and enrich release notes.")
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

    # 1. Fetch current release info
    current_body = ""
    current_tag = ""
    current_name = ""
    if args.repo and args.release_id and args.token:
        rel_info = get_release_info(args.repo, args.release_id, args.token)
        if rel_info:
            current_body = rel_info.get("body", "")
            current_tag = rel_info.get("tag_name", "")
            current_name = rel_info.get("name", "")

    # 2. Query merged PRs and commit SHAs via GraphQL
    pr_numbers, pr_commit_shas = set(), set()
    if args.repo and args.token:
        pr_numbers, pr_commit_shas = get_merged_prs_via_graphql(args.repo, args.token)

    # 3. Determine commit range
    prev_tag, commit_range = get_git_commit_range()
    print(f"Inspecting commits in range: {commit_range} (previous tag: {prev_tag or 'None'})")

    # 4. Retrieve direct commits
    commits = get_commits(
        commit_range,
        existing_body=current_body,
        pr_numbers=pr_numbers,
        pr_commit_shas=pr_commit_shas,
    )

    if not commits and not args.force:
        print("All direct commits are already recorded or belong to merged pull requests. No update needed.")
        set_action_output("direct_commit_count", "0")
        set_action_output("has_direct_commits", "false")
        return

    print(f"Found {len(commits)} direct commits to merge.")
    set_action_output("direct_commit_count", str(len(commits)))
    set_action_output("has_direct_commits", "true")

    # 5. Categorize commits
    categorized, category_order = categorize_commits(commits, config)

    # 6. SemVer Resolution
    commit_increment = resolve_commit_semver_increment(commits, config)
    base_semver = SemVer.parse(prev_tag) if prev_tag else SemVer(0, 0, 0, "v")
    calculated_semver = base_semver.bump(commit_increment)
    current_draft_semver = SemVer.parse(current_tag)

    target_semver = max(calculated_semver, current_draft_semver) if current_draft_semver.major or current_draft_semver.minor or current_draft_semver.patch else calculated_semver

    # Determine if tag / name needs updating
    new_tag: Optional[str] = None
    new_name: Optional[str] = None
    if target_semver > current_draft_semver or current_tag.startswith("untagged-"):
        new_tag = target_semver.to_tag_string()
        new_name = target_semver.to_tag_string()
        print(f"SemVer bump detected: upgrading release tag from '{current_tag}' to '{new_tag}'")

    # Export outputs to GITHUB_OUTPUT
    set_action_output("resolved_version", target_semver.to_version_string())
    set_action_output("major_version", str(target_semver.major))
    set_action_output("minor_version", str(target_semver.minor))
    set_action_output("patch_version", str(target_semver.patch))
    if new_tag:
        set_action_output("tag_name", new_tag)
        set_action_output("name", new_name or new_tag)

    # 7. Merge direct commits into release body
    repo_url = f"https://github.com/{args.repo}" if args.repo else None
    template_str = config.get("change-template")

    if current_body:
        updated_body = merge_commits_into_release_body(
            current_body,
            categorized,
            category_order,
            template=template_str,
            repo_url=repo_url,
        )
    else:
        out = []
        for cat in category_order:
            if cat in categorized:
                out.append(f"### {cat}")
                for c in categorized[cat]:
                    out.append(format_commit_item(c, template=template_str, repo_url=repo_url))
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

    # Check if update is required
    body_changed = (updated_body != current_body)
    tag_changed = (new_tag is not None and new_tag != current_tag)

    if not body_changed and not tag_changed:
        print("Release notes and version are already identical. No update necessary.")
        return

    print(f"Updating release {args.release_id} on {args.repo}...")
    success = update_release_notes(
        args.repo,
        args.release_id,
        args.token,
        updated_body,
        new_tag=new_tag,
        new_name=new_name,
    )
    if success:
        print("✅ Release notes and version successfully updated with direct commits mixed alongside PRs!")
    else:
        print("❌ Failed to update release notes.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
