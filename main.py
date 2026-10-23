import uuid, json, os, asyncio, hmac, secrets, math, time, hashlib, re, sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse
from urllib.parse import urlsplit

app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Falls der Pfad zu rust_worker/pkg anders ist, bitte anpassen:
app.mount("/pkg", StaticFiles(directory=os.path.join(BASE_DIR, "rust_worker", "pkg")), name="pkg")

# Netzwerk: standardmäßig nur lokal erreichbar; ins Netz nur mit TLS (siehe __main__)
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
GRID_HOST = os.environ.get("GRID_HOST", "127.0.0.1")
GRID_PORT = int(os.environ.get("GRID_PORT", "8000"))
SSL_CERTFILE = os.environ.get("GRID_SSL_CERTFILE")
SSL_KEYFILE = os.environ.get("GRID_SSL_KEYFILE")
# Hostnamen, unter denen der Server aufgerufen werden darf (Schutz vor DNS-Rebinding)
ALLOWED_HOSTS = LOOPBACK_HOSTS | {
    h.strip().lower() for h in os.environ.get("GRID_ALLOWED_HOSTS", "").split(",") if h.strip()}

def hostname_of(netloc):
    # "example.com:8000" / "[::1]:8000" -> "example.com" / "::1"; ungültig -> None
    try:
        return urlsplit("//" + netloc).hostname
    except ValueError:
        return None

class HostOriginGuard:
    # Host-Header gegen ALLOWED_HOSTS prüfen (DNS-Rebinding) und bei WebSockets den Origin
    # gegen den Host (Cross-Site-WebSocket-Hijacking). Starlettes TrustedHostMiddleware
    # zerlegt IPv6-Hosts falsch, daher eigene Variante.
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = Headers(scope=scope)
            host = headers.get("host", "")
            ok = hostname_of(host) in ALLOWED_HOSTS
            if ok and scope["type"] == "websocket":
                # Browser senden immer Origin; ohne Origin (kein Browser) gibt es kein CSWSH
                origin = headers.get("origin")
                if origin is not None:
                    parts = urlsplit(origin)
                    ok = parts.scheme in ("http", "https") and parts.netloc.lower() == host.lower()
            if not ok:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await PlainTextResponse("Invalid host header", status_code=400)(scope, receive, send)
                return
        await self.app(scope, receive, send)

app.add_middleware(HostOriginGuard)

# Seiten-CSP: nur eigene Skripte (keine Inline-Skripte/-Handler), WASM erlaubt, Netzwerk nur zum eigenen Host
PAGE_CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; worker-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

class SecurityHeaders:
    # Setzt die Seiten-CSP auf alle HTTP-Antworten ohne eigene CSP (sandbox_worker.js behält ihre strengere)
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Content-Security-Policy", PAGE_CSP)
                headers.setdefault("X-Content-Type-Options", "nosniff")
            await send(message)

        await self.app(scope, receive, send_with_headers)

app.add_middleware(SecurityHeaders)

# Requester dürfen Code an alle Worker verteilen -> immer Token-pflichtig.
# Ohne Token-Datei gilt ein gemeinsamer Token: GRID_REQUESTER_TOKEN oder bei jedem Start ein zufälliger.
REQUESTER_TOKEN = os.environ.get("GRID_REQUESTER_TOKEN") or secrets.token_urlsafe(24)
# Mit Token-Datei ein Token je Person (python3 main.py add-requester NAME)
REQUESTER_TOKENS_FILE = os.environ.get("GRID_REQUESTER_TOKENS_FILE") or os.path.join(BASE_DIR, "requester_tokens.txt")
TOKEN_RELOAD_S = 2
# Worker sind optional geschützt (offenes Volunteer-Grid, solange nicht gesetzt).
WORKER_TOKEN = os.environ.get("GRID_WORKER_TOKEN")
AUTH_TIMEOUT_S = 5

