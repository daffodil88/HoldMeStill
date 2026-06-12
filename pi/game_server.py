"""Flask game server for the lock-game framework."""

import importlib.util
import inspect
import os as _os
import random as _random
import uuid
from flask import Flask, abort, jsonify, request, send_from_directory

import lock_client
from lock_client import (
    api_pass_lock,
    api_pass_adjust,
    api_status as _api_status,
    opens_at,
)

app = Flask(__name__, static_folder="static", static_url_path="/static")

# ── Password — RAM only, never sent to browser, never written to disk ──────────
_password = None

# ── Game instances — {instance_id: {"game_id": str, "state": dict}} ───────────
_instances: dict = {}

# ── Plugin registry — {game_id: plugin_instance} ──────────────────────────────
_plugins: dict = {}


def _register_plugin(plugin) -> None:
    if not getattr(plugin, "enabled", True):
        return
    _plugins[plugin.id] = plugin
    if hasattr(plugin, "register_routes"):
        plugin.register_routes(app, _instances, lambda: _password)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _status_payload(status: dict) -> dict:
    """Build the /api/status response body from a raw lock_client status dict."""
    remaining = status.get("remaining_seconds")
    lock_max = status.get("lock_seconds_max")
    payload = {
        "locked": status["locked"],
        "pass_locked": status["pass_locked"],
        "remaining_seconds": remaining,
        "lock_seconds_max": lock_max,
        "opens_at": opens_at(remaining) if remaining is not None else None,
        "max_opens_at": opens_at(lock_max) if lock_max is not None else None,
        "password_set": _password is not None,
        "random_blind": status.get("random_blind", False),
    }
    if "password_valid" in status:
        payload["password_valid"] = status["password_valid"]
    return payload


def _random_initial_secs(initial_secs: int, max_secs: int) -> int:
    """Pick a random initial duration in [RANDOM_START_PERCENT%, 100%] of the span."""
    span = max_secs - initial_secs
    min_secs = initial_secs + int(span * lock_client.RANDOM_START_PERCENT / 100)
    return _random.randint(min_secs, max_secs)


# ── Static routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/game/<game_id>")
def game_page(game_id):
    if game_id not in _plugins:
        abort(404)
    return send_from_directory("static", "game.html")


@app.route("/games/<game_id>/<path:filename>")
def game_static(game_id, filename):
    return send_from_directory(_os.path.join("games", game_id), filename)


# ── API: lock state ────────────────────────────────────────────────────────────

@app.route("/api/games")
def api_games():
    result = []
    for p in _plugins.values():
        err = getattr(p, "_config_error", None)
        if err:
            result.append({"id": p.id, "name": p.name, "error": err})
        elif not getattr(p, "SHOW_TIMES", True):
            result.append({"id": p.id, "name": p.name, "description": p.description})
        else:
            result.append({
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "penalty_seconds": p.ATTEMPT_PENALTY_SECONDS,
                "reward_seconds": p.WIN_REWARD_SECONDS,
            })
    return jsonify(result)


@app.route("/api/status")
def api_status_route():
    try:
        status = _api_status(_password)
    except SystemExit:
        return jsonify({
            "error": "esp32_unreachable",
            "esp32_url": lock_client.BASE_URL,
        }), 503
    return jsonify(_status_payload(status))


