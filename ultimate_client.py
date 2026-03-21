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
from PIL import Image
import psutil
import getpass
import platform
import subprocess
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key
import av
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, AudioStreamTrack, RTCRtpSender
from aiortc.contrib.media import MediaStreamTrack, MediaRelay

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
                if val < 1 and len(self.sct.monitors) > 1: val = 1
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
            
            mon = self.sct.monitors[mon_idx]
            sct_img = self.sct.grab(mon)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            
            w, h = img.size
            limit = 960 if current_quality < 60 else 1280
            if w > limit:
                new_h = int(h * (limit / w))
                img = img.resize((limit, new_h), Image.Resampling.BILINEAR)
        except:
            img = Image.new("RGB", (960, 540), (0, 0, 0))

        frame = av.VideoFrame.from_image(img)
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
    pc = RTCPeerConnection()
    video_track = ScreenVideoTrack(sct)

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
    params = transceiver.sender.getParameters()
    if not params.encodings:
        from aiortc import RTCRtpEncodingParameters
        params.encodings = [RTCRtpEncodingParameters(maxBitrate=BITRATE, maxFramerate=60)]
    await transceiver.sender.setParameters(params)

    # Audio Track
    pc.addTrack(SystemAudioTrack())

    # Handshake Handling
    async def listen_signaling():
        async for m in ws:
            try:
                event = orjson.loads(m)
                if event.get("t") == "rtc_offer":
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=event["sdp"], type=event["type"]))
                    ans = await pc.createAnswer()
                    await pc.setLocalDescription(ans)
                    await ws.send(orjson.dumps({"t": "rtc_answer", "sdp": pc.localDescription.sdp, "type": pc.localDescription.type}).decode())
                elif event.get("t") == "rtc_ice":
                    from aiortc import RTCIceCandidate
                    cand = event["candidate"]["candidate"]
                    sdpMid = event["candidate"]["sdpMid"]
                    sdpMLineIndex = event["candidate"]["sdpMLineIndex"]
                    await pc.addIceCandidate(RTCIceCandidate(candidate=cand, sdpMid=sdpMid, sdpMLineIndex=sdpMLineIndex))
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
                    await ws.send(orjson.dumps({"type": "client_auth", "id": client_id, "specs": specs}).decode())
                    await start_session(ws, sct)
        except Exception as e:
            print(f"Connection failed: {e}. Retrying...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main_loop())
