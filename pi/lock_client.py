#!/usr/bin/env python3
"""Lock client for ESP32 magnetic lock controller.

Can be used as a CLI script or imported as a module:

    from lock_client import api_status, api_pass_lock, api_pass_adjust
"""

import datetime
import math
import os
import random
import re
import sys
import time

import requests

_session = requests.Session()

# ── Config ─────────────────────────────────────────────────────────────────────

def _load_config(path: str) -> dict:
    """Parse a simple KEY=VALUE config file into a dict of strings."""
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()
    return config

_cfg = _load_config(os.path.join(os.path.dirname(os.path.realpath(__file__)), "config"))
BASE_URL              = _cfg.get("BASE_URL", "")
CERT_PATH             = _cfg.get("CERT_PATH", "").strip()   # path to ESP32 TLS cert; empty = system CA
VERIFY_TOLERANCE_SECS = int(_cfg.get("VERIFY_TOLERANCE_SECS", "5"))

RANDOM_START_PERCENT  = int(_cfg.get("RANDOM_START_PERCENT", "50"))

# ── Time parsing ───────────────────────────────────────────────────────────────

def parse_duration(s: str) -> int:
    """Parse a duration string and return seconds as an integer.

    Accepted formats:
        90          bare integer = seconds
        90m         minutes
        1h          hours
        1h30m       hours + minutes
        1h30m45s    hours + minutes + seconds
        20:30       absolute clock time (local); midnight rollover handled

    For pass-adjust the caller strips a leading +/- before calling this.
    """
    # Absolute clock time HH:MM
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        target_h = int(m.group(1))
        target_m = int(m.group(2))
        now = time.time()
        today_midnight = datetime.datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        target_seconds = today_midnight + target_h * 3600 + target_m * 60
        delta = target_seconds - now
        if delta <= 0:
            target_seconds += 86400
            delta = target_seconds - now
        return math.ceil(delta)

    # Relative duration: optional h/m/s components or bare integer
    total = 0
    remainder = s

    m = re.match(r'^(\d+)h(.*)$', remainder)
    if m:
        total += int(m.group(1)) * 3600
        remainder = m.group(2)

    m = re.match(r'^(\d+)m(.*)$', remainder)
    if m:
        total += int(m.group(1)) * 60
        remainder = m.group(2)

    m = re.match(r'^(\d+)s$', remainder)
    if m:
        total += int(m.group(1))
        remainder = ""

    m = re.match(r'^(\d+)$', remainder)
    if m:
        total += int(m.group(1))
        remainder = ""

    if remainder:
        raise ValueError(f"Error: unrecognised time format: '{s}'")

    return total

# ── Random duration selection ──────────────────────────────────────────────────

def _random_lock_secs(duration_secs: int) -> int:
    """Pick a random duration in [RANDOM_START_PERCENT%, 100%] of duration_secs."""
    min_secs = int(duration_secs * RANDOM_START_PERCENT / 100)
    return random.randint(min_secs, duration_secs)


def _random_pass_lock_secs(initial_secs: int, max_secs: int) -> int:
    """Pick a random initial duration in the selectable slice of [initial, max].

    The first RANDOM_START_PERCENT% of the span (max - initial) is excluded.
    """
    span = max_secs - initial_secs
    min_secs = initial_secs + int(span * RANDOM_START_PERCENT / 100)
    return random.randint(min_secs, max_secs)

# ── HTTP helpers ───────────────────────────────────────────────────────────────

_REQUEST_RETRIES           = 2  # extra attempts after the first failure
_REQUEST_RETRIES_PASS_LOCK = 5  # /pass-lock-specific: a dropped response leaves the
                                # ESP32 holding a password the client never received,
                                # and the password cannot be re-fetched. Extra retries
                                # widen the window in which the nonce cache will
                                # short-circuit a successful retry.
_REQUEST_RETRY_DELAY       = 2  # seconds between retries after a Timeout
_REQUEST_RECONNECT_DELAY   = 6  # seconds between retries after a ConnectionError
                                # (device offline — longer wait lets it reconnect)


