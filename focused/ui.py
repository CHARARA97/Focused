"""The Focused dashboard: one page, no build step, no external resources.

A desktop toolkit would look more like an "app", but it would also tie Focused to
one desktop and one set of dependencies.  A local page works from any browser, on
any session, and over SSH; the same actions are available from the CLI and the
HTTP API, so nothing is only reachable through a click.

Kept in its own module so ``server.py`` stays about routing.
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Focused</title>
<style>
  :root {
    color-scheme: light dark;
    --bg:#ffffff; --fg:#1f2328; --muted:#5b636e; --card:#f6f8fa; --border:#d0d7de;
    --accent:#2f7d4f; --warn:#b7791f; --danger:#c0392b;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#15171b; --fg:#e6e8eb; --muted:#9aa2ad; --card:#1d2025; --border:#333941; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.6 system-ui,"Noto Sans SC","Segoe UI",sans-serif; }
  header { display:flex; gap:14px; align-items:center; flex-wrap:wrap;
           padding:14px 20px; border-bottom:1px solid var(--border); position:sticky; top:0;
           background:var(--bg); z-index:5; }
  .dot { width:10px; height:10px; border-radius:50%; background:var(--muted); display:inline-block; }
  .dot.on { background:var(--accent); } .dot.paused { background:var(--warn); }
  .title { font-weight:600; }
  .count { color:var(--muted); }
  header .spacer { flex:1; }
  main { display:flex; gap:20px; padding:20px; align-items:flex-start; }
  nav { display:flex; flex-direction:column; gap:4px; min-width:130px; }
  nav button { text-align:left; padding:8px 10px; border-radius:8px; border:1px solid transparent;
               background:transparent; color:inherit; cursor:pointer; font:inherit; }
  nav button.active { background:var(--card); border-color:var(--border); font-weight:600; }
  section { flex:1; max-width:900px; display:none; }
  section.active { display:block; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px;
          padding:16px; margin-bottom:16px; }
  h2 { font-size:15px; margin:0 0 10px; }
  h3 { font-size:13px; margin:16px 0 6px; color:var(--muted); }
  button.action { padding:8px 14px; border-radius:8px; border:1px solid var(--border);
                  background:transparent; color:inherit; cursor:pointer; font:inherit; }
  button.action:hover { border-color:currentColor; }
  button.primary { border-color:var(--accent); color:var(--accent); }
  button.danger { border-color:var(--danger); color:var(--danger); }
  [data-dirty="1"] { border-color:var(--accent); }
  input[type=text], input[type=number], input[type=password] {
    padding:7px 9px; border-radius:8px; border:1px solid var(--border);
    background:var(--bg); color:inherit; font:inherit; width:100%;
  }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .row input[type=text] { flex:1; min-width:160px; }
  ul { list-style:none; margin:6px 0; padding:0; }
  li { display:flex; gap:8px; align-items:center; padding:4px 0; }
  li .grow { flex:1; }
  .muted { color:var(--muted); }
  .pill { font-size:12px; padding:1px 7px; border-radius:999px; border:1px solid var(--border); }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }
  .proc { display:flex; flex-wrap:wrap; gap:6px; max-height:230px; overflow:auto;
          border:1px solid var(--border); border-radius:8px; padding:8px; }
  .proc label { display:flex; gap:5px; align-items:center; border:1px solid var(--border);
                border-radius:999px; padding:2px 9px; cursor:pointer; }
  .proc label.locked { opacity:.55; cursor:default; }
  .proc .empty { color:var(--muted); padding:4px 2px; }
  details.fold { border-top:1px solid var(--border); margin-top:12px; padding-top:8px; }
  details.fold > summary { cursor:pointer; font-weight:600; padding:4px 0; }
  details.fold > summary::marker { color:var(--muted); }
  details.fold ul { margin:6px 0 0; }
  .tag { font-size:11px; color:var(--muted); border:1px solid var(--border);
         border-radius:999px; padding:0 6px; }
  code { background:rgba(127,127,127,.16); padding:1px 5px; border-radius:4px; }
  pre { background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:10px;
        max-height:320px; overflow:auto; font-size:12px; white-space:pre-wrap; }
  .banner { padding:8px 12px; border-radius:8px; border:1px solid var(--warn); color:var(--warn);
            margin-bottom:12px; }
  .modes { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:10px; }
  .modes button { padding:7px 12px; border-radius:8px; border:1px solid var(--border);
                  background:transparent; color:inherit; cursor:pointer; font:inherit; }
  .modes button.active { border-color:var(--accent); color:var(--accent); font-weight:600; }
  .durations { display:flex; gap:10px; align-items:flex-end; flex-wrap:wrap; }
  .durations label { display:flex; flex-direction:column; gap:4px; font-size:12px; color:var(--muted); }
  .durations input { width:88px; }
  .presets { display:flex; gap:6px; flex-wrap:wrap; }
  .presets button { padding:4px 10px; border-radius:999px; border:1px solid var(--border);
                    background:transparent; color:inherit; cursor:pointer; font:inherit; font-size:12px; }
  .clock { font-size:34px; font-weight:600; font-variant-numeric:tabular-nums; letter-spacing:1px; }
  .clock small { font-size:14px; font-weight:400; color:var(--muted); letter-spacing:0; }
</style>
</head>
<body>
<header>
  <span class="dot" id="dot"></span>
  <span class="title" id="state">正在读取状态</span>
  <span class="count" id="timer"></span>
  <span class="count" id="frozenSummary"></span>
  <span class="spacer"></span>
  <button class="action" id="startPomodoro" onclick="startPomodoroFromSettings()">番茄钟</button>
  <button class="action" id="startStopwatch" onclick="startStopwatch()">正向计时</button>
  <button class="action" id="pauseButton" onclick="pauseFromSettings()">放行</button>
  <button class="action" id="stopButton" onclick="stopSession()">结束专注</button>
  <button class="action danger" onclick="post('/thaw', {})">立即恢复全部</button>
</header>

<main>
  <nav>
    <button data-tab="overview" class="active">概览</button>
    <button data-tab="apps">应用名单</button>
    <button data-tab="urls">网址名单</button>
    <button data-tab="plugins">插件</button>
    <button data-tab="settings">设置</button>
    <button data-tab="log">日志</button>
  </nav>

  <section id="tab-overview" class="active">
    <div class="banner" id="dryBanner" style="display:none">当前为「只记录，不冻结」：命中名单的进程不会被挂起。</div>
    <div class="card">
      <h2>专注会话</h2>
      <div class="clock" id="clock">未生效 · 未开始专注</div>
      <div class="muted" id="clockDetail">选择模式与时长后开始专注。</div>

      <h3>模式</h3>
      <div class="modes" id="modes">
        <button data-mode="pomodoro">番茄钟</button>
        <button data-mode="stopwatch">正向计时</button>
      </div>
      <div class="muted">轮数为 1 时只做一轮专注，等同于单次倒计时。</div>

      <div class="durations">
        <label id="labWork">专注时长（分钟）
          <input type="number" id="workMinutes" min="1" max="600" step="1" />
        </label>
        <label id="labBreak">休息时长（分钟）
          <input type="number" id="breakMinutes" min="1" max="120" step="1" />
        </label>
        <label id="labCycles">轮数（0 = 一直循环）
          <input type="number" id="cycles" min="0" max="99" step="1" />
        </label>
        <button class="action primary" id="startBtn" onclick="startSessionFromForm()">开始专注</button>
      </div>
      <div class="presets" id="presets" style="margin-top:10px">
        <button data-preset="15">15 分钟</button>
        <button data-preset="25">25 分钟</button>
        <button data-preset="45">45 分钟</button>
        <button data-preset="60">60 分钟</button>
        <button data-preset="90">90 分钟</button>
      </div>

      <div class="row" style="margin-top:12px">
        <button class="action" id="extendBtn" onclick="extendSession(5)">+5 分钟</button>
        <button class="action" id="skipBtn" onclick="skipBreak()">跳过休息</button>
        <button class="action" id="pauseCardButton" onclick="pauseFromSettings()">放行</button>
        <button class="action danger" onclick="stopSession()">结束专注</button>
      </div>
    </div>

    <div class="card">
      <h2>现在</h2>
      <div class="grid">
        <div><div class="muted">状态</div><div id="ov-state">正在读取</div></div>
        <div><div class="muted">会话来源</div><div id="ov-who">—</div></div>
        <div><div class="muted">已冻结</div><div id="ov-frozen">0</div></div>
        <div><div class="muted">本轮开始时间</div><div id="ov-since">—</div></div>
        <div><div class="muted">模式</div><div id="ov-mode">—</div></div>
        <div><div class="muted">阶段</div><div id="ov-phase">—</div></div>
      </div>
    </div>
    <div class="card">
      <h2>已冻结的应用</h2>
      <ul id="frozenList"></ul>
      <div class="muted" id="frozenEmpty">当前没有已冻结的应用。</div>
    </div>
    <div class="card">
      <h2>运行环境自检</h2>
      <div class="grid">
        <div><div class="muted">cgroup v2</div><div id="env-cgroup">—</div></div>
        <div><div class="muted">状态文件</div><div id="env-state" class="muted"></div></div>
        <div><div class="muted">保护名单</div><div id="env-protect">—</div></div>
        <div><div class="muted">本进程 cgroup</div><div id="env-self" class="muted"></div></div>
      </div>
    </div>
    <div class="card">
      <h2>事件记录（最近）</h2>
      <pre id="ov-events">—</pre>
    </div>
  </section>

  <section id="tab-apps">
    <div class="card">
      <h2>要屏蔽的应用</h2>
      <div class="modes" id="appsModes">
        <button data-apps-mode="block">屏蔽模式</button>
        <button data-apps-mode="protect">保护模式</button>
      </div>
      <div class="muted" id="appsModeHint"></div>

      <div class="row" style="margin-top:10px">
        <input type="text" id="procSearch" placeholder="搜索运行中的进程" oninput="renderProcesses()" />
        <input type="text" id="newEntry" placeholder="手动输入进程名，支持 * 与 ?" />
        <button class="action" onclick="addEntry()">添加</button>
      </div>
      <div class="muted" id="appsFeedback"></div>
      <div class="proc" id="procList"></div>
      <div class="muted" id="appsSummary"></div>

      <details class="fold" id="foldNames">
        <summary>屏蔽名单（<span id="namesCount">0</span>）</summary>
        <ul id="nameList"></ul>
      </details>

      <details class="fold" id="foldCmdline">
        <summary>按启动参数匹配（<span id="cmdlineCount">0</span>）</summary>
        <div class="row">
          <input type="text" id="newCmdline" placeholder="例如 --profile=distraction" />
          <button class="action" onclick="addCmdline()">添加</button>
        </div>
        <ul id="cmdlineList"></ul>
      </details>

      <details class="fold" id="foldProtect">
        <summary>保护名单（<span id="protectCount">0</span>）· 永不被冻结</summary>
        <div class="muted" id="protectHint"></div>
        <ul id="protectList"></ul>
      </details>
    </div>
  </section>

  <section id="tab-urls">
    <div class="card">
      <h2>浏览器网址名单</h2>
      <div class="muted">Focused 不操作浏览器。该名单由浏览器插件读取，用于在专注期间关闭匹配的标签页。
        含 <code>*</code> 的条目按通配匹配整个网址，其余按子串匹配，大小写不敏感。</div>
      <div class="row" style="margin-top:10px">
        <input type="text" id="newUrl" placeholder="例如 reddit.com 或 *://*.twitter.com/*" />
        <button class="action" onclick="addUrl()">添加</button>
      </div>
      <div class="muted" id="urlFeedback"></div>
      <details class="fold" open>
        <summary>名单（<span id="urlCount">0</span>）</summary>
        <ul id="urlList"></ul>
      </details>
    </div>
  </section>

  <section id="tab-plugins">
    <div class="card">
      <h2>已连接的插件</h2>
      <ul id="pluginList"></ul>
      <div class="muted" id="pluginEmpty">当前没有已连接的插件。游戏插件与浏览器扩展在启动后会自动注册。</div>
    </div>
    <div class="card">
      <h2>接入新的插件</h2>
      <div class="muted">
        任何语言都可以：注册后拿到自己的 <code>id</code> 与令牌，然后<br />
        <code>PUT /api/v1/plugins/{id}/rules</code> 贡献规则、
        <code>POST /api/v1/plugins/{id}/session</code> 认领会话（<code>{"active":true,"ttl_seconds":30}</code>）、
        <code>GET /api/v1/events?since=0</code> 读事件。
        规则是<strong>并集</strong>：多个插件互不干扰，谁也不覆盖谁。
      </div>
      <div class="row">
        <input type="text" id="newPluginName" placeholder="插件名，例如 pomodoro" />
        <button class="action" onclick="registerPlugin()">注册并显示令牌</button>
      </div>
      <pre id="registerResult" style="display:none"></pre>
      <div class="muted" style="margin-top:10px">
        需要更深的能力（在扫描里做判断、冻结时顺带做事）可以写进程内 Python 插件，放在
        <code id="pluginsPath">~/.config/focused/plugins/</code>，接口见 <code>docs/focused-design.md</code>。
      </div>
    </div>
  </section>

  <section id="tab-settings">
    <div class="card">
      <h2>运行设置</h2>
      <div class="row"><label><input type="checkbox" id="setDryRun" /> 只记录，不冻结（不挂起任何应用）</label></div>
      <div class="row" style="margin-top:10px">
        <label style="flex:1">默认模式
          <select id="setSessionMode">
            <option value="pomodoro">番茄钟（专注 / 休息）</option>
            <option value="stopwatch">正向计时（一直走）</option>
          </select></label>
        <label style="flex:1">放行时长（分钟）
          <input type="number" id="setPauseMinutes" min="1" max="600" /></label>
      </div>
      <div class="row" style="margin-top:10px">
        <label style="flex:1">番茄钟专注（分钟）
          <input type="number" id="setPomodoroWork" min="1" max="180" /></label>
        <label style="flex:1">番茄钟休息（分钟）
          <input type="number" id="setPomodoroBreak" min="1" max="60" /></label>
        <label style="flex:1">番茄钟轮数（0 = 一直循环）
          <input type="number" id="setPomodoroCycles" min="0" max="99" /></label>
        <label style="flex:1">扫描间隔（秒）
          <input type="number" id="setScanInterval" min="0.1" max="60" step="0.1" /></label>
      </div>
      <div class="row" style="margin-top:10px">
        <label style="flex:1">HTTP 令牌（留空 = 不校验，仅本机）
          <input type="password" id="setToken" placeholder="留空表示沿用当前值" /></label>
      </div>
      <div class="row" style="margin-top:12px">
        <button class="action primary" onclick="saveSettings()">保存设置</button>
        <span class="muted" id="settingsResult"></span>
      </div>
    </div>
    <div class="card">
      <h2>运行信息（只读）</h2>
      <pre id="settingsInfo">—</pre>
    </div>
  </section>

  <section id="tab-log">
    <div class="card">
      <h2>事件记录</h2>
      <div class="row">
        <label><input type="checkbox" id="autoScroll" checked /> 自动跟随最新</label>
        <button class="action" onclick="loadEvents(true)">刷新</button>
        <span class="muted" id="logHint"></span>
      </div>
      <pre id="logList">—</pre>
    </div>
  </section>
</main>

<script>
'use strict';
let state = null;
let config = null;
let processes = [];
let events = [];
let eventSeq = 0;

const $ = (id) => document.getElementById(id);
const show = (id, text) => { const el = $(id); if (el) el.textContent = text; };

async function api(path, method = 'GET', body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch('/api/v1' + path, options);
  const payload = await response.json().catch(() => ({ ok: false, error: 'bad json' }));
  if (!response.ok && payload.error) throw new Error(payload.error);
  return payload;
}

async function post(path, body) {
  try {
    await api(path, 'POST', body || {});
  } catch (err) {
    alert('操作失败：' + err.message);
  }
  await refreshAll();
}

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
    mode === 'stopwatch' ? '正向计时不使用该时长' : '专注时长（分钟）';
  $('workMinutes').disabled = mode === 'stopwatch';
  $('startBtn').textContent = mode === 'stopwatch' ? '开始正向计时' : '开始番茄钟';
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
    note('clockDetail', '请填写专注时长（分钟）。');
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
    ? ' · 一直循环'
    : (defaults.cycles === 1 ? ' · 单轮' : ' · ' + defaults.cycles + ' 轮');
  pomodoro.textContent = '番茄钟 ' + defaults.work + ' 分钟' + rest;
  pomodoro.title = '专注 ' + defaults.work + ' 分钟，休息 ' + defaults.rest + ' 分钟';
  stopwatch.title = '从 0 开始计时，手动结束';
  pomodoro.classList.toggle('primary', defaults.mode === 'pomodoro');
  stopwatch.classList.toggle('primary', defaults.mode === 'stopwatch');
  $('pauseButton').textContent = '放行 ' + defaults.pause + ' 分钟';
  $('pauseCardButton').textContent = '放行 ' + defaults.pause + ' 分钟';
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

  $('extendBtn').disabled = !running || session.mode === 'stopwatch';
  $('skipBtn').disabled = !running || session.phase !== 'break';

  if (!running) {
    clock.textContent = state && state.active ? '生效中 · 外部会话' : '未生效 · 未开始专注';
    detail.textContent = state && state.active
      ? '会话来源：插件 ' + (state.delegated_source || '未知来源')
      : '选择模式与时长后开始专注。';
    return;
  }

  clock.textContent = formatClock(session.remaining || session.elapsed || 0);
  if (session.mode === 'stopwatch') {
    clock.textContent = formatClock(session.elapsed || 0);
    clock.innerHTML = clock.textContent + ' <small>已专注 · 正向计时</small>';
    detail.textContent = '正向计时 · 开始于 ' + new Date(session.started_at * 1000).toLocaleTimeString() + ' · 需手动结束';
    return;
  }
  if (session.mode === 'pomodoro') {
    clock.innerHTML = clock.textContent +
      ' <small>' + (session.phase === 'break' ? '休息中 · 已恢复全部应用' : '专注中') + '</small>';
    detail.textContent = '第 ' + session.cycle + ' / ' +
      (session.cycles ? session.cycles : '∞') + ' 轮 · 专注 ' + session.work_minutes +
      ' 分钟 · 休息 ' + session.break_minutes + ' 分钟';
    return;
  }
  clock.innerHTML = clock.textContent + ' <small>剩余 · 倒计时</small>';
  detail.textContent = '本轮共 ' + (session.until - session.started_at) / 60 + ' 分钟 · 结束后自动恢复';
}

let appsMode = 'block';
let appsModeSeeded = false;

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
    button.textContent = '移除';
    button.onclick = () => removeEntry(listId, value);
    li.appendChild(button);
    list.appendChild(li);
  }
  if (!currentList(listId).length) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = '（空）';
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
  applyPatch(patch, '已移除 ' + value);
}

function selectAppsMode(mode) {
  appsMode = mode;
  document.querySelectorAll('#appsModes button').forEach((button) => {
    button.classList.toggle('active', button.dataset.appsMode === mode);
  });
  note('appsModeHint', mode === 'block'
    ? '屏蔽模式：勾选的进程在专注期间会被冻结。受保护进程不在此列出。'
    : '保护模式：列出的进程永不被冻结。内置保护项不可移除，自定义项可移除。');
  $('newEntry').placeholder = mode === 'block'
    ? '手动输入进程名，支持 * 与 ?'
    : '手动输入要保护的进程名';
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
          applyPatch({ blacklist: { names: Array.from(names).sort() } }, '已更新屏蔽名单');
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
        tag: custom ? '自定义' : '内置',
        onChange: () => {
          const names = (config.protect.names || []).filter(
            (name) => name.toLowerCase() !== proc.name.toLowerCase());
          applyPatch({ protect: { names } }, '已取消保护 ' + proc.name);
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
        tag: '自定义 · 未运行',
        onChange: () => applyPatch(
          { protect: { names: currentList('protectList').filter((v) => v !== name) } },
          '已取消保护 ' + name),
      }));
    }
  }

  if (!shown) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = needle
      ? '没有匹配的进程。'
      : (appsMode === 'block' ? '没有可屏蔽的运行中进程。' : '当前没有被保护的运行中进程。');
    container.appendChild(empty);
  }

  const selected = appsMode === 'block'
    ? (config.blacklist.names || []).length
    : (config.protect.names || []).length;
  note('appsSummary', appsMode === 'block'
    ? '显示 ' + shown + ' 项 · 屏蔽名单 ' + selected + ' 项'
    : '显示 ' + shown + ' 项 · 自定义保护 ' + selected + ' 项 · 内置保护 ' +
      (config.protected_count || 0) + ' 项');
}

function addEntry() {
  const value = $('newEntry').value.trim();
  if (!value) {
    note('appsFeedback', '请输入进程名。');
    return;
  }
  const key = appsMode === 'block' ? 'names' : 'protect_names';
  if (key === 'names') {
    const names = new Set(config.blacklist.names || []);
    names.add(value);
    $('newEntry').value = '';
    applyPatch({ blacklist: { names: Array.from(names).sort() } }, '已添加 ' + value + ' 到屏蔽名单');
  } else {
    const names = new Set(config.protect.names || []);
    names.add(value);
    $('newEntry').value = '';
    applyPatch({ protect: { names: Array.from(names).sort() } },
      '已添加 ' + value + ' 到保护名单');
  }
}

function addCmdline() {
  const value = $('newCmdline').value.trim();
  if (!value) return;
  const list = [...(config.blacklist.cmdline_substrings || []), value];
  $('newCmdline').value = '';
  applyPatch({ blacklist: { cmdline_substrings: list } }, '已添加匹配条件 ' + value);
}

function addUrl() {
  const value = $('newUrl').value.trim();
  if (!value) {
    note('urlFeedback', '请输入网址或匹配模式。');
    return;
  }
  $('newUrl').value = '';
  applyPatch({ urls: { patterns: [...(config.urls.patterns || []), value] } },
    '已添加 ' + value);
}

async function save(patch, message) {
  try {
    config = await api('/config', 'PATCH', patch);
    if (message) {
      note(patch.urls ? 'urlFeedback' : 'appsFeedback', message);
    }
  } catch (err) {
    note(patch.urls ? 'urlFeedback' : 'appsFeedback', '保存失败：' + err.message);
  }
  await refreshAll();
}

function renderPlugins() {
  const list = $('pluginList');
  list.innerHTML = '';
  const plugins = (state && state.plugins) || [];
  $('pluginEmpty').style.display = plugins.length ? 'none' : 'block';
  for (const plugin of plugins) {
    const li = document.createElement('li');
    const dot = document.createElement('span');
    dot.className = 'dot' + (plugin.enabled ? ' on' : '');
    li.appendChild(dot);
    const span = document.createElement('span');
    span.className = 'grow';
    const seen = plugin.last_seen_ago === null ? '从未连接' : Math.round(plugin.last_seen_ago) + ' 秒前';
    const claim = plugin.session_live ? ' · 正在认领会话'
      : (plugin.session_claim ? ' · 会话已结束' : '');
    span.textContent = plugin.id + '  ' + (plugin.version || '') +
      '  「' + plugin.kind + '」 最后联系 ' + seen +
      '  规则 ' + plugin.rules_count + ' 条' + claim +
      (plugin.errors ? '  错误 ' + plugin.errors : '');
    li.appendChild(span);
    const toggle = document.createElement('button');
    toggle.className = 'action';
    toggle.textContent = plugin.enabled ? '禁用' : '启用';
    toggle.onclick = async () => {
      await api('/plugins/' + plugin.id + '/enable', 'POST', { enabled: !plugin.enabled });
      await refreshAll();
    };
    li.appendChild(toggle);
    const remove = document.createElement('button');
    remove.className = 'action danger';
    remove.textContent = '移除';
    remove.onclick = async () => {
      if (!confirm('移除插件 ' + plugin.id + '？它的规则与会话认领会立即撤回。')) return;
      await api('/plugins/' + plugin.id + '/remove', 'POST', {});
      await refreshAll();
    };
    li.appendChild(remove);
    list.appendChild(li);
  }
}

async function registerPlugin() {
  const name = $('newPluginName').value.trim();
  if (!name) return;
  try {
    const result = await api('/plugins/register', 'POST', { name });
    const box = $('registerResult');
    box.style.display = 'block';
    box.textContent = '插件 id: ' + result.id + '\\n访问令牌: ' + result.token +
      '\\n\\n规则：PUT ' + result.endpoints.rules +
      '\\n会话：POST ' + result.endpoints.session + '   {"active":true,"ttl_seconds":30}' +
      '\\n状态：GET ' + result.endpoints.state +
      '\\n事件：GET ' + result.endpoints.events;
  } catch (err) {
    alert('注册失败：' + err.message);
  }
  await refreshAll();
}

async function loadEvents(force) {
  const payload = await api('/events?since=' + (force ? 0 : eventSeq) + '&limit=100');
  if (force) events = [];
  events = events.concat(payload.events || []);
  eventSeq = payload.next_seq;
  const lines = events.slice(-120).map((event) => {
    const when = new Date(event.ts * 1000).toLocaleTimeString();
    return when + '  ' + String(event.kind).padEnd(16) +
      (event.source ? '[' + event.source + '] ' : '') +
      (event.name || '') + (event.pid ? ' (pid ' + event.pid + ')' : '') +
      (event.detail ? '  ' + event.detail : '');
  });
  $('logList').textContent = lines.join('\\n') || '（暂无事件记录）';
  $('ov-events').textContent = lines.slice(-8).join('\\n') || '（暂无事件记录）';
  show('logHint', '已加载 ' + events.length + ' 条 · 最新序号 ' + eventSeq);
  if ($('autoScroll').checked) $('logList').scrollTop = $('logList').scrollHeight;
}

// The page refreshes itself every few seconds.  That must never overwrite a field
// somebody is typing in: unsaved input silently reverting to the stored value
// looks like the app ignoring the user.  Every auto-fill therefore goes through
// these two helpers, which skip a field that is focused or already edited.
const dirtyFields = new Set();

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
  show('settingsResult', '有未保存的修改。');
}

const SETTINGS_FIELDS = [
  'setDryRun', 'setSessionMode', 'setPauseMinutes', 'setPomodoroWork',
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
  setFieldValue('setDryRun', config.dry_run);
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
    freeze: config.freeze,
    protected_count: config.protected_count,
  }, null, 2);
}

async function saveSettings() {
  const patch = {
    dry_run: $('setDryRun').checked,
    scan_interval: Number($('setScanInterval').value),
    session: {
      mode: $('setSessionMode').value,
      pause_minutes: Number($('setPauseMinutes').value),
      pomodoro_work_minutes: Number($('setPomodoroWork').value),
      pomodoro_break_minutes: Number($('setPomodoroBreak').value),
      pomodoro_cycles: Number($('setPomodoroCycles').value),
    },
  };
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
    show('settingsResult', warnings.length ? warnings.join(' · ') : '设置已保存');
  } catch (err) {
    // Keep the marks: the edits are still unsaved, and the next refresh must not
    // wipe them just because the save failed.
    show('settingsResult', '保存失败：' + err.message);
  }
  await refreshAll();
}

function renderOverview() {
  const dot = $('dot');
  const paused = state.pause_until > Date.now() / 1000;
  dot.className = 'dot' + (state.active ? (paused ? ' paused' : ' on') : '');
  show('state', state.active ? (paused ? '已暂停 · 放行中' : '生效中 · 专注中') : '未生效 · 未开始专注');
  show('timer', state.active && state.session_since
    ? '· 开始于 ' + new Date(state.session_since * 1000).toLocaleTimeString() : '');
  show('frozenSummary', state.frozen_count ? '· 已冻结 ' + state.frozen_count + ' 个' : '· 已冻结 0 个');
  $('dryBanner').style.display = state.dry_run ? 'block' : 'none';

  const who = [];
  if (state.manual_session) who.push('Focused 应用');
  if (state.delegated_active || state.delegated) who.push('插件：' + (state.delegated_source || '未知来源'));
  show('ov-state', state.active ? (paused ? '已暂停 · 放行中' : '生效中 · 冻结中') : '未生效 · 未开始专注');
  show('ov-who', who.join(' · ') || '—');
  show('ov-frozen', String(state.frozen_count));
  show('ov-since', state.session_since ? new Date(state.session_since * 1000).toLocaleString() : '—');
  const modeNames = { countdown: '单轮专注（命令行）', pomodoro: '番茄钟', stopwatch: '正向计时' };
  show('ov-mode', modeNames[state.session_mode] || '—');
  show('ov-phase', state.session_phase === 'break' ? '休息（已解冻）' : (state.session_mode ? '专注' : '—'));
  show('env-cgroup', state.unified_cgroup2 ? '可用' : '不可用 · 仅按进程树冻结');
  show('env-state', state.state_path);
  show('env-protect', state.protect_count + ' 条');
  show('env-self', state.self_cgroup || '—');

  const list = $('frozenList');
  list.innerHTML = '';
  for (const item of state.frozen || []) {
    const li = document.createElement('li');
    li.textContent = item.name + '（pid ' + item.pid + '，' +
      (item.mode === 'unit' ? '整单元 ' + item.unit : '进程树 ' + (item.pids || []).length + ' 个') +
      '，' + new Date(item.since * 1000).toLocaleTimeString() + '）';
    list.appendChild(li);
  }
  $('frozenEmpty').style.display = (state.frozen || []).length ? 'none' : 'block';
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

function seedAppsMode() {
  if (appsModeSeeded) return;
  appsModeSeeded = true;
  selectAppsMode(appsMode);
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
  show('protectHint', '内置保护项 ' + config.protected_count +
    ' 条，不可移除；下方为自定义保护项，只增不减。');
  seedAppsMode();
  renderProcesses();
  renderPlugins();
  renderSettings();
  renderHeaderActions();
  seedSessionForm();
  renderSessionClock();
  await loadEvents(false);
}

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
refreshAll().catch((err) => { show('state', '无法读取状态：' + err.message); });
setInterval(() => { refreshAll().catch(() => {}); }, 3000);
</script>
</body>
</html>
"""