# Limits gegen Ressourcen-DoS (Signaling-Nachrichten sind nur wenige KB groß)
MAX_MESSAGE_BYTES = 64 * 1024
MAX_WORKERS = 1000
MAX_REQUESTERS = 20
MAX_STATS_LISTENERS = 500
MSG_RATE = 50       # Nachrichten pro Sekunde je Verbindung ...
MSG_BURST = 200     # ... mit kurzzeitigem Puffer (ICE-Bursts beim Verbindungsaufbau)
MAX_SCORE = 1_000_000
TOKEN_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
TOKEN_HASH_RE = re.compile(r"[0-9a-f]{64}")

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def read_token_file(path):
    # Zeilen "name sha256-hash"; Leerzeilen und # ignorieren, ungültige Zeilen melden und überspringen
    entries = []
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) == 2 and TOKEN_NAME_RE.fullmatch(parts[0]) and TOKEN_HASH_RE.fullmatch(parts[1]):
                entries.append((parts[0], parts[1]))
            else:
                print(f"[grid] {path}:{number}: ungültige Zeile übersprungen", flush=True)
    return entries

def write_token_file(path, entries):
    # Atomar ersetzen, damit der laufende Server nie eine halb geschriebene Datei liest
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# name sha256(token) - verwalten mit: python3 main.py add-requester/remove-requester NAME\n")
        for name, digest in entries:
            f.write(f"{name} {digest}\n")
    os.replace(tmp, path)

class RequesterTokens:
    # Ohne Datei gilt der gemeinsame REQUESTER_TOKEN. Sobald die Datei existiert, gelten nur ihre
    # Einträge; sie wird bei Änderung neu eingelesen (auch wenn sie später gelöscht wird: dann niemand).
    def __init__(self, path, shared_token):
        self.path = path
        self.shared = {token_hash(shared_token): "requester"}
        self.file_mode = False
        self.entries = {} # hash: name
        self.signature = None

    def refresh(self):
        try:
            st = os.stat(self.path)
            signature = (st.st_mtime_ns, st.st_size, st.st_ino)
            if signature == self.signature:
                return
            entries = read_token_file(self.path)
        except FileNotFoundError:
            if self.file_mode and self.signature is not None:
                print(f"[grid] {self.path} fehlt: keine Requester-Anmeldung möglich", flush=True)
            self.signature, self.entries = None, {}
            return
        except (OSError, UnicodeDecodeError) as e:
            print(f"[grid] {self.path} nicht lesbar ({e}): keine Requester-Anmeldung möglich", flush=True)
            self.signature, self.entries, self.file_mode = None, {}, True
            return
        self.signature, self.file_mode = signature, True
        self.entries = {digest: name for name, digest in entries}

    def active(self):
        return self.entries if self.file_mode else self.shared

    def check(self, token):
        # Liefert (name, hash) oder None; vergleicht gegen alle Einträge in konstanter Zeit je Eintrag
        self.refresh()
        presented, found = token_hash(token), None
        for digest, name in self.active().items():
            if hmac.compare_digest(presented, digest):
                found = (name, digest)
        return found

    def is_valid(self, digest):
        return digest in self.active()

requester_tokens = RequesterTokens(REQUESTER_TOKENS_FILE, REQUESTER_TOKEN)

def check_worker_token(token):
    if WORKER_TOKEN is None or hmac.compare_digest(token.encode(), WORKER_TOKEN.encode()):
        return True
    return None

async def authenticate(websocket: WebSocket, check):
    # Erste Nachricht muss {"type": "auth", "token": ...} sein, sonst Close 1008.
    # check(token) liefert die Identität oder None.
    await websocket.accept()
    try:
        msg = json.loads(await asyncio.wait_for(websocket.receive_text(), AUTH_TIMEOUT_S))
        token = msg.get("token") if isinstance(msg, dict) and msg.get("type") == "auth" else None
        identity = check(token) if isinstance(token, str) else None
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, KeyError):
        identity = None
    if identity is None:
        try:
            await websocket.close(code=1008)
        except Exception:
            pass
    return identity

class RateLimiter:
    # Token-Bucket je Verbindung. Bremst statt zu verwerfen, damit kein ICE verloren geht;
    # ein Flood blockiert nur die eigene Verbindung.
    def __init__(self, rate=MSG_RATE, burst=MSG_BURST):
        self.rate, self.burst = rate, burst
        self.tokens, self.last = burst, time.monotonic()

    async def wait(self):
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens < 1:
            await asyncio.sleep((1 - self.tokens) / self.rate)
            self.tokens, self.last = 1, time.monotonic()
        self.tokens -= 1