def _ssl_cert_error(exc: Exception) -> None:
    """Print a certificate mismatch message and exit. Does not return."""
    if CERT_PATH:
        print(
            f"Error: The ESP32's TLS certificate does not match the pinned certificate at\n"
            f"  {CERT_PATH}\n"
            "The device may have been reprovisioned. "
            "Run 'lock_client.py setup-tls' to re-pin.",
            file=sys.stderr,
        )
    else:
        print(
            f"Error: TLS certificate verification failed for {BASE_URL}.\n"
            "Run 'lock_client.py setup-tls' to pin the ESP32 certificate,\n"
            "then add CERT_PATH=<path> to the config file.",
            file=sys.stderr,
        )
    sys.exit(1)


def _check_cert_path() -> None:
    """Exit with a clear message if CERT_PATH/BASE_URL are misconfigured."""
    if not BASE_URL:
        cfg_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config")
        print(
            f"Error: BASE_URL is not set in {cfg_path}\n"
            "Set it to the ESP32's address, e.g.: BASE_URL=https://192.168.1.88",
            file=sys.stderr,
        )
        sys.exit(1)
    if CERT_PATH and not BASE_URL.startswith("https://"):
        print(
            f"Error: CERT_PATH is set but BASE_URL does not use https://.\n"
            f"  BASE_URL:  {BASE_URL}\n"
            f"  CERT_PATH: {CERT_PATH}\n"
            "Update BASE_URL to https:// in the config file.",
            file=sys.stderr,
        )
        sys.exit(1)
    if CERT_PATH and not os.path.exists(CERT_PATH):
        print(
            f"Error: certificate file not found: {CERT_PATH}\n"
            "Check the CERT_PATH setting in the config file.\n"
            "Run 'lock_client.py setup-tls' to pin the ESP32 certificate.",
            file=sys.stderr,
        )
        sys.exit(1)


def _get(path: str) -> dict:
    _check_cert_path()
    verify = CERT_PATH or True
    for attempt in range(1 + _REQUEST_RETRIES):
        delay = _REQUEST_RETRY_DELAY
        try:
            r = _session.get(f"{BASE_URL}{path}", timeout=5, verify=verify)
        except requests.exceptions.SSLError as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e) or "certificate verify failed" in str(e).lower():
                _ssl_cert_error(e)  # exits
            # Other SSL error (e.g. handshake dropped due to ESP32 memory pressure) — retry
        except requests.exceptions.ConnectionError:
            delay = _REQUEST_RECONNECT_DELAY
        except requests.exceptions.Timeout:
            pass
        else:
            try:
                return r.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                print(
                    f"Error: ESP32 at {BASE_URL} returned an unexpected response "
                    f"(HTTP {r.status_code}). Is it running the correct firmware?",
                    file=sys.stderr,
                )
                sys.exit(1)
        if attempt < _REQUEST_RETRIES:
            time.sleep(delay)
    print(
        f"Error: Cannot reach ESP32 at {BASE_URL}. "
        "Check it is powered on and BASE_URL is correct in the config file.",
        file=sys.stderr,
    )
    sys.exit(1)


def _try_status() -> dict | None:
    """Best-effort one-shot GET /status. Returns None on any failure.

    Used by _post for recovery messages when retries are exhausted — never
    retries, never exits.
    """
    try:
        verify = CERT_PATH or True
        r = _session.get(f"{BASE_URL}/status", timeout=5, verify=verify)
        return r.json()
    except Exception:
        return None


def _post(path: str, body: dict) -> dict:
    _check_cert_path()
    verify = CERT_PATH or True
    retries = _REQUEST_RETRIES_PASS_LOCK if path == "/pass-lock" else _REQUEST_RETRIES
    for attempt in range(1 + retries):
        delay = _REQUEST_RETRY_DELAY
        try:
            r = _session.post(
                f"{BASE_URL}{path}",
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=5,
                verify=verify,
            )
        except requests.exceptions.SSLError as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e) or "certificate verify failed" in str(e).lower():
                _ssl_cert_error(e)  # exits
            # Other SSL error — retry
        except requests.exceptions.ConnectionError:
            delay = _REQUEST_RECONNECT_DELAY
        except requests.exceptions.Timeout:
            pass
        else:
            try:
                return r.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                print(
                    f"Error: ESP32 at {BASE_URL} returned an unexpected response "
                    f"(HTTP {r.status_code}). Is it running the correct firmware?",
                    file=sys.stderr,
                )
                sys.exit(1)
        if attempt < retries:
            time.sleep(delay)

    # All retries exhausted. For /pass-lock, the request body's nonce may have
    # actually engaged the lock on the ESP32 even though every response was
    # dropped — and the generated password is in ESP32 RAM only, unrecoverable.
    # Probe /status to detect this so the user is told the truth instead of a
    # misleading "cannot reach" message.
    if path == "/pass-lock":
        status = _try_status()
        if status and status.get("pass_locked"):
            lock_max = status.get("lock_seconds_max")
            lock_max_str = format_seconds(lock_max) if lock_max else "unknown"
            print(
                f"Error: ESP32 is now pass-locked but the /pass-lock response was lost in transit.\n"
                f"  If this command engaged the lock, the generated password is unrecoverable\n"
                f"  (stored only in the ESP32's RAM). The lock will release in up to {lock_max_str}.",
                file=sys.stderr,
            )
            sys.exit(1)

    print(
        f"Error: Cannot reach ESP32 at {BASE_URL}. "
        "Check it is powered on and BASE_URL is correct in the config file.",
        file=sys.stderr,
    )
    sys.exit(1)

