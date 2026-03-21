import struct
import json
import zlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.fernet import Fernet
import os

class NetworkCore:
    def __init__(self, key: bytes):
        """
        Initializes the network core with a 32-byte AES key.
        Fernet requires a url-safe base64-encoded 32-byte key.
        """
        import base64
        self.key = key
        self.aesgcm = AESGCM(key)
        self.fernet = Fernet(base64.urlsafe_b64encode(key))

    def _encrypt_fast(self, data: bytes) -> bytes:
        """Fast encryption for video/audio streams using AES-GCM."""
        nonce = os.urandom(12)
        ct = self.aesgcm.encrypt(nonce, data, None)
        return nonce + ct

    def _decrypt_fast(self, data: bytes) -> bytes:
        """Fast decryption for video/audio streams using AES-GCM."""
        nonce = data[:12]
        ct = data[12:]
        return self.aesgcm.decrypt(nonce, ct, None)

    def send_packet(self, sock, packet_type: int, data, is_json=False):
        """
        Unified sending for avoiding stream desync.
        Header: [Type (1 byte)] [Length (4 bytes)]
        """
        if is_json:
            payload = json.dumps(data).encode('utf-8')
            enc_payload = self.fernet.encrypt(payload)
        else:
            enc_payload = self._encrypt_fast(data)
            
        header = struct.pack("!BI", packet_type, len(enc_payload))
        sock.sendall(header + enc_payload)

    def recv_packet(self, sock):
        """
        Unified receiving.
        Returns (packet_type, data) where data is parsed JSON dict if type is 0 or 2 or 5 or 6, else bytes.
        """
        header = self._recv_exact(sock, 5)
        if not header:
            return None, None
            
        packet_type, length = struct.unpack("!BI", header)
        enc_payload = self._recv_exact(sock, length)
        if not enc_payload:
            return None, None
            
        # JSON Types: 0 (Handshake/Auth), 2 (Input), 5 (Chat), 6 (Stats)
        if packet_type in (0, 2, 5, 6):
            payload = self.fernet.decrypt(enc_payload)
            return packet_type, json.loads(payload.decode('utf-8'))
        else:
            return packet_type, self._decrypt_fast(enc_payload)

    def send_udp(self, sock, addr, packet_type: int, data: bytes):
        """Send a fast UDP frame (ignores errors). Dropping packets is fine for real-time video."""
        try:
            enc_payload = self._encrypt_fast(data)
            header = struct.pack("!BI", packet_type, len(enc_payload))
            if len(header) + len(enc_payload) < 65000:
                sock.sendto(header + enc_payload, addr)
        except Exception:
            pass

    def recv_udp(self, sock):
        """Receive a fast UDP frame."""
        try:
            data, addr = sock.recvfrom(65536)
            if len(data) < 5: return None, None, None
            packet_type, length = struct.unpack("!BI", data[:5])
            payload = self._decrypt_fast(data[5:5+length])
            return packet_type, payload, addr
        except Exception:
            return None, None, None

    def _recv_exact(self, sock, length: int) -> bytes:
        data = bytearray()
        while len(data) < length:
            try:
                packet = sock.recv(length - len(data))
                if not packet:
                    return None
                data.extend(packet)
            except Exception:
                return None
        return bytes(data)
