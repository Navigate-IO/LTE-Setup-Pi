#!/bin/bash
# ============================================================
# Raspberry Pi LTE Setup Script
#
# Sets up a Raspberry Pi with a SIM767XX LTE module for
# drone-to-drone communication via MQTT over LTE.
#
# Run once on each Pi. Requires a SIM767XX module connected
# via USB with an active T-Mobile SIM card and LTE antenna.
#
# Usage: sudo ./pi_setup.sh
# ============================================================

set -e

if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo ./pi_setup.sh"
    exit 1
fi

echo "========================================"
echo "  NavigateIO LTE Bridge - Pi Setup"
echo "========================================"
echo ""

# ---- Disable ModemManager ----
echo "[1/5] Disabling ModemManager..."
systemctl stop ModemManager 2>/dev/null || true
systemctl disable ModemManager 2>/dev/null || true
echo "  Done."

# ---- Install dependencies ----
echo "[2/5] Installing dependencies..."
apt update -qq
apt install -y python3-pip mosquitto-clients minicom
pip3 install paho-mqtt --break-system-packages 2>/dev/null || pip3 install paho-mqtt
echo "  Done."

# ---- Create LTE auto-connect script ----
echo "[3/5] Creating LTE auto-connect script..."
cat > /usr/local/bin/lte-connect.sh << 'LTEEOF'
#!/bin/bash
# LTE ECM Auto-Connect Script for SIM767XX
DEVICE="/dev/ttyACM0"
LOG="/var/log/lte-connect.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG"
    echo "$1"
}

# Stop ModemManager if running
systemctl stop ModemManager 2>/dev/null || true

# Wait for device
RETRIES=30
while [ ! -e "$DEVICE" ] && [ $RETRIES -gt 0 ]; do
    log "Waiting for $DEVICE... ($RETRIES)"
    sleep 2
    RETRIES=$((RETRIES - 1))
done

if [ ! -e "$DEVICE" ]; then
    log "ERROR: $DEVICE not found"
    exit 1
fi

# Release any processes holding the port
fuser -k "$DEVICE" 2>/dev/null || true
sleep 2

log "Enabling auto-dial..."
(cat "$DEVICE" > /dev/null 2>&1 &)
sleep 1
echo -e "AT+DIALMODE=0\r\n" > "$DEVICE"
sleep 5
killall cat 2>/dev/null || true

# Find LTE interface
LTE_IF=""
for iface in eth1 eth0 usb0; do
    if ip link show "$iface" > /dev/null 2>&1; then
        # Skip if this is the main ethernet (has a non-192.168.0.x IP)
        EXISTING_IP=$(ip -4 addr show "$iface" 2>/dev/null | grep "inet " | awk '{print $2}' | head -1)
        if [ -z "$EXISTING_IP" ] || echo "$EXISTING_IP" | grep -q "192.168.0"; then
            LTE_IF="$iface"
            break
        fi
    fi
done

if [ -z "$LTE_IF" ]; then
    LTE_IF=$(ip -o link show | grep -v 'lo\|wlan\|bat\|docker\|br-\|vir' | awk -F': ' '{print $2}' | grep -E '^(enx|usb|eth)' | tail -1)
fi

if [ -z "$LTE_IF" ]; then
    log "ERROR: No LTE interface found"
    exit 1
fi

log "LTE interface: $LTE_IF"

# Get IP
dhclient "$LTE_IF" 2>> "$LOG" || true
sleep 3

# Set IPv6 DNS
echo "nameserver 2001:4860:4860::8888" > /etc/resolv.conf
echo "nameserver 2001:4860:4860::8844" >> /etc/resolv.conf

# Remove IPv4 default route on LTE to avoid breaking SSH
ip route del default dev "$LTE_IF" 2>/dev/null || true

if ping -c 1 -W 5 -I "$LTE_IF" 2001:4860:4860::8888 > /dev/null 2>&1; then
    log "SUCCESS: LTE is up on $LTE_IF"
else
    log "WARNING: LTE interface up but connectivity check failed"
fi
LTEEOF
chmod +x /usr/local/bin/lte-connect.sh
echo "  Done."

# ---- Create systemd services ----
echo "[4/5] Creating systemd services..."

cat > /etc/systemd/system/lte-connect.service << 'EOF'
[Unit]
Description=LTE ECM Auto-Connect
After=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/lte-connect.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/lte-bridge.service << EOF
[Unit]
Description=LTE MQTT Bridge for Drone Communication
After=lte-connect.service network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStartPre=/bin/sleep 10
ExecStart=/usr/bin/python3 $(pwd)/lte_bridge.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable lte-connect
systemctl enable lte-bridge
echo "  Done."

# ---- Summary ----
echo ""
echo "[5/5] Setup complete!"
echo ""
echo "========================================"
echo "  IMPORTANT: First-time module setup"
echo "========================================"
echo ""
echo "  If this is a NEW SIM767XX module, you need to"
echo "  configure ECM mode once (stored on module):"
echo ""
echo "    sudo minicom -D /dev/ttyACM0 -b 115200"
echo ""
echo "  In minicom type:"
echo "    AT+CGDCONT=1,\"IPV4V6\",\"fast.t-mobile.com\""
echo "    AT\$MYCONFIG=\"usbnetmode\",1"
echo ""
echo "  Exit minicom (Ctrl+A, X) and reboot."
echo "  This only needs to be done once per module."
echo ""
echo "========================================"
echo "  Configuration"
echo "========================================"
echo ""
echo "  Edit lte_bridge.py and set:"
echo "    EC2_IPV6  = your EC2 IPv6 address"
echo "    DRONE_ID  = \"drone_1\" or \"drone_2\""
echo ""
echo "========================================"
echo "  Running"
echo "========================================"
echo ""
echo "  Start now:"
echo "    sudo /usr/local/bin/lte-connect.sh"
echo "    sudo python3 lte_bridge.py"
echo ""
echo "  Or reboot and services start automatically."
echo ""
echo "  Test:"
echo "    curl http://localhost:8099/lte/status"
echo ""
echo "========================================"
