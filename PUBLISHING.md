# Publishing checklist

The repository history has been rewritten to remove the old `vendor/` and
`tools/rubberband/` snapshots. The current tree and all commits no longer
contain those paths. Re-run the checks below before the first push or after
importing a vendor snapshot again.

## 1. Confirm what is tracked

```bash
git ls-files | grep -E '^(vendor/|tools/rubberband/)' && echo "STILL TRACKED"
git status --ignored --short
```

Expected: no `vendor/` or `tools/rubberband/` paths in `git ls-files`; they
appear as ignored (`!!`).

## 2. Back up the local repository

```bash
git clone --mirror . ../video-translate-backup.git
```

## 3. Remove vendor from all history (only if still present)

Install `git-filter-repo` once:

```bash
python -m pip install --user git-filter-repo
```

Then, from the repository root:

```bash
git filter-repo \
  --path vendor \
  --path tools/rubberband \
  --invert-paths
```

This is destructive. It removes those paths from every commit and rewrites
hashes. The working-tree files remain only if they are ignored/untracked; keep
a separate copy before running it.

If you prefer not to rewrite history, create a clean orphan repository instead:

```bash
git checkout --orphan clean-main
git add -A
git commit -m "Initial public release"
git branch -D main
git branch -m main
```

## 4. Secret scan

```bash
gitleaks detect --source . --redact
```

Also verify no `config.json`, `params.json`, API key, media, models, venv, or
`.runtime/` is tracked.

## 5. Add remote and push

```bash
git remote add origin https://github.com/<you>/video-translate.git
git push -u origin main
```

After pushing, check the GitHub file list: it should contain `SKILL.md`,
`README.md`, `AGENTS.md`, `LICENSE`, `THIRD_PARTY_NOTICES.md`, `scripts/`,
`references/`, `assets/glossary.txt`, `tests/`, and `.github/`, but **not**
`vendor/` or `tools/rubberband/`.

## 6. Release notes

Tag the first release after verifying a fresh clone can run:

```bash
git tag -a v0.1.0 -m "video-translate v0.1.0"
git push origin v0.1.0
```

A fresh-clone smoke test can use the automatic bootstrap:

```bash
python scripts/pipeline.py setup
python scripts/pipeline.py doctor
```

`setup` downloads pyVideoTrans v4.16 and IndexTTS2 v2.0.0, applies `patches/`,
and then downloads models/checkpoints. See `README.md` and
`THIRD_PARTY_NOTICES.md`.
