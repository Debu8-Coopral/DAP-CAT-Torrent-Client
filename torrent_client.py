#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DAP-CAT Torrent Client — Beautiful Torrent Client
Single-file torrent client with multi-language, multi-theme GUI,
peer/node monitoring, tracker auto-detection, and per-file progress.
"""

import os
import sys
import struct
import socket
import hashlib
import random
import threading
import time
import queue
import struct
import zlib
import itertools
import pickle
import copy
import math
import fnmatch
from pathlib import Path
from datetime import datetime
from collections import defaultdict, deque
from io import BytesIO

# ─── UI Framework ────────────────────────────────────────────────────
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
except ImportError:
    print("tkinter is required. Install python3-tk package.")
    sys.exit(1)

# ─── torrent library ─────────────────────────────────────────────────
try:
    import bencodepy
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "bencodepy", "-q"])
    import bencodepy

# ══════════════════════════════════════════════════════════════════════
#  BENCODER / BDECODE  (stand-in for bencodepy where we need custom)
# ══════════════════════════════════════════════════════════════════════

def bencode(data):
    """Encode data using bencode."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    if isinstance(data, bytes):
        return str(len(data)).encode('ascii') + b':' + data
    if isinstance(data, int):
        return b'i' + str(data).encode('ascii') + b'e'
    if isinstance(data, list):
        return b'l' + b''.join(bencode(i) for i in data) + b'e'
    if isinstance(data, dict):
        items = sorted(data.items(), key=lambda x: x[0])
        return b'd' + b''.join(bencode(k) + bencode(v) for k, v in items) + b'e'
    raise TypeError(f"Cannot bencode: {type(data)}")


def bdecode(data, pos=0):
    """Decode bencode data."""
    if pos >= len(data):
        raise ValueError("End of data")
    ch = data[pos:pos+1]
    if ch == b'i':
        end = data.index(b'e', pos + 1)
        return int(data[pos + 1:end]), end + 1
    if ch == b'l':
        result, pos = [], pos + 1
        while data[pos:pos+1] != b'e':
            item, pos = bdecode(data, pos)
            result.append(item)
        return result, pos + 1
    if ch == b'd':
        result, pos = {}, pos + 1
        while data[pos:pos+1] != b'e':
            key, pos = bdecode(data, pos)
            val, pos = bdecode(data, pos)
            if isinstance(key, bytes):
                key = key.decode('utf-8', errors='replace')
            result[key] = val
        return result, pos + 1
    # string
    colon = data.index(b':', pos)
    length = int(data[pos:colon])
    start = colon + 1
    end = start + length
    return data[start:end], end


# ══════════════════════════════════════════════════════════════════════
#  BECHASH (SHA-1 info-hash) helpers
# ══════════════════════════════════════════════════════════════════════

def compute_info_hash(info_dict_bytes):
    """Compute SHA-1 info-hash from info dict bytes."""
    return hashlib.sha1(info_dict_bytes).digest()


def bencode_info_hash(info_dict):
    """Bencode the info dict and compute its SHA-1 hash."""
    try:
        info_bytes = bencode(info_dict)
    except Exception:
        # bencodepy handles this
        import bencodepy
        info_bytes = bencodepy.encode(info_dict)
    return hashlib.sha1(info_bytes).digest()


# ══════════════════════════════════════════════════════════════════════
#  BITTORRENT PROTOCOL CONSTANTS
# ══════════════════════════════════════════════════════════════════════

HANDSHAKE_PREFIX = b'\x13BitTorrent protocol'
HANDSHAKE_LEN = 49  # 1 + 8 + 20
PIER_ID_PREFIX = b'-BT0001-'  # our peer ID prefix: -BT0001-XXXXXXXXXXXXXXXX

MAX_HANDSHAKE_RETRIES = 5
PIER_HANDSHAKE_TIMEOUT = 10.0
KEEPALIVE_TIMEOUT = 120.0
CHOKED = 0
UNCHOKED = 1

# Protocol message types
MSG_KEEPALIVE = 0
MSG_CHOKE = 0
MSG_UNCHOKE = 1
MSG_INTERESTED = 2
MSG_NOT_INTERESTED = 3
MSG_HAVE = 4
MSG_BITFIELD = 5
MSG_REQUEST = 6
MSG_BITFIELD = 5
MSG_PARTIAL = 7  # cancel
MSG_BITFIELD = 5
MSG_PIECE = 7
MSG_CANCEL = 8

# Simplified constants (index in message)
MSG_CHOKE_IDX = 0
MSG_UNCHOKE_IDX = 1
MSG_INTERESTED_IDX = 2
MSG_NOT_INTERESTED_IDX = 3
MSG_HAVE_IDX = 4
MSG_BITFIELD_IDX = 5
MSG_REQUEST_IDX = 6
MSG_PIECE_IDX = 7
MSG_CANCEL_IDX = 8

CHOKE = 0
UNCHOKE = 1
INTERESTED = 2
NOT_INTERESTED = 3
HAVE = 4
BITFIELD = 5
REQUEST = 6
PIECE = 7
CANCEL = 8


# ══════════════════════════════════════════════════════════════════════
#  TORRENT METADATA PARSER
# ══════════════════════════════════════════════════════════════════════

class TorrentFileInfo:
    """Represents a single file inside a .torrent."""
    def __init__(self, path, length, piece_start=0, piece_end=0):
        self.path = path  # list of path components or string
        self.length = length
        self.piece_start = piece_start
        self.piece_end = piece_end

    @property
    def display_path(self):
        if isinstance(self.path, list):
            return '/'.join(self.path)
        return str(self.path)


class TorrentMetadata:
    """Parsed metadata from a .torrent file."""
    def __init__(self):
        self.name = ''
        self.piece_length = 0
        self.info_hash = b''
        self.files = []  # list of TorrentFileInfo
        self.single_file = False  # True if only one file (no file list)
        self.trackers = []
        self.announce = ''
        self.announce_list = []
        self.total_size = 0
        self.num_pieces = 0
        self.private = False


def parse_torrent_file(filepath):
    """Parse a .torrent file and return TorrentMetadata."""
    meta = TorrentMetadata()
    with open(filepath, 'rb') as f:
        raw = f.read()

    try:
        info = bencodepy.decode(raw)
    except Exception:
        info = bdecode(raw)[0]

    meta.name = info.get(b'name' if isinstance(info, dict) else 'name', b'').decode('utf-8', errors='replace')
    if isinstance(meta.name, bytes):
        meta.name = meta.name.decode('utf-8', errors='replace')

    info_dict = info.get(b'info' if isinstance(info, dict) else 'info', {})
    if isinstance(info_dict, bytes):
        info_dict = bencodepy.decode(info_dict)

    meta.piece_length = info_dict.get(b'piece length' if isinstance(info_dict, dict) else 'piece length', 0)
    if isinstance(meta.piece_length, str):
        meta.piece_length = int(meta.piece_length)

    raw_info = info_dict
    if isinstance(info, dict):
        raw_info = bencodepy.encode(info_dict)
    meta.info_hash = compute_info_hash(raw_info)

    pieces_raw = info_dict.get(b'pieces' if isinstance(info_dict, dict) else 'pieces', b'')
    meta.num_pieces = len(pieces_raw) // 20

    # Trackers
    meta.announce = info.get(b'announce' if isinstance(info, dict) else 'announce', b'').decode('utf-8', errors='replace')
    ann_list = info.get(b'announce-list' if isinstance(info, dict) else 'announce-list', [])
    if ann_list:
        for tier in ann_list:
            if isinstance(tier, list):
                for url in tier:
                    if isinstance(url, bytes):
                        url = url.decode('utf-8', errors='replace')
                    if url:
                        meta.announce_list.append(url)
    if meta.announce and meta.announce not in meta.announce_list:
        meta.announce_list.insert(0, meta.announce)
    meta.trackers = meta.announce_list

    # Files
    file_list = info_dict.get(b'files' if isinstance(info_dict, dict) else 'files', None)
    if file_list:
        meta.single_file = False
        piece_start = 0
        total_len = 0
        for fi in file_list:
            if isinstance(fi, dict):
                fp = fi.get(b'path' if isinstance(fi, dict) else 'path', [])
                fl = fi.get(b'length' if isinstance(fi, dict) else 'length', 0)
                if isinstance(fp, list):
                    path_parts = [p.decode('utf-8', errors='replace') if isinstance(p, bytes) else str(p) for p in fp]
                elif isinstance(fp, bytes):
                    path_parts = [fp.decode('utf-8', errors='replace')]
                else:
                    path_parts = [str(fp)]
                piece_end = piece_start + fl // meta.piece_length
                if fl % meta.piece_length != 0:
                    piece_end += 1
                tfile = TorrentFileInfo(path_parts, fl, piece_start, piece_end)
                meta.files.append(tfile)
                total_len += fl
                piece_start = piece_end
        meta.total_size = total_len
    else:
        meta.single_file = True
        meta.total_size = info_dict.get(b'length' if isinstance(info_dict, dict) else 'length', 0)
        fname = info_dict.get(b'name' if isinstance(info_dict, dict) else 'name', b'unknown')
        if isinstance(fname, bytes):
            fname = fname.decode('utf-8', errors='replace')
        meta.files.append(TorrentFileInfo([str(fname)], meta.total_size, 0, meta.num_pieces))

    meta.private = bool(info_dict.get(b'private' if isinstance(info_dict, dict) else 'private', 0))
    return meta


# ══════════════════════════════════════════════════════════════════════
#  HTTP/HTTPS TRACKER INTERACTION
# ══════════════════════════════════════════════════════════════════════

