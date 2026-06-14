/**
 * proofofwork.js — Proof of Work frontend plugin.
 *
 * Three views:
 *   selecting  — task picker grid (mirrors main game menu style)
 *   active     — task display + upload form
 *   result     — LLM feedback + time delta (won/lost)
 */

(function () {
    const ProofOfWorkPlugin = {
        id: 'proofofwork',
        name: 'Proof of Work',

        init(container, instanceId, apiFetch) {
            let gameState = null;

            // ── Helpers ──────────────────────────────────────────────────────

            function escHtml(s) {
                return String(s)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;');
            }

            function fmtSeconds(secs) {
                const abs = Math.abs(secs);
                const m = Math.floor(abs / 60);
                const s = abs % 60;
                if (m > 0 && s > 0) return `${m}m ${s}s`;
                if (m > 0) return `${m}m`;
                return `${s}s`;
            }

            function fmtMins(secs) {
                return Math.round(secs / 60) + 'm';
            }

            // ── Render dispatcher ────────────────────────────────────────────

            function render(state) {
                gameState = state;
                if (state.status === 'selecting') {
                    renderSelecting(state);
                } else if (state.status === 'active' || state.status === 'evaluating') {
                    renderActive(state);
                } else if (state.status === 'won' || state.status === 'lost') {
                    renderResult(state);
                }
            }

            // ── View 1: Task picker ──────────────────────────────────────────

            function renderSelecting(state) {
                container.innerHTML = '<div class="pow-task-list" id="pow-task-list"></div>';
                const list = container.querySelector('#pow-task-list');

                for (const task of state.available_tasks) {
                    const card = document.createElement('div');
                    card.className = 'game-card pow-task-card';
                    card.innerHTML = `
                        <div class="game-card-name">${escHtml(task.subject)}</div>
                        <div class="game-card-desc">${escHtml(task.description)}</div>
                        <div class="game-card-times">
                            <span class="time-reward">up to −${fmtMins(task.max_time_reduction)} on good submission</span>
                            <span class="time-penalty">up to +${fmtMins(task.max_time_addition)} on poor submission</span>
                        </div>
                        <button class="attempt-btn pow-select-btn" data-task-id="${escHtml(task.id)}">
                            Select task
                        </button>
                    `;
                    list.appendChild(card);
                }

                list.addEventListener('click', async (e) => {
                    const btn = e.target.closest('.pow-select-btn');
                    if (!btn) return;
                    btn.disabled = true;
                    btn.textContent = 'Loading…';

                    const response = await apiFetch('select', { task_id: btn.dataset.taskId });
                    if (response.message) {
                        btn.disabled = false;
                        btn.textContent = 'Select task';
                        const card = btn.closest('.pow-task-card');
                        let errEl = card.querySelector('.pow-select-error');
                        if (!errEl) {
                            errEl = document.createElement('div');
                            errEl.className = 'game-message pow-select-error error';
                            card.appendChild(errEl);
                        }
                        errEl.textContent = response.message;
                        return;
                    }
                    render(response.state);
                });
            }

            // ── View 2: Active task + upload form ────────────────────────────

            function renderActive(state) {
                const isEvaluating = state.status === 'evaluating';

                let requirementsHtml = '';
                if (state.requirements && state.requirements.length > 0) {
                    const items = state.requirements.map(r => `<li>${escHtml(r)}</li>`).join('');
                    requirementsHtml = `
                        <div class="pow-requirements">
                            <div class="pow-requirements-label">Before submitting:</div>
                            <ul class="pow-requirements-list">${items}</ul>
                        </div>`;
                }

                let scoredOnHtml = '';
                if (state.criteria && state.criteria.length > 0) {
                    const items = state.criteria.map(c => `<li>${escHtml(c)}</li>`).join('');
                    scoredOnHtml = `
                        <div class="pow-scored-on">
                            <div class="pow-scored-on-label">Scored on:</div>
                            <ol class="pow-scored-on-list">${items}</ol>
                        </div>`;
                }

                let taskHtml = '';
                let questionCounter = 1;
                for (const section of state.tasks) {
                    taskHtml += `<div class="pow-section">`;
                    taskHtml += `<div class="pow-section-title">${escHtml(section.title)}</div>`;

                    if (section.questions && section.questions.length > 0) {
                        taskHtml += `<ol class="pow-questions" start="${questionCounter}">`;
                        for (const q of section.questions) {
                            taskHtml += `<li>${escHtml(q)}</li>`;
                            questionCounter++;
                        }
                        taskHtml += '</ol>';
                    }

                    if (section.generated_content) {
                        const lines = escHtml(section.generated_content)
                            .split('\n')
                            .map(l => `<p>${l || '&nbsp;'}</p>`)
                            .join('');
                        taskHtml += `<div class="pow-generated">${lines}</div>`;
                        questionCounter += section.generate_count || 0;
                    }

                    if (section.player_notes) {
                        taskHtml += `<p class="pow-instructions">${escHtml(section.player_notes)}</p>`;
                    }

                    taskHtml += '</div>';
                }

                container.innerHTML = `
                    <div class="pow-wrapper">
                        <div class="pow-subject">${escHtml(state.subject)}</div>
                        <div class="pow-active-times">
                            <span class="time-reward">up to −${fmtMins(state.max_time_reduction)} on good submission</span>
                            <span class="pow-active-times-sep">·</span>
                            <span class="time-penalty">up to +${fmtMins(state.max_time_addition)} on poor submission</span>
                        </div>

                        ${requirementsHtml}
                        ${scoredOnHtml}
                        <div class="pow-tasks">${taskHtml}</div>

                        <div class="pow-submit-area" id="pow-submit-area">
                            <div class="pow-field">
                                <label class="pow-label" for="pow-text">Written answers</label>
                                <textarea
                                    id="pow-text"
                                    class="pow-textarea"
                                    placeholder="Type your answers here…"
                                    rows="8"
                                ></textarea>
                            </div>

                            <div class="pow-field">
                                <label class="pow-label">
                                    Files (photos, PDFs, text files)
                                </label>
                                <button type="button" class="pow-add-files-btn" id="pow-add-files">
                                    Add files…
                                </button>
                                <input
                                    type="file"
                                    id="pow-files"
                                    class="pow-file-input"
                                    multiple
                                    accept="image/*,application/pdf,text/*,.txt,.py,.js,.java,.c,.cpp,.md"
                                    style="display:none"
                                />
                                <div class="pow-file-list" id="pow-file-list"></div>
                            </div>

                            <button class="attempt-btn pow-submit-btn" id="pow-submit">
                                Submit for evaluation
                            </button>
                            <div class="game-message pow-msg" id="pow-msg"></div>
                        </div>

                        ${isEvaluating ? '<div class="pow-evaluating-overlay" id="pow-overlay">Evaluating your submission…</div>' : ''}

                        <button class="pow-back-btn" id="pow-back"${isEvaluating ? ' disabled' : ''}>← Back</button>
                    </div>
                `;

                const fileInput  = container.querySelector('#pow-files');
                const addBtn     = container.querySelector('#pow-add-files');
                const fileList   = container.querySelector('#pow-file-list');
                const submitBtn  = container.querySelector('#pow-submit');
                const msgEl      = container.querySelector('#pow-msg');

                // Map of filename → File object; preserves insertion order,
                // deduplicates by name (later pick of same name replaces earlier).
                const selectedFiles = new Map();

                function renderFileTags() {
                    fileList.innerHTML = '';
                    for (const [name, file] of selectedFiles) {
                        const tag = document.createElement('span');
                        tag.className = 'pow-file-tag';

                        const label = document.createElement('span');
                        label.textContent = name;

                        const removeBtn = document.createElement('button');
                        removeBtn.type = 'button';
                        removeBtn.className = 'pow-file-remove';
                        removeBtn.textContent = '×';
                        removeBtn.setAttribute('aria-label', `Remove ${name}`);
                        removeBtn.addEventListener('click', () => {
                            selectedFiles.delete(name);
                            renderFileTags();
                        });

                        tag.appendChild(label);
                        tag.appendChild(removeBtn);
                        fileList.appendChild(tag);
                    }
                }

                // "Add files" button opens the hidden input each time
                addBtn.addEventListener('click', () => fileInput.click());

                // Merge newly picked files into the map instead of replacing
                fileInput.addEventListener('change', () => {
                    for (const f of fileInput.files) {
                        selectedFiles.set(f.name, f);
                    }
                    fileInput.value = '';  // reset so the same file can be re-picked
                    renderFileTags();
                });

                const backBtn = container.querySelector('#pow-back');
                backBtn.addEventListener('click', async () => {
                    backBtn.disabled = true;
                    const response = await apiFetch('back', {});
                    render(response.state);
                });

                if (isEvaluating) {
                    submitBtn.disabled = true;
                    addBtn.disabled = true;
                    return;
                }

                submitBtn.addEventListener('click', async () => {
                    submitBtn.disabled = true;
                    submitBtn.textContent = 'Submitting…';
                    msgEl.textContent = '';
                    msgEl.className = 'game-message pow-msg';

                    const maxBytes = state.max_upload_bytes;
                    let totalBytes = 0;
                    for (const f of selectedFiles.values()) {
                        totalBytes += f.size;
                    }
                    if (maxBytes && totalBytes > maxBytes) {
                        const limitMb = Math.floor(maxBytes / (1024 * 1024));
                        const actualMb = (totalBytes / (1024 * 1024)).toFixed(1);
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Submit for evaluation';
                        msgEl.textContent =
                            `Upload too large: ${actualMb} MB exceeds the ${limitMb} MB limit. ` +
                            `Remove or shrink a file and try again.`;
                        msgEl.className = 'game-message pow-msg error';
                        return;
                    }

                    const formData = new FormData();
                    formData.append('text_answers', container.querySelector('#pow-text').value);
                    for (const f of selectedFiles.values()) {
                        formData.append('files[]', f);
                    }

                    // Show evaluating overlay
                    const submitArea = container.querySelector('#pow-submit-area');
                    const overlay = document.createElement('div');
                    overlay.className = 'pow-evaluating-overlay';
                    overlay.textContent = 'Evaluating your submission…';
                    submitArea.appendChild(overlay);

                    let result;
                    try {
                        const resp = await fetch(
                            `/api/game/proofofwork/${instanceId}/upload`,
                            { method: 'POST', body: formData }
                        );
                        result = await resp.json();
                    } catch (err) {
                        overlay.remove();
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Submit for evaluation';
                        msgEl.textContent = 'Network error — please try again.';
                        msgEl.className = 'game-message pow-msg error';
                        return;
                    }

                    if (result.error) {
                        overlay.remove();
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Submit for evaluation';
                        msgEl.textContent = 'Evaluation failed: ' + (result.detail || result.error);
                        msgEl.className = 'game-message pow-msg error';
                        return;
                    }

                    // Update local state and show result
                    gameState = {
                        ...gameState,
                        status: result.status,
                        adjustment_seconds: result.adjustment_seconds,
                        feedback: result.feedback,
                        quality: result.quality,
                    };
                    renderResult(gameState);

                    // Tell the framework to apply win/lose (timer update + post-game buttons)
                    if (result.quality === 'good') {
                        await apiFetch('win', {});
                    } else {
                        await apiFetch('lose', {});
                    }
                });
            }

            // ── View 3: Result ───────────────────────────────────────────────

            function renderResult(state) {
                const isGood = state.quality === 'good';
                const adj = state.adjustment_seconds;  // negative=reduce, positive=add
                const absAdj = Math.abs(adj);

                let timeLine;
                if (isGood) {
                    timeLine = `<span class="pow-time-good">−${fmtSeconds(absAdj)} reduced</span>`;
                } else {
                    timeLine = `<span class="pow-time-bad">+${fmtSeconds(absAdj)} added</span>`;
                }

                container.innerHTML = `
                    <div class="pow-wrapper pow-result">
                        <div class="pow-result-heading ${isGood ? 'pow-result-good' : 'pow-result-bad'}">
                            ${isGood ? 'Submission accepted' : 'Submission rejected'}
                        </div>
                        <div class="pow-time-delta">${timeLine}</div>
                        <div class="pow-feedback">${escHtml(state.feedback || '')}</div>
                    </div>
                `;
            }

            // ── Bootstrap: restore state ─────────────────────────────────────

            apiFetch('state', {}).then(response => {
                render(response.state);
            });
        },
    };

    GameFramework.registerPlugin(ProofOfWorkPlugin);
})();
