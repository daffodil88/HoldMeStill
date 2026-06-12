"""Vocabulary Trainer backend plugin."""

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


class VocabularyGame:
    id = "vocabulary"
    name = "Vocabulary Trainer"
    description = "Practise vocabulary by typing the foreign word from a translation."

    VOCAB_DIR = os.path.dirname(os.path.abspath(__file__))
    WORD_COUPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "word-couples")

    def __init__(self):
        self._config_error = None
        try:
            config = _load_config(os.path.join(self.VOCAB_DIR, "config"))
        except FileNotFoundError:
            self._config_error = f"games/{self.id}/config not found"
            return

        required = ["ATTEMPT_PENALTY_SECONDS", "WIN_REWARD_SECONDS", "WIN_ACCURACY_PERCENT"]
        missing = [k for k in required if not config.get(k, "").strip()]
        if missing:
            self._config_error = (
                f"games/{self.id}/config is missing: {', '.join(missing)}"
            )
            return

        try:
            self.ATTEMPT_PENALTY_SECONDS = int(config["ATTEMPT_PENALTY_SECONDS"])
            self.WIN_REWARD_SECONDS = int(config["WIN_REWARD_SECONDS"])
            self._win_accuracy_percent = int(config["WIN_ACCURACY_PERCENT"])
        except ValueError as e:
            self._config_error = f"games/{self.id}/config has an invalid value: {e}"
            return

        if not os.path.isdir(self.VOCAB_DIR):
            print(
                f"Error: vocabulary directory not found at {self.VOCAB_DIR}",
                file=sys.stderr,
            )
            sys.exit(1)

    # ── Plugin interface ───────────────────────────────────────────────────────

    def new_instance(self) -> dict:
        """Create and return initial game state for a new instance."""
        return {
            "status": "selecting",
            "available_files": self._list_files(),
            "file": None,
            "words": [],
            "word_status": [],
            "first_try_correct": [],
            "current_idx": None,
            "last_idx": None,
            "total_mistakes": 0,
            "last_result": None,
        }

    def get_state(self, state: dict) -> dict:
        """Return browser-safe game state. Foreign words (answers) are hidden while active."""
        words = state.get("words", [])
        word_status = state.get("word_status", [])
        first_try_correct = state.get("first_try_correct", [])

        safe = {
            "status": state["status"],
            "file": state.get("file"),
            "total": len(words),
            "remaining": sum(1 for s in word_status if s == "pending"),
            "total_mistakes": state.get("total_mistakes", 0),
            "correct_first_try_count": sum(1 for x in first_try_correct if x is True),
            "last_result": state.get("last_result"),
        }

        if state["status"] == "selecting":
            safe["available_files"] = state.get("available_files", [])

        if state["status"] == "active":
            idx = state["current_idx"]
            safe["current_native"] = words[idx][1]
            safe["word_number"] = sum(1 for s in word_status if s == "done") + 1

        return safe

    def handle_action(self, state: dict, action: str, payload: dict) -> dict:
        """Apply an action to the game state and return updated state + result."""
        if action == "start":
            return self._handle_start(state, payload)
        if action == "guess":
            return self._handle_guess(state, payload)
        return {"state": state, "result": "continue"}

    # ── Internals ──────────────────────────────────────────────────────────────

    def _handle_start(self, state: dict, payload: dict) -> dict:
        if state["status"] != "selecting":
            return {"state": state, "result": "continue"}
        filename = payload.get("file", "").strip()

        # Validate filename against the directory listing (prevents path traversal)
        if filename not in self._list_files():
            return {"state": state, "result": "continue"}

        words = self._load_file(filename)
        if not words:
            return {"state": state, "result": "continue"}

        n = len(words)
        new_state = {
            "status": "active",
            "available_files": state.get("available_files", []),
            "file": filename,
            "words": words,
            "word_status": ["pending"] * n,
            "first_try_correct": [None] * n,
            "current_idx": random.randrange(n),
            "last_idx": None,
            "total_mistakes": 0,
            "last_result": None,
        }
        return {"state": new_state, "result": "continue"}

    def _handle_guess(self, state: dict, payload: dict) -> dict:
        if state["status"] != "active":
            return {"state": state, "result": "continue"}

        idx = state["current_idx"]
        correct_word = state["words"][idx][0]
        guess = payload.get("word", "")
        is_correct = guess == correct_word

        word_status = list(state["word_status"])
        first_try_correct = list(state["first_try_correct"])
        total_mistakes = state["total_mistakes"]

        last_result = {
            "correct": is_correct,
            "your_answer": guess,
            "correct_answer": correct_word if not is_correct else None,
        }

        if is_correct:
            word_status[idx] = "done"
            if first_try_correct[idx] is None:
                first_try_correct[idx] = True
        else:
            total_mistakes += 1
            if first_try_correct[idx] is None:
                first_try_correct[idx] = False

        pending = [i for i, s in enumerate(word_status) if s == "pending"]

        if not pending:
            # All words answered correctly — determine win or lose
            total = len(state["words"])
            allowed_mistakes = total * (1 - self._win_accuracy_percent / 100)
            won = total_mistakes <= allowed_mistakes

            new_state = {
                **state,
                "status": "won" if won else "lost",
                "word_status": word_status,
                "first_try_correct": first_try_correct,
                "current_idx": None,
                "last_idx": idx,
                "total_mistakes": total_mistakes,
                "last_result": last_result,
            }
            return {"state": new_state, "result": "win" if won else "lose"}

        # Pick the next word, avoiding the same pair twice in a row
        candidates = pending if len(pending) == 1 else [i for i in pending if i != idx]
        next_idx = random.choice(candidates)

        new_state = {
            **state,
            "word_status": word_status,
            "first_try_correct": first_try_correct,
            "current_idx": next_idx,
            "last_idx": idx,
            "total_mistakes": total_mistakes,
            "last_result": last_result,
        }
        return {"state": new_state, "result": "continue"}

    def _list_files(self) -> list:
        """Return a sorted list of vocabulary files in the word-couples directory."""
        try:
            return sorted(
                f for f in os.listdir(self.WORD_COUPLES_DIR)
                if os.path.isfile(os.path.join(self.WORD_COUPLES_DIR, f))
            )
        except OSError:
            return []

    def _load_file(self, filename: str) -> list:
        """Load word pairs from a vocabulary file. Returns list of [foreign, native]."""
        path = os.path.join(self.WORD_COUPLES_DIR, filename)
        pairs = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or "|" not in line:
                    continue
                foreign, native = line.split("|", 1)
                foreign = foreign.strip()
                native = native.strip()
                if foreign and native:
                    pairs.append([foreign, native])
        return pairs
