# Release Drafter Configurations & GitHub Action

[![Test and Validate](https://github.com/juftin/release-drafter/actions/workflows/test.yaml/badge.svg)](https://github.com/juftin/release-drafter/actions/workflows/test.yaml)
[![Release Drafter](https://github.com/juftin/release-drafter/actions/workflows/release.yaml/badge.svg)](https://github.com/juftin/release-drafter/actions/workflows/release.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Centralized, battle-tested [Release Drafter](https://github.com/release-drafter/release-drafter) configurations and reusable GitHub Actions. Supports **Conventional Commits**, **Gitmoji**, and a **Hybrid** preset with automatic PR autolabeling, SemVer version resolution, and formatted changelogs.

---

## ⚡ Features

- 🎯 **Pre-built Presets**: Ready-to-use configurations for **Conventional Commits**, **Gitmoji**, and **Hybrid** styles.
- 🏷️ **Intelligent Autolabeling**: Automatically tags PRs based on title regex (both unicode emojis and `:shortcode:` formats) and branch prefixes.
- 🔢 **Semantic Versioning**: Automatically resolves major, minor, and patch bumps from PR labels and commit types.
- 🚀 **Multiple Ways to Consume**:
  1. **[Reusable Workflow](#method-1-reusable-workflow-recommended)**: Call directly via `uses: juftin/release-drafter/workflow.yaml@v1`.
  2. **[Composite GitHub Action](#method-2-composite-action)**: Integrate into custom workflows using `uses: juftin/release-drafter@v1`.
  3. **[Config Inheritance (`_extends`)](#method-3-config-inheritance-_extends)**: Extend directly in `.github/release-drafter.yaml`.

---

## 📦 Available Presets

| Preset | Description | Config File |
| :--- | :--- | :--- |
| `conventional-commits` *(default)* | Follows the [Conventional Commits](https://www.conventionalcommits.org/) 1.0.0 specification (`feat`, `fix`, `docs`, `perf`, `refactor`, `breaking`, etc.). | [`configs/conventional-commits.yaml`](configs/conventional-commits.yaml) |
| `gitmoji` | Full coverage of all 75 official [carloscuesta/gitmoji](https://github.com/carloscuesta/gitmoji) types using both Unicode emojis (e.g., `✨`, `🐛`, `💥`) and shortcodes (`:sparkles:`, `:bug:`, `:boom:`). | [`configs/gitmoji.yaml`](configs/gitmoji.yaml) |
| `hybrid` | Combines Conventional Commits and all 75 Gitmojis, supporting either style or mixed formats (e.g., `✨ feat: ...`). | [`configs/hybrid.yaml`](configs/hybrid.yaml) |

---

## 🚀 Usage

### Method 1: Reusable Workflow (Recommended)

Add a workflow in your caller repository at `.github/workflows/release.yaml`:

```yaml
name: Release Drafter

on:
  push:
    branches:
      - main
  pull_request_target:
    types:
      - opened
      - reopened
      - synchronize
      - edited

jobs:
  draft-release:
    permissions:
      contents: write       # Needed to create/update releases
      pull-requests: write  # Needed for autolabeler to label PRs
    uses: juftin/release-drafter/workflow.yaml@v1
    with:
      config: conventional-commits # Options: conventional-commits, gitmoji, hybrid
```

#### Using Gitmoji Preset with the Reusable Workflow

```yaml
jobs:
  draft-release:
    permissions:
      contents: write
      pull-requests: write
    uses: juftin/release-drafter/workflow.yaml@v1
    with:
      config: gitmoji
```

---

### Method 2: Composite Action

If you want to invoke Release Drafter inside your own custom job:

```yaml
name: Release Drafter

on:
  push:
    branches:
      - main
  pull_request_target:
    types: [opened, reopened, synchronize, edited]

jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    steps:
      - name: Run Release Drafter
        uses: juftin/release-drafter@v1
        with:
          config: conventional-commits # Options: conventional-commits, gitmoji, hybrid
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

---

### Method 3: Config Inheritance (`_extends`)

If you want to use the upstream `release-drafter/release-drafter` action directly and extend one of the shared configurations:

Create `.github/release-drafter.yaml` in your repository:

```yaml
# Inherit all categories, autolabeler regex, and version resolvers
_extends: juftin/release-drafter:configs/conventional-commits.yaml

# (Optional) Override or add custom settings
tag-prefix: 'v'
```

For the Gitmoji preset:

```yaml
_extends: juftin/release-drafter:configs/gitmoji.yaml
```

Or for the default / hybrid preset:

```yaml
_extends: juftin/release-drafter
```

Then run the standard release-drafter action in your workflow:

```yaml
- uses: release-drafter/release-drafter@v7
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

---

## ⚙️ Inputs & Outputs

### Reusable Workflow & Action Inputs

| Input | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `config` | `string` | `'conventional-commits'` | Preset to use (`conventional-commits`, `gitmoji`, or `hybrid`). |
| `config-name` | `string` | `''` | Path or name of a custom config file in caller repository (overrides `config`). |
| `publish` | `boolean` | `false` | Publishes draft release immediately upon execution. |
| `prerelease` | `boolean` | `false` | Marks the release as a prerelease. |
| `prerelease-identifier` | `string` | `''` | Prerelease identifier (e.g. `alpha`, `beta`, `rc`). |
| `include-commits` | `boolean` | `true` | Whether to parse and include direct Git commits when no pull requests exist. |
| `commitish` | `string` | `''` | Target commit ref, SHA, branch, or tag for the release. |
| `version` | `string` | `''` | Explicit version override. |
| `header` | `string` | `''` | Markdown prepended before release notes body. |
| `footer` | `string` | `''` | Markdown appended after release notes body. |
| `latest` | `string` | `'true'` | Release "latest" setting: `'true'`, `'false'`, or `'legacy'`. |

### Outputs

| Output | Description |
| :--- | :--- |
| `id` | GitHub Release ID |
| `name` | Release title / name |
| `tag_name` | Resolved release tag name |
| `body` | Rendered Markdown release notes |
| `html_url` | Direct URL to GitHub Release page |
| `upload_url` | Asset upload URL for release artifacts |
| `major_version` | Resolved SemVer major version |
| `minor_version` | Resolved SemVer minor version |
| `patch_version` | Resolved SemVer patch version |
| `resolved_version` | Complete resolved version string |
| `direct_commit_count` | Number of direct commits parsed and merged |

---

## 🏷️ Commit & PR Labeling Cheat Sheet

### Conventional Commits

| Commit Type | Example PR Title | Assigned Label | Category | Version Bump |
| :--- | :--- | :--- | :--- | :--- |
| Breaking Change | `feat!: drop python 3.8 support` | `breaking` | 💥 Breaking Changes | **Major** |
| `feat` / `feature` | `feat(api): add export endpoint` | `feat` | 🚀 Features & Enhancements | **Minor** |
| `fix` / `bugfix` | `fix: resolve null pointer exception` | `fix` | 🐛 Bug Fixes | **Patch** |
| `perf` | `perf: cache database query results` | `perf` | ⚡ Performance Improvements | **Patch** |
| `docs` | `docs: update deployment instructions` | `docs` | 📚 Documentation | **Patch** |
| `refactor` | `refactor: simplify parser logic` | `refactor` | 🧰 Maintenance & Code Quality | **Patch** |
| `deps` | `chore(deps): bump actions/checkout` | `dependencies` | 📦 Dependency Updates | **Patch** |
| `test` | `test: add unit tests for auth service` | `test` | 🧰 Maintenance & Code Quality | **Patch** |
| `ci` / `build` | `ci: optimize matrix build cache` | `ci` | 🧰 Maintenance & Code Quality | **Patch** |
| `chore` | `chore: clean up repo config` | `chore` | 🧰 Maintenance & Code Quality | **Patch** |
| `revert` | `revert: rollback commit abc1234` | `revert` | 🔄 Reverts | **Patch** |

### Gitmoji

| Emoji | Shortcode | Meaning | Assigned Label | Category | Version Bump |
| :---: | :--- | :--- | :--- | :--- | :--- |
| 💥 | `:boom:` | Breaking change | `boom`, `breaking` | 💥 Breaking Changes | **Major** |
| ✨ | `:sparkles:` | New feature | `sparkles` | ✨ Features & Enhancements | **Minor** |
| 🎉 | `:tada:` | Initial release / celebration | `tada` | ✨ Features & Enhancements | **Minor** |
| 🚀 | `:rocket:` | Deployment / performance | `rocket` | ✨ Features & Enhancements | **Minor** |
| 🐛 | `:bug:` | Bug fix | `bug` | 🐛 Bug Fixes & Security | **Patch** |
| 🚑️ | `:ambulance:` | Critical hotfix | `ambulance` | 🐛 Bug Fixes & Security | **Patch** |
| 🩹 | `:adhesive_bandage:` | Simple fix | `adhesive-bandage` | 🐛 Bug Fixes & Security | **Patch** |
| 🔒️ | `:lock:` | Security fix | `security` | 🐛 Bug Fixes & Security | **Patch** |
| ⚡️ | `:zap:` | Performance improvement | `zap` | ⚡ Performance Improvements | **Patch** |
| 📝 | `:memo:` | Documentation | `memo` | 📝 Documentation | **Patch** |
| 📦️ | `:package:` | Compiled package / dependencies | `package`, `dependencies`| 📦 Dependency Updates | **Patch** |
| ⬆️ | `:arrow_up:` | Upgrade dependencies | `dependencies` | 📦 Dependency Updates | **Patch** |
| ♻️ | `:recycle:` | Refactoring | `recycle` | ♻️ Code Refactoring & Style | **Patch** |
| 🎨 | `:art:` | Code style / formatting | `art` | ♻️ Code Refactoring & Style | **Patch** |
| 👷 | `:construction_worker:` | CI build system | `construction-worker` | 👷 CI, Build & Tooling | **Patch** |
| 🔧 | `:wrench:` | Configuration / tooling | `wrench` | 👷 CI, Build & Tooling | **Patch** |
| ✅ | `:white_check_mark:` | Adding/updating tests | `test` | 🧪 Tests | **Patch** |
| ⏪️ | `:rewind:` | Revert changes | `rewind` | ⏪️ Reverts | **Patch** |

---

## 🏷️ Automated Version Tags & Floating Major Tag (`@v1`)

This repository is equipped with automated CI/CD for release management and floating tags:

- **Release Drafting & Publishing**: [`.github/workflows/release.yaml`](.github/workflows/release.yaml) drafts releases automatically on merge to `main` and on pull requests using `.github/release-drafter.yaml`. Releases can be published manually via `workflow_dispatch` or via the GitHub Releases UI.
- **Floating Major Version Tag (`v1`)**: [`.github/workflows/update-major-tag.yaml`](.github/workflows/update-major-tag.yaml) automatically updates the floating major tag (`v1`) to track the latest release (e.g., `v1.2.3`). Consumers referencing `uses: juftin/release-drafter@v1` or `uses: juftin/release-drafter/workflow.yaml@v1` will automatically receive backward-compatible updates without manual intervention.

---

## 🔒 Permissions & Security

When triggering on pull requests, use **`pull_request_target`** instead of `pull_request` so the workflow has permissions to add labels to PRs opened from forks:

```yaml
permissions:
  contents: write       # Required to draft or publish releases and create tags
  pull-requests: write  # Required for autolabeler to add labels to PRs
```

> [!NOTE]
> Releases drafted with the default `GITHUB_TOKEN` will not trigger downstream workflows listening to `on: release: [published]`. If your deployment pipeline triggers on release events, pass a Personal Access Token (PAT) with `repo` scope to the workflow:
> ```yaml
> with:
>   config: conventional-commits
> secrets:
>   token: ${{ secrets.CUSTOM_RELEASE_TOKEN }}
> ```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
