SHARED = r"""
let state = null;

let config = null;

let processes = [];

let events = [];

let eventSeq = 0;

const $ = (id) => document.getElementById(id);

const show = (id, text) => { const el = $(id); if (el) el.textContent = text; };

function dashboardToken() {
  return sessionStorage.getItem('focused-token') || '';
}

async function api(path, method = 'GET', body, allowPrompt = true) {
  const options = { method, headers: {} };
  const token = dashboardToken();
  if (token) options.headers['X-Focused-Token'] = token;
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch('/api/v1' + path, options);
  if (response.status === 401 && allowPrompt) {
    sessionStorage.removeItem('focused-token');
    const entered = window.prompt(T('shared.message.7'), '');
    if (entered) {
      sessionStorage.setItem('focused-token', entered.trim());
      return api(path, method, body, false);
    }
  }
  const payload = await response.json().catch(() => ({ ok: false, error: 'bad json' }));
  if (!response.ok && payload.error) throw new Error(payload.error);
  return payload;
}

async function post(path, body) {
  try {
    await api(path, 'POST', body || {});
  } catch (err) {
    alert(T('shared.alert.1') + err.message);
  }
  await refreshAll();
}

function currentList(listId) {
  if (listId === 'nameList') return config.blacklist.names || [];
  if (listId === 'cmdlineList') return config.blacklist.cmdline_substrings || [];
  if (listId === 'protectList') return config.protect.names || [];
  return config.urls.patterns || [];
}

function note(target, message) {
  const element = $(target);
  if (element) element.textContent = message;
}

function renderList(listId) {
  const list = $(listId);
  list.innerHTML = '';
  for (const value of currentList(listId)) {
    const li = document.createElement('li');
    const span = document.createElement('span');
    span.className = 'grow';
    span.textContent = value;
    li.appendChild(span);
    const button = document.createElement('button');
    button.className = 'action';
    button.textContent = T('shared.message.6');
    button.onclick = () => removeEntry(listId, value);
    li.appendChild(button);
    list.appendChild(li);
  }
  if (!currentList(listId).length) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = T('shared.message.5');
    list.appendChild(li);
  }
}

function applyPatch(patch, message) {
  return save(patch, message);
}

function removeEntry(listId, value) {
  const patch = { blacklist: {}, protect: {}, urls: {} };
  if (listId === 'nameList') {
    patch.blacklist.names = currentList(listId).filter((v) => v !== value);
  } else if (listId === 'cmdlineList') {
    patch.blacklist.cmdline_substrings = currentList(listId).filter((v) => v !== value);
  } else if (listId === 'protectList') {
    patch.protect.names = currentList(listId).filter((v) => v !== value);
  } else {
    patch.urls.patterns = currentList(listId).filter((v) => v !== value);
  }
  applyPatch(patch, T('shared.message.4') + value);
}

async function save(patch, message) {
  try {
    config = await api('/config', 'PATCH', patch);
    if (message) {
      note(patch.urls ? 'urlFeedback' : 'appsFeedback', message);
    }
  } catch (err) {
    note(patch.urls ? 'urlFeedback' : 'appsFeedback', T('shared.toast.1') + err.message);
  }
  await refreshAll();
}

function isFieldBusy(id) {
  const element = $(id);
  if (!element) return false;
  return element === document.activeElement || dirtyFields.has(id);
}

function setFieldValue(id, value) {
  const element = $(id);
  if (!element || isFieldBusy(id)) return;
  if (element.type === 'checkbox') {
    element.checked = !!value;
  } else {
    element.value = value;
  }
}

function markDirty(id) {
  dirtyFields.add(id);
  const element = $(id);
  if (element) element.dataset.dirty = '1';
  show('settingsResult', T('shared.message.3'));
}

async function refreshAll() {
  state = await api('/status');
  config = await api('/config');
  const procPayload = await api('/processes?limit=400');
  processes = procPayload.processes || [];
  renderOverview();
  renderList('nameList');
  renderList('cmdlineList');
  renderList('protectList');
  renderList('urlList');
  show('namesCount', (config.blacklist.names || []).length);
  show('cmdlineCount', (config.blacklist.cmdline_substrings || []).length);
  show('protectCount', (config.protect.names || []).length);
  show('urlCount', (config.urls.patterns || []).length);
  show('protectHint', T('shared.message.2') + config.protected_count +
    T('shared.message.1'));
  seedAppsMode();
  renderProcesses();
  renderPlugins();
  renderSettings();
  renderHeaderActions();
  seedSessionForm();
  renderSessionClock();
  await loadEvents(false);
}
"""

INIT = r"""

'use strict';

document.querySelectorAll('#appsModes button').forEach((button) => {
  button.addEventListener('click', () => selectAppsMode(button.dataset.appsMode));
});

document.querySelectorAll('#modes button').forEach((button) => {
  button.addEventListener('click', () => selectMode(button.dataset.mode));
});

document.querySelectorAll('nav button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('nav button').forEach((b) => b.classList.remove('active'));
    document.querySelectorAll('section').forEach((s) => s.classList.remove('active'));
    button.classList.add('active');
    $('tab-' + button.dataset.tab).classList.add('active');
    if (button.dataset.tab === 'log') loadEvents(true);
  });
});

watchSettingsFields();

refreshAll().catch((err) => { show('state', T('shared.message.8') + err.message); });

setInterval(tickClock, 1000);
setInterval(() => { refreshAll().catch(() => {}); }, 3000);
"""
