import subprocess
import time
import sys
import os

print("--- MRL- Remote@T: Go Online Mission ---")
print("Starting Broker Server...")

# 1. Start the web server in the background
try:
    server_proc = subprocess.Popen([sys.executable, "web_server.py"], stdout=subprocess.DEVNULL)
    time.sleep(3)
except Exception as e:
    print(f"Error starting server: {e}")
    sys.exit(1)

# 2. Start the tunnel
print("Creating Public Tunnel via localhost.run...")
print("IMPORTANT: Copy the URL below and use it as your Hub Link!")
print("---")

# Use SSH tunnel to localhost.run
try:
    # This will output the public URL to the screen
    cmd = ["ssh", "-R", "80:localhost:8000", "nokey@localhost.run"]
    subprocess.run(cmd)
except KeyboardInterrupt:
    print("\nStopping MRL- Remote@T Hub...")
    server_proc.terminate()
except Exception as e:
    print(f"Error starting tunnel: {e}")
    server_proc.terminate()
