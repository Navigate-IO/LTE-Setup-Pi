# NavigateIO LTE Bridge

Drone-to-drone communication relay over LTE using MQTT. Enables two Raspberry Pis with SIM767XX LTE modules to exchange data through an AWS EC2 MQTT broker, providing a cellular backhaul for the NavigateIO UAV mesh network.

## Architecture

```
Drone 1 (Pi)                    AWS EC2                     Drone 2 (Pi)
┌──────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│ Java Drone Server│     │                 │     │ Java Drone Server│
│   (port 80)      │     │   Mosquitto     │     │   (port 80)      │
│        │         │     │  MQTT Broker    │     │        ▲         │
│        ▼         │     │   (port 1883)   │     │        │         │
│  LTE Bridge      │     │                 │     │  LTE Bridge      │
│  (port 8099)     │     │                 │     │  (port 8099)     │
│        │         │     │        ▲        │     │        ▲         │
│   SIM767XX       │     │        │        │     │   SIM767XX       │
│   LTE Module     │─────┤   IPv6/MQTT     ├─────│   LTE Module     │
└──────────────────┘ LTE └─────────────────┘ LTE └──────────────────┘
```

## Components

| File | Where | Purpose |
|------|-------|---------|
| `lte_bridge.py` | Each Pi | MQTT client + local HTTP API for Java server |
| `pi_setup.sh` | Each Pi | Installs dependencies, creates systemd services |
| `ec2_setup.sh` | AWS EC2 | Installs and configures Mosquitto MQTT broker |

## Hardware Requirements

- Raspberry Pi (tested on Pi 4/CM4)
- SIM767XX LTE module (SIM7672G tested)
- Active T-Mobile SIM card with data plan
- LTE antenna connected to **MANT** port on the module
- USB cable connecting SIM767XX to Pi
- AWS EC2 instance (t2.micro/t3.micro, free tier eligible)

## Complete Setup From Scratch

### Step 1: EC2 Server (one time)

#### 1a. Create an AWS Account

Go to https://aws.amazon.com and sign up. The free tier includes a t2.micro instance running 24/7 for 12 months.

#### 1b. Launch an EC2 Instance

In the AWS Console, go to EC2 and click "Launch Instance":
- Name: `lte-relay-server`
- OS: Ubuntu Server 24.04 LTS (free tier eligible)
- Instance type: t2.micro (free tier eligible)
- Create a new key pair, download the `.pem` file
- Storage: 8 GB (default)
- Launch the instance

#### 1c. Enable IPv6 on EC2

T-Mobile LTE provides IPv6-only connectivity, so your EC2 instance needs an IPv6 address.

1. **VPC:** Go to VPC → Your VPCs → select your VPC → Actions → Edit CIDRs → Add IPv6 CIDR → Amazon-provided → Allocate
2. **Subnet:** Go to Subnets → select your instance's subnet → Actions → Edit IPv6 CIDRs → Add IPv6 CIDR → Save
3. **Route Table:** Go to Route Tables → select the route table for your subnet → Routes → Edit routes → Add route → Destination `::/0` → Target: your Internet Gateway → Save
4. **Instance:** Go to EC2 → Instances → select your instance → Actions → Networking → Manage IP addresses → Expand network interface → IPv6 addresses → Assign new IP address → Save

#### 1d. Configure Security Group

Go to your instance → Security tab → click the security group → Edit inbound rules. Add:

| Type | Port | Source | Purpose |
|------|------|--------|---------|
| SSH | 22 | Your IP | SSH access |
| Custom TCP | 1883 | `::/0` | MQTT from Pi LTE (IPv6) |
| Custom TCP | 1883 | `0.0.0.0/0` | MQTT from IPv4 clients (optional) |

#### 1e. Install Mosquitto

SSH into your EC2 instance:
```bash
chmod 400 lte-relay-key.pem
ssh -i lte-relay-key.pem ubuntu@YOUR_EC2_PUBLIC_IPV4
```

