import uasyncio as asyncio
import ujson as json
import ubinascii
import utime
import network
import os
import ssl
from machine import Pin, I2C
import config

# ── Hardware ──────────────────────────────────────────────────────────────────

pin = Pin(5, Pin.OUT)
pin.value(1)          # HIGH = unlocked; set before anything else

led = Pin(2, Pin.OUT) if config.LED_ON_LOCK else None
if led:
    led.value(0)      # off at boot (not locked)

# ── State ─────────────────────────────────────────────────────────────────────

state = {
    "locked":                False,
    "pass_locked":           False,
    "password":              None,   # RAM only, never logged
    "remaining_seconds":     None,
    "lock_seconds_max":      None,
    "random_blind_remaining": None,  # counts down from RANDOM_BLIND_SECS; None = not a random lock
}

# ── Nonce cache ───────────────────────────────────────────────────────────────
#
# Deduplicates retried POSTs: if the ESP32 processes a request but the response
# is dropped by WiFi, the client retries with the same nonce.  We return the
# cached response instead of re-applying the side effect (e.g. adjusting the
# timer twice).  Only successful responses are cached; errors are never stored
# because no state was mutated on an error path.

_nonce_cache = {}       # nonce_str -> (response_bytes, epoch) (insertion-ordered dict)
_NONCE_CACHE_MAX = 16   # keep last N nonces; ~16 covers several minutes of retries
_lock_epoch = 0         # incremented on every unlock; makes all cached entries stale


def _nonce_cache_get(nonce):
    entry = _nonce_cache.get(nonce)
    if not entry:
        return None
    resp, epoch = entry
    if epoch != _lock_epoch:   # lock was released since this response was cached
        return None
    return resp


def _nonce_cache_put(nonce, resp):
    if len(_nonce_cache) >= _NONCE_CACHE_MAX:
        _nonce_cache.pop(next(iter(_nonce_cache)))   # evict oldest
    _nonce_cache[nonce] = (resp, _lock_epoch)


# ── Helpers ───────────────────────────────────────────────────────────────────

def set_pin(locked):
    """LOW when locked, HIGH when unlocked. Mirrors state to LED if enabled."""
    pin.value(0 if locked else 1)
    if led:
        led.value(1 if locked else 0)


def do_unlock():
    """Clear lock state and set pin HIGH."""
    global _lock_epoch
    _lock_epoch += 1
    set_pin(False)
    state["locked"] = False
    state["remaining_seconds"] = None
    state["random_blind_remaining"] = None


def do_pass_unlock():
    """Clear all pass-lock state and set pin HIGH."""
    global _lock_epoch
    _lock_epoch += 1
    set_pin(False)
    state["locked"] = False
    state["pass_locked"] = False
    state["remaining_seconds"] = None
    state["lock_seconds_max"] = None
    state["password"] = None
    state["random_blind_remaining"] = None


def gen_password():
    return ubinascii.hexlify(os.urandom(32)).decode()

# ── Background countdown ──────────────────────────────────────────────────────
#
# utime.ticks_ms() / ticks_diff() measure elapsed wall-clock time directly.
# If the asyncio event loop is briefly blocked (e.g. during a TLS handshake),
# countdown_loop cannot fire — but ticks_ms() still advances, so the next
# 200 ms wake-up sees the full elapsed time and catches up correctly.




# ── Optional S-35710 external watchdog ─────────────────────────────────────────
#
# If enabled in config.py, the ESP32 periodically re-arms an Adafruit S-35710
# wake-up timer over I2C. If the firmware freezes and stops re-arming it, the
# external watchdog circuit releases the lock through the OR gate.

def cfg(name, default):
    """Read optional config values without breaking older config.py files."""
    return getattr(config, name, default)


WATCHDOG_ENABLED = bool(cfg("WATCHDOG_ENABLED", 0))
WATCHDOG_SDA_PIN = cfg("WATCHDOG_SDA_PIN", 21)
WATCHDOG_SCL_PIN = cfg("WATCHDOG_SCL_PIN", 22)
WATCHDOG_I2C_ADDR = cfg("WATCHDOG_I2C_ADDR", 0x32)
WATCHDOG_TIMEOUT_SECS = cfg("WATCHDOG_TIMEOUT_SECS", 30)
WATCHDOG_FEED_SECS = cfg("WATCHDOG_FEED_SECS", 5)


