#!/usr/bin/env python3
"""
Generate Release Drafter Gitmoji and Hybrid configurations
from the official gitmojis.json database.
"""

import json
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
DATA_FILE = ROOT_DIR / "data" / "gitmojis.json"

CATEGORIES = [
    {
        "title": "💥 Breaking Changes",
        "semver": "major",
        "names": ["boom"],
        "extra_labels": ["breaking", "breaking-change"],
    },
    {
        "title": "✨ Features & Improvements",
        "semver": "minor",
        "names": [
            "sparkles",
            "tada",
            "rocket",
            "children-crossing",
            "iphone",
            "dizzy",
            "globe-with-meridians",
            "bento",
            "wheelchair",
            "egg",
            "alembic",
            "triangular-flag-on-post",
            "necktie",
            "t-rex",
            "airplane",
        ],
        "extra_labels": ["feature", "enhancement", "feat"],
        "branch_patterns": ["/^feat(\\//|-)/i", "/^feature(\\//|-)/i"],
    },
    {
        "title": "🐛 Bug Fixes & Security",
        "semver": "patch",
        "names": [
            "bug",
            "ambulance",
            "adhesive-bandage",
            "lock",
            "closed-lock-with-key",
            "passport-control",
            "safety-vest",
            "goal-net",
            "rotating-light",
            "pencil2",
            "alien",
        ],
        "extra_labels": ["fix", "bugfix", "security"],
        "branch_patterns": ["/^fix(\\//|-)/i", "/^bugfix(\\//|-)/i", "/^hotfix(\\//|-)/i", "/^sec(urity)?(\\//|-)/i"],
    },
    {
        "title": "⚡ Performance",
        "semver": "patch",
        "names": ["zap", "thread", "mag"],
        "extra_labels": ["perf", "performance"],
        "branch_patterns": ["/^perf(\\//|-)/i"],
    },
    {
        "title": "📝 Documentation",
        "semver": "patch",
        "names": ["memo", "bulb", "page-facing-up", "busts-in-silhouette", "speech-balloon"],
        "extra_labels": ["docs", "documentation"],
        "branch_patterns": ["/^docs?(\\//|-)/i"],
    },
    {
        "title": "♻️ Code Refactoring & Style",
        "semver": "patch",
        "names": [
            "recycle",
            "art",
            "fire",
            "coffin",
            "building-construction",
            "wastebasket",
            "truck",
            "label",
            "card-file-box",
            "lipstick",
            "poop",
        ],
        "extra_labels": ["refactor", "style"],
        "branch_patterns": ["/^refactor(\\//|-)/i", "/^style(\\//|-)/i"],
    },
    {
        "title": "📦 Dependency Updates",
        "semver": "patch",
        "names": [
            "arrow-up",
            "arrow-down",
            "pushpin",
            "heavy-plus-sign",
            "heavy-minus-sign",
            "package",
        ],
        "extra_labels": ["dependencies", "deps"],
        "branch_patterns": ["/^dependabot\\//i", "/^renovate\\//i", "/^deps?(\\//|-)/i"],
    },
    {
        "title": "👷 CI, Build & Tooling",
        "semver": "patch",
        "names": [
            "construction-worker",
            "green-heart",
            "wrench",
            "hammer",
            "bricks",
            "technologist",
            "see-no-evil",
            "chart-with-upwards-trend",
            "stethoscope",
            "money-with-wings",
            "bookmark",
            "construction",
            "loud-sound",
            "mute",
        ],
        "extra_labels": ["ci", "build", "chore"],
        "branch_patterns": ["/^ci(\\//|-)/i", "/^build(\\//|-)/i", "/^chore(\\//|-)/i"],
    },
    {
        "title": "🧪 Tests",
        "semver": "patch",
        "names": [
            "white-check-mark",
            "test-tube",
            "camera-flash",
            "clown-face",
            "seedling",
            "monocle-face",
        ],
        "extra_labels": ["test"],
        "branch_patterns": ["/^tests?(\\//|-)/i"],
    },
    {
        "title": "⏪️ Reverts & Branches",
        "semver": "patch",
        "names": ["rewind", "twisted-rightwards-arrows", "beers"],
        "extra_labels": ["revert"],
        "branch_patterns": ["/^revert(\\//|-)/i"],
    },
]


def load_gitmojis():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {g["name"]: g for g in data["gitmojis"]}


def make_emoji_regex(emoji: str, code: str) -> str:
    # Escape variation selector optionality
    raw_emoji = emoji.replace("\ufe0f", "")
    escaped_code = code.replace(":", r"\:")
    if raw_emoji != emoji:
        # has variation selector in canonical form
        pattern = f"^({code}|{raw_emoji}\ufe0f?)"
    else:
        pattern = f"^({code}|{raw_emoji})"
    return f"/{pattern}/"


