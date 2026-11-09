"""Platform adapters: GitHub, GitLab, Forgejo/Gitea, and local folders.

Every adapter exposes:
    test()          -> (ok: bool, info: str)
    user()          -> str | None
    list_repos()    -> [{"name", "private", "clone_url"}]   (the source side)
    ensure_repo(r)  -> push URL (creates the repo if missing)  (target side)
    repo_url(r)     -> expected URL, read-only (used by sync --dry-run)

Auth: tokens are embedded in URLs used by the tool (config file is 0600),
so no ambient git credential helper is required.
"""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

TIMEOUT = 60


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def ssh_url(host, owner, name, port=None):
    """scp-style SSH url: git@host:owner/name.git (port: ssh://git@host:port/...)"""
    host = host.replace("https://", "").replace("http://", "").rstrip("/")
    if port and int(port) != 22:
        return "ssh://git@%s:%s/%s/%s.git" % (host, int(port), owner, name)
    return "git@%s:%s/%s.git" % (host, owner, name)


def _fs_safe(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


GIT_TIMEOUT = 14400        # long operations (clone/push) pass this explicitly
API_TIMEOUT = 120          # CLI/API calls (gh repo list, glab api, ...)


def run(cmd, cwd=None, timeout=300):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL,
                           cwd=cwd, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %ss" % timeout
    except Exception as e:
        return 1, "", str(e)


def _http(method, url, token=None, payload=None, timeout=TIMEOUT,
          auth_style="token", want_headers=False):
    """auth_style: 'token' (GitHub/Forgejo), 'private-token' or 'bearer' (GitLab)

    want_headers=True returns (status, body, headers) instead of
    (status, body) — needed to read GitHub's token-expiry header.
    """
    data = json.dumps(payload).encode() if payload is not None else None
    # GitLab.com rate-limits aggressively (HTTP 429) and briefly returns
    # 502/503 under load. A sync of 50 repos bursts hard, so back off and
    # retry rather than reporting a perfectly healthy repo as failed.
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, method=method)
        if token:
            if auth_style == "private-token":
                req.add_header("PRIVATE-TOKEN", token)
            elif auth_style == "bearer":
                req.add_header("Authorization", "Bearer " + token)
            else:
                req.add_header("Authorization", "token " + token)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode() or "{}"
                hdrs = {k.lower(): v for k, v in r.headers.items()}
                try:
                    parsed = json.loads(body)
                except ValueError:
                    parsed = body
                return (r.status, parsed, hdrs) if want_headers \
                    else (r.status, parsed)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                body = json.loads(body)
            except ValueError:
                pass
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                wait = 2 ** attempt
                try:
                    ra = e.headers.get("Retry-After") if e.headers else None
                    if ra:
                        wait = min(float(ra), 30)
                except (TypeError, ValueError):
                    pass
                time.sleep(wait)
                continue
            return e.code, body
        except Exception as e:
            return None, str(e)[:200]
    return None, "request failed after retries"


# ---------------------------------------------------------------- GitHub

