OPENING = '<section id="tab-log">'

SECTION = r"""
    <div class="card">
      <h2>[[log.heading.1]]</h2>
      <div class="row">
        <label><input type="checkbox" id="autoScroll" checked />[[log.text.1]]</label>
        <button class="action" onclick="loadEvents(true)">[[log.button.1]]</button>
        <span class="muted" id="logHint"></span>
      </div>
      <pre id="logList">—</pre>
    </div>
  
"""

SCRIPT = r"""
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
  $('logList').textContent = lines.join('\\n') || T('log.message.4');
  $('ov-events').textContent = lines.slice(-8).join('\\n') || T('log.message.3');
  show('logHint', T('log.message.2') + events.length + T('log.message.1') + eventSeq);
  if ($('autoScroll').checked) $('logList').scrollTop = $('logList').scrollHeight;
}

// The page refreshes itself every few seconds.  That must never overwrite a field
// somebody is typing in: unsaved input silently reverting to the stored value
// looks like the app ignoring the user.  Every auto-fill therefore goes through
// these two helpers, which skip a field that is focused or already edited.
"""
