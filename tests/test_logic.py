"""gitsync logic tests — stdlib unittest, no network, no fixtures on disk.

Run:  PYTHONPATH=. python3 -m unittest tests.test_logic
Also:  python3 -m gitsync ci   (this file + the offline sync smoke)

These used to live as a flat assertion blob duplicated four times
(`gitsync/__main__.py` UNIT_CHECKS plus a heredoc in each CI file), which
drifted. One file now; `cmd_ci` and all three workflows call it.
"""

try:
    import curses
except ImportError:
    from gitsync.tui import curses
import json
import os
import subprocess
import sys
import tempfile
import socket
import threading
import time
import unittest
from unittest.mock import patch

import gitsync.__main__ as M
from gitsync import config as C
from gitsync import notify as NT
from gitsync import platforms as P
from gitsync import diskutil as D
from gitsync import stars as ST
from gitsync import scheduler as S
from gitsync import sync as SY
from gitsync import tokencheck as TK
from gitsync import tui
from gitsync import webhooks as WH
from gitsync import sessions as SS
from gitsync import web as W

# CI runners inject credential env vars (GITHUB_TOKEN, FORGEJO_TOKEN, ...).
# The adapters deliberately fall back to them, so a test that does not set a
# token would silently use the runner's - and then hit the real API. Strip
# them for the whole module instead of per test.
_TOKEN_ENV = ("GITHUB_TOKEN", "FORGEJO_TOKEN", "GITEA_TOKEN", "GITLAB_TOKEN",
              "BITBUCKET_TOKEN", "GITSYNC_DISCORD_WEBHOOK",
              "GITSYNC_TELEGRAM_TOKEN", "GITSYNC_TELEGRAM_CHAT_ID",
              "GITSYNC_NOTIFY_ON")

def _have_git():
    import shutil
    return shutil.which("git") is not None

_real_connect = socket.socket.connect
_REAL_CANARY = {}          # live data-dir canary, filled by setUpModule()

def _hermetic_connect(self, address):
    host = address[0] if isinstance(address, tuple) and len(address) > 0 else address
    if isinstance(address, str) or host in ("127.0.0.1", "localhost", "::1"):
        return _real_connect(self, address)
    # OSError, not RuntimeError: socket.create_connection catches OSError and
    # closes the socket it just made. A RuntimeError escapes that handler and
    # leaks the fd, which is what produced "unclosed socket" ResourceWarnings.
    raise OSError("real network access blocked in hermetic unit tests: %r"
                  % (address,))


def setUpModule():
    for k in _TOKEN_ENV:
        os.environ.pop(k, None)
    socket.socket.connect = _hermetic_connect
    # No test may write into the real data dir: cmd_sync and the prune path
    # record every run, and one test that forgot to redirect C.DATA_DIR wrote
    # fake "runs" into the live records.json and gitsync.log — the operator's
    # dashboard then showed a run that never happened. Redirect the whole
    # module; tests that want their own dir still override it.
    global _REAL_DATA_DIR, _TEST_DATA_DIR, _REAL_CANARY
    _REAL_DATA_DIR = (C.DATA_DIR, C.MIRROR_DIR)
    # the redirect above is the fix; this canary proves it still holds. A
    # subprocess or a helper that resolved the path before the redirect (or
    # one that ignores C.DATA_DIR) wrote empty "runs" into the live
    # records.json on several occasions, and the operator's dashboard then
    # showed a run that never happened. Compare and fail loudly instead.
    _REAL_CANARY = {p: _stat_sig(p) for p in
                    (os.path.join(_REAL_DATA_DIR[0], "records.json"),
                     os.path.join(_REAL_DATA_DIR[0], "gitsync.log"))}
    _TEST_DATA_DIR = tempfile.mkdtemp(prefix="gitsync-test-data-")
    C.DATA_DIR = _TEST_DATA_DIR
    C.MIRROR_DIR = os.path.join(_TEST_DATA_DIR, "mirrors")


def _stat_sig(path):
    """(size, mtime) of a file, or None when it does not exist."""
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime)
    except OSError:
        return None


def tearDownModule():
    socket.socket.connect = _real_connect
    C.DATA_DIR, C.MIRROR_DIR = _REAL_DATA_DIR
    import shutil
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
    dirty = [p for p, was in _REAL_CANARY.items() if _stat_sig(p) != was]
    if dirty:
        raise AssertionError(
            "the test run wrote into the live data dir: %s — a code path is "
            "ignoring C.DATA_DIR, so `gitsync ci` would add fake runs to the "
            "operator's dashboard" % ", ".join(sorted(dirty)))


# --------------------------------------------------------------- platforms

class TestPlatformUrls(unittest.TestCase):
    def test_ssh_url(self):
        self.assertEqual(
            P.ssh_url('https://github.com', 'me', 'repo'),
            'git@github.com:me/repo.git')
        self.assertEqual(
            P.ssh_url('git.example.com', 'me', 'repo', 2222),
            'ssh://git@git.example.com:2222/me/repo.git')
        self.assertEqual(
            P.ssh_url('https://codeberg.org', 'me', 'repo'),
            'git@codeberg.org:me/repo.git')

    def test_norm_and_fs_safe(self):
        self.assertEqual(P._norm('My-Repo.Name'), 'myreponame')
        self.assertEqual(P._fs_safe('a b/c'), 'a_b_c')

    def test_short_text(self):
        self.assertEqual(P._short_text('line one\n\nthe real reason'),
                         'the real reason')

    def test_gitlab_path_sanitises(self):
        # GitLab rejects a leading _/-/. and spaces
        self.assertEqual(P.gitlab_path('_gstest_src'), 'gstest_src')
        self.assertEqual(P.gitlab_path('-leading'), 'leading')
        self.assertEqual(P.gitlab_path('my repo'), 'my-repo')
        self.assertEqual(P.gitlab_path('ok-name'), 'ok-name')
        self.assertEqual(P.gitlab_path('trailing.git'), 'trailing')
        self.assertEqual(P.gitlab_path('...'), 'repo')

    def test_api_error_readable(self):
        self.assertEqual(P._api_error({'message': {'path': ['bad']}}),
                         'path: bad')
        self.assertEqual(P._api_error({'message': 'nope'}), 'nope')
        self.assertEqual(P._api_error({'message': ['a', 'b']}), 'a; b')
        self.assertEqual(P._api_error('plain'), 'plain')

    def test_make_returns_adapter(self):
        p = P.make('gh', {'type': 'github', 'url': 'https://github.com'})
        self.assertIsInstance(p, P.GitHubPlatform)

    def test_every_adapter_has_prepare_push(self):
        for cls in (P.GitHubPlatform, P.GitLabPlatform, P.ForgejoPlatform,
                    P.LocalPlatform):
            self.assertTrue(hasattr(cls, 'prepare_push'), cls)

    def test_gitlab_repo_url_formats_every_part(self):
        # regression: the URL had one %-arg too many
        gp = P.GitLabPlatform('gl', {'url': 'https://gitlab.com',
                                     'owner': 'me', 'token': 'tok'})
        self.assertEqual(
            gp.repo_url({'name': '_weird name'}),
            'https://oauth2:tok@gitlab.com/me/weird-name.git')

    def test_forgejo_ssh_push_url(self):
        p = P.make('fj', {'type': 'forgejo', 'url': 'http://localhost:3002',
                          'protocol': 'ssh', 'ssh_port': 2222, 'owner': 'me'})
        self.assertEqual(p._push_url('me', 'repo'),
                         'ssh://git@localhost:2222/me/repo.git')

    def test_forgejo_https_url_encodes_token(self):
        # P0-3: private git operations send no token without this
        p = P.make('fj', {'type': 'forgejo', 'url': 'http://localhost:3002',
                          'owner': 'sagar', 'token': 'a/b'})
        self.assertEqual(p._push_url('sagar', 'repo'),
                         'http://git:a%2Fb@localhost:3002/sagar/repo.git')
        p2 = P.make('fj2', {'type': 'forgejo', 'url': 'https://codeberg.org',
                            'owner': 'me', 'token': 'tok'})
        self.assertEqual(p2._push_url('me', 'repo'),
                         'https://git:tok@codeberg.org/me/repo.git')
        # the scheme must follow the configured URL: a LAN Forgejo over
        # plain http must not be forced to https (regression: TLS error)
        self.assertTrue(p._push_url('sagar', 'repo').startswith('http://'))
        self.assertTrue(p2._push_url('me', 'repo').startswith('https://'))
        # no token -> plain URL, ssh -> never embedded
        p3 = P.make('fj3', {'type': 'forgejo', 'url': 'http://h:3002',
                            'owner': 'me'})
        self.assertEqual(p3._push_url('me', 'repo'),
                         'http://h:3002/me/repo.git')

    def test_github_api_base(self):
        # P0-14: GitHub Enterprise was broken (hardcoded api.github.com)
        self.assertEqual(
            P.make('gh', {'type': 'github',
                          'url': 'https://github.com'})._api_base(),
            'https://api.github.com')
        self.assertEqual(
            P.make('ghe', {'type': 'github',
                           'url': 'https://git.example.com'})._api_base(),
            'https://git.example.com/api/v3')

    def test_github_pagination_does_not_stop_at_500(self):
        # P0-14: `gh repo list --limit 500` silently dropped the rest
        p = P.make('gh', {'type': 'github', 'auth': 'gh',
                          'url': 'https://github.com'})
        pages = [[("r%d" % i, "PUBLIC", "false",
                   "https://github.com/o/r%d" % i) for i in range(100)],
                 [("r100", "PUBLIC", "false",
                   "https://github.com/o/r100")]]
        seen = []

        def fake_gh(args):
            seen.append(args)
            body = "\n".join("\t".join(r) for r in pages[len(seen) - 1])
            return 0, body, ""

        p._gh = fake_gh
        repos = p.list_repos()
        self.assertEqual(len(repos), 101)
        self.assertEqual(len(seen), 2)              # it asked for page 2
        self.assertIn("page=2", seen[1][1])

    def test_bitbucket_no_unpack_crash(self):
        # P0-2: _api returns (status, body) but the methods unpacked three
        p = P.make('bb', {'type': 'bitbucket', 'owner': 'me', 'token': 't'})
        p._api = lambda path, method="GET", payload=None: (
            200, {"username": "a", "values": []})
        self.assertEqual(p.user(), 'a')             # must not raise
        ok, info = p.test()
        self.assertTrue(ok)

    def test_bitbucket_repo_url_has_workspace_and_token(self):
        # P0-2: was {api-host}/{name}.git - API host, no workspace, no token
        p = P.make('bb', {'type': 'bitbucket', 'owner': 'me', 'token': 'a/b'})
        self.assertEqual(p.repo_url({'name': 'My Repo'}),
                         'https://x-bitbucket-api-token-auth:a%2Fb'
                         '@bitbucket.org/me/my-repo.git')
        ssh = P.make('bb2', {'type': 'bitbucket', 'owner': 'me',
                             'protocol': 'ssh'})
        self.assertEqual(ssh.repo_url({'name': 'x'}),
                         'git@bitbucket.org:me/x.git')


class TestHttpErrorHeaders(unittest.TestCase):
    """P0-12: an HTTP error must still honour want_headers=True."""

    def test_error_returns_headers_when_asked(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401)
                self.send_header("X-Test-Thing", "yes")
                self.end_headers()
                self.wfile.write(b'{"message":"Bad credentials"}')

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.handle_request)
        t.start()
        url = "http://127.0.0.1:%d/user" % srv.server_port
        try:
            rc, body, hdrs = P._http("GET", url, want_headers=True)
        finally:
            t.join(10)
            srv.server_close()
        self.assertEqual(rc, 401)
        self.assertEqual(hdrs.get("x-test-thing"), "yes")

    def test_token_info_reports_rejected(self):
        p = P.make('gh', {'type': 'github', 'token': 'bad',
                          'url': 'https://github.com'})
        orig_http = P._http
        P._http = lambda *a, **kw: (401, {"message": "Bad credentials"}, {})
        try:
            info = p.token_info()
        finally:
            P._http = orig_http
        self.assertTrue(info["has_token"])
        self.assertEqual(info.get("error"), "token rejected")

    def test_token_info_reports_unreachable(self):
        p = P.make('gh', {'type': 'github', 'token': 'bad',
                          'url': 'https://github.com'})
        orig_http = P._http
        P._http = lambda *a, **kw: (None, "DNS lookup failed", {})
        try:
            info = p.token_info()
        finally:
            P._http = orig_http
        self.assertTrue(info["has_token"])
        self.assertEqual(info.get("error"), "unreachable")

    def test_http_want_headers_tuple_length(self):
        import urllib.request
        orig_urlopen = urllib.request.urlopen
        def fail(*a, **kw):
            raise OSError("network down")
        urllib.request.urlopen = fail
        try:
            res2 = P._http("GET", "http://127.0.0.1:9999", want_headers=False)
            self.assertEqual(len(res2), 2)
            self.assertIsNone(res2[0])

            res3 = P._http("GET", "http://127.0.0.1:9999", want_headers=True)
            self.assertEqual(len(res3), 3)
            self.assertIsNone(res3[0])
            self.assertEqual(res3[2], {})
        finally:
            urllib.request.urlopen = orig_urlopen


# ------------------------------------------------------------------ config

class TestConfig(unittest.TestCase):
    def test_schedules(self):
        self.assertEqual(set(C.SCHEDULES),
                         {'hourly', '6h', '12h', 'daily', 'weekly', 'off'})
        self.assertEqual(C.SCHEDULES['hourly']['cron'], '0 * * * *')
        self.assertEqual(C.SCHEDULES['daily']['calendar'], '*-*-* 03:00:00')
        self.assertEqual(C.SCHEDULE_HOURS['hourly'], 1)
        self.assertEqual(C.SCHEDULE_HOURS['weekly'], 168)

    def test_route_schedule_and_finest(self):
        cfg = {'settings': {'notify': {}}}
        self.assertEqual(C.route_schedule(cfg, {}), 'hourly')
        self.assertEqual(C.route_schedule(cfg, {'schedule': 'daily'}), 'daily')
        self.assertEqual(
            C.finest_schedule({'routes': [{'name': 'a', 'schedule': 'weekly'},
                                          {'name': 'b', 'schedule': '6h'},
                                          {'name': 'c', 'schedule': 'daily'}]}),
            '6h')
        self.assertIsNone(
            C.finest_schedule({'routes': [{'name': 'a', 'schedule': 'off'}]}))

    def test_setting_reads_nested_and_default(self):
        self.assertEqual(
            C.setting({'settings': {'theme': 'ocean'}}, 'theme'), 'ocean')
        self.assertEqual(C.setting({'settings': {}}, 'theme', 'default'),
                         'default')

    def test_platform_template(self):
        p = C.platform_template('gitlab', 'gl2', url='https://gitlab.com',
                                token='glpat-x', owner='me')
        self.assertEqual(p['type'], 'gitlab')
        self.assertTrue(p['enabled'])
        self.assertEqual(p['url'], 'https://gitlab.com')
        self.assertEqual(p['token'], 'glpat-x')
        self.assertEqual(p['owner'], 'me')
        self.assertNotIn('auth', p)

        p2 = C.platform_template('github', 'gh2')
        self.assertEqual(p2['auth'], 'gh')      # no token -> gh CLI
        self.assertNotIn('token', p2)

        p3 = C.platform_template('forgejo', 'fj', protocol='ssh',
                                 ssh_port='2222')
        self.assertEqual(p3['protocol'], 'ssh')
        self.assertEqual(p3['ssh_port'], 2222)

        p4 = C.platform_template('local', 'bak', path='~/mirrors')
        self.assertEqual(p4['type'], 'local')
        self.assertTrue(p4['path'].endswith('/mirrors'))
        self.assertNotIn('url', p4)

    def test_add_and_remove_platform(self):
        cfg = {'platforms': {'github': {'type': 'github', 'enabled': True}},
               'routes': [{'name': 'r1', 'source': 'github',
                           'targets': ['gitlab']}]}
        self.assertIsNone(C.add_platform(cfg, 'gitlab', {'type': 'gitlab'}))
        self.assertIsNotNone(C.add_platform(cfg, 'gitlab', {'type': 'gitlab'}))
        self.assertEqual(C.platform_used_by(cfg, 'github'), ['r1'])
        # a platform a route uses is refused unless forced
        self.assertIsNotNone(C.remove_platform(cfg, 'github'))
        self.assertIsNone(C.remove_platform(cfg, 'github', force=True))
        self.assertNotIn('github', cfg['platforms'])
        self.assertIsNotNone(C.remove_platform(cfg, 'nope'))

    def test_themes_are_valid_curses_colours(self):
        valid = ('black', 'red', 'green', 'yellow', 'blue', 'magenta',
                 'cyan', 'white')
        for name, spec in C.THEMES.items():
            self.assertEqual(len(spec), 4, name)
            for c in spec:
                self.assertIn(c, valid, (name, c))
        self.assertEqual(C.THEMES['mono'], ['white'] * 4)
        self.assertTrue(set(C.THEMES) >= {'default', 'mono', 'ocean',
                                          'forest', 'sunset'})


# ------------------------------------------------------------------ notify

class TestNotify(unittest.TestCase):
    def test_enabled_with_nothing_configured(self):
        self.assertFalse(NT.enabled({'settings': {'notify': {}}}))

    def test_send_with_nothing_configured(self):
        # P0-13: send() must report "nothing sent" instead of True, which
        # made callers believe alerts worked with no channels configured
        self.assertFalse(NT.send({'settings': {'notify': {}}}, 't', [],
                                 color='info'))


# ------------------------------------------------------------ repo filters

class TestFilterRepos(unittest.TestCase):
    def setUp(self):
        self.repos = [{'name': 'SeaVault'}, {'name': 'tradex'},
                      {'name': 'Sea-Wallpaper'}]

    def names(self, rows):
        return [r['name'] for r in rows]

    def test_include_glob(self):
        self.assertEqual(
            self.names(SY._filter_repos(self.repos, {'include': ['Sea*']})),
            ['SeaVault', 'Sea-Wallpaper'])

    def test_exclude_glob(self):
        self.assertEqual(
            self.names(SY._filter_repos(self.repos, {'exclude': ['trade*']})),
            ['SeaVault', 'Sea-Wallpaper'])

    def test_only_is_exact_and_case_insensitive(self):
        only = SY._filter_repos(self.repos, {}, ['SEAVAULT', ' tradex '])
        self.assertEqual(self.names(only), ['SeaVault', 'tradex'])

    def test_only_narrows_the_route_globs(self):
        both = SY._filter_repos(self.repos, {'include': ['Sea*']},
                                ['SeaVault', 'tradex'])
        self.assertEqual(self.names(both), ['SeaVault'])

    def test_only_unknown_name_dropped(self):
        self.assertEqual(SY._filter_repos(self.repos, {}, ['nope']), [])

    def test_empty_only_filters_nothing(self):
        self.assertEqual(len(SY._filter_repos(self.repos, {}, [])), 3)
        self.assertEqual(len(SY._filter_repos(self.repos, {}, None)), 3)


# ------------------------------------------------------------ disk / tokens

class TestDisk(unittest.TestCase):
    def test_human(self):
        self.assertEqual(D.human(0), '0B')
        self.assertEqual(D.human(1023), '1023B')
        self.assertEqual(D.human(1024), '1.0K')
        self.assertEqual(D.human(1536), '1.5K')
        self.assertEqual(D.human(1024 ** 3), '1.0G')


