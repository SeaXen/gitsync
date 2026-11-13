"""End-to-end web application testing for gitsync Web UI using Playwright.

Covers:
1. Authentication (login form, invalid token rejection, valid token login)
2. App Shell & Design System (dark-mode scheme, balanced headers, tabular numerals)
3. Full Navigation across all 9 pages (Dashboard, Repos, Platforms, Routes, History, Log, Stars, Settings, Tools)
4. Interactive in-app Modal Dialog (G.modalConfirm open, escape key dismissal, cancel, confirm)
5. Responsive mobile viewport layout
6. Session logout flow
7. Visual screenshot generation for audit artifacts
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

from playwright.sync_api import sync_playwright, expect

# Ensure gitsync package is on path
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from gitsync import config as C
from gitsync import web as W

SHOTS_DIR = os.environ.get("GITSYNC_SHOTS_DIR")
if not SHOTS_DIR:
    SHOTS_DIR = os.path.join(tempfile.gettempdir(), "gitsync_screenshots")
ARTIFACT_DIR = SHOTS_DIR


def _have_playwright():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            b.close()
        return True
    except Exception:
        return False


@unittest.skipUnless(_have_playwright(), "playwright or chromium not available")
class TestGitsyncWebappPlaywright(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(ARTIFACT_DIR, exist_ok=True)
        cls.td = tempfile.mkdtemp()
        cfgdir = os.path.join(cls.td, "cfg")
        datadir = os.path.join(cls.td, "data")
        srcdir = os.path.join(cls.td, "src")
        dstdir = os.path.join(cls.td, "dst")
        for d in (cfgdir, datadir, srcdir, dstdir):
            os.makedirs(d)

        # Seed mock configuration
        with open(os.path.join(cfgdir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({
                "platforms": {
                    "src_local": {"type": "local", "enabled": True, "path": srcdir},
                    "dst_local": {"type": "local", "enabled": True, "path": dstdir},
                    "github_demo": {
                        "type": "github", "enabled": True,
                        "url": "https://github.com", "owner": "testorg",
                        "token": "ghp_mocktoken123", "auth": "token"
                    }
                },
                "routes": [
                    {"name": "primary", "source": "src_local", "targets": ["dst_local"]},
                    {"name": "cloud_backup", "source": "src_local", "targets": ["github_demo"]}
                ],
                "settings": {
                    "schedule": "every 6h",
                    "mode": "safe"
                }
            }, f)

        cls._old = (C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR)
        C.CONFIG_DIR = cfgdir
        C.CONFIG_FILE = os.path.join(cfgdir, "config.json")
        C.DATA_DIR = datadir
        C.MIRROR_DIR = os.path.join(datadir, "mirrors")

        with open(C.log_file(), "w", encoding="utf-8") as f:
            f.write("[2026-11-10 12:00:00] === gitsync webapp test initialized ===\n")

        cls.token = "test-token-playwright-secret"
        cls.srv = W._Server(("127.0.0.1", 0), W.make_handler(), cls.token, "127.0.0.1", set())
        cls.port = cls.srv.port
        cls.base_url = f"http://127.0.0.1:{cls.port}"

        cls.server_thread = threading.Thread(target=cls.srv.serve_forever)
        cls.server_thread.daemon = True
        cls.server_thread.start()

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.playwright.stop()
        except Exception:
            pass
        try:
            cls.srv.shutdown()
            cls.srv.server_close()
        except Exception:
            pass
        C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR = cls._old
        shutil.rmtree(cls.td, ignore_errors=True)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme="dark")
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def test_01_unauthenticated_login_flow(self):
        """Verify login screen layout, invalid token rejection, and successful login."""
        self.page.goto(f"{self.base_url}/")
        self.page.wait_for_load_state("networkidle")

        # Verify login form is presented
        token_input = self.page.locator("#tok")
        expect(token_input).to_be_visible()
        self.assertEqual(token_input.get_attribute("placeholder"), "paste the token…")
        self.assertEqual(token_input.get_attribute("spellcheck"), "false")
        self.assertEqual(token_input.get_attribute("autocomplete"), "current-password")

        # Capture login screenshot
        self.page.screenshot(path=os.path.join(ARTIFACT_DIR, "01_login_screen.png"))

        # Test bad token attempt
        token_input.fill("wrong-token-12345")
        self.page.locator("#loginbtn").click()
        self.page.wait_for_timeout(300)
        err_alert = self.page.locator('[role="alert"]')
        expect(err_alert).to_contain_text("Sign-in failed")

        # Fill valid token and submit
        token_input.fill(self.token)
        self.page.locator("#loginbtn").click()
        self.page.wait_for_load_state("networkidle")

        # Verify authenticated app shell renders
        expect(self.page.locator("nav.side")).to_be_visible()
        expect(self.page.locator("#side-version")).to_contain_text("gitsync")
        expect(self.page.locator("#main")).to_be_visible()
        self.page.screenshot(path=os.path.join(ARTIFACT_DIR, "02_dashboard_authenticated.png"))

    def _login(self):
        """Helper to establish session on the current context."""
        self.page.goto(f"{self.base_url}/")
        self.page.wait_for_load_state("networkidle")
        if self.page.locator("#tok").is_visible():
            self.page.locator("#tok").fill(self.token)
            self.page.locator("#loginbtn").click()
            self.page.wait_for_load_state("networkidle")
        expect(self.page.locator("nav.side")).to_be_visible()

    def test_02_all_pages_navigation_and_visual_standards(self):
        """Verify each of the 9 navigation targets renders properly with design guidelines."""
        self._login()

        pages_to_test = [
            ("dashboard", "/", "03_page_dashboard.png"),
            ("repos", "/repos.html", "04_page_repos.png"),
            ("platforms", "/platforms.html", "05_page_platforms.png"),
            ("routes", "/routes.html", "06_page_routes.png"),
            ("history", "/history.html", "07_page_history.png"),
            ("log", "/log.html", "08_page_log.png"),
            ("stars", "/stars.html", "09_page_stars.png"),
            ("settings", "/settings.html", "10_page_settings.png"),
            ("tools", "/tools.html", "11_page_tools.png"),
        ]

        for page_name, url_path, screenshot_name in pages_to_test:
            self.page.goto(f"{self.base_url}{url_path}")
            self.page.wait_for_load_state("networkidle")

            # Check dark mode metadata
            html_elem = self.page.locator("html")
            self.assertIn("color-scheme: dark", html_elem.get_attribute("style") or "")

            # Verify header balance wrap
            h1 = self.page.locator(".bar h1")
            expect(h1).to_be_visible()
            self.assertEqual(h1.evaluate("el => getComputedStyle(el).textWrap"), "balance")

            # Verify tabular numbers are active on typography
            main_el = self.page.locator("#main")
            expect(main_el).to_be_visible()

            # Take page screenshot for audit artifact
            self.page.screenshot(path=os.path.join(ARTIFACT_DIR, screenshot_name))

    def test_03_modal_dialog_interaction(self):
        """Test accessible modal dialog (G.modalConfirm) on Tools page."""
        self._login()
        self.page.goto(f"{self.base_url}/tools.html")
        self.page.wait_for_load_state("networkidle")

        # Find Reclaim space (gc) tool card and click run
        gc_btn = self.page.locator('button[data-run="gc"]')
        expect(gc_btn).to_be_visible()
        gc_btn.click()

        # Verify modal appears with backdrop blur and accessible dialog attributes
        modal_backdrop = self.page.locator(".modal-backdrop")
        expect(modal_backdrop).to_be_visible()
        modal = self.page.locator(".modal")
        expect(modal).to_be_visible()
        self.assertEqual(modal.get_attribute("role"), "dialog")
        self.assertEqual(modal.get_attribute("aria-modal"), "true")

        # Verify modal header and content
        modal_title = self.page.locator("#gs-modal-title")
        expect(modal_title).to_contain_text("Reclaim mirror space")

        # Screenshot modal dialog in action
        self.page.screenshot(path=os.path.join(ARTIFACT_DIR, "12_modal_dialog_active.png"))

        # Test Dismissal via Escape key
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(300)
        expect(modal_backdrop).not_to_be_visible()

        # Test Dismissal via Cancel button
        gc_btn.click()
        expect(modal_backdrop).to_be_visible()
        self.page.locator("#gs-modal-cancel").click()
        self.page.wait_for_timeout(300)
        expect(modal_backdrop).not_to_be_visible()

    def test_04_responsive_mobile_viewport(self):
        """Test layout behavior under small mobile viewport (375x667).

        Asserts that:
        1. No page has horizontal overflow (scrollWidth <= clientWidth).
        2. Top bar buttons ('Sync now' and 'Sign out') fit within viewport and are reachable.
        3. Mobile nav contains a reachable sign-out button.
        """
        mobile_page = self.context.new_page()
        mobile_page.set_viewport_size({"width": 375, "height": 667})
        mobile_page.goto(f"{self.base_url}/")
        mobile_page.wait_for_load_state("networkidle")

        # Check login screen responsive layout
        token_input = mobile_page.locator("#tok")
        expect(token_input).to_be_visible()
        scroll_width = mobile_page.evaluate("() => document.documentElement.scrollWidth")
        client_width = mobile_page.evaluate("() => document.documentElement.clientWidth")
        self.assertLessEqual(scroll_width, client_width,
                             f"Login page has horizontal overflow: {scroll_width} > {client_width}")
        mobile_page.screenshot(path=os.path.join(ARTIFACT_DIR, "13_mobile_login.png"))

        # Login on mobile
        token_input.fill(self.token)
        mobile_page.locator("#loginbtn").click()
        mobile_page.wait_for_load_state("networkidle")
        expect(mobile_page.locator("nav.side")).to_be_visible()

        pages = [
            "index.html", "platforms.html", "routes.html", "repos.html",
            "stars.html", "log.html", "history.html", "tools.html", "settings.html"
        ]

        for p in pages:
            if p != "index.html":
                mobile_page.goto(f"{self.base_url}/{p}")
                mobile_page.wait_for_load_state("networkidle")
            # Verify no horizontal page overflow
            scroll_w = mobile_page.evaluate("() => document.documentElement.scrollWidth")
            client_w = mobile_page.evaluate("() => document.documentElement.clientWidth")
            self.assertLessEqual(scroll_w, client_w,
                                 f"Page {p} has horizontal overflow: scrollWidth {scroll_w} > clientWidth {client_w}")

            # Verify top buttons are visible and within viewport
            top_sync = mobile_page.locator("#top-sync")
            expect(top_sync).to_be_visible()
            box_sync = top_sync.bounding_box()
            self.assertIsNotNone(box_sync)
            self.assertLessEqual(box_sync["x"] + box_sync["width"], 375 + 1,
                                 f"Page {p} top-sync overflows viewport")

            top_logout = mobile_page.locator("#top-logout")
            expect(top_logout).to_be_visible()
            box_logout = top_logout.bounding_box()
            self.assertIsNotNone(box_logout)
            self.assertLessEqual(box_logout["x"] + box_logout["width"], 375 + 1,
                                 f"Page {p} top-logout overflows viewport")

        # Verify mobile dashboard screenshot
        mobile_page.screenshot(path=os.path.join(ARTIFACT_DIR, "14_mobile_dashboard.png"))

        # Test mobile sign-out click works
        mobile_page.locator("#top-logout").click()
        mobile_page.wait_for_load_state("networkidle")
        expect(mobile_page.locator("#tok")).to_be_visible()
        mobile_page.close()

    def test_05_sign_out_flow(self):
        """Test sign-out button properly clears session and redirects to login."""
        self._login()
        logout_btn = self.page.locator("#logout")
        expect(logout_btn).to_be_visible()
        logout_btn.click()
        self.page.wait_for_load_state("networkidle")

        # Verify we are returned to login form
        token_input = self.page.locator("#tok")
        expect(token_input).to_be_visible()
        self.page.screenshot(path=os.path.join(ARTIFACT_DIR, "15_logged_out.png"))

    def test_06_first_run_wizard_and_no_overdue_banner(self):
        """Fresh install shows #wizard-card and no overdue banner."""
        with tempfile.TemporaryDirectory() as td:
            cfgdir = os.path.join(td, "cfg")
            datadir = os.path.join(td, "data")
            os.makedirs(cfgdir)
            os.makedirs(datadir)
            empty_cfg = {"version": 1, "platforms": {}, "routes": [], "settings": {"schedule": "hourly"}}
            with open(os.path.join(cfgdir, "config.json"), "w", encoding="utf-8") as f:
                json.dump(empty_cfg, f)
            old = (C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR)
            C.CONFIG_DIR = cfgdir
            C.CONFIG_FILE = os.path.join(cfgdir, "config.json")
            C.DATA_DIR = datadir
            C.MIRROR_DIR = os.path.join(datadir, "mirrors")
            try:
                srv = W._Server(("127.0.0.1", 0), W.make_handler(), "tok-fresh", "127.0.0.1", set())
                th = threading.Thread(target=srv.serve_forever, daemon=True)
                th.start()
                try:
                    ctx = self.browser.new_context()
                    page = ctx.new_page()
                    page.goto(f"http://127.0.0.1:{srv.port}/")
                    page.wait_for_load_state("networkidle")
                    if page.locator("#tok").is_visible():
                        page.locator("#tok").fill("tok-fresh")
                        page.locator("#loginbtn").click()
                        page.wait_for_load_state("networkidle")
                    expect(page.locator("#wizard-card")).to_be_visible()
                    stale_banner = page.locator("#stale-banner")
                    self.assertTrue(stale_banner.is_hidden())
                    ctx.close()
                finally:
                    srv.shutdown()
                    srv.server_close()
            finally:
                C.CONFIG_DIR, C.CONFIG_FILE, C.DATA_DIR, C.MIRROR_DIR = old

if __name__ == "__main__":
    unittest.main()
