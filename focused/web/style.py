CSS = """
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
  .banner.bad { border-color:var(--danger); color:var(--danger); }
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
"""
