OPENING = '<section id="tab-urls">'

SECTION = r"""
    <div class="card">
      <h2>[[urls.heading.1]]</h2>
      <div class="muted">[[urls.text.1]]<code>*</code>[[urls.text.2]]</div>
      <div class="row" style="margin-top:10px">
        <input type="text" id="newUrl" placeholder="[[urls.placeholder.1]]" />
        <button class="action" onclick="addUrl()">[[urls.button.1]]</button>
      </div>
      <div class="muted" id="urlFeedback"></div>
      <details class="fold" open>
        <summary>[[urls.summary.1]]<span id="urlCount">0</span>）</summary>
        <ul id="urlList"></ul>
      </details>
    </div>
  
"""

SCRIPT = r"""
function addUrl() {
  const value = $('newUrl').value.trim();
  if (!value) {
    note('urlFeedback', T('urls.toast.1'));
    return;
  }
  $('newUrl').value = '';
  applyPatch({ urls: { patterns: [...(config.urls.patterns || []), value] } },
    T('urls.message.1') + value);
}
"""