# ── Verification ───────────────────────────────────────────────────────────────

def _verify_timer(label: str, requested: int, actual: int | None) -> bool:
    """Return True if actual is within [requested - VERIFY_TOLERANCE_SECS, requested]."""
    if actual is None:
        print(
            f"Error: {label} is None — lock may have expired before verification.",
            file=sys.stderr,
        )
        return False
    if actual > requested or actual < requested - VERIFY_TOLERANCE_SECS:
        print(
            f"Error: {label} mismatch — requested {requested}s, ESP32 reports {actual}s.",
            file=sys.stderr,
        )
        return False
    return True

# ── Human-readable time formatting ─────────────────────────────────────────────

def format_seconds(secs: int) -> str:
    """Format an integer number of seconds as e.g. '1h30m45s'."""
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    result = ""
    if h > 0:
        result += f"{h}h"
    if m > 0:
        result += f"{m}m"
    if s > 0 or (h == 0 and m == 0):
        result += f"{s}s"
    return result


def opens_at(remaining: int) -> str:
    """Return the wall-clock time when 'remaining' seconds have elapsed.

    Returns 'HH:MM' for durations under 24 h.
    Returns 'Fri 29 May, 14:32' (weekday + date + time) for 24 h or more.
    """
    target = time.time() + remaining
    dt = datetime.datetime.fromtimestamp(target)
    if remaining >= 86400:
        return dt.strftime("%a %d %b, %H:%M")
    return dt.strftime("%H:%M")

# ── Public API functions ───────────────────────────────────────────────────────

def api_status(password: str | None = None) -> dict:
    """GET /status — return current lock state dict.

    If password is given, the ESP32 validates it and includes
    'password_valid': bool in the response (only when pass-locked).
    """
    path = f"/status?password={password}" if password is not None else "/status"
    return _get(path)


def api_lock(lock_seconds: int | None = None, random_lock: bool = False) -> dict:
    """POST /lock — lock the bolt, optionally with a countdown timer."""
    body: dict = {
        "nonce": os.urandom(8).hex(),  # dedup: safe to retry without double-locking
    }
    if lock_seconds is not None:
        body["lock_seconds"] = lock_seconds
    if random_lock:
        body["random_lock"] = True
    raw = _post("/lock", body)
    if "error" in raw:
        print(f"Error: {raw['error']}", file=sys.stderr)
        sys.exit(1)
    return raw


def api_unlock() -> dict:
    """POST /unlock — unlock the bolt (fails if pass-locked)."""
    raw = _post("/unlock", {})
    if "error" in raw:
        print(f"Error: {raw['error']}", file=sys.stderr)
        sys.exit(1)
    return raw


def api_pass_lock(lock_seconds: int, lock_seconds_max: int, random_lock: bool = False) -> dict:
    """POST /pass-lock — engage password-protected lock.

    Returns dict containing 'password' (treat as a secret; printed once).
    """
    body: dict = {
        "lock_seconds": lock_seconds,
        "lock_seconds_max": lock_seconds_max,
        "nonce": os.urandom(8).hex(),  # dedup: safe to retry without double-locking
    }
    if random_lock:
        body["random_lock"] = True
    raw = _post("/pass-lock", body)
    if "error" in raw:
        print(f"Error: {raw['error']}", file=sys.stderr)
        sys.exit(1)
    return raw


