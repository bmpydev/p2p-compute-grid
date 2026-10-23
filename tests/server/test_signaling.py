"""Tests für den Signaling-Server (main.py).

Startet main.app mit kleinen Limits (limits_app.py) auf einem freien Port.
Ausführen im Repo-Wurzelverzeichnis:  python3 -m unittest discover -s tests/server -v
"""
import asyncio, http.client, json, os, socket, subprocess, sys, tempfile, time, unittest
import websockets
from websockets.protocol import State

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOKEN = "test-token"
PORT = None
SERVER = None
SERVER_LOG = None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(host, port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection((host, port), timeout=0.2).close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


def server_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GRID_")}
    env.update(extra)
    return env


def setUpModule():
    global PORT, SERVER, SERVER_LOG
    PORT = free_port()
    SERVER_LOG = tempfile.TemporaryFile()
    SERVER = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "limits_app:app", "--app-dir", os.path.dirname(__file__),
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=ROOT, env=server_env(GRID_REQUESTER_TOKEN=TOKEN, GRID_ALLOWED_HOSTS="grid.example"),
        stdout=SERVER_LOG, stderr=subprocess.STDOUT)
    if not wait_for_port("127.0.0.1", PORT):
        SERVER.kill()
        raise RuntimeError("Testserver startet nicht")


def tearDownModule():
    SERVER.terminate()
    SERVER.wait(5)
    SERVER_LOG.seek(0)
    log = SERVER_LOG.read().decode(errors="replace")
    if "Traceback" in log:
        raise AssertionError("Server-Log enthält Exceptions:\n" + log)


def ws_url(path):
    return f"ws://127.0.0.1:{PORT}{path}"


def auth(token):
    return json.dumps({"type": "auth", "token": token})


async def recv_json(ws, timeout=1.0):
    try:
        return json.loads(await asyncio.wait_for(ws.recv(), timeout))
    except asyncio.TimeoutError:
        return None


async def connect_requester():
    ws = await websockets.connect(ws_url("/ws/requester"))
    await ws.send(auth(TOKEN))
    msg = await recv_json(ws)
    assert msg and msg["type"] == "init_workers", msg
    return ws


async def connect_worker():
    ws = await websockets.connect(ws_url("/ws/worker"))
    await ws.send(auth(""))
    return ws


async def is_alive(ws):
    await ws.send(json.dumps({"type": "noop"}))
    await asyncio.sleep(0.2)
    return ws.state is State.OPEN


async def stats():
    async with websockets.connect(ws_url("/ws/stats")) as s:
        return await recv_json(s)


async def close_all(*sockets):
    for ws in sockets:
        await ws.close()
    await asyncio.sleep(0.2)  # Server räumt asynchron auf


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_handshakes_are_closed_with_1008(self):
        cases = {
            "keine Auth-Nachricht (Timeout)": None,
            "falscher Token": auth("falsch"),
            "kein JSON": "{nope",
            "Token keine Zeichenkette": json.dumps({"type": "auth", "token": 5}),
            "offer statt auth": json.dumps({"type": "offer", "target_id": "x"}),
        }
        for name, first in cases.items():
            with self.subTest(name):
                ws = await websockets.connect(ws_url("/ws/requester"))
                if first is not None:
                    await ws.send(first)
                await asyncio.wait_for(ws.wait_closed(), 3)
                self.assertEqual(ws.close_code, 1008)

    async def test_valid_requester_and_open_worker_are_accepted(self):
        r = await connect_requester()
        w = await connect_worker()
        try:
            self.assertTrue(await is_alive(w))
        finally:
            await close_all(r, w)


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_requester_id_is_stamped_by_server_and_answers_are_routed(self):
        r1, r2 = await connect_requester(), await connect_requester()
        w = await connect_worker()
        try:
            joined1, joined2 = await recv_json(r1), await recv_json(r2)
            self.assertEqual((joined1["type"], joined2["type"]), ("new_worker", "new_worker"))
            wid = joined1["worker_id"]

            await r1.send(json.dumps({"type": "offer", "target_id": wid, "requester_id": "FAKE", "sdp": {}}))
            offer = await recv_json(w)
            self.assertNotEqual(offer["requester_id"], "FAKE")

            await w.send(json.dumps({"type": "answer", "requester_id": offer["requester_id"], "sdp": {}}))
            self.assertEqual((await recv_json(r1))["type"], "answer")
            self.assertIsNone(await recv_json(r2, 0.5))

            for bad in (["x"], {"a": 1}, None, 5, "unbekannt"):
                await w.send(json.dumps({"type": "answer", "requester_id": bad, "sdp": {}}))
            self.assertTrue(await is_alive(w))

            await r1.close()
            left = await recv_json(w)
            self.assertEqual(left, {"type": "requester_left", "requester_id": offer["requester_id"]})
        finally:
            await close_all(r1, r2, w)


class ValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_junk_frames_do_not_kill_connections(self):
        r, w = await connect_requester(), await connect_worker()
        try:
            for frame in ["{kein json", b"\x00\x01binary", "[1,2,3]", "42", "null", '"str"']:
                await w.send(frame)
                await r.send(frame)
            self.assertTrue(await is_alive(w))
            self.assertTrue(await is_alive(r))
        finally:
            await close_all(r, w)

    async def test_only_valid_scores_are_accepted(self):
        w = await connect_worker()
        try:
            for bad in ['"abc"', "[1]", "NaN", "Infinity", "1e308", "-5", "true", '{"a":1}', "null"]:
                await w.send('{"type":"report_score","score":%s}' % bad)
            await asyncio.sleep(0.3)
            self.assertEqual((await stats())["power"], 0)
            await w.send(json.dumps({"type": "report_score", "score": 123}))
            await asyncio.sleep(0.3)
            self.assertEqual((await stats())["power"], 123)
        finally:
            await close_all(w)

    async def test_forwarded_message_types_are_whitelisted(self):
        r = await connect_requester()
        w = await connect_worker()
        try:
            wid = (await recv_json(r))["worker_id"]
            # Worker -> Requester: nur answer/ice
            await w.send(json.dumps({"type": "init_workers", "requester_id": "x", "workers": ["fake"]}))
            self.assertIsNone(await recv_json(r, 0.5))
            # Requester -> Worker: nur offer/ice mit String-target_id
            for target in (["x"], {"a": 1}, 5, None):
                await r.send(json.dumps({"type": "offer", "target_id": target, "sdp": {}}))
            await r.send(json.dumps({"type": "requester_left", "target_id": wid}))
            self.assertIsNone(await recv_json(w, 0.5))
            self.assertTrue(await is_alive(r))
            await r.send(json.dumps({"type": "offer", "target_id": wid, "sdp": {}}))
            self.assertEqual((await recv_json(w))["type"], "offer")
        finally:
            await close_all(r, w)

    async def test_oversized_message_is_ignored(self):
        w = await connect_worker()
        try:
            await w.send(json.dumps({"type": "answer", "requester_id": "x", "sdp": "A" * 100_000}))
            self.assertTrue(await is_alive(w))
        finally:
            await close_all(w)


class LimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_throttles_only_the_flooding_connection(self):
        w = await connect_worker()
        try:
            t0 = time.monotonic()
            for _ in range(300):  # Puffer 200, danach 50/s -> ca. 2 s
                await w.send(json.dumps({"type": "noop"}))
            t1 = time.monotonic()
            self.assertIsNotNone(await stats())
            self.assertLess(time.monotonic() - t1, 0.5, "anderer Client muss sofort bedient werden")
            await w.send(json.dumps({"type": "report_score", "score": 7}))
            while (await stats())["power"] != 7:
                await asyncio.sleep(0.1)
            self.assertGreater(time.monotonic() - t0, 1.2)
        finally:
            await close_all(w)

    async def test_connection_limits(self):
        r1, r2 = await connect_requester(), await connect_requester()
        listeners = [await websockets.connect(ws_url("/ws/stats")) for _ in range(3)]
        try:
            with self.assertRaises(websockets.exceptions.InvalidStatus):
                await websockets.connect(ws_url("/ws/requester"))
            with self.assertRaises(websockets.exceptions.InvalidStatus):
                await websockets.connect(ws_url("/ws/stats"))
        finally:
            await close_all(r1, r2, *listeners)
        self.assertIsNotNone(await stats())

    async def test_disconnected_worker_is_removed(self):
        w = await connect_worker()
        await asyncio.sleep(0.2)
        self.assertEqual((await stats())["nodes"], 1)
        await close_all(w)
        self.assertEqual((await stats())["nodes"], 0)


