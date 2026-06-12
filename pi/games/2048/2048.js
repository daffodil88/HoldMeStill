/**
 * 2048.js — 2048 frontend plugin.
 *
 * Registers with GameFramework as plugin id "2048".
 * Supports arrow key input and touch swipe in all four directions.
 */

(function () {
    const Plugin2048 = {
        id: '2048',
        name: '2048',

        init(container, instanceId, apiFetch) {
            // ── Build DOM ────────────────────────────────────────────────────
            container.innerHTML = `
                <div class="t2048-wrapper">
                    <div class="t2048-header">
                        <div class="t2048-target-label" id="t2048-target"></div>
                        <div class="t2048-score-box">
                            <div class="t2048-score-label">SCORE</div>
                            <div class="t2048-score-value" id="t2048-score">0</div>
                        </div>
                    </div>
                    <div class="t2048-board" id="t2048-board"></div>
                    <div class="game-message" id="t2048-msg"></div>
                </div>
            `;

            const boardEl   = container.querySelector('#t2048-board');
            const scoreEl   = container.querySelector('#t2048-score');
            const msgEl     = container.querySelector('#t2048-msg');
            const targetEl  = container.querySelector('#t2048-target');

            // ── Build board cells ────────────────────────────────────────────
            // 16 background cells (always visible grid squares)
            for (let i = 0; i < 16; i++) {
                const bg = document.createElement('div');
                bg.className = 't2048-bg-cell';
                boardEl.appendChild(bg);
            }

            // 16 tile overlay divs (positioned over the background cells)
            const tileDivs = [];
            for (let i = 0; i < 16; i++) {
                const tile = document.createElement('div');
                tile.className = 't2048-tile t2048-empty';
                boardEl.appendChild(tile);
                tileDivs.push(tile);
            }

            // ── State ────────────────────────────────────────────────────────
            let gameState = null;
            let gameOver  = false;
            let moving    = false;  // debounce: ignore input while a move is in-flight

            // ── Render helpers ───────────────────────────────────────────────
            function renderBoard(state) {
                for (let r = 0; r < 4; r++) {
                    for (let c = 0; c < 4; c++) {
                        const value = state.board[r][c];
                        const tile  = tileDivs[r * 4 + c];
                        if (value === 0) {
                            tile.textContent = '';
                            tile.className = 't2048-tile t2048-empty';
                        } else {
                            tile.textContent = value;
                            // Cap CSS class at 2048; higher values share that style
                            const cls = value <= 2048 ? value : 'high';
                            tile.className = `t2048-tile t2048-v${cls}`;
                        }
                    }
                }
            }

            function renderScore(score) {
                scoreEl.textContent = score;
            }

            function renderTarget(winTarget) {
                if (winTarget !== 2048) {
                    targetEl.textContent = `Win Target ${winTarget}`;
                } else {
                    targetEl.textContent = '';
                }
            }

            function showMessage(text) {
                msgEl.textContent = text;
            }

            // ── Send move to server ──────────────────────────────────────────
            async function sendMove(direction) {
                if (gameOver || moving) return;
                moving = true;
                try {
                    const response = await apiFetch('move', { direction });
                    gameState = response.state;
                    renderBoard(gameState);
                    renderScore(gameState.score);
                    renderTarget(gameState.win_target);

                    if (response.result === 'win') {
                        gameOver = true;
                        showMessage('You won!');
                        await apiFetch('win', {});
                    } else if (response.result === 'lose') {
                        gameOver = true;
                        showMessage('No more moves. Game over!');
                        await apiFetch('lose', {});
                    }
                } finally {
                    moving = false;
                }
            }

            // ── Arrow key input ──────────────────────────────────────────────
            const KEY_MAP = {
                ArrowUp:    'up',
                ArrowDown:  'down',
                ArrowLeft:  'left',
                ArrowRight: 'right',
            };

            function onKeyDown(e) {
                if (gameOver) return;
                const dir = KEY_MAP[e.key];
                if (!dir) return;
                e.preventDefault();  // prevent page scroll on arrow keys
                sendMove(dir);
            }

            document.addEventListener('keydown', onKeyDown);
            // Safety cleanup if user navigates without a full page reload
            window._2048Cleanup = () => document.removeEventListener('keydown', onKeyDown);

            // ── Touch swipe input ────────────────────────────────────────────
            let touchStartX = 0;
            let touchStartY = 0;
            const SWIPE_MIN = 30;  // minimum px to register as a swipe

            boardEl.addEventListener('touchstart', e => {
                touchStartX = e.touches[0].clientX;
                touchStartY = e.touches[0].clientY;
            }, { passive: true });

            boardEl.addEventListener('touchend', e => {
                if (gameOver) return;
                const dx = e.changedTouches[0].clientX - touchStartX;
                const dy = e.changedTouches[0].clientY - touchStartY;
                if (Math.abs(dx) < SWIPE_MIN && Math.abs(dy) < SWIPE_MIN) return;
                let dir;
                if (Math.abs(dx) >= Math.abs(dy)) {
                    dir = dx > 0 ? 'right' : 'left';
                } else {
                    dir = dy > 0 ? 'down' : 'up';
                }
                sendMove(dir);
            }, { passive: true });

            // ── Load initial state (handles page reload mid-game) ────────────
            apiFetch('state', {}).then(response => {
                gameState = response.state;
                renderBoard(gameState);
                renderScore(gameState.score);
                renderTarget(gameState.win_target);

                if (gameState.status === 'won') {
                    gameOver = true;
                    showMessage('You won!');
                } else if (gameState.status === 'lost') {
                    gameOver = true;
                    showMessage('No more moves. Game over!');
                }
            });
        },
    };

    GameFramework.registerPlugin(Plugin2048);
})();
