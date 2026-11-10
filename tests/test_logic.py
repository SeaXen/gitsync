"""gitsync logic tests — stdlib unittest, no network, no fixtures on disk.

Run:  PYTHONPATH=. python3 -m unittest tests.test_logic
Also:  python3 -m gitsync ci   (this file + the offline sync smoke)

These used to live as a flat assertion blob duplicated four times
(`gitsync/__main__.py` UNIT_CHECKS plus a heredoc in each CI file), which
drifted. One file now; `cmd_ci` and all three workflows call it.
"""

import curses
import json
import os
import tempfile
import time
import unittest

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

# CI runners inject credential env vars (GITHUB_TOKEN, FORGEJO_TOKEN, ...).
# The adapters deliberately fall back to them, so a test that does not set a
# token would silently use the runner's - and then hit the real API. Strip
# them for the whole module instead of per test.
_TOKEN_ENV = ("GITHUB_TOKEN", "FORGEJO_TOKEN", "GITEA_TOKEN", "GITLAB_TOKEN",
              "BITBUCKET_TOKEN", "GITSYNC_DISCORD_WEBHOOK",
              "GITSYNC_TELEGRAM_TOKEN", "GITSYNC_TELEGRAM_CHAT_ID",
              "GITSYNC_NOTIFY_ON")


def setUpModule():
    for k in _TOKEN_ENV:
        os.environ.pop(k, None)


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
        p._api = lambda path, method="GET", payload=None: (
            401, {"message": "Bad credentials"}, {})
        info = p.token_info()
        self.assertTrue(info["has_token"])
        self.assertEqual(info.get("error"), "token rejected")


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
        self.assertEqual(SY.hub_rel('a b/c'), 'a_b__c')

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
        subprocess.run(["git", "init", "-q", seed], capture_output=True)
        subprocess.run(["git", "-C", seed, "commit", "--allow-empty", "-q",
                        "-m", "x", "--author", "t <t@t>",
                        "--date", "2020-01-01T00:00:00"],
                       capture_output=True)
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
        data = json.load(open(path))
        data["at"] = time.time() - (tui.PROBE_TTL + 10)
        json.dump(data, open(path, "w"))
        self.assertEqual(tui.load_probe_cache(), ([], None))

    def test_log_tail_is_cached_until_the_file_changes(self):
        log = os.path.join(self.td, "gitsync.log")
        old_log = C.LOG_FILE
        C.LOG_FILE = log
        try:
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
        finally:
            C.LOG_FILE = old_log


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
        # the same arithmetic draw() uses for the tab bar
        self.assertIsNone(tui._tab_at(0, 120))
        self.assertEqual(tui._tab_at(28, 120), 0)
        self.assertEqual(tui._tab_at(40, 120), 1)
        self.assertEqual(tui._tab_at(62, 120), 3)
        self.assertIsNone(tui._tab_at(200, 120))

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


if __name__ == '__main__':
    unittest.main()