class S35710:
    """Minimal MicroPython driver for the S-35710 wake-up timer."""

    def __init__(self, i2c, address=0x32):
        self.i2c = i2c
        self.address = address

    def arm(self, seconds):
        """Set/re-arm the watchdog alarm in seconds."""
        seconds = int(seconds)
        if seconds < 1:
            seconds = 1
        if seconds > 0xFFFFFF:
            seconds = 0xFFFFFF

        self.i2c.writeto(self.address, bytes([
            0x81,
            (seconds >> 16) & 0xFF,
            (seconds >> 8) & 0xFF,
            seconds & 0xFF,
        ]))


def init_watchdog():
    """Initialize and arm the optional external watchdog."""
    if not WATCHDOG_ENABLED:
        print("Watchdog: disabled")
        return None

    if WATCHDOG_FEED_SECS >= WATCHDOG_TIMEOUT_SECS:
        raise RuntimeError("WATCHDOG_FEED_SECS must be less than WATCHDOG_TIMEOUT_SECS")

    i2c = I2C(
        0,
        sda=Pin(WATCHDOG_SDA_PIN),
        scl=Pin(WATCHDOG_SCL_PIN),
        freq=100000,
    )

    found = i2c.scan()
    if WATCHDOG_I2C_ADDR not in found:
        raise RuntimeError("S-35710 watchdog not found on I2C bus")

    watchdog = S35710(i2c, WATCHDOG_I2C_ADDR)
    watchdog.arm(WATCHDOG_TIMEOUT_SECS)
    print("Watchdog: enabled, timeout =", WATCHDOG_TIMEOUT_SECS, "seconds")
    return watchdog


async def watchdog_loop(watchdog):
    """Periodically re-arm the external watchdog while firmware is healthy."""
    if watchdog is None:
        return

    while True:
        try:
            await asyncio.sleep(WATCHDOG_FEED_SECS)
            watchdog.arm(WATCHDOG_TIMEOUT_SECS)
        except Exception as exc:
            print("ERROR: watchdog feed failed:", exc)
            set_pin(False)  # fail open / unlock

            # Stop feeding forever. If the external watchdog circuit is present,
            # it will time out and keep the lock released.
            while True:
                await asyncio.sleep(60)


async def countdown_loop():
    last_ms = utime.ticks_ms()
    while True:
        try:
            await asyncio.sleep_ms(200)
            now_ms = utime.ticks_ms()
            elapsed_ms = utime.ticks_diff(now_ms, last_ms)
            elapsed_secs = elapsed_ms // 1000
            if elapsed_secs <= 0:
                continue
            # Advance last_ms by whole seconds consumed, preserving sub-second remainder
            last_ms = utime.ticks_add(last_ms, elapsed_secs * 1000)

            if state["remaining_seconds"] is not None:
                state["remaining_seconds"] -= elapsed_secs
                if state["remaining_seconds"] <= 0:
                    if state["pass_locked"]:
                        do_pass_unlock()
                    else:
                        do_unlock()
            if state["pass_locked"] and state["lock_seconds_max"] is not None:
                state["lock_seconds_max"] -= elapsed_secs
                if state["lock_seconds_max"] <= 0:
                    do_pass_unlock()
            if state["random_blind_remaining"] is not None:
                state["random_blind_remaining"] = max(0, state["random_blind_remaining"] - elapsed_secs)
        except Exception as exc:
            print("countdown_loop error:", exc)

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def parse_request(data):
    """Return (method, path, query_string, body_bytes) from raw HTTP request bytes."""
    sep = data.find(b"\r\n\r\n")
    headers_raw = data[:sep] if sep != -1 else data
    body = data[sep + 4:] if sep != -1 else b""
    first_line = headers_raw.split(b"\r\n")[0].decode()
    parts = first_line.split(" ")
    method = parts[0] if len(parts) > 0 else ""
    raw_path = parts[1] if len(parts) > 1 else "/"
    path_parts = raw_path.split("?", 1)
    path = path_parts[0]
    query_string = path_parts[1] if len(path_parts) > 1 else ""
    return method, path, query_string, body