@app.route("/api/pass-lock", methods=["POST"])
def api_pass_lock_route():
    global _password
    data = request.json
    lock_seconds = int(data["lock_seconds"])
    lock_seconds_max = int(data["lock_seconds_max"])
    use_random = bool(data.get("random", False))

    if use_random:
        lock_seconds = _random_initial_secs(lock_seconds, lock_seconds_max)

    result = api_pass_lock(lock_seconds, lock_seconds_max, random_lock=use_random)
    _password = result["password"]  # stored in RAM only — never sent to browser

    lock_max = result["lock_seconds_max"]
    if use_random:
        # Blind period active: hide remaining_seconds and opens_at from the browser
        return jsonify({
            "locked": True,
            "remaining_seconds": None,
            "lock_seconds_max": lock_max,
            "opens_at": None,
            "max_opens_at": opens_at(lock_max),
            "password_set": True,
            "random_blind": True,
        })
    remaining = result["remaining_seconds"]
    return jsonify({
        "locked": True,
        "remaining_seconds": remaining,
        "lock_seconds_max": lock_max,
        "opens_at": opens_at(remaining),
        "max_opens_at": opens_at(lock_max),
        "password_set": True,
        "random_blind": False,
    })


@app.route("/api/set-password", methods=["POST"])
def api_set_password():
    global _password
    pwd = (request.json or {}).get("password", "").strip()
    if not pwd:
        return jsonify({"error": "password required"}), 400
    _password = pwd  # RAM only — never echoed, never logged
    return jsonify({"ok": True})


@app.route("/api/clear-password", methods=["POST"])
def api_clear_password():
    global _password
    _password = None
    return jsonify({"ok": True})


@app.route("/api/parse-duration", methods=["POST"])
def api_parse_duration():
    s = (request.json or {}).get("duration", "").strip()
    try:
        secs = lock_client.parse_duration(s)
        return jsonify({"seconds": secs})
    except (ValueError, SystemExit):
        return jsonify({"error": f"Invalid duration: '{s}'"}), 400


# ── API: game lifecycle ────────────────────────────────────────────────────────

@app.route("/api/attempt", methods=["POST"])
def api_attempt():
    data = request.json
    game_id = data["game_id"]
    plugin = _plugins.get(game_id)
    if not plugin:
        return jsonify({"error": "unknown game"}), 404
    if getattr(plugin, "_config_error", None):
        return jsonify({"error": "Game is misconfigured — check the server config."}), 400

    # Capture remaining_seconds before the penalty so we can compute what was
    # actually added — the ESP32 clamps to lock_seconds_max, so the effective
    # penalty may be smaller than ATTEMPT_PENALTY_SECONDS.
    pre_status = _api_status(_password)
    pre_remaining = pre_status["remaining_seconds"]

    # Apply attempt penalty at instance-creation time (not at loss time)
    result = api_pass_adjust(_password, plugin.ATTEMPT_PENALTY_SECONDS)

    # Actual seconds added (≤ ATTEMPT_PENALTY_SECONDS when clamped).
    # During the blind period both pre_remaining and result["remaining_seconds"]
    # are null, so fall back to the nominal penalty. Clamping is unlikely this
    # early in the lock.
    if pre_remaining is None:
        actual_penalty = plugin.ATTEMPT_PENALTY_SECONDS
    else:
        actual_penalty = result["remaining_seconds"] - pre_remaining

    instance_id = str(uuid.uuid4())
    _instances[instance_id] = {
        "game_id": game_id,
        "state": plugin.new_instance(),
        "resolved": False,
        "actual_penalty": actual_penalty,
    }

    return jsonify({
        "instance_id": instance_id,
        "remaining_seconds": result["remaining_seconds"],
        "lock_seconds_max": result["lock_seconds_max"],
        "clamped": result["clamped"],
    })


