#!/usr/bin/env python3
import socket
import threading
import struct
import json
import uuid
import time
import hmac
import hashlib
import psutil
from influxdb import InfluxDBClient

# ===================== CONFIG =====================
HOST = '0.0.0.0'
PORT = 50023
SHARED_SECRET = b"xx"
ALLOWED_COMMAND_KEYS = {"uptime", "hostname", "disk", "lslogs", "metrics", "network"}

# InfluxDB config
INFLUX_HOST = "localhost"
INFLUX_PORT = 8086
INFLUX_DB = "pimetrics"

# ===================== SETUP INFLUX =====================
influx_client = InfluxDBClient(host=INFLUX_HOST, port=INFLUX_PORT)
influx_client.create_database(INFLUX_DB)
influx_client.switch_database(INFLUX_DB)

# ===================== PNCP MESSAGE LOGGER =====================
def log_pncp_message(direction, addr, msg):
    msg_type = msg.get("type", "unknown").upper()
    print(f"\n==============================\n📡 PNCP {direction} MESSAGE ({msg_type}) from {addr}\n==============================")
    print(json.dumps(msg, indent=2))
    print("==============================\n")
    try:
        with open("pncp_log.txt", "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {direction} from {addr}\n")
            f.write(json.dumps(msg, indent=2))
            f.write("\n\n")
    except Exception:
        pass

# ===================== SOCKET HELPERS =====================
def send_msg(conn, obj, addr=None):
    data = json.dumps(obj).encode('utf-8')
    if addr:
        log_pncp_message("SEND", addr, obj)
    conn.sendall(struct.pack('>I', len(data)) + data)

def recv_msg(conn, addr=None):
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
    msg = json.loads(payload.decode('utf-8'))
    if addr:
        log_pncp_message("RECV", addr, msg)
    return msg

# ===================== GLOBAL AGENTS =====================
connected_agents = {}
agents_lock = threading.Lock()

# ===================== METRICS PRINT =====================
def print_metrics(agent_id, metrics_json):
    try:
        metrics = json.loads(metrics_json)
        print(f"\n[{agent_id}] Metrics:")
        print("CPU usage per core:", metrics['cpu_percent'])
        mem = metrics['memory']
        print(f"Memory: {mem['used'] / (1024**2):.1f} MB used / {mem['total'] / (1024**2):.1f} MB total ({mem['percent']}%)")
        swap = mem
        print(f"Swap: {swap['swap_used'] / (1024**2):.1f} MB used / {swap['swap_total'] / (1024**2):.1f} MB total ({swap['swap_percent']}%)")
        load = metrics['load_avg']
        print(f"Load Average: 1min={load['1min']}, 5min={load['5min']}, 15min={load['15min']}")
        disk = metrics['disk']
        print(f"Disk: {disk['used'] / (1024**3):.2f} GB used / {disk['total'] / (1024**3):.2f} GB total ({disk['percent']}%)")
        net = metrics.get('net', {})
        if net:
            print("Network Interfaces:")
            for iface, stats in net.items():
                print(f"  {iface}: {stats['bytes_sent']/1024:.2f} KB sent, {stats['bytes_recv']/1024:.2f} KB received")
        print("-" * 40)
    except Exception as e:
        print(f"[-] Failed to parse metrics from {agent_id}: {e}")
        print(metrics_json)

# ===================== INFLUX WRITE =====================
def store_in_influx(agent_id, command, output):
    measurement = command.lower()
    json_body = [{
        "measurement": measurement,
        "tags": {"agent": agent_id},
        "time": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "fields": {"output": str(output)}
    }]
    try:
        influx_client.write_points(json_body)
    except Exception as e:
        print(f"[-] Failed to write {agent_id}/{command} to InfluxDB: {e}")

