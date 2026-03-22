import os

CHUNK_SIZE = 55 * 1024 * 1024  # 55 MB per chunk
file_num = 1
os.makedirs('agent_chunks', exist_ok=True)

with open('mrl_agent.exe', 'rb') as f:
    while True:
        chunk = f.read(CHUNK_SIZE)
        if not chunk:
            break
        with open(f'agent_chunks/chunk_{file_num}.bin', 'wb') as chunk_file:
            chunk_file.write(chunk)
        print(f"Wrote chunk {file_num}")
        file_num += 1
