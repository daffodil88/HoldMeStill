"""Are you watching? — backend plugin."""

import json
import os
import select
import subprocess
import threading
import time


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


def _ffprobe_duration(abs_path: str):
    """Return video duration in seconds (float), or None if unavailable."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet",
             "-show_entries", "format=duration",
             "-of", "json", abs_path],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(probe.stdout)
        return float(data["format"]["duration"])
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, KeyError):
        return None


class WatchingGame:
    id = "watching"
    name = "Are you watching?"
    description = "Watch the video and click the dots before they vanish. Miss too many and you lose."

    GAME_DIR = os.path.dirname(os.path.abspath(__file__))
    VIDEOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "videos")

    _current_proc = None          # Popen | None — at most one stream at a time
    _proc_lock = threading.Lock()

    def __init__(self):
        self._config_error = None
        try:
            config = _load_config(os.path.join(self.GAME_DIR, "config"))
        except FileNotFoundError:
            self._config_error = f"games/{self.id}/config not found"
            return

        required = [
            "ATTEMPT_PENALTY_SECONDS",
            "WIN_REWARD_SECONDS",
            "MIN_DOT_INTERVAL_SECONDS",
            "MAX_DOT_INTERVAL_SECONDS",
            "DOT_TIMEOUT_SECONDS",
            "MAX_MISS_PERCENT",
        ]
        missing = [k for k in required if not config.get(k, "").strip()]
        if missing:
            self._config_error = (
                f"games/{self.id}/config is missing: {', '.join(missing)}"
            )
            return

        try:
            self.ATTEMPT_PENALTY_SECONDS = int(config["ATTEMPT_PENALTY_SECONDS"])
            self.WIN_REWARD_SECONDS = int(config["WIN_REWARD_SECONDS"])
            self.MIN_DOT_INTERVAL_SECONDS = int(config["MIN_DOT_INTERVAL_SECONDS"])
            self.MAX_DOT_INTERVAL_SECONDS = int(config["MAX_DOT_INTERVAL_SECONDS"])
            self.DOT_TIMEOUT_SECONDS = int(config["DOT_TIMEOUT_SECONDS"])
            self.MAX_MISS_PERCENT = int(config["MAX_MISS_PERCENT"])
            # Optional: 0 means play the full video (no segment slider)
            self.SEGMENT_DURATION_SECONDS = int(config.get("SEGMENT_DURATION_SECONDS", "0"))
        except ValueError as e:
            self._config_error = f"games/{self.id}/config has an invalid value: {e}"
            return

        raw_video_dir = config.get("VIDEO_DIR", "").strip()
        if raw_video_dir:
            self.videos_dir = os.path.realpath(raw_video_dir)
        else:
            self.videos_dir = os.path.realpath(self.VIDEOS_DIR)

    # ── Custom routes ──────────────────────────────────────────────────────────

    def register_routes(self, app, *_) -> None:
        """Register streaming video, thumbnail, and stop routes."""
        from flask import Response, abort, request, stream_with_context

        videos_dir = self.videos_dir

        def _safe_video_path(filename):
            """Return (abs_path, safe_name) if valid, else abort 404."""
            safe_name = os.path.basename(filename)
            abs_path = os.path.realpath(os.path.join(videos_dir, safe_name))
            if not abs_path.startswith(videos_dir + os.sep) and abs_path != videos_dir:
                abort(404)
            if not os.path.isfile(abs_path):
                abort(404)
            return abs_path, safe_name

        @app.route("/games/watching/videos/<filename>")
        def watching_video(filename):
            abs_path, safe_name = _safe_video_path(filename)

            # Parse optional start offset and segment duration (seconds)
            try:
                start_secs = max(0.0, float(request.args.get("start", "0")))
            except ValueError:
                start_secs = 0.0
            try:
                seg_duration = max(0.0, float(request.args.get("duration", "0")))
            except ValueError:
                seg_duration = 0.0

            # Kill any existing stream before starting a new one (only one at a time)
            with WatchingGame._proc_lock:
                old = WatchingGame._current_proc
                WatchingGame._current_proc = None
            if old and old.poll() is None:
                old.kill()
                old.wait()

            # Probe codecs
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-show_streams", "-of", "json", abs_path],
                    capture_output=True, text=True, timeout=10,
                )
                streams = json.loads(probe.stdout).get("streams", [])
            except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
                # ffprobe unavailable or failed — fall back to direct file serving
                from flask import send_from_directory
                return send_from_directory(videos_dir, safe_name)

            video_codec = next(
                (s.get("codec_name") for s in streams if s.get("codec_type") == "video"),
                None,
            )
            audio_codec = next(
                (s.get("codec_name") for s in streams if s.get("codec_type") == "audio"),
                None,
            )

            if video_codec == "h264" and audio_codec in ("aac", "mp3"):
                extra = ["-c:v", "copy", "-c:a", "copy"]
            else:
                extra = ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"]

            cmd = ["ffmpeg"]
            if start_secs > 0:
                cmd += ["-ss", str(start_secs)]   # input seeking (fast, before -i)
            cmd += ["-i", abs_path]
            if seg_duration > 0:
                cmd += ["-t", str(seg_duration)]  # output duration limit (after -i)
            cmd += [
                *extra,
                "-f", "mp4", "-movflags", "frag_keyframe+empty_moov",
                "pipe:1",
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    bufsize=0)
            fd = proc.stdout.fileno()
            with WatchingGame._proc_lock:
                WatchingGame._current_proc = proc

            def generate():
                try:
                    while True:
                        try:
                            r, _, _ = select.select([fd], [], [], 0.5)
                        except (ValueError, OSError):
                            break
                        if not r:
                            # Timeout — check if ffmpeg exited (all data already read)
                            if proc.poll() is not None:
                                break
                            continue
                        try:
                            chunk = os.read(fd, 65536)
                        except OSError:
                            break
                        if not chunk:
                            break
                        yield chunk
                finally:
                    proc.kill()
                    proc.wait()
                    with WatchingGame._proc_lock:
                        if WatchingGame._current_proc is proc:
                            WatchingGame._current_proc = None

            return Response(
                stream_with_context(generate()),
                mimetype="video/mp4",
            )

        @app.route("/games/watching/thumbnail/<filename>")
        def watching_thumbnail(filename):
            abs_path, _ = _safe_video_path(filename)

            try:
                t = max(0.0, float(request.args.get("t", "0")))
            except ValueError:
                t = 0.0

            try:
                result = subprocess.run(
                    ["ffmpeg", "-ss", str(t), "-i", abs_path,
                     "-vframes", "1", "-f", "image2", "-vcodec", "mjpeg", "pipe:1"],
                    capture_output=True, timeout=10,
                )
                if result.returncode == 0 and result.stdout:
                    return Response(result.stdout, mimetype="image/jpeg")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            return ("", 204)

        @app.route("/games/watching/stream/stop", methods=["POST"])
        def watching_stop_stream():
            with WatchingGame._proc_lock:
                proc = WatchingGame._current_proc
                WatchingGame._current_proc = None
            if proc and proc.poll() is None:
                proc.kill()
                # generate()'s finally will call proc.wait(); don't block here
            return ("", 204)

    # ── Plugin interface ───────────────────────────────────────────────────────

    def new_instance(self) -> dict:
        return {"status": "selecting"}

    def get_state(self, state: dict) -> dict:
        return state

    def handle_action(self, state: dict, action: str, payload: dict) -> dict:
        if action == "list_videos":
            return self._handle_list_videos(state)
        if action == "select_video":
            return self._handle_select_video(state, payload)
        if action == "report":
            return self._handle_report(state, payload)
        return {"state": state, "result": "continue"}

    # ── Internals ──────────────────────────────────────────────────────────────

    _VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}

    def _list_videos(self) -> list:
        try:
            return sorted(
                f for f in os.listdir(self.videos_dir)
                if os.path.isfile(os.path.join(self.videos_dir, f))
                and os.path.splitext(f)[1].lower() in self._VIDEO_EXTENSIONS
            )
        except OSError:
            return []

    def _handle_list_videos(self, state: dict) -> dict:
        if state.get("status") not in ("selecting", "segment_selecting"):
            return {"state": state, "result": "continue"}
        new_state = {
            **state,
            "status": "selecting",
            "videos": self._list_videos(),
            "config": {
                "min_interval": self.MIN_DOT_INTERVAL_SECONDS,
                "max_interval": self.MAX_DOT_INTERVAL_SECONDS,
                "dot_timeout": self.DOT_TIMEOUT_SECONDS,
                "max_miss_percent": self.MAX_MISS_PERCENT,
                "segment_duration": self.SEGMENT_DURATION_SECONDS,
                "videos_dir": self.videos_dir,
            },
        }
        return {"state": new_state, "result": "continue"}

    def _handle_select_video(self, state: dict, payload: dict) -> dict:
        if state.get("status") not in ("selecting", "segment_selecting"):
            return {"state": state, "result": "continue"}
        video = payload.get("video", "")
        # Prevent path traversal: only allow plain filenames that exist in videos dir
        safe_name = os.path.basename(video)
        if not safe_name or safe_name not in self._list_videos():
            return {"state": state, "result": "continue"}

        abs_path = os.path.join(self.videos_dir, safe_name)

        # If start_time is explicitly supplied this is the "confirm" call after the
        # slider — skip probing and go straight to playing.
        # This path is only valid from "segment_selecting" (where video_duration is
        # already stored in state). Calling it from bare "selecting" would skip the
        # ffprobe step, leaving video_duration unknown and making timing validation
        # unreliable.
        if "start_time" in payload and state.get("status") == "segment_selecting":
            try:
                start_time = float(payload["start_time"])
            except (TypeError, ValueError):
                start_time = 0.0
            start_time = max(0.0, start_time)
            # Clamp against stored video_duration if available
            video_duration = state.get("video_duration")
            if video_duration and self.SEGMENT_DURATION_SECONDS > 0:
                start_time = min(start_time, max(0.0, video_duration - self.SEGMENT_DURATION_SECONDS))
            # playing_secs: segment duration, clamped against remaining video length
            if self.SEGMENT_DURATION_SECONDS > 0:
                playing_secs = float(self.SEGMENT_DURATION_SECONDS)
                if video_duration:
                    playing_secs = min(playing_secs, video_duration - start_time)
            else:
                playing_secs = video_duration  # None if not probed
            new_state = {**state, "status": "playing", "video": safe_name, "start_time": start_time,
                         "playing_since": time.time(), "playing_secs": playing_secs}
            return {"state": new_state, "result": "continue"}

        # First click: probe duration to decide whether the slider is needed.
        probed_duration = None
        if self.SEGMENT_DURATION_SECONDS > 0:
            probed_duration = _ffprobe_duration(abs_path)
            if probed_duration is not None and probed_duration > self.SEGMENT_DURATION_SECONDS:
                new_state = {
                    **state,
                    "status": "segment_selecting",
                    "video": safe_name,
                    "video_duration": probed_duration,
                }
                return {"state": new_state, "result": "continue"}

        # No slider needed — go straight to playing.
        # Probe duration now if not already known (used for timing validation in report).
        if probed_duration is None:
            probed_duration = _ffprobe_duration(abs_path)
        playing_secs = float(self.SEGMENT_DURATION_SECONDS) if self.SEGMENT_DURATION_SECONDS > 0 else probed_duration
        new_state = {**state, "status": "playing", "video": safe_name, "start_time": 0.0,
                     "playing_since": time.time(), "playing_secs": playing_secs}
        return {"state": new_state, "result": "continue"}

    def _handle_report(self, state: dict, payload: dict) -> dict:
        if state.get("status") != "playing":
            return {"state": state, "result": "continue"}

        try:
            hits = int(payload.get("hits", 0))
            misses = int(payload.get("misses", 0))
        except (TypeError, ValueError):
            hits, misses = 0, 0
        if hits < 0 or misses < 0:
            return {"state": {**state, "status": "lost", "hits": 0, "misses": 0},
                    "result": "lose"}

        # ── Server-side timing validation ────────────────────────────────────────
        playing_since = state.get("playing_since")
        playing_secs = state.get("playing_secs")
        elapsed = time.time() - playing_since if playing_since is not None else None

        def _instant_lose():
            return {"state": {**state, "status": "lost", "hits": 0, "misses": 0},
                    "result": "lose"}

        if elapsed is not None:
            # Reject if the video hasn't had enough time to finish.
            # Allow a 3 s buffer for ffmpeg startup and end-of-stream latency.
            # If playing_secs is None (ffprobe unavailable), we cannot verify timing
            # and must reject the report to prevent an immediate-win exploit.
            if playing_secs is None or elapsed < playing_secs - 3.0:
                return _instant_lose()

            # Reject impossible hit counts: at minimum one dot per MIN_DOT_INTERVAL.
            # Add 1 for edge-case timing (first dot can appear before one interval elapses).
            max_possible_hits = int(elapsed / self.MIN_DOT_INTERVAL_SECONDS) + 1
            if hits > max_possible_hits:
                return _instant_lose()
        # ── End timing validation ─────────────────────────────────────────────────

        total = hits + misses
        if total == 0:
            result = "lose"
        else:
            miss_percent = (misses / total) * 100
            result = "win" if miss_percent <= self.MAX_MISS_PERCENT else "lose"
        status = "won" if result == "win" else "lost"
        new_state = {**state, "status": status, "hits": hits, "misses": misses}
        return {"state": new_state, "result": result}
