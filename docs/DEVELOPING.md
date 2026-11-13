# Developing gitsync

Standard library only, Python 3.8+. `python3 -m gitsync ci` runs the same
checks the CI workflows run, and `tests/test_logic.py` is the only home for
logic assertions.

## CI without a provider

`gitsync ci` runs the entire check suite locally in about two seconds —
no account, no runner, no verification:

```bash
gitsync ci
#   python syntax (gitsync/*.py)    PASS
#   shell syntax (installers)       PASS
#   CLI smoke tests                 PASS
#   unit checks (tests.test_logic)  PASS
# CI: all 4 checks passed
```

The same checks are also in `.github/workflows/ci.yml`,
`.forgejo/workflows/ci.yml` and `.gitlab-ci.yml` for hosted CI, and this repo
ships a `pre-push` hook that runs them before every push
(`git push --no-verify` to bypass).

---

[← back to the README](../README.md)