def generate_gitmoji_yaml(gitmojis):
    lines = [
        "name-template: 'v$RESOLVED_VERSION'",
        "tag-template: 'v$RESOLVED_VERSION'",
        "change-template: '- $TITLE (#$NUMBER) @$AUTHOR'",
        "",
        "template: |",
        "  ## What's Changed",
        "",
        "  $CHANGES",
        "",
        "  **Full Changelog**: $PREVIOUS_TAG...$NEXT_TAG",
        "",
        "categories:",
    ]

    for cat in CATEGORIES:
        lines.append(f"  - title: '{cat['title']}'")
        lines.append("    labels:")
        all_labels = cat["names"] + cat.get("extra_labels", [])
        for label in all_labels:
            lines.append(f"      - '{label}'")

    lines.append("")
    lines.append("version-resolver:")

    # Major
    major_labels = []
    minor_labels = []
    patch_labels = []

    for cat in CATEGORIES:
        labels = cat["names"] + cat.get("extra_labels", [])
        if cat["semver"] == "major":
            major_labels.extend(labels)
        elif cat["semver"] == "minor":
            minor_labels.extend(labels)
        else:
            patch_labels.extend(labels)

    lines.append("  major:")
    lines.append("    labels:")
    for l in major_labels:
        lines.append(f"      - '{l}'")

    lines.append("  minor:")
    lines.append("    labels:")
    for l in minor_labels:
        lines.append(f"      - '{l}'")

    lines.append("  patch:")
    lines.append("    labels:")
    for l in patch_labels:
        lines.append(f"      - '{l}'")

    lines.append("  default: patch")
    lines.append("")
    lines.append("autolabeler:")

    # Special breaking rule
    lines.append("  - label: 'boom'")
    lines.append("    title:")
    lines.append(f"      - '{make_emoji_regex(gitmojis['boom']['emoji'], gitmojis['boom']['code'])}'")
    lines.append("      - '/BREAKING CHANGE/i'")
    lines.append("    branch:")
    lines.append("      - '/.*breaking.*/i'")
    lines.append("")

    for cat in CATEGORIES:
        for name in cat["names"]:
            if name == "boom":
                continue  # already added above
            g = gitmojis[name]
            lines.append(f"  - label: '{name}'")
            lines.append("    title:")
            lines.append(f"      - '{make_emoji_regex(g['emoji'], g['code'])}'")
            if "branch_patterns" in cat and name == cat["names"][0]:
                lines.append("    branch:")
                for bp in cat["branch_patterns"]:
                    lines.append(f"      - '{bp}'")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def generate_hybrid_yaml(gitmojis):
    lines = [
        "name-template: 'v$RESOLVED_VERSION'",
        "tag-template: 'v$RESOLVED_VERSION'",
        "change-template: '- $TITLE (#$NUMBER) @$AUTHOR'",
        "",
        "template: |",
        "  ## What's Changed",
        "",
        "  $CHANGES",
        "",
        "  **Full Changelog**: $PREVIOUS_TAG...$NEXT_TAG",
        "",
        "categories:",
    ]

    for cat in CATEGORIES:
        lines.append(f"  - title: '{cat['title']}'")
        lines.append("    labels:")
        all_labels = cat["names"] + cat.get("extra_labels", [])
        for label in all_labels:
            lines.append(f"      - '{label}'")

    lines.append("")
    lines.append("version-resolver:")

    major_labels = []
    minor_labels = []
    patch_labels = []

    for cat in CATEGORIES:
        labels = cat["names"] + cat.get("extra_labels", [])
        if cat["semver"] == "major":
            major_labels.extend(labels)
        elif cat["semver"] == "minor":
            minor_labels.extend(labels)
        else:
            patch_labels.extend(labels)

    lines.append("  major:")
    lines.append("    labels:")
    for l in major_labels:
        lines.append(f"      - '{l}'")

    lines.append("  minor:")
    lines.append("    labels:")
    for l in minor_labels:
        lines.append(f"      - '{l}'")

    lines.append("  patch:")
    lines.append("    labels:")
    for l in patch_labels:
        lines.append(f"      - '{l}'")

    lines.append("  default: patch")
    lines.append("")
    lines.append("autolabeler:")

    # Breaking Changes (Conventional + Gitmoji)
    lines.append("  - label: 'breaking'")
    lines.append("    title:")
    lines.append("      - '/^(:boom:|💥)/'")
    lines.append("      - '/^([a-z]+(\\([^\\)]+\\))?!:|.*BREAKING CHANGE:?)/i'")
    lines.append("      - '/BREAKING[ -]CHANGE/i'")
    lines.append("    branch:")
    lines.append("      - '/.*breaking.*/i'")
    lines.append("")

    # Conventional commit types mappings
    conv_rules = [
        ("feat", ["/^((:sparkles:|✨|:tada:|🎉|:rocket:|🚀)\\s*)?feat(ure)?(\\([^\\)]+\\))?:/i"], ["/^feat(\\//|-)/i", "/^feature(\\//|-)/i"]),
        ("fix", ["/^((:bug:|🐛|:ambulance:|🚑️|🚑|:adhesive_bandage:|🩹)\\s*)?(fix|bugfix|hotfix)(\\([^\\)]+\\))?:/i"], ["/^fix(\\//|-)/i", "/^bugfix(\\//|-)/i", "/^hotfix(\\//|-)/i"]),
        ("security", ["/^((:lock:|🔒)\\s*)?sec(urity)?(\\([^\\)]+\\))?:/i"], ["/^sec(urity)?(\\//|-)/i"]),
        ("perf", ["/^((:zap:|⚡️|⚡)\\s*)?perf(ormance)?(\\([^\\)]+\\))?:/i"], ["/^perf(\\//|-)/i"]),
        ("docs", ["/^((:memo:|📝|:books:|📚|:bulb:|💡)\\s*)?docs?(\\([^\\)]+\\))?:/i"], ["/^docs?(\\//|-)/i"]),
        ("refactor", ["/^((:recycle:|♻️|♻|:art:|🎨|:fire:|🔥)\\s*)?refactor(\\([^\\)]+\\))?:/i"], ["/^refactor(\\//|-)/i"]),
        ("dependencies", ["/^((:arrow_up:|⬆️|⬆|:arrow_down:|⬇️|⬇|:package:|📦️|📦|:pushpin:|📌)\\s*)?(deps?|dependencies)(\\([^\\)]+\\))?:/i", "/^chore\\(deps(-[a-z0-9]+)?\\):/i"], ["/^dependabot\\//i", "/^renovate\\//i", "/^deps?(\\//|-)/i"]),
        ("ci", ["/^((:construction_worker:|👷|:green_heart:|💚)\\s*)?(ci|build)(\\([^\\)]+\\))?:/i"], ["/^ci(\\//|-)/i", "/^build(\\//|-)/i"]),
        ("chore", ["/^((:wrench:|🔧|:hammer:|🔨|:truck:|🚚|:wastebasket:|🗑️|🗑)\\s*)?chore(\\([^\\)]+\\))?:/i"], ["/^chore(\\//|-)/i"]),
        ("test", ["/^((:white_check_mark:|✅|:test_tube:|🧪)\\s*)?tests?(\\([^\\)]+\\))?:/i"], ["/^tests?(\\//|-)/i"]),
        ("revert", ["/^((:rewind:|⏪️|⏪)\\s*)?revert(\\([^\\)]+\\))?:/i"], ["/^revert(\\//|-)/i"]),
    ]

    for label, title_patterns, branch_patterns in conv_rules:
        lines.append(f"  - label: '{label}'")
        lines.append("    title:")
        for tp in title_patterns:
            lines.append(f"      - '{tp}'")
        lines.append("    branch:")
        for bp in branch_patterns:
            lines.append(f"      - '{bp}'")
        lines.append("")

    # Add individual Gitmoji autolabelers
    for cat in CATEGORIES:
        for name in cat["names"]:
            if name == "boom":
                continue
            g = gitmojis[name]
            lines.append(f"  - label: '{name}'")
            lines.append("    title:")
            lines.append(f"      - '{make_emoji_regex(g['emoji'], g['code'])}'")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def main():
    gitmojis = load_gitmojis()
    print(f"Loaded {len(gitmojis)} gitmojis from {DATA_FILE}")

    gitmoji_yaml = generate_gitmoji_yaml(gitmojis)
    hybrid_yaml = generate_hybrid_yaml(gitmojis)

    (ROOT_DIR / "configs" / "gitmoji.yml").write_text(gitmoji_yaml, encoding="utf-8")
    (ROOT_DIR / ".github" / "release-drafter-gitmoji.yml").write_text(gitmoji_yaml, encoding="utf-8")
    (ROOT_DIR / "configs" / "hybrid.yml").write_text(hybrid_yaml, encoding="utf-8")
    (ROOT_DIR / ".github" / "release-drafter.yml").write_text(hybrid_yaml, encoding="utf-8")

    print("Successfully generated:")
    print(" - configs/gitmoji.yml")
    print(" - .github/release-drafter-gitmoji.yml")
    print(" - configs/hybrid.yml")
    print(" - .github/release-drafter.yml")


if __name__ == "__main__":
    main()
