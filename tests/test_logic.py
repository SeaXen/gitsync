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
import unittest

from gitsync import config as C
from gitsync import notify as NT
from gitsync import platforms as P
from gitsync import diskutil as D
from gitsync import stars as ST
from gitsync import sync as SY
from gitsync import tokencheck as TK
from gitsync import tui
from gitsync import webhooks as WH


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
                         'https://git:a%2Fb@localhost:3002/sagar/repo.git')
        p2 = P.make('fj2', {'type': 'forgejo', 'url': 'https://codeberg.org',
                            'owner': 'me', 'token': 'tok'})
        self.assertEqual(p2._push_url('me', 'repo'),
                         'https://git:tok@codeberg.org/me/repo.git')
        # no token -> plain URL, ssh -> never embedded
        p3 = P.make('fj3', {'type': 'forgejo', 'url': 'http://h:3002',
                            'owner': 'me'})
        self.assertEqual(p3._push_url('me', 'repo'),
                         'https://h:3002/me/repo.git')

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
        # NOTE (P0-13): this locks in the current behaviour. `send` should
        # return False when it sent nothing; flip both together.
        self.assertTrue(NT.send({'settings': {'notify': {}}}, 't', [],
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


# ------------------------------------------------------------------- stars

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


# --------------------------------------------------------------------- TUI

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
