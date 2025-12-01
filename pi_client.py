#!/usr/bin/env python3
import socket
import struct
import json
import time
import hmac
import hashlib
import psutil
import uuid

CONTROLLER_HOST = '192.168.1.136'  # Your controller IP
CONTROLLER_PORT = 50023
SHARED_SECRET = b"xx"

COMMAND_MAP = {
    'uptime': None,     # will generate programmatically
    'hostname': None,   # programmatically
    'disk': None,       # programmatically
    'lslogs': ['/usr/bin/ls', '/var/log'],
    'metrics': None     # new command for CPU/memory/load/disk
}

def send_msg(conn, obj):
    data = json.dumps(obj).encode('utf-8')
    conn.sendall(struct.pack('>I', len(data)) + data)

def recv_msg(conn):
    hdr = conn.recv(4)
    if not hdr:
        return None
    (length,) = struct.unpack('>I', hdr)
    payload = b''
    while len(payload) < length:
        chunk = conn.recv(length - len(payload))
        if not chunk:
            raise ConnectionError('Socket closed mid-message')
        payload += chunk
    return json.loads(payload.decode('utf-8'))

def collect_metrics():
    """Collect CPU, memory, load average, disk usage."""
    cpu_percents = psutil.cpu_percent(percpu=True)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    load1, load5, load15 = psutil.getloadavg()
    disk = psutil.disk_usage('/')

    return {
        "cpu_percent": cpu_percents,
        "memory": {
            "total": mem.total,
            "used": mem.used,
            "free": mem.available,
            "percent": mem.percent,
            "swap_total": swap.total,
            "swap_used": swap.used,
            "swap_free": swap.free,
            "swap_percent": swap.percent
        },
        "load_avg": {"1min": load1, "5min": load5, "15min": load15},
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent
        }
    }

def run_mapped_command(key):
    if key not in COMMAND_MAP:
        return 1, "Command key not allowed"
    try:
        if key == 'uptime':
            load1, load5, load15 = psutil.getloadavg()
            users = len(psutil.users())
            uptime_sec = time.time() - psutil.boot_time()
            output = f"uptime_sec={uptime_sec}, users={users}, load_avg=({load1}, {load5}, {load15})"
            return 0, output

        elif key == 'hostname':
            import socket
            return 0, socket.gethostname()

        elif key == 'disk':
            disk = psutil.disk_usage('/')
            output = f"Total: {disk.total}, Used: {disk.used}, Free: {disk.free}, Percent: {disk.percent}"
            return 0, output

        elif key == 'metrics':
            metrics = collect_metrics()
            return 0, json.dumps(metrics)

        else:
            import subprocess
            proc = subprocess.run(COMMAND_MAP[key], capture_output=True, text=True, timeout=30)
            return proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        return -124, "Command timeout"
    except Exception as e:
        return -1, str(e)

def main():
    sock = socket.create_connection((CONTROLLER_HOST, CONTROLLER_PORT))

    ts = int(time.time())
    h = hmac.new(SHARED_SECRET, str(ts).encode(), hashlib.sha256).hexdigest()
    send_msg(sock, {'type':'auth', 'agent':'pi-1', 'ts': ts, 'hmac': h})
    auth_resp = recv_msg(sock)
    if not auth_resp or not auth_resp.get('ok'):
        print("Authentication failed")
        sock.close()
        return
    print("[*] Authenticated to controller")

    try:
        while True:
            msg = recv_msg(sock)
            if msg is None:
                print("Controller disconnected")
                break
            if msg.get('type') == 'cmd':
                key = msg.get('cmd')
                reqid = msg.get('id')
                rc, output = run_mapped_command(key)
                send_msg(sock, {'type':'result', 'id': reqid, 'rc': rc, 'output': output})
            else:
                print("Unknown message:", msg)
    finally:
        sock.close()

if __name__ == '__main__':
    main()