def api_pass_unlock(password: str) -> dict:
    """POST /pass-unlock — disarm password-protected lock."""
    raw = _post("/pass-unlock", {
        "password": password,
        "nonce": os.urandom(8).hex(),  # dedup: safe to retry without false-failure
    })
    if "error" in raw:
        # "not pass-locked" can be a true success on a retry-after-success when
        # the ESP32's nonce cache has evicted the entry or rebooted (the original
        # request DID unlock, but the cache miss makes the retry look like a
        # fresh call to an already-open lock). Probe /status to disambiguate.
        if raw["error"] == "not pass-locked":
            status = api_status()
            if not status.get("locked"):
                return {"locked": False, "pass_locked": False}
        print(f"Error: {raw['error']}", file=sys.stderr)
        sys.exit(1)
    return raw


def api_pass_adjust(password: str, adjust_seconds: int) -> dict:
    """POST /adjust-timer — add or subtract seconds from the countdown."""
    raw = _post("/adjust-timer", {
        "password": password,
        "adjust_seconds": adjust_seconds,
        "nonce": os.urandom(8).hex(),  # dedup: safe to retry without double-adjusting
    })
    if "error" in raw:
        print(f"Error: {raw['error']}", file=sys.stderr)
        sys.exit(1)
    return raw

# ── CLI subcommand implementations ─────────────────────────────────────────────

def cmd_status(password: str | None = None) -> None:
    raw = api_status(password)
    locked = raw.get("locked")
    pass_locked = raw.get("pass_locked")
    remaining = raw.get("remaining_seconds")
    lock_seconds_max = raw.get("lock_seconds_max")

    if not locked:
        print("Locked:    no")
        return

    print("Locked:    yes")

    if pass_locked:
        print("Secured:   yes (pass-locked)")
    else:
        print("Secured:   no")

    if raw.get("random_blind"):
        print("Remaining: X (hidden during blind period)")
    elif remaining is not None:
        print(f"Remaining: {format_seconds(remaining)}")
        print(f"Opens at:  {opens_at(remaining)}")

    if lock_seconds_max is not None:
        print(
            f"Max lock:  {format_seconds(lock_seconds_max)} remaining "
            f"(opens at {opens_at(lock_seconds_max)} at latest)"
        )

    if password is not None and pass_locked:
        valid = raw.get("password_valid")
        if valid is True:
            print("Password:  correct")
        elif valid is False:
            print("Password:  wrong")


def cmd_lock(duration_str: str | None, use_random: bool = False) -> None:
    secs: int | None = None
    if duration_str:
        try:
            secs = parse_duration(duration_str)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)

    pre_status = _get("/status")
    if pre_status.get("locked"):
        print("Error: Lock is already engaged.", file=sys.stderr)
        sys.exit(1)

    if secs is not None and secs >= 86400:
        if use_random:
            min_secs = int(secs * RANDOM_START_PERCENT / 100)
            print(
                f"WARNING: You are about to lock for up to {format_seconds(secs)} "
                f"(random mode: actual duration will be between "
                f"{format_seconds(min_secs)} and {format_seconds(secs)}). "
                "If this is not intentional, STOP LOCKING YOURSELF UP NOW."
            )
        else:
            print(
                f"WARNING: You are about to lock for {format_seconds(secs)}. "
                "If this is not intentional, STOP LOCKING YOURSELF UP NOW."
            )

    if use_random:
        if secs is None:
            print("Error: 'random' requires a duration argument.", file=sys.stderr)
            sys.exit(1)
        secs = _random_lock_secs(secs)

    raw = api_lock(lock_seconds=secs, random_lock=use_random)

    if secs is not None:
        status_raw = _get("/status")
        if status_raw.get("random_blind"):
            print("Locked (time hidden during blind period).")
        else:
            status_remaining = status_raw.get("remaining_seconds")
            if not _verify_timer("lock_seconds", secs, status_remaining):
                print("Unlocking due to verification failure.", file=sys.stderr)
                rollback = _post("/unlock", {})
                if rollback.get("error"):
                    print(f"Warning: rollback unlock also failed: {rollback['error']}", file=sys.stderr)
                sys.exit(1)
            print(f"Locked. Opens in {format_seconds(status_remaining)} (at {opens_at(status_remaining)}).")
    else:
        print("THINK TWICE: You didn't specify an unlock time — magnet will stay locked until you run 'unlock'.", file=sys.stderr)
        print("Locked.")


