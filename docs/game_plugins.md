# Building a Game Plugin

Every game in HoldMeStill is a plugin: a self-contained directory under `pi/games/`. Drop the directory in, restart the server, and the new game appears in the selector alongside the built-in ones. No changes to any framework file are required.

This guide walks through the four files you write, lists the rules the framework expects you to follow, and shows a complete minimal game (Guess the Number) so you can see how the pieces fit together.

## Directory layout

```
games/mygame/
├── mygame.py     # Backend: Python class with new_instance / get_state / handle_action
├── mygame.js     # Frontend: JavaScript plugin registered with GameFramework
├── style.css     # Game-specific styles
└── config        # ATTEMPT_PENALTY_SECONDS=... and WIN_REWARD_SECONDS=...
```

The framework handles the things every game needs: lock communication, the timer in the corner, instance lifecycle, applying the penalty when a game starts, applying the reward when one is won. Your plugin only writes the game itself.

## 1. `config`

A plain `KEY=VALUE` file. Two keys are required:

```
ATTEMPT_PENALTY_SECONDS=600
WIN_REWARD_SECONDS=300
```

`ATTEMPT_PENALTY_SECONDS` is added to the lock timer the instant the player clicks **Attempt a game**. They cannot avoid it by closing the tab. `WIN_REWARD_SECONDS` is the net seconds taken off on a win — the framework first reverses the attempt penalty, then subtracts this value, so the number you write here is the true net gain. Both are capped by `lock_seconds_max`; the player can never escape sooner than that.

Add any other custom keys your game needs (word lists, difficulty, timeouts) to the same file. Your Python class reads them at startup.

## 2. The Python backend — `mygame.py`

The backend is a single class with three methods. The framework finds it automatically: at startup it looks in every subdirectory of `games/` for a `<name>.py` file and registers the first class it finds that has both an `id` attribute and a `new_instance` method.

```python
class MyGame:
    id          = "mygame"
    name        = "My Game"
    description = "Short blurb shown on the game card."

    # Loaded from games/mygame/config at startup. See an existing game
    # (e.g. games/hangman/hangman.py) for the standard loader pattern.
    ATTEMPT_PENALTY_SECONDS = ...
    WIN_REWARD_SECONDS      = ...

    def new_instance(self) -> dict:
        # Return the initial state dict for a new game.
        # Called once when the player clicks "Attempt a game".
        return {"status": "playing", ...}

    def get_state(self, state: dict) -> dict:
        # Return a copy of the state safe to send to the browser.
        # Strip anything the player must not see (e.g. the answer)
        # until the game is over.
        ...

    def handle_action(self, state: dict, action: str, payload: dict) -> dict:
        # Apply a player action. Return:
        #   {"state": <new state>, "result": "win" | "lose" | "continue"}
        # Optionally include "message": "<text>" to forward a string to the
        # browser (errors, hints, anything transient).
        ...
```

A few things to know about how state and actions work:

- **State is just a dict.** The framework stores one dict per game instance in server RAM. You receive it, you return the updated copy. You don't manage storage.
- **One instance per "Attempt a game" click, and they run in parallel.** A player can have several instances of your game alive at once across browser tabs, and instances of different games alive at the same time. Each one is fully independent. The practical consequence: do not keep game data on the class or in module-level variables. The dict the framework hands you *is* your per-instance store. Anything you put on `self` is shared across every instance and will leak between them.
- **`status` is part of state, and the values matter.** When the game ends in a win, the state you return *must* have `status: "won"`. When it ends in a loss, `status: "lost"`. The framework checks `get_state()["status"] == "won"` before applying the reward and rejects the call otherwise. Use whatever names you like for intermediate states (`"playing"`, `"selecting"`, `"guessing"`...).
- **Every action handler must guard on `status` before doing anything.** A player can call any action directly over HTTP, bypassing your frontend. Without a guard, the player can cheat in three different ways depending on the action type:

  - **Terminal actions** (those that can return `"win"`) — without a guard, a player can fire the action straight from the initial state and claim the reward without playing. Allow only the active-play status.
  - **State-resetting actions** (a `"start"` or `"select"` that re-initialises mid-game state) — without a guard, a player who is about to lose can reset and try again without paying a fresh attempt penalty. Allow only the entry status these actions are meant to be called from.
  - **Read-only / incremental actions** — without a guard, a finished game keeps accepting input. Allow only the active-play status.

  The guard is a one-liner at the top of each handler:

  ```python
  if state["status"] != "playing":          # name of the status the action is valid in
      return {"state": state, "result": "continue"}
  ```