async def receive_json(websocket: WebSocket, limiter: RateLimiter):
    # Liefert ein dict oder None (Binärframe, zu groß, kein JSON-Objekt -> ignorieren)
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    await limiter.wait()
    data = message.get("text")
    if data is None or len(data) > MAX_MESSAGE_BYTES:
        return None
    try:
        msg = json.loads(data)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None

def valid_score(score):
    return (isinstance(score, (int, float)) and not isinstance(score, bool)
            and math.isfinite(score) and 0 <= score <= MAX_SCORE)

class ConnectionManager:
    def __init__(self):
        self.active_workers = {} # id: {"ws": websocket, "score": score}
        self.requesters = {} # id: websocket
        self.requester_auth = {} # id: (name, token-hash)
        self.stats_listeners = set() # Neu: Alle Besucher der index.html
        self.total_verified_jobs = 0 # Neu: Globaler Zähler

    def stats_payload(self):
        # Berechnet die aktuellen Werte für alle
        total_power = sum(v.get("score", 0) for v in self.active_workers.values())
        return json.dumps({
            "type": "global_stats_update",
            "nodes": len(self.active_workers),
            "power": total_power,
            "jobs": self.total_verified_jobs
        })

    async def broadcast_stats(self):
        data = self.stats_payload()
        for listener in list(self.stats_listeners):
            try:
                await listener.send_text(data)
            except Exception:
                self.stats_listeners.discard(listener)

    async def connect_worker(self, websocket: WebSocket):
        worker_id = str(uuid.uuid4())[:8]
        self.active_workers[worker_id] = {"ws": websocket}
        await self.broadcast_to_requesters({
            "type": "new_worker",
            "worker_id": worker_id
        })
        return worker_id

    async def connect_requester(self, websocket: WebSocket, auth):
        requester_id = str(uuid.uuid4())[:8]
        self.requesters[requester_id] = websocket
        self.requester_auth[requester_id] = auth
        # WICHTIG: Liste von Objekten senden
        workers_list = [{"id": k} for k in self.active_workers]
        try:
            await websocket.send_text(json.dumps({
                "type": "init_workers",
                "workers": workers_list
            }))
        except Exception:
            self.requesters.pop(requester_id, None)
            self.requester_auth.pop(requester_id, None)
            raise
        print(f"[grid] Requester {auth[0]} verbunden ({requester_id})", flush=True)
        return requester_id

    def disconnect_worker(self, worker_id: str):
        if worker_id in self.active_workers:
            del self.active_workers[worker_id]

    async def disconnect_requester(self, requester_id: str):
        self.requesters.pop(requester_id, None)
        auth = self.requester_auth.pop(requester_id, None)
        if auth:
            print(f"[grid] Requester {auth[0]} getrennt ({requester_id})", flush=True)
        # Worker bauen ihre Peer-Verbindung zu diesem Requester ab
        for worker in list(self.active_workers.values()):
            try:
                await worker["ws"].send_text(json.dumps({"type": "requester_left", "requester_id": requester_id}))
            except Exception:
                pass

    async def drop_revoked_requesters(self):
        # Requester, deren Token aus der Datei entfernt wurde, sofort trennen; Aufräumen macht ihr Handler
        for requester_id, (name, digest) in list(self.requester_auth.items()):
            if not requester_tokens.is_valid(digest):
                print(f"[grid] Token von {name} entzogen, trenne {requester_id}", flush=True)
                del self.requester_auth[requester_id]
                ws = self.requesters.pop(requester_id, None)
                if ws:
                    try:
                        await ws.close(code=1008)
                    except Exception:
                        pass

    async def broadcast_to_requesters(self, message: dict):
        data = json.dumps(message)
        for ws in list(self.requesters.values()):
            try:
                await ws.send_text(data)
            except Exception:
                pass

    async def forward_to_worker(self, requester_id: str, target_id: str, message: dict):
        if target_id in self.active_workers:
            # Absender setzt der Server, nicht der Client -> nicht fälschbar
            message["requester_id"] = requester_id
            try:
                await self.active_workers[target_id]["ws"].send_text(json.dumps(message))
            except Exception:
                pass # Worker gerade weg; sein Handler räumt auf

    async def send_to_requester(self, message: dict):
        requester_id = message.get("requester_id")
        ws = self.requesters.get(requester_id) if isinstance(requester_id, str) else None
        if ws:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                pass # Requester gerade weg; sein Handler räumt auf

