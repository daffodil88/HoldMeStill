#!/usr/bin/env bash
# setup-tls.sh — Provision the ESP32 with a self-signed TLS certificate.
#
# Run this once from the repo root on a machine with USB access to the ESP32.
# The private key is generated locally, uploaded to the ESP32, then deleted.
# The certificate (public) is kept as esp32_cert.pem for use with setup-tls.
#
# Prerequisites:
#   openssl   — for certificate generation
#   mpremote  — for uploading to the ESP32 (pip install mpremote)
#
# After running this script:
#   1. Restart the ESP32 (it will start HTTPS on port 443).
#   2. On each machine that uses lock_client.py, run:
#        pi/lock_client.py setup-tls
#      then add the printed CERT_PATH line to pi/config.

set -euo pipefail

# ── Help ──────────────────────────────────────────────────────────────────────

usage() {
    cat <<'EOF'
setup-tls.sh — Provision the ESP32 with a self-signed TLS certificate

USAGE
    ./setup-tls.sh [-h | help]

WHAT IT DOES
    1. Reads BASE_URL and CERT_PATH from pi/config.
    2. Verifies the ESP32 is connected via USB and has no existing certificate.
    3. Generates a P-256 EC private key and a 100-year self-signed certificate
       with the correct Subject Alternative Name (hostname + IP).
    4. Uploads cert.pem and key.pem to the ESP32 filesystem via mpremote.
    5. Deletes the private key from this machine immediately after upload.
    6. If CERT_PATH is set in pi/config and its directory exists, saves the
       certificate there automatically.

PREREQUISITES
    openssl    — certificate generation  (brew install openssl / apt install openssl)
    mpremote   — ESP32 file transfer     (pip install mpremote)
    USB cable  — the ESP32 must be connected and detected by mpremote

AFTER RUNNING
    1. Restart the ESP32 so it picks up the new certificate and key.
       It will switch from HTTP (port 80) to HTTPS (port 443).
    2. On EACH machine that uses lock_client.py (the Pi, and any other computer),
       run:
           pi/lock_client.py setup-tls
       and add the printed CERT_PATH line to pi/config.
    3. On the machine that ran this script the certificate is already available
       locally. Set CERT_PATH in pi/config to the path shown when the script
       finishes (no need to run lock_client.py setup-tls on this machine).

OPTIONS
    -h, help   Show this help and exit
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "help" ]]; then
    usage
    exit 0
fi

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/pi/config"
LOCAL_CERT="$SCRIPT_DIR/esp32_cert.pem"

# The private key is written to /tmp (tmpfs / RAM-backed on Linux) so it never
# touches persistent storage.  The EXIT trap guarantees deletion even if the
# script is interrupted or fails partway through.
LOCAL_KEY=$(mktemp /tmp/esp32_key.XXXXXX.pem)
trap 'rm -f "$LOCAL_KEY"' EXIT

# ── Read config ───────────────────────────────────────────────────────────────

BASE_URL=$(grep -m1 '^BASE_URL=' "$CONFIG" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)
CERT_PATH=$(grep -m1 '^CERT_PATH=' "$CONFIG" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)

# Where the certificate will be written: CERT_PATH when configured, otherwise
# the repo root (so lock_client.py setup-tls can be used to pin it later).
CERT_OUT="${CERT_PATH:-$LOCAL_CERT}"

if [[ -z "$BASE_URL" ]]; then
    echo "Error: BASE_URL is not set in pi/config." >&2
    echo "       Set it to the ESP32's address, e.g.: BASE_URL=https://192.168.1.88" >&2
    exit 1
fi

# Extract hostname from BASE_URL (strip scheme, port, and trailing path).
HOSTNAME=$(echo "$BASE_URL" | sed -E 's|^https?://||; s|/.*||; s|:[0-9]+$||')

if [[ -z "$HOSTNAME" ]]; then
    echo "Error: could not parse hostname from BASE_URL ($BASE_URL)." >&2
    exit 1
fi

# ── Build SAN string ──────────────────────────────────────────────────────────