- **For recoverable input errors, return state unchanged with `result: "continue"`.** If the player sends bad input (wrong type, empty string, out of range), return the *original* state, `result: "continue"`, and an optional `"message"` explaining the problem. The frontend forwards the message to the player. Because state didn't change, no guess is consumed — the player just tries again. The `_guess` handler in the example below does this for non-numeric input.
- **Hide secrets in `get_state`.** If your game has an answer, omit it from the dict `get_state` returns while `status` is active. Add it back in once the game is over — the browser usually wants to show what the right answer was.

## 3. The JavaScript frontend — `mygame.js`

The frontend is one plugin object registered with the framework:

```javascript
(function () {
    const MyGamePlugin = {
        id:   'mygame',
        name: 'My Game',

        init(container, instanceId, apiFetch) {
            // container — the DOM element you render into
            // instanceId — UUID of this game instance (you rarely need it directly)
            // apiFetch(action, payload) — POSTs to your backend and returns the response

            // Restore state after a page reload before drawing anything else.
            apiFetch('state', {}).then(response => {
                // render UI from response.state
            });
        }
    };

    GameFramework.registerPlugin(MyGamePlugin);
})();
```

`apiFetch` is your one connection to the backend. Three actions are built into the framework and behave specially:

- `apiFetch('state', {})` — returns the current state without modifying it. Use it on `init` so a page reload mid-game restores correctly.
- `apiFetch('win',  {})` — tells the framework the player won. The reward is applied, the timer updates.
- `apiFetch('lose', {})` — tells the framework the player lost. No timer change (the penalty was already applied at attempt time), but the post-game buttons appear.

Any other action name is routed to your backend's `handle_action`. When the response comes back with `result: "win"` or `"lose"`, *you* call `apiFetch('win', {})` or `apiFetch('lose', {})` to close out the round. The framework does not infer this from the result field — it waits for the explicit call so you can show the player what happened first ("You won!", "The word was BANJO", etc.).

## 4. `style.css`

Game-specific styles only. Shared classes that already exist (`.letter-keyboard`, `.game-message`) live in `pi/static/style.css` and are available everywhere — reuse them when they fit.

## A complete example — Guess the Number

The server picks a number from 1 to 10. The player has three guesses. Each guess gets a "higher" or "lower" hint. Drop these four files into `pi/games/guess/` and restart the server.

**`pi/games/guess/config`**

```
ATTEMPT_PENALTY_SECONDS=120
WIN_REWARD_SECONDS=60
MAX_GUESSES=3
```

**`pi/games/guess/guess.py`**

