/**
 * wordle.js — Wordle frontend plugin.
 *
 * Registers with GameFramework as plugin id "wordle".
 */

(function () {
    const WordlePlugin = {
        id: 'wordle',
        name: 'Wordle',

        init(container, instanceId, apiFetch) {
            // ── Build DOM ────────────────────────────────────────────────────
            container.innerHTML = `
                <div class="wordle-wrapper">
                    <div class="wordle-grid" id="wgrid"></div>
                    <div class="wordle-indicators">
                        <div class="indicator-row" id="wind-misplaced"></div>
                        <div class="indicator-row" id="wind-correct"></div>
                    </div>
                    <div class="game-message" id="wmsg"></div>
                    <div class="letter-keyboard" id="wkbd"></div>
                </div>
            `;

            const gridEl       = container.querySelector('#wgrid');
            const kbdEl        = container.querySelector('#wkbd');
            const msgEl        = container.querySelector('#wmsg');
            const rowMisplaced = container.querySelector('#wind-misplaced');
            const rowCorrect   = container.querySelector('#wind-correct');

            // ── Grid cells ───────────────────────────────────────────────────
            const cells = [];
            for (let r = 0; r < 6; r++) {
                cells[r] = [];
                for (let c = 0; c < 5; c++) {
                    const cell = document.createElement('div');
                    cell.className = 'wordle-cell';
                    gridEl.appendChild(cell);
                    cells[r][c] = cell;
                }
            }

            // ── On-screen keyboard ───────────────────────────────────────────
            const KEY_ROWS = [
                ['Q','W','E','R','T','Y','U','I','O','P'],
                ['A','S','D','F','G','H','J','K','L'],
                ['BACKSPACE','Z','X','C','V','B','N','M','ENTER'],
            ];
            const keyEls = {};  // letter → button element

            KEY_ROWS.forEach(row => {
                const rowEl = document.createElement('div');
                rowEl.className = 'letter-keyboard-row';
                row.forEach(key => {
                    const btn = document.createElement('button');
                    btn.className = 'letter-keyboard-key';
                    if (key === 'ENTER' || key === 'BACKSPACE') {
                        btn.classList.add('key-wide');
                    }
                    btn.textContent = key === 'BACKSPACE' ? '⌫' : key;
                    btn.dataset.key = key;
                    btn.addEventListener('click', () => handleKey(key));
                    rowEl.appendChild(btn);
                    if (key !== 'ENTER' && key !== 'BACKSPACE') {
                        keyEls[key] = btn;
                    }
                });
                kbdEl.appendChild(rowEl);
            });

            // ── State ────────────────────────────────────────────────────────
            let gameState  = null;   // current state from server
            let currentInput = '';   // letters typed for current row
            let gameOver   = false;

            // ── Physical keyboard ────────────────────────────────────────────
            function onKeyDown(e) {
                if (gameOver) return;
                if (e.ctrlKey || e.altKey || e.metaKey) return;
                if (e.key === 'Enter') handleKey('ENTER');
                else if (e.key === 'Backspace') handleKey('BACKSPACE');
                else if (/^[a-zA-Z]$/.test(e.key)) handleKey(e.key.toUpperCase());
            }
            document.addEventListener('keydown', onKeyDown);

            // Remove listener if user navigates away without full page-unload
            // (safety cleanup; page normally reloads on navigation)
            window._wordleCleanup = () => document.removeEventListener('keydown', onKeyDown);

            // ── Key handler ──────────────────────────────────────────────────
            function handleKey(key) {
                if (gameOver || !gameState) return;

                if (key === 'BACKSPACE') {
                    if (currentInput.length > 0) {
                        currentInput = currentInput.slice(0, -1);
                        renderInput();
                    }
                } else if (key === 'ENTER') {
                    submitGuess();
                } else if (currentInput.length < 5) {
                    currentInput += key;
                    renderInput();
                }
            }

            // ── Render helpers ───────────────────────────────────────────────
            function renderInput() {
                if (!gameState) return;
                const row = gameState.guesses.length;
                if (row >= 6) return;
                for (let c = 0; c < 5; c++) {
                    const letter = currentInput[c] || '';
                    cells[row][c].textContent = letter;
                    cells[row][c].className = 'wordle-cell' + (letter ? ' filled' : '');
                }
            }

            function renderGrid() {
                if (!gameState) return;
                // Render all completed rows
                for (let r = 0; r < gameState.guesses.length; r++) {
                    const row = gameState.results[r];
                    for (let c = 0; c < 5; c++) {
                        cells[r][c].textContent = row[c].letter;
                        cells[r][c].className = `wordle-cell revealed ${row[c].state}`;
                    }
                }
                // Clear rows beyond completed ones
                for (let r = gameState.guesses.length; r < 6; r++) {
                    for (let c = 0; c < 5; c++) {
                        cells[r][c].textContent = '';
                        cells[r][c].className = 'wordle-cell';
                    }
                }
                if (!gameOver) renderInput();
            }

            function renderKeyboard() {
                if (!gameState) return;
                const PRIORITY = { correct: 3, present: 2, absent: 1 };
                const best = {};
                for (const row of gameState.results) {
                    for (const { letter, state } of row) {
                        if (!best[letter] || PRIORITY[state] > PRIORITY[best[letter]]) {
                            best[letter] = state;
                        }
                    }
                }
                for (const [letter, btn] of Object.entries(keyEls)) {
                    const state = best[letter];
                    btn.className = 'letter-keyboard-key' + (state ? ` key-${state}` : '');
                }
            }

            function renderIndicators() {
                if (!gameState) return;

                // Collect information from all guesses
                const misplacedLetters = new Set();
                const correctPositions = Array(5).fill(null);

                for (const row of gameState.results) {
                    for (let i = 0; i < row.length; i++) {
                        const { letter, state } = row[i];
                        if (state === 'present') misplacedLetters.add(letter);
                        if (state === 'correct') correctPositions[i] = letter;
                    }
                }

                // Yellow row: letters known to be misplaced (not yet confirmed correct)
                rowMisplaced.innerHTML = '';
                const truelyMisplaced = [...misplacedLetters].filter(
                    l => !correctPositions.includes(l)
                ).sort();

                if (truelyMisplaced.length > 0) {
                    const lbl = document.createElement('span');
                    lbl.className = 'indicator-label';
                    lbl.textContent = 'Misplaced:';
                    rowMisplaced.appendChild(lbl);
                    for (const letter of truelyMisplaced) {
                        const tile = document.createElement('span');
                        tile.className = 'indicator-tile present';
                        tile.textContent = letter;
                        rowMisplaced.appendChild(tile);
                    }
                }

                // Green row: confirmed positions (show all 5 slots)
                rowCorrect.innerHTML = '';
                if (correctPositions.some(l => l !== null)) {
                    const lbl = document.createElement('span');
                    lbl.className = 'indicator-label';
                    lbl.textContent = 'Known:';
                    rowCorrect.appendChild(lbl);
                    for (let i = 0; i < 5; i++) {
                        const tile = document.createElement('span');
                        tile.className = `indicator-tile ${correctPositions[i] ? 'correct' : 'empty'}`;
                        tile.textContent = correctPositions[i] || '_';
                        rowCorrect.appendChild(tile);
                    }
                }
            }

            function shakeRow(rowIndex) {
                for (const cell of cells[rowIndex]) {
                    cell.classList.add('shake');
                    cell.addEventListener('animationend', () => {
                        cell.classList.remove('shake');
                    }, { once: true });
                }
            }

            function showMessage(text, isError = false) {
                msgEl.textContent = text;
                msgEl.classList.toggle('error', isError);
            }

            // ── Submit guess ─────────────────────────────────────────────────
            async function submitGuess() {
                if (!gameState || gameOver) return;

                if (currentInput.length !== 5) {
                    shakeRow(gameState.guesses.length);
                    return;
                }

                const word = currentInput;
                currentInput = '';

                const response = await apiFetch('guess', { word });

                if (response.message) {
                    // Restore input on error
                    currentInput = word;
                    renderInput();
                    shakeRow(gameState.guesses.length);
                    showMessage(response.message, true);
                    return;
                }

                gameState = response.state;
                showMessage('');
                renderGrid();
                renderKeyboard();
                renderIndicators();

                if (response.result === 'win') {
                    gameOver = true;
                    showMessage('You won! 🎉');
                    await apiFetch('win', {});
                } else if (response.result === 'lose') {
                    gameOver = true;
                    showMessage(`The word was ${gameState.answer}`);
                    await apiFetch('lose', {});
                }
            }

            // ── Load initial state (handles page reload mid-game) ────────────
            apiFetch('state', {}).then(response => {
                gameState = response.state;
                renderGrid();
                renderKeyboard();
                renderIndicators();

                if (gameState.status === 'won') {
                    gameOver = true;
                    showMessage('You won! 🎉');
                } else if (gameState.status === 'lost') {
                    gameOver = true;
                    showMessage(`The word was ${gameState.answer}`);
                }
            });
        },
    };

    GameFramework.registerPlugin(WordlePlugin);
})();