class GitHubPlatform:
    ptype = "github"

    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg
        self.url = (cfg.get("url") or "https://github.com").rstrip("/")
        self.owner = cfg.get("owner") or ""
        self.token = cfg.get("token") or os.environ.get("GITHUB_TOKEN", "")
        self.auth = cfg.get("auth") or "gh"   # "gh" = use gh CLI

    # -- helpers
    def _gh(self, args):
        return run(["gh"] + args, timeout=API_TIMEOUT)

    def _api(self, path):
        return _http("GET", "https://api.github.com" + path, token=self.token)

    def user(self):
        if self.auth == "gh" and not self.token:
            rc, out, _ = self._gh(["api", "user", "--jq", ".login"])
            if rc == 0:
                return out.strip()
        rc, out = self._api("/user")
        if isinstance(out, dict) and out.get("login"):
            return out["login"]
        return None

    def test(self):
        if self.auth == "gh" and not self.token:
            rc, out, err = self._gh(["auth", "status"])
            if rc == 0:
                u = self.user() or "unknown"
                return True, "gh CLI authenticated as %s" % u
            return False, "gh CLI not authenticated (run: gh auth login)"
        rc, out = self._api("/user")
        if isinstance(out, dict) and out.get("login"):
            return True, "token OK — user %s" % out["login"]
        return False, "API failed: %s" % (out if isinstance(out, str)
                                          else out.get("message", out))

    def _clone_url(self, full_name):
        if self.cfg.get("protocol") == "ssh":
            return ssh_url(self.url, *full_name.split("/", 1))
        if self.token:
            return "https://x-access-token:%s@%s/%s.git" % (
                self.token, self.url.replace("https://", ""), full_name)
        return "https://%s/%s.git" % (self.url.replace("https://", ""),
                                      full_name)

    def list_repos(self):
        repos = []
        if self.auth == "gh" and not self.token:
            rc, out, err = self._gh(["repo", "list", "--limit", "500",
                                     "--json", "name,visibility,isFork,url"])
            if rc != 0:
                raise RuntimeError("gh repo list failed: " + err[:200])
            for r in json.loads(out):
                repos.append({"name": r["name"], "private":
                              r.get("visibility") != "PUBLIC",
                              "fork": bool(r.get("isFork")),
                              "clone_url": r["url"]})
            return repos
        # REST: owned repos, paginated
        page = 1
        while True:
            rc, out = self._api("/user/repos?per_page=100&page=%d"
                                   "&affiliation=owner" % page)
            if not isinstance(out, list):
                raise RuntimeError("GitHub API failed: %s" % out)
            for r in out:
                repos.append({"name": r["name"],
                              "private": bool(r.get("private")),
                              "fork": bool(r.get("fork")),
                              "clone_url": self._clone_url(r["full_name"])})
            if len(out) < 100:
                break
            page += 1
        return repos

    def repo_url(self, repo):
        """expected URL without creating anything (read-only probing)"""
        owner = self.owner or (self.user() or "")
        return self._clone_url("%s/%s" % (owner, repo["name"]))

    def prepare_push(self, repo, push_url):
        """allow a mirror force-push to succeed. GitHub: nothing to do."""
        return []

    def token_info(self):
        """expiry + scopes, for the token-expiry warning"""
        if not self.token:
            return {"has_token": False,
                    "note": "gh CLI auth — expiry not exposed"}
        rc, out, hdrs = _http("GET", "https://api.github.com/user",
                              token=self.token, want_headers=True)
        if not isinstance(out, dict) or not out.get("login"):
            return {"has_token": True, "error": "token rejected"}
        return {"has_token": True,
                "expires_at": hdrs.get("github-authentication-token-expiration"),
                "scopes": hdrs.get("x-oauth-scopes"),
                "user": out.get("login")}

    def ensure_repo(self, repo):
        owner = self.owner or (self.user() or "")
        name = repo["name"]
        # does it exist?
        rc, out = self._api("/repos/%s/%s" % (owner, name))
        if isinstance(out, dict) and out.get("full_name"):
            return self._clone_url(out["full_name"])
        if rc == 404 or (isinstance(out, dict) and out.get("message") == "Not Found"):
            rc2, out2 = self._api("/user/repos", "POST",
                                  {"name": name,
                                   "private": repo.get("private", False),
                                   "auto_init": False})
            if isinstance(out2, dict) and out2.get("full_name"):
                return self._clone_url(out2["full_name"])
            raise RuntimeError("GitHub create failed: %s"
                               % (out2.get("message") if isinstance(out2, dict)
                                  else out2))
        raise RuntimeError("GitHub API error: %s" % out)


# ---------------------------------------------------------------- GitLab