manager = ConnectionManager()

async def watch_requester_tokens():
    while True:
        await asyncio.sleep(TOKEN_RELOAD_S)
        requester_tokens.refresh()
        await manager.drop_revoked_requesters()

@asynccontextmanager
async def lifespan(app):
    requester_tokens.refresh()
    if requester_tokens.file_mode:
        print(f"[grid] Requester-Tokens aus {REQUESTER_TOKENS_FILE} ({len(requester_tokens.entries)} Einträge)", flush=True)
    else:
        print(f"[grid] Requester-Zugang: /requester#token={REQUESTER_TOKEN}", flush=True)
    if WORKER_TOKEN:
        print("[grid] Worker benötigen Token: /worker#token=<GRID_WORKER_TOKEN>", flush=True)
    watcher = asyncio.create_task(watch_requester_tokens())
    try:
        yield
    finally:
        watcher.cancel()

# app wird oben vor den Middlewares erzeugt, die Lifespan braucht aber manager
app.router.lifespan_context = lifespan

@app.get("/")
async def get_index(): return FileResponse("index.html")

@app.get("/worker")
async def get_worker(): return FileResponse("worker.html")

@app.get("/requester")
async def get_requester(): return FileResponse("requester.html")

@app.get("/index.js")
async def get_index_js(): return FileResponse("index.js", media_type="application/javascript")

@app.get("/worker.js")
async def get_worker_js(): return FileResponse("worker.js", media_type="application/javascript")

@app.get("/requester.js")
async def get_requester_js(): return FileResponse("requester.js", media_type="application/javascript")

@app.get("/compute_node.js")
async def get_compute_node():
    return FileResponse("compute_node.js", media_type="application/javascript")

# Die CSP ist die eigentliche Sandbox für Requester-Code: kein Netzwerk, kein Nachladen, keine Sub-Worker
SANDBOX_CSP = "default-src 'none'; script-src 'unsafe-eval'"

@app.get("/sandbox_worker.js")
async def get_sandbox_worker():
    return FileResponse("sandbox_worker.js", media_type="application/javascript",
                        headers={"Content-Security-Policy": SANDBOX_CSP, "Cache-Control": "no-store"})

@app.websocket("/ws/stats")
async def stats_endpoint(websocket: WebSocket):
    if len(manager.stats_listeners) >= MAX_STATS_LISTENERS:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    manager.stats_listeners.add(websocket)
    try:
        # Nur dem neuen Besucher senden, nicht allen (sonst Verstärkung per Connect-Flood)
        await websocket.send_text(manager.stats_payload())
        limiter = RateLimiter()
        while True: await receive_json(websocket, limiter) # Offen halten
    except WebSocketDisconnect:
        pass
    finally:
        manager.stats_listeners.discard(websocket)

@app.websocket("/ws/worker")
async def worker_endpoint(websocket: WebSocket):
    if len(manager.active_workers) >= MAX_WORKERS:
        await websocket.close(code=1013)
        return
    if await authenticate(websocket, check_worker_token) is None:
        return
    worker_id = await manager.connect_worker(websocket)
    try:
        await manager.broadcast_stats()
        limiter = RateLimiter()
        while True:
            msg = await receive_json(websocket, limiter)
            if msg is None:
                continue
            # Worker meldet seinen Score an den Server für die index.html
            if msg.get("type") == "report_score":
                if valid_score(msg.get("score")):
                    manager.active_workers[worker_id]["score"] = msg["score"]
                    await manager.broadcast_stats()
            elif msg.get("type") in ("answer", "ice"):
                msg["worker_id"] = worker_id
                await manager.send_to_requester(msg)
    except WebSocketDisconnect:
        pass
    finally:
        # Auch bei unerwarteten Fehlern keine Geister-Worker zurücklassen
        manager.disconnect_worker(worker_id)
        await manager.broadcast_stats()