def response(status, body_dict):
    body_bytes = json.dumps(body_dict).encode()
    headers = (
        "HTTP/1.1 {}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: {}\r\n"
        "Connection: keep-alive\r\n"
        "Keep-Alive: timeout=10\r\n"
        "\r\n"
    ).format(status, len(body_bytes)).encode()
    return headers + body_bytes


def ok(body_dict):
    return response("200 OK", body_dict)


def err(code, msg):
    return response(code, {"error": msg})


def parse_body(raw):
    """Parse JSON body; return (dict, None) or (None, error_response)."""
    if not raw:
        return {}, None
    try:
        text = raw.decode().strip()
    except Exception:
        return None, err("400 Bad Request", "invalid encoding")
    if not text:
        return {}, None
    try:
        return json.loads(text), None
    except Exception:
        return None, err("400 Bad Request", "invalid JSON")

# ── Endpoint handlers ─────────────────────────────────────────────────────────

def handle_status(query_string=""):
    blind_active = (
        state["random_blind_remaining"] is not None
        and state["random_blind_remaining"] > 0
    )
    resp = {
        "locked":            state["locked"],
        "pass_locked":       state["pass_locked"],
        "remaining_seconds": None if blind_active else state["remaining_seconds"],
        "lock_seconds_max":  state["lock_seconds_max"],
        "random_blind":      blind_active,
    }
    if query_string:
        params = dict(p.split("=", 1) for p in query_string.split("&") if "=" in p)
        pw = params.get("password")
        if pw is not None:
            if state["pass_locked"]:
                resp["password_valid"] = (pw == state["password"])
            # if not pass-locked, omit password_valid entirely
    return ok(resp)


def handle_lock(body_raw):
    data, e = parse_body(body_raw)
    if e:
        return e

    # Nonce dedup: if the response was dropped and the client retries, return
    # the cached response instead of re-applying side effects (timer reset,
    # blind-period erasure).  Must come before the guards so a retry after a
    # dropped response reaches the cache rather than hitting the 409.
    nonce = data.get("nonce")
    if nonce:
        cached = _nonce_cache_get(nonce)
        if cached:
            return cached

    if state["pass_locked"]:
        return err("403 Forbidden", "pass_locked - use /pass-unlock")
    if state["locked"]:
        return err("409 Conflict", "already locked")

    lock_seconds = data.get("lock_seconds", None)

    if lock_seconds is not None:
        try:
            lock_seconds = int(lock_seconds)
        except (ValueError, TypeError):
            return err("400 Bad Request", "lock_seconds must be an integer")
        if lock_seconds <= 0:
            return err("400 Bad Request", "lock_seconds must be positive")

    state["locked"] = True
    set_pin(True)
    state["remaining_seconds"] = lock_seconds
    if data.get("random_lock"):
        state["random_blind_remaining"] = config.RANDOM_BLIND_SECS if config.RANDOM_BLIND_SECS > 0 else None
    else:
        state["random_blind_remaining"] = None

    blind_active = (
        state["random_blind_remaining"] is not None
        and state["random_blind_remaining"] > 0
    )
    result = ok({"locked": True, "remaining_seconds": None if blind_active else state["remaining_seconds"]})
    if nonce:
        _nonce_cache_put(nonce, result)
    return result


def handle_unlock(body_raw):
    if state["pass_locked"]:
        return err("403 Forbidden", "pass_locked - use /pass-unlock")

    do_unlock()
    return ok({"locked": False})


