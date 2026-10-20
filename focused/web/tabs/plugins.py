OPENING = '<section id="tab-plugins">'

SECTION = r"""
    <div class="card">
      <h2>[[plugins.heading.1]]</h2>
      <ul id="pluginList"></ul>
      <div class="muted" id="pluginEmpty">[[plugins.text.1]]</div>
    </div>
    <div class="card">
      <h2>[[plugins.heading.2]]</h2>
      <div class="muted">[[plugins.text.2]]<code>id</code>[[plugins.text.3]]<br />
        <code>PUT /api/v1/plugins/{id}/rules</code>[[plugins.text.4]]<code>POST /api/v1/plugins/{id}/session</code>[[plugins.text.5]]<code>{"active":true,"ttl_seconds":30}</code>）、
        <code>GET /api/v1/events?since=0</code>[[plugins.text.6]]<strong>[[plugins.text.7]]</strong>[[plugins.text.8]]</div>
      <div class="row">
        <input type="text" id="newPluginName" placeholder="[[plugins.placeholder.1]]" />
        <button class="action" onclick="registerPlugin()">[[plugins.button.1]]</button>
      </div>
      <pre id="registerResult" style="display:none"></pre>
      <div class="muted" style="margin-top:10px">[[plugins.text.9]]<code id="pluginsPath">~/.config/focused/plugins/</code>[[plugins.text.10]]<code>docs/focused-design.md</code>。
      </div>
    </div>
  
"""

SCRIPT = r"""
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
    const seen = plugin.last_seen_ago === null ? T('plugins.message.17') : Math.round(plugin.last_seen_ago) + T('plugins.message.16');
    const claim = plugin.session_live ? T('plugins.message.15')
      : (plugin.session_claim ? T('plugins.message.14') : '');
    span.textContent = plugin.id + '  ' + (plugin.version || '') +
      '  「' + plugin.kind + T('plugins.message.13') + seen +
      T('plugins.message.12') + plugin.rules_count + T('plugins.message.11') + claim +
      (plugin.errors ? T('plugins.message.10') + plugin.errors : '');
    li.appendChild(span);
    const toggle = document.createElement('button');
    toggle.className = 'action';
    toggle.textContent = plugin.enabled ? T('plugins.message.9') : T('plugins.message.8');
    toggle.onclick = async () => {
      await api('/plugins/' + plugin.id + '/enable', 'POST', { enabled: !plugin.enabled });
      await refreshAll();
    };
    li.appendChild(toggle);
    const remove = document.createElement('button');
    remove.className = 'action danger';
    remove.textContent = T('plugins.message.7');
    remove.onclick = async () => {
      if (!confirm(T('plugins.confirm.2') + plugin.id + T('plugins.confirm.1'))) return;
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
    box.textContent = T('plugins.message.6') + result.id + T('plugins.message.5') + result.token +
      T('plugins.message.4') + result.endpoints.rules +
      T('plugins.message.3') + result.endpoints.session + '   {"active":true,"ttl_seconds":30}' +
      T('plugins.message.2') + result.endpoints.state +
      T('plugins.message.1') + result.endpoints.events;
  } catch (err) {
    alert(T('plugins.alert.1') + err.message);
  }
  await refreshAll();
}
"""
