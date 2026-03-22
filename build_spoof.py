import sys
import os

# Override the corrupted python executable path
sys.executable = r"C:\Users\MRL\AppData\Local\Python\bin\python.exe"
sys._base_executable = sys.executable

import PyInstaller.__main__

PyInstaller.__main__.run([
    '--onefile',
    '--noconsole',
    '--hidden-import', 'pynput.keyboard._win32',
    '--hidden-import', 'pynput.mouse._win32',
    '--name', 'mrl_agent',
    'ultimate_client.py'
])