def handle_pass_lock(body_raw):
    data, e = parse_body(body_raw)
    if e:
        return e

    # Nonce dedup: if the response was dropped and the client retries, return
    # the cached response (which includes the one-time password) instead of
    # trying to lock again and returning "already pass-locked".
    # IMPORTANT: this check must come before the pass_locked/locked guards so
    # that a retry after a dropped response reaches the cache rather than
    # hitting the guard and losing the password permanently.
    nonce = data.get("nonce")
    if nonce:
        cached = _nonce_cache_get(nonce)
        if cached:
            return cached

    if state["pass_locked"]:
        return err("403 Forbidden", "already pass-locked")
    if state["locked"]:
        return err("409 Conflict", "locked — use /unlock before /pass-lock")

    lock_seconds     = data.get("lock_seconds")
    lock_seconds_max = data.get("lock_seconds_max")

    if lock_seconds is None or lock_seconds_max is None:
        return err("400 Bad Request", "lock_seconds and lock_seconds_max are required")

    try:
        lock_seconds     = int(lock_seconds)
        lock_seconds_max = int(lock_seconds_max)
    except (ValueError, TypeError):
        return err("400 Bad Request", "lock_seconds and lock_seconds_max must be integers")

    if lock_seconds <= 0 or lock_seconds_max <= 0:
        return err("400 Bad Request", "lock_seconds and lock_seconds_max must be positive")

    if lock_seconds > lock_seconds_max:
        return err("400 Bad Request", "lock_seconds must be <= lock_seconds_max")

    password = gen_password()

    state["locked"]           = True
    state["pass_locked"]      = True
    state["password"]         = password
    state["remaining_seconds"] = lock_seconds
    state["lock_seconds_max"] = lock_seconds_max
    if data.get("random_lock"):
        state["random_blind_remaining"] = config.RANDOM_BLIND_SECS if config.RANDOM_BLIND_SECS > 0 else None
    else:
        state["random_blind_remaining"] = None
    set_pin(True)

    blind_active = (
        state["random_blind_remaining"] is not None
        and state["random_blind_remaining"] > 0
    )
    result = ok({
        "locked":           True,
        "pass_locked":      True,
        "remaining_seconds": None if blind_active else lock_seconds,
        "lock_seconds_max": lock_seconds_max,
        "password":         password,
    })
    if nonce:
        _nonce_cache_put(nonce, result)
    return result


def handle_pass_unlock(body_raw):
    data, e = parse_body(body_raw)
    if e:
        return e

    # Nonce dedup: if the response was dropped and the client retries, return
    # the cached response instead of failing the "not pass-locked" guard
    # (do_pass_unlock has already cleared state on the original request).
    # This check must come BEFORE the pass_locked/password guards: unlike
    # /adjust-timer, /pass-unlock wipes state["password"], so on a retry the
    # password check would fail before the nonce check is reached. Replaying a
    # captured nonce without the password just returns the cached success bytes
    # — no actual unlock occurs and the password is never exposed.
    nonce = data.get("nonce")
    if nonce:
        cached = _nonce_cache_get(nonce)
        if cached:
            return cached

    if not state["pass_locked"]:
        return err("400 Bad Request", "not pass-locked")

    if data.get("password") != state["password"]:
        return err("403 Forbidden", "wrong password")

    do_pass_unlock()
    result = ok({"locked": False, "pass_locked": False})
    if nonce:
        _nonce_cache_put(nonce, result)
    return result


def handle_adjust_timer(body_raw):
    data, e = parse_body(body_raw)
    if e:
        return e

    if not state["pass_locked"]:
        return err("400 Bad Request", "not pass-locked")

    if data.get("password") != state["password"]:
        return err("403 Forbidden", "wrong password")

    # Nonce dedup: if the response was dropped and the client retries, return
    # the cached response instead of applying the adjustment a second time.
    # NOTE: this check must come AFTER authentication — a valid nonce must not
    # be replayable without the correct password.
    nonce = data.get("nonce")
    if nonce:
        cached = _nonce_cache_get(nonce)
        if cached:
            return cached

    adjust_seconds = data.get("adjust_seconds")
    if adjust_seconds is None:
        return err("400 Bad Request", "adjust_seconds is required")

    try:
        adjust_seconds = int(adjust_seconds)
    except (ValueError, TypeError):
        return err("400 Bad Request", "adjust_seconds must be an integer")

    proposed = state["remaining_seconds"] + adjust_seconds
    clamped  = False

    if proposed > state["lock_seconds_max"]:
        proposed = state["lock_seconds_max"]
        clamped  = True

    if proposed <= 0:
        do_pass_unlock()
        result = ok({
            "remaining_seconds": None,
            "lock_seconds_max":  None,
            "clamped":           clamped,
        })
        if nonce:
            _nonce_cache_put(nonce, result)
        return result

    state["remaining_seconds"] = proposed

    blind_active = (
        state["random_blind_remaining"] is not None
        and state["random_blind_remaining"] > 0
    )
    result = ok({
        "remaining_seconds": None if blind_active else proposed,
        "lock_seconds_max":  state["lock_seconds_max"],
        "clamped":           clamped,
    })
    if nonce:
        _nonce_cache_put(nonce, result)
    return result