def cmd_unlock() -> None:
    status = _get("/status")
    if not status.get("locked"):
        print("Already unlocked.")
        return

    raw = _post("/unlock", {})
    error = raw.get("error")
    if error:
        if "pass_locked" in error:
            print(
                "Error: Lock is pass-locked. Use 'pass-unlock <password>' to unlock.",
                file=sys.stderr,
            )
        else:
            print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    print("Unlocked.")


def cmd_pass_lock(initial_str: str | None, max_str: str | None, use_random: bool = False) -> None:
    if not initial_str or not max_str:
        print("Usage: pass-lock <initial-duration> <max-duration> [random]", file=sys.stderr)
        sys.exit(1)

    try:
        initial_secs = parse_duration(initial_str)
        max_secs = parse_duration(max_str)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if initial_secs == 0:
        print("Error: Initial lock duration cannot be zero.", file=sys.stderr)
        sys.exit(1)
    if max_secs == 0:
        print("Error: Maximum lock duration cannot be zero.", file=sys.stderr)
        sys.exit(1)
    if initial_secs > max_secs:
        print(
            f"Error: Initial duration ({format_seconds(initial_secs)}) "
            f"exceeds maximum ({format_seconds(max_secs)}).",
            file=sys.stderr,
        )
        sys.exit(1)

    if max_secs >= 86400:
        if use_random:
            span = max_secs - initial_secs
            min_initial = initial_secs + int(span * RANDOM_START_PERCENT / 100)
            print(
                f"WARNING: You are about to set a pass-lock of up to {format_seconds(max_secs)} "
                f"(random mode: actual initial duration will be between "
                f"{format_seconds(min_initial)} and {format_seconds(max_secs)}).\n"
                "WARNING: If this is not intentional, STOP LOCKING YOURSELF UP NOW."
            )
        else:
            print(
                f"WARNING: You are about to set a pass-lock of up to {format_seconds(max_secs)} "
                f"(initial: {format_seconds(initial_secs)}).\n"
                "WARNING: If this is not intentional, STOP LOCKING YOURSELF UP NOW."
            )

    pre_status = _get("/status")
    if pre_status.get("locked"):
        print("Error: Lock is already engaged. Unlock it first.", file=sys.stderr)
        sys.exit(1)

    if use_random:
        initial_secs = _random_pass_lock_secs(initial_secs, max_secs)

    raw = api_pass_lock(initial_secs, max_secs, random_lock=use_random)
    password = raw["password"]
    print(f"Password: {password}")

    status_raw = _get("/status")
    status_max = status_raw.get("lock_seconds_max")

    if not _verify_timer("lock_seconds_max", max_secs, status_max):
        print("Unlocking due to verification failure.", file=sys.stderr)
        rollback = _post("/pass-unlock", {
            "password": password,
            "nonce": os.urandom(8).hex(),
        })
        if rollback.get("error"):
            print(f"Warning: rollback unlock also failed: {rollback['error']}", file=sys.stderr)
            print(
                f"Lock is still engaged. Use 'pass-unlock {password}' to release it.",
                file=sys.stderr,
            )
        sys.exit(1)

    if status_raw.get("random_blind"):
        print("Pass-locked (time hidden during blind period).")
        print(f"Max lock:  {format_seconds(status_max)} (opens at {opens_at(status_max)} at latest).")
    else:
        status_remaining = status_raw.get("remaining_seconds")
        if not _verify_timer("lock_seconds", initial_secs, status_remaining):
            print("Unlocking due to verification failure.", file=sys.stderr)
            rollback = _post("/pass-unlock", {
                "password": password,
                "nonce": os.urandom(8).hex(),
            })
            if rollback.get("error"):
                print(f"Warning: rollback unlock also failed: {rollback['error']}", file=sys.stderr)
                print(
                    f"Lock is still engaged. Use 'pass-unlock {password}' to release it.",
                    file=sys.stderr,
                )
            sys.exit(1)
        print("Pass-locked.")
        print(f"Remaining: {format_seconds(status_remaining)} (opens at {opens_at(status_remaining)}).")
        print(f"Max lock:  {format_seconds(status_max)} (opens at {opens_at(status_max)} at latest).")