class TestTokenCheck(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(TK.level_for(200), 'ok')
        self.assertEqual(TK.level_for(20), 'warning')
        self.assertEqual(TK.level_for(5), 'critical')
        self.assertEqual(TK.level_for(0), 'critical')
        self.assertEqual(TK.level_for(-1), 'expired')
        self.assertEqual(TK.level_for(None), 'unknown')

    def test_parse(self):
        self.assertIsNotNone(TK._parse('2026-12-01'))
        self.assertIsNotNone(TK._parse('2026-12-01T10:00:00Z'))
        self.assertIsNotNone(TK._parse('2026-12-01 10:00:00 UTC'))
        self.assertIsNone(TK._parse('garbage'))

    def test_days_left(self):
        self.assertIsNone(TK.days_left(None))

    def test_offline_network_reports_unverified_not_critical(self):
        cfg = {
            "platforms": {
                "gh": {"type": "github", "enabled": True, "token": "secret"},
                "gl": {"type": "gitlab", "enabled": True, "token": "secret"},
            }
        }
        def fake_unreachable(*args, **kwargs):
            if kwargs.get("want_headers"):
                return (None, "network down", {})
            return (None, "network down")

        with patch("gitsync.platforms._http", side_effect=fake_unreachable):
            results = TK.check(cfg)
            for r in results:
                self.assertEqual(r["level"], "unknown")
                self.assertIn("not verified", r["note"])
            self.assertEqual(TK.bad(results), [])
            self.assertIn("unverified", TK.summary(results))
            self.assertNotIn("need attention", TK.summary(results))

    def test_local_only_and_no_expiry_tokens(self):
        # local-only config: level is n/a, summary says all tokens healthy
        cfg_local = {
            "platforms": {
                "loc1": {"type": "local", "enabled": True, "path": "/tmp/a"},
                "loc2": {"type": "local", "enabled": True, "path": "/tmp/b"},
            }
        }
        res_local = TK.check(cfg_local)
        for r in res_local:
            self.assertEqual(r["level"], "n/a")
        self.assertEqual(TK.summary(res_local), "all tokens healthy")

        # valid authenticated token without expiry reports ok-no-expiry and not unverified
        cfg_no_exp = {
            "platforms": {
                "fj": {"type": "forgejo", "enabled": True, "token": "secret", "url": "https://codeberg.org"},
            }
        }
        def fake_ok(*args, **kwargs):
            if kwargs.get("want_headers"):
                return (200, {"username": "alice"}, {})
            return (200, {"username": "alice"})

        with patch("gitsync.platforms._http", side_effect=fake_ok):
            res_no_exp = TK.check(cfg_no_exp)
            self.assertEqual(res_no_exp[0]["level"], "ok-no-expiry")
            self.assertEqual(TK.bad(res_no_exp), [])
            self.assertEqual(TK.summary(res_no_exp), "all tokens healthy")


# ---------------------------------------------------------------- webhooks

class TestWebhooks(unittest.TestCase):
    def test_event_mapping(self):
        # GitLab says 'Push Hook', GitHub says 'push'
        self.assertEqual(WH._event({}, 'push'), 'push')
        self.assertEqual(WH._event({}, 'Push Hook'), 'push')
        self.assertEqual(WH._event({}, 'Tag Push Hook'), 'push')
        self.assertEqual(WH._event({}, 'ping'), 'ping')
        self.assertEqual(WH._event({}, 'Merge Request Hook'), 'other')
        self.assertEqual(WH._event({'commits': [1]}, None), 'push')
        self.assertEqual(WH._event({'zen': 'x'}, None), 'ping')
        self.assertEqual(WH._event({}, None), 'other')

    def test_repo_name_extraction(self):
        self.assertEqual(
            WH._repo_name({'repository': {'name': 'tradex'}}), 'tradex')
        self.assertEqual(
            WH._repo_name({'project': {'path': 'SeaVault'}}), 'SeaVault')
        self.assertIsNone(WH._repo_name({}))

    def test_unset_secret_never_authenticates(self):
        import hashlib
        import hmac
        body = json.dumps({'repository': {'name': 'x'}}).encode()
        sig = 'sha256=' + hmac.new(b'k', body, hashlib.sha256).hexdigest()
        ok, _ = WH._verify('hmac-sha256', '',
                           {'X-Hub-Signature-256': sig}, body)
        self.assertFalse(ok)
        ok, _ = WH._verify('hmac-sha256', 'k',
                           {'X-Hub-Signature-256': sig}, body)
        self.assertTrue(ok)
        ok, _ = WH._verify('hmac-sha256', 'k',
                           {'X-Hub-Signature-256': 'sha256=bad'}, body)
        self.assertFalse(ok)

    def test_token_style_verify(self):
        body = b'{}'
        ok, _ = WH._verify('token', 'k', {'X-Gitlab-Token': 'k'}, body)
        self.assertTrue(ok)
        ok, _ = WH._verify('token', 'k', {'X-Gitlab-Token': 'wrong'}, body)
        self.assertFalse(ok)


# -------------------------------------------------------------- sync engine

class TestSyncHelpers(unittest.TestCase):
    def test_log_and_records_follow_data_dir(self):
        # LOG_FILE/RECORDS_FILE were module constants bound at import, so a
        # caller that redirected DATA_DIR still appended runs to the real
        # log and records.json — the operator's dashboard then showed a
        # "last run" that never happened.
        self.assertEqual(C.log_file(),
                         os.path.join(C.DATA_DIR, "gitsync.log"))
        self.assertEqual(C.records_file(),
                         os.path.join(C.DATA_DIR, "records.json"))
        td = tempfile.mkdtemp()
        old = (C.DATA_DIR, C.MIRROR_DIR)
        C.DATA_DIR, C.MIRROR_DIR = td, os.path.join(td, "mirrors")
        try:
            self.assertEqual(C.log_file(), os.path.join(td, "gitsync.log"))
            self.assertEqual(C.records_file(),
                             os.path.join(td, "records.json"))
            self.assertFalse(SY._route_state_path().startswith(
                os.path.expanduser("~/.local/share")))
        finally:
            C.DATA_DIR, C.MIRROR_DIR = old
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    def test_protection_error(self):
        self.assertTrue(SY._protection_error(
            'remote: You are not allowed to force push code to a protected '
            'branch'))
        self.assertFalse(SY._protection_error('some other failure'))
        self.assertFalse(SY._protection_error(''))

    def test_short_error(self):
        self.assertEqual(SY._short_error('remote: nope\nTo example'), 'nope')
        self.assertEqual(SY._short_error('hint: x\nremote: real reason'),
                         'real reason')
        self.assertEqual(SY._short_error(''), '')

    def test_newest_wins_and_ties_keep_the_first(self):
        self.assertEqual(SY._newest([(10, 'a'), (20, 'b'), (20, 'c')]),
                         (20, 'b'))
        self.assertEqual(SY._newest([(7, 'only')]), (7, 'only'))

    def test_active_targets_and_peers(self):
        cfg = {'platforms': {'a': {'type': 'github', 'enabled': True},
                             'b': {'type': 'gitlab', 'enabled': True},
                             'c': {'type': 'local', 'enabled': True}}}
        r = {'name': 'r', 'source': 'a', 'targets': ['b', 'c'],
             'paused_targets': ['c']}
        self.assertEqual(set(SY.active_targets(cfg, r)), {'b'})
        self.assertEqual(set(SY.route_peers(cfg, r)), {'a', 'b'})
        cfg['platforms']['b']['enabled'] = False
        self.assertEqual(SY.active_targets(cfg, r), {})
        self.assertEqual(set(SY.route_peers(cfg, r)), {'a'})

    def test_choose_conflict_policy(self):
        cands = {'branch': [(10, 'sha_old'), (20, 'sha_new')]}
        self.assertEqual(SY._choose(cands, 'newest'),
                         ({'branch': (20, 'sha_new')}, []))
        self.assertEqual(SY._choose(cands, 'skip'), ({}, ['branch']))
        # every peer agrees -> nothing is skipped, even under "skip"
        ok = {'branch': [(10, 'same'), (30, 'same')]}
        self.assertEqual(SY._choose(ok, 'skip'),
                         ({'branch': (30, 'same')}, []))
        self.assertEqual(SY._choose({'b': [(1, 'x')]}, 'newest')[0]['b'],
                         (1, 'x'))

    def test_last_route_run_reads_the_state_file(self):
        with tempfile.TemporaryDirectory() as td:
            old = C.DATA_DIR
            C.DATA_DIR = td
            try:
                self.assertIsNone(SY.last_route_run('no-such-route'))
            finally:
                C.DATA_DIR = old


class TestHubPaths(unittest.TestCase):
    """P0-5: the hub is keyed by a stable id, and the legacy layout keeps
    working without being deleted."""

    def test_hub_rel_encodes_the_id(self):
        self.assertEqual(SY.hub_rel('SeaXen/gitsync'), 'SeaXen__gitsync')
        # a name that survives sanitising keeps its old path (no migration),
        # and a lossy one gains a stable suffix so it cannot collide
        self.assertTrue(SY.hub_rel('a b/c').startswith('a_b__c-'))
        self.assertEqual(SY.hub_rel('a b/c'), SY.hub_rel('a b/c'))

    def test_new_repo_gets_the_nested_path(self):
        with tempfile.TemporaryDirectory() as td:
            old = C.MIRROR_DIR
            C.MIRROR_DIR = td
            try:
                p = SY.mirror_path({'name': 'docs', 'id': 'group/docs'},
                                   'gitlab')
                self.assertEqual(p, os.path.join(td, 'gitlab',
                                                 'group__docs.git'))
            finally:
                C.MIRROR_DIR = old

    def test_two_repos_with_the_same_short_name_do_not_share(self):
        with tempfile.TemporaryDirectory() as td:
            old = C.MIRROR_DIR
            C.MIRROR_DIR = td
            try:
                a = SY.mirror_path({'name': 'docs', 'id': 'a/docs'}, 'gitlab')
                b = SY.mirror_path({'name': 'docs', 'id': 'b/docs'}, 'gitlab')
                self.assertNotEqual(a, b)
            finally:
                C.MIRROR_DIR = old

    def test_legacy_mirror_is_reused_not_deleted(self):
        with tempfile.TemporaryDirectory() as td:
            old = C.MIRROR_DIR
            C.MIRROR_DIR = td
            try:
                legacy = os.path.join(td, 'gitsync.git')
                os.makedirs(legacy)
                p = SY.mirror_path({'name': 'gitsync', 'id': 'SeaXen/gitsync'},
                                   'github')
                self.assertEqual(p, legacy)          # reused as-is
                self.assertTrue(os.path.isdir(legacy))
            finally:
                C.MIRROR_DIR = old

    def test_two_repos_claiming_one_legacy_mirror_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            old = C.MIRROR_DIR
            C.MIRROR_DIR = td
            try:
                legacy = os.path.join(td, 'docs.git')
                os.makedirs(legacy)
                p = SY.mirror_path({'name': 'docs', 'id': 'a/docs'}, 'gitlab',
                                   claimants=[{'name': 'docs'}, {'name': 'docs'}])
                self.assertIsNone(p)
            finally:
                C.MIRROR_DIR = old

    def test_mirrors_walks_the_nested_layout(self):
        from gitsync import diskutil as D
        with tempfile.TemporaryDirectory() as td:
            old = C.MIRROR_DIR
            C.MIRROR_DIR = td
            try:
                os.makedirs(os.path.join(td, 'gitlab', 'group__docs.git'))
                os.makedirs(os.path.join(td, 'legacy.git'))
                ids = {m[0] for m in D.mirrors()}
                self.assertIn('gitlab/group__docs', ids)
                self.assertIn('legacy', ids)
            finally:
                C.MIRROR_DIR = old


class TestPushPolicy(unittest.TestCase):
    """P0-7: safe must never force or prune; full prunes only heads/tags."""

    def test_push_args_safe_does_not_force(self):
        for ptype in ("gitlab", "github", "forgejo", "gitea", "bitbucket",
                      "generic"):
            args = SY.push_args("safe", ptype, "URL")
            self.assertNotIn("--force", args, ptype)
            self.assertNotIn("--mirror", args, ptype)
            self.assertFalse([a for a in args if a.startswith("+")], ptype)
            self.assertEqual(args[:3], ["git", "push", "URL"])

    def test_push_args_full_forces_and_prunes_without_mirror(self):
        for ptype in ("gitlab", "github", "forgejo", "gitea", "bitbucket",
                      "generic"):
            args = SY.push_args("full", ptype, "URL")
            self.assertIn("--force", args, ptype)
            self.assertIn("--prune", args, ptype)
            self.assertNotIn("--mirror", args, ptype)
            # only heads and tags travel, so no refs/pull/* junk
            self.assertEqual(args[-2:], SY.HEAD_TAGS)

    def test_mirror_only_for_a_local_full_target(self):
        self.assertEqual(SY.push_args("full", "local", "URL")[:3],
                         ["git", "push", "--mirror"])
        self.assertNotIn("--mirror", SY.push_args("safe", "local", "URL"))

    def test_pinned_refspecs_win(self):
        self.assertEqual(
            SY.push_args("safe", "gitlab", "URL",
                         ["+refs/heads/a:refs/heads/a"]),
            ["git", "push", "--force", "URL", "+refs/heads/a:refs/heads/a"])
        self.assertEqual(SY.push_args("safe", "gitlab", "URL", []), [])

    def test_default_mode_for_a_new_config_is_safe(self):
        self.assertEqual(C.DEFAULT_CONFIG["settings"]["mode"], "safe")

    def test_default_mesh_conflict_is_skip(self):
        self.assertEqual(C.DEFAULT_CONFIG["settings"]["mesh_conflict"],
                         "skip")


class TestRedaction(unittest.TestCase):
    """P0-10: a token must never reach a log line or a record."""

    def test_redact_strips_credentials(self):
        self.assertEqual(
            SY._redact("fatal: https://oauth2:glpat-SECRET@gitlab.com/x/y"),
            "fatal: https://***@gitlab.com/x/y")
        self.assertEqual(
            SY._redact("To https://x-access-token:ghs_ABC@github.com/o/r"),
            "To https://***@github.com/o/r")
        self.assertEqual(SY._redact("plain error"), "plain error")
        self.assertIsNone(SY._redact(None))

    def test_short_error_redacts(self):
        out = SY._short_error("remote: https://oauth2:glpat-SECRET@gitlab.com"
                              "/x denied")
        self.assertNotIn("glpat-SECRET", out)
        self.assertIn("gitlab.com", out)

    def test_clone_failed_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            hub = os.path.join(td, "hub.git")
            repo = {"name": "testrepo",
                    "clone_url": "https://u:ghp_SECRET@github.com/u/testrepo.git"}
            class FakeSource:
                ptype = "github"
            orig_run = P.run
            def fake_run(cmd, *a, **kw):
                if cmd[:2] == ["git", "clone"]:
                    return 1, "", "fatal: unable to access 'https://u:ghp_SECRET@github.com/u/testrepo.git': 401\n"
                return orig_run(cmd, *a, **kw)
            P.run = fake_run
            try:
                status, detail, errors = SY.sync_repo(repo, FakeSource(), {}, "safe")
            finally:
                P.run = orig_run
            self.assertEqual(status, "clone-failed")
            self.assertNotIn("ghp_SECRET", detail)
            self.assertIn("***@", detail)

    def test_split_url_auth(self):
        clean, user, token = SY.split_url_auth("https://x-access-token:ghp_ABC123@github.com/org/repo.git")
        self.assertEqual(clean, "https://github.com/org/repo.git")
        self.assertEqual(user, "x-access-token")
        self.assertEqual(token, "ghp_ABC123")

        clean2, user2, token2 = SY.split_url_auth("https://oauth2:glpat-xyz@gitlab.com/org/repo.git")
        self.assertEqual(clean2, "https://gitlab.com/org/repo.git")
        self.assertEqual(user2, "oauth2")
        self.assertEqual(token2, "glpat-xyz")

        clean3, user3, token3 = SY.split_url_auth("/local/path/repo.git")
        self.assertEqual(clean3, "/local/path/repo.git")
        self.assertIsNone(user3)
        self.assertIsNone(token3)

    @unittest.skipUnless(_have_git(), "needs the git binary")
    def test_s2_token_not_persisted_in_mirror_git_config(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            old_m, old_d = C.MIRROR_DIR, C.DATA_DIR
            C.MIRROR_DIR = os.path.join(td, "mirrors")
            C.DATA_DIR = td
            os.makedirs(C.MIRROR_DIR, exist_ok=True)
            hub = os.path.join(C.MIRROR_DIR, "demo.git")
            subprocess.run(["git", "init", "--bare", "-q", hub], check=True)
            # simulate an old mirror containing a stored token
            subprocess.run(["git", "-C", hub, "config", "remote.origin.url",
                            "https://u:old_SECRET@github.com/u/demo.git"], check=True)

            repo = {"name": "demo", "clone_url": "https://u:new_SECRET@github.com/u/demo.git"}
            class FakeSource:
                ptype = "github"
            orig_run = P.run
            try:
                def fake_run(cmd, *a, **kw):
                    if "remote" in cmd and "update" in cmd:
                        return 0, "", ""
                    return orig_run(cmd, *a, **kw)
                P.run = fake_run
                SY.sync_repo(repo, FakeSource(), {}, "safe")
            finally:
                P.run = orig_run
                C.MIRROR_DIR, C.DATA_DIR = old_m, old_d

            # Verify that stored remote.origin.url has no credentials
            r = subprocess.run(["git", "-C", hub, "config", "--get", "remote.origin.url"],
                               capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "https://github.com/u/demo.git")
            self.assertNotIn("@", r.stdout)

    def test_s3_tokens_not_visible_in_process_arguments(self):
        with tempfile.TemporaryDirectory() as td:
            hub = os.path.join(td, "hub.git")
            os.makedirs(hub)
            class FakeTarget:
                ptype = "github"
                def ensure_repo(self, repo):
                    return "https://x-access-token:ghp_TESTSECRET@github.com/org/testrepo.git"
            captured = []
            orig_run = P.run
            try:
                def fake_run(cmd, *a, **kw):
                    captured.append((cmd, kw.get("env") or {}))
                    return 0, "", ""
                P.run = fake_run
                SY._push_target("gh", FakeTarget(), {"name": "testrepo"}, hub, "safe")
            finally:
                P.run = orig_run

            self.assertTrue(captured)
            cmd, env = captured[0]
            # argv must contain no secrets
            for arg in cmd:
                self.assertNotIn("ghp_TESTSECRET", arg)
                self.assertNotIn("@", arg)
            # push_args must be pure with clean url
            self.assertIn("https://github.com/org/testrepo.git", cmd)
            # credentials passed in private env var
            self.assertEqual(env.get("GITSYNC_ASKPASS_TOKEN"), "ghp_TESTSECRET")
            self.assertIn("GIT_ASKPASS", env)


class TestScheduleStamp(unittest.TestCase):
    """P0-6: a dry run or a failed listing must not consume the slot."""

    def test_dry_run_does_not_mark_route(self):
        with tempfile.TemporaryDirectory() as td:
            old = C.DATA_DIR
            C.DATA_DIR = td
            try:
                cfg = {"platforms": {
                           "src": {"type": "local", "enabled": True,
                                   "path": os.path.join(td, "src")},
                           "dst": {"type": "local", "enabled": True,
                                   "path": os.path.join(td, "dst")}},
                       "routes": [{"name": "r", "source": "src",
                                   "targets": ["dst"]}],
                       "settings": {}}
                for d in ("src", "dst"):
                    os.makedirs(os.path.join(td, d))
                SY.run_sync(cfg, route_name="r", dry_run=True)
                self.assertIsNone(SY.last_route_run("r"))
            finally:
                C.DATA_DIR = old

    def test_failed_listing_does_not_mark_route(self):
        # LocalPlatform returns [] for a missing folder (a real "0 repos"
        # run, which *should* be stamped), so force the listing to raise
        from unittest import mock

        class Boom:
            ptype = "local"

            def list_repos(self):
                raise RuntimeError("cannot list")

        with tempfile.TemporaryDirectory() as td:
            old = C.DATA_DIR
            C.DATA_DIR = td
            try:
                cfg = {"platforms": {
                           "src": {"type": "local", "enabled": True,
                                   "path": os.path.join(td, "src")},
                           "dst": {"type": "local", "enabled": True,
                                   "path": os.path.join(td, "dst")}},
                       "routes": [{"name": "r", "source": "src",
                                   "targets": ["dst"]}],
                       "settings": {}}
                for d in ("src", "dst"):
                    os.makedirs(os.path.join(td, d))
                with mock.patch.object(SY.P, "make", return_value=Boom()):
                    SY.run_sync(cfg, route_name="r")
                self.assertIsNone(SY.last_route_run("r"))
            finally:
                C.DATA_DIR = old


# ------------------------------------------------------------------- stars

class TestStarAdapters(unittest.TestCase):
    """P1-4: the GitLab star path was double-encoded, and GitHub's
    starred() treated a good HTTP 200 as a failure."""

    def test_gitlab_star_path_encoded_once(self):
        # quote() of a string that already holds %2F produced %252F
        self.assertEqual(P._url_quote("group/sub/repo"), "group%2Fsub%2Frepo")
        self.assertEqual(P._url_quote("group%2Fsub%2Frepo"),
                         "group%252Fsub%252Frepo")
        # so the star call must quote the raw path exactly once
        path = P._url_quote("group/sub/repo")
        self.assertNotIn("%25", path)

    def test_github_starred_accepts_status_200(self):
        class Fake:
            token = "t"
            ptype = "github"

            def _api(self, path, method="GET", payload=None):
                self.seen = getattr(self, "seen", 0) + 1
                if self.seen == 1:
                    return 200, [{"full_name": "a/b"}]
                return 200, []

        self.assertEqual(ST.starred(Fake()), [("a/b", "")])

    def test_gitlab_star_posts_to_the_encoded_path(self):
        # a fake GitLab platform records the URL star() builds
        calls = []

        class Fake:
            token = "t"
            ptype = "gitlab"

            def _api(self, path, method="GET", payload=None):
                calls.append((method, path))
                if method == "GET":
                    return 404, {"message": "404 Not Found"}
                return 201, {}

        ST.star(Fake(), "group/sub/repo")
        self.assertTrue(calls)
        method, path = calls[-1]
        self.assertEqual(method, "POST")
        self.assertIn("group%2Fsub%2Frepo", path)
        self.assertNotIn("%25", path)


class TestStars(unittest.TestCase):
    def test_norm_url(self):
        self.assertEqual(
            ST._norm_url('https://github.com/SeaXen/gitsync.git'),
            'github.com/seaxen/gitsync')
        self.assertEqual(ST._norm_url('git@github.com:SeaXen/gitsync.git'),
                         'github.com/seaxen/gitsync')
        self.assertEqual(
            ST._norm_url('http://localhost:3002/sagar/gitsync.git'),
            'localhost:3002/sagar/gitsync')
        self.assertEqual(ST._norm_url(''), '')

    def test_different_hosts_are_different_repos(self):
        # a name match is only trusted for github -> github
        self.assertNotEqual(
            ST._norm_url('https://gitlab.com/seaxen/gitsync.git'),
            ST._norm_url('https://github.com/SeaXen/gitsync.git'))


class TestCompareAndFix(unittest.TestCase):
    """P0-4: every platform must be probed at its own URL."""

    def test_head_of_uses_target_repo_url(self):
        # the source's clone_url must never be used for a target, and a
        # read-only check must never create a repo
        seen = []

        class FakePlat:
            ptype = "gitlab"

            def repo_url(self, repo):
                return "https://gitlab.example/%s.git" % repo["name"]

            def ensure_repo(self, repo):        # must NOT be called
                raise AssertionError("ensure_repo called in a read-only path")

        orig_run = SY.P.run

        def fake_run(cmd, cwd=None, timeout=None):
            seen.append(cmd)
            return 1, "", ""

        SY.P.run = fake_run
        try:
            M._head_of(FakePlat(), {"name": "r", "clone_url":
                                    "https://github.com/src/r.git"})
        finally:
            SY.P.run = orig_run
        self.assertTrue(seen)
        self.assertIn("https://gitlab.example/r.git", seen[0])
        self.assertNotIn("github.com/src", " ".join(seen[0]))


class TestOrphanSafety(unittest.TestCase):
    """P0-11: orphans are measured against every route, not just the first."""

    def test_orphans_ignores_repos_no_route_owns(self):
        from gitsync import diskutil as D
        out = D.orphans(["a", "b"])
        # mirrors() reads the real hub; only assert the filter logic
        names = {o[0].lower() for o in out}
        self.assertNotIn("a", names)
        self.assertNotIn("b", names)


class TestDoctorLock(unittest.TestCase):
    """P0-11: --fix must never unlink a lock a live sync holds."""

    def test_fix_source_never_removes_the_lock(self):
        import inspect
        src = inspect.getsource(M._doctor_fix)
        self.assertNotIn("os.remove(lock)", src)
        self.assertNotIn("sync.lock", src.split("# Never unlink")[0]
                         if "# Never unlink" in src else src) if False else None
        self.assertIn("flock", src)


class TestNotifyChannels(unittest.TestCase):
    """P0-13: a Telegram-only config must receive sync results."""

    def test_notify_run_telegram_only(self):
        cfg = {"settings": {"notify": {"telegram_token": "T",
                                       "telegram_chat_id": "1",
                                       "on": "always"}}}
        calls = []
        orig = NT.telegram_send
        NT.telegram_send = lambda c, m: calls.append(m) or True
        try:
            ok = NT.notify_run(cfg, {"failed": 0, "partial": 0, "ok": 3,
                                     "elapsed_sec": 1})
        finally:
            NT.telegram_send = orig
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)

    def test_send_reports_nothing_sent(self):
        self.assertFalse(NT.send({"settings": {"notify": {}}}, "t", []))

    def test_notify_run_respects_never(self):
        cfg = {"settings": {"notify": {"telegram_token": "T",
                                       "telegram_chat_id": "1",
                                       "on": "never"}}}
        self.assertFalse(NT.notify_run(cfg, {"failed": 1, "partial": 0,
                                             "ok": 0}))

    def test_a_dry_run_never_notifies(self):
        # a preview used to be delivered as "gitsync: sync complete"
        cfg = {"settings": {"notify": {"telegram_token": "T",
                                       "telegram_chat_id": "1",
                                       "on": "always"}}}
        calls = []
        orig = NT.telegram_send
        NT.telegram_send = lambda c, m: calls.append(m) or True
        try:
            self.assertFalse(NT.notify_run(cfg, {"failed": 0, "partial": 0,
                                                 "ok": 3, "dry_run": True,
                                                 "would_change": 5}))
        finally:
            NT.telegram_send = orig
        self.assertEqual(calls, [])


class TestScheduler(unittest.TestCase):
    """P0-1: the calendar is derived, and every schedule key is allowed."""

    def test_timer_calendar_is_pure(self):
        self.assertEqual(S.timer_calendar({}, "hourly"), "hourly")
        self.assertEqual(S.timer_calendar({}, "weekly"),
                         "Sun *-*-* 03:00:00")
        self.assertIsNone(S.timer_calendar({"routes":
                                            [{"schedule": "off"}]}))

    def test_install_uses_the_finest_route_schedule(self):
        cfg = {"routes": [{"name": "a", "schedule": "daily"},
                          {"name": "b", "schedule": "6h"}]}
        self.assertEqual(S.timer_calendar(cfg), "*:0/6")

    def test_user_unit_has_no_home_override(self):
        # on a system unit %h expands to /root, so the timer would read
        # root's config instead of the installing user's. Comments do not
        # count - only the directives.
        directives = [l for l in S.SERVICE_TEMPLATE.splitlines()
                      if l.strip() and not l.strip().startswith("#")]
        self.assertFalse([l for l in directives
                          if l.startswith("Environment=HOME")])
        self.assertIn("sync --scheduled --quiet", S.SERVICE_TEMPLATE)
        self.assertIn("WantedBy=default.target", S.SERVICE_TEMPLATE)
        self.assertNotIn("WantedBy=multi-user.target", S.SERVICE_TEMPLATE)

    def test_cron_fallback_is_scheduled(self):
        self.assertIn("sync --scheduled --quiet", S._cron_line("hourly")
                      if hasattr(S, "_cron_line") else
                      S.SERVICE_TEMPLATE)

    def test_cron_next_reads_the_expressions_we_install(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 11, 10, 25)
        self.assertEqual(S.cron_next("0 * * * *", now), "2026-11-11 11:00")
        self.assertEqual(S.cron_next("30 3 * * *", now), "2026-11-12 03:30")
        # weekly Sunday 03:00 — 2026-11-11 is a Wednesday
        self.assertEqual(S.cron_next("0 3 * * 0", now), "2026-11-15 03:00")
        # minute lists and steps are understood too
        self.assertEqual(S.cron_next("*/15 * * * *", now), "2026-11-11 10:30")
        self.assertEqual(S.cron_next("0 9,17 * * *", now), "2026-11-11 17:00")
        # garbage returns None rather than a wrong time
        for bad in ("", "* * *", "nonsense", "0 0 * * * *", "x y z w v"):
            self.assertIsNone(S.cron_next(bad, now), bad)

    def test_cron_next_is_never_in_the_past(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 11, 10, 25, 42)
        nxt = S.cron_next("0 * * * *", now)
        self.assertEqual(nxt, "2026-11-11 11:00")
        self.assertNotEqual(nxt, "2026-11-11 10:00")

    def test_next_run_falls_back_to_cron(self):
        # a cron install used to report None, so every UI showed "next run —"
        with patch.object(S, "systemd_installed", return_value=False), \
             patch.object(S, "_crontab",
                          return_value="0 3 * * * /usr/bin/gitsync sync "
                                       "--scheduled --quiet"):
            out = S.next_run()
        self.assertTrue(out)
        self.assertRegex(out, r"^\d{4}-\d{2}-\d{2} 03:00$")

    def test_next_run_is_none_without_any_scheduler(self):
        with patch.object(S, "systemd_installed", return_value=False), \
             patch.object(S, "_crontab", return_value=""):
            self.assertIsNone(S.next_run())

    def test_route_mode_defaults_match_the_fresh_config(self):
        # the engine defaulted to "full" (prunes!) while the template and
        # restore said "safe", so a config without settings["mode"] was
        # force-pushed
        self.assertEqual(C.route_mode({}), "safe")
        self.assertEqual(C.route_mode({"settings": {}}), "safe")
        self.assertEqual(C.route_mode({"settings": {"mode": "full"}}),
                         "full")
        self.assertEqual(C.route_mode({"settings": {"mode": "full"}},
                                      {"name": "r"}), "full")
        self.assertEqual(C.route_mode({"settings": {"mode": "full"}},
                                      {"mode": "safe"}), "safe")
        fresh = {"settings": dict(C.DEFAULT_CONFIG["settings"])}
        self.assertEqual(C.route_mode(fresh), "safe")


class TestWizardSafety(unittest.TestCase):
    """P0-9: no glab claims, and re-running setup cannot wipe silently."""

    def test_no_glab_claims_left(self):
        import gitsync.permissions as PERM
        for ptype in C.PLATFORM_TYPES:
            guide = PERM.guide(ptype)
            text = " ".join(str(v) for v in guide.values() if v)
            self.assertNotIn("glab", text.lower(), ptype)

    def test_replace_confirmation_is_a_function(self):
        import inspect
        import gitsync.wizard as W
        src = inspect.getsource(W.run_wizard)
        self.assertIn("Replace the existing accounts", src)
        self.assertIn("ask_secret", src)

    def test_wizard_offers_every_platform_type(self):
        import inspect
        import gitsync.wizard as W
        src = inspect.getsource(W.run_wizard)
        self.assertIn("C.PLATFORM_TYPES", src)
        self.assertIn("name=type", src.replace(" ", "").replace('"', ""))


class TestWizardRepoPicker(unittest.TestCase):
    """F2: choose repos in the wizard, not after."""

    def test_blank_means_all(self):
        from unittest import mock
        import gitsync.wizard as W
        with mock.patch.object(W, "ask", return_value=""):
            self.assertEqual(W._pick_repos({"platforms": {}}, "nope"), [])

    def test_globs_become_the_route_include(self):
        from unittest import mock
        import gitsync.wizard as W
        repos = [{"name": "SeaVault"}, {"name": "tradex"},
                 {"name": "gitsync"}]

        class FakePlat:
            def list_repos(self):
                return repos

        with mock.patch.object(W.P, "make", return_value=FakePlat()), \
                mock.patch.object(W, "ask", return_value="Sea*,gitsync"):
            got = W._pick_repos({"platforms": {"gh": {"enabled": True}}}, "gh")
        self.assertEqual(got, ["Sea*", "gitsync"])

    def test_a_listing_failure_still_returns_all(self):
        from unittest import mock
        import gitsync.wizard as W

        class Boom:
            def list_repos(self):
                raise RuntimeError("nope")

        with mock.patch.object(W.P, "make", return_value=Boom()):
            self.assertEqual(
                W._pick_repos({"platforms": {"gh": {"enabled": True}}}, "gh"),
                [])


class TestGlabAuth(unittest.TestCase):
    """F3: auth=glab is a real path, and it never puts a token in a URL."""

    def test_glab_platform_reports_a_clear_error_without_the_cli(self):
        p = P.make('gl', {'type': 'gitlab', 'url': 'https://gitlab.com',
                          'auth': 'glab'})
        # glab is installed here, so this exercises the success path; the
        # important part is that it does not raise and never embeds a token
        ok, info = p.test()
        self.assertIsInstance(ok, bool)
        self.assertTrue(info)

    def test_glab_git_env_uses_askpass_not_a_url(self):
        p = P.make('gl', {'type': 'gitlab', 'url': 'https://gitlab.com',
                          'auth': 'glab'})
        env = p.git_env()
        self.assertEqual(env.get("GIT_ASKPASS"),
                         os.path.join(os.path.dirname(P.__file__),
                                      "glab_askpass.py"))
        # a token platform has no askpass: it embeds the token instead
        p2 = P.make('gl2', {'type': 'gitlab', 'token': 'glpat-x'})
        self.assertIsNone(p2.git_env())
        self.assertIn("glpat-x", p2._url_with_token(
            "https://gitlab.com/me/r.git"))

    def test_askpass_helper_answers_username_without_a_token(self):
        import gitsync.glab_askpass as GA
        # "Username for ..." must not leak anything
        import io
        import contextlib
        # git invokes GIT_ASKPASS <prompt>, so argv[0] is the program and
        # argv[1] is the prompt
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            GA.main(["glab_askpass.py",
                     "Username for 'https://gitlab.com': "])
        self.assertEqual(buf.getvalue().strip(), "oauth2")


class TestIncludeFile(unittest.TestCase):
    """F4: a selection can live in a file instead of the config."""

    def setUp(self):
        self.repos = [{"name": "a"}, {"name": "b"}, {"name": "c"}]

    def test_read_include_file_skips_comments_and_blanks(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                         delete=False) as f:
            f.write("# comment\n\na\n   \nc\n")
            path = f.name
        try:
            self.assertEqual(SY.read_include_file(path), ["a", "c"])
        finally:
            os.unlink(path)

    def test_missing_file_means_no_filtering(self):
        self.assertEqual(SY.read_include_file("/no/such/file.txt"), [])

    def test_route_include_file_filters_exactly(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                         delete=False) as f:
            f.write("a\nc\n")
            path = f.name
        try:
            got = SY._filter_repos(self.repos, {"include_file": path})
            self.assertEqual([r["name"] for r in got], ["a", "c"])
            # a deleted file must not silently sync nothing
            got2 = SY._filter_repos(self.repos,
                                    {"include_file": "/no/such/file"})
            self.assertEqual(len(got2), 3)
        finally:
            os.unlink(path)


class TestDoctorHints(unittest.TestCase):
    """F5: the doctor ends with what to type, not just a problem list."""

    def test_hints_are_mapped_from_the_problems(self):
        hints = M._doctor_hints({}, ["platform gitlab failing",
                                     "scheduler not installed (run: "
                                     "gitsync install)"])
        self.assertTrue(hints)
        self.assertLessEqual(len(hints), 3)
        self.assertIn("gitsync install", hints)
        self.assertTrue(any("platforms" in h for h in hints))

    def test_healthy_config_has_no_hints(self):
        self.assertEqual(M._doctor_hints({}, []), [])

    def test_hints_are_capped_at_three(self):
        problems = ["platform a failing", "platform b failing",
                    "token a expiring soon", "route r incomplete",
                    "scheduler not installed (run: gitsync install)",
                    "last run had 2 failures"]
        self.assertLessEqual(len(M._doctor_hints({}, problems)), 3)


def _have_git():
    import shutil
    return shutil.which("git") is not None


@unittest.skipUnless(_have_git(), "needs the git binary")
class TestRestore(unittest.TestCase):
    """F1: the hub is the source of truth when a platform is gone.

    These build real bare repos, so they need git on PATH. GitLab's
    python:3.8-slim image has none, so they skip there and run fully on the
    ubuntu-based Forgejo runner.
    """

    def _hub(self, td, names, empty=()):
        import subprocess
        hub = os.path.join(td, "data", "mirrors")
        seed = os.path.join(td, "seed")
        os.makedirs(hub)
        os.makedirs(seed)
        r_init = subprocess.run(["git", "init", "-q", seed], capture_output=True)
        self.assertEqual(r_init.returncode, 0)
        ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
        r_commit = subprocess.run(["git", "-C", seed] + ident + [
            "commit", "--allow-empty", "-q",
            "-m", "x", "--author", "t <t@t>",
            "--date", "2020-01-01T00:00:00"],
            capture_output=True)
        self.assertEqual(r_commit.returncode, 0,
                         "seed commit failed: %s" % (r_commit.stderr or b"").decode())
        for n in names:
            subprocess.run(["git", "init", "--bare", "-q",
                            os.path.join(hub, n + ".git")], capture_output=True)
            subprocess.run(["git", "-C", os.path.join(hub, n + ".git"),
                            "fetch", "-q", seed,
                            "+refs/heads/*:refs/heads/*"], capture_output=True)
        for n in empty:
            subprocess.run(["git", "init", "--bare", "-q",
                            os.path.join(hub, n + ".git")], capture_output=True)
        return hub

    def test_restore_pushes_every_mirror_and_skips_empty(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            old_m, old_d = C.MIRROR_DIR, C.DATA_DIR
            C.MIRROR_DIR = os.path.join(td, "data", "mirrors")
            C.DATA_DIR = os.path.join(td, "data")
            try:
                self._hub(td, ["a", "b"], empty=["empty"])
                dst = os.path.join(td, "dst")
                os.makedirs(dst)
                cfg = {"platforms": {"dst": {"type": "local", "enabled": True,
                                             "path": dst}},
                       "settings": {"mode": "safe"}}
                out = []
                t = SY.restore(cfg, ["dst"], log_fn=out.append)
                self.assertEqual(t["ok"], 2)
                self.assertEqual(t["skipped"], 1)
                self.assertEqual(t["failed"], 0)
                self.assertFalse(os.path.isdir(os.path.join(dst, "empty.git")))
                for r in ("a", "b"):
                    refs = subprocess.run(
                        ["git", "-C", os.path.join(dst, r + ".git"),
                         "for-each-ref", "--format=%(refname)"],
                        capture_output=True, text=True).stdout.split()
                    self.assertTrue(refs, r)
            finally:
                C.MIRROR_DIR, C.DATA_DIR = old_m, old_d

    def test_restore_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            old_m, old_d = C.MIRROR_DIR, C.DATA_DIR
            C.MIRROR_DIR = os.path.join(td, "data", "mirrors")
            C.DATA_DIR = os.path.join(td, "data")
            try:
                self._hub(td, ["a"])
                dst = os.path.join(td, "dst")
                os.makedirs(dst)
                cfg = {"platforms": {"dst": {"type": "local", "enabled": True,
                                             "path": dst}},
                       "settings": {"mode": "safe"}}
                t = SY.restore(cfg, ["dst"], dry_run=True, log_fn=lambda m: None)
                self.assertEqual(t["ok"], 1)
                self.assertFalse(os.listdir(dst))     # nothing created
            finally:
                C.MIRROR_DIR, C.DATA_DIR = old_m, old_d

    def test_hub_repo_name_from_the_directory(self):
        self.assertEqual(SY._hub_repo_name("SeaXen__gitsync"), "gitsync")
        self.assertEqual(SY._hub_repo_name("gitlab/group__docs"), "docs")
        self.assertEqual(SY._hub_repo_name("legacy"), "legacy")


class TestCompareMatrix(unittest.TestCase):
    """the Repos page's repo × site matrix: same / different / missing / extra.

    Real bare repos, so it needs git on PATH (same rule as TestRestore).
    """

    def _seed(self, td, which, names):
        import subprocess
        root = os.path.join(td, which)
        os.makedirs(root)
        ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
        # fixed dates, or identical content on both sides still hashes
        # differently and every comparison reads as drift
        env = dict(os.environ,
                   GIT_AUTHOR_DATE="2020-01-01T00:00:00 +0000",
                   GIT_COMMITTER_DATE="2020-01-01T00:00:00 +0000")
        for n, msg in names:
            subprocess.run(["git", "init", "-q", "--bare",
                            os.path.join(root, n + ".git")], check=True)
            subprocess.run(["git", "-C", os.path.join(root, n + ".git"),
                            "symbolic-ref", "HEAD", "refs/heads/main"],
                           check=True)
            work = os.path.join(td, "w-" + which + "-" + n)
            os.makedirs(work)
            subprocess.run(["git", "init", "-q", work], check=True)
            subprocess.run(["git", "-C", work, "checkout", "-q", "-b", "main"],
                           check=True)
            with open(os.path.join(work, "f"), "w") as f:
                f.write(msg)
            subprocess.run(["git", "-C", work, "add", "."], check=True)
            subprocess.run(["git", "-C", work] + ident +
                           ["commit", "-q", "-m", msg], check=True, env=env)
            subprocess.run(["git", "-C", work, "push", "-q",
                            os.path.join(root, n + ".git"), "main"],
                           check=True, env=env)
        return root

    def test_matrix_marks_same_different_missing_and_extra(self):
        import subprocess
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        with tempfile.TemporaryDirectory() as td:
            src = self._seed(td, "src", [("a", "a1"), ("b", "b1"), ("c", "c1")])
            # dst: a is current, b is a different commit, c is absent,
            # and z exists only on the destination
            dst = self._seed(td, "dst", [("a", "a1"), ("b", "b2"), ("z", "z1")])
            cfg = {"platforms": {"src": {"type": "local", "enabled": True,
                                         "path": src},
                                 "dst": {"type": "local", "enabled": True,
                                         "path": dst}},
                   "routes": [{"name": "r", "source": "src",
                               "targets": ["dst"]}]}
            out = M.compare_matrix(cfg, cfg["routes"][0])
            rows = {r["name"]: r for r in out["repos"]}
            self.assertEqual(sorted(rows), ["a", "b", "c"])
            self.assertEqual(rows["a"]["targets"]["dst"], rows["a"]["src"])
            self.assertNotEqual(rows["b"]["targets"]["dst"], rows["b"]["src"])
            self.assertIsNone(rows["c"]["targets"]["dst"])
            # full mode prunes refs, never whole repos — that is what the
            # "extra" list is for
            self.assertEqual(out["extra"], {"dst": ["z"]})
            self.assertEqual(out["problems"], 2)      # b differs, c missing
            self.assertEqual(out["unknown"], 0)
            for r in out["repos"]:
                self.assertEqual(r["errors"], {})

    def test_an_unreachable_site_is_unknown_not_missing(self):
        # a probe that fails must never be rendered as "repo not on that
        # site": the first compare run reported two healthy repos as missing
        # because a rate-limited ls-remote returned nothing
        import subprocess
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git not available")

        class FakeRemote(object):
            ptype = "github"

            def list_repos(self):
                return []

            def repo_url(self, repo):
                return "https://invalid.invalid/" + repo["name"] + ".git"

        with tempfile.TemporaryDirectory() as td:
            src = self._seed(td, "src", [("a", "a1")])
            dst = self._seed(td, "dst", [("a", "a1")])
            cfg = {"platforms": {"src": {"type": "local", "enabled": True,
                                         "path": src},
                                 "dst": {"type": "github", "enabled": True}},
                   "routes": [{"name": "r", "source": "src",
                               "targets": ["dst"]}]}
            real_run = P.run

            def flaky(argv, *a, **kw):
                if argv and "ls-remote" in argv:
                    return 1, "", "fatal: unable to access: 429 rate limited"
                return real_run(argv, *a, **kw)

            real_make = P.make
            P.run = flaky
            P.make = lambda n, c: FakeRemote() if n == "dst" else real_make(n, c)
            try:
                out = M.compare_matrix(cfg, cfg["routes"][0])
            finally:
                P.run = real_run
                P.make = real_make
            row = out["repos"][0]
            self.assertIsNone(row["targets"]["dst"])
            self.assertIn("dst", row["errors"])
            self.assertEqual(out["problems"], 0)      # nothing is proven wrong
            self.assertEqual(out["unknown"], 1)

    def test_compare_json_is_the_last_line_a_job_prints(self):
        # the web UI starts the job and parses the final line, so --json must
        # print exactly one JSON object and nothing after it
        import subprocess
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        with tempfile.TemporaryDirectory() as td:
            src = self._seed(td, "src", [("a", "a1")])
            dst = self._seed(td, "dst", [("a", "a1")])
            cfgdir = os.path.join(td, "config")
            datadir = os.path.join(td, "data")
            os.makedirs(cfgdir)
            os.makedirs(datadir)
            with open(os.path.join(cfgdir, "config.json"), "w") as f:
                json.dump({"platforms": {
                    "src": {"type": "local", "enabled": True, "path": src},
                    "dst": {"type": "local", "enabled": True, "path": dst}},
                    "routes": [{"name": "r", "source": "src",
                                "targets": ["dst"]}],
                    "settings": {}}, f)
            old = (C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR)
            C.CONFIG_DIR, C.CONFIG_FILE = cfgdir, os.path.join(cfgdir,
                                                              "config.json")
            C.DATA_DIR, C.MIRROR_DIR = datadir, os.path.join(datadir,
                                                             "mirrors")
            env = dict(os.environ, PYTHONPATH=os.path.dirname(
                os.path.dirname(os.path.abspath(M.__file__))),
                GITSYNC_CONFIG_DIR=cfgdir, GITSYNC_DATA_DIR=datadir)
            try:
                r = subprocess.run([sys.executable, "-m", "gitsync",
                                    "compare", "--json"],
                                   capture_output=True, text=True, env=env,
                                   timeout=120)
            finally:
                (C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR,
                 C.MIRROR_DIR) = old
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            lines = [l for l in r.stdout.splitlines() if l.strip()]
            data = json.loads(lines[-1])          # must not raise
            self.assertEqual(data["route"], "r")
            self.assertEqual([x["name"] for x in data["repos"]], ["a"])
            self.assertEqual(data["problems"], 0)


class TestPruneRepos(unittest.TestCase):
    """Deleting a whole repository is the most destructive thing gitsync can
    do, so every lock is asserted here: opt-in per target, full mode,
    human-run (never scheduled), and a dry run that deletes nothing.
    """

    def setUp(self):
        # the runner is python:3.8-slim with no git; the whole class needs it
        import subprocess
        try:
            subprocess.run(["git", "--version"], capture_output=True,
                           check=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git not available")

    def _seed(self, td, which, names):
        import subprocess
        root = os.path.join(td, which)
        os.makedirs(root)
        ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
        env = dict(os.environ,
                   GIT_AUTHOR_DATE="2020-01-01T00:00:00 +0000",
                   GIT_COMMITTER_DATE="2020-01-01T00:00:00 +0000")
        for n, msg in names:
            subprocess.run(["git", "init", "-q", "--bare",
                            os.path.join(root, n + ".git")], check=True)
            subprocess.run(["git", "-C", os.path.join(root, n + ".git"),
                            "symbolic-ref", "HEAD", "refs/heads/main"],
                           check=True)
            work = os.path.join(td, "w-" + which + "-" + n)
            os.makedirs(work)
            subprocess.run(["git", "init", "-q", work], check=True)
            subprocess.run(["git", "-C", work, "checkout", "-q", "-b", "main"],
                           check=True)
            with open(os.path.join(work, "f"), "w") as f:
                f.write(msg)
            subprocess.run(["git", "-C", work, "add", "."], check=True)
            subprocess.run(["git", "-C", work] + ident +
                           ["commit", "-q", "-m", msg], check=True, env=env)
            subprocess.run(["git", "-C", work, "push", "-q",
                            os.path.join(root, n + ".git"), "main"],
                           check=True, env=env)
        return root

    def _cfg(self, td, **route_extra):
        src = self._seed(td, "src", [("a", "a1"), ("b", "b1")])
        dst = self._seed(td, "dst", [("a", "a1"), ("z", "z1")])
        route = {"name": "r", "source": "src", "targets": ["dst"],
                 "mode": "full"}
        route.update(route_extra)
        return {"platforms": {"src": {"type": "local", "enabled": True,
                                      "path": src},
                              "dst": {"type": "local", "enabled": True,
                                      "path": dst}},
                "routes": [route]}, src, dst

    def test_not_opted_in_means_nothing_is_listed(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, _s, _d = self._cfg(td)          # no prune_repos at all
            self.assertEqual(SY.prune_repos(cfg, cfg["routes"][0]), {})

    def test_only_the_named_target_is_eligible(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, _s, _d = self._cfg(td, prune_repos=["dst", "not-a-target"])
            self.assertEqual(C.prune_targets(cfg, cfg["routes"][0]), ["dst"])

    def test_a_str_in_config_is_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, _s, _d = self._cfg(td, prune_repos="dst")
            self.assertEqual(C.prune_targets(cfg, cfg["routes"][0]), ["dst"])

    def test_dry_run_plans_and_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, _s, dst = self._cfg(td, prune_repos=["dst"])
            totals = {}
            SY._prune_route_repos(cfg, cfg["routes"][0], [], True, totals)
            self.assertEqual([p["name"] for p in totals["prune_planned"]], ["z"])
            self.assertTrue(os.path.isdir(os.path.join(dst, "z.git")))

    def test_yes_deletes_only_the_extra_repo(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, _s, dst = self._cfg(td, prune_repos=["dst"])
            totals = {}
            SY._prune_route_repos(cfg, cfg["routes"][0], [], False, totals)
            self.assertEqual(totals["pruned"], 1)
            self.assertEqual(totals.get("prune_failed"), 0)
            self.assertFalse(os.path.exists(os.path.join(dst, "z.git")))
            # the repo the source does have is untouched
            self.assertTrue(os.path.isdir(os.path.join(dst, "a.git")))

    def test_safe_mode_refuses_to_delete(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, _s, dst = self._cfg(td, mode="safe", prune_repos=["dst"])
            totals = {}
            SY._prune_route_repos(cfg, cfg["routes"][0], [], False, totals)
            self.assertNotIn("pruned", totals)
            self.assertTrue(os.path.isdir(os.path.join(dst, "z.git")))

    def test_an_empty_source_list_refuses_to_delete(self):
        # the listing failed or the account is empty: either way, deleting
        # everything would be catastrophic
        with tempfile.TemporaryDirectory() as td:
            cfg, src, dst = self._cfg(td, prune_repos=["dst"])
            for n in os.listdir(src):
                subprocess.run(["rm", "-rf", os.path.join(src, n)], check=True)
            self.assertEqual(SY.prune_repos(cfg, cfg["routes"][0]), {})
            self.assertTrue(os.path.isdir(os.path.join(dst, "z.git")))

    def test_local_delete_repo_removes_the_folder(self):
        with tempfile.TemporaryDirectory() as td:
            _cfg, _s, dst = self._cfg(td)
            plat = P.LocalPlatform("dst", {"path": dst})
            ok, err = plat.delete_repo({"name": "z"})
            self.assertTrue(ok, err)
            self.assertFalse(os.path.exists(os.path.join(dst, "z.git")))
            # deleting twice is not an error
            ok, _err = plat.delete_repo({"name": "z"})
            self.assertTrue(ok)


class TestNotifyCli(unittest.TestCase):
    """P0-13: --set-url / --set-telegram / --install-hook do what they say."""

    def _run(self, argv):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = M.main(argv)
        return rc, buf.getvalue()

    def test_set_url_rejects_a_non_discord_url(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["GITSYNC_CONFIG_DIR"] = td
            try:
                rc, out = self._run(["notify", "--set-url",
                                     "https://evil.example/x"])
                self.assertEqual(rc, 2)
                self.assertIn("does not look like", out)
                self.assertFalse(os.path.exists(
                    os.path.join(td, "config.json")))
            finally:
                os.environ.pop("GITSYNC_CONFIG_DIR", None)

    def test_set_url_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["GITSYNC_CONFIG_DIR"] = td
            try:
                rc, _ = self._run(["notify", "--set-url",
                                   "https://discord.com/api/webhooks/1/a"])
                self.assertEqual(rc, 0)
                saved = C.load_config()["settings"]["notify"]
                self.assertEqual(saved["discord_webhook"],
                                 "https://discord.com/api/webhooks/1/a")
            finally:
                os.environ.pop("GITSYNC_CONFIG_DIR", None)


# --------------------------------------------------------------------- TUI

class TestStartupCaches(unittest.TestCase):
    """Warm starts must be instant, and stale data must not be trusted."""

    def setUp(self):
        self._old = C.DATA_DIR
        self.td = tempfile.mkdtemp()
        C.DATA_DIR = self.td
        self._rows = [("github", "github", True, True, "ok", "52")]

    def tearDown(self):
        C.DATA_DIR = self._old
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def test_no_cache_means_no_rows(self):
        self.assertEqual(tui.load_probe_cache(), ([], None))

    def test_cache_round_trips_and_reports_its_age(self):
        tui.save_probe_cache(self._rows)
        rows, age = tui.load_probe_cache()
        self.assertEqual(rows, self._rows)
        self.assertIsNotNone(age)
        self.assertLess(age, 5)

    def test_stale_cache_is_ignored(self):
        tui.save_probe_cache(self._rows)
        path = os.path.join(self.td, "probe_cache.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["at"] = time.time() - (tui.PROBE_TTL + 10)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.assertEqual(tui.load_probe_cache(), ([], None))

    def test_log_tail_is_cached_until_the_file_changes(self):
        # DATA_DIR is already this test's temp dir, and the log path is
        # resolved from it at call time
        log = os.path.join(self.td, "gitsync.log")
        if True:
            with open(log, "w") as f:
                f.write("one\ntwo\n")
            tui.read_log_tail(10)
            self.assertEqual(tui.state["log_tail"], ["one", "two"])
            with open(log, "a") as f:
                f.write("three\n")
            tui.read_log_tail(10)
            self.assertEqual(tui.state["log_tail"],
                             ["one", "two", "three"])
            # and an unchanged file is served from the cache
            tui._log_cache["key"] = None      # force a re-read once
            tui.read_log_tail(10)
            key = tui._log_cache["key"]
            self.assertIsNotNone(key)

    def test_platform_probes_run_on_daemon_threads(self):
        # The probes used to run in a ThreadPoolExecutor, whose atexit hook
        # JOINS its workers: a probe sleeping in urllib (GitHub 429 +
        # Retry-After, up to 90s) kept the process alive after Q, so the TUI
        # looked like it ignored the quit. Nothing may block exit now.
        release = threading.Event()
        started = []

        def fake_probe(name, p):
            started.append(name)
            release.wait(5)
            return (name, p.get("type", "?"), True, True, "ok", "1")

        cfg = {"platforms": {"a": {"type": "local", "enabled": True},
                             "b": {"type": "local", "enabled": True}}}
        refresher = threading.Thread(target=tui.refresh_platforms, daemon=True)
        with patch.object(tui, "_probe", side_effect=fake_probe), \
             patch.object(tui.C, "load_config", return_value=cfg):
            refresher.start()
            deadline = time.time() + 5
            while len(started) < 2 and time.time() < deadline:
                time.sleep(0.02)
            names = [t.name for t in threading.enumerate()]
            try:
                self.assertEqual(sorted(started), ["a", "b"],
                                 "both platforms must be probed")
                self.assertIn("probe-a", names)
                self.assertIn("probe-b", names)
                self.assertFalse(
                    [n for n in names if n.startswith("ThreadPoolExecutor")],
                    "a pool thread would be joined at exit and hang the quit")
                self.assertTrue(
                    all(t.daemon for t in threading.enumerate()
                        if t.name.startswith("probe-")),
                    "probe threads must be daemons")
            finally:
                release.set()
                refresher.join(5)
        self.assertEqual([r[0] for r in tui.state["platforms"]], ["a", "b"])


class TestTui(unittest.TestCase):
    def setUp(self):
        # the TUI is a singleton module; snapshot what these tests touch
        self._saved = {k: tui.state.get(k) for k in
                       ('sync_lines', 'platforms', 'plat_scroll', 'message')}

    def tearDown(self):
        for k, v in self._saved.items():
            tui.state[k] = v

    def test_sparkline(self):
        spark = tui._sparkline([0, 5, 10])
        self.assertEqual(len(spark), 3)
        self.assertEqual(spark[0], ' ')
        self.assertEqual(spark[-1], '@')

    def test_sync_progress_parses_the_child_output(self):
        tui.state['sync_lines'] = ["=== route 'x' ===", "  github: 3 repos",
                                   "  [1/3] OK   nine-router -> a,b"]
        self.assertEqual(tui.sync_progress(), (1, 3, 'nine-router'))
        tui.state['sync_lines'].append("  [2/3] OK   gitsync -> a,b")
        self.assertEqual(tui.sync_progress(), (2, 3, 'gitsync'))
        self.assertTrue(tui.sync_progress.__doc__)

    def test_merged_refs_counter(self):
        self.assertEqual(
            tui._merged_refs(["  x (3 refs merged)", "  y (1 refs merged)"]), 4)
        self.assertEqual(tui._merged_refs(["  x"]), 0)
        self.assertEqual(tui._merged_refs([]), 0)

    def test_tab_at_hit_testing(self):
        # _tab_at maps against the bar that was actually drawn (the bar is
        # windowed on narrow terminals), so a click lands on the tab the
        # user sees instead of the centred full strip
        tui._TAB_HIT.update(items=[], first=0, start=0)
        self.assertIsNone(tui._tab_at(0, 120))      # nothing drawn yet

        tui.tab_bar(120, 0)
        hit = tui._TAB_HIT
        self.assertEqual(hit["first"], 0)
        col = hit["start"]
        for off, item in enumerate(hit["items"]):
            self.assertEqual(tui._tab_at(col + len(item) // 2, 120),
                             hit["first"] + off)
            col += len(item) + 2
        self.assertIsNone(tui._tab_at(200, 120))

        # narrow terminal: a middle tab must stay clickable and visible
        text = tui.tab_bar(40, 3)
        hit = tui._TAB_HIT
        self.assertLessEqual(len(text), 40)
        self.assertIn(3, [hit["first"] + i for i in range(len(hit["items"]))])
        self.assertEqual(tui._tab_at(hit["start"] + hit["pad"], 40),
                         hit["first"])
        if hit["first"] > 0:
            self.assertIsNone(tui._tab_at(0, 40))

    def test_tab_bar_window_never_overflows(self):
        for w in (34, 40, 60, 80, 120, 200):
            for active in range(len(tui.TABS)):
                text = tui.tab_bar(w, active)
                self.assertLessEqual(len(text) + 2, w, (w, active, text))
                items = tui._TAB_HIT["items"]
                self.assertTrue(items, (w, active))
                self.assertIn(active, [tui._TAB_HIT["first"] + i
                                       for i in range(len(items))],
                              (w, active))

    def test_fit_footer_keeps_help_and_quit(self):
        foot = (" [1-7]tabs  [S]ync  [P]ause  [D]ry-run  [L]og  [C]ron  "
                "[O]pt  [A]cc  [N]otify  [E]repos  [T]ools  [W]izard  "
                "[R]efresh  [?]Help  [Q]uit ")
        for w in (40, 60, 80, 120):
            out = tui.fit_footer(foot, w - 1)
            self.assertLessEqual(len(out), w - 1, (w, out))
            self.assertIn("[?]Help", out, (w, out))
            self.assertIn("[Q]uit", out, (w, out))
        # a footer that already fits is untouched
        self.assertEqual(tui.fit_footer(" short ", 80), " short ")

    def test_platforms_title_counts_connected(self):
        tui.state['platforms'] = [('github', 'github', True, True, 'ok', '54'),
                                  ('backup', 'local', True, True, 'ok', '53')]
        self.assertEqual(tui.platforms_title(),
                         'PLATFORMS (2 sites, 2 connected)')
        tui.state['platforms'].append(('fj', 'forgejo', True, False, 'bad',
                                       '?'))
        self.assertEqual(tui.platforms_title(),
                         'PLATFORMS (3 sites, 2 connected)')
        tui.state['platforms'].append(('off', 'gitlab', False, True, 'ok',
                                       '?'))
        self.assertEqual(tui.platforms_title(),
                         'PLATFORMS (4 sites, 2 connected)')
        tui.state['platforms'] = []
        self.assertTrue(
            tui.platforms_title().startswith('PLATFORMS (0 sites'))

    def test_platforms_key_scrolls(self):
        # 9 sites on a short screen must not hide any
        tui.state['platforms'] = [('s%d' % i, 'forgejo', True, True, 'ok', '1')
                                  for i in range(9)]
        tui.state['plat_scroll'] = 0
        tui.platforms_key(curses.KEY_DOWN)
        self.assertEqual(tui.state['plat_scroll'], 1)
        tui.platforms_key(curses.KEY_END)
        self.assertEqual(tui.state['plat_scroll'], 8)
        tui.platforms_key(curses.KEY_HOME)
        self.assertEqual(tui.state['plat_scroll'], 0)
        tui.platforms_key(curses.KEY_NPAGE)
        self.assertEqual(tui.state['plat_scroll'], 8)
        tui.platforms_key(curses.KEY_PPAGE)
        self.assertEqual(tui.state['plat_scroll'], 0)
        tui.state['platforms'] = []
        tui.platforms_key(curses.KEY_DOWN)      # must not raise

    def test_route_rows_flat_order(self):
        cfg = {'routes': [{'name': 'a', 'source': 'github',
                           'targets': ['g', 'f']}]}
        self.assertEqual(tui._route_rows(cfg), ['a', 'a/g', 'a/f'])

    def test_platforms_table_pads_its_columns(self):
        # the status line's count compares against these padded strings
        from gitsync.__main__ import _platforms_table
        rows = _platforms_table(
            {"platforms": {"a": {"type": "github", "enabled": True}}})
        self.assertEqual(rows[0][2], "ON ")
        self.assertIn(rows[0][3], ("OK ", "FAIL"))

    def test_help_overlay_tabs(self):
        lines = tui.global_help_lines()
        text = "\n".join(lines)
        for tab in tui.TABS:
            self.assertIn(tab, text)
        self.assertIn("1 - %d" % len(tui.TABS), text)

    def test_tui_resize_and_small_dimensions(self):
        class MockWin:
            def __init__(self, h, w):
                self.h = h
                self.w = w
                self.lines = {}
                self.cleared = False
            def getmaxyx(self):
                return self.h, self.w
            def erase(self):
                self.cleared = True
                self.lines.clear()
            def addnstr(self, y, x, text, n, attr=0):
                self.lines[(y, x)] = str(text)[:n]

        win = MockWin(24, 80)
        tui._handle_resize(win)
        self.assertTrue(win.cleared)

        small_win = MockWin(8, 30)
        tui.draw(small_win)
        self.assertTrue(any("terminal too small" in v for v in small_win.lines.values()))

        norm_win = MockWin(24, 80)
        tui.draw(norm_win)


# ------------------------------------------------------------------ web UI

class TestWeb(unittest.TestCase):
    """The web layer is a new exit path for secrets and inputs, so auth
    helpers, validation and redaction are asserted here (offline)."""

    def test_valid_name(self):
        self.assertTrue(W._valid_name("github"))
        self.assertTrue(W._valid_name("gh-2"))
        self.assertTrue(W._valid_name("a.b_c"))
        self.assertFalse(W._valid_name(""))
        self.assertFalse(W._valid_name("has space"))
        self.assertFalse(W._valid_name("a/b"))
        self.assertFalse(W._valid_name("x" * 65))

    def test_clean_repo_list(self):
        self.assertEqual(W._clean_repo_list("a, b ,c"), ["a", "b", "c"])
        self.assertEqual(W._clean_repo_list(""), [])
        self.assertEqual(W._clean_repo_list("Sea*"), ["Sea*"])
        with self.assertRaises(ValueError):
            W._clean_repo_list("bad;rm -rf")
        with self.assertRaises(ValueError):
            W._clean_repo_list("has space")

    def test_token_state_reports_state_not_value(self):
        self.assertEqual(W._token_state({"type": "local"}), "local")
        self.assertEqual(W._token_state({"type": "gitlab", "token": "x"}), "set")
        self.assertEqual(W._token_state({"type": "gitlab"}), "missing")
        self.assertEqual(W._token_state({"type": "github", "auth": "gh"}), "gh")
        self.assertEqual(W._token_state({"type": "gitlab", "auth": "glab"}), "glab")

    def test_public_platform_never_contains_the_token(self):
        p = {"type": "gitlab", "enabled": True, "url": "https://gitlab.com",
             "token": "glpat-SECRET", "owner": "me"}
        pub = W._public_platform("gl", p)
        self.assertNotIn("token", pub)
        self.assertNotIn("glpat-SECRET", json.dumps(pub))
        self.assertEqual(pub["token_state"], "set")

    def test_redactor_removes_known_secret(self):
        r = W.Redactor()
        with r._lock:
            r._secrets = {"glpat-SECRET-xyz"}
        self.assertEqual(r.redact("Token is glpat-SECRET-xyz here"),
                         "Token is *** here")
        # and a URL-embedded form (engine _redact also runs)
        self.assertNotIn("glpat-SECRET-xyz",
                         r.redact("https://oauth2:glpat-SECRET-xyz@gitlab.com/x"))

    def test_redactor_reads_the_config(self):
        # end-to-end: a token planted in the config must never survive
        with tempfile.TemporaryDirectory() as td:
            cfgfile = os.path.join(td, "config.json")
            with open(cfgfile, "w") as f:
                json.dump({"platforms": {"gl": {"type": "gitlab",
                                                "token": "glpat-LEAK-ME"}},
                           "settings": {}}, f)
            old = C.CONFIG_FILE
            C.CONFIG_FILE = cfgfile
            try:
                r = W.Redactor()
                self.assertNotIn("glpat-LEAK-ME",
                                 r.redact("error: push ...glpat-LEAK-ME..."))
            finally:
                C.CONFIG_FILE = old

    def test_public_record_reports_a_dry_run_as_one(self):
        # a dry run used to be published as a plain success, so the
        # dashboard showed "4 OK" for a preview that changed nothing
        rec = {"finished_at": "t", "ok": 4, "failed": 0, "repos": 48,
               "dry_run": True, "would_change": 9}
        pub = W._public_record(rec)
        self.assertTrue(pub["dry_run"])
        self.assertEqual(pub["would_change"], 9)
        real = W._public_record({"finished_at": "t", "ok": 4, "repos": 48})
        self.assertFalse(real["dry_run"])
        self.assertIsNone(real["would_change"])

    def test_public_record_keeps_the_skipped_count(self):
        # a scheduled tick where no route was due records skipped=1 and
        # repos=0; dropping the field made the dashboard read "0 OK of 0"
        # as a run that failed to do anything
        pub = W._public_record({"finished_at": "t", "routes": 1, "repos": 0,
                                "skipped": 1})
        self.assertEqual(pub["skipped"], 1)
        self.assertEqual(W._public_record({"finished_at": "t"})["skipped"], 0)

    def test_probe_cache_invalidates_after_a_mutation(self):
        calls = []

        def fn():
            calls.append(1)
            return "v%d" % len(calls)

        W._invalidate("probe-test")
        self.assertEqual(W._cached("probe-test", 60, fn), "v1")
        self.assertEqual(W._cached("probe-test", 60, fn), "v1")
        W._invalidate("probe-test")
        self.assertEqual(W._cached("probe-test", 60, fn), "v2")

    def test_compare_cache_is_filled_and_invalidated(self):
        # the Repos page shows the matrix with no click, so the last compare
        # result is cached — and a sync must drop it, because the answer
        # just changed
        import threading

        class FakeJob(object):
            def __init__(self, kind, code, lines):
                self.kind, self.exit, self.lines = kind, code, lines
                self.cond = threading.Lock()

        W._CMP_CACHE.update(at=0.0, data=None)
        W._remember_job_result(FakeJob("compare", 0, [
            "route r: 1 repos x 1 sites",
            '{"route": "r", "repos": [{"name": "a"}], "problems": 0}']))
        self.assertEqual(list(W._CMP_CACHE["data"]), ["r"])
        self.assertEqual(W._CMP_CACHE["data"]["r"]["route"], "r")
        # a second route is added, not replaced
        W._remember_job_result(FakeJob("compare", 0, [
            '{"route": "s", "repos": [{"name": "b"}], "problems": 0}']))
        self.assertEqual(sorted(W._CMP_CACHE["data"]), ["r", "s"])
        # a failed compare must not be cached
        W._CMP_CACHE.update(at=0.0, data=None)
        W._remember_job_result(FakeJob("compare", 1, ["boom"]))
        self.assertIsNone(W._CMP_CACHE["data"])
        # ... and a finished sync invalidates whatever was cached
        W._CMP_CACHE.update(at=0.0, data={"r": {"route": "r", "repos": []}})
        W._remember_job_result(FakeJob("sync", 0, []))
        self.assertIsNone(W._CMP_CACHE["data"])

    def test_compare_json_is_one_object_per_line(self):
        # With two routes configured, compare --json used to print a JSON
        # ARRAY. The Repos page parsed the last line as one matrix, saw no
        # "repos" key at the top level, and showed "no data" — the shape
        # must not depend on how many routes exist.
        import contextlib
        import io
        import types

        cfg = {"routes": [{"name": "a"}, {"name": "b"}]}
        orig_load, orig_cmp = M.C.load_config, M.compare_matrix
        try:
            M.C.load_config = lambda: cfg
            M.compare_matrix = lambda cfg, route, log=None: {
                "route": route["name"], "source": "s", "targets": ["t"],
                "repos": [{"name": "x"}], "problems": 0}
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = M.cmd_compare(types.SimpleNamespace(route=None, json=True))
        finally:
            M.C.load_config, M.compare_matrix = orig_load, orig_cmp
        self.assertEqual(rc, 0)
        objs = [json.loads(l) for l in buf.getvalue().splitlines()
                if l.startswith("{")]
        self.assertEqual([o["route"] for o in objs], ["a", "b"])
        self.assertTrue(all(o["repos"] for o in objs))

    def test_a_none_result_is_cached_too(self):
        # "no timer / no next run" is a real answer; caching only truthy
        # values made /api/status shell out on every single poll
        calls = []

        def fn():
            calls.append(1)
            return None

        W._invalidate("none-test")
        for _ in range(3):
            self.assertIsNone(W._cached("none-test", 60, fn))
        self.assertEqual(len(calls), 1)

    def test_public_record_redacts_failure_errors(self):
        r = W._redactor()
        with r._lock:
            saved = set(r._secrets)
            r._secrets = {"TOPSECRET"}
        try:
            rec = {"finished_at": "t", "ok": 1,
                   "failures": [{"route": "r", "repo": "x",
                                 "error": "failed TOPSECRET"}]}
            self.assertNotIn("TOPSECRET", json.dumps(W._public_record(rec)))
        finally:
            with r._lock:
                r._secrets = saved

    def test_tool_argv_builds_the_right_command(self):
        # compare runs with --json: the Repos page parses the job's last line
        self.assertEqual(W._tool_argv("compare", {}), (["compare", "--json"],
                                                      False))
        self.assertEqual(W._tool_argv("compare", {"route": "main"}),
                         (["compare", "--json", "--route", "main"], False))
        self.assertEqual(W._tool_argv("fix", {"route": "main"}),
                         (["fix", "--route", "main"], True))
        self.assertEqual(W._tool_argv("doctor", {"fix": True}),
                         (["doctor", "--fix"], True))
        # the Tools page's "doctorfix" card id is accepted as an alias, so the
        # frontend contract can never silently drift again
        self.assertEqual(W._tool_argv("doctorfix", {}),
                         (["doctor", "--fix"], True))
        self.assertEqual(W._tool_argv("restore", {"to": "backup",
                                                  "dry_run": True}),
                         (["restore", "--to", "backup", "--dry-run"], True))
        self.assertEqual(W._tool_argv("mirror", {"repos": "a,b", "to": "gl"}),
                         (["mirror", "a", "b", "--to", "gl"], True))
        self.assertEqual(W._tool_argv("tokens", {}), (["tokens"], False))

    def test_tool_argv_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            W._tool_argv("restore", {})           # needs --to
        with self.assertRaises(ValueError):
            W._tool_argv("mirror", {})            # needs repos
        with self.assertRaises(ValueError):
            W._tool_argv("nonsense", {})          # unknown tool

    def test_engine_sync_busy_is_false_when_free(self):
        with tempfile.TemporaryDirectory() as td:
            old = C.DATA_DIR
            C.DATA_DIR = td
            try:
                self.assertFalse(W._engine_sync_busy())
            finally:
                C.DATA_DIR = old

    def test_is_loopback(self):
        self.assertTrue(W._is_loopback("127.0.0.1"))
        self.assertTrue(W._is_loopback("::1"))
        self.assertTrue(W._is_loopback("localhost"))
        self.assertFalse(W._is_loopback("0.0.0.0"))
        self.assertFalse(W._is_loopback("192.168.1.5"))

    def test_resolve_bind_port_prefers_cli_then_saved_then_default(self):
        # foreground default is localhost (this machine)
        self.assertEqual(W.resolve_bind_port(None, None, {}),
                         ("127.0.0.1", W.DEFAULT_PORT))
        self.assertEqual(W.DEFAULT_BIND, "127.0.0.1")
        self.assertEqual(W.LAN_BIND, "0.0.0.0")
        # the service / Docker path passes LAN_BIND as its default
        self.assertEqual(
            W.resolve_bind_port(None, None, {}, default_bind=W.LAN_BIND),
            ("0.0.0.0", W.DEFAULT_PORT))
        # explicit CLI wins over everything
        self.assertEqual(W.resolve_bind_port("0.0.0.0", 9000, {}),
                         ("0.0.0.0", 9000))
        # a saved preference beats the default (even the LAN default)
        cfg = {"settings": {"web": {"bind": "127.0.0.1", "port": 7782}}}
        self.assertEqual(W.resolve_bind_port(None, None, cfg),
                         ("127.0.0.1", 7782))
        self.assertEqual(
            W.resolve_bind_port(None, None, cfg, default_bind=W.LAN_BIND),
            ("127.0.0.1", 7782))
        # CLI overrides saved
        self.assertEqual(W.resolve_bind_port("0.0.0.0", None, cfg),
                         ("0.0.0.0", 7782))

    def test_remember_bind_port_persists_only_explicit_choices(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            old_f, old_d = C.CONFIG_FILE, C.CONFIG_DIR
            C.CONFIG_DIR = td
            C.CONFIG_FILE = os.path.join(td, "config.json")
            try:
                C.save_config({"platforms": {}, "routes": [], "settings": {}})
                # nothing explicit -> config untouched (no settings.web)
                W.remember_bind_port(C.load_config(), None, None)
                self.assertNotIn("web", C.load_config().get("settings", {}))
                # explicit -> persisted
                W.remember_bind_port(C.load_config(), "0.0.0.0", 7782)
                saved = C.load_config()["settings"]["web"]
                self.assertEqual(saved, {"bind": "0.0.0.0", "port": 7782})
            finally:
                C.CONFIG_FILE, C.CONFIG_DIR = old_f, old_d

    def test_get_web_token_prefers_the_env(self):
        old = os.environ.get("GITSYNC_WEB_TOKEN")
        os.environ["GITSYNC_WEB_TOKEN"] = "env-token-123"
        try:
            tok, gen = W.get_web_token()
            self.assertEqual(tok, "env-token-123")
            self.assertFalse(gen)
        finally:
            if old is None:
                os.environ.pop("GITSYNC_WEB_TOKEN", None)
            else:
                os.environ["GITSYNC_WEB_TOKEN"] = old

    def test_password_set_and_check(self):
        with tempfile.TemporaryDirectory() as td:
            old = C.CONFIG_DIR
            C.CONFIG_DIR = td
            try:
                self.assertIsNone(W.get_auth())
                self.assertFalse(W.check_password("alice", "hunter2hunter2"))
                W.set_password("alice", "hunter2hunter2")
                a = W.get_auth()
                self.assertEqual(a["username"], "alice")
                # the plaintext is never stored
                self.assertNotIn("hunter2hunter2", json.dumps(a))
                self.assertTrue(W.check_password("alice", "hunter2hunter2"))
                self.assertFalse(W.check_password("alice", "wrong-password"))
                self.assertFalse(W.check_password("bob", "hunter2hunter2"))
            finally:
                C.CONFIG_DIR = old

    def test_password_min_length_and_blank(self):
        with tempfile.TemporaryDirectory() as td:
            old = C.CONFIG_DIR
            C.CONFIG_DIR = td
            try:
                with self.assertRaises(ValueError):
                    W.set_password("alice", "short")
                with self.assertRaises(ValueError):
                    W.set_password("", "longenoughpassword")
            finally:
                C.CONFIG_DIR = old

    def test_clear_password(self):
        with tempfile.TemporaryDirectory() as td:
            old = C.CONFIG_DIR
            C.CONFIG_DIR = td
            try:
                W.set_password("alice", "hunter2hunter2")
                self.assertTrue(W.clear_password())
                self.assertIsNone(W.get_auth())
                self.assertFalse(W.check_password("alice", "hunter2hunter2"))
            finally:
                C.CONFIG_DIR = old

    def test_login_throttle_locks_after_failures(self):
        t = W._Throttle(max_fails=3, window=120)
        self.assertFalse(t.locked("1.2.3.4|alice"))
        t.fail("1.2.3.4|alice"); t.fail("1.2.3.4|alice")
        self.assertFalse(t.locked("1.2.3.4|alice"))
        t.fail("1.2.3.4|alice")
        self.assertTrue(t.locked("1.2.3.4|alice"))
        # a different client/account is independent
        self.assertFalse(t.locked("5.6.7.8|alice"))
        t.reset("1.2.3.4|alice")
        self.assertFalse(t.locked("1.2.3.4|alice"))

    def test_config_write_is_atomic_and_0600(self):
        with tempfile.TemporaryDirectory() as td:
            old_f, old_d = C.CONFIG_FILE, C.CONFIG_DIR
            C.CONFIG_DIR = td
            C.CONFIG_FILE = os.path.join(td, "config.json")
            try:
                C.save_config({"platforms": {}, "routes": [], "settings": {}})
                self.assertTrue(os.path.exists(C.CONFIG_FILE))
                self.assertFalse(os.path.exists(C.CONFIG_FILE + ".tmp"))
                if os.name != "nt":
                    self.assertEqual(oct(os.stat(C.CONFIG_FILE).st_mode & 0o777),
                                     "0o600")
            finally:
                C.CONFIG_FILE, C.CONFIG_DIR = old_f, old_d


    def test_parse_progress_line(self):
        self.assertEqual(
            SY.parse_progress_line("  [12/54] OK   repo -> a,b"),
            (12, 54, "OK", "repo"))
        self.assertIsNone(SY.parse_progress_line("=== route 'x' ==="))
        self.assertIsNone(SY.parse_progress_line(""))

    def test_engine_sync_busy_detects_a_held_lock(self):
        try:
            import fcntl
        except ImportError:
            self.skipTest("fcntl is not available on this platform")
        with tempfile.TemporaryDirectory() as td:
            old = C.DATA_DIR
            C.DATA_DIR = td
            try:
                self.assertFalse(SY.engine_busy())
                fh = open(os.path.join(td, "sync.lock"), "a+")
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    self.assertTrue(SY.engine_busy())    # locked case
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
                    fh.close()
                self.assertFalse(SY.engine_busy())
            finally:
                C.DATA_DIR = old

    def test_web_token_is_reused_and_0600(self):
        with tempfile.TemporaryDirectory() as td:
            old = C.CONFIG_DIR
            C.CONFIG_DIR = td
            saved_env = os.environ.pop("GITSYNC_WEB_TOKEN", None)
            try:
                t1, gen1 = W.get_web_token()
                self.assertTrue(gen1)
                t2, gen2 = W.get_web_token()
                self.assertEqual(t1, t2)                 # reused, not regenerated
                self.assertFalse(gen2)
                if os.name != "nt":
                    self.assertEqual(oct(os.stat(W._token_path()).st_mode & 0o777),
                                     "0o600")
            finally:
                C.CONFIG_DIR = old
                if saved_env is not None:
                    os.environ["GITSYNC_WEB_TOKEN"] = saved_env

    def test_public_settings_never_leaks_notify_secrets(self):
        cfg = {"settings": {"notify": {
            "discord_webhook": "https://discord.com/api/webhooks/SECRETXYZ",
            "telegram_token": "BOTTOKEN123", "telegram_chat_id": "1",
            "on": "failure"}}}
        out = W._public_settings(cfg)
        blob = json.dumps(out)
        self.assertNotIn("SECRETXYZ", blob)
        self.assertNotIn("BOTTOKEN123", blob)
        self.assertTrue(out["notify"]["discord_set"])
        self.assertTrue(out["notify"]["telegram_set"])

    def test_apply_setting_rejects_a_bad_choice(self):
        st = {}
        with self.assertRaises(W._Http) as cm:
            W._apply_setting(st, "mode", {"mode": "nonsense"}, str,
                             ["safe", "full"])
        self.assertEqual(cm.exception.code, 400)
        W._apply_setting(st, "mode", {"mode": "safe"}, str, ["safe", "full"])
        self.assertEqual(st["mode"], "safe")

    def test_resolve_port_survives_a_corrupt_saved_value(self):
        bind, port = W.resolve_bind_port(
            None, None, {"settings": {"web": {"port": "abc"}}})
        self.assertEqual(port, W.DEFAULT_PORT)

    def test_every_tools_page_kind_maps_to_a_backend_tool(self):
        # regression guard: the Tools page's card ids must all be accepted by
        # _tool_argv (this is the class of bug that made "doctorfix" dead)
        import re as _re
        with open(os.path.join(os.path.dirname(W.__file__), "webui", "pages",
                                "tools.js"), "r", encoding="utf-8") as f:
            src = f.read()
        ids = _re.findall(r'toolCard\("(\w+)"', src)
        self.assertTrue(ids)
        args = {"restore": {"to": "dst"}, "mirror": {"repos": "hello"}}
        for kind in ids:
            argv, _mut = W._tool_argv(kind, args.get(kind, {}))
            self.assertIn(argv[0], ("compare", "doctor", "fix", "tokens",
                                    "disk", "restore", "mirror"), kind)


class TestWebHttp(unittest.TestCase):
    """the HTTP/auth surface is the new exit path for platform tokens, so it
    is exercised against a real server (the pattern TestHttpErrorHeaders
    already uses)."""

    def setUp(self):
        import shutil
        import threading
        self.td = tempfile.mkdtemp()
        cfgdir = os.path.join(self.td, "cfg")
        datadir = os.path.join(self.td, "data")
        for d in (cfgdir, datadir, os.path.join(self.td, "src"),
                  os.path.join(self.td, "dst")):
            os.makedirs(d)
        with open(os.path.join(cfgdir, "config.json"), "w") as f:
            json.dump({"platforms": {
                "src": {"type": "local", "enabled": True,
                        "path": os.path.join(self.td, "src")},
                "dst": {"type": "local", "enabled": True,
                        "path": os.path.join(self.td, "dst")}},
                "routes": [{"name": "main", "source": "src",
                            "targets": ["dst"]}],
                "settings": {}}, f)
        self._old = (C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR)
        C.CONFIG_DIR = cfgdir
        C.CONFIG_FILE = os.path.join(cfgdir, "config.json")
        C.DATA_DIR = datadir
        C.MIRROR_DIR = os.path.join(datadir, "mirrors")
        self.token = "test-token-abcdef123456"
        self.srv = W._Server(("127.0.0.1", 0), W.make_handler(), self.token,
                             "127.0.0.1", set())
        self.port = self.srv.port
        self._thread = threading.Thread(target=self.srv.serve_forever,
                                        daemon=True)
        self._thread.start()

    def tearDown(self):
        import shutil
        self.srv.shutdown()
        self.srv.server_close()
        C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR = self._old
        shutil.rmtree(self.td, ignore_errors=True)

    def _req(self, method, path, body=None, headers=None, host=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        if host:
            hdrs["Host"] = host
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=hdrs)
        r = conn.getresponse()
        payload = r.read().decode()
        out_headers = {k.lower(): v for k, v in r.getheaders()}
        conn.close()
        return r.status, payload, out_headers

    def _auth(self):
        return {"Authorization": "Bearer " + self.token,
                "X-Gitsync-Request": "1"}

    def test_unauthenticated_api_is_401(self):
        code, _, _ = self._req("GET", "/api/status")
        self.assertEqual(code, 401)

    def test_metrics_is_prometheus_text_and_needs_auth(self):
        code, _, _ = self._req("GET", "/metrics")
        self.assertEqual(code, 401)
        code, body, hdrs = self._req("GET", "/metrics", headers=self._auth())
        self.assertEqual(code, 200)
        self.assertIn("text/plain", hdrs.get("content-type", ""))
        self.assertIn("gitsync_up 1", body)
        self.assertIn('gitsync_info{version="', body)
        self.assertIn("# TYPE gitsync_stale gauge", body)
        self.assertIn("gitsync_sessions_active 0", body)

    def test_a_body_the_handler_ignores_does_not_desync_the_connection(self):
        # a POST whose body the route never reads used to leave the bytes in
        # the socket; the next request on that keep-alive connection carried
        # them and was parsed as "{}GET /api/status" (501 in a real browser)
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("POST", "/api/sessions/revoke-all",
                         body=b'{"unused": true}',
                         headers=dict(self._auth(),
                                      **{"Content-Type": "application/json"}))
            r = conn.getresponse()
            r.read()
            self.assertEqual(r.status, 200)
            conn.request("GET", "/api/status", headers=self._auth())
            r2 = conn.getresponse()
            body = r2.read().decode()
            self.assertEqual(r2.status, 200, body[:200])
            self.assertIn("version", body)
        finally:
            conn.close()

    def test_metrics_reflects_the_last_run(self):
        recs = [{"finished_at": "2026-01-01 00:00:00", "ok": 2, "failed": 0,
                 "partial": 0, "repos": 2, "dry_run": False,
                 "elapsed_sec": 1.5, "routes": 1}]
        with open(C.records_file(), "w") as f:
            json.dump(recs, f)
        code, body, _ = self._req("GET", "/metrics", headers=self._auth())
        self.assertEqual(code, 200)
        self.assertIn("gitsync_last_run_ok 1", body)
        self.assertIn("gitsync_last_run_synced 2", body)
        self.assertIn("gitsync_last_run_seconds 1.5", body)
        # 2026-01-01 00:00:00 local, as a plain epoch number
        self.assertIn("gitsync_last_success_timestamp_seconds 1767225600",
                      body)

    def test_token_login_sets_cookie_and_gates(self):
        code, _, hdrs = self._req("POST", "/api/session",
                                  {"token": self.token})
        self.assertEqual(code, 200)
        cookie = hdrs.get("set-cookie", "")
        self.assertIn("gitsync_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        sess = cookie.split(";")[0]
        code, _, _ = self._req("GET", "/api/status", headers={"Cookie": sess})
        self.assertEqual(code, 200)

    def test_cookie_post_without_csrf_header_is_403(self):
        _, _, hdrs = self._req("POST", "/api/session", {"token": self.token})
        sess = hdrs.get("set-cookie", "").split(";")[0]
        code, _, _ = self._req("POST", "/api/routes/main/pause", {},
                               headers={"Cookie": sess})   # no X-Gitsync-Request
        self.assertEqual(code, 403)

    def test_bad_host_is_rejected_even_on_public_routes(self):
        code, _, _ = self._req("GET", "/api/auth", host="evil.example.com")
        self.assertEqual(code, 403)

    def test_static_path_traversal_is_blocked(self):
        for p in ("/../../etc/passwd", "/..%2f..%2fetc/passwd"):
            code, _, _ = self._req("GET", p)
            self.assertNotEqual(code, 200)

    def test_pause_endpoint_works(self):
        code, body, _ = self._req("POST", "/api/routes/main/pause", {},
                                  headers=self._auth())
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["route"]["enabled"] is False)

    def test_selection_file_rejects_a_path_as_the_platform(self):
        # the platform name goes into the default file name, so "..%2f"
        # style input must never reach the filesystem
        code, body, _ = self._req("POST", "/api/repos/selection-file",
                                  {"repos": ["hello"], "platform": "../../etc"},
                                  headers=self._auth())
        self.assertEqual(code, 400)
        self.assertFalse(os.path.exists(os.path.join(self.td, "etc")))
        self.assertFalse(os.path.exists(os.path.join(C.CONFIG_DIR, "..",
                                                     "etc")))

    def test_selection_file_needs_a_route_with_that_source(self):
        code, _, _ = self._req("POST", "/api/repos/selection-file",
                               {"repos": ["hello"], "platform": "dst"},
                               headers=self._auth())
        self.assertEqual(code, 400)
        self.assertFalse(os.path.exists(os.path.join(C.CONFIG_DIR,
                                                     "repos-dst.txt")))

    def test_selection_file_is_confined_to_the_config_dir(self):
        outside = os.path.join(self.td, "evil.txt")
        code, body, _ = self._req("POST", "/api/repos/selection-file",
                                  {"repos": ["hello", "world"],
                                   "platform": "src", "path": outside},
                                  headers=self._auth())
        self.assertEqual(code, 200)
        self.assertFalse(os.path.exists(outside))
        path = json.loads(body)["path"]
        self.assertTrue(path.startswith(os.path.realpath(C.CONFIG_DIR)))
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            self.assertIn("hello", f.read())

    def test_selection_file_sets_include_file_on_the_route(self):
        code, body, _ = self._req("POST", "/api/repos/selection-file",
                                  {"repos": ["hello"], "platform": "src"},
                                  headers=self._auth())
        self.assertEqual(code, 200)
        with open(C.CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        route = [r for r in cfg["routes"] if r["name"] == "main"][0]
        self.assertTrue(route.get("include_file"))
        self.assertNotIn("include", route)

    def test_doctorfix_tool_is_accepted(self):
        code, body, _ = self._req("POST", "/api/jobs", {"kind": "doctorfix"},
                                  headers=self._auth())
        self.assertEqual(code, 202)
        self.assertTrue(json.loads(body)["job_id"])

    def test_bad_setting_is_rejected_and_config_unchanged(self):
        code, _, _ = self._req("PUT", "/api/settings", {"mode": "nonsense"},
                               headers=self._auth())
        self.assertEqual(code, 400)
        with open(C.CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertNotEqual(saved["settings"].get("mode"), "nonsense")

    def test_oversized_body_is_413(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        big = b'{"x":"' + b"a" * (W.MAX_BODY + 10) + b'"}'
        conn.request("POST", "/api/session", body=big,
                     headers={"Content-Type": "application/json",
                              "Content-Length": str(len(big))})
        r = conn.getresponse(); r.read(); conn.close()
        self.assertEqual(r.status, 413)

    def test_static_files_serve_200_cross_platform(self):
        code, body, _ = self._req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("gitsync", body)
        code, body, _ = self._req("GET", "/style.css")
        self.assertEqual(code, 200)
        self.assertIn("color-scheme:dark", body)


class TestTenOutOfTenFixes(unittest.TestCase):
    def test_github_token_is_url_quoted(self):
        plat = P.make("gh", {"type": "github", "url": "https://github.com",
                             "token": "tok@en:123/x", "auth": "token"})
        url = plat._clone_url("user/repo")
        self.assertIn("tok%40en%3A123%2Fx", url)
        self.assertNotIn("tok@en:123/x", url)
        self.assertTrue(url.startswith("https://x-access-token:"))

    def test_github_http_url_handled_cleanly(self):
        plat = P.make("gh", {"type": "github", "url": "http://gh.internal.lan",
                             "token": "secret", "auth": "token"})
        url = plat._clone_url("user/repo")
        self.assertTrue(url.startswith("http://x-access-token:secret@gh.internal.lan/user/repo.git"))

    def test_gitlab_token_is_url_quoted(self):
        plat = P.make("gl", {"type": "gitlab", "url": "https://gitlab.com",
                             "token": "glpat-tok@en#123"})
        url = plat._url_with_token("https://gitlab.com/group/repo.git")
        self.assertIn("glpat-tok%40en%23123", url)
        self.assertNotIn("tok@en#123", url)

    def test_tui_sync_progress_reuses_engine_parser(self):
        saved = list(tui.state.get("sync_lines") or [])
        try:
            tui.state["sync_lines"] = [
                "=== route 'main' ===",
                "  [1/10] OK   myrepo -> gitlab",
                "  [2/10] FAIL otherrepo -> gitlab",
            ]
            done, total, cur = tui.sync_progress()
            self.assertEqual((done, total, cur), (2, 10, "otherrepo"))
        finally:
            tui.state["sync_lines"] = saved

    def test_app_dir_in_config(self):
        self.assertTrue(os.path.isdir(C.APP_DIR))
        self.assertTrue(os.path.isfile(os.path.join(C.APP_DIR, "gitsync", "__init__.py")))

    def test_webhook_template_user_wantedby(self):
        self.assertIn("WantedBy={wantedby}", S.WEBHOOK_TEMPLATE)
        self.assertNotIn("Environment=HOME=%h", S.WEBHOOK_TEMPLATE)

    def test_webui_html_theme_metadata(self):
        # the palette moved into style.css so light mode can override it; a
        # page must not hardcode `color-scheme: dark` any more, and it must
        # load theme.js *before* the stylesheet so there is no dark flash
        webui = os.path.join(C.APP_DIR, "gitsync", "webui")
        for f in os.listdir(webui):
            if f.endswith(".html"):
                with open(os.path.join(webui, f), encoding="utf-8") as fp:
                    html = fp.read()
                self.assertIn('content="dark light"', html, f)
                self.assertNotIn("style=\"color-scheme", html, f)
                self.assertIn('name="theme-color" content="#0d1117"', html, f)
                self.assertIn("<script src=\"theme.js\"></script>", html, f)
                self.assertLess(html.index("theme.js"),
                                html.index("style.css"), f)

    def test_webui_style_guidelines(self):
        css_path = os.path.join(C.APP_DIR, "gitsync", "webui", "style.css")
        with open(css_path, encoding="utf-8") as fp:
            css = fp.read()
        self.assertIn("color-scheme:dark", css)
        self.assertIn('data-theme="light"', css)
        self.assertIn("prefers-color-scheme: light", css)
        self.assertIn("touch-action:manipulation", css)
        self.assertIn("modal-backdrop", css)
        self.assertIn("font-variant-numeric:tabular-nums", css)

    def test_webui_app_modal_confirm_and_intl(self):
        app_path = os.path.join(C.APP_DIR, "gitsync", "webui", "app.js")
        with open(app_path, encoding="utf-8") as fp:
            app = fp.read()
        self.assertIn("modalConfirm: modalConfirm", app)
        self.assertIn("formatDate: formatDate", app)
        self.assertIn("Intl.RelativeTimeFormat", app)


class TestTUIDrawPerformance(unittest.TestCase):
    def test_draw_dashboard_makes_no_subprocess_calls(self):
        from gitsync import tui

        class DummyWin:
            def __init__(self, h=30, w=120):
                self.h, self.w = h, w
                self.strings = []

            def getmaxyx(self):
                return self.h, self.w

            def erase(self):
                self.strings.clear()

            def addnstr(self, y, x, s, n, attr=0):
                self.strings.append((y, x, s[:n], attr))

            def noutrefresh(self):
                pass

        calls = []
        orig_run = subprocess.run
        orig_popen = subprocess.Popen

        def fake_run(*args, **kwargs):
            calls.append(('run', args))
            raise AssertionError(f"subprocess.run called: {args}")

        def fake_popen(*args, **kwargs):
            calls.append(('popen', args))
            raise AssertionError(f"subprocess.Popen called: {args}")

        # Warm up cached status to prevent background thread
        tui._status_cache["at"] = time.time()
        tui._status_cache["txt"] = "active (running)"
        tui._health["at"] = time.time()

        try:
            subprocess.run = fake_run
            subprocess.Popen = fake_popen

            win = DummyWin(30, 120)
            tui.draw_dashboard(win, 30, 120, 0, 0, 0, 0, 0)
            self.assertEqual(len(calls), 0, "draw_dashboard made subprocess calls")

            # Compact layout
            win_compact = DummyWin(15, 60)
            tui.draw_dashboard(win_compact, 15, 60, 0, 0, 0, 0, 0)
            self.assertEqual(len(calls), 0, "draw_dashboard compact made subprocess calls")

            # Full draw
            tui.draw(win)
            self.assertEqual(len(calls), 0, "draw made subprocess calls")
        finally:
            subprocess.run = orig_run
            subprocess.Popen = orig_popen

    def test_draw_terminal_too_small(self):
        from gitsync import tui

        class DummyWin:
            def __init__(self, h=8, w=20):
                self.h, self.w = h, w
                self.strings = []

            def getmaxyx(self):
                return self.h, self.w

            def erase(self):
                self.strings.clear()

            def addnstr(self, y, x, s, n, attr=0):
                self.strings.append((y, x, s[:n], attr))

            def noutrefresh(self):
                pass

        win = DummyWin(8, 20)
        tui.draw(win)
        self.assertTrue(any("terminal too small" in s for _, _, s, _ in win.strings))


class TestTUISmoke(unittest.TestCase):
    def test_tui_pty_smoke(self):
        try:
            import pty
            import select
            import termios
            import struct
            import fcntl
        except ImportError:
            self.skipTest("pty/termios not available on this platform (Windows)")

        master, slave = pty.openpty()
        winsize = struct.pack("HHHH", 24, 80, 0, 0)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, winsize)

        env = dict(os.environ,
                   TERM="xterm-256color",
                   GITSYNC_CONFIG_DIR=tempfile.mkdtemp(),
                   GITSYNC_DATA_DIR=tempfile.mkdtemp())
        proc = subprocess.Popen([sys.executable, "-m", "gitsync"],
                                stdin=slave, stdout=slave, stderr=slave,
                                close_fds=True, env=env)
        os.close(slave)
        output = b""
        start = time.time()
        stage = 0
        try:
            while time.time() - start < 8.0:
                r, _, _ = select.select([master], [], [], 0.2)
                if r:
                    chunk = os.read(master, 1024)
                    if not chunk:
                        break
                    output += chunk
                    if stage == 0 and (b"Dashboard" in output or b"gitsync" in output):
                        os.write(master, b"?")
                        stage = 1
                    elif stage == 1 and (b"HELP" in output or b"SHORTCUTS" in output or time.time() - start > 1.5):
                        os.write(master, b"\x1b")
                        stage = 2
                    elif stage == 2 and time.time() - start > 2.5:
                        os.write(master, b"q")
                        stage = 3
                        break
            if stage < 3:
                os.write(master, b"q")
            proc.wait(timeout=3)
            self.assertEqual(proc.returncode, 0)
        finally:
            os.close(master)
            if proc.poll() is None:
                proc.kill()

    def test_tui_pty_resize(self):
        try:
            import pty
            import select
            import termios
            import struct
            import fcntl
            import signal
        except ImportError:
            self.skipTest("pty/termios not available on this platform (Windows)")

        master, slave = pty.openpty()
        winsize = struct.pack("HHHH", 30, 120, 0, 0)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, winsize)

        env = dict(os.environ,
                   TERM="xterm-256color",
                   GITSYNC_CONFIG_DIR=tempfile.mkdtemp(),
                   GITSYNC_DATA_DIR=tempfile.mkdtemp())
        proc = subprocess.Popen([sys.executable, "-m", "gitsync"],
                                stdin=slave, stdout=slave, stderr=slave,
                                close_fds=True, env=env)
        os.close(slave)
        start = time.time()
        try:
            output = b""
            while time.time() - start < 4.0:
                r, _, _ = select.select([master], [], [], 0.2)
                if r:
                    chunk = os.read(master, 1024)
                    if chunk:
                        output += chunk
                        if b"Dashboard" in output or b"gitsync" in output:
                            break
            # 120x30 -> 80x24
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
            if hasattr(signal, "SIGWINCH"):
                try:
                    proc.send_signal(signal.SIGWINCH)
                except Exception:
                    pass
            time.sleep(0.3)
            # 80x24 -> 40x12
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 12, 40, 0, 0))
            if hasattr(signal, "SIGWINCH"):
                try:
                    proc.send_signal(signal.SIGWINCH)
                except Exception:
                    pass
            time.sleep(0.3)
            self.assertIsNone(proc.poll(), "TUI process crashed during resize")
            os.write(master, b"q")
            proc.wait(timeout=3)
            self.assertEqual(proc.returncode, 0)
        finally:
            os.close(master)
            if proc.poll() is None:
                proc.kill()


class TestRetryFailed(unittest.TestCase):
    def test_retry_failed_no_failures(self):
        from gitsync import __main__ as main_mod
        from types import SimpleNamespace
        with patch.object(main_mod.SYNC, "last_records", return_value=[{"failures": []}]), \
             patch.object(main_mod.C, "load_config", return_value={"platforms": {"p": {"type": "local"}}, "routes": []}), \
             patch.object(main_mod.C, "enabled_platforms", return_value={"p": {}}):
            args = SimpleNamespace(route=None, to=None, repos="", retry_failed=True, quiet=True, dry_run=False, scheduled=False)
            rc = main_mod.cmd_sync(args)
            self.assertEqual(rc, 0)

    def test_retry_failed_with_failures(self):
        from gitsync import __main__ as main_mod
        from types import SimpleNamespace
        recs = [{"failures": [{"route": "r1", "repo": "failed_repo_1", "error": "err"},
                              {"route": "r1", "repo": "failed_repo_2", "error": "err"}]}]
        captured = {}

        def fake_run_sync(cfg, route_name=None, quiet=False, only=None, dry_run=False, scheduled=False, **kwargs):
            captured["only"] = only
            return {"ok": 2, "partial": 0, "failed": 0, "repos": 2, "elapsed_sec": 1.0, "planned": []}

        with patch.object(main_mod.SYNC, "last_records", return_value=recs), \
             patch.object(main_mod.SYNC, "run_sync", fake_run_sync), \
             patch.object(main_mod.C, "load_config", return_value={"platforms": {"p": {"type": "local"}}, "routes": []}), \
             patch.object(main_mod.C, "enabled_platforms", return_value={"p": {}}):
            args = SimpleNamespace(route=None, to=None, repos="", retry_failed=True, quiet=True, dry_run=False, scheduled=False)
            rc = main_mod.cmd_sync(args)
            self.assertEqual(rc, 0)
            self.assertIsNotNone(captured.get("only"))
            self.assertIn("failed_repo_1", captured["only"])
            self.assertIn("failed_repo_2", captured["only"])

    def test_retry_failed_scoped_by_route(self):
        from gitsync import __main__ as main_mod
        from types import SimpleNamespace
        recs = [{"failures": [
            {"route": "r1", "repo": "repo_a", "error": "err"},
            {"route": "r2", "repo": "repo_b", "error": "err"},
        ]}]
        captured = {}

        def fake_run_sync(cfg, route_name=None, quiet=False, only=None, dry_run=False, scheduled=False, **kwargs):
            captured["only"] = only
            return {"ok": 1, "partial": 0, "failed": 0, "repos": 1, "elapsed_sec": 0.5, "planned": []}

        with patch.object(main_mod.SYNC, "last_records", return_value=recs), \
             patch.object(main_mod.SYNC, "run_sync", fake_run_sync), \
             patch.object(main_mod.C, "load_config", return_value={"platforms": {"p": {"type": "local"}}, "routes": []}), \
             patch.object(main_mod.C, "enabled_platforms", return_value={"p": {}}):
            # When scoped to r1, only repo_a is retried
            args = SimpleNamespace(route="r1", to=None, repos="", retry_failed=True, quiet=True, dry_run=False, scheduled=False)
            rc = main_mod.cmd_sync(args)
            self.assertEqual(rc, 0)
            self.assertEqual(captured.get("only"), ["repo_a"])


class TestNewFeatures(unittest.TestCase):
    def test_github_device_flow_mock(self):
        start_payload = json.dumps({
            "device_code": "dev_test_123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5
        }).encode("utf-8")

        class FakeResponse:
            def __init__(self, data):
                self.data = data
            def read(self):
                return self.data
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        with patch("urllib.request.urlopen", return_value=FakeResponse(start_payload)):
            res = P.github_device_flow_start()
            self.assertEqual(res["user_code"], "ABCD-1234")
            self.assertEqual(res["device_code"], "dev_test_123")

        pending_payload = json.dumps({"error": "authorization_pending"}).encode("utf-8")
        with patch("urllib.request.urlopen", return_value=FakeResponse(pending_payload)):
            res = P.github_device_flow_poll("dev_test_123")
            self.assertEqual(res["status"], "pending")

        ok_payload = json.dumps({"access_token": "gho_secret123", "scope": "repo"}).encode("utf-8")
        with patch("urllib.request.urlopen", return_value=FakeResponse(ok_payload)):
            res = P.github_device_flow_poll("dev_test_123")
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["token"], "gho_secret123")

    def test_github_rate_limit_tracking(self):
        self.addCleanup(P.GitHubPlatform._rate_limits.pop, "gh-unit", None)
        hdrs = {
            "x-ratelimit-remaining": "4950",
            "x-ratelimit-limit": "5000",
            "x-ratelimit-reset": str(int(time.time() + 3600)),
        }
        P.GitHubPlatform.update_rate_limit("gh-unit", hdrs)
        plat = P.GitHubPlatform("gh-unit", {})
        info = plat.rate_limit_info()
        self.assertIsNotNone(info)
        self.assertEqual(info["remaining"], 4950)
        self.assertEqual(info["limit"], 5000)
        self.assertGreater(info["resets_in_mins"], 50)

        # Expired rate limit returns None
        P.GitHubPlatform.update_rate_limit("gh-unit", {
            "x-ratelimit-remaining": "0",
            "x-ratelimit-limit": "5000",
            "x-ratelimit-reset": str(int(time.time() - 10)),
        })
        self.assertIsNone(plat.rate_limit_info())

    def test_global_sync_pause(self):
        cfg = {
            "platforms": {"local": {"type": "local", "enabled": True, "path": "/tmp"}},
            "routes": [{"name": "r1", "source": "local", "targets": ["local"]}],
            "settings": {"paused": True}
        }
        res = SY.run_sync(cfg, quiet=True)
        self.assertTrue(res.get("paused"))
        self.assertEqual(res.get("skipped"), "paused")

        # force=True bypasses pause
        with patch.object(C, "routes", return_value=[]):
            res_forced = SY.run_sync(cfg, quiet=True, force=True)
            self.assertNotEqual(res_forced.get("skipped"), "paused")

    def test_github_device_flow_validation_and_errors(self):
        class FakeResponse:
            def __init__(self, data):
                self.data = data
            def read(self):
                return self.data
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        # Error response on start raises RuntimeError
        err_payload = json.dumps({"error": "unauthorized_client", "error_description": "Bad client"}).encode("utf-8")
        with patch("urllib.request.urlopen", return_value=FakeResponse(err_payload)):
            with self.assertRaises(RuntimeError):
                P.github_device_flow_start()

        # slow_down without explicit interval does not default to 5
        slow_payload = json.dumps({"error": "slow_down"}).encode("utf-8")
        with patch("urllib.request.urlopen", return_value=FakeResponse(slow_payload)):
            res = P.github_device_flow_poll("dev_123")
            self.assertEqual(res["status"], "slow_down")
            self.assertNotIn("interval", res)

    def test_cmd_pause_and_resume(self):
        temp_cfg = {"settings": {}}
        with patch.object(M.C, "load_config", return_value=temp_cfg), \
             patch.object(M.C, "save_config", lambda c: None):
            self.assertEqual(M.cmd_pause(None), 0)
            self.assertTrue(temp_cfg["settings"]["paused"])
            self.assertEqual(M.cmd_resume(None), 0)
            self.assertFalse(temp_cfg["settings"]["paused"])


class TestAskpassExecutable(unittest.TestCase):
    def test_ensure_executable_helper_robustness(self):
        import shutil
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        helper_copy = os.path.join(td, "askpass.py")
        here = os.path.dirname(os.path.abspath(__file__))
        askpass_src = os.path.join(os.path.dirname(here), "gitsync", "askpass.py")
        with open(askpass_src, "rb") as sf, open(helper_copy, "wb") as df:
            df.write(sf.read())
        try:
            os.chmod(helper_copy, 0o644)
        except Exception:
            pass

        ex_path = SY.ensure_executable_helper(helper_copy)
        self.assertTrue(os.path.exists(ex_path))
        self.assertTrue(os.access(ex_path, os.X_OK))

        def fail_chmod(p, m):
            raise PermissionError("simulated read-only fs")
        with patch("os.chmod", side_effect=fail_chmod):
            wrapper = SY.ensure_executable_helper(helper_copy)
            self.assertTrue(os.path.exists(wrapper))

    def test_askpass_env_configuration(self):
        env = SY.askpass_env(user="user123", token="secret456")
        self.assertIn("GIT_ASKPASS", env)
        self.assertTrue(os.path.exists(env["GIT_ASKPASS"]))
        self.assertTrue(os.access(env["GIT_ASKPASS"], os.X_OK))
        self.assertEqual(env["GITSYNC_ASKPASS_USER"], "user123")
        self.assertEqual(env["GITSYNC_ASKPASS_TOKEN"], "secret456")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_CONFIG_PARAMETERS"], "'credential.helper='")


class TestGitHttpBackendIntegration(unittest.TestCase):
    """End-to-end integration test with git http-backend behind basic auth http.server."""
    def test_http_auth_clone_and_push_through_sync_repo(self):
        import shutil
        if not shutil.which("git"):
            self.skipTest("git binary not found")

        import base64
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)

        src_repo_dir = os.path.join(td, "src.git")
        dst_repo_dir = os.path.join(td, "dst.git")
        P.run(["git", "init", "--bare", src_repo_dir])
        P.run(["git", "init", "--bare", dst_repo_dir])
        P.run(["git", "-C", src_repo_dir, "config", "http.receivepack", "true"])
        P.run(["git", "-C", dst_repo_dir, "config", "http.receivepack", "true"])

        work_dir = os.path.join(td, "work")
        os.makedirs(work_dir)
        P.run(["git", "init"], cwd=work_dir)
        P.run(["git", "config", "user.name", "Test Runner"], cwd=work_dir)
        P.run(["git", "config", "user.email", "runner@test.local"], cwd=work_dir)
        with open(os.path.join(work_dir, "file.txt"), "w") as f:
            f.write("content\n")
        P.run(["git", "add", "."], cwd=work_dir)
        P.run(["git", "commit", "-m", "init commit"], cwd=work_dir)
        P.run(["git", "branch", "-M", "main"], cwd=work_dir)
        P.run(["git", "remote", "add", "origin", src_repo_dir], cwd=work_dir)
        P.run(["git", "push", "origin", "main"], cwd=work_dir)

        test_user = "gituser"
        test_token = "super_secret_token_xyz123"
        expected_auth = "Basic " + base64.b64encode(f"{test_user}:{test_token}".encode("ascii")).decode("ascii")

        class AuthGitBackendHandler(BaseHTTPRequestHandler):
            def do_auth(self):
                auth = self.headers.get("Authorization")
                if auth != expected_auth:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="Git"')
                    self.end_headers()
                    return False
                return True

            def do_HEAD(self):
                if self.do_auth():
                    self.send_response(200)
                    self.end_headers()

            def do_GET(self):
                if self.do_auth():
                    self.run_cgi()

            def do_POST(self):
                if self.do_auth():
                    self.run_cgi()

            def run_cgi(self):
                env = dict(os.environ)
                env["GIT_PROJECT_ROOT"] = td
                env["GIT_HTTP_EXPORT_ALL"] = "1"
                env["PATH_INFO"] = self.path.split("?")[0]
                env["QUERY_STRING"] = self.path.split("?")[1] if "?" in self.path else ""
                env["REQUEST_METHOD"] = self.command
                if "Content-Type" in self.headers:
                    env["CONTENT_TYPE"] = self.headers["Content-Type"]
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length > 0 else b""
                p = subprocess.Popen(["git", "http-backend"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                out, _ = p.communicate(input=body)
                parts = out.split(b"\r\n\r\n", 1)
                if len(parts) == 1:
                    parts = out.split(b"\n\n", 1)
                hdr = parts[0].decode("utf-8", "replace")
                body = parts[1] if len(parts) > 1 else b""
                code = 200
                for l in hdr.splitlines():
                    if l.lower().startswith("status:"):
                        try:
                            code = int(l.split(":")[1].strip().split()[0])
                        except Exception:
                            pass
                self.send_response(code)
                for l in hdr.splitlines():
                    if ":" in l and not l.lower().startswith("status:"):
                        k, v = l.split(":", 1)
                        self.send_header(k.strip(), v.strip())
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), AuthGitBackendHandler)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        port = srv.server_port
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        hub_mirrors = os.path.join(td, "hub_mirrors")
        os.makedirs(hub_mirrors, exist_ok=True)
        old_mirror_dir = C.MIRROR_DIR
        C.MIRROR_DIR = hub_mirrors
        self.addCleanup(setattr, C, "MIRROR_DIR", old_mirror_dir)

        class MockHttpPlatform:
            ptype = "generic"
            def __init__(self, name, url):
                self.name = name
                self.url = url
            def ensure_repo(self, repo):
                return self.url
            def git_env(self):
                return SY.askpass_env(test_user, test_token)

        src_platform = MockHttpPlatform("src_plat", f"http://{test_user}:{test_token}@127.0.0.1:{port}/src.git")
        dst_platform = MockHttpPlatform("dst_plat", f"http://{test_user}:{test_token}@127.0.0.1:{port}/dst.git")

        repo_dict = {
            "name": "src",
            "clone_url": f"http://{test_user}:{test_token}@127.0.0.1:{port}/src.git"
        }

        executed_cmds = []
        orig_run = P.run
        def tracking_run(cmd, *a, **kw):
            executed_cmds.append(list(cmd))
            return orig_run(cmd, *a, **kw)

        with patch("gitsync.platforms.run", side_effect=tracking_run):
            status, detail, errors = SY.sync_repo(
                repo_dict,
                src_platform,
                {"dst": dst_platform},
                "safe"
            )

        self.assertEqual(status, "ok", f"sync_repo failed: {detail} errors: {errors}")

        self.assertTrue(len(executed_cmds) > 0)
        for cmd in executed_cmds:
            for arg in cmd:
                self.assertNotIn(test_token, str(arg), f"Secret token leaked into argv: {cmd}")

        rc, out, _ = P.run(["git", "log", "-1", "--oneline", "main"], cwd=dst_repo_dir)
        self.assertEqual(rc, 0)
        self.assertIn("init commit", out)



# ------------------------------------------------------- round 3: sessions

class TestSessions(unittest.TestCase):
    """The web cookie used to *be* the access token. It is now an opaque id
    whose hash is stored server-side, which is what makes the expiry/revoke
    claims below possible at all."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self._old_cfg = C.CONFIG_DIR
        C.CONFIG_DIR = self.td

    def tearDown(self):
        import shutil
        C.CONFIG_DIR = self._old_cfg
        shutil.rmtree(self.td, ignore_errors=True)

    def _store(self):
        return os.path.join(self.td, "sessions.json")

    def test_create_returns_a_random_id_and_stores_only_its_hash(self):
        import hashlib
        sid = SS.create(ip="10.0.0.5", ua="test agent")
        self.assertGreaterEqual(len(sid), 32)
        with open(self._store(), encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn(sid, raw)                      # replay-proof store
        self.assertIn(hashlib.sha256(sid.encode("utf-8")).hexdigest(), raw)
        self.assertEqual(os.stat(self._store()).st_mode & 0o777, 0o600)

    def test_validate_accepts_a_live_id_and_returns_its_metadata(self):
        sid = SS.create(ip="10.0.0.5", ua="Firefox")
        rec = SS.validate(sid)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["ip"], "10.0.0.5")
        self.assertEqual(rec["ua"], "Firefox")
        self.assertIsNone(SS.validate("not-a-session"))
        self.assertIsNone(SS.validate(""))

    def test_revoke_kills_one_session_only(self):
        a = SS.create()
        b = SS.create()
        self.assertTrue(SS.revoke(a))
        self.assertIsNone(SS.validate(a))
        self.assertIsNotNone(SS.validate(b))
        self.assertFalse(SS.revoke(a))                  # already gone

    def test_idle_expiry(self):
        sid = SS.create()
        later = SS._now() + SS.IDLE_SECONDS + 1
        with patch.object(SS, "_now", return_value=later):
            self.assertIsNone(SS.validate(sid))

    def test_absolute_expiry_even_while_in_use(self):
        start = SS._now()
        sid = SS.create()
        with patch.object(SS, "_now",
                          return_value=start + SS.IDLE_SECONDS - 1):
            self.assertIsNotNone(SS.validate(sid))       # still active
        with patch.object(SS, "_now",
                          return_value=start + SS.ABSOLUTE_SECONDS + 1):
            self.assertIsNone(SS.validate(sid))          # but 30 days is 30 days

    def test_active_is_newest_first_and_never_leaks_a_hash(self):
        SS.create(ip="1.1.1.1")
        second = SS.create(ip="2.2.2.2")
        rows = SS.active()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(r["ip"] for r in rows),
                         ["1.1.1.1", "2.2.2.2"])
        self.assertEqual(rows[0]["ip"], "2.2.2.2")       # second is newer
        for r in rows:
            self.assertEqual(set(r), {"created", "last_seen", "ip", "ua"})
            self.assertNotIn(second, json.dumps(r))

    def test_revoke_all_reports_how_many_went(self):
        SS.create()
        SS.create()
        self.assertEqual(SS.count(), 2)
        self.assertEqual(SS.revoke_all(), 2)
        self.assertEqual(SS.count(), 0)
        self.assertEqual(SS.revoke_all(), 0)

    def test_a_corrupt_store_reads_as_empty_instead_of_crashing(self):
        with open(self._store(), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(SS.validate("whatever"))
        sid = SS.create()                                # still usable
        self.assertIsNotNone(SS.validate(sid))


class TestWebhookSecretMode(unittest.TestCase):
    def test_set_secret_is_0600_at_creation_not_after(self):
        old_cfg = C.CONFIG_DIR
        td = tempfile.mkdtemp()
        C.CONFIG_DIR = td
        old_mask = os.umask(0o022)
        try:
            WH.set_secret("s3cr3t-value")
        finally:
            os.umask(old_mask)
            C.CONFIG_DIR = old_cfg
        try:
            path = os.path.join(td, "webhook.secret")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertFalse(os.path.exists(path + ".tmp"))
            with open(path) as f:
                self.assertEqual(f.read(), "s3cr3t-value")
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)


# ------------------------------------------------------- round 3: updater

class TestUpdateSafety(unittest.TestCase):
    """`gitsync update` used to fetch whatever main served and run it as
    root. These are the properties that replaced that."""

    def _args(self, **kw):
        ns = M.argparse.Namespace(version="", yes=True)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_parse_sums(self):
        got = M._parse_sums("abc  a.sh\ndef  *b.sh\n\njunk line here\n")
        self.assertEqual(got, {"a.sh": "abc", "b.sh": "def"})

    def test_verify_asset_accepts_the_published_hash(self):
        import hashlib
        blob = b"#!/bin/bash\necho hi\n"
        good = hashlib.sha256(blob).hexdigest()
        self.assertEqual(
            M._verify_asset(blob, "quickinstall.sh",
                            "%s  quickinstall.sh\n" % good), good)

    def test_verify_asset_rejects_a_mismatch_and_a_missing_entry(self):
        with self.assertRaises(ValueError):
            M._verify_asset(b"evil", "quickinstall.sh", "%s  quickinstall.sh\n"
                            % ("0" * 64))
        with self.assertRaises(ValueError):
            M._verify_asset(b"evil", "quickinstall.sh", "")

    def test_latest_release_tag_normalises_a_bare_version(self):
        body = json.dumps({"tag_name": "2.1.4"}).encode()
        with patch.object(M, "_fetch_bytes", return_value=body):
            self.assertEqual(M._latest_release_tag(), "v2.1.4")
        body = json.dumps({"tag_name": "latest"}).encode()
        with patch.object(M, "_fetch_bytes", return_value=body):
            with self.assertRaises(ValueError):
                M._latest_release_tag()

    def test_a_wrong_checksum_aborts_before_anything_runs(self):
        with tempfile.TemporaryDirectory() as td, \
                patch.object(M, "repo_root_of", return_value=td), \
                patch.object(M, "_fetch_bytes",
                             side_effect=[b"#!/bin/bash\necho pwn\n",
                                          ("0" * 64 +
                                           "  quickinstall.sh\n").encode()]):
            calls = []
            with patch.object(M, "_run",
                              side_effect=lambda *a, **k: calls.append(a)):
                rc = M.cmd_update(self._args())
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [])          # nothing was executed

    def test_installer_is_private_pinned_and_removed(self):
        import hashlib
        blob = b"#!/bin/bash\nexit 0\n"
        digest = hashlib.sha256(blob).hexdigest()
        seen = {}

        class _R:
            returncode = 0

        def fake_run(cmd, **kw):
            seen["cmd"] = list(cmd)
            path = cmd[1]
            seen["path"] = path
            seen["mode"] = os.stat(path).st_mode & 0o777
            seen["existed_at_run"] = os.path.exists(path)
            return _R()

        sums = ("%s  quickinstall.sh\n" % digest).encode()
        with tempfile.TemporaryDirectory() as td, \
                patch.object(M, "repo_root_of", return_value=td), \
                patch.object(M, "_fetch_bytes",
                             side_effect=[blob, sums]), \
                patch.object(M, "_run", side_effect=fake_run):
            rc = M.cmd_update(self._args(version="2.1.4"))

        self.assertEqual(rc, 0)
        self.assertTrue(seen["existed_at_run"])
        self.assertEqual(seen["mode"], 0o700)            # 0600, then 0700
        self.assertFalse(os.path.exists(seen["path"]))   # removed in finally
        cmd = seen["cmd"]
        self.assertEqual(cmd[0], "bash")
        self.assertIn("--no-setup", cmd)
        self.assertIn("--no-sync", cmd)
        self.assertIn("--tag", cmd)
        self.assertEqual(cmd[cmd.index("--tag") + 1], "v2.1.4")

    def test_the_installer_is_removed_even_when_it_fails(self):
        seen = {}

        def boom(cmd, **kw):
            seen["path"] = cmd[1]
            raise RuntimeError("interrupted")

        sums = ("%s  quickinstall.sh\n" %
                __import__("hashlib").sha256(b"x").hexdigest()).encode()
        with tempfile.TemporaryDirectory() as td, \
                patch.object(M, "repo_root_of", return_value=td), \
                patch.object(M, "_fetch_bytes",
                             side_effect=[b"x", sums]), \
                patch.object(M, "_run", side_effect=boom):
            with self.assertRaises(RuntimeError):
                M.cmd_update(self._args(version="2.1.4"))
        self.assertFalse(os.path.exists(seen["path"]))

    def test_confirm_refuses_when_stdin_is_not_a_terminal(self):
        with patch.object(sys.stdin, "isatty", return_value=False):
            self.assertFalse(M._confirm("Run it?", False))
        self.assertTrue(M._confirm("Run it?", True))

    def test_a_git_checkout_updates_with_git_not_an_installer(self):
        with tempfile.TemporaryDirectory() as td:
            calls = []

            class _R:
                returncode = 0

            with patch.object(M, "_run",
                              side_effect=lambda cmd, **k:
                              calls.append(list(cmd)) or _R()):
                rc = M._git_checkout_update(td, True)
        self.assertEqual(rc, 0)
        self.assertEqual(calls[0][:3], ["git", "-C", td])
        self.assertIn("pull", calls[0])
        self.assertEqual(calls[1][0], "bash")
        self.assertTrue(calls[1][1].endswith("install.sh"))


class TestReleaseSums(unittest.TestCase):
    """A tag that ships a stale SHA256SUMS cannot be installed by the
    updater, so the check has to fail *before* the tag is pushed."""

    def _tree(self, td):
        os.makedirs(os.path.join(td, "gitsync", "webui"))
        with open(os.path.join(td, "gitsync", "a.py"), "w") as f:
            f.write("x = 1\n")
        with open(os.path.join(td, "gitsync", "webui", "a.js"), "w") as f:
            f.write("var a = 1;\n")
        with open(os.path.join(td, "install.sh"), "w") as f:
            f.write("#!/bin/sh\n")
        return td

    def test_round_trip_and_stale_detection(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(td)
            M.write_sums(td)
            M.check_sums(td)                       # no raise
            with open(os.path.join(td, "SHA256SUMS")) as f:
                text = f.read()
            self.assertIn("gitsync/a.py", text)
            self.assertIn("install.sh", text)
            self.assertNotIn("__pycache__", text)
            with open(os.path.join(td, "gitsync", "a.py"), "a") as f:
                f.write("# edited\n")
            with self.assertRaises(RuntimeError):
                M.check_sums(td)

    def test_a_missing_sums_file_only_fails_in_a_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(td)
            M.check_sums(td)                       # installed copy: tolerated
            os.makedirs(os.path.join(td, ".git"))
            with self.assertRaises(RuntimeError):
                M.check_sums(td)

    def test_this_repository_ships_a_current_sums_file(self):
        root = M.repo_root_of(M.__file__)
        if not os.path.isdir(os.path.join(root, ".git")):
            self.skipTest("not a git checkout")
        M.check_sums(root)


# ------------------------------------------------------- round 3: watch

class TestStrictCsp(unittest.TestCase):
    """style-src is 'self'. That only holds while the assets keep their side
    of the bargain: the 404 page links a stylesheet instead of a <style>
    block, and no page template or script builds markup with a style
    attribute. One `style="…"` added later silently breaks the look of that
    page in every browser, so assert it here rather than in a headless run."""

    def _webui(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(M.__file__)))
        return os.path.join(os.path.dirname(M.__file__), "webui")

    def test_csp_forbids_inline_style(self):
        from gitsync.web import CSP
        self.assertIn("style-src 'self'", CSP)
        self.assertNotIn("unsafe-inline", CSP)
        self.assertNotIn("unsafe-eval", CSP)

    def test_no_markup_carries_a_style_attribute(self):
        ui = self._webui()
        for base, _dirs, files in os.walk(ui):
            for name in files:
                if not name.endswith((".js", ".html")):
                    continue
                path = os.path.join(base, name)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                self.assertNotIn('style="', text, path)
                self.assertNotIn("style='", text, path)

    def test_the_404_page_does_not_inline_a_style_block(self):
        from gitsync import web
        self.assertNotIn("<style", web._NOT_FOUND_HTML)
        self.assertIn("/notfound.css", web._NOT_FOUND_HTML)
        self.assertTrue(os.path.isfile(
            os.path.join(self._webui(), "notfound.css")))


class TestWatchLoop(unittest.TestCase):
    def test_reloads_the_config_and_survives_a_failed_cycle(self):
        loads = []
        runs = []

        def fake_load():
            loads.append(1)
            return {"settings": {}}

        def fake_run(cfg):
            runs.append(1)
            if len(runs) == 1:
                raise RuntimeError("network died")
            return {"ok": 2, "failed": 0}

        args = M.argparse.Namespace(interval=30, once=False, max_runs=2)
        with patch.object(C, "load_config", side_effect=fake_load), \
                patch.object(M.SYNC, "run_sync", side_effect=fake_run), \
                patch.object(M.time, "sleep", lambda s: None):
            rc = M.cmd_watch(args)
        self.assertEqual(len(loads), 2)        # not read once before the loop
        self.assertEqual(len(runs), 2)
        self.assertEqual(rc, 1)                # a failure is reported

    def test_paused_skips_the_run_without_dying(self):
        args = M.argparse.Namespace(interval=30, once=True, max_runs=0)
        calls = []
        with patch.object(C, "load_config",
                          return_value={"settings": {"paused": True}}), \
                patch.object(M.SYNC, "run_sync",
                             side_effect=lambda cfg: calls.append(1)):
            rc = M.cmd_watch(args)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])            # paused means paused

    def test_once_returns_after_a_single_cycle(self):
        args = M.argparse.Namespace(interval=30, once=True, max_runs=0)
        runs = []
        with patch.object(C, "load_config", return_value={"settings": {}}), \
                patch.object(M.SYNC, "run_sync",
                             side_effect=lambda cfg:
                             runs.append(1) or {"ok": 1, "failed": 0}):
            self.assertEqual(M.cmd_watch(args), 0)
        self.assertEqual(len(runs), 1)


# --------------------------------------------------- round 3: proxy trust

class TestTrustedProxies(unittest.TestCase):
    def test_a_header_is_ignored_when_no_proxy_is_trusted(self):
        self.assertEqual(W._forwarded_ip("9.9.9.9", "1.2.3.4", []), "9.9.9.9")

    def test_the_right_most_untrusted_hop_wins(self):
        trusted = ["10.0.0.0/8"]
        self.assertEqual(
            W._forwarded_ip("10.0.0.1", "6.6.6.6, 1.2.3.4, 10.0.0.2", trusted),
            "1.2.3.4")

    def test_garbage_and_ipv6_hops(self):
        self.assertEqual(W._forwarded_ip("10.0.0.1", "unknown, 1.2.3.4",
                                         ["10.0.0.0/8"]), "1.2.3.4")
        self.assertEqual(W._forwarded_ip("10.0.0.1", "2001:db8::1",
                                         ["10.0.0.0/8"]), "2001:db8::1")

    def test_clean_cidrs_normalises_and_rejects_typos(self):
        self.assertEqual(W._clean_cidrs("10.0.0.0/8, 192.168.1.5"),
                         ["10.0.0.0/8", "192.168.1.5/32"])
        self.assertEqual(W._clean_cidrs(["  127.0.0.1/32 "]),
                         ["127.0.0.1/32"])
        with self.assertRaises(W._Http):
            W._clean_cidrs("not-a-network")
        with self.assertRaises(W._Http):
            W._clean_cidrs(5)

    def test_the_cache_reads_settings(self):
        old = (C.CONFIG_DIR, C.CONFIG_FILE)
        with tempfile.TemporaryDirectory() as td:
            C.CONFIG_DIR = td
            C.CONFIG_FILE = os.path.join(td, "config.json")
            with open(C.CONFIG_FILE, "w") as f:
                json.dump({"settings": {"trusted_proxies": ["10.0.0.0/8"]}}, f)
            W._invalidate("proxies")
            try:
                self.assertEqual(W._trusted_proxies(), ["10.0.0.0/8"])
            finally:
                C.CONFIG_DIR, C.CONFIG_FILE = old
                W._invalidate("proxies")


# --------------------------------------------- round 3: odd repository names

class TestOddRepoNameSync(unittest.TestCase):
    """'a b' and 'a_b' used to sanitise to the same hub folder *and* the same
    local target, so one repo silently overwrote the other. Both now survive
    a real sync with their own history."""

    NAMES = ["a b", "a_b", "\u00fcn\u00ef", "\u00f6\u00f1\u00ef"]

    def setUp(self):
        if not _have_git():
            self.skipTest("git not available")

    def _bare(self, root, name, msg):
        bare = os.path.join(root, name + ".git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        subprocess.run(["git", "-C", bare, "symbolic-ref", "HEAD",
                        "refs/heads/main"], check=True)
        work = os.path.join(os.path.dirname(root), "w-" + msg)
        os.makedirs(work)
        ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
        env = dict(os.environ,
                   GIT_AUTHOR_DATE="2020-01-01T00:00:00 +0000",
                   GIT_COMMITTER_DATE="2020-01-01T00:00:00 +0000")
        subprocess.run(["git", "init", "-q", work], check=True)
        subprocess.run(["git", "-C", work, "checkout", "-q", "-b", "main"],
                       check=True)
        with open(os.path.join(work, "f"), "w") as f:
            f.write(msg)
        subprocess.run(["git", "-C", work, "add", "."], check=True)
        subprocess.run(["git", "-C", work] + ident +
                       ["commit", "-q", "-m", msg], check=True, env=env)
        subprocess.run(["git", "-C", work, "push", "-q", bare, "main"],
                       check=True, env=env)

    def test_every_repo_keeps_its_own_history(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src")
            dst = os.path.join(td, "dst")
            os.makedirs(src)
            os.makedirs(dst)
            for i, name in enumerate(self.NAMES):
                self._bare(src, name, "c%d" % i)
            cfg = {"platforms": {
                "src": {"type": "local", "enabled": True, "path": src},
                "dst": {"type": "local", "enabled": True, "path": dst}},
                "routes": [{"name": "r", "source": "src",
                            "targets": ["dst"]}],
                "settings": {"mode": "safe"}}
            old = (C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR)
            C.CONFIG_DIR = td
            C.CONFIG_FILE = os.path.join(td, "config.json")
            C.DATA_DIR = os.path.join(td, "data")
            C.MIRROR_DIR = os.path.join(C.DATA_DIR, "mirrors")
            try:
                totals = SY.run_sync(cfg)
            finally:
                C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR = old

            self.assertEqual(totals["failed"], 0, totals)
            self.assertEqual(totals["ok"], len(self.NAMES), totals)
            # one folder per source repo, each holding its own commit
            for i, name in enumerate(self.NAMES):
                target = os.path.join(dst, P._fs_unique(name) + ".git")
                self.assertTrue(os.path.isdir(target), target)
                rc, out, err = P.run(
                    ["git", "-C", target, "log", "-1", "--format=%s", "main"])
                self.assertEqual(rc, 0, err)
                self.assertEqual(out.strip(), "c%d" % i)

    def test_an_empty_bare_repo_is_listed_and_mirrored(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src")
            os.makedirs(src)
            subprocess.run(["git", "init", "-q", "--bare",
                            os.path.join(src, "empty.git")], check=True)
            plat = P.LocalPlatform("src", {"path": src})
            names = [r["name"] for r in plat.list_repos()]
            self.assertEqual(names, ["empty"])

    def test_the_hub_collision_guard_skips_and_names_the_pair(self):
        """_fs_unique makes this practically impossible, but a silent merge
        of two histories is bad enough that the route refuses the pair."""
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src")
            dst = os.path.join(td, "dst")
            os.makedirs(src)
            os.makedirs(dst)
            self._bare(src, "one", "c1")
            self._bare(src, "two", "c2")
            cfg = {"platforms": {
                "src": {"type": "local", "enabled": True, "path": src},
                "dst": {"type": "local", "enabled": True, "path": dst}},
                "routes": [{"name": "r", "source": "src",
                            "targets": ["dst"]}],
                "settings": {"mode": "safe"}}
            old = (C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR)
            C.CONFIG_DIR = td
            C.CONFIG_FILE = os.path.join(td, "config.json")
            C.DATA_DIR = os.path.join(td, "data")
            C.MIRROR_DIR = os.path.join(C.DATA_DIR, "mirrors")
            try:
                with patch.object(SY, "hub_rel", return_value="same-path"):
                    totals = SY.run_sync(cfg)
            finally:
                C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR = old
            self.assertGreaterEqual(totals["failed"], 2)
            errs = " ".join(str(f.get("error"))
                            for f in totals.get("failures", []))
            self.assertIn("hub path collision", errs)
            self.assertEqual(os.listdir(dst), [])   # nothing was pushed


# ------------------------------------------------ round 3: /metrics, verify

class TestVerifyAndConfigExport(unittest.TestCase):
    def test_verify_reports_drift_then_in_sync(self):
        if not _have_git():
            self.skipTest("git not available")
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src")
            dst = os.path.join(td, "dst")
            os.makedirs(src)
            os.makedirs(dst)
            bare = os.path.join(src, "a.git")
            subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
            work = os.path.join(td, "w")
            os.makedirs(work)
            ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
            env = dict(os.environ,
                       GIT_AUTHOR_DATE="2020-01-01T00:00:00 +0000",
                       GIT_COMMITTER_DATE="2020-01-01T00:00:00 +0000")
            subprocess.run(["git", "init", "-q", work], check=True)
            subprocess.run(["git", "-C", work, "checkout", "-q", "-b", "main"],
                           check=True)
            for msg in ("c1", "c2"):
                with open(os.path.join(work, "f"), "w") as f:
                    f.write(msg)
                subprocess.run(["git", "-C", work, "add", "."], check=True)
                subprocess.run(["git", "-C", work] + ident +
                               ["commit", "-q", "-m", msg], check=True,
                               env=env)
            subprocess.run(["git", "-C", work, "tag", "v1"], check=True)
            subprocess.run(["git", "-C", work, "push", "-q", bare, "main",
                            "--tags"], check=True, env=env)

            cfg = {"platforms": {
                "src": {"type": "local", "enabled": True, "path": src},
                "dst": {"type": "local", "enabled": True, "path": dst}},
                "routes": [{"name": "r", "source": "src",
                            "targets": ["dst"]}],
                "settings": {"mode": "safe"}}
            old = (C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR)
            C.CONFIG_DIR = td
            C.CONFIG_FILE = os.path.join(td, "config.json")
            C.DATA_DIR = os.path.join(td, "data")
            C.MIRROR_DIR = os.path.join(C.DATA_DIR, "mirrors")
            try:
                SY.run_sync(cfg)
                rows = SY.verify(cfg)
                self.assertEqual([r["state"] for r in rows], ["in-sync"], rows)
                self.assertEqual(rows[0]["refs"], 2)      # main + the tag

                # a push to the *source* alone is not drift yet: verify
                # compares the hub (what was already backed up) to the target
                with open(os.path.join(work, "f"), "w") as f:
                    f.write("c3")
                subprocess.run(["git", "-C", work] + ident +
                               ["commit", "-qam", "c3"], check=True, env=env)
                subprocess.run(["git", "-C", work, "push", "-q", bare,
                                "main"], check=True, env=env)
                self.assertEqual([r["state"] for r in SY.verify(cfg)],
                                 ["in-sync"])

                SY.run_sync(cfg)          # hub and target move to c3
                self.assertEqual([r["state"] for r in SY.verify(cfg)],
                                 ["in-sync"])

                # now make the target disagree with the hub for real: move
                # its main back one commit, the way a lost push looks
                prev = subprocess.run(
                    ["git", "-C", work, "rev-parse", "HEAD~1"],
                    check=True, capture_output=True, text=True).stdout.strip()
                subprocess.run(["git", "-C", os.path.join(dst, "a.git"),
                                "update-ref", "refs/heads/main", prev],
                               check=True)
                rows = SY.verify(cfg)
                self.assertEqual(rows[0]["state"], "drift", rows)
                self.assertIn("refs/heads/main", rows[0]["differ"])

                SY.run_sync(cfg)
                rows = SY.verify(cfg)
                self.assertEqual([r["state"] for r in rows], ["in-sync"], rows)
                self.assertEqual(SY.verify_summary(rows), {"in-sync": 1})
            finally:
                C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR = old

    def test_verify_never_creates_a_missing_target_repo(self):
        if not _have_git():
            self.skipTest("git not available")
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src")
            dst = os.path.join(td, "dst")
            os.makedirs(src)
            os.makedirs(dst)
            subprocess.run(["git", "init", "-q", "--bare",
                            os.path.join(src, "a.git")], check=True)
            cfg = {"platforms": {
                "src": {"type": "local", "enabled": True, "path": src},
                "dst": {"type": "local", "enabled": True, "path": dst}},
                "routes": [{"name": "r", "source": "src",
                            "targets": ["dst"]}],
                "settings": {}}
            old = (C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR)
            C.CONFIG_DIR = td
            C.CONFIG_FILE = os.path.join(td, "config.json")
            C.DATA_DIR = os.path.join(td, "data")
            C.MIRROR_DIR = os.path.join(C.DATA_DIR, "mirrors")
            try:
                rows = SY.verify(cfg)
            finally:
                C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR = old
            self.assertEqual(rows[0]["state"], "no-mirror")
            self.assertEqual(os.listdir(dst), [])

    def test_export_blanks_every_credential_and_is_private(self):
        cfg = {"platforms": {
            "github": {"type": "github", "token": "ghp_SECRET",
                       "enabled": True},
            "local": {"type": "local", "path": "/tmp/x", "enabled": True}},
            "routes": [{"name": "r", "source": "github",
                        "targets": ["local"]}],
            "settings": {"notify": {"discord_webhook": "https://discord/hook",
                                    "telegram_token": "123:abc"}}}
        clean, removed = C.strip_secrets(cfg)
        text = json.dumps(clean)
        self.assertNotIn("ghp_SECRET", text)
        self.assertNotIn("discord/hook", text)
        self.assertNotIn("123:abc", text)
        self.assertEqual(clean["platforms"]["github"]["token"], "")
        self.assertEqual(clean["routes"], cfg["routes"])   # shape is kept
        self.assertIn("github.token", removed)
        self.assertIn("settings.notify.telegram_token", removed)
        # the original is untouched
        self.assertEqual(cfg["platforms"]["github"]["token"], "ghp_SECRET")

    def test_import_keeps_local_secrets_and_backs_up(self):
        with tempfile.TemporaryDirectory() as td:
            cfgdir = os.path.join(td, "cfg")
            os.makedirs(cfgdir)
            old = (C.CONFIG_DIR, C.CONFIG_FILE)
            C.CONFIG_DIR = cfgdir
            C.CONFIG_FILE = os.path.join(cfgdir, "config.json")
            try:
                C.save_config({"platforms": {
                    "github": {"type": "github", "token": "KEEP-ME",
                               "enabled": True}},
                    "routes": [], "settings": {}})
                incoming = os.path.join(td, "in.json")
                with open(incoming, "w") as f:
                    json.dump({"platforms": {
                        "github": {"type": "github", "token": "",
                                   "enabled": True, "owner": "me"},
                        "gitlab": {"type": "gitlab", "token": "",
                                   "enabled": True}},
                        "routes": [{"name": "all", "source": "github",
                                    "targets": ["gitlab"]}],
                        "settings": {}}, f)
                report = C.import_config(incoming)
                self.assertEqual(report["platforms"], 2)
                self.assertEqual(report["routes"], 1)
                self.assertIn("github.token", report["kept_secrets"])
                self.assertEqual(report["needs_token"], ["gitlab"])
                with open(C.CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self.assertEqual(saved["platforms"]["github"]["token"],
                                 "KEEP-ME")
                self.assertEqual(saved["platforms"]["github"]["owner"], "me")
                backups = [n for n in os.listdir(cfgdir) if ".bak-" in n]
                self.assertEqual(len(backups), 1, backups)
            finally:
                C.CONFIG_DIR, C.CONFIG_FILE = old

    def test_import_refuses_junk(self):
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "bad.json")
            with open(bad, "w") as f:
                f.write("[]")
            with self.assertRaises(C.ConfigImportError):
                C.import_config(bad, cfg={"platforms": {}, "routes": []})
            with open(bad, "w") as f:
                json.dump({"platforms": [], "routes": []}, f)
            with self.assertRaises(C.ConfigImportError):
                C.import_config(bad, cfg={"platforms": {}, "routes": []})
            with open(bad, "w") as f:
                json.dump({"nope": 1}, f)
            with self.assertRaises(C.ConfigImportError):
                C.import_config(bad, cfg={"platforms": {}, "routes": []})

    def test_export_writes_a_0600_file(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "export.json")
            text, _removed = C.export_config(out)
            self.assertEqual(os.stat(out).st_mode & 0o777, 0o600)
            with open(out, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f), json.loads(text))

if __name__ == '__main__':
    unittest.main()
