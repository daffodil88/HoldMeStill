"""Wordle backend plugin."""

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


class WordleGame:
    id = "wordle"
    name = "Wordle"
    description = "Guess the 5-letter word in 6 tries."

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
        except ValueError as e:
            self._config_error = f"games/{self.id}/config has an invalid value: {e}"
            return

        self.strict_mode = config.get("STRICT_MODE", "false").lower() == "true"

        word_list_path = os.path.join(self._GAME_DIR, "wordle-list")
        try:
            with open(word_list_path) as f:
                words = [line.strip().upper() for line in f if line.strip()]
        except FileNotFoundError:
            print(f"Error: wordle-list not found at {word_list_path}", file=sys.stderr)
            sys.exit(1)

        if not words:
            print("Error: wordle-list is empty", file=sys.stderr)
            sys.exit(1)

        self._words = words

        if self.strict_mode:
            allowed_path = os.path.join(self._GAME_DIR, "allowed-guesses")
            try:
                with open(allowed_path) as f:
                    allowed = {line.strip().upper() for line in f if line.strip()}
            except FileNotFoundError:
                print(f"Error: allowed-guesses not found at {allowed_path}", file=sys.stderr)
                sys.exit(1)
            # Answers are always valid guesses even if missing from allowed-guesses
            self._allowed = allowed | set(self._words)

    # ── Plugin interface ───────────────────────────────────────────────────────

    def new_instance(self) -> dict:
        """Create and return initial game state for a new instance."""
        return {
            "answer": random.choice(self._words),
            "guesses": [],
            "results": [],
            "status": "active",
        }

    def get_state(self, state: dict) -> dict:
        """Return browser-safe game state. Answer is hidden while game is active."""
        safe = {
            "guesses": state["guesses"],
            "results": state["results"],
            "status": state["status"],
        }
        # Reveal answer only when the game is over
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

        word = payload.get("word", "").strip().upper()
        if len(word) != 5:
            return {"state": state, "result": "continue"}

        if self.strict_mode and word not in self._allowed:
            return {"state": state, "result": "continue", "message": "Not a valid word"}

        result_row = self._score_guess(state["answer"], word)
        new_state = {
            "answer": state["answer"],
            "guesses": state["guesses"] + [word],
            "results": state["results"] + [result_row],
            "status": state["status"],
        }

        if word == state["answer"]:
            new_state["status"] = "won"
            return {"state": new_state, "result": "win"}

        if len(new_state["guesses"]) >= 6:
            new_state["status"] = "lost"
            return {"state": new_state, "result": "lose"}

        return {"state": new_state, "result": "continue"}

    def _score_guess(self, answer: str, guess: str) -> list:
        """Score a guess against the answer, applying standard Wordle rules."""
        result = [None] * 5
        answer_pool = list(answer)

        # First pass: mark correct positions
        for i in range(5):
            if guess[i] == answer[i]:
                result[i] = "correct"
                answer_pool[i] = None  # consume this letter from the pool

        # Second pass: mark present / absent
        for i in range(5):
            if result[i] is not None:
                continue
            if guess[i] in answer_pool:
                result[i] = "present"
                answer_pool[answer_pool.index(guess[i])] = None
            else:
                result[i] = "absent"

        return [{"letter": guess[i], "state": result[i]} for i in range(5)]
