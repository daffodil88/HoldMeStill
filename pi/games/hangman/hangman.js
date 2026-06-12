/**
 * hangman.js — Hangman frontend plugin.
 *
 * Registers with GameFramework as plugin id "hangman".
 */

(function () {
    const BODY_PARTS = ['hm-head', 'hm-body', 'hm-larm', 'hm-rarm', 'hm-lleg', 'hm-rleg'];

    const HangmanPlugin = {
        id: 'hangman',
        name: 'Hangman',

        init(container, instanceId, apiFetch) {
            // ── Build DOM ────────────────────────────────────────────────────
            container.innerHTML = `
                <div class="hangman-wrapper">
                    <svg class="hangman-figure" viewBox="0 0 200 260" width="180" height="234"
                         xmlns="http://www.w3.org/2000/svg">
                        <!-- Gallows (always visible) -->
                        <line x1="20" y1="240" x2="180" y2="240"
                              stroke="#818384" stroke-width="4" stroke-linecap="round"/>
                        <line x1="60" y1="240" x2="60" y2="20"
                              stroke="#818384" stroke-width="4" stroke-linecap="round"/>
                        <line x1="60" y1="20" x2="140" y2="20"
                              stroke="#818384" stroke-width="4" stroke-linecap="round"/>
                        <line x1="140" y1="20" x2="140" y2="50"
                              stroke="#818384" stroke-width="4" stroke-linecap="round"/>
                        <!-- Body parts (shown per wrong guess) -->
                        <circle id="hm-head" cx="140" cy="70" r="20"
                                stroke="#e0e0e0" stroke-width="3" fill="none"/>
                        <line id="hm-body" x1="140" y1="90" x2="140" y2="160"
                              stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
                        <line id="hm-larm" x1="140" y1="110" x2="108" y2="140"
                              stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
                        <line id="hm-rarm" x1="140" y1="110" x2="172" y2="140"
                              stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
                        <line id="hm-lleg" x1="140" y1="160" x2="108" y2="200"
                              stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
                        <line id="hm-rleg" x1="140" y1="160" x2="172" y2="200"
                              stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
                    </svg>

                    <div class="hangman-word" id="hword"></div>
                    <div class="hangman-counter" id="hcounter"></div>
                    <div class="game-message" id="hmsg"></div>
                    <div class="letter-keyboard" id="hkbd"></div>
                </div>
            `;

            const wordEl    = container.querySelector('#hword');
            const counterEl = container.querySelector('#hcounter');
            const msgEl     = container.querySelector('#hmsg');
            const kbdEl     = container.querySelector('#hkbd');

            // ── On-screen keyboard (letters only) ────────────────────────────
            const KEY_ROWS = [
                ['Q','W','E','R','T','Y','U','I','O','P'],
                ['A','S','D','F','G','H','J','K','L'],
                ['Z','X','C','V','B','N','M'],
            ];
            const keyEls = {};  // letter → button element

            KEY_ROWS.forEach(row => {
                const rowEl = document.createElement('div');
                rowEl.className = 'letter-keyboard-row';
                row.forEach(key => {
                    const btn = document.createElement('button');
                    btn.className = 'letter-keyboard-key';
                    btn.textContent = key;
                    btn.dataset.key = key;
                    btn.addEventListener('click', () => handleLetter(key));
                    rowEl.appendChild(btn);
                    keyEls[key] = btn;
                });
                kbdEl.appendChild(rowEl);
            });

            // ── State ────────────────────────────────────────────────────────
            let gameState = null;
            let gameOver  = false;

            // ── Physical keyboard ────────────────────────────────────────────
            function onKeyDown(e) {
                if (gameOver) return;
                if (e.ctrlKey || e.altKey || e.metaKey) return;
                if (/^[a-zA-Z]$/.test(e.key)) handleLetter(e.key.toUpperCase());
            }
            document.addEventListener('keydown', onKeyDown);
            window._hangmanCleanup = () => document.removeEventListener('keydown', onKeyDown);

            // ── Letter handler ───────────────────────────────────────────────
            async function handleLetter(letter) {
                if (gameOver || !gameState) return;
                if (gameState.guessed.includes(letter)) return;

                const response = await apiFetch('guess', { letter });
                if (response.error) return;

                gameState = response.state;
                renderAll();

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

            // ── Render helpers ───────────────────────────────────────────────
            function renderFigure() {
                const wrongCount = gameState ? gameState.wrong_count : 0;
                const maxWrong   = gameState ? gameState.max_wrong   : BODY_PARTS.length;
                const partsToShow = wrongCount >= maxWrong
                    ? BODY_PARTS.length
                    : Math.min(Math.ceil(wrongCount / maxWrong * BODY_PARTS.length), BODY_PARTS.length - 1);
                BODY_PARTS.forEach((id, i) => {
                    const el = container.querySelector('#' + id);
                    el.style.display = i < partsToShow ? '' : 'none';
                });
            }

            function renderWord() {
                if (!gameState) return;
                wordEl.innerHTML = '';
                gameState.display.forEach(letter => {
                    const span = document.createElement('span');
                    span.className = 'hangman-letter';
                    span.textContent = letter === '_' ? '' : letter;
                    wordEl.appendChild(span);
                });
            }

            function renderCounter() {
                if (!gameState) return;
                counterEl.textContent = `Wrong: ${gameState.wrong_count} / ${gameState.max_wrong}`;
            }

            function renderKeyboard() {
                if (!gameState) return;
                const answer = gameState.answer || '';
                for (const [letter, btn] of Object.entries(keyEls)) {
                    if (gameState.guessed.includes(letter)) {
                        const isCorrect = answer
                            ? answer.includes(letter)
                            : gameState.display.includes(letter);
                        btn.className = 'letter-keyboard-key ' + (isCorrect ? 'key-correct' : 'key-absent');
                        btn.disabled = true;
                    } else {
                        btn.className = 'letter-keyboard-key';
                        btn.disabled = gameOver;
                    }
                }
            }

            function renderAll() {
                renderFigure();
                renderWord();
                renderCounter();
                renderKeyboard();
            }

            function showMessage(text) {
                msgEl.textContent = text;
            }

            // ── Load initial state (handles page reload mid-game) ────────────
            apiFetch('state', {}).then(response => {
                gameState = response.state;
                renderAll();

                if (gameState.status === 'won') {
                    gameOver = true;
                    showMessage('You won!');
                } else if (gameState.status === 'lost') {
                    gameOver = true;
                    showMessage(`The word was ${gameState.answer}`);
                }
            });
        },
    };

    GameFramework.registerPlugin(HangmanPlugin);
})();
