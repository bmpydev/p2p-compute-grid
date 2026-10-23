import uuid, json, os, requests, asyncio, hmac, secrets, math, time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Falls der Pfad zu rust_worker/pkg anders ist, bitte anpassen:
app.mount("/pkg", StaticFiles(directory=os.path.join(BASE_DIR, "rust_worker", "pkg")), name="pkg")

# Requester dürfen Code an alle Worker verteilen -> immer Token-pflichtig.
# Ohne GRID_REQUESTER_TOKEN wird bei jedem Start ein zufälliger Token erzeugt.
REQUESTER_TOKEN = os.environ.get("GRID_REQUESTER_TOKEN") or secrets.token_urlsafe(24)
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
print(f"[grid] Requester-Zugang: /requester#token={REQUESTER_TOKEN}", flush=True)
if WORKER_TOKEN:
    print("[grid] Worker benötigen Token: /worker#token=<GRID_WORKER_TOKEN>", flush=True)

async def authenticate(websocket: WebSocket, expected_token):
    # Erste Nachricht muss {"type": "auth", "token": ...} sein, sonst Close 1008
    await websocket.accept()
    try:
        msg = json.loads(await asyncio.wait_for(websocket.receive_text(), AUTH_TIMEOUT_S))
        token = msg.get("token") if isinstance(msg, dict) and msg.get("type") == "auth" else None
        ok = isinstance(token, str) and (
            expected_token is None or hmac.compare_digest(token.encode(), expected_token.encode()))
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, KeyError):
        ok = False
    if not ok:
        try:
            await websocket.close(code=1008)
        except Exception:
            pass
    return ok

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

def get_geo_info(ip):
    if ip == "127.0.0.1" or ip == "localhost":
        return {"country": "Local Host", "countryCode": "LOC"}
    try:
        # Kostenlose API für Geo-Daten
        res = requests.get(f"http://ip-api.com/json/{ip}", timeout=2).json()
        return {
            "country": res.get("country", "Unknown"),
            "countryCode": res.get("countryCode", "UN")
        }
    except:
        return {"country": "Unknown", "countryCode": "UN"}

class ConnectionManager:
    def __init__(self):
        self.active_workers = {} # id: {"ws": websocket, "geo": geo}
        self.requesters = {} # id: websocket
        self.stats_listeners = set() # Neu: Alle Besucher der index.html
        self.total_verified_jobs = 0 # Neu: Globaler Zähler

    def stats_payload(self):
        # Berechnet die aktuellen Werte für alle
        total_power = sum(v.get("score", 0) for v in self.active_workers.values())
        return json.dumps({
            "type": "global_stats_update",
            "nodes": len(self.active_workers),
            "power": total_power,
            "jobs": self.total_verified_jobs,
            "countries": [v["geo"]["countryCode"] for v in self.active_workers.values()]
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
        geo = get_geo_info(websocket.client.host)
        self.active_workers[worker_id] = {"ws": websocket, "geo": geo}
        await self.broadcast_to_requesters({
            "type": "new_worker",
            "worker_id": worker_id,
            "geo": geo
        })
        return worker_id
        await self.broadcast_stats()

    async def connect_requester(self, websocket: WebSocket):
        requester_id = str(uuid.uuid4())[:8]
        self.requesters[requester_id] = websocket
        # WICHTIG: Liste von Objekten senden
        workers_list = [{"id": k, "geo": v["geo"]} for k, v in self.active_workers.items()]
        try:
            await websocket.send_text(json.dumps({
                "type": "init_workers",
                "workers": workers_list
            }))
        except Exception:
            self.requesters.pop(requester_id, None)
            raise
        return requester_id

    def disconnect_worker(self, worker_id: str):
        if worker_id in self.active_workers:
            del self.active_workers[worker_id]

    async def disconnect_requester(self, requester_id: str):
        self.requesters.pop(requester_id, None)
        # Worker bauen ihre Peer-Verbindung zu diesem Requester ab
        for worker in list(self.active_workers.values()):
            try:
                await worker["ws"].send_text(json.dumps({"type": "requester_left", "requester_id": requester_id}))
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

@app.get("/")
async def get_index(): return FileResponse("index.html")

@app.get("/worker")
async def get_worker(): return FileResponse("worker.html")

@app.get("/requester")
async def get_requester(): return FileResponse("requester.html")

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
    if not await authenticate(websocket, WORKER_TOKEN):
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
    if not await authenticate(websocket, REQUESTER_TOKEN):
        return
    requester_id = await manager.connect_requester(websocket)
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

if __name__ == "__main__":
    import uvicorn
    # Frames über dem Limit verwirft uvicorn schon beim Empfang (bei uvicorn-CLI: --ws-max-size 65536)
    uvicorn.run(app, host="0.0.0.0", port=8000, ws_max_size=MAX_MESSAGE_BYTES)
