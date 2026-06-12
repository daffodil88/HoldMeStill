/**
 * framework.js — shared frontend framework.
 *
 * Provides:
 *   GameFramework.registerPlugin(plugin)   — register a frontend plugin
 *   GameFramework.startTimer(element)      — init countdown timer in an element
 *   GameFramework.updateTimer(secs)        — push a new remaining_seconds value
 *   GameFramework.startGame(container, gameId, instanceId)
 */

const GameFramework = (() => {
    // ── Timer state ──────────────────────────────────────────────────────────
    let _timerEl = null;
    let _timerMinimal = false;
    let _remainingSecs = null;
    let _maxOpensAt = null;
    let _randomBlind = false;   // true while ESP32 is in random-lock blind period
    let _connectingMsg = null;  // shown below timer while retrying ESP32

    function _computeOpensAt(secs) {
        const target = new Date(Date.now() + secs * 1000);
        const h = String(target.getHours()).padStart(2, '0');
        const m = String(target.getMinutes()).padStart(2, '0');
        if (secs >= 86400) {
            const DAYS   = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            const dow = DAYS[target.getDay()];
            const day = String(target.getDate()).padStart(2, '0');
            const mon = MONTHS[target.getMonth()];
            return `${dow} ${day} ${mon}, ${h}:${m}`;
        }
        return `${h}:${m}`;
    }

    function _formatRemaining(secs) {
        if (secs <= 0) return '0s';
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = secs % 60;
        if (h > 0) return `${h}h ${m}m ${s}s`;
        if (m > 0) return `${m}m ${s}s`;
        return `${s}s`;
    }

    function _renderTimer() {
        if (!_timerEl) return;
        if (_randomBlind) {
            const maxPart = _maxOpensAt ? ` (latest ${_maxOpensAt})` : '';
            let html;
            if (_timerMinimal) {
                html = `<span class="timer-countdown">X remaining</span>`;
            } else {
                html =
                    `<span class="timer-lock-line">Locked \u2013 Opens at ?${maxPart}</span><br>` +
                    `<span class="timer-countdown">X remaining</span>`;
            }
            if (_connectingMsg) {
                html += `<br><span class="timer-connecting">${_connectingMsg}</span>`;
            }
            _timerEl.innerHTML = html;
            return;
        }
        if (_remainingSecs === null) {
            if (_connectingMsg) {
                _timerEl.innerHTML = `<span class="timer-connecting">${_connectingMsg}</span>`;
            }
            return;
        }
        const display = _formatRemaining(_remainingSecs);
        let html;
        if (_timerMinimal) {
            html = `<span class="timer-countdown">${display} remaining</span>`;
        } else {
            const opensAt = _computeOpensAt(_remainingSecs);
            const maxPart = _maxOpensAt ? ` (latest ${_maxOpensAt})` : '';
            html =
                `<span class="timer-lock-line">Locked \u2013 Opens at ${opensAt}${maxPart}</span><br>` +
                `<span class="timer-countdown">${display} remaining</span>`;
        }
        if (_connectingMsg) {
            html += `<br><span class="timer-connecting">${_connectingMsg}</span>`;
        }
        _timerEl.innerHTML = html;
    }

    function updateTimer(secs) {
        _remainingSecs = secs;
        _renderTimer();
    }

    async function _syncWithServer() {
        try {
            const resp = await fetch('/api/status');
            const data = await resp.json();
            if (data.error) {
                // ESP32 unreachable or other server error — keep counting down locally
            } else if (data.random_blind) {
                _randomBlind = true;
                _remainingSecs = null;
                _maxOpensAt = data.max_opens_at || null;
                _renderTimer();
            } else if (data.remaining_seconds !== null) {
                _randomBlind = false;
                _remainingSecs = data.remaining_seconds;
                _maxOpensAt = data.max_opens_at || null;
                _renderTimer();
            } else {
                _randomBlind = false;
                _remainingSecs = null;
                if (_timerEl) _timerEl.innerHTML = '';
            }
        } catch (_) {
            // Network error — keep counting down from local value
        }
    }

    function startTimer(element, { minimal = false } = {}) {
        _timerEl = element;
        _timerMinimal = minimal;

        // Initial sync with server
        _syncWithServer();

        // Count down every second locally
        setInterval(() => {
            if (_remainingSecs !== null && _remainingSecs > 0) {
                _remainingSecs--;
                _renderTimer();
            }
        }, 1000);

        // Resync every 60 seconds to correct drift (not more frequently)
        setInterval(_syncWithServer, 60 * 1000);
    }

    // ── Plugin registry ──────────────────────────────────────────────────────
    const _plugins = {};

    function registerPlugin(plugin) {
        _plugins[plugin.id] = plugin;
    }

    // ── Post-game buttons ────────────────────────────────────────────────────
    function _showGameEndButtons(container, gameId) {
        if (document.getElementById('game-end-buttons')) return;

        const div = document.createElement('div');
        div.id = 'game-end-buttons';
        div.className = 'game-end-buttons';

        const attemptBtn = document.createElement('button');
        attemptBtn.className = 'attempt-btn';
        attemptBtn.textContent = 'Another attempt';
        attemptBtn.addEventListener('click', async () => {
            attemptBtn.disabled = true;
            const resp = await fetch('/api/attempt', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ game_id: gameId }),
            });
            const data = await resp.json();
            if (data.instance_id) {
                if (data.remaining_seconds !== null) updateTimer(data.remaining_seconds);
                window.location.href = `/game/${gameId}?instance_id=${data.instance_id}`;
            } else {
                attemptBtn.disabled = false;
            }
        });

        const menuBtn = document.createElement('button');
        menuBtn.className = 'main-menu-btn';
        menuBtn.textContent = 'Main menu';
        menuBtn.addEventListener('click', () => {
            window.location.href = '/';
        });

        div.appendChild(attemptBtn);
        div.appendChild(menuBtn);
        container.insertAdjacentElement('afterend', div);
    }

    // ── apiFetch factory ─────────────────────────────────────────────────────
    // "win" and "lose" are intercepted and routed to /api/win and /api/lose.
    // All other actions go to /api/game/<gameId>/<instanceId>/<action>.
    function _makeApiFetch(gameId, instanceId, container) {
        return async function apiFetch(action, payload) {
            if (action === 'win') {
                while (true) {
                    let data;
                    try {
                        const resp = await fetch('/api/win', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ instance_id: instanceId }),
                        });
                        data = await resp.json();
                    } catch (_) {
                        data = { error: 'network_error' };
                    }

                    if (data.error === 'esp32_unreachable' || data.error === 'network_error') {
                        _connectingMsg = 'Applying win reward\u2026 connecting to lock';
                        _renderTimer();
                        await new Promise(r => setTimeout(r, 5000));
                        continue;
                    }

                    _connectingMsg = null;
                    if (data.remaining_seconds !== null) {
                        updateTimer(data.remaining_seconds);
                    }
                    _showGameEndButtons(container, gameId);
                    return data;
                }
            }

            if (action === 'lose') {
                const resp = await fetch('/api/lose', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ instance_id: instanceId }),
                });
                const data = await resp.json();
                if (data.remaining_seconds !== null) {
                    updateTimer(data.remaining_seconds);
                }
                _showGameEndButtons(container, gameId);
                return data;
            }

            const resp = await fetch(`/api/game/${gameId}/${instanceId}/${action}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            return await resp.json();
        };
    }

    // ── startGame ────────────────────────────────────────────────────────────
    function startGame(container, gameId, instanceId) {
        const plugin = _plugins[gameId];
        if (!plugin) {
            container.textContent = `Unknown game: ${gameId}`;
            return;
        }
        const apiFetch = _makeApiFetch(gameId, instanceId, container);
        plugin.init(container, instanceId, apiFetch);
    }

    return { registerPlugin, startTimer, startGame, updateTimer };
})();
