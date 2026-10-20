OPENING = '<section id="tab-overview" class="active">'

SECTION = r"""
    <div class="banner" id="auditBanner" style="display:none">[[overview.text.1]]</div>
    <div class="banner bad" id="healthBanner" style="display:none"></div>
    <div class="card">
      <h2>[[overview.heading.1]]</h2>
      <div class="clock" id="clock">[[overview.text.2]]</div>
      <div class="muted" id="clockDetail">[[overview.text.3]]</div>

      <h3>[[overview.heading.2]]</h3>
      <div class="modes" id="modes">
        <button data-mode="pomodoro">[[overview.button.1]]</button>
        <button data-mode="stopwatch">[[overview.button.2]]</button>
      </div>
      <div class="muted">[[overview.text.4]]</div>

      <div class="durations">
        <label id="labWork">[[overview.label.1]]<input type="number" id="workMinutes" min="1" max="600" step="1" />
        </label>
        <label id="labBreak">[[overview.label.2]]<input type="number" id="breakMinutes" min="1" max="120" step="1" />
        </label>
        <label id="labCycles">[[overview.label.3]]<input type="number" id="cycles" min="0" max="99" step="1" />
        </label>
        <button class="action primary" id="startBtn" onclick="startSessionFromForm()">[[overview.button.3]]</button>
      </div>
      <div class="presets" id="presets" style="margin-top:10px">
        <button data-preset="15">[[overview.button.4]]</button>
        <button data-preset="25">[[overview.button.5]]</button>
        <button data-preset="45">[[overview.button.6]]</button>
        <button data-preset="60">[[overview.button.7]]</button>
        <button data-preset="90">[[overview.button.8]]</button>
      </div>

      <div class="row" style="margin-top:12px">
        <button class="action" id="extendBtn" onclick="extendSession(5)">[[overview.button.9]]</button>
        <button class="action" id="skipBtn" onclick="skipBreak()">[[overview.button.10]]</button>
        <button class="action" id="pauseCardButton" onclick="pauseFromSettings()">[[overview.button.11]]</button>
        <button class="action danger" onclick="stopSession()">[[overview.button.12]]</button>
      </div>
    </div>

    <div class="card">
      <h2>[[overview.heading.3]]</h2>
      <div class="grid">
        <div><div class="muted">[[overview.text.5]]</div><div id="ov-state">[[overview.text.6]]</div></div>
        <div><div class="muted">[[overview.text.7]]</div><div id="ov-who">—</div></div>
        <div><div class="muted">[[overview.text.8]]</div><div id="ov-killed">0</div></div>
        <div><div class="muted">[[overview.text.9]]</div><div id="ov-since">—</div></div>
        <div><div class="muted">[[overview.text.10]]</div><div id="ov-mode">—</div></div>
        <div><div class="muted">[[overview.text.11]]</div><div id="ov-phase">—</div></div>
      </div>
    </div>
    <div class="card">
      <h2>[[overview.heading.4]]</h2>
      <ul id="recentList"></ul>
      <div class="muted" id="recentEmpty">[[overview.text.12]]</div>
    </div>
    <div class="card">
      <h2>[[overview.heading.5]]</h2>
      <div class="grid">
        <div><div class="muted">cgroup v2</div><div id="env-cgroup">—</div></div>
        <div><div class="muted">[[overview.text.13]]</div><div id="env-state" class="muted"></div></div>
        <div><div class="muted">[[overview.text.14]]</div><div id="env-protect">—</div></div>
        <div><div class="muted">[[overview.text.15]]</div><div id="env-self" class="muted"></div></div>
      </div>
    </div>
    <div class="card">
      <h2>[[overview.heading.6]]</h2>
      <pre id="ov-events">—</pre>
    </div>
  
"""

