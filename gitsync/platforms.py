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

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

TIMEOUT = 60


def _short_text(err, limit=160):
    """last meaningful line of a CLI error"""
    lines = [l.strip() for l in (err or "").strip().splitlines() if l.strip()]
    return (lines[-1] if lines else "")[:limit]


def _url_quote(s):
    """quote a path segment for a URL (GitLab project paths need %2F)"""
    import urllib.parse
    return urllib.parse.quote(str(s), safe="")


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def ssh_url(host, owner, name, port=None):
    """scp-style SSH url: git@host:owner/name.git (port: ssh://git@host:port/...)"""
    host = host.replace("https://", "").replace("http://", "").rstrip("/")
    if port and int(port) != 22:
        return "ssh://git@%s:%s/%s/%s.git" % (host, int(port), owner, name)
    return "git@%s:%s/%s.git" % (host, owner, name)


def _fs_safe(name):
    """a filesystem-safe folder name. Unicode letters survive.

    The old pattern was [^A-Za-z0-9._-], so every non-ASCII character became
    "_": "ünï" and "öñï" both became "_n_" and shared one folder (and one
    target repo). \\w is Unicode-aware, so those names round-trip.
    """
    return re.sub(r"[^\w.\-]", "_", str(name), flags=re.UNICODE)


def _fs_unique(name):
    """a folder name that is unique per *original* name.

    Sanitising is lossy — "a b" and "a_b" both map to "a_b" — so two repos
    shared one hub mirror and one target folder, and the second push failed
    with "destination path already exists" (or, with parallel workers,
    overwrote the first). A name that needs no change (every normal ASCII
    name) keeps its current path, so existing mirrors and targets are never
    migrated; only the lossy ones gain a short hash of the original.
    """
    name = str(name)
    safe = _fs_safe(name)
    if safe == name:
        return safe
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
    return "%s-%s" % (safe, digest)


GIT_TIMEOUT = 14400        # long operations (clone/push) pass this explicitly
API_TIMEOUT = 120          # CLI/API calls (gh repo list, gh api, ...)


def run(cmd, cwd=None, timeout=300, env=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL,
                           cwd=cwd, timeout=timeout, env=env)
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
            try:
                body = e.read().decode()
            finally:
                e.close()          # HTTPError is a file object: close the socket
            try:
                body = json.loads(body)
            except ValueError:
                pass
            hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
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
            # an error must still honour want_headers: callers such as
            # GitHubPlatform.token_info unpack three values to report
            # "token rejected" instead of crashing on a 401
            return (e.code, body, hdrs) if want_headers else (e.code, body)
        except Exception as e:
            return (None, str(e)[:200], {}) if want_headers else (None, str(e)[:200])
    return (None, "request failed after retries", {}) if want_headers else (None, "request failed after retries")


# ---------------------------------------------------------------- GitHub

DEFAULT_GITHUB_CLIENT_ID = "Ov23li6dtbvT9gmwepvU"
DEFAULT_GITHUB_SCOPES = "repo,read:user"


def github_device_flow_start(client_id=None, scopes=None):
    """Initiates GitHub Device Authorization Flow (RFC 8628).
    Returns dict:
      {
        "device_code": str,
        "user_code": str,
        "verification_uri": str,
        "expires_in": int,
        "interval": int
      }
    """
    cid = client_id or os.environ.get("GITSYNC_GITHUB_CLIENT_ID") or DEFAULT_GITHUB_CLIENT_ID
    sc = scopes or DEFAULT_GITHUB_SCOPES
    data = urllib.parse.urlencode({"client_id": cid, "scope": sc}).encode("utf-8")
    req = urllib.request.Request(
        "https://github.com/login/device/code",
        data=data,
        headers={"Accept": "application/json", "User-Agent": "gitsync"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))
    if not isinstance(res, dict) or "error" in res:
        err_msg = res.get("error_description", res.get("error", "Failed to start GitHub device flow")) if isinstance(res, dict) else "Invalid response from GitHub"
        raise RuntimeError(err_msg)
    if "device_code" not in res or "user_code" not in res:
        raise RuntimeError("Missing device_code or user_code in GitHub response")
    return res