# ── HTTP server ───────────────────────────────────────────────────────────────

ROUTES = {
    ("GET",  "/status"):       handle_status,
    ("POST", "/lock"):         handle_lock,
    ("POST", "/unlock"):       handle_unlock,
    ("POST", "/pass-lock"):    handle_pass_lock,
    ("POST", "/pass-unlock"):  handle_pass_unlock,
    ("POST", "/adjust-timer"): handle_adjust_timer,
}

KEEPALIVE_TIMEOUT = 10  # seconds; close idle connection after this

_MAX_CONNECTIONS = 4    # reject new connections above this limit
_active_connections = 0


async def handle_client(reader, writer):
    global _active_connections

    # Enforce connection limit before any I/O (TLS handshake is lazy in
    # MicroPython, so this also avoids the handshake cost for excess connections).
    if _active_connections >= _MAX_CONNECTIONS:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        return

    _active_connections += 1
    try:
        while True:
            try:
                data = await asyncio.wait_for(reader.read(4096), KEEPALIVE_TIMEOUT)
            except asyncio.TimeoutError:
                break          # idle too long — close gracefully
            if not data:
                break          # client closed its end

            method, path, query_string, body = parse_request(data)

            key = (method, path)
            if key not in ROUTES:
                resp = err("404 Not Found", "not found")
            elif method == "GET":
                resp = ROUTES[key](query_string)
            else:
                resp = ROUTES[key](body)

            writer.write(resp)
            await writer.drain()
    except Exception as exc:
        print("client error:", exc)
    finally:
        _active_connections -= 1
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

# ── Boot ──────────────────────────────────────────────────────────────────────

async def main():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)

    print("Connecting to WiFi...")
    for _ in range(40):
        if wlan.isconnected():
            break
        await asyncio.sleep(0.5)
    else:
        print("ERROR: WiFi connection timed out after 20s — check SSID/password")
        raise SystemExit(1)

    ip = wlan.ifconfig()[0]
    print("WiFi connected:", ip)

    try:
        watchdog = init_watchdog()
    except Exception as exc:
        print("ERROR: watchdog enabled but failed:", exc)
        set_pin(False)  # fail open
        raise SystemExit(1)

    asyncio.create_task(countdown_loop())
    asyncio.create_task(watchdog_loop(watchdog))

    # Load TLS credentials if provisioned; fall back to plain HTTP only when
    # neither cert.pem nor key.pem is present (pre-provisioning / dev mode).
    # If the files exist but fail to load (e.g. corruption), the exception
    # propagates and the device halts — deliberately, to prevent a silent
    # downgrade to unencrypted HTTP.
    ssl_ctx = None
    files = os.listdir()
    if "cert.pem" in files and "key.pem" in files:
        print("TLS: loading cert.pem / key.pem...")
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain("cert.pem", "key.pem")  # raises on failure — intentional
        port = 443
        scheme = "HTTPS"
    else:
        port = 80
        scheme = "HTTP"
        print("TLS: cert.pem / key.pem not found — running HTTP (run setup-tls.sh to provision)")

    server = await asyncio.start_server(
        handle_client, "0.0.0.0", port, ssl=ssl_ctx, backlog=4
    )
    print(scheme, "server listening on", ip, "port", port)

    while True:
        await asyncio.sleep(30)
        if not wlan.isconnected():
            print("WiFi lost — reconnecting...")
            wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
            for _ in range(40):
                if wlan.isconnected():
                    break
                await asyncio.sleep(0.5)
            if wlan.isconnected():
                print("WiFi reconnected:", wlan.ifconfig()[0])
            else:
                print("ERROR: WiFi reconnect failed")


asyncio.run(main())
