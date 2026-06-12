"""Hangman backend plugin."""

import os
import random
import sys


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


class HangmanGame:
    id = "hangman"
    name = "Hangman"
    description = "Guess the hidden word one letter at a time. 6 wrong guesses and it's over."

    _GAME_DIR = os.path.dirname(os.path.abspath(__file__))

    def __init__(self):
        self._config_error = None
        try:
            config = _load_config(os.path.join(self._GAME_DIR, "config"))
        except FileNotFoundError:
            self._config_error = f"games/{self.id}/config not found"
            return

        required = ["ATTEMPT_PENALTY_SECONDS", "WIN_REWARD_SECONDS", "MAX_WRONG"]
        missing = [k for k in required if not config.get(k, "").strip()]
        if missing:
            self._config_error = (
                f"games/{self.id}/config is missing: {', '.join(missing)}"
            )
            return

        try:
            self.ATTEMPT_PENALTY_SECONDS = int(config["ATTEMPT_PENALTY_SECONDS"])
            self.WIN_REWARD_SECONDS = int(config["WIN_REWARD_SECONDS"])
            self.MAX_WRONG = int(config["MAX_WRONG"])
        except ValueError as e:
            self._config_error = f"games/{self.id}/config has an invalid value: {e}"
            return

        word_list_path = os.path.join(self._GAME_DIR, "hangman-list")
        try:
            with open(word_list_path) as f:
                words = [line.strip().upper() for line in f if line.strip()]
        except FileNotFoundError:
            print(f"Error: hangman-list not found at {word_list_path}", file=sys.stderr)
            sys.exit(1)

        if not words:
            print("Error: hangman-list is empty", file=sys.stderr)
            sys.exit(1)

        self._words = words

    # ── Plugin interface ───────────────────────────────────────────────────────

    def new_instance(self) -> dict:
        """Create and return initial game state for a new instance."""
        return {
            "answer": random.choice(self._words),
            "guessed": [],
            "status": "active",
        }

    def get_state(self, state: dict) -> dict:
        """Return browser-safe game state. Answer is hidden while game is active."""
        wrong_count = sum(
            1 for letter in state["guessed"] if letter not in state["answer"]
        )
        display = [
            letter if letter in state["guessed"] else "_"
            for letter in state["answer"]
        ]
        safe = {
            "guessed": state["guessed"],
            "wrong_count": wrong_count,
            "max_wrong": self.MAX_WRONG,
            "display": display,
            "status": state["status"],
        }
        if state["status"] != "active":
            safe["answer"] = state["answer"]
        return safe

    def handle_action(self, state: dict, action: str, payload: dict) -> dict:
        """Apply an action to the game state and return updated state + result."""
        if action == "guess":
            return self._handle_guess(state, payload)
        return {"state": state, "result": "continue"}

    # ── Internals ──────────────────────────────────────────────────────────────

    def _handle_guess(self, state: dict, payload: dict) -> dict:
        if state["status"] != "active":
            return {"state": state, "result": "continue"}

        letter = payload.get("letter", "").strip().upper()
        if len(letter) != 1 or not letter.isalpha():
            return {"state": state, "result": "continue"}

        if letter in state["guessed"]:
            return {"state": state, "result": "continue"}

        new_state = {
            "answer": state["answer"],
            "guessed": state["guessed"] + [letter],
            "status": state["status"],
        }

        wrong_count = sum(
            1 for l in new_state["guessed"] if l not in new_state["answer"]
        )

        # Check win: every letter in answer has been guessed
        if all(l in new_state["guessed"] for l in new_state["answer"]):
            new_state["status"] = "won"
            return {"state": new_state, "result": "win"}

        # Check lose: too many wrong guesses
        if wrong_count >= self.MAX_WRONG:
            new_state["status"] = "lost"
            return {"state": new_state, "result": "lose"}

        return {"state": new_state, "result": "continue"}
