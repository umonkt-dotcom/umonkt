import asyncio
import json
import io
import os
import sys
import time
import socket
import struct
import argparse
import threading

import mss
import websockets
from PIL import Image
import psutil
import orjson
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key
import sounddevice as sd
import pyperclip
import getpass

# WebRTC Imports
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, AudioStreamTrack, RTCRtpSender, RTCIceCandidate
from aiortc.contrib.media import MediaStreamTrack
import av

# --- Settings ---
QUALITY = 50
TARGET_FPS = 30 
BITRATE = 1500000 
SAMPLE_RATE = 48000
CHANNELS = 2
webcam_enabled = False
clipboard_sync_enabled = True
device_name = getpass.getuser()
selected_monitor = 1 # MSS monitor 1 is "All", 2 is first physical

# --- Controllers ---
input_lock = threading.Lock()
mouse = MouseController()
keyboard = KeyboardController()
# Removed dx_camera = dxcam.create() - only one instance in main()
KEY_MAP = {
    "Enter": Key.enter, "Backspace": Key.backspace, "Tab": Key.tab,
    "Escape": Key.esc, "Delete": Key.delete, "ArrowLeft": Key.left,
    "ArrowRight": Key.right, "ArrowUp": Key.up, "ArrowDown": Key.down,
    "Control": Key.ctrl, "Alt": Key.alt, "Shift": Key.shift,
    " ": Key.space, "F1": Key.f1, "F2": Key.f2, "F3": Key.f3, "F4": Key.f4,
    "F5": Key.f5, "F6": Key.f6, "F7": Key.f7, "F8": Key.f8,
    "F9": Key.f9, "F10": Key.f10, "F11": Key.f11, "F12": Key.f12
}

send_lock = asyncio.Lock()
last_clipboard = ""

async def safe_send(ws, data):
    async with send_lock:
        if isinstance(data, dict):
            await ws.send(orjson.dumps(data).decode('utf-8'))
        elif isinstance(data, str):
            await ws.send(data)
        else:
            await ws.send(data) # bytes

# -------------------------------------------------------
# WebRTC Tracks
# -------------------------------------------------------
class ScreenVideoTrack(VideoStreamTrack):
    def __init__(self, sct):
        super().__init__()
        self.sct = sct

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        
        # Capture using MSS (Global selected_monitor)
        try:
            # mss.monitors[0] = All, [1] = Monitor 1, etc.
            # We use global selected_monitor to switch
            mon = self.sct.monitors[min(len(self.sct.monitors)-1, selected_monitor)]
            sct_img = self.sct.grab(mon)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except:
            img = Image.new("RGB", (1920, 1080), (0, 0, 0))
        
        # Pro-Level Fix: Downscale to 720p for WAN stability
        w, h = img.size
        if w > 1280:
            img = img.resize((1280, int(h * 1280 / w)), Image.Resampling.BILINEAR)
            
        frame = av.VideoFrame.from_image(img)
        frame.pts = pts
        frame.time_base = time_base
        return frame

class SystemAudioTrack(AudioStreamTrack):
    def __init__(self):
        super().__init__()
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int16')
        self.stream.start()

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        data, _ = self.stream.read(960)
        frame = av.AudioFrame.from_ndarray(data, format='s16', layout='stereo')
        frame.pts = pts
        frame.time_base = time_base
        frame.sample_rate = SAMPLE_RATE
# Removed WebcamVideoTrack for stability (Numpy dependency)


# -------------------------------------------------------
# Elite Features: Clipboard & Processes
# -------------------------------------------------------
async def monitor_clipboard(ws):
    global last_clipboard
    while True:
        try:
            if clipboard_sync_enabled:
                current = await asyncio.to_thread(pyperclip.paste)
                if current and current != last_clipboard:
                    last_clipboard = current
                    header = struct.pack('BB', 8, 0)
                    await safe_send(ws, header + current.encode('utf-8'))
        except: pass
        await asyncio.sleep(1)