# ===================== AGENT HANDLER =====================
class AgentHandler(threading.Thread):
    def __init__(self, conn, addr):
        super().__init__(daemon=True)
        self.conn = conn
        self.addr = addr
        self.agent_id = None

    def run(self):
        try:
            print(f"[+] Connection from {self.addr}")
            msg = recv_msg(self.conn, self.addr)
            if not msg or msg.get('type') != 'auth':
                print('[-] Expected auth, closing')
                self.conn.close()
                return

            if not self.validate_auth(msg):
                print('[-] Auth failed')
                send_msg(self.conn, {'type': 'auth_result', 'ok': False}, self.addr)
                self.conn.close()
                return

            base_name = msg.get('agent', f'{self.addr[0]}')
            self.agent_id = f"{base_name}_{self.addr[0]}:{self.addr[1]}"

            with agents_lock:
                connected_agents[self.agent_id] = self.conn

            send_msg(self.conn, {'type': 'auth_result', 'ok': True}, self.addr)
            print(f"[+] Agent authenticated: {self.agent_id}")

            # Log connection to InfluxDB
            self.log_agent_status("connected")

            # Start monitoring
            self.monitor_connection()

        except Exception as e:
            print(f"[-] Exception in handler for {self.addr}: {e}")
        finally:
            self.cleanup()

    def monitor_connection(self):
        """Periodically checks if the agent is still connected."""
        try:
            while True:
                self.conn.settimeout(3.0)
                try:
                    test = self.conn.recv(1, socket.MSG_PEEK)
                    if not test:
                        print(f"[!] Agent disconnected (no data): {self.agent_id}")
                        break
                except socket.timeout:
                    continue  # still alive
                except (ConnectionResetError, ConnectionAbortedError):
                    print(f"[!] Agent forcibly disconnected: {self.agent_id}")
                    break
                except Exception as e:
                    print(f"[-] Error checking connection for {self.agent_id}: {e}")
                    break
                time.sleep(3)
        except Exception as e:
            print(f"[-] Monitor error for {self.agent_id}: {e}")

    def cleanup(self):
        """Removes agent and logs disconnection."""
        with agents_lock:
            if self.agent_id in connected_agents:
                del connected_agents[self.agent_id]
        try:
            self.conn.close()
        except:
            pass
        if self.agent_id:
            print(f"[*] Connection closed: {self.agent_id}")
            self.log_agent_status("disconnected")
        else:
            print(f"[*] Connection closed: {self.addr}")

    def validate_auth(self, msg):
        try:
            ts = int(msg.get('ts', 0))
        except:
            return False
        if abs(int(time.time()) - ts) > 60:
            print("[-] Timestamp out of range")
            return False
        client_hmac = msg.get('hmac', '')
        expected = hmac.new(SHARED_SECRET, str(ts).encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(client_hmac, expected)

    def log_agent_status(self, status):
        """Logs connection/disconnection to InfluxDB."""
        json_body = [{
            "measurement": "agent_status",
            "tags": {"agent": self.agent_id},
            "time": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            "fields": {"status": status}
        }]
        try:
            influx_client.write_points(json_body)
            print(f"[InfluxDB] Logged {self.agent_id} as {status}")
        except Exception as e:
            print(f"[-] Failed to log agent status for {self.agent_id}: {e}")

# ===================== COMMAND LOOP =====================
def command_loop():
    while True:
        try:
            cmd = input(f"cmd to all Pis (keys: {sorted(ALLOWED_COMMAND_KEYS)}, blank to close): ").strip()
        except EOFError:
            print("[!] EOF detected. Exiting controller...")
            break

        if not cmd:
            print("[*] Closing all connections")
            with agents_lock:
                for conn in connected_agents.values():
                    try: conn.close()
                    except: pass
                connected_agents.clear()
            break

        if cmd not in ALLOWED_COMMAND_KEYS:
            print("[-] Command key not allowed")
            continue

        req_id = str(uuid.uuid4())
        with agents_lock:
            agents = list(connected_agents.items())
        if not agents:
            print("[*] Waiting for agents to connect...")
            time.sleep(1)
            continue

        for agent_id, conn in agents:
            try:
                send_msg(conn, {'type': 'cmd', 'id': req_id, 'cmd': cmd}, agent_id)
                resp = recv_msg(conn, agent_id)
                if resp and resp.get('type') == 'result' and resp.get('id') == req_id:
                    if cmd == 'metrics':
                        print_metrics(agent_id, resp.get('output'))
                    else:
                        print(f"[{agent_id}] rc={resp.get('rc')}\nOutput:\n{resp.get('output')}")
                    store_in_influx(agent_id, cmd, resp.get('output'))
                else:
                    print(f"[-] Unexpected response from {agent_id}: {resp}")
            except Exception as e:
                print(f"[-] Error communicating with {agent_id}: {e}")

# ===================== ACCEPT LOOP =====================
def accept_loop(sock):
    print("[DEBUG] Accept loop started, waiting for agent connections...")
    try:
        while True:
            conn, addr = sock.accept()
            AgentHandler(conn, addr).start()
    except Exception as e:
        print("Accept loop stopped:", e)

# ===================== MAIN SERVER =====================
def start_server():
    print("[DEBUG] Starting PNCP Controller...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(50)
    print(f"[+] Controller listening on {HOST}:{PORT}")
    print("[DEBUG] Ready to accept connections.")

    threading.Thread(target=accept_loop, args=(sock,), daemon=True).start()
    command_loop()

# ===================== ENTRY POINT =====================
if __name__ == '__main__':
    print("[DEBUG] Running controller_server.py main entry")
    start_server()