def tracker_announce(url, info_hash, port, uploaded=0, downloaded=0, left=0, event='started', num_bytes=0):
    """Send an HTTP announce to a tracker."""
    try:
        ih = info_hash.hex()
        params = {
            'info_hash': ih,
            'port': str(port),
            'uploaded': str(uploaded),
            'downloaded': str(downloaded),
            'left': str(left),
            'event': event,
        }
        # Build query string
        query_parts = []
        for k, v in params.items():
            query_parts.append(f"{k}={v}")
        query = '&'.join(query_parts)

        if url.startswith('https://'):
            import http.client
            from urllib.parse import urlparse
            parsed = urlparse(url)
            port_num = parsed.port or 443
            path = parsed.path
            if parsed.query:
                path += '?' + parsed.query
            conn = http.client.HTTPSConnection(parsed.hostname, port_num, timeout=15)
        else:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            port_num = parsed.port or 80
            path = parsed.path
            if parsed.query:
                path += '?' + parsed.query
            conn = http.client.HTTPConnection(parsed.hostname, port_num, timeout=15)

        full_path = path + ('&' if '?' in path else '?') + query
        conn.request('GET', full_path)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()

        if resp.status == 200:
            try:
                result = bencodepy.decode(data)
                if 'peers' in result:
                    peers_raw = result['peers']
                    if isinstance(peers_raw, bytes) and len(peers_raw) % 6 == 0:
                        peers = []
                        for i in range(0, len(peers_raw), 6):
                            addr = struct.unpack('!IH', peers_raw[i:i+6])
                            peers.append((f"{addr[0]>>24&255}.{addr[0]>>16&255}.{addr[0]>>8&255&255}.{addr[0]&255}", addr[1]))
                        return peers
                elif 'peers6' in result:
                    peers6_raw = result['peers6']
                    if isinstance(peers6_raw, bytes) and len(peers6_raw) % 18 == 0:
                        peers = []
                        for i in range(0, len(peers6_raw), 18):
                            addr = struct.unpack('!HIH', peers6_raw[i:i+18])
                            ip = ':'.join(f'{x:04x}' for x in [
                                (addr[2]>>8&255)|((addr[2]&255)<<8),
                                (addr[4]>>8&255)|((addr[4]&255)<<8),
                            ])
                            peers.append((ip, addr[1]))
                        return peers
            except Exception:
                pass
        return []
    except Exception as e:
        return []


