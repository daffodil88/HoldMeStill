"""2048 backend plugin."""

import os
import random


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


class Game2048:
    id = "2048"
    name = "2048"
    description = "Slide tiles to combine them and reach the target tile."

    _GAME_DIR = os.path.dirname(os.path.abspath(__file__))

    def __init__(self):
        self._config_error = None
        try:
            config = _load_config(os.path.join(self._GAME_DIR, "config"))
        except FileNotFoundError:
            self._config_error = f"games/{self.id}/config not found"
            return

        required = ["ATTEMPT_PENALTY_SECONDS", "WIN_REWARD_SECONDS"]
        missing = [k for k in required if not config.get(k, "").strip()]
        if missing:
            self._config_error = (
                f"games/{self.id}/config is missing: {', '.join(missing)}"
            )
            return

        try:
            self.ATTEMPT_PENALTY_SECONDS = int(config["ATTEMPT_PENALTY_SECONDS"])
            self.WIN_REWARD_SECONDS = int(config["WIN_REWARD_SECONDS"])
            self._win_target = int(config.get("WIN_TARGET", "2048"))
        except ValueError as e:
            self._config_error = f"games/{self.id}/config has an invalid value: {e}"
            return

    # ── Plugin interface ───────────────────────────────────────────────────────

    def new_instance(self) -> dict:
        """Create and return initial game state for a new instance."""
        board = [[0] * 4 for _ in range(4)]
        board = self._spawn_tile(board)
        board = self._spawn_tile(board)
        return {
            "board": board,
            "score": 0,
            "status": "active",
            "win_target": self._win_target,
        }

    def get_state(self, state: dict) -> dict:
        """Return browser-safe game state (all fields are safe for 2048)."""
        return state

    def handle_action(self, state: dict, action: str, payload: dict) -> dict:
        """Apply an action to the game state and return updated state + result."""
        if action == "move":
            return self._handle_move(state, payload)
        return {"state": state, "result": "continue"}

    # ── Internals ──────────────────────────────────────────────────────────────

    def _handle_move(self, state: dict, payload: dict) -> dict:
        if state["status"] != "active":
            return {"state": state, "result": "continue"}

        direction = payload.get("direction")
        if direction not in ("up", "down", "left", "right"):
            return {"state": state, "result": "continue"}

        new_board, delta, moved = self._move_board(state["board"], direction)
        if not moved:
            return {"state": state, "result": "continue"}

        new_board = self._spawn_tile(new_board)
        new_score = state["score"] + delta
        win_target = state["win_target"]

        if self._check_win(new_board):
            return {
                "state": {"board": new_board, "score": new_score, "status": "won", "win_target": win_target},
                "result": "win",
            }

        if not self._has_valid_moves(new_board):
            return {
                "state": {"board": new_board, "score": new_score, "status": "lost", "win_target": win_target},
                "result": "lose",
            }

        return {
            "state": {"board": new_board, "score": new_score, "status": "active", "win_target": win_target},
            "result": "continue",
        }

    def _slide_row(self, row: list) -> tuple:
        """Slide one row leftward, merging equal adjacent pairs.

        Returns (new_row, score_delta).
        """
        tiles = [t for t in row if t != 0]
        score_delta = 0
        merged = []
        i = 0
        while i < len(tiles):
            if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
                value = tiles[i] * 2
                merged.append(value)
                score_delta += value
                i += 2
            else:
                merged.append(tiles[i])
                i += 1
        merged += [0] * (4 - len(merged))
        return merged, score_delta

    def _move_board(self, board: list, direction: str) -> tuple:
        """Apply slides to the full board in the given direction.

        Returns (new_board, total_score_delta, moved).
        """
        # Transform board so we always slide "left", then transform back.
        if direction == "left":
            rows = [list(row) for row in board]
        elif direction == "right":
            rows = [list(reversed(row)) for row in board]
        elif direction == "up":
            # Transpose: rows become columns
            rows = [[board[r][c] for r in range(4)] for c in range(4)]
        else:  # down
            # Transpose reversed: bottom-to-top columns become rows
            rows = [[board[r][c] for r in range(3, -1, -1)] for c in range(4)]

        new_rows = []
        total_delta = 0
        for row in rows:
            new_row, delta = self._slide_row(row)
            new_rows.append(new_row)
            total_delta += delta

        # Transform back
        if direction == "left":
            new_board = new_rows
        elif direction == "right":
            new_board = [list(reversed(row)) for row in new_rows]
        elif direction == "up":
            # Transpose back
            new_board = [[new_rows[c][r] for c in range(4)] for r in range(4)]
        else:  # down
            # Reverse-transpose back
            new_board = [[new_rows[c][3 - r] for c in range(4)] for r in range(4)]

        moved = new_board != board
        return new_board, total_delta, moved

    def _spawn_tile(self, board: list) -> list:
        """Place a new tile (2 with 90% chance, 4 with 10%) in a random empty cell."""
        empty = [(r, c) for r in range(4) for c in range(4) if board[r][c] == 0]
        if not empty:
            return board
        r, c = random.choice(empty)
        new_board = [list(row) for row in board]
        new_board[r][c] = 4 if random.random() < 0.1 else 2
        return new_board

    def _check_win(self, board: list) -> bool:
        """Return True if any tile has reached the win target."""
        return any(cell >= self._win_target for row in board for cell in row)

    def _has_valid_moves(self, board: list) -> bool:
        """Return True if any move is possible (empty cell or adjacent equal tiles)."""
        for r in range(4):
            for c in range(4):
                if board[r][c] == 0:
                    return True
                if c + 1 < 4 and board[r][c] == board[r][c + 1]:
                    return True
                if r + 1 < 4 and board[r][c] == board[r + 1][c]:
                    return True
        return False
