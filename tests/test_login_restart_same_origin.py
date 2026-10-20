"""Regression coverage for login redirect origin and setup restart gating."""

from __future__ import annotations

import unittest
from pathlib import Path

from starlette.requests import Request

from app.api.app import _panel_redirect


ROOT = Path(__file__).resolve().parents[1]


def _request(scheme: str = "http") -> Request:
    return Request(
        {
            "type": "http",
            "scheme": scheme,
            "server": ("example.test", 9000),
            "client": ("127.0.0.1", 50000),
            "http_version": "1.1",
            "method": "POST",
            "path": "/login",
            "raw_path": b"/login",
            "query_string": b"",
            "headers": [(b"host", b"example.test:9000")],
        }
    )


class LoginRedirectOriginTests(unittest.TestCase):
    def test_http_login_keeps_current_browser_origin(self):
        response = _panel_redirect(_request("http"), "/home")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/home")

    def test_https_login_keeps_current_browser_origin(self):
        response = _panel_redirect(_request("https"), "/pg")
        self.assertEqual(response.headers["location"], "/pg")

    def test_login_redirect_never_accepts_protocol_relative_target(self):
        response = _panel_redirect(_request(), "//example.evil/path")
        self.assertEqual(response.headers["location"], "/example.evil/path")

    def test_login_redirect_can_preserve_explicit_status(self):
        response = _panel_redirect(_request(), "/home", status_code=307)
        self.assertEqual(response.status_code, 307)


class LoginRestartGuardTests(unittest.TestCase):
    def test_restarting_page_disables_submit_until_health_recovers(self):
        html = (ROOT / "app/web/templates/login.html").read_text(encoding="utf-8")
        self.assertIn(
            "id=\"login-submit\" {% if request.query_params.get('restarting') == '1' %}disabled{% endif %}",
            html,
        )
        self.assertIn("submit.disabled = true", html)
        self.assertIn("healthy >= 2", html)
        self.assertIn("submit.disabled = false", html)
        self.assertIn("fetch('/health'", html)


if __name__ == "__main__":
    unittest.main()
