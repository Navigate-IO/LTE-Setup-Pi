#!/usr/bin/env python3
"""
MQTT LTE Bridge for Drone Communication.

Runs alongside the Java drone server on each Raspberry Pi.
Connects to an MQTT broker on AWS EC2 over LTE (IPv6).

Receiving: Subscribes to MQTT topics for this drone. When a message arrives,
POSTs it to the local Java drone server's /messenger or /update endpoint.

Sending: Exposes a local HTTP API on port 8099. The Java server POSTs here
to relay data to other drones over LTE.

MQTT Topics:
  drone/<drone_id>/messenger    - messages for /messenger endpoint
  drone/<drone_id>/update       - messages for /update endpoint
  drone/<drone_id>/gps          - GPS data
  drone/<drone_id>/raw          - raw messages with custom endpoint
  drone/all/messenger           - broadcast to all drones
  drone/all/update              - broadcast updates to all drones
  drone/status/<drone_id>       - online/offline status (retained)

Local HTTP API (for Java server integration):
  POST http://localhost:8099/lte/send
    Body: {"target": "drone_2", "payload": "<data>", "endpoint": "/messenger"}

  POST http://localhost:8099/lte/broadcast
    Body: {"payload": "<data>", "endpoint": "/messenger"}

  GET http://localhost:8099/lte/status
    Returns: {"mqtt_connected": true, "drone_id": "drone_1"}

Usage:
  sudo python3 lte_bridge.py

Requirements:
  pip3 install paho-mqtt --break-system-packages
"""
import json
import threading
import time
import sys
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("ERROR: paho-mqtt not installed.")
    print("Run: pip3 install paho-mqtt --break-system-packages")
    sys.exit(1)

# ============================================================
# CONFIGURATION - CHANGE THESE PER DRONE
# ============================================================
EC2_IPV6 = "2600:1f16:1d94:43e0:c29f:b730:5165:4a92"  # Your EC2 IPv6 address
MQTT_PORT = 1883
DRONE_ID = "drone_1"           # "drone_1" on first Pi, "drone_2" on second
JAVA_SERVER = "http://localhost:80"
BRIDGE_PORT = 8099
# ============================================================