async def send_process_list(ws):
    try:
        procs = []
        # Update CPU percents first
        for p in psutil.process_iter():
            try: p.cpu_percent()
            except: pass
        await asyncio.sleep(0.1)
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
            try:
                procs.append({
                    "pid": p.info['pid'],
                    "name": p.info['name'],
                    "cpu": p.info['cpu_percent'],
                    "ram": p.info['memory_info'].rss if p.info['memory_info'] else 0
                })
            except: continue
        header = struct.pack('BB', 7, 0)
        await safe_send(ws, header + json.dumps(procs).encode('utf-8'))
    except: pass

async def send_process_list_dc(dc):
    try:
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
            try:
                procs.append({
                    "pid": p.info['pid'],
                    "name": p.info['name'],
                    "cpu": p.info['cpu_percent'],
                    "ram": p.info['memory_info'].rss / (1024 * 1024)
                })
            except: continue
        dc.send(orjson.dumps({"t": "process_list", "data": procs}))
    except: pass

# -------------------------------------------------------
# JPEG Fallback Stream
# -------------------------------------------------------
async def stream_screen_jpeg(ws, camera):
    global QUALITY, TARGET_FPS
    while True:
        try:
            start = time.perf_counter()
            frame_nb = await asyncio.to_thread(camera.grab)
            if frame_nb is not None:
                img = Image.fromarray(frame_nb)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=QUALITY)
                frame_bytes = buf.getvalue()
                header = struct.pack('BB', 1, 0)
                await safe_send(ws, header + frame_bytes)

            # Basic Stats
            info = {"cpu": psutil.cpu_percent(), "ram": psutil.virtual_memory().percent}
            await safe_send(ws, struct.pack('BB', 5, 0) + json.dumps(info).encode())
            
            elapsed = time.perf_counter() - start
            await asyncio.sleep(max(0, (1.0 / 10) - elapsed)) # Lower FPS for JPEG fallback
        except: await asyncio.sleep(1)

# -------------------------------------------------------
# Messaging & WebRTC
# -------------------------------------------------------
def handle_event(event):
    global last_clipboard
    with input_lock:
        try:
            t = event.get("t")
            if t == "mm": # Mouse Move
                mouse.position = (event["x"], event["y"])
            elif t == "mc": # Mouse Click
                btn = Button.left if event["b"] == 0 else Button.right
                if event["s"]: mouse.press(btn)
                else: mouse.release(btn)
            elif t == "scroll": mouse.scroll(0, -event.get("dy", 1))
            elif t == "kd": # Key Down
                key = event["k"]
                if len(key) == 1: keyboard.press(key)
                else:
                    k_attr = getattr(Key, key.lower(), None)
                    if k_attr: keyboard.press(k_attr)
            elif t == "ku": # Key Up
                key = event["k"]
                if len(key) == 1: keyboard.release(key)
                else:
                    k_attr = getattr(Key, key.lower(), None)
                    if k_attr: keyboard.release(k_attr)
            elif t == "clipboard_sync":
                last_clipboard = event["d"]
                pyperclip.copy(last_clipboard)
            elif t == "kill_process":
                p = psutil.Process(event["pid"])
                p.terminate()
            elif t == "select_monitor":
                global selected_monitor
                selected_monitor = int(event.get("index", 1))
        except: pass