def gitlab_path(name):
    """sanitise a repo name into a valid GitLab project path.

    GitLab is stricter than GitHub: a path may hold only non-accented
    letters, digits, '_', '-' and '.', may not start with '-', '_' or '.',
    and may not end with '-', '_', '.', '.git' or '.atom'. A GitHub name
    like '_gstest_src' is legal there but rejected here, which used to
    surface as an opaque 'GitLab create failed'.
    """
    import unicodedata
    p = unicodedata.normalize("NFKD", str(name))
    p = p.encode("ascii", "ignore").decode("ascii")
    p = re.sub(r"[^A-Za-z0-9_.\-]", "-", p)
    p = p.lstrip("-_.")
    for suffix in (".git", ".atom"):
        if p.lower().endswith(suffix):
            p = p[:-len(suffix)]
    p = p.rstrip("-_.")
    return p or "repo"


def _api_error(out):
    """flatten a GitLab/Gitea error body into something readable"""
    if isinstance(out, dict):
        msg = out.get("message")
        if isinstance(msg, dict):
            # validation errors: {'path': ['can only include ...']}
            bits = []
            for k, v in msg.items():
                if isinstance(v, list):
                    bits.append("%s: %s" % (k, "; ".join(str(x) for x in v)))
                else:
                    bits.append("%s: %s" % (k, v))
            return " | ".join(bits)
        if isinstance(msg, list):
            return "; ".join(str(x) for x in msg)
        if msg:
            return str(msg)
        if out.get("error"):
            return str(out["error"])
    return str(out)[:200]