def github_device_flow_poll(device_code, client_id=None):
    """Polls GitHub token endpoint for Device Flow completion.
    Returns:
      {"status": "pending"}
      {"status": "slow_down", ["interval": int]}
      {"status": "expired", "error": str}
      {"status": "ok", "token": str, "scope": str}
      {"status": "error", "error": str}
    """
    cid = client_id or os.environ.get("GITSYNC_GITHUB_CLIENT_ID") or DEFAULT_GITHUB_CLIENT_ID
    data = urllib.parse.urlencode({
        "client_id": cid,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://github.com/login/oauth/access_token",
        data=data,
        headers={"Accept": "application/json", "User-Agent": "gitsync"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8")
            data = json.loads(raw)
        except Exception:
            data = {"error": str(e)}
        finally:
            e.close()
    except Exception as e:
        return {"status": "error", "error": str(e)}

    if not isinstance(data, dict):
        return {"status": "error", "error": "Invalid response from GitHub"}

    if "access_token" in data:
        return {"status": "ok", "token": data["access_token"], "scope": data.get("scope", "")}

    err = data.get("error", "")
    if err == "authorization_pending":
        return {"status": "pending"}
    elif err == "slow_down":
        ret = {"status": "slow_down"}
        if "interval" in data:
            try:
                ret["interval"] = int(data["interval"])
            except (ValueError, TypeError):
                pass
        return ret
    elif err in ("expired_token", "access_denied"):
        return {"status": "expired", "error": err}
    else:
        return {"status": "error", "error": data.get("error_description", err or "unknown error")}


class GitHubPlatform:
    ptype = "github"
    _rate_limits = {}

    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg
        self.url = (cfg.get("url") or "https://github.com").rstrip("/")
        self.owner = cfg.get("owner") or ""
        self.token = cfg.get("token") or os.environ.get("GITHUB_TOKEN", "")
        self.auth = cfg.get("auth") or "gh"   # "gh" = use gh CLI

    @classmethod
    def update_rate_limit(cls, name, headers):
        if not headers or not name:
            return
        rem = headers.get("x-ratelimit-remaining")
        lim = headers.get("x-ratelimit-limit")
        rst = headers.get("x-ratelimit-reset")
        if rem is not None and lim is not None:
            try:
                cls._rate_limits[name] = {
                    "remaining": int(rem),
                    "limit": int(lim),
                    "reset": int(rst) if rst else 0,
                    "updated_at": time.time(),
                }
            except (ValueError, TypeError):
                pass

    def rate_limit_info(self):
        info = GitHubPlatform._rate_limits.get(self.name)
        if info:
            now = time.time()
            reset_ts = info.get("reset", 0)
            if reset_ts and now >= reset_ts:
                GitHubPlatform._rate_limits.pop(self.name, None)
                return None
            resets_in = max(0, int(reset_ts - now))
            resets_min = (resets_in + 59) // 60
            return {
                "remaining": info["remaining"],
                "limit": info["limit"],
                "reset": info["reset"],
                "resets_in_secs": resets_in,
                "resets_in_mins": resets_min,
            }
        return None

    # -- helpers
    def _gh(self, args):
        return run(["gh"] + args, timeout=API_TIMEOUT)

    def _api_base(self):
        """api.github.com for github.com, {url}/api/v3 for Enterprise.

        GHE was broken before: the API host was hardcoded, so a self-hosted
        instance could never be listed.
        """
        host = self.url.replace("https://", "").replace("http://", "")
        if host == "github.com":
            return "https://api.github.com"
        return "https://%s/api/v3" % host

    def _api(self, path, method="GET", payload=None):
        rc, out, hdrs = _http(method, self._api_base() + path,
                              token=self.token, payload=payload, want_headers=True)
        self.update_rate_limit(self.name, hdrs)
        return rc, out

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
            # one call, not two: `gh auth status` then `gh api user` cost
            # ~1.4s of the 2.7s probe. If gh is not logged in, `gh api user`
            # says so just as clearly.
            rc, out, _ = self._gh(["api", "user", "--jq", ".login"])
            if rc == 0 and out.strip():
                return True, "gh CLI authenticated as %s" % out.strip()
            return False, ("gh CLI not authenticated "
                           "(run: gh auth login)")
        rc, out = self._api("/user")
        if isinstance(out, dict) and out.get("login"):
            return True, "token OK — user %s" % out["login"]
        return False, "API failed: %s" % (out if isinstance(out, str)
                                          else out.get("message", out))

    def _clone_url(self, full_name):
        if self.cfg.get("protocol") == "ssh":
            return ssh_url(self.url, *full_name.split("/", 1))
        scheme = "https" if self.url.startswith("https") else "http"
        host = self.url.replace("https://", "").replace("http://", "").rstrip("/")
        if self.token:
            return "%s://x-access-token:%s@%s/%s.git" % (
                scheme, urllib.parse.quote(self.token, safe=""), host, full_name)
        return "%s://%s/%s.git" % (scheme, host, full_name)

    def list_repos(self):
        """every repo this account can see, paginated to the end.

        `gh repo list --limit 500` used to cap the listing, and the REST path
        asked for `affiliation=owner` only, so organization repos were never
        mirrored even with the read:org scope. `github.affiliation` in the
        config now controls that (default owner, as before).
        """
        repos = []
        if self.auth == "gh" and not self.token:
            page = 1
            while True:
                rc, out, err = self._gh(
                    ["api", "user/repos?per_page=100&page=%d" % page,
                     "--jq", ".[] | [.name, .visibility, .fork, .html_url, "
                             ".archived] "
                             "| @tsv"])
                if rc != 0:
                    raise RuntimeError("gh api user/repos failed: "
                                       + err[:200])
                rows = [l.split("\t") for l in out.splitlines() if l.strip()]
                for r in rows:
                    name = r[0]
                    vis = r[1] if len(r) > 1 else "PUBLIC"
                    fork = (r[2] if len(r) > 2 else "false") == "true"
                    archived = (r[4] if len(r) > 4 else "false") == "true"
                    url = r[3] if len(r) > 3 else ""
                    rid = (url.split("github.com/")[-1].replace(".git", "")
                           if "github.com/" in url else name)
                    repos.append({"name": name, "id": rid or name,
                                  "private": vis != "PUBLIC", "archived": archived,
                                  "fork": fork, "clone_url": url})
                if len(rows) < 100:
                    break
                page += 1
                if page > 30:            # 3000 repos is already absurd
                    break
            return repos
        affiliation = self.cfg.get("affiliation") or "owner"
        page = 1
        while True:
            rc, out = self._api("/user/repos?per_page=100&page=%d"
                                "&affiliation=%s" % (page, affiliation))
            if not isinstance(out, list):
                raise RuntimeError("GitHub API failed: %s" % out)
            for r in out:
                repos.append({"name": r["name"],
                              "id": r.get("full_name") or r["name"],
                              "private": bool(r.get("private")),
                              "fork": bool(r.get("fork")),
                              "archived": bool(r.get("archived")),
                              "clone_url": self._clone_url(r["full_name"])})
            if len(out) < 100:
                break
            page += 1
            if page > 30:
                break
        return repos

    def repo_url(self, repo):
        """expected URL without creating anything (read-only probing)"""
        owner = self.owner or (self.user() or "")
        return self._clone_url("%s/%s" % (owner, repo["name"]))

    def prepare_push(self, repo, push_url):
        """allow a mirror force-push to succeed. GitHub: nothing to do."""
        return []

    def delete_repo(self, repo):
        """permanently delete a repository. Opt-in only (see sync.prune_repos):
        a sync never calls this unless the route names this target."""
        owner = self.owner or (self.user() or "")
        rc, out = self._api("/repos/%s/%s" % (owner, repo["name"]), "DELETE")
        if rc in (204, 200):
            return True, ""
        return False, _short_text(out if isinstance(out, str) else
                                  (out or {}).get("message", ""))

    def token_info(self):
        """expiry + scopes + rate limit, for the token-expiry warning"""
        if not self.token:
            return {"has_token": False,
                    "note": "gh CLI auth — expiry not exposed"}
        rc, out, hdrs = _http("GET", self._api_base() + "/user",
                              token=self.token, want_headers=True)
        self.update_rate_limit(self.name, hdrs)
        if rc is None:
            return {"has_token": True, "error": "unreachable"}
        if rc in (401, 403):
            return {"has_token": True, "error": "token rejected"}
        if not isinstance(out, dict) or not out.get("login"):
            return {"has_token": True, "error": "token rejected"}
        res = {"has_token": True,
               "expires_at": hdrs.get("github-authentication-token-expiration"),
               "scopes": hdrs.get("x-oauth-scopes"),
               "user": out.get("login")}
        rate = self.rate_limit_info()
        if rate:
            res["rate_limit"] = rate
        return res

    def ensure_repo(self, repo):
        owner = self.owner or (self.user() or "")
        name = repo["name"]
        # does it exist?
        rc, out = self._api("/repos/%s/%s" % (owner, name))
        if isinstance(out, dict) and out.get("full_name"):
            return self._clone_url(out["full_name"])
        if self.auth == "gh" and not self.token:
            # the gh CLI owns the credential, so let it do the creation
            rc2, _o, err2 = self._gh(
                ["repo", "create", "%s/%s" % (owner, name),
                 "--private" if repo.get("private") else "--public",
                 "--description", "mirrored by gitsync"])
            if rc2 != 0 and "already exists" not in (err2 or "").lower():
                raise RuntimeError("GitHub create failed: "
                                   + _short_text(err2))
            return self._clone_url("%s/%s" % (owner, name))
        if rc == 404 or (isinstance(out, dict)
                         and out.get("message") == "Not Found"):
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
        # auth: "glab" uses the CLI for the API and a GIT_ASKPASS helper for
        # git, so the token never lands in a remote URL
        self.auth = cfg.get("auth") or "token"

    def _api(self, path, method="GET", payload=None):
        # auth: "glab" shells out to the CLI instead of sending a token
        if self.auth == "glab":
            return self._glab_api(path, method, payload)
        # GitLab wants PRIVATE-TOKEN (PAT) or Bearer, not Authorization: token
        return _http(method, self.url + "/api/v4" + path,
                     token=self.token, payload=payload,
                     auth_style="private-token")

    def _glab(self, args, stdin_text=None):
        if stdin_text is None:
            return run(["glab"] + args, timeout=API_TIMEOUT)
        try:
            r = subprocess.run(["glab"] + args, capture_output=True,
                               text=True, input=stdin_text,
                               timeout=API_TIMEOUT)
            return r.returncode, r.stdout, r.stderr
        except Exception as e:
            return 1, "", str(e)

    def _glab_api(self, path, method="GET", payload=None):
        """glab api <path>. Returns (status, body) like _http."""
        args = ["api", "--method", method, path]
        if payload is not None:
            args += ["--input", "-"]
        rc, out, err = self._glab(
            args, stdin_text=json.dumps(payload) if payload else None)
        if rc != 0:
            return None, (err or out).strip()[:200]
        try:
            return 200, json.loads(out)
        except ValueError:
            return 200, out

    def user(self):
        rc, out = self._api("/user")
        if isinstance(out, dict) and out.get("username"):
            return out["username"]
        return None

    def test(self):
        if self.auth == "glab":
            rc, _o, err = self._glab(["auth", "status"])
            if rc != 0:
                return False, ("glab not logged in - run 'glab auth login', "
                               "or paste a token instead")
            u = self.user() or "unknown"
            return True, "glab CLI authenticated as %s" % u
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
                repos.append({"name": p["name"],
                              "id": p.get("path_with_namespace") or
                              p["name"], "private":
                              p.get("visibility") == "private",
                              "fork": bool(p.get("forked_from_project")),
                              "archived": bool(p.get("archived")),
                              "clone_url": self._url_with_token(
                                  p.get("http_url_to_repo"))})
            if len(out) < 100:
                break
            page += 1
        return repos

    def git_env(self):
        """environment for git subprocesses when auth=glab.

        The token is read from `glab auth status -t` at call time by a tiny
        helper, so it is never written into a remote URL (and so never into
        a hub config file).
        """
        if self.auth != "glab":
            return None
        helper = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "glab_askpass.py")
        try:
            from .sync import ensure_executable_helper
            helper = ensure_executable_helper(helper)
        except Exception:
            pass
        env = dict(os.environ)
        env["GIT_ASKPASS"] = helper
        env["GITLAB_HOST"] = self.url
        return env

    def _url_with_token(self, http_url):
        if self.cfg.get("protocol") == "ssh" and http_url:
            # https://gitlab.com/group/repo.git -> git@gitlab.com:group/repo.git
            path = http_url.split("://", 1)[-1].split("/", 1)[-1]
            if path.endswith(".git"):
                path = path[:-4]
            return ssh_url(self.url, *path.rsplit("/", 1))
        if self.token and http_url:
            return http_url.replace("://", "://oauth2:%s@"
                                    % urllib.parse.quote(self.token, safe=""), 1)
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

    def delete_repo(self, repo):
        """permanently delete a project. Opt-in only (see sync.prune_repos)."""
        path = gitlab_path(repo["name"])
        proj = "%s/%s" % (self.owner, path) if self.owner else path
        rc, out = self._api("/projects/%s" % urllib.request.quote(proj,
                                                                  safe=""),
                            "DELETE")
        if rc in (202, 204, 200):
            return True, ""
        return False, _short_text(out if isinstance(out, str) else
                                  (out or {}).get("message", ""))

    def token_info(self):
        """expiry + scopes, for the token-expiry warning"""
        if not self.token:
            return {"has_token": False,
                    "note": "glab auth — expiry not exposed"}
        rc, out = self._api("/personal_access_tokens/self")
        if rc is None:
            return {"has_token": True, "error": "unreachable"}
        if rc in (401, 403):
            return {"has_token": True, "error": "token rejected"}
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
            # namespaces[0] may be a group, so the repo could be created
            # somewhere the user does not expect. Prefer the namespace whose
            # path matches the account login (the personal namespace).
            rc, out = self._api("/namespaces")
            login = self.user()
            if isinstance(out, list) and out:
                pick = None
                for ns in out:
                    if login and ns.get("path") == login:
                        pick = ns
                        break
                if pick is None:
                    for ns in out:
                        if ns.get("kind") == "user":
                            pick = ns
                            break
                if pick is None:
                    pick = out[0]
                ns_id = str(pick["id"])
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
        self._login_cache = None

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

    def _login(self):
        """the account login, cached: git URLs need a user, and calling
        /user once per repo made a 50-repo sync do 50 extra round-trips."""
        if self._login_cache is None:
            self._login_cache = self.user() or ""
        return self._login_cache

    def _push_url(self, owner, name):
        if self.cfg.get("protocol") == "ssh":
            host = self.url.replace("https://", "").replace("http://", "")
            if ":" in host:                     # strip the HTTP port
                host = host.rsplit(":", 1)[0]
            # the http port (3000/3002...) is NOT the ssh port - use ssh_port
            # from the config, defaulting to standard 22
            port = self.cfg.get("ssh_port") or 22
            return ssh_url(host, owner, name, port)
        # HTTPS must carry credentials, or a private clone/push fails: stdin
        # is /dev/null during a sync, so git cannot prompt. The token is
        # percent-encoded so one containing @ or / cannot break the URL.
        # The scheme follows the configured URL: a LAN Forgejo served over
        # plain http must not be forced to https.
        scheme = "https" if self.url.startswith("https") else "http"
        host = self.url.replace("https://", "").replace("http://", "")
        user = self.cfg.get("user") or self._login() or "git"
        if self.token:
            return "%s://%s:%s@%s/%s/%s.git" % (
                scheme, user, urllib.parse.quote(self.token, safe=""),
                host, owner, name)
        return "%s://%s/%s/%s.git" % (scheme, host, owner, name)

    def list_repos(self):
        repos, page = [], 1
        while True:
            rc, out = self._api("/user/repos?limit=50&page=%d" % page)
            if not isinstance(out, list):
                raise RuntimeError("%s API failed: %s"
                                   % (self.ptype, out))
            for r in out:
                owner = (r.get("owner") or {}).get("login") or ""
                repos.append({"name": r["name"],
                              "id": "%s/%s" % (owner, r["name"])
                              if owner else r["name"],
                              "private": bool(r.get("private")),
                              "fork": bool(r.get("fork")),
                              "archived": bool(r.get("archived")),
                              "clone_url": self._push_url(
                                  owner, r["name"])})
            if len(out) < 50:
                break
            page += 1
        return repos

    def repo_url(self, repo):
        """expected URL without creating anything (read-only probing)"""
        owner = self.owner or self._login()
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
        if not self.token:
            return {"has_token": False, "note": "no token configured"}
        rc, out = self._api("/user")
        if rc is None:
            return {"has_token": True, "error": "unreachable"}
        if rc in (401, 403):
            return {"has_token": True, "error": "token rejected"}
        user = out.get("username") or out.get("login") if isinstance(out, dict) else None
        return {"has_token": True, "expires_at": None,
                "note": "%s tokens do not expire" % self.ptype,
                "user": user}

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

    def delete_repo(self, repo):
        """permanently delete a repository. Opt-in only (see sync.prune_repos):
        no sync path calls this unless the route names this target."""
        owner = self.owner or (self.user() or "")
        rc, out = self._api("/repos/%s/%s" % (owner, repo["name"]), "DELETE")
        if rc in (204, 200):
            return True, ""
        return False, _short_text(out if isinstance(out, str) else
                                  (out or {}).get("message", ""))


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
            if not (entry.endswith(".git") and os.path.isdir(full)):
                continue
            # a bare repository is a repo even with no refs yet: an empty
            # source repo used to be invisible ("src: 2 repos" for 3
            # folders) while mirror_is_valid() already treated it as valid
            rc, out, _ = run(["git", "rev-parse", "--is-bare-repository"],
                             cwd=full, timeout=30)
            if rc == 0 and out.strip() == "true":
                # a folder with no refs is legitimate but usually a
                # placeholder; report it so a route can skip_empty
                rc2, out2, _ = run(["git", "rev-list", "--count", "--all"],
                                   cwd=full, timeout=30)
                empty = rc2 == 0 and out2.strip() in ("", "0")
                repos.append({"name": entry[:-4], "id": entry[:-4],
                              "private": True, "fork": False,
                              "archived": False, "empty": empty,
                              "clone_url": full})
        return repos

    def repo_url(self, repo):
        """expected URL without creating anything (read-only probing)"""
        return os.path.join(self.path, _fs_unique(repo["name"]) + ".git")

    def prepare_push(self, repo, push_url):
        """a bare folder has no branch protection"""
        return []

    def delete_repo(self, repo):
        """remove the bare folder for a repo. Opt-in only (see
        sync.prune_repos) — this is the most destructive of the four."""
        import stat
        path = os.path.join(self.path, _fs_unique(repo["name"]) + ".git")
        if not os.path.isdir(path):
            return True, ""                     # already gone
        def _onerror(func, p, _exc):
            try:
                os.chmod(p, stat.S_IWRITE)
                func(p)
            except Exception:
                pass
        try:
            shutil.rmtree(path, onerror=_onerror)
        except OSError as e:
            return False, str(e)[:120]
        return True, ""

    def token_info(self):
        """local folders need no credentials"""
        return {"has_token": False, "note": "local folder — no credentials"}

    def ensure_repo(self, repo):
        if not self.path:
            raise RuntimeError("local platform has no path configured")
        os.makedirs(self.path, exist_ok=True)
        # _fs_unique, not _fs_safe: "a b" and "a_b" must not land in one repo
        dest = os.path.join(self.path, _fs_unique(repo["name"]) + ".git")
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

    def _git_url(self, owner, slug):
        """authenticated HTTPS URL, or the plain one when no token is set.

        Was `{api-host}/{name}.git` — the API host, no workspace and no
        credentials, so every private clone/push failed.
        """
        host = "bitbucket.org"
        if self.cfg.get("protocol") == "ssh":
            return ssh_url(host, owner, slug)
        if self.token:
            user = self.cfg.get("user") or "x-bitbucket-api-token-auth"
            return "https://%s:%s@%s/%s/%s.git" % (
                user, urllib.parse.quote(self.token, safe=""), host,
                owner, slug)
        return "https://%s/%s/%s.git" % (host, owner, slug)

    def user(self):
        rc, out = self._api("/user")
        if isinstance(out, dict) and out.get("username"):
            return out["username"]
        return None

    def test(self):
        if not self.owner:
            return False, "workspace (owner) is required for Bitbucket"
        rc, out = self._api("/repositories/%s?pagelen=1" % self.owner)
        if isinstance(out, dict) and "values" in out:
            return True, "token OK — workspace %s" % self.owner
        return False, "API failed: %s" % (out.get("message")
                                          if isinstance(out, dict) else out)

    def list_repos(self):
        repos, path = [], "/repositories/%s?pagelen=100" % self.owner
        while path:
            rc, out = self._api(path)
            if not isinstance(out, dict) or "values" not in out:
                raise RuntimeError("Bitbucket API failed: %s" % out)
            for r in out["values"]:
                parent = (r.get("parent") or {}).get("full_name")
                repos.append({"name": r["name"],
                              "id": "%s/%s" % (self.owner, r["name"]),
                              "private": bool(r.get("is_private")),
                              "fork": bool(parent),
                              "clone_url": self._git_url(
                                  self.owner, r.get("slug") or
                                  r["name"].lower().replace(" ", "-")
                                  .replace("_", "-"))})
            nxt = out.get("next")
            path = nxt.replace(self.url + "/2.0", "") if nxt else None
        return repos

    def repo_url(self, repo):
        """expected URL without creating anything (read-only probing)"""
        slug = repo["name"].lower().replace(" ", "-").replace("_", "-")
        return self._git_url(self.owner, slug)

    def prepare_push(self, repo, push_url):
        return []

    def token_info(self):
        """Bitbucket app-password expiry is not exposed by the API"""
        if not self.token:
            return {"has_token": False, "note": "no token configured"}
        rc, out = self._api("/user")
        if rc is None:
            return {"has_token": True, "error": "unreachable"}
        if rc in (401, 403):
            return {"has_token": True, "error": "token rejected"}
        user = out.get("username") if isinstance(out, dict) else None
        return {"has_token": True, "expires_at": None,
                "note": "Bitbucket app-password expiry not exposed",
                "user": user}

    def ensure_repo(self, repo):
        name = repo["name"]
        slug = name.lower().replace(" ", "-").replace("_", "-")
        rc, out = self._api("/repositories/%s/%s" % (self.owner, slug))
        if isinstance(out, dict) and out.get("uuid"):
            return self._git_url(self.owner, slug)
        rc, out = self._api("/repositories/%s/%s" % (self.owner, slug),
                            "POST", {"scm": "git",
                                     "is_private": bool(repo.get("private"))})
        if isinstance(out, dict) and out.get("uuid"):
            return self._git_url(self.owner, slug)
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
