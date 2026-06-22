import uuid, json, os, requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Falls der Pfad zu rust_worker/pkg anders ist, bitte anpassen:
app.mount("/pkg", StaticFiles(directory=os.path.join(BASE_DIR, "rust_worker", "pkg")), name="pkg")

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
        self.requester = None
        self.stats_listeners = set() # Neu: Alle Besucher der index.html
        self.total_verified_jobs = 0 # Neu: Globaler Zähler

    async def broadcast_stats(self):
        # Berechnet die aktuellen Werte für alle
        total_power = sum(v.get("score", 0) for v in self.active_workers.values())
        countries = [v["geo"]["countryCode"] for v in self.active_workers.values()]
        data = json.dumps({
            "type": "global_stats_update",
            "nodes": len(self.active_workers),
            "power": total_power,
            "jobs": self.total_verified_jobs,
            "countries": [v["geo"]["countryCode"] for v in self.active_workers.values()]
        })
        for listener in list(self.stats_listeners):
            try:
                await listener.send_text(data)
            except:
                self.stats_listeners.remove(listener)

    async def connect_worker(self, websocket: WebSocket):
        await websocket.accept()
        worker_id = str(uuid.uuid4())[:8]
        geo = get_geo_info(websocket.client.host)
        self.active_workers[worker_id] = {"ws": websocket, "geo": geo}
        if self.requester:
            await self.requester.send_text(json.dumps({
                "type": "new_worker",
                "worker_id": worker_id,
                "geo": geo
            }))
        return worker_id
        await self.broadcast_stats()

    async def connect_requester(self, websocket: WebSocket):
        await websocket.accept()
        self.requester = websocket
        # WICHTIG: Liste von Objekten senden
        workers_list = [{"id": k, "geo": v["geo"]} for k, v in self.active_workers.items()]
        await websocket.send_text(json.dumps({
            "type": "init_workers",
            "workers": workers_list
        }))

    def disconnect_worker(self, worker_id: str):
        if worker_id in self.active_workers:
            del self.active_workers[worker_id]

    async def forward_to_worker(self, target_id: str, message: dict):
        if target_id in self.active_workers:
            await self.active_workers[target_id]["ws"].send_text(json.dumps(message))

    async def send_to_requester(self, message: dict):
        if self.requester:
            await self.requester.send_text(json.dumps(message))
            await self.broadcast_stats()

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

@app.websocket("/ws/stats")
async def stats_endpoint(websocket: WebSocket):
    await websocket.accept()
    manager.stats_listeners.add(websocket)
    await manager.broadcast_stats()
    try:
        while True: await websocket.receive_text() # Offen halten
    except: manager.stats_listeners.remove(websocket)

@app.websocket("/ws/worker")
async def worker_endpoint(websocket: WebSocket):
    worker_id = await manager.connect_worker(websocket)
    await manager.broadcast_stats()
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            # NEU: Worker meldet seinen Score an den Server für die index.html
            if msg.get("type") == "report_score":
                manager.active_workers[worker_id]["score"] = msg["score"]
                await manager.broadcast_stats()
            else:
                msg["worker_id"] = worker_id
                await manager.send_to_requester(msg)
    except WebSocketDisconnect:
        manager.disconnect_worker(worker_id)
        await manager.broadcast_stats()

@app.websocket("/ws/requester")
async def requester_endpoint(websocket: WebSocket):
    await manager.connect_requester(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            # NEU: Hier kommt dein Schnipsel hin!
            if msg.get("type") == "report_job":
                manager.total_verified_jobs += 1
                await manager.broadcast_stats()
            elif "target_id" in msg:
                await manager.forward_to_worker(msg["target_id"], msg)
    except WebSocketDisconnect:
        manager.requester = None

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