mqtt_client = None
mqtt_connected = threading.Event()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def post_to_java(endpoint, body, content_type="text/plain"):
    """POST received data to the local Java drone server."""
    url = f"{JAVA_SERVER}{endpoint}"
    try:
        data = body.encode() if isinstance(body, str) else body
        req = urllib.request.Request(url, data=data,
            headers={"Content-Type": content_type}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = resp.read().decode()
            log(f"-> Java {endpoint}: {resp.status} {result[:100]}")
            return resp.status, result
    except Exception as e:
        log(f"-> Java {endpoint} FAILED: {e}")
        return None, str(e)


# ---- MQTT Callbacks ----

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        log("MQTT connected!")
        mqtt_connected.set()
        topics = [
            (f"drone/{DRONE_ID}/messenger", 1),
            (f"drone/{DRONE_ID}/update", 1),
            (f"drone/{DRONE_ID}/gps", 1),
            (f"drone/{DRONE_ID}/raw", 1),
            (f"drone/all/messenger", 1),
            (f"drone/all/update", 1),
        ]
        for topic, qos in topics:
            client.subscribe(topic, qos)
            log(f"Subscribed: {topic}")
        client.publish(
            f"drone/status/{DRONE_ID}",
            json.dumps({"status": "online", "timestamp": time.time()}),
            qos=1, retain=True
        )
    else:
        log(f"MQTT connect failed: {reason_code}")
        mqtt_connected.clear()


def on_disconnect(client, userdata, flags, reason_code, properties=None):
    log(f"MQTT disconnected: {reason_code}")
    mqtt_connected.clear()


def on_message(client, userdata, msg):
    """Handle incoming MQTT messages - forward to local Java server."""
    topic = msg.topic
    payload = msg.payload.decode()
    log(f"MQTT recv [{topic}]: {payload[:100]}")

    if "/messenger" in topic:
        post_to_java("/messenger", payload, "text/plain")
    elif "/update" in topic:
        post_to_java("/update", payload, "application/json")
    elif "/gps" in topic:
        post_to_java("/messenger", payload, "text/plain")
    elif "/raw" in topic:
        try:
            raw = json.loads(payload)
            post_to_java(
                raw.get("endpoint", "/messenger"),
                raw.get("body", payload),
                raw.get("content_type", "text/plain")
            )
        except:
            post_to_java("/messenger", payload, "text/plain")
    else:
        post_to_java("/messenger", payload, "text/plain")


# ---- Local HTTP Server for Java ----

class BridgeHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()
        try:
            msg = json.loads(body)
        except:
            self._respond(400, {"error": "invalid JSON"})
            return

        if self.path == "/lte/send":
            target = msg.get("target")
            payload = msg.get("payload", "")
            endpoint = msg.get("endpoint", "/messenger")
            if not target:
                self._respond(400, {"error": "need target"})
                return
            topic = f"drone/{target}/{endpoint.strip('/')}"
            self._mqtt_publish(topic, payload)

        elif self.path == "/lte/broadcast":
            payload = msg.get("payload", "")
            endpoint = msg.get("endpoint", "/messenger")
            topic = f"drone/all/{endpoint.strip('/')}"
            self._mqtt_publish(topic, payload)

        elif self.path == "/lte/publish":
            topic = msg.get("topic")
            payload = msg.get("payload", "")
            if not topic:
                self._respond(400, {"error": "need topic"})
                return
            self._mqtt_publish(topic, payload)

        else:
            self._respond(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/lte/status":
            self._respond(200, {
                "mqtt_connected": mqtt_connected.is_set(),
                "drone_id": DRONE_ID,
                "broker": f"[{EC2_IPV6}]:{MQTT_PORT}",
            })
        else:
            self._respond(404, {"error": "not found"})

    def _mqtt_publish(self, topic, payload):
        if not mqtt_connected.is_set():
            self._respond(503, {"error": "mqtt not connected"})
            return
        try:
            if isinstance(payload, dict):
                payload = json.dumps(payload)
            mqtt_client.publish(topic, payload, qos=1)
            log(f"Published [{topic}]: {str(payload)[:100]}")
            self._respond(200, {"status": "published", "topic": topic})
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format, *args):
        pass


def main():
    global mqtt_client

    log(f"{'='*50}")
    log(f"  LTE MQTT Bridge - {DRONE_ID}")
    log(f"  Broker: [{EC2_IPV6}]:{MQTT_PORT}")
    log(f"  Java:   {JAVA_SERVER}")
    log(f"  Bridge: http://localhost:{BRIDGE_PORT}")
    log(f"{'='*50}")

    mqtt_client = mqtt.Client(
        client_id=DRONE_ID,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message
    mqtt_client.will_set(
        f"drone/status/{DRONE_ID}",
        json.dumps({"status": "offline", "timestamp": time.time()}),
        qos=1, retain=True
    )
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)

    while True:
        try:
            log("Connecting to MQTT broker...")
            mqtt_client.connect(EC2_IPV6, MQTT_PORT, keepalive=45)
            mqtt_client.loop_start()
            break
        except Exception as e:
            log(f"MQTT connect failed: {e}")
            log("Retrying in 5s...")
            time.sleep(5)

    server = HTTPServer(("0.0.0.0", BRIDGE_PORT), BridgeHandler)
    log(f"Bridge HTTP on port {BRIDGE_PORT}")
    log(f"")
    log(f"Java integration:")
    log(f"  POST http://localhost:{BRIDGE_PORT}/lte/send")
    log(f"    {{\"target\":\"drone_2\",\"payload\":\"<data>\",\"endpoint\":\"/messenger\"}}")
    log(f"  POST http://localhost:{BRIDGE_PORT}/lte/broadcast")
    log(f"    {{\"payload\":\"<data>\",\"endpoint\":\"/messenger\"}}")
    log(f"  GET  http://localhost:{BRIDGE_PORT}/lte/status")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down...")
        mqtt_client.publish(
            f"drone/status/{DRONE_ID}",
            json.dumps({"status": "offline", "timestamp": time.time()}),
            qos=1, retain=True
        )
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


if __name__ == "__main__":
    main()