class GitLabPlatform:
    ptype = "gitlab"

    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg
        self.url = (cfg.get("url") or "https://gitlab.com").rstrip("/")
        self.owner = cfg.get("owner") or ""
        self.token = cfg.get("token") or os.environ.get("GITLAB_TOKEN", "")
        self.ns_id = cfg.get("ns_id") or ""

    def _api(self, path, method="GET", payload=None):
        # GitLab wants PRIVATE-TOKEN (PAT) or Bearer, not Authorization: token
        return _http(method, self.url + "/api/v4" + path,
                     token=self.token, payload=payload,
                     auth_style="private-token")

    def user(self):
        rc, out = self._api("/user")
        if isinstance(out, dict) and out.get("username"):
            return out["username"]
        return None

    def test(self):
        rc, out = self._api("/user")
        if isinstance(out, dict) and out.get("username"):
            return True, "token OK — user %s" % out["username"]
        return False, "API failed: %s" % (out.get("message")
                                          if isinstance(out, dict) else out)

    def list_repos(self):
        repos, page = [], 1
        while True:
            rc, out = self._api("/projects?membership=true&per_page=100"
                                   "&page=%d" % page)
            if not isinstance(out, list):
                raise RuntimeError("GitLab API failed: %s" % out)
            for p in out:
                repos.append({"name": p["name"], "private":
                              p.get("visibility") == "private",
                              "fork": bool(p.get("forked_from_project")),
                              "clone_url": self._url_with_token(
                                  p.get("http_url_to_repo"))})
            if len(out) < 100:
                break
            page += 1
        return repos

    def _url_with_token(self, http_url):
        if self.cfg.get("protocol") == "ssh" and http_url:
            # https://gitlab.com/group/repo.git -> git@gitlab.com:group/repo.git
            path = http_url.split("://", 1)[-1].split("/", 1)[-1]
            if path.endswith(".git"):
                path = path[:-4]
            return ssh_url(self.url, *path.rsplit("/", 1))
        if self.token and http_url:
            return http_url.replace("://", "://oauth2:%s@" % self.token, 1)
        return http_url

    def repo_url(self, repo):
        """expected URL without creating anything (read-only probing)"""
        owner = self.owner or (self.user() or "")
        return self._url_with_token("%s/%s/%s.git" % (self.url, owner,
                                                      gitlab_path(repo["name"])))

    def prepare_push(self, repo, push_url):
        """Enable force-push on protected branches.

        A protected branch rejects force-push by default, so a full mirror
        push dies with "You are not allowed to force push code to a
        protected branch". The branch protection itself is kept (merge and
        push access levels unchanged); only allow_force_push is turned on,
        because that is what mirroring requires.
        """
        notes = []
        project = self._project_id(repo)
        if not project:
            return notes
        rc, out = self._api("/projects/%s/protected_branches" % project)
        if not isinstance(out, list):
            return notes
        for b in out:
            name = b.get("name")
            if not name or b.get("allow_force_push"):
                continue
            rc2, res = self._api(
                "/projects/%s/protected_branches/%s" % (project, name),
                "PATCH", {"allow_force_push": True})
            if rc2 in (200, 201):
                notes.append("%s: force-push enabled on '%s'" % (self.name,
                                                                 name))
            else:
                notes.append("%s: could not enable force-push on '%s' (%s)"
                             % (self.name, name, _api_error(res)))
        return notes

    def _project_id(self, repo):
        """numeric project id, or 'owner%2Fpath' for API paths"""
        path = gitlab_path(repo["name"])
        owner = self.owner
        if owner:
            return urllib.request.quote("%s/%s" % (owner, path), safe="")
        return None

    def token_info(self):
        """expiry + scopes, for the token-expiry warning"""
        if not self.token:
            return {"has_token": False}
        rc, out = self._api("/personal_access_tokens/self")
        if not isinstance(out, dict):
            return {"has_token": True, "error": "cannot read token info"}
        return {"has_token": True,
                "expires_at": out.get("expires_at"),
                "scopes": out.get("scopes"),
                "active": out.get("active"),
                "revoked": out.get("revoked"),
                "user": out.get("name")}

    def ensure_repo(self, repo):
        name = repo["name"]
        path = gitlab_path(name)
        # lookup by path under owner namespace
        if self.owner:
            rc, out = self._api("/projects/%s%%2F%s"
                                   % (self.owner, path))
            if isinstance(out, dict) and out.get("http_url_to_repo"):
                return self._url_with_token(out["http_url_to_repo"])
        ns_id = self.ns_id
        if not ns_id:
            rc, out = self._api("/namespaces")
            if isinstance(out, list) and out:
                ns_id = str(out[0]["id"])
        rc, out = self._api("/projects", "POST", {
            "name": name, "path": path,
            "namespace_id": int(ns_id) if str(ns_id).isdigit() else ns_id,
            "visibility": "private" if repo.get("private") else "public",
        })
        if isinstance(out, dict) and out.get("http_url_to_repo"):
            return self._url_with_token(out["http_url_to_repo"])
        # maybe created under a sanitized path — search for it
        rc, out = self._api("/projects?search=%s&membership=true"
                               % urllib.request.quote(name))
        if isinstance(out, list):
            for p in out:
                if _norm(p["name"]) == _norm(name):
                    return self._url_with_token(p["http_url_to_repo"])
        raise RuntimeError("GitLab create failed for '%s': %s"
                           % (path, _api_error(out)))


# ------------------------------------------------------- Forgejo / Gitea