Run the setup script:
```bash
sudo ./ec2_setup.sh
```

Or manually:
```bash
sudo apt update && sudo apt install -y mosquitto mosquitto-clients
sudo tee /etc/mosquitto/conf.d/drone-relay.conf > /dev/null <<'EOF'
listener 1883 ::
allow_anonymous true
max_keepalive 60
EOF
sudo systemctl restart mosquitto
sudo systemctl enable mosquitto
```

Note your EC2 IPv6 address (shown in the EC2 console or run `curl -s http://v6.ident.me`). You'll need this for every Pi.

#### 1f. Test Mosquitto

On EC2:
```bash
mosquitto_sub -t 'test/#' -v &
mosquitto_pub -t 'test/hello' -m 'working'
# Should print: test/hello working
```

EC2 setup is done. You never need to touch it again.

---

### Step 2: Raspberry Pi Setup (repeat for each drone)

#### 2a. Flash and Boot

Flash Raspberry Pi OS to your SD card, boot the Pi, and connect a keyboard/monitor. Also connect Ethernet so you can SSH in later.

#### 2b. Connect Hardware

1. Insert active T-Mobile SIM card into the SIM767XX module
2. Connect LTE antenna to the **MANT** port on the module
3. Connect the SIM767XX to the Pi via USB

#### 2c. Verify Module Detection

```bash
lsusb
# Should show a Qualcomm or SIMCom device

ls /dev/ttyACM*
# Should show /dev/ttyACM0 (and possibly ACM1, ACM2, ACM3)
```

If you see `/dev/ttyACM*` devices, the module is detected. If not, check the USB cable.

#### 2d. Disable ModemManager

ModemManager grabs the serial port and interferes with AT commands. Disable it permanently:

```bash
sudo systemctl stop ModemManager
sudo systemctl disable ModemManager
```

#### 2e. First-Time Module Configuration

