OPENING = '<section id="tab-settings">'

SECTION = r"""
    <div class="card">
      <h2>[[settings.heading.1]]</h2>
      <div class="row"><label>[[settings.mode.label]]<select id="setMode"><option value="audit">[[settings.mode.audit]]</option><option value="kill">[[settings.mode.kill]]</option></select></label></div>
      <div class="row" style="margin-top:10px">
        <label style="flex:1">[[settings.label.1]]<select id="setSessionMode">
            <option value="pomodoro">[[settings.option.1]]</option>
            <option value="stopwatch">[[settings.option.2]]</option>
          </select></label>
        <label style="flex:1">[[settings.label.2]]<input type="number" id="setPauseMinutes" min="1" max="600" /></label>
      </div>
      <div class="row" style="margin-top:10px">
        <label style="flex:1">[[settings.label.3]]<input type="number" id="setPomodoroWork" min="1" max="180" /></label>
        <label style="flex:1">[[settings.label.4]]<input type="number" id="setPomodoroBreak" min="1" max="60" /></label>
        <label style="flex:1">[[settings.label.5]]<input type="number" id="setPomodoroCycles" min="0" max="99" /></label>
        <label style="flex:1">[[settings.label.6]]<input type="number" id="setScanInterval" min="0.1" max="60" step="0.1" /></label>
      </div>
      <div class="row" style="margin-top:10px">
        <label style="flex:1">[[settings.label.7]]<input type="password" id="setToken" placeholder="[[settings.placeholder.1]]" /></label>
      </div>
      <div class="row" style="margin-top:12px">
        <button class="action primary" onclick="saveSettings()">[[settings.button.1]]</button>
        <span class="muted" id="settingsResult"></span>
      </div>
    </div>
    <div class="card">
      <h2>[[settings.heading.2]]</h2>
      <pre id="settingsInfo">—</pre>
    </div>
  
"""

SCRIPT = r"""
const dirtyFields = new Set();

const SETTINGS_FIELDS = [
  'setMode', 'setSessionMode', 'setPauseMinutes', 'setPomodoroWork',
  'setPomodoroBreak', 'setPomodoroCycles', 'setScanInterval', 'setToken',
];

function watchSettingsFields() {
  for (const id of SETTINGS_FIELDS) {
    const element = $(id);
    if (!element) continue;
    const mark = () => markDirty(id);
    element.addEventListener('input', mark);
    element.addEventListener('change', mark);
  }
}

function renderSettings() {
  setFieldValue('setMode', (config.enforcement || {}).mode || 'audit');
  // "countdown" is one round of pomodoro by another name, so that is how it shows.
  setFieldValue('setSessionMode',
    config.session.mode === 'stopwatch' ? 'stopwatch' : 'pomodoro');
  setFieldValue('setPauseMinutes', config.session.pause_minutes);
  setFieldValue('setPomodoroWork', config.session.pomodoro_work_minutes);
  setFieldValue('setPomodoroBreak', config.session.pomodoro_break_minutes);
  setFieldValue('setPomodoroCycles', config.session.pomodoro_cycles);
  setFieldValue('setScanInterval', config.scan_interval);
  // The token is write-only: it is never filled back in, dirty or not.
  $('pluginsPath').textContent = config.plugins_path || '~/.config/focused/plugins/';
  $('settingsInfo').textContent = JSON.stringify({
    config_path: config.path,
    http: config.http,
    enforcement: config.enforcement,
    protected_count: config.protected_count,
  }, null, 2);
}

async function saveSettings() {
  const patch = {
    enforcement: { mode: $('setMode').value },
    scan_interval: Number($('setScanInterval').value),
    session: {
      mode: $('setSessionMode').value,
      pause_minutes: Number($('setPauseMinutes').value),
      pomodoro_work_minutes: Number($('setPomodoroWork').value),
      pomodoro_break_minutes: Number($('setPomodoroBreak').value),
      pomodoro_cycles: Number($('setPomodoroCycles').value),
    },
  };
  if (patch.enforcement.mode === 'kill') patch.confirm = 'kill';
  const token = $('setToken').value;
  if (token) patch.http = { token };
  try {
    config = await api('/config', 'PATCH', patch);
    $('setToken').value = '';
    for (const id of SETTINGS_FIELDS) {
      dirtyFields.delete(id);
      const element = $(id);
      if (element) delete element.dataset.dirty;
    }
    const warnings = config.warnings || [];
    show('settingsResult', warnings.length ? warnings.join(' · ') : T('settings.message.2'));
  } catch (err) {
    // Keep the marks: the edits are still unsaved, and the next refresh must not
    // wipe them just because the save failed.
    show('settingsResult', T('settings.message.1') + err.message);
  }
  await refreshAll();
}
"""
