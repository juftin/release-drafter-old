# Contributing to Release Drafter

Thank you for your interest in contributing to this project!

## Development Workflow

### Prerequisites
- Python 3.10+ (for `scripts/generate_gitmoji.py`)
- Ruby 3.x (with standard `yaml` module for validation checks)
- Git

### Local Setup
Clone the repository:
```bash
git clone https://github.com/juftin/release-drafter.git
cd release-drafter
```

### Running Tests and Validations
Run the lint and validation suite:
```bash
make test
```

### Synchronizing Gitmojis
When updating or modifying Gitmoji support, update `data/gitmojis.json` or categories in `scripts/generate_gitmoji.py` and run:
```bash
make sync
```
Then verify with:
```bash
make test
```

### Submitting Pull Requests
1. Fork the repo and create your branch from `main`.
2. Follow Conventional Commits or Gitmoji commit message styles.
3. Ensure all tests and validations pass with `make test`.
4. Submit a pull request.