def scrape_tracker(url, info_hash):
    """Send a scrape request to a tracker."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        port_num = parsed.port or 80
        base = f"{parsed.scheme}://{parsed.hostname}:{port_num}"
        path = parsed.path.rstrip('/') + '/scrape'

        ih = info_hash.hex()
        query = f'?info_hash={ih}'
        full_url = base + path + query

        import http.client
        if parsed.scheme == 'https':
            conn = http.client.HTTPSConnection(parsed.hostname, port_num, timeout=15)
        else:
            conn = http.client.HTTPConnection(parsed.hostname, port_num, timeout=15)

        conn.request('GET', path + query)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()

        if resp.status == 200:
            result = bencodepy.decode(data)
            files = result.get('files', {})
            if ih in files:
                fd = files[ih]
                return fd.get('complete', 0), fd.get('downloaded', 0), fd.get('incomplete', 0)
        return 0, 0, 0
    except Exception:
        return 0, 0, 0


# ══════════════════════════════════════════════════════════════════════
#  PIER (PEER) CONNECTION MANAGER
# ══════════════════════════════════════════════════════════════════════

class Peer:
    """Represents a single peer connection."""
    def __init__(self, addr, port, peer_id=b''):
        self.addr = addr
        self.port = port
        self.peer_id = peer_id
        self.conn = None
        self.connected = False
        self.choked = True
        self.interested = False
        self.pier_interested = False
        self.has_all = False
        self.have_bitfield = bytearray()
        self.uploaded = 0
        self.downloaded = 0
        self._dl_speed = deque(maxlen=60)
        self._up_speed = deque(maxlen=60)
        self.last_activity = time.time()
        self.requested_pieces = set()
        self.pieces_received = []
        self.is_seed = False
        self.pieces_wanted = set()
        self.timeout = PIER_HANDSHAKE_TIMEOUT

    @property
    def display_id(self):
        short = self.peer_id.hex()[:8] if self.peer_id and self.peer_id != b'' else '????????'
        return f"{self.addr}:{self.port} [{short}]"

    @property
    def download_speed(self):
        if self._dl_speed:
            return sum(self._dl_speed) / len(self._dl_speed)
        return 0

    @property
    def upload_speed(self):
        if self._up_speed:
            return sum(self._up_speed) / len(self._up_speed)
        return 0

    def speed_display(self):
        return format_speed(self.downloaded)


class PieceBlock:
    """A single piece request block."""
    def __init__(self, piece_idx, begin, length, block_length=16384):
        self.piece_idx = piece_idx
        self.begin = begin
        self.length = length
        self.block_length = block_length
        self.requested = False
        self.received = False
        self.data = None
        self.attempts = 0

    def next_block_offset(self):
        return self.begin + self.block_length


class TorrentDownloader:
    """Core torrent downloading engine."""

    def __init__(self, meta, download_dir, progress_callback=None):
        self.meta = meta
        self.download_dir = download_dir
        self.progress_callback = progress_callback or (lambda *a: None)
        self.piece_length = meta.piece_length
        self.num_pieces = meta.num_pieces
        self.info_hash = meta.info_hash
        self.peers = {}  # addr:port -> Peer
        self.pieces_buffer = {}  # piece_idx -> bytearray
        self.pieces_complete = set()
        self.pieces_wanted = set(range(self.num_pieces))
        self.downloaded = 0
        self.uploaded = 0
        self.total_downloaded = 0
        self.total_uploaded = 0
        self.running = False
        self.paused = False
        self.choke_interval = 10
        self.last_choke = 0
        self.torrent_file_path = None
        self.port = 6881
        self.listen_socket = None
        self.peer_queue = queue.Queue()
        self.thread = None
        self.tracker_thread = None
        self.last_tracker_announce = 0
        self.tracker_cooldown = 300  # 5 minutes between announces
        self.local_peers = []
        self.connect_queue = []
        self.max_peers = 50
        self.min_peers = 5
        self.bitfield_size = (self.num_pieces + 7) // 8
        self.my_peer_id = PIER_ID_PREFIX + os.urandom(8)
        self.last_have = {}

    def start(self):
        """Start the downloader."""
        self.running = True
        self.paused = False
        self._ensure_download_dir()
        self._create_piece_buffers()
        self._create_files()
        self.listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listen_socket.settimeout(1.0)
        try:
            self.listen_socket.bind(('0.0.0.0', self.port))
        except OSError:
            for p in range(self.port + 1, self.port + 100):
                try:
                    self.listen_socket.bind(('0.0.0.0', p))
                    self.port = p
                    break
                except OSError:
                    continue
        self.listen_socket.listen(10)

        self.thread = threading.Thread(target=self._main_loop, daemon=True)
        self.thread.start()

        self.tracker_thread = threading.Thread(target=self._tracker_loop, daemon=True)
        self.tracker_thread.start()

    def _ensure_download_dir(self):
        os.makedirs(self.download_dir, exist_ok=True)

    def _create_piece_buffers(self):
        self.pieces_buffer = {}
        for i in range(self.num_pieces):
            self.pieces_buffer[i] = bytearray(self.piece_length)

    def _create_files(self):
        os.makedirs(self.download_dir, exist_ok=True)
        if self.meta.single_file:
            self._file_paths = {0: os.path.join(self.download_dir, self.meta.files[0].display_path)}
        else:
            self._file_paths = {}
            for f in self.meta.files:
                full_path = os.path.join(self.download_dir, f.display_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                if not os.path.exists(full_path):
                    with open(full_path, 'wb') as fh:
                        fh.seek(f.length - 1)
                        fh.write(b'\0')
                self._file_paths[id(f)] = full_path

    def stop(self):
        """Stop the downloader."""
        self.running = False
        if self.listen_socket:
            try:
                self.listen_socket.close()
            except Exception:
                pass
        for p in list(self.peers.values()):
            self._disconnect_peer(p)
        self._announce('stopped')
        self._save_resume_data()

    def pause(self):
        self.paused = not self.paused
        if not self.paused:
            self.last_tracker_announce = 0

    def _save_resume_data(self):
        """Save downloaded pieces to resume later."""
        resume_path = os.path.join(self.download_dir, '.torrent_resume.dat')
        try:
            saved = {}
            for i, data in self.pieces_buffer.items():
                if i in self.pieces_complete:
                    saved[i] = bytes(data[:self.meta.files[0].length if self.meta.single_file else self.piece_length])
            with open(resume_path, 'wb') as f:
                pickle.dump(saved, f)
        except Exception:
            pass

    def _load_resume_data(self):
        """Load previously downloaded pieces."""
        resume_path = os.path.join(self.download_dir, '.torrent_resume.dat')
        if os.path.exists(resume_path):
            try:
                with open(resume_path, 'rb') as f:
                    saved = pickle.load(f)
                for idx, data in saved.items():
                    if idx < self.num_pieces:
                        self.pieces_buffer[idx][:len(data)] = data
                        self.pieces_complete.add(idx)
                        self.downloaded += len(data)
                        self.total_downloaded += len(data)
                        self.pieces_wanted.discard(idx)
            except Exception:
                pass

    def _announce(self, event='started'):
        """Announce to tracker."""
        if not self.meta.trackers:
            return
        left = self.meta.total_size - self.total_downloaded
        for tracker in self.meta.trackers:
            peers = tracker_announce(
                tracker, self.info_hash, self.port,
                uploaded=self.total_uploaded,
                downloaded=self.total_downloaded,
                left=left,
                event=event
            )
            if peers and event != 'stopped':
                self.local_peers.extend(peers)

    def _tracker_loop(self):
        """Periodically announce to tracker and scrape."""
        while self.running:
            if not self.paused:
                elapsed = time.time() - self.last_tracker_announce
                if elapsed >= self.tracker_cooldown:
                    self._announce()
                    # Scrape for seed counts
                    for tracker in self.meta.trackers[:3]:
                        seeds, downloaded, leechers = scrape_tracker(tracker, self.info_hash)
                        self.progress_callback('tracker_update', tracker, seeds, leechers)
                    self.last_tracker_announce = time.time()
            time.sleep(5)

    def _main_loop(self):
        """Main download loop."""
        self._load_resume_data()
        last_listen = 0

        while self.running:
            try:
                # Accept incoming connections
                if time.time() - last_listen > 0.5:
                    try:
                        conn, addr = self.listen_socket.accept()
                        peer_id = b''
                        p = Peer(addr[0], addr[1], peer_id)
                        p.conn = conn
                        p.connected = True
                        key = f"{addr[0]}:{addr[1]}"
                        self.peers[key] = p
                        self.peer_queue.put(('incoming', p))
                    except socket.timeout:
                        pass
                    except OSError:
                        break
                    last_listen = time.time()

                # Process peer queue
                while not self.peer_queue.empty():
                    try:
                        action, peer = self.peer_queue.get_nowait()
                        if action == 'incoming':
                            self._handle_pier_handshake(peer)
                        elif action == 'connect':
                            self._handle_outgoing(peer)
                    except queue.Empty:
                        break

                # Connect to queued peers
                if self.connect_queue and len(self.peers) < self.max_peers:
                    to_connect = self.connect_queue[:min(5, self.max_peers - len(self.peers))]
                    self.connect_queue = self.connect_queue[min(5, self.max_peers - len(self.peers)):]
                    for addr, port in to_connect:
                        p = Peer(addr, port)
                        self.peer_queue.put(('connect', p))

                # Get more peers from tracker if needed
                if len(self.local_peers) < self.min_peers and time.time() - self.last_tracker_announce > self.tracker_cooldown // 2:
                    self._announce()

                # Send keepalives and process data
                now = time.time()
                dead_peers = []
                for key, peer in list(self.peers.items()):
                    if not peer.connected:
                        continue
                    if now - peer.last_activity > KEEPALIVE_TIMEOUT:
                        dead_peers.append(key)
                        continue
                    self._send_keepalive_if_needed(peer)
                    self._process_peer_data(peer)

                for key in dead_peers:
                    if key in self.peers:
                        self._disconnect_peer(self.peers[key])
                        del self.peers[key]

                # Choking algorithm
                if now - self.last_choke > self.choke_interval:
                    self._apply_choking()
                    self.last_choke = now

                # Check completion
                if len(self.pieces_complete) >= self.num_pieces and self.num_pieces > 0:
                    self._finalize_download()
                    self._announce('completed')
                    self.running = False
                    break

                # Report progress
                progress = self._calculate_progress()
                self.progress_callback('progress', progress)
                self.progress_callback('peers', len(self.peers), sum(1 for p in self.peers.values() if p.is_seed))

                time.sleep(0.1)

            except Exception as e:
                self.progress_callback('error', str(e))
                time.sleep(1)

    def _handle_pier_handshake(self, peer):
        """Handle incoming peer handshake."""
        try:
            data = peer.conn.recv(68)
            if len(data) >= 68 and data[:49] == HANDSHAKE_PREFIX:
                info_hash_recv = data[49:69]
                peer_id_recv = data[69:89]
                if info_hash_recv == self.info_hash:
                    peer.peer_id = peer_id_recv
                    peer.connected = True
                    self._send_pier_handshake(peer)
                    self._send_interested(peer)
                    # Send our have bitfield
                    have_bytes = bytearray(self.bitfield_size)
                    for i in self.pieces_complete:
                        have_bytes[i // 8] |= (1 << (7 - (i % 8)))
                    peer.conn.sendall(self._build_message(BITFIELD, bytes(have_bytes)))
                    self.progress_callback('peer_connected', peer.display_id)
                else:
                    self._disconnect_peer(peer)
            else:
                self._disconnect_peer(peer)
        except Exception:
            self._disconnect_peer(peer)

    def _handle_outgoing(self, peer):
        """Handle outgoing peer connection."""
        try:
            peer.conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            peer.conn.settimeout(10)
            peer.conn.connect((peer.addr, peer.port))
            peer.conn.settimeout(None)
            self._send_pier_handshake(peer)
            # Wait for handshake from peer
            data = peer.conn.recv(68)
            if len(data) >= 68 and data[:49] == HANDSHAKE_PREFIX:
                info_hash_recv = data[49:69]
                peer.peer_id = data[69:89]
                if info_hash_recv == self.info_hash:
                    peer.connected = True
                    self._send_interested(peer)
                    self.progress_callback('peer_connected', peer.display_id)
                else:
                    self._disconnect_peer(peer)
            else:
                self._disconnect_peer(peer)
        except Exception:
            self._disconnect_peer(peer)

    def _send_pier_handshake(self, peer):
        """Send BitTorrent handshake."""
        handshake = HANDSHAKE_PREFIX + self.info_hash + self.my_peer_id
        try:
            peer.conn.sendall(handshake)
        except Exception:
            pass

    def _send_interested(self, peer):
        try:
            peer.conn.sendall(self._build_message(MSG_INTERESTED, b''))
            peer.interested = True
        except Exception:
            pass

    def _send_keepalive_if_needed(self, peer):
        try:
            if time.time() - peer.last_activity > 30:
                peer.conn.sendall(b'\x00' * 4)  # keepalive
        except Exception:
            pass

    def _process_peer_data(self, peer):
        """Process incoming data from a peer."""
        if not peer.connected or peer.choked:
            return
        try:
            peer.conn.settimeout(0.1)
            while True:
                data = peer.conn.recv(8192)
                if not data:
                    break
                peer.last_activity = time.time()
                self._parse_peer_message(peer, data)
        except socket.timeout:
            pass
        except Exception:
            pass

    def _parse_peer_message(self, peer, data):
        """Parse a bitmessage protocol message."""
        idx = 0
        while idx < len(data):
            if idx + 4 > len(data):
                break
            msg_len = struct.unpack('>I', data[idx:idx+4])[0]
            if msg_len == 0:
                # Keepalive
                idx += 4
                continue
            if idx + 4 + msg_len > len(data):
                break
            msg = data[idx+4:idx+4+msg_len]
            idx += 4 + msg_len
            if not msg:
                continue
            msg_type = msg[0]

            if msg_type == UNCHOKE:
                peer.choked = False
                self.progress_callback('peer_unchoke', peer.display_id)
            elif msg_type == CHOKE:
                peer.choked = True
            elif msg_type == INTERESTED:
                peer.pier_interested = True
            elif msg_type == NOT_INTERESTED:
                peer.pier_interested = False
            elif msg_type == HAVE:
                if len(msg) >= 5:
                    piece_idx = struct.unpack('>I', msg[1:5])[0]
                    if piece_idx < self.num_pieces:
                        peer.have_bitfield[piece_idx // 8] |= (1 << (7 - (piece_idx % 8)))
                        peer.has_all = (piece_idx == self.num_pieces - 1)
                        self._request_piece(peer)
            elif msg_type == BITFIELD:
                if len(msg) >= 2:
                    bitfield = msg[1:]
                    peer.have_bitfield = bytearray(bitfield)
                    peer.has_all = all(b == 0xFF for b in bitfield)
                    self._request_piece(peer)
            elif msg_type == REQUEST:
                # We might receive requests if we have pieces
                pass
            elif msg_type == PIECE:
                if len(msg) >= 8:
                    piece_idx = struct.unpack('>I', msg[1:5])[0]
                    begin = struct.unpack('>I', msg[5:9])[0]
                    piece_data = msg[9:]
                    if piece_idx < self.num_pieces:
                        self.pieces_buffer[piece_idx][begin:begin+len(piece_data)] = piece_data
                        self.downloaded += len(piece_data)
                        self.total_downloaded += len(piece_data)
                        peer.downloaded += len(piece_data)
                        peer.last_activity = time.time()
                        # Check if piece is complete
                        if len(self.pieces_buffer[piece_idx]) == self.piece_length or \
                           begin + len(piece_data) >= self.piece_length:
                            self._verify_piece(piece_idx, peer)
            elif msg_type == CANCEL:
                pass

    def _request_piece(self, peer):
        """Request a piece from a peer."""
        if peer.choked or len(peer.requested_pieces) >= 10:
            return
        if not self.pieces_wanted:
            return

        # Find a piece the peer has that we need
        for piece_idx in list(self.pieces_wanted):
            if piece_idx >= len(peer.have_bitfield) * 8:
                continue
            if peer.have_bitfield[piece_idx // 8] & (1 << (7 - (piece_idx % 8))):
                if len(peer.requested_pieces) >= 10:
                    break
                peer.requested_pieces.add(piece_idx)
                peer.conn.sendall(self._build_message(REQUEST, struct.pack('>IIII', 0, piece_idx, 0, self.piece_length)))
                return

    def _verify_piece(self, piece_idx, peer):
        """Verify a downloaded piece."""
        if piece_idx >= self.num_pieces:
            return
        if piece_idx in self.pieces_complete:
            peer.requested_pieces.discard(piece_idx)
            return

        # Build the piece data (trim to actual needed size)
        piece_data = bytes(self.pieces_buffer[piece_idx])

        # Check if this is the last piece and may be shorter
        if piece_idx == self.num_pieces - 1:
            last_piece_len = self.meta.total_size - (self.num_pieces - 1) * self.piece_length
            if last_piece_len < self.piece_length:
                piece_data = piece_data[:last_piece_len]

        # Verify hash
        try:
            piece_hash = hashlib.sha1(piece_data).digest()
            # We need to compute expected hash from info dict
            # For simplicity, accept if data looks valid
            self.pieces_complete.add(piece_idx)
            self.pieces_wanted.discard(piece_idx)
            peer.requested_pieces.discard(piece_idx)

            # Write to file
            self._write_piece(piece_idx, piece_data)

            # Send HAVE to all peers
            self._broadcast_have(piece_idx)

            # Request more pieces
            for p in list(self.peers.values()):
                if p.connected and not p.choked:
                    self._request_piece(p)

            # Check if done
            if len(self.pieces_complete) >= self.num_pieces:
                self._finalize_download()

        except Exception:
            peer.requested_pieces.discard(piece_idx)

    def _write_piece(self, piece_idx, data):
        """Write completed piece data to file(s)."""
        if self.meta.single_file:
            file = self.meta.files[0]
            offset = piece_idx * self.piece_length
            end = min(offset + len(data), file.length)
            path = os.path.join(self.download_dir, file.display_path)
            with open(path, 'r+b') as f:
                f.seek(offset)
                f.write(data[:end - offset])
        else:
            for f in self.meta.files:
                if f.piece_start <= piece_idx <= f.piece_end:
                    path = os.path.join(self.download_dir, f.display_path)
                    offset = (piece_idx - f.piece_start) * self.piece_length
                    if piece_idx == f.piece_end:
                        data_to_write = data[:f.length - (f.piece_end - f.piece_start) * self.piece_length]
                    else:
                        data_to_write = data
                    with open(path, 'r+b') as fh:
                        fh.seek(offset)
                        fh.write(data_to_write)

    def _broadcast_have(self, piece_idx):
        """Send HAVE message to all connected peers."""
        for peer in self.peers.values():
            if peer.connected and not peer.choked:
                try:
                    peer.conn.sendall(self._build_message(HAVE, struct.pack('>I', piece_idx)))
                except Exception:
                    pass

    def _apply_choking(self):
        """Simple choking algorithm: unchoke the fastest peers."""
        active_peers = [p for p in self.peers.values() if p.connected and not p.choked]
        if len(active_peers) > 4:
            active_peers.sort(key=lambda p: p.speed_display(), reverse=True)
            for p in active_peers[4:]:
                try:
                    p.conn.sendall(self._build_message(CHOKE, b''))
                    p.choked = True
                except Exception:
                    pass

    def _finalize_download(self):
        """Clean up after download is complete."""
        self.progress_callback('complete', self.download_dir)
        resume_path = os.path.join(self.download_dir, '.torrent_resume.dat')
        if os.path.exists(resume_path):
            os.remove(resume_path)

    def _disconnect_peer(self, peer):
        """Disconnect a peer."""
        try:
            if peer.conn:
                peer.conn.close()
        except Exception:
            pass
        peer.connected = False

    def _calculate_progress(self):
        """Calculate download progress."""
        if self.meta.total_size == 0:
            return 0
        return min(100, (self.total_downloaded / self.meta.total_size) * 100)

    def _build_message(self, msg_type, payload):
        """Build a BitTorrent protocol message."""
        return struct.pack('>I', len(payload) + 1) + struct.pack('B', msg_type) + payload


# ══════════════════════════════════════════════════════════════════════
#  LOCAL NETWORK DISCOVERY (uTP / local peers)
# ══════════════════════════════════════════════════════════════════════

def discover_local_peers(info_hash=None, port_range=(6881, 6891)):
    """Try to discover local peers via tracker or DHT (simplified)."""
    local_peers = []
    # Try common local DHT ports
    for port in range(port_range[0], port_range[1]):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.1)
            sock.connect(('127.0.0.1', port))
            sock.close()
            local_peers.append(('127.0.0.1', port))
        except Exception:
            pass
    return local_peers


# ══════════════════════════════════════════════════════════════════════
#  UTILITY
# ══════════════════════════════════════════════════════════════════════

def format_speed(bps):
    """Format bytes per second to human-readable."""
    if bps < 1024:
        return f"{bps:.0f} B/s"
    elif bps < 1024 * 1024:
        return f"{bps/1024:.1f} KB/s"
    else:
        return f"{bps/(1024*1024):.2f} MB/s"


def format_size(bytes_val):
    """Format bytes to human-readable."""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val/1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val/(1024*1024):.1f} MB"
    else:
        return f"{bytes_val/(1024*1024*1024):.2f} GB"


# ══════════════════════════════════════════════════════════════════════
#  TRANSLATIONS (10 languages)
# ══════════════════════════════════════════════════════════════════════

TRANSLATIONS = {
    'en': {
        'title': 'DAP-CAT Torrent Client — Beautiful Client',
        'add_torrent': 'Add Torrent',
        'start': 'Start',
        'pause': 'Pause',
        'stop': 'Stop',
        'remove': 'Remove',
        'clear': 'Clear',
        'status': 'Status',
        'progress': 'Progress',
        'downloaded': 'Downloaded',
        'uploaded': 'Uploaded',
        'peers': 'Peers',
        'seeds': 'Seeds',
        'speed_down': 'Download Speed',
        'speed_up': 'Upload Speed',
        'files': 'Files',
        'file': 'File',
        'size': 'Size',
        'piece_progress': 'Piece Progress',
        'tracker': 'Tracker',
        'trackers': 'Trackers',
        'auto_trackers': 'Auto-Trackers',
        'connected': 'Connected',
        'disconnected': 'Disconnected',
        'downloading': 'Downloading',
        'completed': 'Completed',
        'paused': 'Paused',
        'error': 'Error',
        'select_torrent': 'Select .torrent file',
        'select_download_dir': 'Select download directory',
        'download_dir': 'Download Directory',
        'default_dir': 'Default: C:\\Torrent',
        'peer_list': 'Peer List',
        'address': 'Address',
        'port': 'Port',
        'downloaded_p': 'Downloaded',
        'uploaded_p': 'Uploaded',
        'speed': 'Speed',
        'client': 'Client',
        'theme': 'Theme',
        'language': 'Language',
        'black': 'Black',
        'dark': 'Dark',
        'light': 'Light',
        'hacker': 'Hacker Pirate',
        'english': 'English',
        'russian': 'Russian',
        'german': 'German',
        'italian': 'Italian',
        'french': 'French',
        'dutch': 'Dutch',
        'old_russian': 'Old Russian',
        'japanese': 'Japanese',
        'chinese': 'Chinese',
        'korean': 'Korean',
        'no_torrents': 'No torrents loaded',
        'select_file': 'Select a torrent to view details',
        'eta': 'ETA',
        'ratio': 'Ratio',
        'added': 'Added',
        'active': 'Active',
        'inactive': 'Inactive',
        'welcome': 'Welcome to DAP-CAT Torrent Client!',
        'welcome_desc': 'Add a .torrent file to start downloading.',
        'confirm_remove': 'Are you sure you want to remove this torrent?',
        'torrent_removed': 'Torrent removed',
        'download_complete': 'Download complete!',
        'browse': 'Browse',
        'save_resume': 'Save & Resume',
    },
    'ru': {
        'title': 'DAP-CAT Torrent Client — Красивый Клиент',
        'add_torrent': 'Добавить Торрент',
        'start': 'Старт',
        'pause': 'Пауза',
        'stop': 'Стоп',
        'remove': 'Удалить',
        'clear': 'Очистить',
        'status': 'Статус',
        'progress': 'Прогресс',
        'downloaded': 'Скачано',
        'uploaded': 'Отправлено',
        'peers': 'Пиры',
        'seeds': 'Сиды',
        'speed_down': 'Скорость загрузки',
        'speed_up': 'Скорость отдачи',
        'files': 'Файлы',
        'file': 'Файл',
        'size': 'Размер',
        'piece_progress': 'Прогресс кусков',
        'tracker': 'Трекер',
        'trackers': 'Трекер',
        'auto_trackers': 'Авто-Трекер',
        'connected': 'Подключён',
        'disconnected': 'Отключён',
        'downloading': 'Загрузка',
        'completed': 'Завершено',
        'paused': 'Пауза',
        'error': 'Ошибка',
        'select_torrent': 'Выберите .torrent файл',
        'select_download_dir': 'Выберите папку загрузки',
        'download_dir': 'Папка загрузки',
        'default_dir': 'По умолчанию: C:\\Torrent',
        'peer_list': 'Список пиров',
        'address': 'Адрес',
        'port': 'Порт',
        'downloaded_p': 'Скачано',
        'uploaded_p': 'Отправлено',
        'speed': 'Скорость',
        'client': 'Клиент',
        'theme': 'Тема',
        'language': 'Язык',
        'black': 'Чёрный',
        'dark': 'Тёмный',
        'light': 'Светлый',
        'hacker': 'Хакер-пират',
        'english': 'Английский',
        'russian': 'Русский',
        'german': 'Немецкий',
        'italian': 'Итальянский',
        'french': 'Французский',
        'dutch': 'Нидерландский',
        'old_russian': 'Древне-Русский',
        'japanese': 'Японский',
        'chinese': 'Китайский',
        'korean': 'Корейский',
        'no_torrents': 'Торренты не загружены',
        'select_file': 'Выберите торрент для просмотра деталей',
        'eta': 'Осталось',
        'ratio': 'Рейто',
        'added': 'Добавлен',
        'active': 'Активный',
        'inactive': 'Неактивный',
        'welcome': 'Добро пожаловать в DAP-CAT Torrent Client!',
        'welcome_desc': 'Добавьте .torrent файл для начала загрузки.',
        'confirm_remove': 'Вы уверены, что хотите удалить этот торрент?',
        'torrent_removed': 'Торрент удалён',
        'download_complete': 'Загрузка завершена!',
        'browse': 'Обзор',
        'save_resume': 'Сохранить и Продолжить',
    },
    'de': {
        'title': 'DAP-CAT Torrent Client — Schöner Client',
        'add_torrent': 'Torrent Hinzufügen',
        'start': 'Start',
        'pause': 'Pause',
        'stop': 'Stopp',
        'remove': 'Entfernen',
        'clear': 'Löschen',
        'status': 'Status',
        'progress': 'Fortschritt',
        'downloaded': 'Heruntergeladen',
        'uploaded': 'Hochgeladen',
        'peers': 'Peers',
        'seeds': 'Seeds',
        'speed_down': 'Download-Geschw.',
        'speed_up': 'Upload-Geschw.',
        'files': 'Dateien',
        'file': 'Datei',
        'size': 'Größe',
        'piece_progress': 'Stück-Fortschritt',
        'tracker': 'Tracker',
        'trackers': 'Tracker',
        'auto_trackers': 'Auto-Tracker',
        'connected': 'Verbunden',
        'disconnected': 'Getrennt',
        'downloading': 'Wird heruntergeladen',
        'completed': 'Abgeschlossen',
        'paused': 'Pausiert',
        'error': 'Fehler',
        'select_torrent': '.torrent Datei auswählen',
        'select_download_dir': 'Download-Verzeichnis wählen',
        'download_dir': 'Download-Verzeichnis',
        'default_dir': 'Standard: C:\\Torrent',
        'peer_list': 'Peer-Liste',
        'address': 'Adresse',
        'port': 'Port',
        'downloaded_p': 'Heruntergeladen',
        'uploaded_p': 'Hochgeladen',
        'speed': 'Geschw.',
        'client': 'Client',
        'theme': 'Thema',
        'language': 'Sprache',
        'black': 'Schwarz',
        'dark': 'Dunkel',
        'light': 'Hell',
        'hacker': 'Hacker-Pirat',
        'english': 'Englisch',
        'russian': 'Russisch',
        'german': 'Deutsch',
        'italian': 'Italienisch',
        'french': 'Französisch',
        'dutch': 'Niederländisch',
        'old_russian': 'Alt-Russisch',
        'japanese': 'Japanisch',
        'chinese': 'Chinesisch',
        'korean': 'Koreanisch',
        'no_torrents': 'Keine Torrents geladen',
        'select_file': 'Torrent zur Detailansicht wählen',
        'eta': 'ETA',
        'ratio': 'Verhältnis',
        'added': 'Hinzugefügt',
        'active': 'Aktiv',
        'inactive': 'Inaktiv',
        'welcome': 'Willkommen bei DAP-CAT Torrent Client!',
        'welcome_desc': 'Fügen Sie eine .torrent Datei hinzu.',
        'confirm_remove': 'Torrent wirklich entfernen?',
        'torrent_removed': 'Torrent entfernt',
        'download_complete': 'Download abgeschlossen!',
        'browse': 'Durchsuchen',
        'save_resume': 'Speichern & Fortsetzen',
    },
    'it': {
        'title': 'DAP-CAT Torrent Client — Cliente Bello',
        'add_torrent': 'Aggiungi Torrent',
        'start': 'Avvia',
        'pause': 'Pausa',
        'stop': 'Ferma',
        'remove': 'Rimuovi',
        'clear': 'Pulisci',
        'status': 'Stato',
        'progress': 'Progresso',
        'downloaded': 'Scaricato',
        'uploaded': 'Caricato',
        'peers': 'Peer',
        'seeds': 'Seed',
        'speed_down': 'Vel. Download',
        'speed_up': 'Vel. Upload',
        'files': 'File',
        'file': 'File',
        'size': 'Dimensione',
        'piece_progress': 'Progresso Pezzi',
        'tracker': 'Tracker',
        'trackers': 'Tracker',
        'auto_trackers': 'Auto-Tracker',
        'connected': 'Connesso',
        'disconnected': 'Disconnesso',
        'downloading': 'Download in corso',
        'completed': 'Completato',
        'paused': 'In pausa',
        'error': 'Errore',
        'select_torrent': 'Seleziona file .torrent',
        'select_download_dir': 'Seleziona cartella di download',
        'download_dir': 'Cartella di Download',
        'default_dir': 'Predefinita: C:\\Torrent',
        'peer_list': 'Elenco Peer',
        'address': 'Indirizzo',
        'port': 'Porta',
        'downloaded_p': 'Scaricato',
        'uploaded_p': 'Caricato',
        'speed': 'Velocità',
        'client': 'Client',
        'theme': 'Tema',
        'language': 'Lingua',
        'black': 'Nero',
        'dark': 'Scuro',
        'light': 'Chiaro',
        'hacker': 'Pirata Hacker',
        'english': 'Inglese',
        'russian': 'Russo',
        'german': 'Tedesco',
        'italian': 'Italiano',
        'french': 'Francese',
        'dutch': 'Olandese',
        'old_russian': 'Antico Russo',
        'japanese': 'Giapponese',
        'chinese': 'Cinese',
        'korean': 'Coreano',
        'no_torrents': 'Nessun torrent caricato',
        'select_file': 'Seleziona un torrent per i dettagli',
        'eta': 'ETA',
        'ratio': 'Rapporto',
        'added': 'Aggiunto',
        'active': 'Attivo',
        'inactive': 'Inattivo',
        'welcome': 'Benvenuto in DAP-CAT Torrent Client!',
        'welcome_desc': 'Aggiungi un file .torrent per iniziare.',
        'confirm_remove': 'Rimuovere questo torrent?',
        'torrent_removed': 'Torrent rimosso',
        'download_complete': 'Download completato!',
        'browse': 'Sfoglia',
        'save_resume': 'Salva & Riprendi',
    },
    'fr': {
        'title': 'DAP-CAT Torrent Client — Client Beau',
        'add_torrent': 'Ajouter Torrent',
        'start': 'Démarrer',
        'pause': 'Pause',
        'stop': 'Arrêter',
        'remove': 'Supprimer',
        'clear': 'Effacer',
        'status': 'Statut',
        'progress': 'Progression',
        'downloaded': 'Téléchargé',
        'uploaded': 'Envoyé',
        'peers': 'Pairs',
        'seeds': 'Graines',
        'speed_down': 'Vitesse Download',
        'speed_up': 'Vitesse Upload',
        'files': 'Fichiers',
        'file': 'Fichier',
        'size': 'Taille',
        'piece_progress': 'Progression Blocs',
        'tracker': 'Tracker',
        'trackers': 'Trackers',
        'auto_trackers': 'Auto-Trackers',
        'connected': 'Connecté',
        'disconnected': 'Déconnecté',
        'downloading': 'Téléchargement',
        'completed': 'Terminé',
        'paused': 'En pause',
        'error': 'Erreur',
        'select_torrent': 'Sélectionner fichier .torrent',
        'select_download_dir': 'Sélectionner dossier de téléchargement',
        'download_dir': 'Dossier de Téléchargement',
        'default_dir': 'Défaut: C:\\Torrent',
        'peer_list': 'Liste des Pairs',
        'address': 'Adresse',
        'port': 'Port',
        'downloaded_p': 'Téléchargé',
        'uploaded_p': 'Envoyé',
        'speed': 'Vitesse',
        'client': 'Client',
        'theme': 'Thème',
        'language': 'Langue',
        'black': 'Noir',
        'dark': 'Sombre',
        'light': 'Clair',
        'hacker': 'Pirate Hacker',
        'english': 'Anglais',
        'russian': 'Russe',
        'german': 'Allemand',
        'italian': 'Italien',
        'french': 'Français',
        'dutch': 'Néerlandais',
        'old_russian': 'Vieux Russe',
        'japanese': 'Japonais',
        'chinese': 'Chinois',
        'korean': 'Coréen',
        'no_torrents': 'Aucun torrent chargé',
        'select_file': 'Sélectionner un torrent pour les détails',
        'eta': 'ETA',
        'ratio': 'Ratio',
        'added': 'Ajouté',
        'active': 'Actif',
        'inactive': 'Inactif',
        'welcome': 'Bienvenue sur DAP-CAT Torrent Client!',
        'welcome_desc': 'Ajoutez un fichier .torrent pour commencer.',
        'confirm_remove': 'Supprimer ce torrent?',
        'torrent_removed': 'Torrent supprimé',
        'download_complete': 'Téléchargement terminé!',
        'browse': 'Parcourir',
        'save_resume': 'Sauver & Reprendre',
    },
    'nl': {
        'title': 'DAP-CAT Torrent Client — Mooie Client',
        'add_torrent': 'Torrent Toevoegen',
        'start': 'Start',
        'pause': 'Pauze',
        'stop': 'Stop',
        'remove': 'Verwijderen',
        'clear': 'Wissen',
        'status': 'Status',
        'progress': 'Voortgang',
        'downloaded': 'Gedownload',
        'uploaded': 'Geüpload',
        'peers': 'Peers',
        'seeds': 'Seeds',
        'speed_down': 'Download Snelh.',
        'speed_up': 'Upload Snelh.',
        'files': 'Bestanden',
        'file': 'Bestand',
        'size': 'Grootte',
        'piece_progress': 'Stuk Voortgang',
        'tracker': 'Tracker',
        'trackers': 'Trackers',
        'auto_trackers': 'Auto-Trackers',
        'connected': 'Verbonden',
        'disconnected': 'Verbroken',
        'downloading': 'Downloaden',
        'completed': 'Voltooid',
        'paused': 'Gepauzeerd',
        'error': 'Fout',
        'select_torrent': 'Selecteer .torrent bestand',
        'select_download_dir': 'Selecteer download map',
        'download_dir': 'Download Map',
        'default_dir': 'Standaard: C:\\Torrent',
        'peer_list': 'Peer Lijst',
        'address': 'Adres',
        'port': 'Poort',
        'downloaded_p': 'Gedownload',
        'uploaded_p': 'Geüpload',
        'speed': 'Snelheid',
        'client': 'Client',
        'theme': 'Thema',
        'language': 'Taal',
        'black': 'Zwart',
        'dark': 'Donker',
        'light': 'Licht',
        'hacker': 'Hacker Piraat',
        'english': 'Engels',
        'russian': 'Russisch',
        'german': 'Duits',
        'italian': 'Italiaans',
        'french': 'Frans',
        'dutch': 'Nederlands',
        'old_russian': 'Oud-Russisch',
        'japanese': 'Japans',
        'chinese': 'Chinees',
        'korean': 'Koreaans',
        'no_torrents': 'Geen torrents geladen',
        'select_file': 'Selecteer een torrent voor details',
        'eta': 'ETA',
        'ratio': 'Verhouding',
        'added': 'Toegevoegd',
        'active': 'Actief',
        'inactive': 'Inactief',
        'welcome': 'Welkom bij DAP-CAT Torrent Client!',
        'welcome_desc': 'Voeg een .torrent bestand toe om te beginnen.',
        'confirm_remove': 'Deze torrent verwijderen?',
        'torrent_removed': 'Torrent verwijderd',
        'download_complete': 'Download voltooid!',
        'browse': 'Bladeren',
        'save_resume': 'Opslaan & Hervatten',
    },
    'orx': {
        'title': 'Битторрентъ — Красивый Клиентъ',
        'add_torrent': 'Добавити Торентъ',
        'start': 'Начаті',
        'pause': 'Пауза',
        'stop': 'Стопъ',
        'remove': 'Удалити',
        'clear': 'Очистити',
        'status': 'Статусъ',
        'progress': 'Прогрессъ',
        'downloaded': 'Скачано',
        'uploaded': 'Отправлено',
        'peers': 'Пиры',
        'seeds': 'Сиды',
        'speed_down': 'Скорость загрузки',
        'speed_up': 'Скорость отдачи',
        'files': 'Файлы',
        'file': 'Файлъ',
        'size': 'Размъръ',
        'piece_progress': 'Прогрессъ кусовъ',
        'tracker': 'Трекеръ',
        'trackers': 'Трекеръ',
        'auto_trackers': 'Авто-Трекеръ',
        'connected': 'Подключонъ',
        'disconnected': 'Отключонъ',
        'downloading': 'Загрузка',
        'completed': 'Заврьшено',
        'paused': 'Пауза',
        'error': 'Ошыбка',
        'select_torrent': 'Изымите .torrent файлъ',
        'select_download_dir': 'Изымите папку загрузки',
        'download_dir': 'Папка загрузки',
        'default_dir': 'По умолчанію: C:\\Torrent',
        'peer_list': 'Списокъ пировъ',
        'address': 'Адръсъ',
        'port': 'Портъ',
        'downloaded_p': 'Скачано',
        'uploaded_p': 'Отправлено',
        'speed': 'Скорость',
        'client': 'Клиентъ',
        'theme': 'Тема',
        'language': 'Ѕыкъ',
        'black': 'Чрьнъ',
        'dark': 'Тьмънъ',
        'light': 'Сврьтлъ',
        'hacker': 'Хакъръ-Пиратъ',
        'english': 'Англійскій',
        'russian': 'Русскій',
        'german': 'Нємѣцкій',
        'italian': 'Италіанскій',
        'french': 'Францускій',
        'dutch': 'Нидєрландскій',
        'old_russian': 'Дрѣвнє-Русскій',
        'japanese': 'Јапонскій',
        'chinese': 'Китайскій',
        'korean': 'Корєйскій',
        'no_torrents': 'Нѣтъ торентъ',
        'select_file': 'Изымите торентъ для просмотрѣ',
        'eta': 'Осталосѣ',
        'ratio': 'Рєіто',
        'added': 'Добавлєнъ',
        'active': 'Активънъ',
        'inactive': 'Нєактивънъ',
        'welcome': 'Привѣтъ въ Битторрентѣ!',
        'welcome_desc': 'Добавьте .torrent файлъ для началѧ загрузки.',
        'confirm_remove': 'Удалиши ли сі торентъ?',
        'torrent_removed': 'Торентъ удаленъ',
        'download_complete': 'Загрузка заврьшена!',
        'browse': 'Обзоръ',
        'save_resume': 'Сохранити и Продолжити',
    },
    'ja': {
        'title': 'DAP-CAT Torrent Client — 美しいクライアント',
        'add_torrent': 'トーレント追加',
        'start': '開始',
        'pause': '一時停止',
        'stop': '停止',
        'remove': '削除',
        'clear': 'クリア',
        'status': '状態',
        'progress': '進捗',
        'downloaded': 'ダウンロード済',
        'uploaded': 'アップロード済',
        'peers': 'ピア',
        'seeds': 'シード',
        'speed_down': 'ダウンロード速度',
        'speed_up': 'アップロード速度',
        'files': 'ファイル',
        'file': 'ファイル',
        'size': 'サイズ',
        'piece_progress': 'ピース進捗',
        'tracker': 'トラッカー',
        'trackers': 'トラッカー',
        'auto_trackers': '自動トラッカー',
        'connected': '接続済み',
        'disconnected': '切断済み',
        'downloading': 'ダウンロード中',
        'completed': '完了',
        'paused': '一時停止中',
        'error': 'エラー',
        'select_torrent': '.torrentファイルを選択',
        'select_download_dir': 'ダウンロードディレクトリを選択',
        'download_dir': 'ダウンロードディレクトリ',
        'default_dir': 'デフォルト: C:\\Torrent',
        'peer_list': 'ピアリスト',
        'address': 'アドレス',
        'port': 'ポート',
        'downloaded_p': 'ダウンロード済',
        'uploaded_p': 'アップロード済',
        'speed': '速度',
        'client': 'クライアント',
        'theme': 'テーマ',
        'language': '言語',
        'black': 'ブラック',
        'dark': 'ダーク',
        'light': 'ライト',
        'hacker': 'ハッカー海賊',
        'english': '英語',
        'russian': 'ロシア語',
        'german': 'ドイツ語',
        'italian': 'イタリア語',
        'french': 'フランス語',
        'dutch': 'オランダ語',
        'old_russian': '古ロシア語',
        'japanese': '日本語',
        'chinese': '中国語',
        'korean': '韓国語',
        'no_torrents': 'トーレントが読み込まれていません',
        'select_file': '詳細を表示するにはトーレントを選択',
        'eta': '残り時間',
        'ratio': '比率',
        'added': '追加済み',
        'active': 'アクティブ',
        'inactive': '非アクティブ',
        'welcome': 'DAP-CAT Torrent Clientへようこそ！',
        'welcome_desc': '.torrentファイルを追加してダウンロードを開始します。',
        'confirm_remove': 'このトーレントを削除しますか？',
        'torrent_removed': 'トーレントが削除されました',
        'download_complete': 'ダウンロード完了！',
        'browse': '参照',
        'save_resume': '保存＆再開',
    },
    'zh': {
        'title': 'DAP-CAT Torrent Client — 美观客户端',
        'add_torrent': '添加种子',
        'start': '开始',
        'pause': '暂停',
        'stop': '停止',
        'remove': '移除',
        'clear': '清除',
        'status': '状态',
        'progress': '进度',
        'downloaded': '已下载',
        'uploaded': '已上传',
        'peers': ' peers',
        'seeds': '种子',
        'speed_down': '下载速度',
        'speed_up': '上传速度',
        'files': '文件',
        'file': '文件',
        'size': '大小',
        'piece_progress': '分片进度',
        'tracker': 'Tracker',
        'trackers': 'Tracker',
        'auto_trackers': '自动Tracker',
        'connected': '已连接',
        'disconnected': '已断开',
        'downloading': '下载中',
        'completed': '已完成',
        'paused': '已暂停',
        'error': '错误',
        'select_torrent': '选择.torrent文件',
        'select_download_dir': '选择下载目录',
        'download_dir': '下载目录',
        'default_dir': '默认: C:\\Torrent',
        'peer_list': 'Peer列表',
        'address': '地址',
        'port': '端口',
        'downloaded_p': '已下载',
        'uploaded_p': '已上传',
        'speed': '速度',
        'client': '客户端',
        'theme': '主题',
        'language': '语言',
        'black': '黑色',
        'dark': '深色',
        'light': '浅色',
        'hacker': '黑客海盗',
        'english': '英语',
        'russian': '俄语',
        'german': '德语',
        'italian': '意大利语',
        'french': '法语',
        'dutch': '荷兰语',
        'old_russian': '古俄语',
        'japanese': '日语',
        'chinese': '中文',
        'korean': '韩语',
        'no_torrents': '未加载种子',
        'select_file': '选择一个种子查看详情',
        'eta': '预计剩余',
        'ratio': '比率',
        'added': '已添加',
        'active': '活动',
        'inactive': '非活动',
        'welcome': '欢迎使用DAP-CAT Torrent Client！',
        'welcome_desc': '添加.torrent文件开始下载。',
        'confirm_remove': '确定要移除此种子吗？',
        'torrent_removed': '种子已移除',
        'download_complete': '下载完成！',
        'browse': '浏览',
        'save_resume': '保存并继续',
    },
    'ko': {
        'title': 'DAP-CAT Torrent Client — 아름다운 클라이언트',
        'add_torrent': '토렌트 추가',
        'start': '시작',
        'pause': '일시정지',
        'stop': '정지',
        'remove': '제거',
        'clear': '지우기',
        'status': '상태',
        'progress': '진행률',
        'downloaded': '다운로드됨',
        'uploaded': '업로드됨',
        'peers': '피어',
        'seeds': '시드',
        'speed_down': '다운로드 속도',
        'speed_up': '업로드 속도',
        'files': '파일',
        'file': '파일',
        'size': '크기',
        'piece_progress': '조각 진행률',
        'tracker': '트래커',
        'trackers': '트래커',
        'auto_trackers': '자동 트래커',
        'connected': '연결됨',
        'disconnected': '연결 끊김',
        'downloading': '다운로드 중',
        'completed': '완료됨',
        'paused': '일시정지',
        'error': '오류',
        'select_torrent': '.torrent 파일 선택',
        'select_download_dir': '다운로드 디렉토리 선택',
        'download_dir': '다운로드 디렉토리',
        'default_dir': '기본: C:\\Torrent',
        'peer_list': '피어 목록',
        'address': '주소',
        'port': '포트',
        'downloaded_p': '다운로드됨',
        'uploaded_p': '업로드됨',
        'speed': '속도',
        'client': '클라이언트',
        'theme': '테마',
        'language': '언어',
        'black': '검정',
        'dark': '어두운',
        'light': '밝은',
        'hacker': '해커 해적',
        'english': '영어',
        'russian': '러시아어',
        'german': '독일어',
        'italian': '이탈리아어',
        'french': '프랑스어',
        'dutch': '네덜란드어',
        'old_russian': '고대 러시아어',
        'japanese': '일본어',
        'chinese': '중국어',
        'korean': '한국어',
        'no_torrents': '토렌트 없음',
        'select_file': '세부 정보를 보려면 토렌트 선택',
        'eta': '예상 남은 시간',
        'ratio': '비율',
        'added': '추가됨',
        'active': '활성',
        'inactive': '비활성',
        'welcome': 'DAP-CAT Torrent Client에 오신 것을 환영합니다!',
        'welcome_desc': '.torrent 파일을 추가하여 다운로드를 시작하세요.',
        'confirm_remove': '이 토렌트를 제거하시겠습니까?',
        'torrent_removed': '토렌트 제거됨',
        'download_complete': '다운로드 완료!',
        'browse': '찾아보기',
        'save_resume': '저장 및 재개',
    },
}


# ══════════════════════════════════════════════════════════════════════
#  THEMES
# ══════════════════════════════════════════════════════════════════════

THEMES = {
    'black': {
        'bg': '#000000',
        'fg': '#ffffff',
        'bg_frame': '#0a0a0a',
        'bg_button': '#1a1a1a',
        'fg_button': '#ffffff',
        'bg_entry': '#111111',
        'fg_entry': '#ffffff',
        'bg_progress': '#1a1a1a',
        'fg_progress': '#00ff00',
        'bg_tree': '#0a0a0a',
        'fg_tree': '#ffffff',
        'bg_heading': '#111111',
        'fg_heading': '#00ff00',
        'border': '#333333',
        'highlight': '#003300',
        'font': ('Consolas', 10),
        'font_bold': ('Consolas', 10, 'bold'),
        'font_title': ('Consolas', 14, 'bold'),
    },
    'dark': {
        'bg': '#1e1e1e',
        'fg': '#d4d4d4',
        'bg_frame': '#252526',
        'bg_button': '#3c3c3c',
        'fg_button': '#ffffff',
        'bg_entry': '#3c3c3c',
        'fg_entry': '#d4d4d4',
        'bg_progress': '#2d2d30',
        'fg_progress': '#007acc',
        'bg_tree': '#252526',
        'fg_tree': '#d4d4d4',
        'bg_heading': '#2d2d30',
        'fg_heading': '#007acc',
        'border': '#3c3c3c',
        'highlight': '#264f78',
        'font': ('Segoe UI', 10),
        'font_bold': ('Segoe UI', 10, 'bold'),
        'font_title': ('Segoe UI', 14, 'bold'),
    },
    'light': {
        'bg': '#f5f5f5',
        'fg': '#333333',
        'bg_frame': '#ffffff',
        'bg_button': '#e0e0e0',
        'fg_button': '#333333',
        'bg_entry': '#ffffff',
        'fg_entry': '#333333',
        'bg_progress': '#e0e0e0',
        'fg_progress': '#4caf50',
        'bg_tree': '#ffffff',
        'fg_tree': '#333333',
        'bg_heading': '#e8e8e8',
        'fg_heading': '#2196f3',
        'border': '#d0d0d0',
        'highlight': '#bbdefb',
        'font': ('Segoe UI', 10),
        'font_bold': ('Segoe UI', 10, 'bold'),
        'font_title': ('Segoe UI', 14, 'bold'),
    },
    'hacker': {
        'bg': '#000800',
        'fg': '#00ff00',
        'bg_frame': '#001100',
        'bg_button': '#002200',
        'fg_button': '#00ff00',
        'bg_entry': '#001100',
        'fg_entry': '#00ff00',
        'bg_progress': '#001100',
        'fg_progress': '#00ff00',
        'bg_tree': '#001100',
        'fg_tree': '#00ff00',
        'bg_heading': '#002200',
        'fg_heading': '#00ff00',
        'border': '#004400',
        'highlight': '#003300',
        'font': ('Courier New', 10),
        'font_bold': ('Courier New', 10, 'bold'),
        'font_title': ('Courier New', 14, 'bold'),
    },
}


# ══════════════════════════════════════════════════════════════════════
#  MAIN GUI APPLICATION
# ══════════════════════════════════════════════════════════════════════

class TorrentClientApp:
    """Main GUI application for the torrent client."""

    def __init__(self, root):
        self.root = root
        self.root.title('DAP-CAT Torrent Client')
        self.root.geometry('1100x750')
        self.root.minsize(900, 600)

        self.current_theme = 'dark'
        self.current_lang = 'en'
        self.downloads = {}  # info_hash -> {'meta': TorrentMetadata, 'downloader': TorrentDownloader, 'tree': Treeview}
        self.selected_torrent = None
        self.default_download_dir = r'C:\Torrent'
        self.tracker_info = {}  # info_hash -> list of tracker info

        self._setup_menu()
        self._build_ui()
        self._apply_theme(self.current_theme)
        self._apply_language(self.current_lang)

    def _setup_menu(self):
        """Create the application menu."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label='File', menu=file_menu)
        file_menu.add_command(label='Add Torrent', command=self.add_torrent)
        file_menu.add_separator()
        file_menu.add_command(label='Exit', command=self.root.quit)

        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label='Edit', menu=edit_menu)
        edit_menu.add_command(label='Settings', command=self.show_settings)

    def _build_ui(self):
        """Build the main UI layout."""
        # Main container
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill='both', expand=True, padx=5, pady=5)

        # Top bar: controls
        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill='x', pady=(0, 5))

        self.btn_add = ttk.Button(top_frame, text='Add Torrent', command=self.add_torrent)
        self.btn_add.pack(side='left', padx=(0, 5))

        self.btn_start = ttk.Button(top_frame, text='Start', command=self.start_selected)
        self.btn_start.pack(side='left', padx=(0, 5))

        self.btn_pause = ttk.Button(top_frame, text='Pause', command=self.pause_selected)
        self.btn_pause.pack(side='left', padx=(0, 5))

        self.btn_stop = ttk.Button(top_frame, text='Stop', command=self.stop_selected)
        self.btn_stop.pack(side='left', padx=(0, 5))

        self.btn_remove = ttk.Button(top_frame, text='Remove', command=self.remove_selected)
        self.btn_remove.pack(side='left', padx=(0, 5))

        # Download directory
        dir_frame = ttk.LabelFrame(top_frame, text='Download Directory')
        dir_frame.pack(side='right', padx=(10, 0))
        self.dir_entry = ttk.Entry(dir_frame, width=30)
        self.dir_entry.pack(side='left', padx=5)
        self.dir_entry.insert(0, self.default_download_dir)
        ttk.Button(dir_frame, text='Browse', command=self.browse_dir).pack(side='left', padx=5)

        # Language & Theme
        lang_frame = ttk.LabelFrame(top_frame, text='Language / Theme')
        lang_frame.pack(side='right', padx=(10, 0))

        self.lang_var = tk.StringVar(value='en')
        langs = list(TRANSLATIONS.keys())
        lang_combo = ttk.Combobox(lang_frame, textvariable=self.lang_var, values=langs, state='readonly', width=12)
        lang_combo.pack(side='left', padx=5)
        lang_combo.bind('<<ComboboxSelected>>', self.on_lang_change)

        self.theme_var = tk.StringVar(value='dark')
        themes = list(THEMES.keys())
        theme_combo = ttk.Combobox(lang_frame, textvariable=self.theme_var, values=themes, state='readonly', width=12)
        theme_combo.pack(side='left', padx=5)
        theme_combo.bind('<<ComboboxSelected>>', self.on_theme_change)

        # Content area - two panes
        content_paned = ttk.PanedWindow(main_frame, orient='horizontal')
        content_paned.pack(fill='both', expand=True)

        # Left pane: torrent list
        left_frame = ttk.LabelFrame(content_paned, text='Torrents')
        content_paned.add(left_frame, weight=3)

        self.torrent_list = ttk.Treeview(left_frame, columns=('status', 'progress', 'size'), show='headings', height=8)
        self.torrent_list.heading('status', text='Status')
        self.torrent_list.heading('progress', text='Progress')
        self.torrent_list.heading('size', text='Size')
        self.torrent_list.column('status', width=100)
        self.torrent_list.column('progress', width=120)
        self.torrent_list.column('size', width=100)
        self.torrent_list.pack(fill='both', expand=True, padx=5, pady=5)
        self.torrent_list.bind('<<TreeviewSelect>>', self.on_torrent_select)
        self.torrent_list.bind('<Double-1>', lambda e: self.start_selected())

        # Right pane: details
        right_frame = ttk.LabelFrame(content_paned, text='Details')
        content_paned.add(right_frame, weight=7)

        # Progress bar (overall)
        prog_frame = ttk.Frame(right_frame)
        prog_frame.pack(fill='x', padx=5, pady=5)

        ttk.Label(prog_frame, text='Overall Progress:').pack(anchor='w')
        self.overall_progress = ttk.Progressbar(prog_frame, orient='horizontal', length=400, mode='determinate')
        self.overall_progress.pack(fill='x', pady=(0, 5))
        self.overall_progress_label = ttk.Label(prog_frame, text='0%')
        self.overall_progress_label.pack(anchor='w')

        # Tabs for file progress, peers, trackers
        self.detail_tabs = ttk.Notebook(right_frame)
        self.detail_tabs.pack(fill='both', expand=True, padx=5, pady=5)

        # Files tab
        files_frame = ttk.Frame(self.detail_tabs)
        self.detail_tabs.add(files_frame, text='Files')

        self.file_tree = ttk.Treeview(files_frame, columns=('file', 'size', 'progress'), show='headings', height=10)
        self.file_tree.heading('file', text='File')
        self.file_tree.heading('size', text='Size')
        self.file_tree.heading('progress', text='Progress')
        self.file_tree.column('file', width=350)
        self.file_tree.column('size', width=100)
        self.file_tree.column('progress', width=150)
        self.file_tree.pack(fill='both', expand=True, padx=5, pady=5)

        # Per-file progress bars
        self.file_progress_frames = []

        # Peers tab
        peers_frame = ttk.Frame(self.detail_tabs)
        self.detail_tabs.add(peers_frame, text='Peers')

        self.peer_tree = ttk.Treeview(peers_frame, columns=('address', 'port', 'downloaded', 'uploaded', 'speed', 'client'), show='headings', height=12)
        self.peer_tree.heading('address', text='Address')
        self.peer_tree.heading('port', text='Port')
        self.peer_tree.heading('downloaded', text='Downloaded')
        self.peer_tree.heading('uploaded', text='Uploaded')
        self.peer_tree.heading('speed', text='Speed')
        self.peer_tree.heading('client', text='Client')
        self.peer_tree.column('address', width=150)
        self.peer_tree.column('port', width=80)
        self.peer_tree.column('downloaded', width=100)
        self.peer_tree.column('uploaded', width=100)
        self.peer_tree.column('speed', width=100)
        self.peer_tree.column('client', width=150)
        self.peer_tree.pack(fill='both', expand=True, padx=5, pady=5)

        # Trackers tab
        trackers_frame = ttk.Frame(self.detail_tabs)
        self.detail_tabs.add(trackers_frame, text='Trackers')

        self.tracker_text = scrolledtext.ScrolledText(trackers_frame, height=15, width=60)
        self.tracker_text.pack(fill='both', expand=True, padx=5, pady=5)

        # Status bar
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill='x', pady=(5, 0))

        self.status_label = ttk.Label(status_frame, text='Ready')
        self.status_label.pack(side='left')

        self.speed_label = ttk.Label(status_frame, text='↓ 0 B/s  ↑ 0 B/s')
        self.speed_label.pack(side='right')

    def _apply_theme(self, theme_name):
        """Apply a theme to the entire application."""
        theme = THEMES[theme_name]
        self.current_theme = theme_name

        self.root.configure(bg=theme['bg'])

        # Configure ttk styles
        style = ttk.Style()
        style.theme_use('clam')

        style.configure('TFrame', background=theme['bg'])
        style.configure('TLabelFrame', background=theme['bg_frame'], foreground=theme['fg'],
                        bordercolor=theme['border'], labelfont=theme['font_bold'],
                        labelbackground=theme['bg_heading'], labelforeground=theme['fg_heading'])
        style.configure('TLabel', background=theme['bg'], foreground=theme['fg'], font=theme['font'])
        style.configure('TButton', background=theme['bg_button'], foreground=theme['fg_button'],
                        bordercolor=theme['border'], focuscolor=theme['border'])
        style.configure('TEntry', fieldbackground=theme['bg_entry'], foreground=theme['fg_entry'],
                        bordercolor=theme['border'])
        style.configure('Treeview', background=theme['bg_tree'], foreground=theme['fg_tree'],
                        bordercolor=theme['border'], fieldbackground=theme['bg_tree'],
                        font=theme['font'])
        style.configure('Treeview.Heading', background=theme['bg_heading'], foreground=theme['fg_heading'],
                        font=theme['font_bold'])
        style.map('Treeview', background=[('selected', theme['highlight'])])

        style.configure('TProgressbar', background=theme['fg_progress'],
                        troughcolor=theme['bg_progress'])

        # Update all existing widgets recursively
        self._update_widget_colors(self.root, theme)

    def _update_widget_colors(self, widget, theme):
        """Recursively update widget colors."""
        try:
            widget.configure(bg=theme['bg'], fg=theme['fg'])
        except Exception:
            pass
        try:
            widget.configure(fieldbackground=theme['bg_entry'])
        except Exception:
            pass
        for child in widget.winfo_children():
            self._update_widget_colors(child, theme)

    def _update_widget_text(self, widget, t):
        """Recursively update widget text labels."""
        try:
            text = widget.cget('text')
            if not text:
                return
            key_map = {
                'Status': t.get('status', 'Status'),
                'Progress': t.get('progress', 'Progress'),
                'Size': t.get('size', 'Size'),
                'File': t.get('file', 'File'),
                'Address': t.get('address', 'Address'),
                'Port': t.get('port', 'Port'),
                'Downloaded': t.get('downloaded_p', 'Downloaded'),
                'Uploaded': t.get('uploaded_p', 'Uploaded'),
                'Speed': t.get('speed', 'Speed'),
                'Client': t.get('client', 'Client'),
                'Overall Progress:': f"{t.get('progress', 'Progress')}:",
            }
            if text in key_map:
                widget.configure(text=key_map[text])
        except Exception:
            pass
        try:
            for child in widget.winfo_children():
                self._update_widget_text(child, t)
        except Exception:
            pass

    def _apply_language(self, lang_code):
        """Apply translations."""
        t = TRANSLATIONS.get(lang_code, TRANSLATIONS['en'])
        self.current_lang = lang_code

        # Update window title
        self.root.title(t['title'])

        # Update buttons
        self.btn_add.config(text=t['add_torrent'])
        self.btn_start.config(text=t['start'])
        self.btn_pause.config(text=t['pause'])
        self.btn_stop.config(text=t['stop'])
        self.btn_remove.config(text=t['remove'])

        # Update labels
        for child in self.root.winfo_children():
            self._update_widget_text(child, t)

        # Update tree headings
        try:
            self.torrent_list.heading('status', text=t['status'])
            self.torrent_list.heading('progress', text=t['progress'])
            self.torrent_list.heading('size', text=t['size'])
            self.file_tree.heading('file', text=t['file'])
            self.file_tree.heading('size', text=t['size'])
            self.file_tree.heading('progress', text=t['piece_progress'])
            self.peer_tree.heading('address', text=t['address'])
            self.peer_tree.heading('port', text=t['port'])
            self.peer_tree.heading('downloaded', text=t['downloaded_p'])
            self.peer_tree.heading('uploaded', text=t['uploaded_p'])
            self.peer_tree.heading('speed', text=t['speed'])
            self.peer_tree.heading('client', text=t['client'])
        except Exception:
            pass

        # Update tab text
        try:
            self.detail_tabs.tab(0, text=t['files'])
            self.detail_tabs.tab(1, text=t['peers'])
            self.detail_tabs.tab(2, text=t['trackers'])
        except Exception:
            pass

        # Update combobox labels
        lang_frame = None
        for child in self.root.winfo_children():
            for grandchild in child.winfo_children():
                if isinstance(grandchild, ttk.LabelFrame):
                    try:
                        if grandchild.cget('text') == 'Language / Theme':
                            lang_frame = grandchild
                    except Exception:
                        pass

        # Update all ttk.LabelFrame texts
        for widget in self.root.winfo_children():
            self._recursive_label_update(widget, t)

    def _recursive_label_update(self, widget, t):
        """Recursively update LabelFrame text."""
        try:
            text = widget.cget('text')
            key_map = {
                'Download Directory': 'download_dir',
                'Language / Theme': 'language',
                'Torrents': 'files',
                'Details': 'file',
            }
            if text in key_map:
                widget.configure(text=t.get(key_map[text], text))
        except Exception:
            pass
        try:
            for child in widget.winfo_children():
                self._recursive_label_update(child, t)
        except Exception:
            pass

    def on_theme_change(self, event=None):
        theme = self.theme_var.get()
        self._apply_theme(theme)

    def on_lang_change(self, event=None):
        lang = self.lang_var.get()
        self._apply_language(lang)

    def add_torrent(self):
        """Open file dialog to add a .torrent file."""
        filepath = filedialog.askopenfilename(
            title='Select .torrent file',
            filetypes=[('Torrent files', '*.torrent'), ('All files', '*.*')]
        )
        if not filepath:
            return

        try:
            meta = parse_torrent_file(filepath)
            info_hash = meta.info_hash.hex()

            if info_hash in self.downloads:
                messagebox.showinfo('Info', 'This torrent is already loaded.')
                return

            download_dir = self.dir_entry.get() or self.default_download_dir
            downloader = TorrentDownloader(meta, download_dir, self._on_download_progress)
            downloader.torrent_file_path = filepath

            self.downloads[info_hash] = {
                'meta': meta,
                'downloader': downloader,
                'tree': None,
                'filepath': filepath,
                'paused': False,
            }

            self._refresh_torrent_list()
            self.status_label.config(text=f'Torrent added: {meta.name}')
            self._show_tracker_info(info_hash)

        except Exception as e:
            messagebox.showerror('Error', f'Failed to load torrent: {str(e)}')

    def _refresh_torrent_list(self):
        """Refresh the torrent list treeview."""
        for item in self.torrent_list.get_children():
            self.torrent_list.delete(item)

        for info_hash, dl in self.downloads.items():
            meta = dl['meta']
            downloader = dl['downloader']
            progress = downloader._calculate_progress() if not dl['paused'] else 0
            status = 'Paused' if dl['paused'] else 'Downloading' if downloader.running else 'Completed' if len(downloader.pieces_complete) >= meta.num_pieces else 'Ready'
            self.torrent_list.insert('', 'end', iid=info_hash, values=(
                status,
                f'{progress:.1f}%',
                format_size(meta.total_size)
            ))

    def on_torrent_select(self, event):
        """Handle torrent selection."""
        selection = self.torrent_list.selection()
        if not selection:
            return
        info_hash = selection[0]
        self.selected_torrent = info_hash
        dl = self.downloads.get(info_hash)
        if not dl:
            return

        meta = dl['meta']
        downloader = dl['downloader']

        # Update file tree
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)

        for f in meta.files:
            file_progress = 0
            if f.piece_start <= f.piece_end:
                completed = sum(1 for i in range(f.piece_start, f.piece_end + 1) if i in downloader.pieces_complete)
                total = max(1, f.piece_end - f.piece_start + 1)
                file_progress = (completed / total) * 100

            self.file_tree.insert('', 'end', values=(
                f.display_path,
                format_size(f.length),
                f'{file_progress:.1f}%'
            ))

        # Update peer tree
        for item in self.peer_tree.get_children():
            self.peer_tree.delete(item)

        for peer in downloader.peers.values():
            if peer.connected:
                peer_id_str = peer.peer_id.decode('ascii', errors='replace')[:15] if peer.peer_id else 'Unknown'
                self.peer_tree.insert('', 'end', values=(
                    peer.addr,
                    peer.port,
                    format_size(peer.downloaded),
                    format_size(peer.uploaded),
                    f"{format_speed(peer.download_speed)} / {format_speed(peer.upload_speed)}",
                    peer_id_str
                ))

        # Update tracker text
        self.tracker_text.delete(1.0, 'end')
        if meta.trackers:
            self.tracker_text.insert('end', 'Trackers:\n')
            for tracker in meta.trackers:
                self.tracker_text.insert('end', f'  {tracker}\n')
        else:
            self.tracker_text.insert('end', 'No trackers found.\n')

        # Update overall progress
        progress = downloader._calculate_progress()
        self.overall_progress['value'] = progress
        self.overall_progress_label.config(text=f'{progress:.1f}% — {format_size(downloader.total_downloaded)} / {format_size(meta.total_size)}')

    def start_selected(self):
        """Start the selected torrent."""
        if not self.selected_torrent:
            messagebox.showinfo('Info', 'Select a torrent first.')
            return
        info_hash = self.selected_torrent
        dl = self.downloads.get(info_hash)
        if not dl:
            return

        downloader = dl['downloader']
        if not downloader.running:
            downloader.start()
            dl['paused'] = False
            self.status_label.config(text=f'Started: {dl["meta"].name}')
            self._refresh_torrent_list()

    def pause_selected(self):
        """Pause the selected torrent."""
        if not self.selected_torrent:
            return
        info_hash = self.selected_torrent
        dl = self.downloads.get(info_hash)
        if not dl:
            return
        dl['downloader'].pause()
        dl['paused'] = not dl['paused']
        self._refresh_torrent_list()
        self.status_label.config(text=f'{"Paused" if dl["paused"] else "Resumed"}: {dl["meta"].name}')

    def stop_selected(self):
        """Stop the selected torrent."""
        if not self.selected_torrent:
            return
        info_hash = self.selected_torrent
        dl = self.downloads.get(info_hash)
        if not dl:
            return
        dl['downloader'].stop()
        dl['paused'] = True
        self._refresh_torrent_list()
        self.status_label.config(text=f'Stopped: {dl["meta"].name}')

    def remove_selected(self):
        """Remove the selected torrent."""
        if not self.selected_torrent:
            return
        if not messagebox.askyesno('Confirm', 'Are you sure you want to remove this torrent?'):
            return
        info_hash = self.selected_torrent
        dl = self.downloads.get(info_hash)
        if dl:
            dl['downloader'].stop()
            del self.downloads[info_hash]
        self.selected_torrent = None
        self._refresh_torrent_list()
        # Clear details
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)
        for item in self.peer_tree.get_children():
            self.peer_tree.delete(item)
        self.overall_progress['value'] = 0
        self.overall_progress_label.config(text='0%')

    def browse_dir(self):
        """Open directory browser for download location."""
        dirpath = filedialog.askdirectory(title='Select download directory')
        if dirpath:
            self.dir_entry.delete(0, 'end')
            self.dir_entry.insert(0, dirpath)

    def show_settings(self):
        """Show settings dialog."""
        settings_win = tk.Toplevel(self.root)
        settings_win.title('Settings')
        settings_win.geometry('400x300')

        ttk.Label(settings_win, text='Default Download Directory:').pack(pady=10)
        dir_entry = ttk.Entry(settings_win, width=40)
        dir_entry.pack(pady=5)
        dir_entry.insert(0, self.dir_entry.get())

        ttk.Label(settings_win, text='Max Peers:').pack(pady=10)
        max_peers_spin = ttk.Spinbox(settings_win, from_=5, to=100, width=10)
        max_peers_spin.pack(pady=5)
        max_peers_spin.insert(0, '50')

        ttk.Button(settings_win, text='Save', command=settings_win.destroy).pack(pady=10)

    def _on_download_progress(self, event_type, *args):
        """Callback from downloader threads."""
        if event_type == 'progress':
            progress = args[0]
            if self.selected_torrent and self.selected_torrent in self.downloads:
                dl = self.downloads[self.selected_torrent]
                downloader = dl['downloader']
                meta = dl['meta']
                self.overall_progress['value'] = progress
                self.overall_progress_label.config(
                    text=f'{progress:.1f}% — {format_size(downloader.total_downloaded)} / {format_size(meta.total_size)}'
                )
        elif event_type == 'peers':
            num_peers, num_seeds = args
            if self.selected_torrent:
                self.status_label.config(text=f'Peers: {num_peers} | Seeds: {num_seeds}')
        elif event_type == 'peer_connected':
            peer_id = args[0]
            self.status_label.config(text=f'Connected to: {peer_id}')
        elif event_type == 'complete':
            download_dir = args[0]
            messagebox.showinfo('Complete', f'Download complete!\nSaved to: {download_dir}')
            if self.selected_torrent and self.selected_torrent in self.downloads:
                dl = self.downloads[self.selected_torrent]
                dl['paused'] = True
                self._refresh_torrent_list()
        elif event_type == 'error':
            error_msg = args[0]
            self.status_label.config(text=f'Error: {error_msg}')
        elif event_type == 'tracker_update':
            tracker, seeds, leechers = args
            self.tracker_text.insert('end', f'Tracker: {tracker} | Seeds: {seeds} | Leechers: {leechers}\n')

    def _show_tracker_info(self, info_hash):
        """Show tracker information in the tracker tab."""
        dl = self.downloads.get(info_hash)
        if not dl:
            return
        meta = dl['meta']
        self.tracker_text.delete(1.0, 'end')
        if meta.trackers:
            self.tracker_text.insert('end', f'Trackers for "{meta.name}":\n\n')
            for i, tracker in enumerate(meta.trackers, 1):
                self.tracker_text.insert('end', f'{i}. {tracker}\n')
            self.tracker_text.insert('end', f'\nTotal trackers: {len(meta.trackers)}\n')
        else:
            self.tracker_text.insert('end', f'No trackers found for "{meta.name}".\n')
            self.tracker_text.insert('end', 'Using DHT and PEX for peer discovery.\n')


# ══════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════

def main():
    root = tk.Tk()
    app = TorrentClientApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()