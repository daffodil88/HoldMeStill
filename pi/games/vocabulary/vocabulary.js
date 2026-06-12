/**
 * vocabulary.js — Vocabulary Trainer frontend plugin.
 *
 * Registers with GameFramework as plugin id "vocabulary".
 */

(function () {
    const VocabularyPlugin = {
        id: 'vocabulary',
        name: 'Vocabulary Trainer',

        init(container, instanceId, apiFetch) {
            // ── Build wrapper ────────────────────────────────────────────────
            container.innerHTML = `<div class="vocab-wrapper" id="vocab-wrapper"></div>`;
            const wrapper = container.querySelector('#vocab-wrapper');

            const WRONG_MESSAGES = [
                'Not quite…',
                'Try again!',
                "You'll get it next time.",
                'Almost there!',
                'Keep going!',
                'Close, but not quite.',
                "Don't worry, it happens!",
                'One more try next round.',
                'Nearly!',
                "That's a tricky one.",
                'You can do it!',
                'No worries — onward!',
                'Every mistake is a step forward.',
                "It'll stick eventually!",
                'Practice makes perfect.',
                'Shake it off and keep going!',
                "You're getting there.",
                'Good effort — keep at it!',
            ];

            let gameOver = false;
            // Set to a function when the reveal screen is showing; Enter/Continue calls it.
            let continueCallback = null;
            // The native-language word currently on screen, so the reveal view can show it.
            let currentNative = null;

            // ── Physical keyboard ────────────────────────────────────────────
            function onKeyDown(e) {
                if (gameOver || e.ctrlKey || e.altKey || e.metaKey) return;
                if (e.key !== 'Enter') return;

                if (continueCallback) {
                    continueCallback();
                    return;
                }
                const input = wrapper.querySelector('.vocab-input');
                if (input && document.activeElement === input) {
                    submitGuess(input);
                }
            }
            document.addEventListener('keydown', onKeyDown);
            window._vocabCleanup = () => document.removeEventListener('keydown', onKeyDown);

            // ── Render dispatcher ────────────────────────────────────────────
            function render(state) {
                continueCallback = null;
                wrapper.innerHTML = '';
                if (state.status === 'selecting') {
                    renderSelecting(state);
                } else if (state.status === 'active') {
                    renderActive(state);
                } else {
                    renderFinished(state);
                }
            }

            // ── View: file selection ─────────────────────────────────────────
            function renderSelecting(state) {
                const heading = document.createElement('p');
                heading.className = 'vocab-heading';
                heading.textContent = 'Choose a vocabulary set:';
                wrapper.appendChild(heading);

                const list = document.createElement('div');
                list.className = 'vocab-file-list';

                const files = state.available_files || [];
                if (files.length === 0) {
                    const msg = document.createElement('p');
                    msg.className = 'vocab-no-files';
                    msg.textContent = 'No vocabulary files found.';
                    wrapper.appendChild(msg);
                    return;
                }

                files.forEach(filename => {
                    const btn = document.createElement('button');
                    btn.className = 'vocab-file-btn';
                    btn.textContent = filename;
                    btn.addEventListener('click', async () => {
                        btn.disabled = true;
                        const response = await apiFetch('start', { file: filename });
                        render(response.state);
                    });
                    list.appendChild(btn);
                });

                wrapper.appendChild(list);
            }

            // ── View: active game ────────────────────────────────────────────
            function renderActive(state) {
                currentNative = state.current_native;

                // Stats
                const stats = document.createElement('div');
                stats.className = 'vocab-stats';
                stats.textContent =
                    `Word ${state.word_number} of ${state.total}` +
                    `  \u2022  Mistakes: ${state.total_mistakes}`;
                wrapper.appendChild(stats);

                // Feedback from last guess (correct answers only — wrong answers
                // are shown in the reveal view before advancing)
                if (state.last_result && state.last_result.correct) {
                    const feedback = document.createElement('div');
                    feedback.className = 'vocab-feedback correct';
                    feedback.textContent = 'Correct!';
                    wrapper.appendChild(feedback);
                } else {
                    // Spacer so the layout doesn't jump
                    const spacer = document.createElement('div');
                    spacer.className = 'vocab-feedback';
                    wrapper.appendChild(spacer);
                }

                // Native word prompt
                const prompt = document.createElement('div');
                prompt.className = 'vocab-prompt';
                prompt.textContent = state.current_native;
                wrapper.appendChild(prompt);

                // Input row
                const inputRow = document.createElement('div');
                inputRow.className = 'vocab-input-row';

                const input = document.createElement('input');
                input.type = 'text';
                input.className = 'vocab-input';
                input.placeholder = 'Type the foreign word\u2026';
                input.autocomplete = 'off';
                input.autocorrect = 'off';
                input.autocapitalize = 'off';
                input.spellcheck = false;

                const submitBtn = document.createElement('button');
                submitBtn.className = 'vocab-submit';
                submitBtn.textContent = 'Submit';
                submitBtn.addEventListener('click', () => submitGuess(input));

                inputRow.appendChild(input);
                inputRow.appendChild(submitBtn);
                wrapper.appendChild(inputRow);

                requestAnimationFrame(() => input.focus());
            }

            // ── View: reveal correct answer after a wrong guess ──────────────
            function renderReveal(correctWord, nextState, wrongWord) {
                continueCallback = null;
                wrapper.innerHTML = '';

                // Randomly picked encouragement message, with the wrong word struck through inline
                const feedback = document.createElement('div');
                feedback.className = 'vocab-feedback wrong';
                feedback.appendChild(document.createTextNode(
                    WRONG_MESSAGES[Math.floor(Math.random() * WRONG_MESSAGES.length)] + ' ('
                ));
                const strike = document.createElement('s');
                strike.textContent = wrongWord;
                feedback.appendChild(strike);
                feedback.appendChild(document.createTextNode(')'));
                wrapper.appendChild(feedback);

                // The native word that was being asked (for association)
                const nativeEl = document.createElement('div');
                nativeEl.className = 'vocab-prompt vocab-prompt--dim';
                nativeEl.textContent = currentNative;
                wrapper.appendChild(nativeEl);

                // The correct foreign word, prominently displayed
                const correctEl = document.createElement('div');
                correctEl.className = 'vocab-correct-reveal';
                correctEl.textContent = correctWord;
                wrapper.appendChild(correctEl);

                // Continue button
                const continueBtn = document.createElement('button');
                continueBtn.className = 'vocab-continue-btn';
                continueBtn.textContent = 'Continue';

                const proceed = () => {
                    continueCallback = null;
                    render(nextState);
                };

                continueCallback = proceed;
                continueBtn.addEventListener('click', proceed);
                wrapper.appendChild(continueBtn);

                requestAnimationFrame(() => continueBtn.focus());
            }

            // ── View: game finished ──────────────────────────────────────────
            function renderFinished(state) {
                const correctCount = state.correct_first_try_count;
                const total = state.total;

                const result = document.createElement('div');
                result.className = 'vocab-result';

                const mistakesEl = document.createElement('p');
                mistakesEl.className = 'vocab-score';
                mistakesEl.textContent = `${state.total_mistakes} mistake${state.total_mistakes === 1 ? '' : 's'}`;
                result.appendChild(mistakesEl);

                const msgEl = document.createElement('p');
                msgEl.className = 'vocab-result-msg';
                if (state.status === 'won') {
                    msgEl.textContent = 'You passed!';
                    msgEl.classList.add('vocab-won');
                } else {
                    msgEl.textContent = 'Not enough correct \u2014 better luck next time.';
                    msgEl.classList.add('vocab-lost');
                }
                result.appendChild(msgEl);

                const scoreEl = document.createElement('p');
                scoreEl.className = 'vocab-mistakes-total';
                scoreEl.textContent = `${correctCount} / ${total} correct on first try`;
                result.appendChild(scoreEl);

                wrapper.appendChild(result);
            }

            // ── Submit a guess ───────────────────────────────────────────────
            async function submitGuess(input) {
                const word = input.value;
                if (!word) return;
                input.value = '';
                input.disabled = true;
                const submitBtn = wrapper.querySelector('.vocab-submit');
                if (submitBtn) submitBtn.disabled = true;

                const response = await apiFetch('guess', { word });

                if (response.state.last_result && !response.state.last_result.correct) {
                    // Wrong answer: pause and show the correct word before advancing
                    renderReveal(response.state.last_result.correct_answer, response.state, word);
                } else {
                    render(response.state);
                    if (response.result === 'win') {
                        gameOver = true;
                        await apiFetch('win', {});
                    } else if (response.result === 'lose') {
                        gameOver = true;
                        await apiFetch('lose', {});
                    }
                }
            }

            // ── Load initial state ───────────────────────────────────────────
            apiFetch('state', {}).then(response => {
                render(response.state);
                if (response.state.status === 'won' || response.state.status === 'lost') {
                    gameOver = true;
                }
            });
        },
    };

    GameFramework.registerPlugin(VocabularyPlugin);
})();
