# PNCP (Pi Network Control Protocol)

A lightweight command & metrics system for monitoring and controlling multiple Raspberry Pis (or Linux devices).
Data is stored in **InfluxDB 1.8** and can be visualized in **Grafana** using **InfluxQL** queries.

This repository contains two programs:

* **agent.py** — Runs on each Pi. Sends metrics and executes whitelisted commands.
* **controller_server.py** — Central controller. Authenticates agents, sends commands, stores results in InfluxDB.

---

# 🔥 Features

### ✔ Agent (client)

* Secure HMAC authentication
* CPU, RAM, disk, load average, network metrics
* Supports only whitelisted commands (safe)
* Sends JSON results back to controller
* Very lightweight — ideal for many Pis

### ✔ Controller (server)

* Handles many agents concurrently
* Logs PNCP protocol messages (`pncp_log.txt`)
* Sends commands to all connected Pis
* Writes metric + command output into **InfluxDB 1.8**
* Allows Grafana dashboards for real-time monitoring

---

# 📂 Repository Structure

```
.
├── pi_client.py
├── controller_server.py
├── README.md
└── pncp_log.txt   # auto-created
```

---

# 🔐 Security (IMPORTANT)

Before publishing to GitHub, replace these values:

```
CONTROLLER_HOST = "<controller-ip>"
SHARED_SECRET   = b"<your-secret>"
INFLUX_HOST     = "<influx-host>"
INFLUX_PORT     = 8086
INFLUX_DB       = "<database>"
```

Never commit real HMAC secrets or local network IPs!

Recommended: create a `.env` or `config.json` and add them to `.gitignore`.

---

# ▶ Running the Controller

```bash
python3 controller_server.py
```

This starts:

* A TCP server on your chosen port
* InfluxDB connection
* Logging system
* Agent handler threads

---

# ▶ Running an Agent

```bash
python3 pi_client.py
```

The agent will:

1. Connect to the controller
2. Authenticate via HMAC (timestamp-based)
3. Wait for commands
4. Periodically return metrics

---

# 📡 Supported Commands

| Command    | Description                             |
| ---------- | --------------------------------------- |
| `uptime`   | Uptime + load average + logged-in count |
| `hostname` | Host name                               |
| `disk`     | Disk usage summary                      |
| `metrics`  | Full system metrics JSON                |
| `lslogs`   | Executes `ls /var/log`                  |
| `network`  | Network interface stats (if added)      |

---

# 🧪 InfluxDB 1.8 Integration (InfluxQL)

The controller writes the following measurements:

| Measurement      | Contains                      |
| ---------------- | ----------------------------- |
| `agent_status`   | connected/disconnected events |
| `metrics`        | full JSON metrics output      |
| `<command-name>` | output of specific commands   |

Example write body:

```json
[
  {
    "measurement": "metrics",
    "tags": {
      "agent": "pi-1"
    },
    "time": "2025-01-01T10:00:00Z",
    "fields": {
      "output": "{...metrics json...}"
    }
  }
]
```

Since this is InfluxDB **1.8**, data is stored using **InfluxQL**, not Flux.

---

# 📈 Grafana Setup (Influx 1.8 + InfluxQL)

### 1. Add a Data Source

* Type: **InfluxDB**
* URL: `http://<influx-host>:8086`
* Database: `<your-db>`
* Access: "Server" or "Browser"
* No Flux — only InfluxQL

### 2. Example InfluxQL Queries

#### **Last 1 hour of metrics**

```
SELECT "output"
FROM "metrics"
WHERE $timeFilter
```

#### **Agent connection status panel**

```
SELECT "status"
FROM "agent_status"
WHERE "agent" = '$agent'
ORDER BY time DESC
LIMIT 50
```

#### **CPU usage (parsed manually or via JSON plugin)**

(You can parse JSON inside Grafana using **Grafana JSON data transformation**.)

---

# 📊 Recommended Grafana Panels

### ✔ CPU panel

Parse JSON → extract each core usage.

### ✔ Memory usage

Extract: `memory.used`, `memory.total`, `memory.percent`.

### ✔ Disk usage

`disk.used` / `disk.total`.

### ✔ Network usage

bytes sent/received per interface.

### ✔ Agent status

Simple graph showing online/offline.

---

# 📝 PNCP Logging

The controller keeps a full log of all protocol messages:

```
pncp_log.txt
```

Useful for debug, replay, and auditing.

---

# ⚠ GitHub Publishing Checklist

Before pushing to GitHub:

### **Remove secrets**

* Replace SHARED_SECRET
* Replace controller/agent IPs
* Replace InfluxDB host

### **Use .gitignore**

Add:

```
pncp_log.txt
.env
__pycache__/
*.pyc
config.json
```

### **Optional: environment variables**

```bash
export PNCP_SECRET="mysecret"
export PNCP_INFLUX_HOST="localhost"
```

Then load them in Python.

---

