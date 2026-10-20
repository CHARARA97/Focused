OPENING = '<section id="tab-apps">'

SECTION = r"""
    <div class="card">
      <h2>[[apps.heading.1]]</h2>
      <div class="modes" id="appsModes">
        <button data-apps-mode="block">[[apps.button.1]]</button>
        <button data-apps-mode="protect">[[apps.button.2]]</button>
      </div>
      <div class="muted" id="appsModeHint"></div>

      <div class="row" style="margin-top:10px">
        <input type="text" id="procSearch" placeholder="[[apps.placeholder.1]]" oninput="renderProcesses()" />
        <input type="text" id="newEntry" placeholder="[[apps.placeholder.2]]" />
        <button class="action" onclick="addEntry()">[[apps.button.3]]</button>
      </div>
      <div class="muted" id="appsFeedback"></div>
      <div class="proc" id="procList"></div>
      <div class="muted" id="appsSummary"></div>

      <details class="fold" id="foldNames">
        <summary>[[apps.summary.1]]<span id="namesCount">0</span>）</summary>
        <ul id="nameList"></ul>
      </details>

      <details class="fold" id="foldCmdline">
        <summary>[[apps.summary.2]]<span id="cmdlineCount">0</span>）</summary>
        <div class="row">
          <input type="text" id="newCmdline" placeholder="[[apps.placeholder.3]]" />
          <button class="action" onclick="addCmdline()">[[apps.button.4]]</button>
        </div>
        <ul id="cmdlineList"></ul>
      </details>

      <details class="fold" id="foldProtect">
        <summary>[[apps.summary.3]]<span id="protectCount">0</span>[[apps.text.1]]</summary>
        <div class="muted" id="protectHint"></div>
        <ul id="protectList"></ul>
      </details>
    </div>
  
"""

SCRIPT = r"""
let appsMode = 'block';

let appsModeSeeded = false;

function selectAppsMode(mode) {
  appsMode = mode;
  document.querySelectorAll('#appsModes button').forEach((button) => {
    button.classList.toggle('active', button.dataset.appsMode === mode);
  });
  note('appsModeHint', mode === 'block'
    ? T('apps.toast.10')
    : T('apps.toast.9'));
  $('newEntry').placeholder = mode === 'block'
    ? T('apps.message.16')
    : T('apps.message.15');
  note('appsFeedback', '');
  renderProcesses();
}

/** One entry in the picker, rendered as a pill. */

function processPill(entry) {
  const label = document.createElement('label');
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.checked = entry.checked;
  box.disabled = entry.locked;
  if (entry.locked) label.className = 'locked';
  if (!entry.locked) box.onchange = entry.onChange;
  label.appendChild(box);
  const text = document.createElement('span');
  text.textContent = entry.text;
  label.appendChild(text);
  if (entry.tag) {
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.textContent = entry.tag;
    label.appendChild(tag);
  }
  return label;
}

function renderProcesses() {
  const needle = ($('procSearch').value || '').toLowerCase();
  const container = $('procList');
  container.innerHTML = '';
  const blockNames = new Set(config.blacklist.names || []);
  const protectNames = new Set((config.protect.names || []).map((name) => name.toLowerCase()));
  const builtinProtected = processes.filter((proc) => proc.protected);
  let shown = 0;

  const matches = (name) => !needle || name.toLowerCase().includes(needle);

  if (appsMode === 'block') {
    // Protected processes are deliberately absent: they can never be frozen, so
    // offering them here would only be a way to be confused.
    for (const proc of processes) {
      if (proc.protected || !matches(proc.name)) continue;
      shown += 1;
      container.appendChild(processPill({
        checked: blockNames.has(proc.name),
        locked: false,
        text: proc.name + (proc.count > 1 ? ' ×' + proc.count : ''),
        onChange: () => {
          const names = new Set(config.blacklist.names || []);
          if (names.has(proc.name)) names.delete(proc.name); else names.add(proc.name);
          applyPatch({ blacklist: { names: Array.from(names).sort() } }, T('apps.message.14'));
        },
      }));
    }
  } else {
    for (const proc of builtinProtected) {
      if (!matches(proc.name)) continue;
      shown += 1;
      const custom = protectNames.has(proc.name.toLowerCase());
      container.appendChild(processPill({
        checked: true,
        locked: !custom,
        text: proc.name + (proc.count > 1 ? ' ×' + proc.count : ''),
        tag: custom ? T('apps.message.13') : T('apps.message.12'),
        onChange: () => {
          const names = (config.protect.names || []).filter(
            (name) => name.toLowerCase() !== proc.name.toLowerCase());
          applyPatch({ protect: { names } }, T('apps.message.11') + proc.name);
        },
      }));
    }
    // Custom entries that are not running right now still deserve to be visible.
    for (const name of currentList('protectList')) {
      if (!matches(name)) continue;
      const running = builtinProtected.some((proc) => proc.name.toLowerCase() === name.toLowerCase());
      if (running) continue;
      shown += 1;
      container.appendChild(processPill({
        checked: true,
        locked: false,
        text: name,
        tag: T('apps.message.10'),
        onChange: () => applyPatch(
          { protect: { names: currentList('protectList').filter((v) => v !== name) } },
          T('apps.message.9') + name),
      }));
    }
  }

  if (!shown) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = needle
      ? T('apps.message.8')
      : (appsMode === 'block' ? T('apps.message.7') : T('apps.message.6'));
    container.appendChild(empty);
  }

  const selected = appsMode === 'block'
    ? (config.blacklist.names || []).length
    : (config.protect.names || []).length;
  note('appsSummary', appsMode === 'block'
    ? T('apps.toast.8') + shown + T('apps.toast.7') + selected + T('apps.toast.6')
    : T('apps.toast.5') + shown + T('apps.toast.4') + selected + T('apps.toast.3') +
      (config.protected_count || 0) + T('apps.toast.2'));
}

function addEntry() {
  const value = $('newEntry').value.trim();
  if (!value) {
    note('appsFeedback', T('apps.toast.1'));
    return;
  }
  const key = appsMode === 'block' ? 'names' : 'protect_names';
  if (key === 'names') {
    const names = new Set(config.blacklist.names || []);
    names.add(value);
    $('newEntry').value = '';
    applyPatch({ blacklist: { names: Array.from(names).sort() } }, T('apps.message.5') + value + T('apps.message.4'));
  } else {
    const names = new Set(config.protect.names || []);
    names.add(value);
    $('newEntry').value = '';
    applyPatch({ protect: { names: Array.from(names).sort() } },
      T('apps.message.3') + value + T('apps.message.2'));
  }
}

function addCmdline() {
  const value = $('newCmdline').value.trim();
  if (!value) return;
  const list = [...(config.blacklist.cmdline_substrings || []), value];
  $('newCmdline').value = '';
  applyPatch({ blacklist: { cmdline_substrings: list } }, T('apps.message.1') + value);
}

function seedAppsMode() {
  if (appsModeSeeded) return;
  appsModeSeeded = true;
  selectAppsMode(appsMode);
}
"""