def cmd_pass_unlock(password: str) -> None:
    if not password:
        print("Usage: pass-unlock <password>", file=sys.stderr)
        sys.exit(1)

    raw = _post("/pass-unlock", {
        "password": password,
        "nonce": os.urandom(8).hex(),  # dedup: safe to retry without false-failure
    })
    error = raw.get("error")
    if error:
        if error == "not pass-locked":
            status = _get("/status")
            if not status.get("locked"):
                print("Already unlocked.")
                return
            print("Error: No pass-lock is active. Use 'unlock' to unlock.", file=sys.stderr)
        elif error == "wrong password":
            print("Error: wrong password", file=sys.stderr)
        else:
            print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    print("Unlocked and pass-lock cleared.")


def cmd_pass_adjust(delta_str: str, password: str) -> None:
    if not delta_str or not password:
        print("Usage: pass-adjust <+/-duration> <password>", file=sys.stderr)
        sys.exit(1)

    arg = delta_str
    sign = 1

    # Reject zero before sign detection so we never suggest +0/-0 as valid alternatives.
    _bare = arg[1:] if arg[:1] in "+-" else arg
    if not re.match(r'^\d{1,2}:\d{2}$', _bare):
        try:
            if parse_duration(_bare) == 0:
                print(
                    "Error: zero is not a valid adjustment. "
                    "To unlock immediately, use 'pass-unlock <password>'.",
                    file=sys.stderr,
                )
                sys.exit(1)
        except ValueError:
            pass

    if arg.startswith("+"):
        arg = arg[1:]
    elif arg.startswith("-"):
        sign = -1
        arg = arg[1:]
        if re.match(r'^\d{1,2}:\d{2}$', arg):
            print(
                "Error: '-' sign is not valid with an absolute clock time. "
                "Use '+HH:MM' or bare 'HH:MM' instead.",
                file=sys.stderr,
            )
            sys.exit(1)
    elif re.match(r'^\d{1,2}:\d{2}$', arg):
        pass  # clock time — positive, no sign required
    else:
        print(
            f"Error: duration requires a '+' or '-' sign "
            f"(e.g. +{arg} to add, -{arg} to subtract).",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        secs = parse_duration(arg)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    pre_status = _get("/status")
    pre_remaining = pre_status.get("remaining_seconds")

    if re.match(r'^\d{1,2}:\d{2}$', arg):
        if pre_remaining is None:
            print("Error: Cannot use absolute time adjustment during the blind period.", file=sys.stderr)
            sys.exit(1)
        adjust = secs - pre_remaining
    else:
        adjust = sign * secs

    raw = _post("/adjust-timer", {
        "password": password,
        "adjust_seconds": adjust,
        "nonce": os.urandom(8).hex(),  # dedup: safe to retry without double-adjusting
    })
    error = raw.get("error")
    if error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    remaining = raw["remaining_seconds"]
    lock_seconds_max = raw["lock_seconds_max"]
    clamped = raw.get("clamped", False)

    if lock_seconds_max is None:
        # Timer hit zero — lock has opened.
        print("Lock opened.")
    else:
        if remaining is None:
            print("Timer adjusted (time hidden during blind period).")
        else:
            print("Timer adjusted.")
            print(f"Remaining: {format_seconds(remaining)} (opens at {opens_at(remaining)}).")
        print(f"Max lock:  {format_seconds(lock_seconds_max)} (opens at {opens_at(lock_seconds_max)} at latest).")
        if clamped:
            print("Note: timer was clamped to the maximum.")

def cmd_setup_tls() -> None:
    """TOFU bootstrap: fetch and pin the ESP32's TLS certificate.

    Connects once without certificate verification, saves the certificate locally,
    and prints the CERT_PATH line to add to the config file.  Run this once after
    provisioning the ESP32 with setup-tls.sh, on every machine that uses
    lock_client.py.
    """
    import ssl as pyssl
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(BASE_URL)
    if parsed.scheme != "https":
        print(
            f"Error: BASE_URL is not https ({BASE_URL!r}).\n"
            "setup-tls requires BASE_URL to use https://.",
            file=sys.stderr,
        )
        sys.exit(1)

    host = parsed.hostname
    port = parsed.port or 443

    # Default save location: next to this script.
    cert_path = CERT_PATH or os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "esp32_cert.pem"
    )

    print(f"Fetching certificate from {host}:{port} (unverified, one-time setup)...")

    ctx = pyssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = pyssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=5) as raw_sock:
            with ctx.wrap_socket(raw_sock, server_hostname=host) as tls_sock:
                der_cert = tls_sock.getpeercert(binary_form=True)
    except OSError as e:
        print(f"Error: Cannot reach ESP32 at {host}:{port}: {e}", file=sys.stderr)
        sys.exit(1)

    pem_cert = pyssl.DER_cert_to_PEM_cert(der_cert)

    # Warn if a different certificate is already pinned.
    if os.path.exists(cert_path):
        with open(cert_path) as f:
            existing = f.read().strip()
        if existing == pem_cert.strip():
            print(f"Certificate already pinned at {cert_path} — no change.")
            return
        print(f"Warning: a different certificate is already saved at {cert_path}.")
        print("This could mean the ESP32 was re-provisioned, or a MITM is present.")
        try:
            confirm = input("Replace the pinned certificate? [y/N] ").strip().lower()
        except EOFError:
            confirm = ""
        if confirm != "y":
            print("Aborted.")
            sys.exit(1)

    with open(cert_path, "w") as f:
        f.write(pem_cert)

    print(f"Certificate pinned to {cert_path}.")
    if not CERT_PATH:
        cfg_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config")
        print(f"\nAdd this line to {cfg_path}:")
        print(f"  CERT_PATH={cert_path}")


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else ""

    match cmd:
        case "setup-tls":
            cmd_setup_tls()
        case "status":
            cmd_status(args[1] if len(args) > 1 else None)
        case "lock":
            lock_args = args[1:]
            use_random = bool(lock_args) and lock_args[-1] == "random"
            if use_random:
                lock_args = lock_args[:-1]
            cmd_lock(lock_args[0] if lock_args else None, use_random)
        case "unlock":
            cmd_unlock()
        case "pass-lock":
            pl_args = args[1:]
            use_random = bool(pl_args) and pl_args[-1] == "random"
            if use_random:
                pl_args = pl_args[:-1]
            cmd_pass_lock(
                pl_args[0] if len(pl_args) > 0 else None,
                pl_args[1] if len(pl_args) > 1 else None,
                use_random,
            )
        case "pass-unlock":
            cmd_pass_unlock(args[1] if len(args) > 1 else "")
        case "pass-adjust":
            cmd_pass_adjust(
                args[1] if len(args) > 1 else "",
                args[2] if len(args) > 2 else "",
            )
        case _:
            print(f"Usage: {sys.argv[0]} <subcommand> [args]", file=sys.stderr)
            print("", file=sys.stderr)
            print("Subcommands:", file=sys.stderr)
            print("  setup-tls                                     pin the ESP32 TLS certificate", file=sys.stderr)
            print("                                                (run once after setup-tls.sh on every new client)", file=sys.stderr)
            print("  status [password]                             show current lock state; optionally verify password", file=sys.stderr)
            print("  lock [duration] [random]                      engage bolt; optional timed auto-unlock", file=sys.stderr)
            print("  unlock                                        release bolt (fails if pass-locked)", file=sys.stderr)
            print("  pass-lock <initial-duration> <max-duration> [random]", file=sys.stderr)
            print("                                                password-protected lock with countdown", file=sys.stderr)
            print("  pass-unlock <password>                        disarm password-protected lock", file=sys.stderr)
            print("  pass-adjust <+/-duration> <password>          add or subtract time from countdown", file=sys.stderr)
            print("", file=sys.stderr)
            print("Duration formats:", file=sys.stderr)
            print("  relative duration                             90 / 90m / 1h / 1h30m / 1h30m45s", file=sys.stderr)
            print("  absolute local time (next occurrence)         20:30", file=sys.stderr)
            print("  pass-adjust prefix: +1h / -30m / +20:30", file=sys.stderr)
            print("", file=sys.stderr)
            cfg_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config")
            print(f"Config (edit {cfg_path}):", file=sys.stderr)
            print(f"  BASE_URL              {BASE_URL}  — full base URL", file=sys.stderr)
            print(f"  CERT_PATH             {CERT_PATH or '(not set)'}  — path to pinned ESP32 TLS certificate", file=sys.stderr)
            print(f"  VERIFY_TOLERANCE_SECS {VERIFY_TOLERANCE_SECS}  — allowed drift when verifying timer after lock", file=sys.stderr)
            print(f"  RANDOM_START_PERCENT  {RANDOM_START_PERCENT}  — earliest point random mode can pick, % of window", file=sys.stderr)
            sys.exit(1)
