# Plugin API

Focused has two plugin tiers. Both can contribute **rules**, claim a **session**,
read **state**, request a **scan** and receive **events**. Neither can send
signals: the host is the only component that freezes or thaws a process, and
every request goes through the same safety gate.

| Tier | Where it runs | Trust | Best for |
|---|---|---|---|
| **In-process Python** | inside the `focused` process, `~/.config/focused/plugins/*.py` | full (same user/process) | deep integration, reacting to events without HTTP |
| **HTTP** | any language, loopback `http://127.0.0.1:8766` | token + capabilities | editors, scripts, other apps, third parties |

There is deliberately **no** `freeze(pid)` / `thaw(pid)` API. Freeze and thaw
are expressed as rule edits:

```text
add rule + scan.request  -> the host may freeze it (after the safety gate)
remove rule              -> the host thaws it immediately if no other source matches
```

---

## 1. Capabilities

A plugin requests capabilities; the host grants them. **Effective = declared ∩
granted.** Dangerous capabilities are never granted by default, and HTTP
registration only ever grants the safe set.

| Capability | Grants | Default |
|---|---|---|
| `rules.read` | read its own rule source | ✅ |
| `rules.write` | replace/add/remove its own rule source | ✅ |
| `state.read` | summary: active, own claim, frozen count, health, scan_ms | ✅ |
| `state.frozen` | read the frozen list | ✅ |
| `session.claim` | claim/release its own session lease | ✅ |
| `scan.request` | ask for a global scan (rate-limited) | ✅ |
| `events.read` | read the event stream / receive `on_event` | ✅ |
| `process.control` | raw freeze/thaw (not implemented) | ❌ |
| `focus.override` | stop other plugins' claims (app-only) | ❌ |

Old manifests may use the short names `rules`, `session`, `status`, `events`,
`scan`; they are expanded to the canonical capabilities above.

---

## 2. In-process plugins

### 2.1 Lifecycle

```python
def register(api) -> dict: ...   # id/name/version/description/capabilities
def on_start(api): ...           # optional
def on_event(api, event): ...    # optional; event is a dict
def on_stop(api): ...            # optional
```

A plugin that raises during `register`/`on_start` is marked failed; its rules
and lease are withdrawn. Five `on_event` failures disable the plugin and clear
its contributions.

### 2.2 Namespaced API

```python
# rules -- only this plugin's source
api.rules.replace(names=(), cmdline_substrings=(), pids=(),
                  protect_names=(), protect_cmdline_substrings=())
api.rules.add(...)      # union with the current source
api.rules.remove(...)   # remove from the current source
api.rules.list() -> {names, cmdline_substrings, pids, ...}

# session -- a vote, not a global switch
api.session.claim(ttl_seconds=30)   # 5..3600
api.session.release()
api.session.status() -> {active, my_claim, claims}

# read-only state
api.state.summary() -> {ok, version, active, my_claim, claims,
                        frozen_count, health, scan_ms}
api.state.frozen()  -> [{name, pid, mode, unit, since, reason}]

# scan request (global, coalesced, at most once per second)
api.scan.request() -> {accepted, frozen, reason}

# events / logging
api.events.recent(since=0, limit=100) -> [event, ...]
api.log(message, level="info")        # debug/info/warning/error
```

Compatibility aliases are kept: `replace_rules`, `claim_session`,
`release_session`, `status`.

`PluginAPI` uses `__slots__`: keep plugin state in your module or a closure,
not on the `api` object.

During `register()`, rule and session calls are buffered and applied once the
registry record exists. State reads (`api.state.*`, `api.rules.list()`) need
that record, so do them in `on_start()` (or later), not inside `register()`.

### 2.3 PID rules

`pids` are accepted by `replace/add/remove`. The host fills the start-time guard
from `/proc` at the moment the rule is applied:

- PID gone/unreadable → `rule.pid-gone` (rejected);
- supplied guard does not match the live process → `rule.pid-reused` (rejected);
- PID 0/1 → `rule.pid-invalid` (rejected).

If a PID rule somehow arrives without a guard, `evaluate` refuses it
(fail-closed). A reused PID is never frozen.

A single source may contain at most **5000** names + cmdline substrings + pids
combined. Blacklist `cmdline_substrings` shorter than two characters are refused
as too broad (`rule.cmdline-short`).

---

## 3. HTTP plugins

### 3.1 Register

```http
POST /api/v1/plugins/register
X-Focused-Token: <application token, if configured>

{"name": "my-plugin", "version": "0.1.0"}
```

The response contains `id`, `token` and the endpoint list. Store the token; it
is shown only here. Registration over HTTP only supports `kind=http` and grants
the safe capability set.

### 3.2 Endpoints

| Method | Path | Permission |
|---|---|---|
| `GET` | `/api/v1/plugins/{id}/rules` | `rules.read` |
| `PUT` | `/api/v1/plugins/{id}/rules` | `rules.write` — replace |
| `POST` | `/api/v1/plugins/{id}/rules/add` | `rules.write` — add |
| `POST` | `/api/v1/plugins/{id}/rules/remove` | `rules.write` — remove |
| `POST` | `/api/v1/plugins/{id}/session` | `session.claim` — `{active, ttl_seconds}` |
| `GET` | `/api/v1/plugins/{id}/state` | `state.read` |
| `POST` | `/api/v1/plugins/{id}/scan` | `scan.request` — 200 accepted / 429 rate-limited |
| `GET` | `/api/v1/focus` | `state.read` (app token always) |
| `GET` | `/api/v1/events?since=N` | `events.read` (app token always) |
| `POST` | `/api/v1/plugins/{id}/enable` | plugin token (self) or app token |
| `POST` | `/api/v1/plugins/{id}/webhook` | plugin token (self) or app token |
| `POST` | `/api/v1/plugins/{id}/remove` | plugin token (self) or app token |