class ForgejoPlatform:
    ptype = "forgejo"

    def __init__(self, name, cfg, ptype="forgejo"):
        self.name = name
        self.cfg = cfg
        self.ptype = ptype
        self.url = (cfg.get("url") or "http://localhost:3000").rstrip("/")
        self.owner = cfg.get("owner") or ""
        self.token = (cfg.get("token") or
                      os.environ.get("FORGEJO_TOKEN",
                                     os.environ.get("GITEA_TOKEN", "")))

    def _api(self, path, method="GET", payload=None):
        return _http(method, self.url + "/api/v1" + path,
                     token=self.token, payload=payload)

    def user(self):
        rc, out = self._api("/user")
        if isinstance(out, dict) and out.get("login"):
            return out["login"]
        return None

    def test(self):
        rc, out = self._api("/user")
        if isinstance(out, dict) and out.get("login"):
            return True, "token OK — user %s" % out["login"]
        return False, ("API failed: %s (check url + token scopes: "
                       "write:repository + write:user)"
                       % (out.get("message") if isinstance(out, dict)
                          else out))

    def _push_url(self, owner, name):
        if self.cfg.get("protocol") == "ssh":
            host = self.url.replace("https://", "").replace("http://", "")
            if ":" in host:                     # strip the HTTP port
                host = host.rsplit(":", 1)[0]
            # the http port (3000/3002...) is NOT the ssh port - use ssh_port
            # from the config, defaulting to standard 22
            port = self.cfg.get("ssh_port") or 22
            return ssh_url(host, owner, name, port)
        return "%s/%s/%s.git" % (self.url, owner, name)

    def list_repos(self):
        repos, page = [], 1
        while True:
            rc, out = self._api("/user/repos?limit=50&page=%d" % page)
            if not isinstance(out, list):
                raise RuntimeError("%s API failed: %s"
                                   % (self.ptype, out))
            for r in out:
                repos.append({"name": r["name"],
                              "private": bool(r.get("private")),
                              "fork": bool(r.get("fork")),
                              "clone_url": self._push_url(
                                  r["owner"]["login"], r["name"])})
            if len(out) < 50:
                break
            page += 1
        return repos

    def repo_url(self, repo):
        """expected URL without creating anything (read-only probing)"""
        owner = self.owner or (self.user() or "")
        return self._push_url(owner, repo["name"])

    def prepare_push(self, repo, push_url):
        """Allow force-push on protected branches (same reason as GitLab)."""
        notes = []
        owner = self.owner or (self.user() or "")
        path = _fs_safe(repo["name"])
        rc, out = self._api("/repos/%s/%s/branch_protections"
                            % (owner, path))
        if not isinstance(out, list):
            return notes
        for b in out:
            name = b.get("branch_name") or b.get("name")
            if not name:
                continue
            body = {"enable_push": True,
                    "enable_push_whitelist": False,
                    "enable_force_push": True}
            rc2, res = self._api(
                "/repos/%s/%s/branch_protections/%s" % (owner, path, name),
                "PATCH", body)
            if rc2 in (200, 201):
                notes.append("%s: force-push enabled on '%s'" % (self.name,
                                                                 name))
            elif rc2 == 404:
                # older Gitea had no PATCH: delete + recreate
                self._api("/repos/%s/%s/branch_protections/%s"
                          % (owner, path, name), "DELETE")
                rc3, res3 = self._api(
                    "/repos/%s/%s/branch_protections" % (owner, path),
                    "POST", {"branch_name": name, "enable_push": True,
                             "enable_force_push": True})
                if rc3 in (200, 201):
                    notes.append("%s: recreated protection on '%s' with "
                                 "force-push" % (self.name, name))
                else:
                    notes.append("%s: could not relax '%s' (%s)"
                                 % (self.name, name, _api_error(res3)))
            else:
                notes.append("%s: could not relax '%s' (%s)"
                             % (self.name, name, _api_error(res)))
        return notes

    def token_info(self):
        """Forgejo/Gitea access tokens do not expire by default"""
        return {"has_token": bool(self.token), "expires_at": None,
                "note": "%s tokens do not expire" % self.ptype}

    def ensure_repo(self, repo):
        name = repo["name"]
        owner = self.owner or (self.user() or "")
        rc, out = self._api("/repos/%s/%s" % (owner, name))
        if isinstance(out, dict) and out.get("clone_url"):
            return self._push_url(owner, name)
        rc, out = self._api("/user/repos", "POST", {
            "name": name, "private": bool(repo.get("private")),
            "auto_init": False,
        })
        if isinstance(out, dict) and out.get("name"):
            return self._push_url(owner, out["name"])
        raise RuntimeError("%s create failed: %s"
                           % (self.ptype,
                              out.get("message") if isinstance(out, dict) else out))


