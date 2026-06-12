(function () {
    const WatchingPlugin = {
        id: 'watching',
        name: 'Are you watching?',

        init(container, instanceId, apiFetch) {
            apiFetch('state', {}).then(response => {
                const state = response.state;
                if (state.status === 'playing' && state.video && state.config) {
                    this._renderPlaying(container, apiFetch, state.video, state.config, state.start_time || 0);
                } else if (state.status === 'segment_selecting' && state.video && state.config) {
                    this._renderSegmentPicker(container, apiFetch, state, state.config,
                        () => this._renderSelecting(container, apiFetch));
                } else {
                    this._renderSelecting(container, apiFetch);
                }
            });
        },

        _renderSelecting(container, apiFetch) {
            container.innerHTML = '';

            const outerDiv = document.createElement('div');
            outerDiv.className = 'watching-game-layout';
            container.appendChild(outerDiv);

            apiFetch('list_videos', {}).then(response => {
                const state = response.state;
                const videos = state.videos || [];   // plain filenames
                const config = state.config || {};

                const title = document.createElement('p');
                title.className = 'watching-select-title';
                title.textContent = 'Select a video to watch:';
                outerDiv.appendChild(title);

                if (videos.length === 0) {
                    const msg = document.createElement('p');
                    msg.className = 'watching-no-videos';
                    const videosDir = (config.videos_dir || 'games/watching/videos') + '/';
                    msg.textContent = `No videos found in ${videosDir}. Add a video file to get started or configure VIDEO_DIR in the config file.`;
                    outerDiv.appendChild(msg);
                    return;
                }

                const list = document.createElement('ul');
                list.className = 'watching-select-list';

                videos.forEach(filename => {
                    const li = document.createElement('li');
                    const btn = document.createElement('button');
                    btn.textContent = filename;
                    btn.addEventListener('click', () => {
                        btn.disabled = true;
                        // select_video probes duration server-side and returns either
                        // "segment_selecting" (slider needed) or "playing" (no slider).
                        apiFetch('select_video', { video: filename }).then(response => {
                            if (response.state.status === 'segment_selecting') {
                                this._renderSegmentPicker(
                                    container, apiFetch, response.state, config,
                                    () => this._renderSelecting(container, apiFetch)
                                );
                            } else {
                                this._renderPlaying(container, apiFetch, filename, config, 0);
                            }
                        });
                    });
                    li.appendChild(btn);
                    list.appendChild(li);
                });

                outerDiv.appendChild(list);
            });
        },

        _renderSegmentPicker(container, apiFetch, state, config, onBack) {
            const videoName = state.video;
            const videoDuration = state.video_duration;
            const segmentDuration = config.segment_duration;
            const maxStart = Math.max(0, Math.floor(videoDuration - segmentDuration));

            container.innerHTML = '';
            const outerDiv = document.createElement('div');
            outerDiv.className = 'watching-game-layout';
            container.appendChild(outerDiv);

            // Header
            const header = document.createElement('div');
            header.className = 'watching-segment-header';

            const backBtn = document.createElement('button');
            backBtn.className = 'watching-segment-back';
            backBtn.textContent = '← Back';
            backBtn.addEventListener('click', onBack);
            header.appendChild(backBtn);

            const titleEl = document.createElement('p');
            titleEl.className = 'watching-segment-title';
            titleEl.textContent = `Choose your segment — ${videoName}`;
            header.appendChild(titleEl);
            outerDiv.appendChild(header);

            // Slider + end-marker overlay
            const trackWrap = document.createElement('div');
            trackWrap.className = 'watching-segment-track-wrap';

            const slider = document.createElement('input');
            slider.type = 'range';
            slider.className = 'watching-segment-slider';
            slider.min = 0;
            slider.max = Math.ceil(videoDuration); // full timeline so end marker stays in bounds
            slider.step = 1;
            slider.value = 0;
            trackWrap.appendChild(slider);

            const endMarker = document.createElement('div');
            endMarker.className = 'watching-segment-end-marker';
            trackWrap.appendChild(endMarker);

            outerDiv.appendChild(trackWrap);

            // Time label
            const label = document.createElement('div');
            label.className = 'watching-segment-label';
            outerDiv.appendChild(label);

            // Frame preview
            const thumb = document.createElement('img');
            thumb.className = 'watching-segment-thumb';
            thumb.alt = 'Frame preview';
            outerDiv.appendChild(thumb);

            // Watch button
            const watchBtn = document.createElement('button');
            watchBtn.className = 'watching-segment-watch';
            watchBtn.textContent = 'Watch this segment';
            outerDiv.appendChild(watchBtn);

            function formatTime(secs) {
                const h = Math.floor(secs / 3600);
                const m = Math.floor((secs % 3600) / 60);
                const s = Math.floor(secs % 60);
                return [h, m, s].map(n => String(n).padStart(2, '0')).join(':');
            }

            function getStartTime() {
                return Math.min(parseInt(slider.value, 10), maxStart);
            }

            function updateLabel() {
                const start = getStartTime();
                const end = start + segmentDuration;
                label.textContent = `${formatTime(start)} — ${formatTime(end)}`;
            }

            function updateEndMarker() {
                const start = getStartTime();
                const pct = ((start + segmentDuration) / videoDuration) * 100;
                endMarker.style.left = pct + '%';
            }

            let thumbDebounce = null;
            function scheduleThumb() {
                clearTimeout(thumbDebounce);
                thumbDebounce = setTimeout(() => {
                    const t = getStartTime();
                    thumb.src = '/games/watching/thumbnail/' + encodeURIComponent(videoName) + '?t=' + t;
                }, 300);
            }

            // Initial state
            updateLabel();
            updateEndMarker();
            thumb.src = '/games/watching/thumbnail/' + encodeURIComponent(videoName) + '?t=0';

            slider.addEventListener('input', () => {
                // Clamp: slider.max = videoDuration but valid start range is [0, maxStart]
                if (parseInt(slider.value, 10) > maxStart) slider.value = maxStart;
                updateLabel();
                updateEndMarker();
                scheduleThumb();
            });

            watchBtn.addEventListener('click', () => {
                const startTime = getStartTime();
                watchBtn.disabled = true;
                backBtn.disabled = true;
                watchBtn.textContent = 'Loading…';
                apiFetch('select_video', { video: videoName, start_time: startTime }).then(() => {
                    this._renderPlaying(container, apiFetch, videoName, config, startTime);
                });
            });
        },

        _renderPlaying(container, apiFetch, videoName, config, startTime) {
            const minInterval = (config.min_interval || 10) * 1000;
            const maxInterval = (config.max_interval || 20) * 1000;
            const dotTimeout  = (config.dot_timeout  || 3)  * 1000;

            container.innerHTML = '';

            const outerDiv = document.createElement('div');
            outerDiv.className = 'watching-game-layout';

            // ── Video container ──────────────────────────────────────────────
            const wrapper = document.createElement('div');
            wrapper.className = 'watching-container';

            const video = document.createElement('video');
            video.className = 'watching-video';
            const segDuration = config.segment_duration || 0;
            const params = new URLSearchParams();
            if (startTime > 0) params.set('start', startTime);
            if (segDuration > 0) params.set('duration', segDuration);
            const qs = params.toString();
            video.src = '/games/watching/videos/' + encodeURIComponent(videoName) + (qs ? '?' + qs : '');
            video.disablePictureInPicture = true;
            // No controls — seek bar is intentionally hidden
            wrapper.appendChild(video);

            const overlay = document.createElement('div');
            overlay.className = 'watching-overlay';
            wrapper.appendChild(overlay);

            outerDiv.appendChild(wrapper);

            // ── Controls ─────────────────────────────────────────────────────
            const controls = document.createElement('div');
            controls.className = 'watching-controls';

            const playPauseBtn = document.createElement('button');
            playPauseBtn.className = 'watching-playpause';
            playPauseBtn.textContent = 'Pause';
            playPauseBtn.addEventListener('click', () => {
                if (video.paused) {
                    video.play();
                } else {
                    video.pause();
                }
            });
            controls.appendChild(playPauseBtn);

            const fullscreenBtn = document.createElement('button');
            fullscreenBtn.className = 'watching-fullscreen';
            fullscreenBtn.textContent = 'Fullscreen';
            fullscreenBtn.addEventListener('click', () => {
                if (document.fullscreenElement === wrapper) {
                    document.exitFullscreen();
                } else {
                    wrapper.requestFullscreen();
                }
            });
            controls.appendChild(fullscreenBtn);

            // ── Keyboard shortcuts ───────────────────────────────────────────
            function handleKeydown(e) {
                if (gameOver) return;
                if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
                if (e.key === ' ') {
                    e.preventDefault();
                    if (video.paused) { video.play(); } else { video.pause(); }
                } else if (e.key === 'f' || e.key === 'F') {
                    if (document.fullscreenElement === wrapper) {
                        document.exitFullscreen();
                    } else {
                        wrapper.requestFullscreen();
                    }
                } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                    e.preventDefault(); // block browser-native seeking via arrow keys
                }
            }
            document.addEventListener('keydown', handleKeydown);

            // ── Status display ───────────────────────────────────────────────
            const statusEl = document.createElement('div');
            statusEl.className = 'watching-status';

            // Append in visual order: video → status → controls
            outerDiv.appendChild(statusEl);
            outerDiv.appendChild(controls);
            container.appendChild(outerDiv);

            // ── Cleanup beacon on tab close / navigation ─────────────────────
            const stopBeacon = () => navigator.sendBeacon('/games/watching/stream/stop');
            window.addEventListener('beforeunload', stopBeacon);

            // ── Game state ───────────────────────────────────────────────────
            let hits = 0;
            let misses = 0;
            let gameOver = false;
            let nextDotTimer = null;
            let activeDotEl = null;
            let activeDotTimeoutHandle = null;
            let activeDotTimeRemaining = 0;
            let dotTimeoutEnd = 0;

            function updateStatus() {
                statusEl.textContent = `Dots: ${hits} hit, ${misses} missed`;
            }
            updateStatus();

            function randomInterval() {
                return Math.floor(Math.random() * (maxInterval - minInterval + 1)) + minInterval;
            }

            function scheduleNextDot() {
                if (gameOver || video.ended) return;
                nextDotTimer = setTimeout(spawnDot, randomInterval());
            }

            function spawnDot() {
                if (gameOver || video.ended || video.paused) return;

                // Random position: 5%–95% both axes so dot stays within overlay
                const left = 5 + Math.random() * 90;
                const top  = 5 + Math.random() * 90;

                const dot = document.createElement('div');
                dot.className = 'watching-dot';
                dot.style.left = left + '%';
                dot.style.top  = top  + '%';
                overlay.appendChild(dot);
                activeDotEl = dot;

                dotTimeoutEnd = Date.now() + dotTimeout;
                activeDotTimeoutHandle = setTimeout(missDot, dotTimeout);

                dot.addEventListener('click', () => {
                    if (dot !== activeDotEl) return; // stale reference guard
                    clearTimeout(activeDotTimeoutHandle);
                    activeDotTimeoutHandle = null;
                    activeDotEl = null;
                    dot.remove();
                    hits++;
                    updateStatus();
                    if (!video.paused) {
                        scheduleNextDot();
                    }
                });
            }

            function missDot() {
                activeDotTimeoutHandle = null;
                if (activeDotEl) {
                    activeDotEl.remove();
                    activeDotEl = null;
                }
                misses++;
                updateStatus();
                if (!video.paused) {
                    scheduleNextDot();
                }
            }

            // ── Cheat detection helpers ──────────────────────────────────────
            function cheat(reason) {
                if (gameOver) return;
                gameOver = true;
                document.removeEventListener('keydown', handleKeydown);
                clearTimeout(nextDotTimer);
                clearTimeout(activeDotTimeoutHandle);
                if (activeDotEl) { activeDotEl.remove(); activeDotEl = null; }
                statusEl.textContent = `Cheating detected — ${reason}.`;
                statusEl.className = 'watching-status cheat';
                playPauseBtn.disabled = true;
                fullscreenBtn.disabled = true;
                window.removeEventListener('beforeunload', stopBeacon);
                apiFetch('lose', {}).then(() => {
                    document.getElementById('game-end-buttons')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                });
            }

            // ── PiP prevention ───────────────────────────────────────────────
            video.addEventListener('enterpictureinpicture', () => cheat('picture-in-picture is not allowed'));

            // ── Seek prevention ──────────────────────────────────────────────
            // The 'seeking' event fires on normal stream re-buffering, causing false
            // positives after ~10 min. Use 'seeked' with a currentTime comparison
            // to only flag actual backward seeks.
            let lastTimeUpdate = 0;
            video.addEventListener('timeupdate', () => { lastTimeUpdate = video.currentTime; });
            video.addEventListener('seeked', () => {
                if (gameOver) return;
                if (video.currentTime < lastTimeUpdate - 0.5) {
                    cheat('seeking is not allowed');
                }
            });

            // ── Speed change prevention ──────────────────────────────────────
            video.addEventListener('ratechange', () => {
                if (video.playbackRate !== 1) cheat('changing the playback speed is not allowed');
            });

            // ── Context menu suppression ─────────────────────────────────────
            video.addEventListener('contextmenu', e => e.preventDefault());

            // ── Fullscreen: native fullscreen on the video element is cheating;
            //   our button requests fullscreen on the wrapper (overlay stays visible)
            document.addEventListener('fullscreenchange', () => {
                if (document.fullscreenElement === video) {
                    cheat('use the Fullscreen button instead');
                } else {
                    wrapper.classList.toggle('is-fullscreen', document.fullscreenElement === wrapper);
                }
            });

            // ── Single-click to toggle play/pause ────────────────────────────
            // Uses a short delay so a double-click can cancel the pending action.
            // Listener is on the wrapper (overlay has pointer-events:none).
            let clickTimer = null;
            wrapper.addEventListener('click', (e) => {
                if (gameOver) return;
                if (e.target.classList.contains('watching-dot')) return; // ignore dot clicks
                clearTimeout(clickTimer);
                clickTimer = setTimeout(() => {
                    if (video.paused) { video.play(); } else { video.pause(); }
                }, 220);
            });

            // ── Double-click to toggle fullscreen ────────────────────────────
            wrapper.addEventListener('dblclick', () => {
                if (gameOver) return;
                clearTimeout(clickTimer); // cancel pending single-click
                if (document.fullscreenElement === wrapper) {
                    document.exitFullscreen();
                } else {
                    wrapper.requestFullscreen();
                }
            });

            // ── Pause / resume ───────────────────────────────────────────────
            video.addEventListener('pause', () => {
                if (gameOver) return;
                playPauseBtn.textContent = 'Play';
                clearTimeout(nextDotTimer);
                nextDotTimer = null;
                // Freeze active dot countdown
                if (activeDotEl && activeDotTimeoutHandle !== null) {
                    clearTimeout(activeDotTimeoutHandle);
                    activeDotTimeoutHandle = null;
                    activeDotTimeRemaining = Math.max(0, dotTimeoutEnd - Date.now());
                }
            });

            video.addEventListener('play', () => {
                if (gameOver) return;
                playPauseBtn.textContent = 'Pause';
                if (activeDotEl) {
                    // Resume frozen dot countdown
                    dotTimeoutEnd = Date.now() + activeDotTimeRemaining;
                    activeDotTimeoutHandle = setTimeout(missDot, activeDotTimeRemaining);
                } else {
                    scheduleNextDot();
                }
            });

            // ── Video end ────────────────────────────────────────────────────
            video.addEventListener('ended', () => {
                if (gameOver) return;
                gameOver = true;
                document.removeEventListener('keydown', handleKeydown);
                clearTimeout(nextDotTimer);
                clearTimeout(activeDotTimeoutHandle);
                if (activeDotEl) { activeDotEl.remove(); activeDotEl = null; }
                playPauseBtn.disabled = true;
                window.removeEventListener('beforeunload', stopBeacon);

                apiFetch('report', { hits, misses }).then(response => {
                    const scrollToEndButtons = () => {
                        document.getElementById('game-end-buttons')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    };
                    if (response.result === 'win') {
                        statusEl.textContent = `You won! ${hits} hit, ${misses} missed.`;
                        statusEl.classList.add('win');
                        apiFetch('win', {}).then(scrollToEndButtons);
                    } else {
                        statusEl.textContent = `You lost. ${hits} hit, ${misses} missed.`;
                        statusEl.classList.add('lose');
                        apiFetch('lose', {}).then(scrollToEndButtons);
                    }
                });
            });

            // Start playback
            video.play().catch(() => {
                // Autoplay blocked — update button so user can start manually
                playPauseBtn.textContent = 'Play';
            });
        },
    };

    GameFramework.registerPlugin(WatchingPlugin);
})();
