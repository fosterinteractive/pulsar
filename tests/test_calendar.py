"""Google Calendar (pulsar/calendar.py) on made-up data: Google's endpoints are faked, no network, no keychain.

    python3 -m unittest discover -s tests
"""

import base64
import hashlib
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from zoneinfo import ZoneInfo  # noqa: E402

from pulsar import calendar, config, server  # noqa: E402

TORONTO, KYIV = ZoneInfo("America/Toronto"), ZoneInfo("Europe/Kyiv")
CLIENT = {"installed": {"client_id": "123456789012-madeupclientid0000.apps.googleusercontent.com",
                        "client_secret": "made-up-test-secret-0000", "project_id": "made-up"}}
GRANTED = {"access_token": "ya29.made-up-access-1", "expires_in": 3599, "refresh_token": "1//made-up-refresh-token",
           "scope": calendar.SCOPE, "token_type": "Bearer"}


def timed(summary, start, end, **extra):
    return {"id": summary.lower().replace(" ", "-"), "status": "confirmed", "summary": summary, "eventType": "default",
            "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


class FakeGoogle:
    """Google's token, events and revoke endpoints: records every request and answers from a script."""

    def __init__(self):
        self.calls = []
        self.token = []        # answers for the token endpoint, in order; then GRANTED-like renewals
        self.events = []       # answers for the events list, in order; then the last one again
        self.default_events = (200, {"summary": "ada@example.com", "items": []})
        self.revoke = (200, {})
        self.renewals = 0

    def __call__(self, method, url, form=None, headers=None, timeout=30):
        self.calls.append({"method": method, "url": url, "form": dict(form or {}), "headers": dict(headers or {})})
        base = url.split("?")[0]
        if base == calendar.TOKEN_URL:
            if self.token:
                return self.token.pop(0)
            self.renewals += 1
            return 200, {"access_token": f"ya29.made-up-renewed-{self.renewals}", "expires_in": 3599,
                         "scope": calendar.SCOPE, "token_type": "Bearer"}
        if base == calendar.EVENTS_URL:
            return self.events.pop(0) if self.events else self.default_events
        if base == calendar.REVOKE_URL:
            return self.revoke
        raise AssertionError("a request to an address Pulsar never uses: " + base)

    def to(self, base):
        return [c for c in self.calls if c["url"].split("?")[0] == base]


class Isolated(unittest.TestCase):
    """Settings, the token and the Google client in a scratch folder; Google faked; nothing kept between tests."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.client_file = os.path.join(self.dir, "google_client.json")
        with open(self.client_file, "w", encoding="utf-8") as fh:
            json.dump(CLIENT, fh)
        self.google = FakeGoogle()
        self.opened = []
        self.patches = [mock.patch.object(config, "_keychain", lambda: False),
                        mock.patch.object(config, "CONFIG_DIR", self.dir),
                        mock.patch.object(config, "SETTINGS_FILE", os.path.join(self.dir, "settings.json")),
                        mock.patch.object(config, "GOOGLE_TOKEN_FILE", os.path.join(self.dir, "google-token")),
                        mock.patch.object(calendar, "CLIENT_FILE", self.client_file),
                        mock.patch.object(calendar, "http", self.google),
                        mock.patch.object(calendar, "open_browser", lambda url: self.opened.append(url) or True)]
        for p in self.patches:
            p.start()
        self.reset()
        config.update({"timezone": "America/Toronto"})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.reset()

    @staticmethod
    def reset():
        config._google_cache[2] = False
        calendar.forget()
        calendar._pending.clear()
        calendar._access.update(token=None, expires=0.0)

    def settings(self):
        return config.load()

    def begin(self, port=5610):
        url = calendar.start(port)
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        return url, q

    def connect(self):
        _, q = self.begin()
        self.google.token.append((200, dict(GRANTED)))
        result = calendar.finish({"state": q["state"], "code": "4/made-up-code"})
        self.assertTrue(result["ok"], result)
        self.google.calls.clear()
        return result


class ClientTest(Isolated):
    def test_placeholders_mean_not_set_up(self):
        with open(os.path.join(os.path.dirname(calendar.__file__), "google_client.json"), encoding="utf-8") as fh:
            shipped = json.load(fh)
        self.assertTrue({"client_id", "client_secret"} <= set(shipped["installed"]))  # the shape Google downloads
        placeholders = {"installed": {"client_id": "REPLACE_WITH_THE_CLIENT_ID.apps.googleusercontent.com",
                                      "client_secret": "REPLACE_WITH_THE_CLIENT_SECRET"}}
        for doc in (placeholders, {}, [], {"installed": "x"}):
            with open(self.client_file, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            self.assertIsNone(calendar.client(), doc)
            self.assertEqual(calendar.status(self.settings()), {"configured": False, "connected": False, "account": ""})
            with self.assertRaises(calendar.CalendarError):
                calendar.start(5610)
        got = calendar.day("2026-11-05", self.settings())
        self.assertEqual((got["connected"], got["events"]), (False, []))
        self.assertEqual(self.google.calls, [])

    def test_the_downloaded_file_is_read_as_it_comes(self):
        self.assertEqual(calendar.client(), {"id": CLIENT["installed"]["client_id"],
                                             "secret": CLIENT["installed"]["client_secret"]})


class SignInTest(Isolated):
    def test_the_sign_in_address(self):
        url, q = self.begin(5610)
        self.assertTrue(url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        self.assertEqual(q["redirect_uri"], "http://127.0.0.1:5610/oauth/google")
        self.assertEqual(q["scope"], "https://www.googleapis.com/auth/calendar.events.readonly")  # that one only
        self.assertEqual((q["response_type"], q["code_challenge_method"], q["access_type"], q["prompt"]),
                         ("code", "S256", "offline", "consent"))
        self.assertEqual(q["client_id"], CLIENT["installed"]["client_id"])
        verifier = calendar._pending[q["state"]]["verifier"]
        self.assertTrue(43 <= len(verifier) <= 128)
        expect = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(q["code_challenge"], expect)
        _, again = self.begin(5610)
        self.assertNotEqual(again["state"], q["state"])
        self.assertNotEqual(again["code_challenge"], q["code_challenge"])

    def test_a_good_answer_connects_once(self):
        _, q = self.begin()
        verifier = calendar._pending[q["state"]]["verifier"]
        self.google.token.append((200, dict(GRANTED)))
        result = calendar.finish({"state": q["state"], "code": "4/made-up-code"})
        self.assertTrue(result["ok"])
        self.assertIn("ada@example.com", result["message"])
        [exchange] = self.google.to(calendar.TOKEN_URL)
        self.assertEqual(exchange["form"], {"code": "4/made-up-code", "client_id": CLIENT["installed"]["client_id"],
                                            "client_secret": CLIENT["installed"]["client_secret"],
                                            "redirect_uri": q["redirect_uri"], "grant_type": "authorization_code",
                                            "code_verifier": verifier})
        self.assertEqual(config.get_google_token(), GRANTED["refresh_token"])
        self.assertEqual(stat.S_IMODE(os.stat(config.GOOGLE_TOKEN_FILE).st_mode), 0o600)
        self.assertEqual(calendar.status(self.settings()),
                         {"configured": True, "connected": True, "account": "ada@example.com"})
        self.assertNotIn("1//made-up-refresh-token", json.dumps(self.settings()))  # never in settings.json
        calls = len(self.google.calls)
        again = calendar.finish({"state": q["state"], "code": "4/made-up-code"})  # the same answer a second time
        self.assertEqual((again["ok"], again["known"]), (False, False))
        self.assertEqual(len(self.google.calls), calls)

    def test_an_unknown_or_late_state_does_nothing(self):
        _, q = self.begin()
        for bad in ("", "x" * 43, q["state"] + "x", None, ["a"]):
            result = calendar.finish({"state": bad, "code": "4/made-up-code"})
            self.assertEqual((result["ok"], result["known"]), (False, False), bad)
        later = time.time() + calendar.STATE_TTL + 1
        with mock.patch.object(calendar, "time", mock.Mock(time=lambda: later)):
            result = calendar.finish({"state": q["state"], "code": "4/made-up-code"})
        self.assertEqual((result["ok"], result["known"]), (False, False))
        self.assertEqual(self.google.calls, [])
        self.assertIsNone(config.get_google_token())

    def test_a_refusal_or_a_missing_permission_keeps_nothing(self):
        _, q = self.begin()
        result = calendar.finish({"state": q["state"], "error": "access_denied"})
        self.assertEqual((result["ok"], result["known"]), (False, True))
        self.assertIn("chose not to allow", result["message"])
        self.assertEqual(self.google.calls, [])
        _, q = self.begin()
        self.google.token.append((200, {**GRANTED, "scope": "openid"}))  # the calendar box left unticked
        self.assertFalse(calendar.finish({"state": q["state"], "code": "4/made-up-code"})["ok"])
        _, q = self.begin()
        self.google.token.append((400, {"error": "invalid_grant", "error_description": "Bad Request"}))
        result = calendar.finish({"state": q["state"], "code": "4/made-up-code"})
        self.assertIn("invalid_grant", result["message"])
        self.assertIsNone(config.get_google_token())


class ReadTest(Isolated):
    def test_the_request_for_a_day(self):
        self.connect()
        calendar.day("2026-11-05", self.settings())
        [listing] = self.google.to(calendar.EVENTS_URL)
        q = {k: v[0] for k, v in parse_qs(urlsplit(listing["url"]).query).items()}
        self.assertEqual((q["timeMin"], q["timeMax"]), ("2026-11-05T00:00:00-05:00", "2026-11-06T00:00:00-05:00"))
        self.assertEqual((q["singleEvents"], q["orderBy"]), ("true", "startTime"))
        for part in ("summary", "start", "end", "status", "eventType", "attendees(self,responseStatus)", "hangoutLink",
                     "conferenceData(entryPoints(entryPointType,uri))"):
            self.assertIn(part, q["fields"])
        self.assertEqual(listing["headers"]["Authorization"], "Bearer " + GRANTED["access_token"])
        self.assertEqual(self.google.to(calendar.TOKEN_URL), [])  # the access token in memory was still good

    def test_events_as_the_page_draws_them(self):
        items = [
            timed("Standup", "2026-11-05T09:30:00-05:00", "2026-11-05T10:00:00-05:00",
                  hangoutLink="https://meet.google.com/abc-defg-hij", recurringEventId="series-1",
                  htmlLink="https://www.google.com/calendar/event?eid=made-up"),
            timed("Client call", "2026-11-05T17:00:00Z", "2026-11-05T18:00:00Z",
                  conferenceData={"entryPoints": [{"entryPointType": "phone", "uri": "tel:+1-555-0100"},
                                                  {"entryPointType": "video", "uri": "https://example.zoom.us/j/1"}]}),
            timed("Review", "2026-11-05T14:00:00-05:00", "2026-11-05T14:30:00-05:00",
                  location="Join https://example.zoom.us/j/2?pwd=x.", htmlLink="javascript:alert(1)"),
            timed("Skipped", "2026-11-05T15:00:00-05:00", "2026-11-05T15:30:00-05:00",
                  attendees=[{"self": True, "responseStatus": "declined"}]),
            timed("Office", "2026-11-05T08:00:00-05:00", "2026-11-05T17:00:00-05:00", eventType="workingLocation"),
            timed("Away", "2026-11-05T11:00:00-05:00", "2026-11-05T12:00:00-05:00", eventType="outOfOffice"),
            timed("Called off", "2026-11-05T13:00:00-05:00", "2026-11-05T13:30:00-05:00", status="cancelled"),
            timed("Release", "2026-11-05T23:00:00-05:00", "2026-11-06T01:00:00-05:00"),
            {"id": "h", "summary": "Holiday", "start": {"date": "2026-11-05"}, "end": {"date": "2026-11-06"}},
            {"id": "c", "summary": "Conference", "start": {"date": "2026-11-04"}, "end": {"date": "2026-11-07"}},
            {"id": "n", "summary": "Tomorrow", "start": {"date": "2026-11-06"}, "end": {"date": "2026-11-07"}},
        ]
        got = calendar.events_on(items, "2026-11-05", TORONTO)
        self.assertEqual([(x["title"], x["s"], x["e"], x["all_day"]) for x in got], [
            ("Conference", 0, 1440, True), ("Holiday", 0, 1440, True), ("Standup", 570, 600, False),
            ("Client call", 720, 780, False), ("Review", 840, 870, False), ("Skipped", 900, 930, False),
            ("Release", 1380, 1440, False)])
        by = {x["title"]: x for x in got}
        self.assertEqual((by["Standup"]["url"], by["Standup"]["recurring"], by["Standup"]["uid"]),
                         ("https://meet.google.com/abc-defg-hij", True, "series-1"))
        self.assertTrue(by["Standup"]["link"].startswith("https://www.google.com/calendar/event"))
        self.assertEqual(by["Client call"]["url"], "https://example.zoom.us/j/1")
        self.assertEqual(by["Review"]["url"], "https://example.zoom.us/j/2?pwd=x")
        self.assertNotIn("link", by["Review"])  # only https links reach the page
        self.assertTrue(by["Skipped"]["declined"])
        self.assertFalse(by["Standup"]["declined"])
        self.assertEqual([(x["title"], x["s"], x["e"]) for x in calendar.events_on(items, "2026-11-06", TORONTO)
                          if not x["all_day"]], [("Release", 0, 60)])
        in_kyiv = {x["title"]: x["s"] for x in calendar.events_on(items, "2026-11-05", KYIV)}
        self.assertEqual(in_kyiv["Standup"], 990)  # 09:30 in Toronto is 16:30 in Kyiv

    def test_kept_five_minutes_and_fresh_asks_at_once(self):
        self.connect()
        self.google.default_events = (200, {"summary": "ada@example.com", "items": [
            timed("Standup", "2026-11-05T09:30:00-05:00", "2026-11-05T10:00:00-05:00")]})
        now = [time.time()]
        with mock.patch.object(calendar, "time", mock.Mock(time=lambda: now[0])):
            first = calendar.day("2026-11-05", self.settings())
            calendar.day("2026-11-05", self.settings())
            self.assertEqual(len(self.google.to(calendar.EVENTS_URL)), 1)
            calendar.day("2026-11-05", self.settings(), fresh=True)  # the ↻ button
            self.assertEqual(len(self.google.to(calendar.EVENTS_URL)), 2)
            now[0] += calendar.FRESH + 1
            calendar.day("2026-11-05", self.settings())
            self.assertEqual(len(self.google.to(calendar.EVENTS_URL)), 3)
            self.google.events.append((0, {"error": "Google took too long to answer"}))
            now[0] += calendar.FRESH + 1
            stale = calendar.day("2026-11-05", self.settings())
        self.assertEqual((first["connected"], first["account"], len(first["events"])), (True, "ada@example.com", 1))
        self.assertEqual(len(stale["events"]), 1)
        self.assertIn("Showing the meetings as read at", stale["problem"])
        self.google.events.append((403, {"error": {"code": 403, "errors": [{"reason": "accessNotConfigured"}]}}))
        got = calendar.day("2026-11-06", self.settings())
        self.assertEqual(got["events"], [])
        self.assertIn("API is not enabled", got["problem"])


class TokenTest(Isolated):
    def test_an_expired_access_token_is_renewed_from_the_refresh_token(self):
        self.connect()
        calendar._access["expires"] = 0.0
        calendar.day("2026-11-05", self.settings())
        [renew] = self.google.to(calendar.TOKEN_URL)
        self.assertEqual(renew["form"], {"grant_type": "refresh_token", "refresh_token": GRANTED["refresh_token"],
                                         "client_id": CLIENT["installed"]["client_id"],
                                         "client_secret": CLIENT["installed"]["client_secret"]})
        [listing] = self.google.to(calendar.EVENTS_URL)
        self.assertEqual(listing["headers"]["Authorization"], "Bearer ya29.made-up-renewed-1")

    def test_a_refused_access_token_is_renewed_once(self):
        self.connect()
        self.google.events.append((401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}}))
        got = calendar.day("2026-11-05", self.settings())
        self.assertEqual(got["problem"], None)
        self.assertEqual([c["headers"]["Authorization"] for c in self.google.to(calendar.EVENTS_URL)],
                         ["Bearer " + GRANTED["access_token"], "Bearer ya29.made-up-renewed-1"])

    def test_access_withdrawn_at_google_disconnects(self):
        self.connect()
        calendar._access["expires"] = 0.0
        self.google.token.append((400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
        got = calendar.day("2026-11-05", self.settings())
        self.assertEqual((got["connected"], got["account"], got["events"]), (False, "", []))
        self.assertEqual(got["problem"], calendar.LAPSED)
        self.assertIsNone(config.get_google_token())


class DisconnectTest(Isolated):
    def test_disconnect_revokes_and_forgets(self):
        self.connect()
        out = calendar.disconnect(self.settings())
        [revoke] = self.google.to(calendar.REVOKE_URL)
        self.assertEqual(revoke["form"], {"token": GRANTED["refresh_token"]})
        self.assertEqual((out["told"], out["calendar"]["connected"]), (True, False))
        self.assertIsNone(config.get_google_token())
        self.assertEqual(self.settings()["calendar_account"], "")
        self.assertIsNone(calendar._access["token"])

    def test_preview_only_forgets_so_pulsar_stays_connected(self):
        # Preview signs in with Pulsar's Google client: a revoke would withdraw the client's access, Pulsar's too
        self.connect()
        with mock.patch.dict(os.environ, {"PULSAR_PREVIEW": "1"}):
            out = calendar.disconnect(self.settings())
        self.assertEqual(self.google.to(calendar.REVOKE_URL), [])
        self.assertIn("Google was not told", out["message"])
        self.assertIsNone(config.get_google_token())
        self.assertFalse(out["calendar"]["connected"])

    def test_offline_it_still_forgets_and_says_where_to_finish(self):
        self.connect()
        self.google.revoke = (0, {"error": "Google could not be looked up"})
        out = calendar.disconnect(self.settings())
        self.assertFalse(out["told"])
        self.assertIn("myaccount.google.com", out["message"])
        self.assertIsNone(config.get_google_token())


class ServerTest(Isolated):
    """The HTTP boundary: the callback's narrow exception, and nothing more, on a real server on a free port."""

    def setUp(self):
        super().setUp()
        more = [mock.patch.object(config, "SECRET_FILE", os.path.join(self.dir, "secret")),
                mock.patch.object(config, "SIGNIN_FILE", os.path.join(self.dir, "signin-code")),
                mock.patch.object(config, "SESSIONS_FILE", os.path.join(self.dir, "sessions.json")),
                mock.patch.object(server.productive, "connection", lambda s: {"kind": None, "problem": "not connected"})]
        for p in more:
            p.start()
        self.patches += more
        from http.server import ThreadingHTTPServer
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.key = {"X-Pulsar-Key": config.session_secret()}  # reads
        self.page = {"X-Pulsar-Session": config.new_session()}  # changes: the key alone may not (server.key_refusal)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def ask(self, path, method="GET", headers=None, body=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers={"Host": f"127.0.0.1:{self.port}", **(headers or {})})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    FROM_GOOGLE = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}

    def test_connect_needs_the_session_and_opens_google(self):
        post = {"X-Pulsar": "1", "Content-Type": "application/json"}
        self.assertEqual(self.ask("/api/calendar/connect", "POST", post, b"{}")[0], 401)
        self.assertEqual(self.ask("/api/calendar/connect", "POST", {**post, **self.key}, b"{}")[0], 403)
        status, _, body = self.ask("/api/calendar/connect", "POST", {**post, **self.page}, b"{}")
        answer = json.loads(body)
        self.assertEqual((status, answer["opened"], self.opened), (200, True, [answer["url"]]))
        q = parse_qs(urlsplit(answer["url"]).query)
        self.assertEqual(q["redirect_uri"], [f"http://127.0.0.1:{self.port}/oauth/google"])

    def test_the_callback_from_google_finishes_the_sign_in_without_a_session(self):
        _, q = self.begin(self.port)
        self.google.token.append((200, dict(GRANTED)))
        path = f"/oauth/google?state={q['state']}&code=4/made-up-code&scope={calendar.SCOPE}"
        status, headers, body = self.ask(path, headers=self.FROM_GOOGLE)  # another site's navigation, no session
        self.assertEqual(status, 200)
        self.assertIn(b"Google Calendar is connected", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn(b"made-up-refresh", body)
        self.assertNotIn(b"ya29", body)
        self.assertEqual(config.get_google_token(), GRANTED["refresh_token"])
        status, _, body = self.ask(path, headers=self.FROM_GOOGLE)  # the same answer again: used up
        self.assertEqual(status, 400)
        self.assertIn(b"not waiting for this answer", body)

    def test_the_callback_does_nothing_without_a_state_pulsar_handed_out(self):
        self.begin(self.port)
        for path in ("/oauth/google?state=made-up-state-0000000000000000&code=4/x", "/oauth/google?code=4/x",
                     "/oauth/google"):
            status, _, body = self.ask(path, headers=self.FROM_GOOGLE)
            self.assertEqual(status, 400, path)
        self.assertEqual(self.google.calls, [])
        self.assertIsNone(config.get_google_token())

    def test_only_a_page_load_reaches_the_callback(self):
        _, q = self.begin(self.port)
        path = f"/oauth/google?state={q['state']}&code=4/made-up-code"
        for extra in ({"Sec-Fetch-Dest": "iframe"}, {"Sec-Fetch-Dest": "image"}, {"Sec-Fetch-Mode": "cors"},
                      {"Sec-Fetch-Mode": "no-cors"}, {"Upgrade": "websocket"}):
            self.assertEqual(self.ask(path, headers={**self.FROM_GOOGLE, **extra})[0], 403, extra)
        self.assertEqual(self.google.calls, [])
        self.google.token.append((200, dict(GRANTED)))  # the state was not used up by the refused requests
        self.assertEqual(self.ask(path, headers=self.FROM_GOOGLE)[0], 200)
        self.assertEqual(self.ask(f"/oauth/google?state={q['state']}", "POST",
                                  {**self.FROM_GOOGLE, "Content-Type": "application/json"}, b"{}")[0], 403)

    def test_the_exception_covers_that_one_path_only(self):
        self.connect()
        for path in ("/api/calendar?date=2026-11-05", "/oauth/google/", "/oauth/googlex", "/api/calendar/status"):
            self.assertEqual(self.ask(path, headers=self.FROM_GOOGLE)[0], 403, path)  # another site: refused
            self.assertEqual(self.ask(path)[0], 401, path)                             # no session: refused

    def test_the_day_and_the_settings_through_the_api(self):
        self.connect()
        self.google.default_events = (200, {"summary": "ada@example.com", "items": [
            timed("Standup", "2026-11-05T09:30:00-05:00", "2026-11-05T10:00:00-05:00")]})
        status, _, body = self.ask("/api/calendar?date=2026-11-05", headers=self.key)
        got = json.loads(body)
        self.assertEqual((status, got["connected"], [(x["title"], x["s"]) for x in got["events"]]),
                         (200, True, [("Standup", 570)]))
        self.ask("/api/calendar?date=2026-11-05", headers=self.key)
        self.ask("/api/calendar?date=2026-11-05&fresh=1", headers=self.key)
        self.assertEqual(len(self.google.to(calendar.EVENTS_URL)), 2)  # kept once, then asked again on ↻
        self.assertEqual(self.ask("/api/calendar?date=2026-02-30", headers=self.key)[0], 400)
        _, _, body = self.ask("/api/config", headers=self.key)
        self.assertEqual(json.loads(body)["calendar"], {"configured": True, "connected": True, "account": "ada@example.com"})
        self.assertNotIn(b"made-up-refresh", body)
        status, _, body = self.ask("/api/calendar/disconnect", "POST",
                                   {**self.page, "X-Pulsar": "1", "Content-Type": "application/json"}, b"{}")
        self.assertEqual((status, json.loads(body)["calendar"]["connected"]), (200, False))
        self.assertEqual(json.loads(self.ask("/api/calendar/status", headers=self.key)[2])["connected"], False)


if __name__ == "__main__":
    unittest.main()