class GiteaPlatform(ForgejoPlatform):
    ptype = "gitea"


# ---------------------------------------------------------------- Local

class LocalPlatform:
    ptype = "local"

    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg
        self.path = os.path.expanduser(cfg.get("path") or "")

    def user(self):
        return None

    def test(self):
        if not self.path:
            return False, "no path configured"
        if not os.path.isdir(self.path):
            try:
                os.makedirs(self.path, exist_ok=True)
            except OSError as e:
                return False, "cannot create %s: %s" % (self.path, e)
        return True, "folder ready: %s" % self.path

    def list_repos(self):
        repos = []
        if not os.path.isdir(self.path):
            return repos
        for entry in sorted(os.listdir(self.path)):
            full = os.path.join(self.path, entry)
            if entry.endswith(".git") and os.path.isdir(full):
                rc, out, _ = run(["git", "for-each-ref", "--format=%(refname)"],
                                 cwd=full, timeout=30)
                if rc == 0 and out.strip():
                    repos.append({"name": entry[:-4], "private": True,
                                  "fork": False, "clone_url": full})
        return repos

    def repo_url(self, repo):
        """expected URL without creating anything (read-only probing)"""
        return os.path.join(self.path, _fs_safe(repo["name"]) + ".git")

    def prepare_push(self, repo, push_url):
        """a bare folder has no branch protection"""
        return []

    def token_info(self):
        """local folders need no credentials"""
        return {"has_token": False, "note": "local folder — no credentials"}

    def ensure_repo(self, repo):
        if not self.path:
            raise RuntimeError("local platform has no path configured")
        os.makedirs(self.path, exist_ok=True)
        dest = os.path.join(self.path, _fs_safe(repo["name"]) + ".git")
        if not os.path.isdir(dest):
            rc, _, err = run(["git", "init", "--bare", dest])
            if rc != 0:
                raise RuntimeError("git init --bare failed: %s" % err[:200])
        return dest



# ---------------------------------------------------------------- Bitbucket

class BitbucketPlatform:
    """Bitbucket Cloud (api.bitbucket.org/2.0).

    Auth: an Atlassian API token sent as a Bearer token (app passwords are
    deprecated). Needs the workspace (owner) to list and create repositories.
    """

    ptype = "bitbucket"

    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg
        self.url = (cfg.get("url") or "https://api.bitbucket.org").rstrip("/")
        self.owner = cfg.get("owner") or ""      # workspace
        self.token = cfg.get("token") or os.environ.get("BITBUCKET_TOKEN", "")

    def _api(self, path, method="GET", payload=None):
        return _http(method, self.url + "/2.0" + path, token=self.token,
                     payload=payload, auth_style="bearer")

    def user(self):
        rc, out, _ = self._api("/user")
        if isinstance(out, dict) and out.get("username"):
            return out["username"]
        return None

    def test(self):
        if not self.owner:
            return False, "workspace (owner) is required for Bitbucket"
        rc, out, _ = self._api("/repositories/%s?pagelen=1" % self.owner)
        if isinstance(out, dict) and "values" in out:
            return True, "token OK — workspace %s" % self.owner
        return False, "API failed: %s" % (out.get("message")
                                          if isinstance(out, dict) else out)

    def _clone_url(self, clone_links):
        if self.cfg.get("protocol") == "ssh":
            for c in clone_links or []:
                if c.get("name") == "ssh":
                    return c["href"]
        for c in clone_links or []:
            if c.get("name") == "https":
                return c["href"]
        return None

    def list_repos(self):
        repos, path = [], "/repositories/%s?pagelen=100" % self.owner
        while path:
            rc, out, _ = self._api(path)
            if not isinstance(out, dict) or "values" not in out:
                raise RuntimeError("Bitbucket API failed: %s" % out)
            for r in out["values"]:
                repos.append({"name": r["name"],
                              "private": bool(r.get("is_private")),
                              "fork": False,
                              "clone_url": self._clone_url(
                                  r.get("links", {}).get("clone"))})
            nxt = out.get("next")
            path = nxt.replace(self.url + "/2.0", "") if nxt else None
        return repos

    def repo_url(self, repo):
        """expected URL without creating anything (read-only probing)"""
        return "%s/%s.git" % (self.url, repo["name"])

    def prepare_push(self, repo, push_url):
        return []

    def token_info(self):
        """Bitbucket app-password expiry is not exposed by the API"""
        return {"has_token": bool(self.token), "expires_at": None,
                "note": "Bitbucket app-password expiry not exposed"}

    def ensure_repo(self, repo):
        name = repo["name"]
        slug = name.lower().replace(" ", "-").replace("_", "-")
        rc, out, _ = self._api("/repositories/%s/%s" % (self.owner, slug))
        if isinstance(out, dict) and out.get("links"):
            url = self._clone_url(out["links"].get("clone"))
            if url:
                return url
        rc, out, _ = self._api("/repositories/%s/%s" % (self.owner, slug),
                               "POST", {"scm": "git",
                                        "is_private": bool(repo.get("private"))})
        if isinstance(out, dict) and out.get("links"):
            url = self._clone_url(out["links"].get("clone"))
            if url:
                return url
        raise RuntimeError("Bitbucket create failed: %s"
                           % (out.get("message") if isinstance(out, dict)
                              else out))