@app.websocket("/ws/requester")
async def requester_endpoint(websocket: WebSocket):
    if len(manager.requesters) >= MAX_REQUESTERS:
        await websocket.close(code=1013)
        return
    auth = await authenticate(websocket, requester_tokens.check)
    if auth is None:
        return
    requester_id = await manager.connect_requester(websocket, auth)
    try:
        limiter = RateLimiter()
        while True:
            msg = await receive_json(websocket, limiter)
            if msg is None:
                continue
            if msg.get("type") == "report_job":
                manager.total_verified_jobs += 1
                await manager.broadcast_stats()
            elif msg.get("type") in ("offer", "ice") and isinstance(msg.get("target_id"), str):
                await manager.forward_to_worker(requester_id, msg["target_id"], msg)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect_requester(requester_id)

def manage_tokens(command, name):
    # CLI: Tokens in REQUESTER_TOKENS_FILE anlegen/entfernen; der laufende Server übernimmt das sofort
    entries = read_token_file(REQUESTER_TOKENS_FILE) if os.path.exists(REQUESTER_TOKENS_FILE) else []
    names = [n for n, _ in entries]
    if command == "list-requesters":
        print("\n".join(names) if names else f"keine Einträge in {REQUESTER_TOKENS_FILE}")
        return
    if not name or not TOKEN_NAME_RE.fullmatch(name):
        raise SystemExit("Name fehlt oder ungültig (erlaubt: A-Z a-z 0-9 _ . -, max. 64 Zeichen)")
    if command == "add-requester":
        if name in names:
            raise SystemExit(f"{name} existiert schon; erst remove-requester {name}")
        token = secrets.token_urlsafe(24)
        write_token_file(REQUESTER_TOKENS_FILE, entries + [(name, token_hash(token))])
        print(f"Requester {name} angelegt. Link (wird nicht gespeichert, nur jetzt sichtbar):")
        print(f"  /requester#token={token}")
        if not entries:
            print("Hinweis: ab jetzt gelten nur noch Tokens aus der Datei, der gemeinsame Token nicht mehr.")
    elif command == "remove-requester":
        if name not in names:
            raise SystemExit(f"{name} nicht gefunden")
        write_token_file(REQUESTER_TOKENS_FILE, [e for e in entries if e[0] != name])
        print(f"Requester {name} entfernt; offene Verbindungen werden innerhalb von {TOKEN_RELOAD_S}s getrennt.")

TOKEN_COMMANDS = ("add-requester", "remove-requester", "list-requesters")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] not in TOKEN_COMMANDS or len(sys.argv) > 3:
            raise SystemExit(f"Aufruf: python3 main.py [{' | '.join(TOKEN_COMMANDS)}] [NAME]")
        manage_tokens(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
        raise SystemExit(0)
    import uvicorn
    tls = bool(SSL_CERTFILE and SSL_KEYFILE)
    # Ohne TLS gingen Token und Signaling im Klartext durchs Netz -> nur lokal erlauben
    if GRID_HOST not in LOOPBACK_HOSTS and not tls and os.environ.get("GRID_ALLOW_INSECURE") != "1":
        raise SystemExit(
            f"[grid] GRID_HOST={GRID_HOST} ohne TLS verweigert: GRID_SSL_CERTFILE und GRID_SSL_KEYFILE setzen, "
            "einen TLS-Reverse-Proxy vor 127.0.0.1 nutzen oder (unsicher) GRID_ALLOW_INSECURE=1.")
    if GRID_HOST not in LOOPBACK_HOSTS and ALLOWED_HOSTS == LOOPBACK_HOSTS:
        print("[grid] Hinweis: GRID_ALLOWED_HOSTS ist leer, Aufrufe über andere Hostnamen/IPs werden abgelehnt.", flush=True)
    # Frames über dem Limit verwirft uvicorn schon beim Empfang (bei uvicorn-CLI: --ws-max-size 65536)
    uvicorn.run(app, host=GRID_HOST, port=GRID_PORT, ws_max_size=MAX_MESSAGE_BYTES,
                ssl_certfile=SSL_CERTFILE if tls else None, ssl_keyfile=SSL_KEYFILE if tls else None)