# TLS validation requires IP: for IP addresses and DNS: for hostnames — they
# are not interchangeable.  Use whichever type matches BASE_URL.
if [[ "$HOSTNAME" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    SAN="IP:$HOSTNAME"
else
    SAN="DNS:$HOSTNAME"
fi

echo "Hostname : $HOSTNAME"
echo "SAN      : $SAN"
echo ""

# ── mpremote with retry ───────────────────────────────────────────────────────

# Wrap every mpremote call in retry logic: the ESP32 sometimes needs a moment
# to enumerate on USB before the first connection succeeds.  A per-attempt
# timeout prevents mpremote from hanging indefinitely if the device is found
# but not yet responsive.
mpremote_retry() {
    local max_attempts=3
    local delay=1
    local timeout_secs=7
    local attempt=1
    while true; do
        echo "mpremote $* (attempt $attempt/$max_attempts)..." >&2
        if timeout "$timeout_secs" mpremote "$@"; then
            return 0
        fi
        if (( attempt >= max_attempts )); then
            echo "Error: mpremote failed after $max_attempts attempts." >&2
            return 1
        fi
        echo "Retrying in ${delay}s..." >&2
        sleep "$delay"
        (( attempt++ ))
    done
}

# ── Pre-flight checks ─────────────────────────────────────────────────────────

# 1. Validate CERT_PATH (if configured).
if [[ -n "$CERT_PATH" ]]; then
    if [[ -e "$CERT_PATH" ]]; then
        echo "Error: CERT_PATH already contains a file at $CERT_PATH." >&2
        echo "       Remove it before re-provisioning, then update CERT_PATH in pi/config." >&2
        exit 1
    fi
    CERT_PATH_DIR="$(dirname "$CERT_PATH")"
    if [[ ! -d "$CERT_PATH_DIR" ]]; then
        echo "Error: CERT_PATH directory does not exist: $CERT_PATH_DIR" >&2
        echo "       Create the directory or update CERT_PATH in pi/config." >&2
        exit 1
    fi
fi

# 2. Refuse to overwrite an existing local cert (only relevant when CERT_PATH
#    is not set; the CERT_PATH check above already covers the other case).
if [[ -z "$CERT_PATH" && -e "$LOCAL_CERT" ]]; then
    echo "Error: $LOCAL_CERT already exists." >&2
    echo "       Remove it before generating a new certificate." >&2
    exit 1
fi

# 3. Check ESP32 connectivity and whether it already has a certificate.
echo "Checking ESP32 connectivity..."

DEVS=$(mpremote devs 2>/dev/null | grep -E '/dev/tty(USB|ACM)')
if [[ -z "$DEVS" ]]; then
    echo "Error: No ESP32 detected. Connect via USB and try again." >&2
    exit 1
fi
DEV_COUNT=$(echo "$DEVS" | wc -l)
if (( DEV_COUNT > 1 )); then
    echo "Error: Multiple devices detected. Disconnect all but one and try again:" >&2
    echo "$DEVS" | awk '{print "  " $1}' >&2
    exit 1
fi
DEVICE=$(echo "$DEVS" | awk '{print $1}')

if ! LS_OUTPUT=$(mpremote_retry ls 2>/dev/null); then
    echo "Error: ESP32 not responding. Try reconnecting the USB cable." >&2
    exit 1
fi
echo "ESP32 connected: $DEVICE"

if echo "$LS_OUTPUT" | grep -qE 'cert\.pem|key\.pem'; then
    echo "Error: The ESP32 already has a certificate or key (cert.pem / key.pem)." >&2
    echo "       Remove them from the device before re-provisioning." >&2
    exit 1
fi
echo ""

# ── Generate certificate ──────────────────────────────────────────────────────

echo "Generating EC private key and self-signed certificate (valid 100 years)..."
openssl ecparam -name prime256v1 -genkey -noout -out "$LOCAL_KEY"
openssl req -new -x509 \
    -key "$LOCAL_KEY" \
    -out "$CERT_OUT" \
    -days 36500 \
    -subj "/CN=lock-esp32" \
    -addext "subjectAltName=$SAN"

echo "Certificate written to $CERT_OUT"
echo ""

# ── Upload to ESP32 ───────────────────────────────────────────────────────────

echo "Uploading cert.pem and key.pem to ESP32..."
mpremote_retry cp "$CERT_OUT" :cert.pem
mpremote_retry cp "$LOCAL_KEY"  :key.pem
echo "Upload complete."
echo ""

# ── Done ──────────────────────────────────────────────────────────────────────

echo "Done. Next steps:"
echo ""
echo "  1. Restart the ESP32 so it picks up the new certificate and key."
echo "     It will switch from HTTP (port 80) to HTTPS (port 443)."
echo ""
echo "  2. On EACH machine that uses lock_client.py (the Pi, and any other computer):"
echo ""
echo "       pi/lock_client.py setup-tls"
echo ""
echo "     This fetches the certificate from the ESP32 and pins it."
echo "     Follow the printed instructions to set CERT_PATH in pi/config."
echo ""
if [[ -n "$CERT_PATH" ]]; then
    echo "     On this machine CERT_PATH is already configured in pi/config —"
    echo "     skip this step."
else
    echo "     On this machine you can skip that step and set CERT_PATH directly,"
    echo "     since you already have the certificate:"
    echo ""
    echo "       CERT_PATH=$CERT_OUT"
fi
echo ""