@app.route("/api/win", methods=["POST"])
def api_win():
    data = request.json
    instance_id = data["instance_id"]
    instance = _instances.get(instance_id)
    if not instance:
        return jsonify({"error": "instance not found"}), 404
    if instance["resolved"]:
        return jsonify({"error": "instance already resolved"}), 409

    plugin = _plugins[instance["game_id"]]

    # Verify the game is actually in a won state before applying any reward
    if plugin.get_state(instance["state"]).get("status") != "won":
        return jsonify({"error": "game not won"}), 403

    # Net win reduction = reward + actual penalty applied at attempt time.
    # Using actual_penalty (not the nominal ATTEMPT_PENALTY_SECONDS) ensures that
    # when the attempt penalty was clamped, the win doesn't over-subtract.
    actual_penalty = instance.get("actual_penalty", plugin.ATTEMPT_PENALTY_SECONDS)
    # Mark resolved before the ESP32 call so a dropped response on retry
    # returns 409 instead of applying the reward a second time.
    instance["resolved"] = True
    try:
        result = api_pass_adjust(_password, -(actual_penalty + plugin.WIN_REWARD_SECONDS))
    except SystemExit:
        instance["resolved"] = False  # allow retry
        return jsonify({"error": "esp32_unreachable"}), 503
    return jsonify({
        "remaining_seconds": result["remaining_seconds"],
        "lock_seconds_max": result["lock_seconds_max"],
        "clamped": result["clamped"],
    })


@app.route("/api/lose", methods=["POST"])
def api_lose():
    data = request.json
    instance_id = data["instance_id"]
    instance = _instances.get(instance_id)
    if not instance:
        return jsonify({"error": "instance not found"}), 404
    if instance["resolved"]:
        return jsonify({"error": "instance already resolved"}), 409

    # No timer adjustment on loss — penalty was applied at instance creation
    instance["resolved"] = True
    status = _api_status(_password)
    return jsonify({"remaining_seconds": status["remaining_seconds"]})


# ── API: in-game actions ───────────────────────────────────────────────────────

@app.route("/api/game/<game_id>/<instance_id>/<action>", methods=["POST"])
def api_game_action(game_id, instance_id, action):
    instance = _instances.get(instance_id)
    if not instance:
        return jsonify({"error": "instance not found"}), 404
    if instance["game_id"] != game_id:
        return jsonify({"error": "instance does not belong to this game"}), 403
    if instance["resolved"]:
        return jsonify({"error": "instance already resolved"}), 409

    plugin = _plugins.get(game_id)
    if not plugin:
        return jsonify({"error": "game not found"}), 404

    # Special built-in action: return current state without modification
    if action == "state":
        return jsonify({
            "state": plugin.get_state(instance["state"]),
            "result": "continue",
        })

    payload = request.json or {}
    action_result = plugin.handle_action(instance["state"], action, payload)

    # Store raw state; send only the browser-safe view
    _instances[instance_id]["state"] = action_result["state"]
    response = {
        "state": plugin.get_state(action_result["state"]),
        "result": action_result["result"],
    }
    if "message" in action_result:
        response["message"] = action_result["message"]
    return jsonify(response)


# ── Plugin auto-discovery ──────────────────────────────────────────────────────

def _autodiscover_plugins() -> None:
    """Scan games/ subdirectories and register any game plugins found."""
    games_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "games")

    # Discover all plugins in alphabetical order
    discovered = []
    for name in sorted(_os.listdir(games_dir)):
        game_dir = _os.path.join(games_dir, name)
        if not _os.path.isdir(game_dir):
            continue
        py_file = _os.path.join(game_dir, f"{name}.py")
        if not _os.path.isfile(py_file):
            continue
        spec = importlib.util.spec_from_file_location(f"games.{name}", py_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if hasattr(cls, "id") and hasattr(cls, "new_instance"):
                discovered.append(cls())
                break

    # Parse games.conf: ordering + disabled set
    disabled = set()
    order_map = {}
    games_conf = _os.path.join(games_dir, "games.conf")
    if _os.path.isfile(games_conf):
        idx = 0
        with open(games_conf) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    gid = line[1:-1].strip()
                    disabled.add(gid)
                else:
                    gid = line
                order_map[gid] = idx
                idx += 1

    # Filter out disabled games and apply ordering
    discovered = [p for p in discovered if p.id not in disabled]
    discovered.sort(key=lambda p: (order_map.get(p.id, len(order_map)), p.id))

    for plugin in discovered:
        _register_plugin(plugin)

_autodiscover_plugins()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