```python
import os
import random


def _load_config(path):
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()
    return config


class GuessGame:
    id          = "guess"
    name        = "Guess the Number"
    description = "Pick the number from 1 to 10. Three tries."

    def __init__(self):
        cfg = _load_config(os.path.join(os.path.dirname(__file__), "config"))
        self.ATTEMPT_PENALTY_SECONDS = int(cfg["ATTEMPT_PENALTY_SECONDS"])
        self.WIN_REWARD_SECONDS      = int(cfg["WIN_REWARD_SECONDS"])
        self.MAX_GUESSES             = int(cfg["MAX_GUESSES"])

    def new_instance(self):
        return {
            "answer":      random.randint(1, 10),
            "guesses":     [],
            "max_guesses": self.MAX_GUESSES,
            "status":      "playing",
        }

    def get_state(self, state):
        safe = {
            "guesses":     state["guesses"],
            "max_guesses": state["max_guesses"],
            "status":      state["status"],
        }
        # Reveal the answer only after the game is over.
        if state["status"] != "playing":
            safe["answer"] = state["answer"]
        return safe

    def handle_action(self, state, action, payload):
        if action == "guess":
            return self._guess(state, payload)
        return {"state": state, "result": "continue"}

    def _guess(self, state, payload):
        # Status guard. Without this, a player could POST /guess directly
        # against a finished game and pile up extra wins.
        if state["status"] != "playing":
            return {"state": state, "result": "continue"}

        try:
            n = int(payload.get("number"))
        except (TypeError, ValueError):
            return {"state": state, "result": "continue",
                    "message": "Enter a number from 1 to 10."}

        new = dict(state)
        new["guesses"] = state["guesses"] + [n]

        if n == state["answer"]:
            new["status"] = "won"
            return {"state": new, "result": "win"}

        if len(new["guesses"]) >= state["max_guesses"]:
            new["status"] = "lost"
            return {"state": new, "result": "lose"}

        hint = "lower" if n > state["answer"] else "higher"
        return {"state": new, "result": "continue",
                "message": f"Try {hint}."}
```

**`pi/games/guess/guess.js`**

```javascript
(function () {
    const GuessPlugin = {
        id:   'guess',
        name: 'Guess the Number',

        init(container, instanceId, apiFetch) {
            container.innerHTML = `
                <div class="guess-wrapper">
                    <p>Pick a number from 1 to 10.</p>
                    <input type="number" id="g-input" min="1" max="10">
                    <button id="g-btn">Guess</button>
                    <div class="game-message" id="g-msg"></div>
                    <div id="g-history"></div>
                </div>
            `;

            const input   = container.querySelector('#g-input');
            const btn     = container.querySelector('#g-btn');
            const msgEl   = container.querySelector('#g-msg');
            const histEl  = container.querySelector('#g-history');

            let gameOver = false;

            function render(state, message) {
                histEl.textContent = `Guesses: ${state.guesses.join(', ') || '—'}` +
                                     ` (${state.guesses.length}/${state.max_guesses})`;
                if (message) msgEl.textContent = message;
            }

            async function submit() {
                if (gameOver) return;
                const number = parseInt(input.value, 10);
                input.value = '';

                const response = await apiFetch('guess', { number });
                render(response.state, response.message);

                if (response.result === 'win') {
                    gameOver = true;
                    msgEl.textContent = `Correct! It was ${response.state.answer}.`;
                    await apiFetch('win', {});
                } else if (response.result === 'lose') {
                    gameOver = true;
                    msgEl.textContent = `Out of guesses. It was ${response.state.answer}.`;
                    await apiFetch('lose', {});
                }
            }

            btn.addEventListener('click', submit);
            input.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });

            // Restore state on reload.
            apiFetch('state', {}).then(response => {
                render(response.state);
                if (response.state.status === 'won') {
                    gameOver = true;
                    msgEl.textContent = `You already won. It was ${response.state.answer}.`;
                } else if (response.state.status === 'lost') {
                    gameOver = true;
                    msgEl.textContent = `Game over. It was ${response.state.answer}.`;
                }
            });
        }
    };

    GameFramework.registerPlugin(GuessPlugin);
})();
```

**`pi/games/guess/style.css`**

```css
.guess-wrapper { max-width: 320px; margin: 2rem auto; text-align: center; }
.guess-wrapper input { font-size: 1.4rem; width: 4rem; text-align: center; }
.guess-wrapper button { font-size: 1.1rem; padding: 0.4rem 1rem; margin-left: 0.5rem; }
#g-history { margin-top: 1rem; opacity: 0.7; }
```

Restart the server, open the lock page in your browser, and "Guess the Number" appears alongside the others. The attempt penalty fires the moment you click **Attempt a game**, the win reward fires the moment you guess right.
