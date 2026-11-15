#!/usr/bin/env python3
"""GIT_ASKPASS credential helper for gitsync git subprocesses.

Provides credentials to git without exposing tokens on argv or persisting
them in the hub mirror's git config.
"""

import os
import subprocess
import sys


def _token_from_glab(host):
    for args in (["auth", "status", "-t"], ["auth", "token"]):
        try:
            r = subprocess.run(["glab"] + args, capture_output=True,
                               text=True, timeout=20,
                               env={"GLAB_HOST": host,
                                    "PATH": os.environ.get("PATH", "/usr/bin:/bin")})
        except Exception:
            continue
        out = (r.stdout or "").strip()
        if out and r.returncode == 0:
            return out.splitlines()[0].strip()
    return ""


def _token_from_gh(_host):
    """the gh CLI's token, so `auth: "gh"` works for private repos too.

    Nothing used to configure git credentials for that mode: the clone URL
    carried no token and there was no askpass, so every private repo failed
    with "could not read Username" unless the user happened to have run
    `gh auth setup-git` themselves.
    """
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True,
                           text=True, timeout=20,
                           env={"PATH": os.environ.get("PATH",
                                                       "/usr/bin:/bin")})
    except Exception:
        return ""
    out = (r.stdout or "").strip()
    return out.splitlines()[0].strip() if out and r.returncode == 0 else ""


def main(argv=None):
    if argv is None:
        argv = sys.argv
    prompt = argv[1] if len(argv) > 1 else ""
    p_lower = prompt.lower()

    if "username" in p_lower:
        user = os.environ.get("GITSYNC_ASKPASS_USER")
        if user:
            print(user)
        else:
            # Default username for OAuth/PAT tokens
            print("x-access-token")
        return

    # Password / Token prompt:
    token = os.environ.get("GITSYNC_ASKPASS_TOKEN")
    if token:
        print(token)
        return

    # Fallback for glab/gh auth if token not in env. GITSYNC_ASKPASS_PROVIDER
    # pins the CLI a platform set up (gh for GitHub, glab for GitLab), so a
    # GitLab host never gets a GitHub token or the other way round.
    provider = (os.environ.get("GITSYNC_ASKPASS_PROVIDER") or "").lower()
    order = [provider] if provider in ("gh", "glab") else ["glab", "gh"]
    host = os.environ.get("GITLAB_HOST") or os.environ.get("GLAB_HOST") or "https://gitlab.com"
    for a in argv[1:]:
        if a.startswith("http"):
            host = a
    for which in order:
        t = (_token_from_gh(host) if which == "gh" else _token_from_glab(host))
        if t:
            print(t)
            return

    print("")


if __name__ == "__main__":
    main()