async def manage_webrtc(ws, camera):
    pc = None
    async for raw in ws:
        try:
            msg = orjson.loads(raw)
            if msg.get("t") == "rtc_offer":
                if pc: await pc.close()
                pc = RTCPeerConnection(configuration={"iceServers": [
                    {"urls": "stun:stun.l.google.com:19302"},
                    {"urls": "stun:stun1.l.google.com:19302"},
                    {"urls": "stun:stun2.l.google.com:19302"},
                    {"urls": "stun:stun.cloudflare.com:3478"},
                    {"urls": "stun:stun.services.mozilla.com:3478"},
                    {
                        "urls": [
                            "turn:openrelay.metered.ca:80",
                            "turn:openrelay.metered.ca:443",
                            "turn:openrelay.metered.ca:443?transport=tcp"
                        ],
                        "username": "openrelayproject",
                        "credential": "openrelayproject"
                    }
                ]})


                # Force H264 for better compression/lag reduction
                # Pro-Level Fix: Ultra-Fast Preset + Zero Latency Tune
                transceiver = pc.addTransceiver("video", direction="sendonly")
                capabilities = RTCRtpSender.getCapabilities("video")
                h264_codecs = [c for c in capabilities.codecs if c.name == "H264"]
                if h264_codecs:
                    transceiver.setCodecPreferences(h264_codecs)
                
                # Add tracks manually now
                pc.addTrack(ScreenVideoTrack(camera))
                pc.addTrack(SystemAudioTrack())
                
                # Set encoding parameters (Bitrate/FPS)
                for sender in pc.getSenders():
                    if sender.track and sender.track.kind == "video":
                        params = sender.getParameters()
                        if not params.encodings:
                            from aiortc import RTCRtpEncodingParameters
                            params.encodings = [RTCRtpEncodingParameters(maxBitrate=BITRATE, maxFramerate=TARGET_FPS)]
                        else:
                            params.encodings[0].maxBitrate = BITRATE
                            params.encodings[0].maxFramerate = TARGET_FPS

                        try: await sender.setParameters(params)
                        except: pass

                # Setup Dummy Webcam (Disabled for stability)
                # webcam_track = WebcamVideoTrack()
                # webcam_sender = pc.addTrack(webcam_track)
                # await webcam_sender.replaceTrack(None)


                @pc.on("datachannel")
                def on_dc(dc):
                    @dc.on("open")
                    async def on_open():
                        # Start P2P Stats Loop
                        while dc.readyState == "open":
                            try:
                                stats = {
                                    "cpu": psutil.cpu_percent(),
                                    "ram": psutil.virtual_memory().percent
                                }
                                dc.send(orjson.dumps({"t": "stats", "data": stats}).decode())
                            except: break
                            await asyncio.sleep(1)

                    @dc.on("message")
                    async def on_msg(m):
                        try:
                            event = orjson.loads(m)
                            if event.get("t") == "get_processes":
                                asyncio.create_task(send_process_list_dc(dc))
                            elif event.get("t") == "toggle_webcam":
                                pass # Webcam disabled for stability


                            else:
                                handle_event(event)
                        except: pass

                await pc.setRemoteDescription(RTCSessionDescription(sdp=msg["sdp"], type=msg["type"]))
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                await safe_send(ws, {"t": "rtc_answer", "sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
            elif msg.get("t") == "rtc_ice" and pc:
                from aiortc import RTCIceCandidate
                c = msg["candidate"]
                if c: await pc.addIceCandidate(RTCIceCandidate(
                    component=c.get("component", 1), foundation=c.get("foundation", ""),
                    ip=c.get("address", ""), port=c.get("port", 0), priority=c.get("priority", 0),
                    protocol=c.get("protocol", "udp"), type=c.get("type", "host"),
                    sdpMid=c.get("sdpMid"), sdpMLineIndex=c.get("sdpMLineIndex"),
                ))
            elif msg.get("t") == "get_processes":
                await send_process_list(ws)
            else:
                handle_event(msg)
        except: pass

async def main(server_host):
    uri = f"wss://{server_host}/ws" if "railway.app" in server_host else f"ws://{server_host}:8000/ws"
    import ssl
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_context.check_hostname, ssl_context.verify_mode = False, ssl.CERT_NONE

    while True:
        try:
            async with websockets.connect(uri, ssl=ssl_context if "wss" in uri else None) as ws:
                print("CONNECTED TO SERVER")
                import platform
                specs = {
                    "name": device_name,
                    "hostname": socket.gethostname(),
                    "platform": platform.system(),
                    "release": platform.release(),
                    "cpu": f"{psutil.cpu_count()} Cores",
                    "ram": f"{round(psutil.virtual_memory().total / (1024**3))}GB",
                    "monitors": [{"index": i, "w": m["width"], "h": m["height"]} for i, m in enumerate(mss.mss().monitors)]
                }
                await ws.send(orjson.dumps({"type": "client_auth", "id": socket.gethostname(), "specs": specs}).decode('utf-8'))

                with mss.mss() as sct:
                    try:
                        await asyncio.gather(manage_webrtc(ws, sct), monitor_clipboard(ws))
                    except: pass

        except: await asyncio.sleep(5)

if __name__ == "__main__":
    import getpass
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="web-production-d6db5.up.railway.app")
    parser.add_argument("--name", default=getpass.getuser())
    args = parser.parse_args()
    device_name = args.name
    asyncio.run(main(args.server))
