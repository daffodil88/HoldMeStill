# lock_client.py — Command & API Reference

This document is a complete reference for `pi/lock_client.py`, a Python 3.10+
client that controls a magnetic lock on an ESP32 microcontroller over HTTP or HTTPS.
It covers every CLI command and every importable Python function, with examples.

**Quick links:**
- [Setup & TLS](#setup) — `pi/config` keys and one-time TLS certificate provisioning/pinning
- [Duration format](#duration-format) — accepted formats: bare seconds, `Nm`/`Nh`, `HH:MM`, and combinations
- [CLI commands](#cli-usage) — all subcommands (`lock`, `unlock`, `pass-lock`, etc.) with examples
- [Python API](#python-module-api) — importable `api_*` functions and utility helpers
- [Blind period](#blind-period) — how the random-lock countdown is hidden until the blind window expires
- [State machine](#lock-state-machine) — state transitions between UNLOCKED, LOCKED, and PASS-LOCKED
- [Error handling](#error-handling-notes) — retry policy, post-action verification, rollback, and TLS errors

---

## Setup

**File:** `pi/lock_client.py`  
**Dependencies:** Python standard library + `requests`

**Configuration** (edit `pi/config`):

| Key | Meaning |
|---|---|
| `BASE_URL` | Full base URL for ESP32 requests — IP or hostname, must use `https://` (e.g. `https://192.168.1.88` or `https://esp32.local`) |
| `CERT_PATH` | Absolute path to the pinned ESP32 TLS certificate; empty = TLS verification fails (required for self-signed certs) |
| `VERIFY_TOLERANCE_SECS` | Allowed clock-drift (seconds) when verifying timer after `lock` or `pass-lock` |
| `RANDOM_START_PERCENT` | Earliest point (% of window) the `random` mode can pick |

**Config file location.** The default path is `pi/config` (next to the script).
Override with either:

- CLI flag `-c <path>` / `--config <path>` / `--config=<path>` — applies to a single invocation.
- Environment variable `LOCK_CLIENT_CONFIG=<path>` — applies to every invocation in the
  environment, including processes that import `lock_client` as a module (e.g. `game_server.py`).

Precedence is CLI flag > env var > default. If `-c` points at a missing file the
client exits with `Error: config file not found: <path>`.

### TLS Setup

The ESP32 serves HTTPS once provisioned with a self-signed certificate. The
client verifies every connection against a locally pinned copy of that
certificate. The private key never leaves the ESP32.

**One-time provisioning** (run from the repo root with USB access to the ESP32):

```
./setup-tls.sh
```

This generates a P-256 EC key and certificate, uploads both to the ESP32 via
`mpremote`, deletes the private key from the local machine, and keeps `esp32_cert.pem`
locally. After running it, restart the ESP32 so it picks up the new files and
switches to HTTPS on port 443.

**Pin the certificate** on each machine that uses `lock_client.py`:

```
pi/lock_client.py setup-tls
```

This performs a one-time unverified connection (TOFU), saves the certificate,
and prints the `CERT_PATH` line to add to `pi/config`. Once `CERT_PATH` is set,
all connections verify against the pinned certificate and reject any mismatch.

The machine that ran `setup-tls.sh` already has `esp32_cert.pem` and can set
`CERT_PATH` directly without running `setup-tls`.

---

## Duration Format

Everywhere a duration is accepted (CLI arguments and Python calls), the following
formats are valid:

| Format | Meaning | Example |
|---|---|---|
| bare integer | seconds | `90` → 90 s |
| `Ns` | seconds | `90s` → 90 s |
| `Nm` | minutes | `30m` → 1800 s |
| `Nh` | hours | `2h` → 7200 s |
| `NhNm` | hours + minutes | `1h30m` → 5400 s |
| `NhNmNs` | hours + minutes + seconds | `1h30m45s` → 5445 s |
| `HH:MM` | absolute local wall-clock time | `20:30` → seconds until 20:30 today (or tomorrow if that time has already passed) |

For `pass-adjust`, relative durations must be prefixed with `+` or `-` to
indicate direction (e.g. `+30m`, `-1h`).

A `HH:MM` clock time is the exception: no sign is needed (or accepted) —
it means "set the timer so the lock opens at exactly HH:MM", and the
client computes the required delta automatically.

---

## CLI Usage

```
pi/lock_client.py <subcommand> [args]
```

### `setup-tls`

Pin the ESP32's [TLS certificate](#tls-setup) using Trust On First Use (TOFU). Run this once
on each machine after the ESP32 has been provisioned with `setup-tls.sh` and
restarted.

```
pi/lock_client.py setup-tls
```

Connects to the ESP32 at `BASE_URL` without certificate verification, saves the
certificate to the path specified by `CERT_PATH` (or `esp32_cert.pem` next to
`lock_client.py` if `CERT_PATH` is not yet set), and prints the config line to
add.

If a certificate is already saved at the target path:
- If it matches the ESP32's current certificate: reports no change and exits cleanly.
- If it differs: warns that the certificate has changed (possible re-provisioning
  or MITM) and asks for confirmation before overwriting.

**Requires** `BASE_URL` to use `https://`. Exits with an error if it does not.

**Output:**
```
Fetching certificate from 192.168.1.88:443 (unverified, one-time setup)...
Certificate pinned to /home/user/holdmestill/pi/esp32_cert.pem.

Add this line to /home/user/holdmestill/pi/config:
  CERT_PATH=/home/user/holdmestill/pi/esp32_cert.pem
```

---

### `status [password]`

Print a human-readable summary of the current lock state.
Optionally supply the one-time password to verify it without unlocking.

```
pi/lock_client.py status
pi/lock_client.py status a3f8c2...
```

**Output when unlocked:**
```
Locked:    no
```

**Output when locked with a timer and pass-lock active:**
```
Locked:    yes
Secured:   yes (pass-locked)
Remaining: 1h39m
Opens at:  20:30
Max lock:  3h00m remaining (opens at 23:00 at latest)
```

**Output when locked without pass-lock:**
```
Locked:    yes
Secured:   no
Remaining: 5m30s
Opens at:  14:22
```

**Output when pass-locked and password is supplied:**
```
Locked:    yes
Secured:   yes (pass-locked)
Remaining: 1h39m
Opens at:  20:30
Max lock:  3h00m remaining (opens at 23:00 at latest)
Password:  correct
```

or, with a wrong password:
```
Password:  wrong
```

**Output when the blind period is active:**
```
Locked:    yes
Secured:   yes (pass-locked)
Remaining: X (hidden during blind period)
Max lock:  3h00m remaining (opens at 23:00 at latest)
```

Fields:
- `Locked` — whether the physical bolt is engaged
- `Secured` — whether a password is required to unlock
- `Remaining` — countdown until the lock auto-opens (absent if no timer; `X` during the blind period)
- `Opens at` — wall-clock time when the lock will open (omitted during the blind period)
- `Max lock` — the hard ceiling on remaining time and its wall-clock deadline (always shown when set)
- `Password` — only shown when the lock is pass-locked and a password argument was given; `correct` or `wrong`
- `random_blind` — `true` in the raw response while the blind period is active

**Notes:**
- The password check has no side effects — it does not unlock or modify state.
- If the lock is not pass-locked the `Password` line is always omitted, even if a password argument is supplied.

---

### `lock [duration] [random]`

Engage the bolt without a password. Can be unlocked without a password too. Fails if the lock is already engaged.

```
pi/lock_client.py lock                # lock indefinitely
pi/lock_client.py lock 90             # lock for 90 seconds
pi/lock_client.py lock 30m            # lock for 30 minutes
pi/lock_client.py lock 1h30m          # lock for 1 h 30 m
pi/lock_client.py lock 20:30          # lock until 20:30 local time
pi/lock_client.py lock 1h random      # lock for a random time in [50%, 100%] of 1h
```

- Without a duration: bolt stays locked until `unlock` is called.
- With a duration: bolt unlocks automatically when the countdown reaches 0.
  Can still be unlocked early with `unlock`.
- `random` suffix: picks a random duration between `RANDOM_START_PERCENT`% and
  100% of the specified duration, then activates the blind period
  (see [Blind Period](#blind-period)). The chosen duration is not printed.

**Success output:**
```
Locked. Opens in 1h30m (at 20:30).
```

If the duration is 24 h or more, a warning is printed to stdout before engaging the lock.
```
WARNING: You are about to lock for 48h. If this is not intentional, STOP LOCKING YOURSELF UP NOW.
```
or (random mode):
```
WARNING: You are about to lock for up to 48h (random mode: actual duration will be between 24h and 48h). If this is not intentional, STOP LOCKING YOURSELF UP NOW.
```

or (no timer):
```
THINK TWICE: You didn't specify an unlock time — magnet will stay locked until you run 'unlock'.
Locked.
```
or (random mode — blind period active):
```
Locked (time hidden during blind period).
```

**Error output (stderr, exit 1):**
```
Error: Lock is already engaged.
```

---

### `unlock`

Release the bolt. Fails if pass-lock is active (use `pass-unlock` instead).

```
pi/lock_client.py unlock
```

**Success output:**
```
Unlocked.
```
or, if the lock was already open:
```
Already unlocked.
```

**Error output (stderr, exit 1):**
```
Error: Lock is pass-locked. Use 'pass-unlock <password>' to unlock.
```

---

### `pass-lock <initial-duration> <max-duration> [random]`

Engage a password-protected lock with a countdown and a hard time ceiling.
The ESP32 generates a one-time password and returns it once — it is printed
to stdout. **Store the password immediately; it cannot be retrieved again.**

Both durations are required. `initial-duration` must be ≤ `max-duration`.

```
pi/lock_client.py pass-lock 2m 10m            # lock 2 min, max 10 min
pi/lock_client.py pass-lock 1h 3h             # lock 1 h, max 3 h
pi/lock_client.py pass-lock 20:30 23:00       # until 20:30, hard ceiling 23:00
pi/lock_client.py pass-lock 1h 3h random      # random initial in [50%, 100%] of span
```

- `initial-duration`: the countdown the lock starts with.
- `max-duration`: the hard ceiling — `remaining_seconds` can never exceed this,
  regardless of how many `pass-adjust` calls add time.
- `random` suffix: picks a random initial duration between `initial + RANDOM_START_PERCENT% * (max - initial)` and `max`, then activates the blind period
  (see [Blind Period](#blind-period)). The chosen duration is not printed.

**Success output:**
```
Password: a3f8...  (64-character hex string)
Pass-locked.
Remaining: 2m0s (opens at 14:32).
Max lock:  10m0s (opens at 14:40 at latest).
```
or (random mode — blind period active):
```
Password: a3f8...  (64-character hex string)
Pass-locked (time hidden during blind period).
Max lock:  10m0s (opens at 14:40 at latest).
```

The password is printed before any verification or status output so that
a transient network failure between locking and verification cannot leave
the user trapped without their recovery key. If verification then fails
and the rollback unlock succeeds, the printed password is harmless; if the
rollback also fails, the user can retry `pass-unlock` with it.

If the maximum duration is 24 h or more, a warning is printed to stdout before engaging the lock.
```
WARNING: You are about to set a pass-lock of up to 168h (initial: 48h).
WARNING: If this is not intentional, STOP LOCKING YOURSELF UP NOW.
```
or (random mode):
```
WARNING: You are about to set a pass-lock of up to 48h (random mode: actual initial duration will be between 36h and 48h).
WARNING: If this is not intentional, STOP LOCKING YOURSELF UP NOW.
```

**Error output (stderr, exit 1):**
```
Error: Lock is already engaged. Unlock it first.
Error: Initial duration (11m) exceeds maximum (10m).
Error: Initial lock duration cannot be zero.
```

---

### `pass-unlock <password>`

Disarm the password-protected lock. Clears all lock state.
The password is the 64-character hex string printed by `pass-lock`.

```
pi/lock_client.py pass-unlock a3f8c2...
```

**Success output:**
```
Unlocked and pass-lock cleared.
```
or, if the lock was already open:
```
Already unlocked.
```

**Error output (stderr, exit 1):**
```
Error: wrong password
Error: No pass-lock is active. Use 'unlock' to unlock.
```
The second error is only shown when the lock is locked but not pass-locked.
If the lock is already fully unlocked, `Already unlocked.` is printed and
the command exits 0.

---

### `pass-adjust <+/-duration> <password>`

Add or subtract time from the active countdown. Requires the one-time password.
The timer cannot be pushed above the configured maximum or below zero.

```
pi/lock_client.py pass-adjust +30m  a3f8c2...   # add 30 minutes
pi/lock_client.py pass-adjust -10m  a3f8c2...   # subtract 10 minutes
pi/lock_client.py pass-adjust +1h   a3f8c2...   # add 1 hour (clamped if over max)
pi/lock_client.py pass-adjust +20:30 a3f8c2...  # set timer so lock opens at 20:30
```

- The `+`/`-` sign is required for relative durations.
- A `HH:MM` clock time means "adjust so that the lock opens at exactly this
  time". Both bare `HH:MM` and `+HH:MM` are accepted; `-HH:MM` is rejected
  with an error. The client computes the difference from the current remaining
  time. **Not allowed during the blind period** — the client cannot compute the
  delta without knowing `remaining_seconds`.
- If the adjustment would exceed `lock_seconds_max`, the timer is clamped to
  `lock_seconds_max` and a note is printed.
- If the adjustment would go below 0, the timer hits 0 and the lock opens
  immediately.

**Success output:**
```
Timer adjusted.
Remaining: 1h30m (opens at 20:30).
Max lock:  3h0m (opens at 23:00 at latest).
```

**With clamping:**
```
Timer adjusted.
Remaining: 3h0m (opens at 23:00).
Max lock:  3h0m (opens at 23:00 at latest).
Note: timer was clamped to the maximum.
```

**During the blind period (relative `+/-` adjustments succeed; remaining time stays hidden):**
```
Timer adjusted (time hidden during blind period).
Max lock:  3h0m (opens at 23:00 at latest).
```

**When the adjustment drops the timer to zero (lock opens immediately):**
```
Lock opened.
```

**Error output (stderr, exit 1):**
```
Error: wrong password
Error: duration requires a '+' or '-' sign (e.g. +30m to add, -30m to subtract).
Error: '-' sign is not valid with an absolute clock time. Use '+HH:MM' or bare 'HH:MM' instead.
Error: Cannot use absolute time adjustment during the blind period.
```

---

## Python Module API

`lock_client.py` can be imported as a module. All `api_*` functions call
`sys.exit(1)` after printing to stderr on any error. There is no exception-based
error path — the recommended pattern for a game server is to treat a non-zero
exit as fatal and let the process crash (or wrap the import in a subprocess if
isolation is required).

```python
from lock_client import (
    api_status,
    api_lock,
    api_unlock,
    api_pass_lock,
    api_pass_unlock,
    api_pass_adjust,
    parse_duration,
    format_seconds,
    opens_at,
)
```

---

### `api_status(password: str | None = None) -> dict`

Returns the current lock state. If `password` is given, the ESP32 validates
it in the same request and includes `password_valid` in the response.

```python
state = api_status()
# {
#   "locked": True,
#   "pass_locked": True,
#   "remaining_seconds": 5400,
#   "lock_seconds_max": 10800
# }

state = api_status("a3f8c2...")
# {
#   "locked": True,
#   "pass_locked": True,
#   "remaining_seconds": 5400,
#   "lock_seconds_max": 10800,
#   "password_valid": True
# }
```

Fields:

| Key | Type | Description |
|---|---|---|
| `locked` | `bool` | Whether the bolt is currently engaged |
| `pass_locked` | `bool` | Whether password enforcement is active |
| `remaining_seconds` | `int \| None` | Seconds until auto-unlock; `None` if no timer or during the blind period |
| `lock_seconds_max` | `int \| None` | Hard ceiling on remaining time; `None` if no pass-lock |
| `random_blind` | `bool` | `True` while the blind period is active; `remaining_seconds` is `None` in this state |
| `password_valid` | `bool \| absent` | Only present when `password` was supplied and the lock is pass-locked; `True` if the password matches |

---

### `api_lock(lock_seconds: int | None = None, random_lock: bool = False) -> dict`

Lock the bolt. Pass `lock_seconds` for a timed lock, omit for indefinite.
Pass `random_lock=True` to activate the blind period on the ESP32 after locking
(see [Blind Period](#blind-period)).

A nonce is generated automatically and sent with every call. If the network
drops the response and `_post` retries, the ESP32 returns its cached response
and the bolt is engaged exactly once.

```python
api_lock()                                    # indefinite lock
api_lock(lock_seconds=3600)                   # lock for 1 hour
api_lock(lock_seconds=3600, random_lock=True) # lock for 1 hour, start blind period
```

Returns:
```python
{"locked": True, "remaining_seconds": 3600}   # with timer
{"locked": True, "remaining_seconds": None}   # without timer, or during blind period
```

Calls `sys.exit(1)` if the lock is pass-locked or already locked.

---

### `api_unlock() -> dict`

Unlock the bolt. Fails if pass-locked.

```python
result = api_unlock()
# {"locked": False}
```

Calls `sys.exit(1)` if pass-locked (use `api_pass_unlock` instead).

---

### `api_pass_lock(lock_seconds: int, lock_seconds_max: int, random_lock: bool = False) -> dict`

Engage password-protected lock. Both arguments are required integers (seconds).
`lock_seconds` must be ≤ `lock_seconds_max`.

Pass `random_lock=True` to activate the blind period on the ESP32 after locking
(see [Blind Period](#blind-period)). The game server uses this when the user enables
the random checkbox in the browser lock form.

```python
result = api_pass_lock(lock_seconds=120, lock_seconds_max=600)
# {
#   "locked": True,
#   "pass_locked": True,
#   "remaining_seconds": 120,
#   "lock_seconds_max": 600,
#   "password": "a3f8c2..."    ← 64-char hex; store immediately, never retrievable again
# }
password = result["password"]

result = api_pass_lock(lock_seconds=120, lock_seconds_max=600, random_lock=True)
# remaining_seconds is null in this response (blind period is already active).
# /status will also return remaining_seconds=null until the blind period expires.
```

Calls `sys.exit(1)` on error (e.g. `lock_seconds > lock_seconds_max`).

---

### `api_pass_unlock(password: str) -> dict`

Disarm the password-protected lock.

```python
result = api_pass_unlock("a3f8c2...")
# {"locked": False, "pass_locked": False}
```

A nonce is generated automatically and sent with every call so a dropped
response followed by a retry returns the ESP32's cached success rather than a
spurious "not pass-locked" error. As a fallback for the edge case where the
nonce has been evicted from the cache (or the ESP32 rebooted between attempts),
the function also probes `/status` on receiving "not pass-locked" — if the lock
is actually open, success is returned instead of exiting.

Calls `sys.exit(1)` on wrong password, or on "not pass-locked" when the lock is
still engaged (i.e. genuinely not pass-locked rather than a retry-after-success).

---

### `api_pass_adjust(password: str, adjust_seconds: int) -> dict`

Add or subtract seconds from the active countdown.
`adjust_seconds` is positive to add time, negative to subtract.
The result is clamped to `[0, lock_seconds_max]`.

```python
result = api_pass_adjust("a3f8c2...", adjust_seconds=1800)    # +30 min
result = api_pass_adjust("a3f8c2...", adjust_seconds=-600)    # -10 min
result = api_pass_adjust("a3f8c2...", adjust_seconds=999999)  # clamped to max
# {
#   "remaining_seconds": 600,   # null during the blind period
#   "lock_seconds_max": 10800,
#   "clamped": True
# }
```

`clamped` is `True` when the `lock_seconds_max` ceiling was hit.
`remaining_seconds` is `None` while the blind period is active (same as `/status`).
If the adjustment drops the timer to zero, the lock opens immediately and both
`remaining_seconds` and `lock_seconds_max` are `None` in the returned dict.

Calls `sys.exit(1)` on wrong password or other error.

---

### `parse_duration(s: str) -> int`

Parse a duration string (any format listed in the Duration Format section above)
and return the number of seconds as an integer. Useful for converting user input
before passing to the API functions.

```python
parse_duration("90")        # 90
parse_duration("30m")       # 1800
parse_duration("1h30m")     # 5400
parse_duration("1h30m45s")  # 5445
parse_duration("20:30")     # seconds until 20:30 local time (midnight rollover handled)
```

Raises `ValueError` on unrecognised format.

---

### `format_seconds(secs: int) -> str`

Format an integer number of seconds as a human-readable string.
Zero-valued components are omitted (e.g. exactly one hour is `"1h"`, not `"1h0m0s"`),
except when the total is zero, which returns `"0s"`.

```python
format_seconds(90)     # "1m30s"
format_seconds(3600)   # "1h"
format_seconds(3660)   # "1h1m"
format_seconds(5445)   # "1h30m45s"
format_seconds(0)      # "0s"
```

---

### `opens_at(remaining: int) -> str`

Return the wall-clock time at which `remaining` seconds from now will elapse.

- For durations under 24 h: returns `"HH:MM"` (e.g. `"15:42"`).
- For durations of 24 h or more: returns `"Weekday DD Mon, HH:MM"` (e.g.
  `"Thu 28 May, 14:32"`). The date component prevents ambiguity when the
  unlock time is days away.

```python
opens_at(3600)    # "15:42"              (1 hour from now — HH:MM only)
opens_at(86400)   # "Thu 28 May, 14:32"  (exactly 24 h — includes date)
opens_at(604800)  # "Wed 03 Jun, 14:32"  (7 days from now)
```

---

## Blind Period

The blind period is a timed window after a **random lock** during which the countdown
is hidden. Its purpose is to conceal the exact unlock time from the user immediately
after locking.

**Activation:** pass the `random` keyword on the CLI, or `random_lock=True` to
`api_lock()` / `api_pass_lock()` in Python.

**Duration:** controlled by `RANDOM_BLIND_SECS` in `esp32/config.py`.
Set it to `0` to disable the blind period entirely.

**While the blind period is active:**

| Observable | Value |
|---|---|
| `GET /status` → `random_blind` | `true` |
| `GET /status` → `remaining_seconds` | `null` |
| CLI `status` `Remaining` line | `X (hidden during blind period)` |
| CLI `lock … random` success | `Locked (time hidden during blind period).` |
| CLI `pass-lock … random` success | `Pass-locked (time hidden during blind period).` |
| CLI `pass-adjust +/-relative` | succeeds; remaining time stays hidden |
| CLI `pass-adjust HH:MM` | **rejected** — needs `remaining_seconds` to compute delta |

The lock is fully active during the blind period. Only the display of remaining time
is suppressed. `lock_seconds_max` (the hard ceiling) is always returned by `/status`
and always shown by the CLI.

The blind period counts down independently on the ESP32 and expires automatically.
It does not affect the lock timer. Unlocking early with `pass-unlock` clears both
the lock and the blind period immediately.

---

## Lock State Machine

```
                      ┌──────────────────────────────────┐
                      │            UNLOCKED              │
                      └──────────────────────────────────┘
                               │               │
                   api_lock()  │               │  api_pass_lock()
                      / lock   │               │    / pass-lock
                               ▼               ▼
           ┌──────────────────────────┐  ┌─────────────────────────────────┐
           │      LOCKED (simple)     │  │          PASS-LOCKED            │◀─┐
           │                          │  │                                 │  │ api_pass_adjust
           │  locked=True             │  │  locked=True                    │──┘   (pw, delta)
           │  pass_locked=False       │  │  pass_locked=True               │  / pass-adjust
           │  remaining_seconds       │  │  remaining_seconds = int        │   +/-duration pw
           │    = int | None          │  │  lock_seconds_max = int         │
           └──────────────────────────┘  │  password = <hex>               │
                        │                └─────────────────────────────────┘
                        │                              │
           api_unlock() │                              │  api_pass_unlock(pw)
             / unlock   │                              │    / pass-unlock <pw>
          (or timer     │                              │  (or timer expires)
           expires)     │                              │
                        └────────────┬─────────────────┘
                                     │
                                     ▼
                        ┌──────────────────────────────────┐
                        │            UNLOCKED              │
                        └──────────────────────────────────┘
```

Key constraints:
- `simple lock` can be unlocked by anyone with `unlock`.
- `pass-locked` can only be unlocked with the correct password via `pass-unlock`
  or `pass-adjust`. Regular `unlock` is rejected.
- `lock_seconds_max` is a hard ceiling: `pass-adjust` can never push
  `remaining_seconds` above it.
- Both timers decrement in real time on the ESP32. When a timer reaches 0 the
  bolt opens automatically and all related state is cleared.
- The password is generated by the ESP32 (cryptographically random, 32 bytes,
  hex-encoded). It is returned **once** in the `pass-lock` response and never
  stored or transmitted again. Treat it as a secret.

---

## Error Handling Notes

Cross-cutting error behavior is documented here. Per-endpoint error messages
(wrong password, lock already engaged, etc.) live in the per-command sections
of [CLI Usage](#cli-usage) and [Python Module API](#python-module-api).

### Programmatic Error Handling

All `api_*` functions print to stderr and call `sys.exit(1)` on error — there is
no exception-based error path. To catch errors in-process, call the private
`_post`/`_get` helpers directly and inspect the returned dict for an `"error"`
key. These helpers are not part of the public API contract.

### Post-action Verification

After a successful `/lock` or `/pass-lock`, the client re-fetches `/status` to
confirm the ESP32 accepted the timer. A mismatch greater than
`VERIFY_TOLERANCE_SECS` triggers a rollback.

| Endpoint     | Fields verified                          | Rollback action |
|---|---|---|
| `/lock`      | `remaining_seconds`                      | `unlock`        |
| `/pass-lock` | `remaining_seconds` + `lock_seconds_max` | `pass-unlock`   |

`/pass-unlock` and `/adjust-timer` have no post-call verification. For
`/adjust-timer`, the timer value returned in the response is used directly for
output.

**Blind period exception.** While the blind period is active, `/status` returns
`remaining_seconds: null`:
- `/lock` (random mode): verification is skipped; the lock is trusted.
- `/pass-lock` (random mode): `lock_seconds_max` is still verified
  (`/status` always returns it); `remaining_seconds` is not.

**If the rollback itself fails:**
- `/lock`: an additional warning is printed before exit.
- `/pass-lock`: a warning is printed plus an explicit manual-recovery instruction
  with the password inlined:
  `Lock is still engaged. Use 'pass-unlock <password>' to release it.`
  The password is also already on stdout (printed immediately after
  `api_pass_lock` returns), so the recovery key is in two places.

### Retry Policy

Transient errors are retried automatically before exiting with "cannot reach ESP32":

| Failure type                                     | Retries | Pause between |
|---|---|---|
| TLS handshake failure / timeout                  | 2       | 2 s           |
| Connection error (device offline / WiFi dropout) | 2       | 6 s           |

Handshake failures typically reflect the ESP32 dropping the TLS handshake under
memory pressure or responding slowly. The longer pause for connection errors
gives the ESP32 time to reassociate with the WiFi access point.

**`/pass-lock` gets 5 retries instead of 2** because a dropped response leaves
the ESP32 holding a freshly generated password that the client never received
and that cannot be re-fetched (it lives in ESP32 RAM only). Extra retries widen
the window in which the nonce cache will short-circuit a successful retry.

If `/pass-lock` retries are still exhausted, the client probes `/status` once
before exiting. If `pass_locked` is true, the error reports that the lock
engaged but the password is unrecoverable and the lock will release at the
`lock_seconds_max` ceiling — instead of the generic "cannot reach ESP32"
message.

### Idempotency

`/lock`, `/pass-unlock`, and `/adjust-timer` each include a client-generated
nonce in the request body. If the network drops the response and the client
retries, the ESP32 returns its cached response and the operation is applied
exactly once.

`/pass-unlock` additionally probes `/status` as a fallback for the edge case
where the nonce has been evicted from the cache (or the ESP32 rebooted between
attempts): on a "not pass-locked" reply, if the lock is actually open, success
is returned instead of exiting.

### TLS Errors

- **`CERT_PATH` set but `BASE_URL` uses `http://`**: the config is inconsistent —
  TLS is configured on the client side but the URL does not use it. The client
  exits immediately on any request with:
  ```
  Error: CERT_PATH is set but BASE_URL does not use https://.
    BASE_URL:  http://192.168.20.88
    CERT_PATH: /home/user/holdmestill/pi/esp32_cert.pem
  Update BASE_URL to https:// in the config file.
  ```

- **Certificate mismatch** (`CERTIFICATE_VERIFY_FAILED`): the ESP32's certificate
  does not match the pinned certificate at `CERT_PATH`. The client exits
  immediately with a message directing the user to run `setup-tls`. Not retried.
  ```
  Error: The ESP32's TLS certificate does not match the pinned certificate at
    /home/user/holdmestill/pi/esp32_cert.pem
  The device may have been reprovisioned. Run 'lock_client.py setup-tls' to re-pin.
  ```

- **No certificate pinned** (`CERT_PATH` empty, `BASE_URL` is `https://`): TLS
  verification fails because no pinned certificate is configured. The client
  exits with a message to run `setup-tls`.
  ```
  Error: TLS certificate verification failed for https://...
  Run 'lock_client.py setup-tls' to pin the ESP32 certificate,
  then add CERT_PATH=<path> to the config file.
  ```

- **Transient handshake failure / timeout** and **connection error**: see
  [Retry Policy](#retry-policy).