These settings are stored on the SIM767XX module itself (not the Pi's SD card), so they survive Pi reflashes. Only do this once per module.

```bash
sudo minicom -D /dev/ttyACM0 -b 115200
```

In minicom, type each command and wait for `OK`:

```
AT
```
Verifies communication. Should return `OK`.

```
AT+CPIN?
```
Should return `+CPIN: READY`. If `ERROR 10`, the SIM is not inserted properly.

```
AT+CSQ
```
Signal strength. Need 10-31. If 99, check the antenna connection.

```
AT+CGDCONT=1,"IPV4V6","fast.t-mobile.com"
```
Sets the APN to T-Mobile with dual-stack IPv4/IPv6.

```
AT$MYCONFIG="usbnetmode",1
```
Sets ECM mode so the module appears as a network interface. **Requires reboot to take effect.**

Exit minicom: press `Ctrl+A`, then `X`, then `Enter`.

Reboot:
```bash
sudo reboot
```

#### 2f. Bring Up LTE Connection

After reboot, disable ModemManager again:

```bash
sudo systemctl stop ModemManager
sudo systemctl disable ModemManager
```

Enable the cellular data connection:
```bash
sudo minicom -D /dev/ttyACM0 -b 115200
```

In minicom:
```
AT+DIALMODE=0
```

Wait for `OK`, exit minicom (`Ctrl+A`, `X`, `Enter`).

Wait about 5 seconds for the connection to establish, then find and configure the LTE network interface:

```bash
# Check what interfaces exist
ifconfig -a
# Look for eth1, usb0, or enxXXXXXX — that's your LTE interface
# It will be different from your regular Ethernet (eth0)

# Get an IP address on the LTE interface (replace eth1 if yours is different)
sudo dhclient eth1

# Set DNS to Google's IPv6 DNS
echo "nameserver 2001:4860:4860::8888" | sudo tee /etc/resolv.conf

# IMPORTANT: Remove the LTE default route so it doesn't break SSH/Ethernet
sudo ip route del default dev eth1 2>/dev/null
```

#### 2g. Verify LTE Connectivity

```bash
ping -c 3 -I eth1 2001:4860:4860::8888
```

You should see replies with 0% packet loss. If not:
- Check antenna is connected to MANT
- Check signal: `sudo minicom -D /dev/ttyACM0 -b 115200`, type `AT+CSQ`
- Make sure `AT+DIALMODE=0` was sent

Also test that you can reach your EC2 MQTT broker:
```bash
sudo apt install -y mosquitto-clients
mosquitto_pub -h YOUR_EC2_IPV6 -p 1883 -t "test/hello" -m "ping from pi"
```

Should return silently with no error.

#### 2h. Clone Repo and Install

```bash
git clone <your-repo-url>
cd <repo>/lte-bridge
```

Edit `lte_bridge.py` and set your configuration:
```python
EC2_IPV6 = "2600:1f16:xxxx:xxxx:xxxx:xxxx:xxxx:xxxx"  # Your EC2 IPv6
DRONE_ID = "drone_1"   # "drone_1" on first Pi, "drone_2" on second
```

Run the setup script to install dependencies and create systemd services:
```bash
sudo ./pi_setup.sh
```

#### 2i. Start the Bridge

```bash
sudo python3 lte_bridge.py
```

You should see:
```
[HH:MM:SS] LTE MQTT Bridge - drone_1
[HH:MM:SS] Connecting to MQTT broker...
[HH:MM:SS] MQTT connected!
[HH:MM:SS] Subscribed: drone/drone_1/messenger
[HH:MM:SS] Subscribed: drone/drone_1/update
...
```

#### 2j. Verify

In a separate terminal:
```bash
curl http://localhost:8099/lte/status
# Should show: {"mqtt_connected": true, "drone_id": "drone_1"}
```

---

### Step 3: Test Drone-to-Drone Communication

With both Pis running the bridge (drone_1 and drone_2), test from drone_1:

```bash
curl -X POST http://localhost:8099/lte/send \
  -H "Content-Type: application/json" \
  -d '{"target":"drone_2","payload":"{\"messageType\":\"drone-gps\",\"reading\":{\"latitude\":36.089}}","endpoint":"/messenger"}'
```

Drone 2's bridge terminal should show:
```
[HH:MM:SS] MQTT recv [drone/drone_2/messenger]: {"messageType":"drone-gps"...
[HH:MM:SS] -> Java /messenger: 200 OK
```

Test the other direction from drone_2:
```bash
curl -X POST http://localhost:8099/lte/send \
  -H "Content-Type: application/json" \
  -d '{"target":"drone_1","payload":"{\"messageType\":\"test\",\"data\":\"hello from drone 2\"}","endpoint":"/messenger"}'
```

---

## Pi Startup After Reboot

If `pi_setup.sh` was run, the LTE connection and bridge services start automatically on boot.

For manual startup (if services aren't working):

```bash
sudo systemctl stop ModemManager
sudo minicom -D /dev/ttyACM0 -b 115200
# Type: AT+DIALMODE=0
# Exit minicom (Ctrl+A, X, Enter)
sudo dhclient eth1
echo "nameserver 2001:4860:4860::8888" | sudo tee /etc/resolv.conf
sudo ip route del default dev eth1 2>/dev/null
sudo python3 lte_bridge.py
```

---

## Java Server Integration

The LTE bridge exposes a local HTTP API on port 8099. The Java drone server sends data over LTE by POSTing to localhost — no direct LTE knowledge needed in Java code.

### Send to specific drone

```java
try {
    URL url = new URL("http://localhost:8099/lte/send");
    HttpURLConnection conn = (HttpURLConnection) url.openConnection();
    conn.setRequestMethod("POST");
    conn.setRequestProperty("Content-Type", "application/json");
    conn.setDoOutput(true);
    conn.setConnectTimeout(2000);
    conn.setReadTimeout(2000);

    String body = "{\"target\":\"drone_2\",\"payload\":"
        + JSONObject.quote(messageJson)
        + ",\"endpoint\":\"/messenger\"}";

    conn.getOutputStream().write(body.getBytes());
    conn.getResponseCode();
    conn.disconnect();
} catch (Exception e) {
    // LTE bridge not available, continue with mesh
}
```

### Broadcast to all drones

```java
String body = "{\"payload\":" + JSONObject.quote(messageJson)
    + ",\"endpoint\":\"/messenger\"}";
// POST to http://localhost:8099/lte/broadcast
```

### Receiving

No Java changes needed for receiving. The bridge automatically POSTs incoming messages to the local Java server's existing `/messenger` endpoint in the format it already expects.

---

## LTE Bridge HTTP API Reference

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| POST | `/lte/send` | `{"target":"drone_2","payload":"...","endpoint":"/messenger"}` | Send to specific drone |
| POST | `/lte/broadcast` | `{"payload":"...","endpoint":"/messenger"}` | Send to all drones |
| POST | `/lte/publish` | `{"topic":"custom/topic","payload":"..."}` | Publish to custom MQTT topic |
| GET | `/lte/status` | - | Connection status |

## MQTT Topics

| Topic | Direction | Description |
|-------|-----------|-------------|
| `drone/<id>/messenger` | Incoming | Forwarded to Java `/messenger` as `text/plain` |
| `drone/<id>/update` | Incoming | Forwarded to Java `/update` as `application/json` |
| `drone/<id>/gps` | Incoming | Forwarded to Java `/messenger` as `text/plain` |
| `drone/all/messenger` | Incoming | Broadcast, forwarded to Java `/messenger` |
| `drone/status/<id>` | Outgoing | Online/offline status (retained) |

---

## Troubleshooting

### Module not detected
```bash
lsusb                              # Check USB connection
ls /dev/ttyACM*                    # Check serial ports
sudo systemctl stop ModemManager   # Release the port
dmesg | grep -i "simcom\|acm\|usb" # Check kernel messages
```

### No signal (AT+CSQ returns 99,99)
- Check antenna is firmly connected to **MANT** port
- Move antenna closer to a window
- Signal strength 10+ is usable, 20+ is good

### SIM not detected (AT+CPIN? returns ERROR 10)
- Power off Pi, remove and reinsert SIM card
- Check SIM orientation and size (micro/nano)
- Test SIM in a phone to confirm it's active

### LTE connected but no IPv6
```bash
sudo sysctl -w net.ipv6.conf.eth1.accept_ra=2
sudo ip link set eth1 down && sudo ip link set eth1 up
sleep 10
ip -6 addr show dev eth1 scope global
```

### SSH breaks when LTE connects
LTE adds a default route that steals traffic from Ethernet. Fix:
```bash
sudo ip route del default dev eth1
```

The `pi_setup.sh` script's `lte-connect.sh` handles this automatically.

### MQTT won't connect from Pi
```bash
# Test MQTT broker reachability
mosquitto_pub -h YOUR_EC2_IPV6 -p 1883 -t "test" -m "ping"

# If timeout: check EC2 security group has port 1883 open for ::/0
# If connection refused: check Mosquitto is running on EC2
ssh ubuntu@EC2_IP "sudo systemctl status mosquitto"
```

### Port /dev/ttyACM0 busy
```bash
sudo lsof /dev/ttyACM0            # Find what's using it
sudo fuser -k /dev/ttyACM0        # Kill the process
sudo systemctl stop ModemManager   # Usually the culprit
```

### minicom says "cannot open" then connects anyway
This is normal — minicom briefly shows the error during initialization. If the status bar appears and you can type AT commands, it's working.

### Diagnostic port spewing garbage (ttyACM1)
ACM1 is the diagnostic port, not the AT command port. Use ACM0 for AT commands. If you accidentally read from ACM1:
```bash
killall cat
reset
```
