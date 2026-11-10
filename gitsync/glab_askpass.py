"""GIT_ASKPASS helper for `auth: "glab"` GitLab platforms.

git calls this with a prompt ("Username for 'https://gitlab.com': ") when it
needs credentials. The username is answered with a placeholder; the password
is read from the glab CLI at call time, so the token never appears in a
remote URL, a hub config file, or a log line.

Used only when a platform sets `"auth": "glab"`.
"""

import subprocess
import sys


def _token(host):
    for args in (["auth", "status", "-t"], ["auth", "token"]):
        try:
            r = subprocess.run(["glab"] + args, capture_output=True,
                               text=True, timeout=20,
                               env={"GLAB_HOST": host,
                                    "PATH": "/usr/bin:/bin"})
        except Exception:
            continue
        out = (r.stdout or "").strip()
        if out and r.returncode == 0:
            return out.splitlines()[0].strip()
    return ""


def main(argv):
    prompt = argv[1] if len(argv) > 1 else ""
    if "username" in prompt.lower():
        # any non-empty user works; the token carries the identity
        print("oauth2")
        return
    host = "https://gitlab.com"
    for a in argv:
        if a.startswith("http"):
            host = a
    print(_token(host))


if __name__ == "__main__":
    main(sys.argv)