SCRIPT = r"""
let sessionMode = 'pomodoro';

/** The configured session values, with sane fallbacks. */

function sessionDefaults() {
  const session = (config && config.session) || {};
  return {
    mode: session.mode === 'stopwatch' ? 'stopwatch' : 'pomodoro',
    work: session.pomodoro_work_minutes || session.default_minutes || 25,
    rest: session.pomodoro_break_minutes || 5,
    cycles: session.pomodoro_cycles === undefined ? 4 : session.pomodoro_cycles,
    pause: session.pause_minutes || 10,
  };
}

function selectMode(mode) {
  sessionMode = mode;
  document.querySelectorAll('#modes button').forEach((button) => {
    button.classList.toggle('active', button.dataset.mode === mode);
  });
  // Each mode owns different numbers, so show only the ones that matter.
  $('labBreak').style.display = mode === 'pomodoro' ? 'flex' : 'none';
  $('labCycles').style.display = mode === 'pomodoro' ? 'flex' : 'none';
  $('labWork').firstChild.textContent =
    mode === 'stopwatch' ? T('overview.message.65') : T('overview.message.64');
  $('workMinutes').disabled = mode === 'stopwatch';
  $('startBtn').textContent = mode === 'stopwatch' ? T('overview.message.63') : T('overview.message.62');
}

function setWork(minutes) {
  $('workMinutes').value = String(minutes);
}

function startSessionFromForm() {
  if (sessionMode === 'stopwatch') {
    post('/focus/session', { mode: 'stopwatch' });
    return;
  }
  const work = Number($('workMinutes').value);
  if (!Number.isFinite(work) || work <= 0) {
    note('clockDetail', T('overview.toast.1'));
    return;
  }
  post('/focus/session', {
    mode: 'pomodoro',
    work_minutes: work,
    break_minutes: Number($('breakMinutes').value) || 5,
    cycles: Number($('cycles').value) || 0,
  });
}

function stopSession() { post('/focus/session', { stop: true }); }

function extendSession(minutes) { post('/focus/session', { extend_minutes: minutes }); }

function skipBreak() { post('/focus/session', { skip_break: true }); }

function pauseFor(seconds) { post('/focus/pause', { seconds }); }

/** Everything the header offers is the configured values, never a fixed number. */

function startPomodoroFromSettings() {
  const defaults = sessionDefaults();
  post('/focus/session', {
    mode: 'pomodoro',
    work_minutes: defaults.work,
    break_minutes: defaults.rest,
    cycles: defaults.cycles,
  });
}

function startStopwatch() { post('/focus/session', { mode: 'stopwatch' }); }

function pauseFromSettings() {
  pauseFor(sessionDefaults().pause * 60);
}

function applyPreset(minutes) {
  selectMode('pomodoro');
  setWork(minutes);
  startSessionFromForm();
}

/** Keep the header honest: its labels are the numbers it will actually use. */

function renderHeaderActions() {
  const defaults = sessionDefaults();
  const pomodoro = $('startPomodoro');
  const stopwatch = $('startStopwatch');
  const rest = defaults.cycles === 0
    ? T('overview.message.61')
    : (defaults.cycles === 1 ? T('overview.message.60') : ' · ' + defaults.cycles + T('overview.message.59'));
  pomodoro.textContent = T('overview.message.58') + defaults.work + T('overview.message.57') + rest;
  pomodoro.title = T('overview.message.56') + defaults.work + T('overview.message.55') + defaults.rest + T('overview.message.54');
  stopwatch.title = T('overview.message.53');
  pomodoro.classList.toggle('primary', defaults.mode === 'pomodoro');
  stopwatch.classList.toggle('primary', defaults.mode === 'stopwatch');
  $('pauseButton').textContent = T('overview.message.52') + defaults.pause + T('overview.message.51');
  $('pauseCardButton').textContent = T('overview.message.50') + defaults.pause + T('overview.message.49');
}

function tickClock() {
  if (!state) return;
  renderSessionClock();
}

function formatClock(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  const pad = (value) => String(value).padStart(2, '0');
  return hours ? hours + ':' + pad(minutes) + ':' + pad(rest) : pad(minutes) + ':' + pad(rest);
}

function renderSessionClock() {
  const clock = $('clock');
  const detail = $('clockDetail');
  const session = (state && state.session_state) || {};
  const running = !!(state && state.manual_session);
  const now = Date.now() / 1000;

  $('extendBtn').disabled = !running || session.mode === 'stopwatch';
  $('skipBtn').disabled = !running || session.phase !== 'break';

  if (!running) {
    clock.textContent = state && state.active ? T('overview.message.48') : T('overview.message.47');
    detail.textContent = state && state.active
      ? T('overview.message.46') + (state.delegated_source || T('overview.message.45'))
      : T('overview.message.44');
    return;
  }

  if (session.mode === 'stopwatch') {
    const elapsed = Math.max(0, now - (session.started_at || now));
    clock.innerHTML = formatClock(elapsed) + T('overview.message.43');
    detail.textContent = T('overview.message.42') +
      new Date(session.started_at * 1000).toLocaleTimeString();
    return;
  }

  if (session.mode === 'pomodoro') {
    const remaining = Math.max(0, (session.phase_ends_at || 0) - now);
    clock.innerHTML = formatClock(remaining) +
      ' <small>' + (session.phase === 'break' ? T('overview.message.41') : T('overview.message.40')) + '</small>';
    detail.textContent = T('overview.message.39') + session.cycle + ' / ' + (session.cycles || '∞') +
      T('overview.message.38') + session.work_minutes + T('overview.message.37') +
      session.break_minutes + T('overview.message.36');
    return;
  }

  const remaining = Math.max(0, (session.until || 0) - now);
  clock.innerHTML = formatClock(remaining) + T('overview.message.35');
  detail.textContent = T('overview.message.34') +
    Math.round((session.until - session.started_at) / 60) + T('overview.message.33');
}

function renderHealth() {
  const health = state.health || {};
  const banner = $('healthBanner');
  if (!banner) return;
  const problems = [];
  if (health.degraded) {
    problems.push(T('overview.message.32') + health.scan_failures + T('overview.message.31') +
      (health.last_scan_error || T('overview.message.30')));
  }
  if (health.kill_failures) {
    problems.push(T('overview.message.29') + health.kill_failures + T('overview.message.28'));
  }
  if (health.respawn_storm && health.respawn_storm.length) {
    problems.push(T('overview.message.27') + health.respawn_storm.join(', '));
  }
  if (health.enforcement_enabled === false) problems.push(T('overview.message.26'));
  const scan = state.scan_ms || {};
  if (scan.p95 && scan.p95 > 50) problems.push(T('overview.message.25') + scan.p95 + ' ms');
  if (!problems.length) {
    banner.style.display = 'none';
    return;
  }
  banner.textContent = T('overview.message.24') + problems.join(' · ');
  banner.style.display = 'block';
}

function renderOverview() {
  const dot = $('dot');
  const paused = state.pause_until > Date.now() / 1000;
  const enforcement = state.enforcement || {};
  const audit = enforcement.mode === 'audit';
  const active = state.active ? (paused ? T('overview.message.23') : T('overview.message.22')) : T('overview.message.21');

  dot.className = 'dot' + (state.active ? (paused ? ' paused' : ' on') : '');
  show('state', active);
  show('timer', state.active && state.session_since
    ? T('overview.message.20') + new Date(state.session_since * 1000).toLocaleTimeString() : '');
  show('killedSummary', state.killed_total
    ? T('overview.message.19') + state.killed_total + T('overview.message.18') : T('overview.message.17'));
  $('auditBanner').style.display = audit ? 'block' : 'none';
  renderHealth();

  const who = [];
  if (state.manual_session) who.push(T('overview.message.16'));
  if (state.delegated_active || state.delegated) {
    who.push(T('overview.message.15') + (state.delegated_source || T('overview.message.14')));
  }
  show('ov-state', active);
  show('ov-who', who.join(' · ') || '—');
  show('ov-killed', String(state.killed_total || 0));
  show('ov-since', state.session_since
    ? new Date(state.session_since * 1000).toLocaleString() : '—');
  const modeNames = { countdown: T('overview.message.13'), pomodoro: T('overview.message.12'), stopwatch: T('overview.message.11') };
  show('ov-mode', modeNames[state.session_mode] || '—');
  show('ov-phase', state.session_phase === 'break'
    ? T('overview.message.10') : (state.session_mode ? T('overview.message.9') : '—'));

  show('env-mode', enforcement.enabled === false
    ? T('overview.message.8') : (audit ? T('overview.message.7') : T('overview.message.6')));
  const report = state.scan_report || {};
  show('env-scan', T('overview.message.5') + (report.killed || 0) + T('overview.message.4') + (report.denied || 0) +
    T('overview.message.3') + (report.throttled || 0));
  show('env-protect', (state.protect_count || 0) + T('overview.message.2'));
  show('env-scans', String((state.stats && state.stats.scans) || 0));

  const list = $('recentList');
  list.innerHTML = '';
  for (const item of state.recent || []) {
    const li = document.createElement('li');
    li.textContent = item.name + ' × ' + item.count + T('overview.message.1') +
      new Date((item.last_at || 0) * 1000).toLocaleTimeString();
    list.appendChild(li);
  }
  $('recentEmpty').style.display = (state.recent || []).length ? 'none' : 'block';
}

let formSeeded = false;

function seedSessionForm() {
  if (formSeeded) return;
  formSeeded = true;
  const defaults = sessionDefaults();
  selectMode(defaults.mode);
  // Only ever seeded once: after that the form belongs to the user, and the
  // three-second refresh must not touch it.
  $('workMinutes').value = defaults.work;
  $('breakMinutes').value = defaults.rest;
  $('cycles').value = defaults.cycles;
  document.querySelectorAll('#presets button').forEach((button) => {
    // A preset is a one-click "start this long, one round".
    button.onclick = () => applyPreset(Number(button.dataset.preset));
  });
}
"""