def http_status(path, host, origin=None, websocket=False):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=3)
    conn.putrequest("GET", path, skip_host=True)
    conn.putheader("Host", host)
    if origin is not None:
        conn.putheader("Origin", origin)
    if websocket:
        for k, v in (("Upgrade", "websocket"), ("Connection", "Upgrade"),
                     ("Sec-WebSocket-Key", "dGhlIHNhbXBsZSBub25jZQ=="), ("Sec-WebSocket-Version", "13")):
            conn.putheader(k, v)
    conn.endheaders()
    status = conn.getresponse().status
    conn.close()
    return status


class HostOriginTests(unittest.TestCase):
    def test_host_header_is_checked(self):
        cases = [(f"localhost:{PORT}", 200), (f"[::1]:{PORT}", 200), ("grid.example", 200),
                 (f"evil.com:{PORT}", 400), ("[::1", 400)]  # evil.com: DNS-Rebinding, [::1: kaputt
        for host, want in cases:
            with self.subTest(host):
                self.assertEqual(http_status("/", host), want)

    def test_websocket_origin_must_match_host(self):
        host = f"localhost:{PORT}"
        cases = [(None, host, 101), (f"http://{host}", host, 101), ("https://evil.com", host, 403),
                 ("http://localhost:3000", host, 403), ("null", host, 403),
                 (f"http://evil.com:{PORT}", f"evil.com:{PORT}", 403)]  # DNS-Rebinding
        for path in ("/ws/stats", "/ws/worker", "/ws/requester"):
            for origin, req_host, want in cases:
                with self.subTest(path=path, origin=origin, host=req_host):
                    self.assertEqual(http_status(path, req_host, origin, websocket=True), want)

    def test_pages_and_scripts_get_page_csp(self):
        for path in ("/", "/worker", "/requester", "/requester.js", "/compute_node.js", "/pkg/rust_worker.js"):
            with self.subTest(path):
                conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=3)
                conn.request("GET", path)
                res = conn.getresponse()
                csp = res.getheader("Content-Security-Policy") or ""
                self.assertIn("script-src 'self' 'wasm-unsafe-eval'", csp)
                self.assertNotIn("unsafe-inline';", csp.split("style-src")[0])  # nur Styles dürfen inline sein
                self.assertIn("frame-ancestors 'none'", csp)
                self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")
                conn.close()

    def test_sandbox_worker_is_served_with_strict_csp(self):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=3)
        conn.request("GET", "/sandbox_worker.js")
        res = conn.getresponse()
        self.assertEqual(res.getheader("Content-Security-Policy"), "default-src 'none'; script-src 'unsafe-eval'")
        conn.close()


class StartupTests(unittest.TestCase):
    def run_main(self, **env):
        return subprocess.Popen([sys.executable, "main.py"], cwd=ROOT, env=server_env(**env),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def test_non_loopback_bind_without_tls_is_refused(self):
        proc = self.run_main(GRID_HOST="127.0.0.2", GRID_PORT=str(free_port()))
        out, _ = proc.communicate(timeout=15)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b"ohne TLS verweigert", out)

    def test_insecure_override_starts_server(self):
        port = free_port()
        proc = self.run_main(GRID_HOST="127.0.0.2", GRID_PORT=str(port), GRID_ALLOW_INSECURE="1")
        try:
            self.assertTrue(wait_for_port("127.0.0.2", port))
        finally:
            proc.terminate()
            proc.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