# ---------------------------------------------------------------- Generic

class GenericPlatform:
    """Any git server without an API — pure URL templates.

    Config example:
        {"type": "generic", "enabled": true,
         "url_template": "https://git.example.com/{owner}/{name}.git",
         "owner": "me", "token": "", "user": ""}

    Works as a TARGET (push). Cannot LIST repositories, so it cannot be a
    sync source — use `gitsync mirror <repo> --url ...` for one-off pushes,
    or pair it as a target of any API platform (github -> generic works).
    """

    ptype = "generic"

    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg
        self.template = cfg.get("url_template") or ""
        self.owner = cfg.get("owner") or ""
        self.token = cfg.get("token") or ""
        self.git_user = cfg.get("user") or "git"

    def user(self):
        return None

    def test(self):
        if not self.template or "{name}" not in self.template:
            return False, "url_template must contain {name}"
        return True, "template: %s" % self.template

    def repo_url(self, repo):
        """generic platforms have no predictable URL"""
        raise RuntimeError("generic platforms cannot probe repos")

    def prepare_push(self, repo, push_url):
        return []

    def token_info(self):
        return {"has_token": bool(self.token), "expires_at": None,
                "note": "generic — expiry unknown"}

    def list_repos(self):
        raise RuntimeError("generic platforms cannot list repos — "
                           "they have no API (use as a target only)")

    def _url(self, name):
        url = self.template.replace("{owner}", self.owner) \
                           .replace("{name}", name)
        if self.token:
            # embed credentials so pushes authenticate
            scheme, rest = url.split("://", 1)
            url = "%s://%s:%s@%s" % (scheme, self.git_user, self.token, rest)
        return url

    def ensure_repo(self, repo):
        return self._url(repo["name"])

# ---------------------------------------------------------------- factory

ADAPTERS = {
    "github": GitHubPlatform,
    "gitlab": GitLabPlatform,
    "forgejo": ForgejoPlatform,
    "gitea": GiteaPlatform,
    "bitbucket": BitbucketPlatform,
    "generic": GenericPlatform,
    "local": LocalPlatform,
}


def make(name, pcfg):
    ptype = pcfg.get("type", "local")
    cls = ADAPTERS.get(ptype)
    if cls is None:
        raise ValueError("unknown platform type: %s" % ptype)
    if cls is GiteaPlatform:
        return cls(name, pcfg, ptype="gitea")
    return cls(name, pcfg)
