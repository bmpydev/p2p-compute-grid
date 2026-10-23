# Sicherheitsprüfung – P2P Global Compute Grid

**Datum:** 2026-10-23
**Geprüfte Dateien:** `main.py`, `compute_node.js`, `worker.html`, `requester.html`, `index.html`, `rust_worker/src/lib.rs`
**Methode:** Statische Code-Analyse

---

## Umsetzungsstand (2026-10-23)

Die Befunde #1–#10 und die Rust-Checks sind umgesetzt (Branch `main`, noch nicht gepusht);
offen ist nur `headerQueue` aus „Niedrig“. Die ursprüngliche Bewertung
unten bleibt als Dokument erhalten; unter jedem Befund steht der Status mit Commit.

| Befund | Status | Commit |
|---|---|---|
| #1 Sandbox / RCE im Worker | ✅ Fremdcode läuft in `sandbox_worker.js` mit CSP `default-src 'none'`, fail-closed, 10-s-Timeout | `4c430ae` |
| #2 Requester ohne Auth | ✅ Token-Pflicht für Requester (`GRID_REQUESTER_TOKEN`), Worker optional (`GRID_WORKER_TOKEN`) | `1c59785` |
| #3 Requester-Hijack | ✅ Mehrere Requester mit IDs, `requester_id` setzt der Server | `86e7089` |
| #4 SSRF / Geo-Lookup | ✅ Geo-Lookup und `requests` entfernt | `60b2a95` |
| #5 Validierung / DoS | ✅ JSON-/Typ-/Score-Prüfung, Whitelist, Rate-Limit, Verbindungs- und Größenlimits | `8b7a50d` |
| #6 Unerreichbarer Code | ✅ entfernt | `807047d` |
| #7 XSS | ✅ `textContent` statt `innerHTML`, Worker-Werte nur als geprüfte Zahlen | `a0eb780` |
| #8 Verifikation wirkungslos | ✅ Mehrheit byte-identischer Ergebnisse, Geometrie aus eigenem Task | `8cc31d4` |
| #9 Klartext-WS / Origin | ✅ Host- und Origin-Prüfung, `wss://` bei HTTPS | `1c170e1` |
| #10 `0.0.0.0` | ✅ Standard `127.0.0.1`, Netz-Bind nur mit TLS (`GRID_ALLOW_INSECURE=1` als Ausnahme) | `1c170e1` |
| Niedrig: Rust-Kernels | ✅ Abmessungs-/Längenprüfung, kein Panic mehr | `3d612f1` |
| Niedrig: `requests` | ✅ mit #4 entfernt | `60b2a95` |
| Niedrig: Tile-Größe | ✅ Mandelbrot entfernt, Sobel nur mit passenden Abmessungen | `ef2f9ab`, `3d612f1` |
| Niedrig: `headerQueue` | ⏳ offen (nur Selbststörung des Requesters, falsche Ergebnisse fängt #8 ab) | – |

**Zusätzlich umgesetzt:**
- Seiten-CSP ohne Inline-Skripte, `frame-ancestors 'none'`, `nosniff` (`1e936ca`); dabei die
  Redundanz-Auswahl im Requester repariert (wirkte vorher nie).
- Sobel-Tiles mit 1-px-Rand, keine Linien mehr an Tile-Grenzen (`af1ea89`).
- Testsuites: `tests/server` (16 Tests), `tests/browser` (12 Tests, Firefox), Rust-Unit-Tests
  (`aca7ab6`, `1e936ca`).

**Verbleibende Punkte:**
- **Offener Worker-Zugang (Design):** Ohne `GRID_WORKER_TOKEN` erhält jeder Worker die Daten der
  Requester (Vertraulichkeit), mehrere Worker einer Person können die Verifikation überstimmen
  (Sybil), Peers sehen gegenseitig ihre IP-Adressen (WebRTC, STUN bei Google).
- **Verbindungen vor der Anmeldung:** zählen nicht zu den Limits (nur 5-s-Timeout); ein Limit
  pro IP gehört bei öffentlichem Betrieb in den Reverse-Proxy.
- **`style-src 'unsafe-inline'`** in der Seiten-CSP (Inline-Styles nicht ausgelagert).
- **Token im URL-Fragment** landet in der Browser-History.
- **`headerQueue`** (s. o.) und **`report_job`** wird von keinem Client gesendet
  (Dashboard-Zähler „Jobs Completed“ bleibt 0, Funktionsfehler).
- Getestet nur in Firefox; Video-Rendering/-Export nicht automatisiert getestet.

---

## Gesamteinschätzung

Solide Architektur-Idee (WebRTC-P2P + WASM + FastAPI-Signaling), aber die **zentrale
Sicherheitsversprechung aus dem README stimmt nicht**: Der Worker wird als „gesicherte Sandbox
mit eingeschränktem API-Zugriff" beworben — tatsächlich führt er beliebigen Fremd-JS-Code quasi
ungeschützt aus. Dazu mehrere handfeste Lücken im Signaling-Server.

**Einstufung: nicht produktionsreif. Nur für vertrauenswürdige, lokale Nutzung geeignet.**

---

## 🔴 Kritisch

### #1 – Die „Sandbox" ist keine: RCE im Worker-Browser (`compute_node.js`)

> **Status: ✅ umgesetzt.** Fremdcode läuft nicht mehr in `compute_node.js`, sondern in `sandbox_worker.js` (CSP `default-src 'none'; script-src 'unsafe-eval'`, verweigert ohne wirksame CSP, 10-s-Timeout). Tests: Exfil-Listener erhält keinen Zugriff. Commit `4c430ae`.

README verspricht: *„Worker nodes execute custom code in a secured environment with restricted
API access (No fetch/XHR)."* Umgesetzt ist das so:

```js
const safeGlobals = { fetch: undefined, XMLHttpRequest: undefined, WebSocket: undefined, ... };
Object.assign(self, safeGlobals);
...
customFn = new Function('data', `"use strict"; ${header.customCode}`);
```

Das ist **keine Isolierung**:

- `Object.assign(self, {fetch:undefined})` überschreibt nur eigene Property-Referenzen. `new Function`
  erzeugt Code im globalen Scope. Angreifer-Code erreicht das Netzwerk/alle Daten weiterhin über
  `globalThis`, `self.constructor`, dynamisches `import("…")` (in Modul-Workern aktiv!),
  `new EventSource`, `navigator.sendBeacon`, `Function("return this")()` usw.
- Das Nullen von vier Namen umgeht man trivial.
- Ein bösartiger Requester (jeder kann einer werden, s. #2) schickt `customCode` an **alle** Worker →
  beliebige Code-Ausführung im Browser jedes Teilnehmers (Datenexfiltration, Mining, Zugriff auf
  fremde Tiles, Weiterverbreitung).

**Fix:** Fremd-JS niemals via `new Function` im Worker-Scope. Entweder ganz streichen und nur die
festen WASM-Kernel erlauben, oder Ausführung in `<iframe sandbox>` auf **separatem Origin** (ohne
`allow-same-origin`) mit `postMessage`-Bridge und ohne Netzwerk-Capabilities. Globale Namen
entfernen bringt nichts.

### #2 – Jeder kann Requester werden & „Custom Logic" an alle verteilen (`main.py`)

> **Status: ✅ umgesetzt.** Erste WebSocket-Nachricht muss `{"type":"auth","token":…}` sein (Close 1008, 5-s-Timeout, `hmac.compare_digest`). Requester immer, Worker optional. Commit `1c59785`.

`/ws/requester` hat keinerlei Authentifizierung. Wer sich verbindet, bekommt sofort die Worker-Liste
(`init_workers`) und kann via WebRTC `customCode` verteilen → zusammen mit #1 ein offenes Botnet.

**Fix:** Auth/Token für `/ws/requester` (und `/ws/worker`).

---

## 🟠 Hoch

### #3 – Requester-Hijack / Single-Requester-Race (`main.py`)

> **Status: ✅ umgesetzt.** Requester-Dict mit IDs; Server stempelt `requester_id`, Worker hält eine Peer-Verbindung pro Requester. Commit `86e7089`.

`self.requester = websocket` – nur **ein** globaler Requester-Slot. Ein zweiter Connect überschreibt
den ersten ohne Prüfung. Angreifer kann den legitimen Requester aushängen (dessen `answer`/`ice`
laufen danach ins Leere → Job-DoS) und alle neuen Worker + Geo-Daten abgreifen.

**Fix:** Mehrere Requester sauber verwalten (dict mit IDs), nicht einen überschreibbaren Slot.

### #4 – SSRF / Datenabfluss über `get_geo_info` (`main.py`)

> **Status: ✅ umgesetzt.** Feature auf Wunsch entfernt (ip-api.com bietet kostenlos kein HTTPS). Damit auch der blockierende Aufruf pro Worker-Connect weg. Commit `60b2a95`.

```py
res = requests.get(f"http://ip-api.com/json/{ip}", timeout=2)
```

`ip` aus `websocket.client.host` wird ungefiltert in eine URL interpoliert (SSRF-Vektor, v. a. falls
später `X-Forwarded-For` übernommen wird). Außerdem: ein ausgehender Request pro Verbindung ohne
Rate-Limit (Amplification/DoS) und die IP jedes Besuchers geht per **unverschlüsseltem HTTP** an
einen Drittdienst.

**Fix:** HTTPS; IP strikt validieren (nur gültige öffentliche IPs); nie ungeprüft aus Headern;
Caching + Rate-Limit — oder Feature weglassen.

### #5 – Fehlende Nachrichten-Validierung / Server-DoS (`main.py`)

> **Status: ✅ umgesetzt.** `receive_json()` ignoriert Ungültiges, `valid_score()`, Typ-Whitelist, Token-Bucket (50/s, Puffer 200), max. 1000 Worker / 20 Requester / 500 Stats, `ws_max_size` 64 KB, `try/finally`-Aufräumen. Commit `8b7a50d`.

- `json.loads(data)` ohne `try/except` in `/ws/worker` und `/ws/requester`; ein nicht-JSON-Frame
  wirft `JSONDecodeError`, der nicht von `except WebSocketDisconnect` gefangen wird → unkontrollierter
  Abbruch.
- `manager.active_workers[worker_id]["score"] = msg["score"]` ungeprüft übernommen. `broadcast_stats`
  macht `sum(v.get("score",0))`; bei `score: "abc"`/Liste bricht die Summe bzw. `toLocaleString()`
  (`index.html`) → **persistenter Crash der Stats für alle**.
- Keine Begrenzung von Verbindungen, Nachrichtengröße oder Rate → trivialer Ressourcen-DoS.

**Fix:** `try/except` um `json.loads`; Typ-/Wertechecks (`score` numerisch, `target_id` bekannt);
Größen- und Rate-Limits.

---

## 🟡 Mittel

### #6 – Unerreichbarer Code in `connect_worker` (`main.py`, ~Z. 59–60)

> **Status: ✅ umgesetzt.** Entfernt. Commit `807047d`.

```py
    return worker_id
    await self.broadcast_stats()   # nie erreicht (nach return)
```

Funktional unkritisch (wird im Endpoint nochmal aufgerufen), aber Zeichen fehlender Reviews.

### #7 – XSS-Potenzial über Geo-/Worker-Daten (`requester.html`)

> **Status: ✅ umgesetzt.** Tatsächlich ausnutzbar war `WORKER_INFO.cores` eines bösartigen Workers sowie der Dateiname im Log. Jetzt `textContent`/DOM-Knoten und nur geprüfte Zahlen (Kapazität 1–256). Commit `a0eb780`; zusätzlich Seiten-CSP `1e936ca`.

`updateUI` baut `card.innerHTML` u. a. aus `p.geo.country` (Drittquelle ip-api.com); `logToConsole`
nutzt ebenfalls `innerHTML`. Latentes DOM-XSS, sobald sich Quelle/Pfad ändert.

**Fix:** `textContent` statt `innerHTML` für alle dynamischen/fremden Daten.

### #8 – „Result Verification" ist wirkungslos (`requester.html`, `handleResult`)

> **Status: ✅ umgesetzt.** Tile erst verifiziert bei `requiredRedundancy` byte-identischen Ergebnissen verschiedener Worker; Konflikte werden verworfen und gemeldet; keine stille Absenkung der Redundanz; Geometrie aus eigenem Task. Commit `8cc31d4`.

Bei erreichtem `threshold` wird einfach `results[0].pixels` genommen — **nie werden zwei Ergebnisse
verglichen**. Redundanz 2/3 kostet nur Rechenzeit, erkennt keine Manipulation. Der SHA-256-Hash aus
`lib.rs` wird nirgends abgeglichen.

**Fix:** Ergebnisse bzw. deren Hashes tatsächlich vergleichen, bevor ein Tile „verified" ist.

### #9 – Klartext-WS & kein Origin-Check (`main.py`, alle HTML)

> **Status: ✅ umgesetzt.** `HostOriginGuard`: Host gegen `GRID_ALLOWED_HOSTS` (DNS-Rebinding), Origin muss zum Host passen; `wss://` bei HTTPS, TLS über `GRID_SSL_CERTFILE`/`GRID_SSL_KEYFILE`. Commit `1c170e1`.

Überall `ws://` (kein `wss://`), keine `Origin`-Prüfung → Cross-Site-WebSocket-Hijacking: jede fremde
Seite kann im Browser des Besuchers eine Verbindung zu einem lokal laufenden Grid aufbauen.

**Fix:** Origin-Check auf allen WebSockets; `wss://` erzwingen.

### #10 – `host="0.0.0.0"` (`main.py`)

> **Status: ✅ umgesetzt.** Standard `127.0.0.1` (`GRID_HOST`/`GRID_PORT`); Bind ins Netz ohne TLS wird verweigert. Commit `1c170e1`.

Server lauscht auf allen Interfaces. Mit fehlender Auth (#1/#2) wird das offene Botnet im
LAN/öffentlich real.

**Fix:** An `127.0.0.1` binden, solange keine Auth existiert.

---

## 🟢 Niedrig / Robustheit

> **Status:** Rust-Kernels ✅ (`3d612f1`), `requests` ✅ (`60b2a95`), Größenlimit ✅ (Mandelbrot
> entfernt `ef2f9ab`, Sobel nur mit passenden Abmessungen); `headerQueue` ⏳ offen (Code liegt
> inzwischen in `worker.js`).

- **`process_sobel` (`lib.rs`):** `for y in 1..height-1` → Underflow-Panic bei `height==0` (u32).
  Kein Bounds-Check `pixels.len()` vs. `width*height*4`.
- **`headerQueue` (`worker.html`):** Header/Binary-Paarung über FIFO ohne Zuordnung — bei
  Reordering/Verlust landen Pixel beim falschen Header (Korrektheit).
- `requests` nur für den fragwürdigen Geo-Call importiert — unnötige Angriffsfläche.
- Kein Größenlimit bei Mandelbrot/Image-Tiles → Requester kann riesige Dimensionen anfordern
  (Memory-DoS beim Worker).

---

## Empfohlene Reihenfolge

1. **#1 Sandbox real machen** (oder Custom-JS entfernen) — größtes Risiko.
2. **#2 + #3** Auth & Requester-Verwaltung.
3. **#5** Input-Validierung aller WS-Nachrichten.
4. **#9 + #10** Origin-Check, `wss://`, Bind auf localhost.
5. **#4** Geo-Call entschärfen/entfernen.
6. **#8** Verifikation echt umsetzen.
7. **#7** `textContent` im Frontend.
8. **Rust** Bounds-/Dimensions-Checks.