Application-token-only (plugins get 401): `/status`, `/processes`, `/config`,
`/plugins` list, `/focus/frozen`, `/scan`, `/thaw`, `/focus/session`,
`/focus/pause`, `/urls`, legacy `/focus/rules`, legacy `/focus/state`.

### 3.3 Examples

```http
PUT /api/v1/plugins/{id}/rules
{"names": ["firefox"], "pids": [4242]}

POST /api/v1/plugins/{id}/rules/add
{"names": ["discord"]}

POST /api/v1/plugins/{id}/rules/remove
{"names": ["discord"]}

POST /api/v1/plugins/{id}/session
{"active": true, "ttl_seconds": 30}

POST /api/v1/plugins/{id}/scan
```

If `ttl_seconds` is omitted for an active claim, the server uses 30 s. The
minimum plugin lease is 5 s; a claim without a lease is not accepted over the
plugin endpoint.

---

## 4. Recipes

### Freeze an application

```python
api.rules.add(names=["firefox", "chrom*"])
api.session.claim(ttl_seconds=30)   # keep renewing while focus continues
api.scan.request()
```

### Freeze a specific process

```python
api.rules.add(pids=[4242])          # guard is filled automatically
api.scan.request()
```

### Thaw it again

```python
api.rules.remove(names=["firefox"])
# or: api.rules.remove(pids=[4242])
# rules are recomputed immediately; _thaw_stale resumes it if no other source matches
```

### Toggle focus

```python
api.session.claim(ttl_seconds=30)   # focus on (this plugin's vote)
api.session.release()               # focus off for this plugin
```

A release never stops another plugin's claim. If another frontend is still
claiming, the global session stays active. A global override would require
`focus.override`, which is app-only and intentionally not exposed.

### Read state / request a scan

```python
api.state.summary()
api.state.frozen()
api.scan.request()
```

---

## 5. Safety model

- **No plugin signals.** The host is the only `SIGSTOP/SIGCONT/systemctl` caller.
- **Rules are per source.** A plugin can only edit its own source; config and
  other plugins are untouchable.
- **Protect list is additive.** `protect_names=[]` cannot remove built-in rails.
- **Catch-all is rejected.** `names=["*"]` → `rule.catch-all` (400 / `PluginError`).
- **PID guard is mandatory.** Missing/unknown/mismatched guard → no freeze.
- **Claims are leases.** A plugin that stops renewing is cleaned up automatically.
- **Capabilities are granted, not self-declared.** Effective = declared ∩ granted.
- **Rate limits.** The scan cooldown is global (one scan per second); rule edits
  and session claims are validated.
- **Failed plugins are cleaned up.** Rules and claims are withdrawn on failure,
  disable or removal.
- **Every refusal has a reason**, logged as `event=freeze.refuse` and audited as
  `freeze-refused`.

The final gate refuses: PID ≤ 1, zombies, kernel threads, the daemon/parent,
unknown UID, foreign UID, the daemon's process group, protect-list matches,
`slice`/`session-manager` units, empty trees, and unknown start times.

---

## 6. Errors

In-process `PluginError.code`:

| code | meaning |
|---|---|
| `manifest.type` / `manifest.id` / `manifest.capability` | bad registration |
| `rules.type` / `rules.rejected` / `rule.pid-gone` / `rule.pid-reused` | bad rule input |
| `session.ttl` | lease outside 5–3600 s |
| `capability.missing` / `plugin.disabled` | not allowed / not enabled |
| `scan.request` | rate-limited or refused |

HTTP status:

| status | meaning |
|---|---|
| 400 | bad JSON/field, catch-all, bad PID rule, bad TTL |
| 401 | missing/wrong token, or plugin token used on an app-only endpoint |
| 403 | plugin disabled or capability missing |
| 429 | scan rate-limited |
| 404 | unknown plugin/endpoint |

---

## 7. Debugging

```text
event=plugin.load.ok source=hello path=/home/.../hello.py
event=plugin.rules.add source=hello names=1 cmdline=0 pids=0
event=plugin.scan.request source=hello accepted=True frozen=2 reason=ok
event=plugin.log source=hello level=info message="froze firefox"
event=freeze.refuse pid=4242 name=firefox reason=shared-unit
event=plugin.load.fail source=bad error="PluginError: rules.rejected: ..."
```

Useful endpoints for the operator: `/api/v1/status` (full state, including
`health`, `scan_ms`, plugin capabilities), `/api/v1/events`, `/api/v1/plugins`.
Plugin tokens cannot read `/status`; they can read `/focus` and `/events`.

---

## 8. Compatibility

- `/api/v1` is additive; unknown response fields must be ignored.
- `X-Focused-Token` is the only auth header.
- Old flat `PluginAPI` methods and short capability names keep working.
- A plugin's id is its identity; changing it starts with empty rules/claims.
- In-process plugins receive `PluginAPI`, not the engine. This is the supported
  contract; code that reached into the engine must migrate.
