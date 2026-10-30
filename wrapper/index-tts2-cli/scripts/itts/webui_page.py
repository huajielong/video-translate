"""WebUI 的单页 HTML/CSS/JS。

刻意做成单个 Python 字符串：不依赖静态文件打包，复制 scripts/ 就能运行，
也不需要 gradio / 任何 CDN。设计方向是“模拟广播控制台 × 现代终端”。
"""

PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#070a0f">
<title>IndexTTS2 · Studio</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23070a0f'/%3E%3Cpath d='M5 17h4l2-7 3 13 3-10 2 4h8' fill='none' stroke='%23f2b84b' stroke-width='2.2' stroke-linejoin='round' stroke-linecap='round'/%3E%3C/svg%3E">
<style>
:root{
  --bg:#070a0f;
  --panel:#0e141c;
  --panel-2:#101821;
  --inset:#090e14;
  --line:#1c2733;
  --line-strong:#2a3948;
  --text:#e8eef6;
  --muted:#8d9bad;
  --dim:#5f6c7c;
  --amber:#f2b84b;
  --amber-2:#ffd27a;
  --teal:#5eead4;
  --red:#ff6b6b;
  --green:#43d99a;
  --mono:"Cascadia Code","JetBrains Mono","SFMono-Regular",Consolas,"Liberation Mono",monospace;
  --display:"Bahnschrift","DIN Alternate","Arial Narrow","Microsoft YaHei UI",sans-serif;
  --body:"Segoe UI Variable Text","Segoe UI","PingFang SC","Microsoft YaHei UI","Noto Sans SC",sans-serif;
  --radius:14px;
}
*{box-sizing:border-box}
html,body{min-height:100%}
body{
  margin:0;
  color:var(--text);
  font-family:var(--body);
  font-size:14px;
  line-height:1.55;
  background:
    radial-gradient(1100px 560px at 12% -12%,rgba(242,184,75,.085),transparent 62%),
    radial-gradient(900px 520px at 102% -4%,rgba(94,234,212,.05),transparent 60%),
    var(--bg);
  -webkit-font-smoothing:antialiased;
}
body::before{
  content:"";
  position:fixed;
  inset:0;
  background-image:
    linear-gradient(rgba(255,255,255,.024) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.024) 1px,transparent 1px);
  background-size:48px 48px;
  mask-image:radial-gradient(ellipse at 50% 0%,#000 0%,transparent 72%);
  pointer-events:none;
  z-index:0;
}
body::after{
  content:"";
  position:fixed;
  inset:0;
  background:radial-gradient(ellipse at 50% 120%,transparent 40%,rgba(0,0,0,.55) 100%);
  pointer-events:none;
  z-index:0;
}
button,input,select,textarea{font:inherit;color:inherit}
button{cursor:pointer}
a{color:inherit}
::selection{background:rgba(242,184,75,.28)}
:focus-visible{outline:2px solid var(--amber);outline-offset:2px}
[hidden]{display:none!important}

/* ---------- 顶栏 ---------- */
.topbar{
  position:relative;
  z-index:2;
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:18px;
  padding:18px clamp(16px,3vw,34px);
  border-bottom:1px solid var(--line);
  background:linear-gradient(180deg,rgba(9,13,19,.92),rgba(9,13,19,.55));
  backdrop-filter:blur(10px);
}
.wordmark{display:flex;align-items:center;gap:13px;min-width:0}
.mark{
  display:grid;place-items:center;
  width:38px;height:38px;
  border:1px solid rgba(242,184,75,.45);
  border-radius:10px;
  color:var(--amber);
  font-size:19px;
  background:linear-gradient(145deg,rgba(242,184,75,.16),rgba(242,184,75,.02));
  box-shadow:0 0 26px rgba(242,184,75,.12),inset 0 0 18px rgba(242,184,75,.06);
}
.brand{
  font-family:var(--display);
  font-size:21px;
  letter-spacing:.16em;
  font-weight:700;
  line-height:1;
  white-space:nowrap;
}
.brand span{color:var(--amber);margin:0 2px}
.sub{
  margin-top:5px;
  font-family:var(--mono);
  font-size:9px;
  letter-spacing:.34em;
  color:var(--dim);
  text-transform:uppercase;
}
.statusline{display:flex;align-items:center;gap:14px;min-width:0}
.status-pill{
  display:inline-flex;align-items:center;gap:8px;
  padding:7px 12px;
  border:1px solid var(--line-strong);
  border-radius:999px;
  font-family:var(--mono);
  font-size:11px;
  letter-spacing:.08em;
  color:var(--muted);
  background:rgba(10,15,21,.7);
  max-width:280px;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.status-pill .dot{width:7px;height:7px;border-radius:50%;background:var(--dim);flex:none}
.status-pill[data-state="ok"]{color:var(--green);border-color:rgba(67,217,154,.35)}
.status-pill[data-state="ok"] .dot{background:var(--green);box-shadow:0 0 10px rgba(67,217,154,.8)}
.status-pill[data-state="load"]{color:var(--amber);border-color:rgba(242,184,75,.4)}
.status-pill[data-state="load"] .dot{background:var(--amber);animation:pulse 1.2s infinite}
.status-pill[data-state="err"]{color:var(--red);border-color:rgba(255,107,107,.4)}
.status-pill[data-state="err"] .dot{background:var(--red)}
.device{
  font-family:var(--mono);
  font-size:10.5px;
  color:var(--dim);
  letter-spacing:.06em;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}

/* ---------- 布局 ---------- */
.layout{
  position:relative;
  z-index:1;
  display:grid;
  grid-template-columns:minmax(0,430px) minmax(0,1fr);
  gap:18px;
  max-width:1440px;
  margin:0 auto;
  padding:20px clamp(14px,3vw,30px) 52px;
  align-items:start;
}
.panel{
  border:1px solid var(--line);
  border-radius:var(--radius);
  background:linear-gradient(180deg,rgba(16,23,32,.94),rgba(11,16,23,.94));
  box-shadow:0 20px 70px rgba(0,0,0,.38),inset 0 1px 0 rgba(255,255,255,.03);
  animation:rise .55s cubic-bezier(.2,.8,.25,1) both;
}
.console{padding:20px 20px 18px}
.monitor{padding:20px;position:sticky;top:18px;animation-delay:.08s}
.panel-head{display:flex;align-items:center;gap:10px;margin:20px 0 10px}
.panel-head:first-child{margin-top:0}
.panel-head .index{
  font-family:var(--mono);
  font-size:10px;
  color:var(--amber);
  letter-spacing:.1em;
  padding:2px 6px;
  border:1px solid rgba(242,184,75,.28);
  border-radius:4px;
  background:rgba(242,184,75,.06);
}
.panel-head h2{
  margin:0;
  font-family:var(--display);
  font-size:12px;
  font-weight:600;
  letter-spacing:.2em;
  color:var(--muted);
  text-transform:uppercase;
  white-space:nowrap;
}
.panel-head .rule{height:1px;flex:1;background:linear-gradient(90deg,var(--line-strong),transparent)}

/* ---------- 文本 ---------- */
.text-wrap{position:relative}
textarea#text{
  width:100%;
  min-height:148px;
  resize:vertical;
  padding:15px 16px 34px;
  border:1px solid var(--line);
  border-radius:11px;
  background:
    linear-gradient(180deg,rgba(6,10,15,.9),rgba(8,13,19,.9));
  color:var(--text);
  font-size:15px;
  line-height:1.75;
  transition:border-color .2s,box-shadow .2s;
}
textarea#text:focus{
  outline:none;
  border-color:rgba(242,184,75,.55);
  box-shadow:0 0 0 3px rgba(242,184,75,.08),inset 0 0 30px rgba(242,184,75,.025);
}
textarea::placeholder{color:#4e5b6b}
.text-meta{
  display:flex;justify-content:space-between;gap:10px;
  margin-top:7px;
  font-family:var(--mono);
  font-size:10px;
  color:var(--dim);
  letter-spacing:.05em;
}
.samples{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
.samples button{
  border:1px solid var(--line);
  background:rgba(255,255,255,.015);
  color:var(--muted);
  border-radius:999px;
  padding:4px 10px;
  font-size:11px;
  transition:.18s;
}
.samples button:hover{color:var(--text);border-color:var(--line-strong);background:rgba(255,255,255,.04)}

/* ---------- 音色 ---------- */
.voice-row{display:flex;gap:8px;align-items:stretch}
.select-wrap{position:relative;flex:1;min-width:0}
.select-wrap select{
  width:100%;
  appearance:none;
  padding:10px 32px 10px 12px;
  border:1px solid var(--line);
  border-radius:9px;
  background:var(--inset);
  color:var(--text);
  font-size:13px;
  text-overflow:ellipsis;
  cursor:pointer;
}
.select-wrap select:hover{border-color:var(--line-strong)}
.select-arrow{
  position:absolute;right:11px;top:50%;transform:translateY(-52%);
  color:var(--dim);pointer-events:none;font-size:13px;
}
button.ghost{
  border:1px solid var(--line-strong);
  background:rgba(255,255,255,.02);
  color:var(--muted);
  border-radius:9px;
  padding:0 13px;
  font-size:12px;
  white-space:nowrap;
  transition:.18s;
}
button.ghost:hover{color:var(--text);border-color:rgba(242,184,75,.4);background:rgba(242,184,75,.06)}
button.ghost:disabled{opacity:.4;cursor:not-allowed}
.voice-note{
  min-height:16px;
  margin-top:7px;
  font-family:var(--mono);
  font-size:10px;
  color:var(--dim);
  letter-spacing:.03em;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}

/* ---------- 情感 ---------- */
.emo-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}
.emo-grid button{
  display:flex;align-items:center;gap:9px;
  padding:10px 11px;
  border:1px solid var(--line);
  border-radius:9px;
  background:rgba(255,255,255,.012);
  color:var(--muted);
  text-align:left;
  transition:.18s;
}
.emo-grid button b{
  font-family:var(--mono);font-size:10px;font-weight:400;
  color:var(--dim);letter-spacing:.08em;
}
.emo-grid button span{font-size:12.5px;white-space:nowrap}
.emo-grid button:hover{border-color:var(--line-strong);color:var(--text)}
.emo-grid button.active{
  border-color:rgba(242,184,75,.55);
  color:var(--amber-2);
  background:linear-gradient(135deg,rgba(242,184,75,.13),rgba(242,184,75,.03));
  box-shadow:inset 0 0 22px rgba(242,184,75,.05);
}
.emo-grid button.active b{color:var(--amber)}
.emo-panel{margin-top:10px}
.panel-hint{font-size:11.5px;color:var(--dim);margin:0 0 7px;line-height:1.6}
.panel-hint code{font-family:var(--mono);color:var(--muted);font-size:10.5px}
.vector-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 14px}
.vec-row{display:grid;grid-template-columns:34px 1fr 34px;align-items:center;gap:7px}
.vec-row label{font-size:12px;color:var(--muted);white-space:nowrap}
.vec-row output{font-family:var(--mono);font-size:10px;color:var(--dim);text-align:right}
input[type="range"]{
  -webkit-appearance:none;appearance:none;
  width:100%;height:18px;background:transparent;cursor:pointer;
}
input[type="range"]::-webkit-slider-runnable-track{
  height:3px;border-radius:2px;
  background:linear-gradient(90deg,rgba(242,184,75,.55),rgba(255,255,255,.12));
}
input[type="range"]::-moz-range-track{
  height:3px;border-radius:2px;
  background:linear-gradient(90deg,rgba(242,184,75,.55),rgba(255,255,255,.12));
}
input[type="range"]::-webkit-slider-thumb{
  -webkit-appearance:none;appearance:none;
  width:12px;height:12px;margin-top:-4.5px;
  border-radius:3px;border:1px solid #2b1e06;
  background:linear-gradient(180deg,var(--amber-2),var(--amber));
  box-shadow:0 0 10px rgba(242,184,75,.4);
}
input[type="range"]::-moz-range-thumb{
  width:11px;height:11px;border-radius:3px;border:1px solid #2b1e06;
  background:linear-gradient(180deg,var(--amber-2),var(--amber));
  box-shadow:0 0 10px rgba(242,184,75,.4);
}
input[type="range"]:disabled{opacity:.35;cursor:not-allowed}
.alpha-row{display:grid;grid-template-columns:82px 1fr 40px;align-items:center;gap:9px;margin-top:11px}
.alpha-row label{font-family:var(--mono);font-size:10px;color:var(--dim);letter-spacing:.06em;text-transform:uppercase}
.alpha-row output{font-family:var(--mono);font-size:10.5px;color:var(--amber);text-align:right}
.emo-textarea{
  width:100%;min-height:62px;resize:vertical;
  padding:10px 12px;border:1px solid var(--line);border-radius:9px;
  background:var(--inset);color:var(--text);font-size:13px;line-height:1.6;
}
.emo-textarea:focus{outline:none;border-color:rgba(242,184,75,.5)}
.check-row{display:flex;align-items:center;gap:8px;margin-top:9px;font-size:12px;color:var(--muted);cursor:pointer}
.check-row input{accent-color:var(--amber);width:14px;height:14px}
.check-row small{color:var(--dim);font-size:10.5px}

/* ---------- 参数 ---------- */
.advanced{
  border:1px solid var(--line);
  border-radius:10px;
  background:rgba(255,255,255,.01);
  overflow:hidden;
}
.advanced summary{
  display:flex;align-items:center;justify-content:space-between;gap:10px;
  padding:10px 12px;cursor:pointer;list-style:none;
  font-family:var(--mono);font-size:10.5px;color:var(--muted);letter-spacing:.08em;
  user-select:none;
}
.advanced summary::-webkit-details-marker{display:none}
.advanced summary::after{content:"+";color:var(--amber);font-family:var(--mono);font-size:14px}
.advanced[open] summary::after{content:"−"}
.advanced summary span{font-size:9.5px;color:var(--dim);letter-spacing:.02em}
.param-grid{
  display:grid;grid-template-columns:1fr 1fr;gap:10px 16px;
  padding:4px 12px 12px;border-top:1px solid var(--line);
}
.param-row{min-width:0}
.param-row .param-top{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:2px}
.param-row label{font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:.02em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.param-row output{font-family:var(--mono);font-size:10px;color:var(--amber);flex:none}
.param-row input[type="range"]{height:14px}
.param-row .help{display:none}
.extra-params{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:0 12px 12px;border-top:1px solid var(--line)}
.extra-params label{display:block;font-family:var(--mono);font-size:10px;color:var(--dim);margin-bottom:4px;letter-spacing:.04em}
.extra-params input{
  width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;
  background:var(--inset);color:var(--text);font-size:12px;
}
.extra-params input:focus{outline:none;border-color:rgba(242,184,75,.45)}

/* ---------- 生成 ---------- */
.fast-toggle{
  display:flex;align-items:center;gap:8px;margin:16px 0 9px;
  font-size:12px;color:var(--muted);cursor:pointer;user-select:none;
}
.fast-toggle input{accent-color:var(--amber);width:14px;height:14px}
.fast-toggle small{color:var(--dim);font-family:var(--mono);font-size:9.5px;letter-spacing:.03em}
.transport{display:flex;gap:9px;align-items:stretch}
.transport-btn{
  flex:1;
  display:flex;align-items:center;justify-content:center;gap:11px;
  min-height:48px;
  border:1px solid rgba(242,184,75,.65);
  border-radius:10px;
  background:linear-gradient(180deg,#f6c35d,#e2a32f);
  color:#191204;
  font-family:var(--display);
  font-size:14px;
  font-weight:700;
  letter-spacing:.16em;
  text-transform:uppercase;
  box-shadow:0 10px 30px rgba(242,184,75,.16),inset 0 1px 0 rgba(255,255,255,.45);
  transition:transform .15s,box-shadow .2s,filter .2s;
}
.transport-btn:hover{transform:translateY(-1px);box-shadow:0 14px 36px rgba(242,184,75,.22),inset 0 1px 0 rgba(255,255,255,.5);filter:saturate(1.05)}
.transport-btn:active{transform:translateY(0)}
.transport-btn:disabled{opacity:.45;cursor:not-allowed;transform:none;filter:grayscale(.2)}
.transport-btn .play{font-size:11px;transform:translateY(-1px)}
.cancel-btn{
  border:1px solid rgba(255,107,107,.4);border-radius:10px;
  background:rgba(255,107,107,.07);color:var(--red);
  padding:0 16px;font-size:12px;letter-spacing:.08em;
}
.cancel-btn:hover{background:rgba(255,107,107,.14)}
.progress{margin-top:12px;padding:11px 12px;border:1px solid var(--line);border-radius:9px;background:rgba(0,0,0,.18)}
.progress-head{display:flex;justify-content:space-between;gap:10px;font-family:var(--mono);font-size:10.5px;color:var(--muted);letter-spacing:.04em}
.progress-head span:last-child{color:var(--amber)}
.track{height:4px;margin-top:8px;border-radius:2px;background:rgba(255,255,255,.07);overflow:hidden}
.fill{
  height:100%;width:0;border-radius:2px;
  background:linear-gradient(90deg,var(--amber),var(--amber-2));
  box-shadow:0 0 12px rgba(242,184,75,.55);
  transition:width .35s ease;
  position:relative;overflow:hidden;
}
.fill::after{
  content:"";position:absolute;inset:0;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.55),transparent);
  transform:translateX(-100%);
  animation:shimmer 1.25s infinite;
}
.progress.failed .fill{background:var(--red);box-shadow:0 0 12px rgba(255,107,107,.45)}
.progress.failed .fill::after{display:none}
.queue{margin-top:6px;font-family:var(--mono);font-size:9.5px;color:var(--dim);min-height:12px;letter-spacing:.03em}
.inline-error{
  margin-top:12px;padding:11px 12px;
  border:1px solid rgba(255,107,107,.4);border-radius:9px;
  background:rgba(255,107,107,.06);
  font-size:12px;color:#ffc2c2;
  position:relative;
}
.inline-error strong{display:block;font-family:var(--mono);font-size:10px;letter-spacing:.06em;color:var(--red);margin-bottom:3px}
.inline-error .hint{margin-top:4px;color:#d99c9c;font-size:11px;line-height:1.55}
.inline-error .close{position:absolute;right:7px;top:5px;border:0;background:none;color:#d99c9c;font-size:15px;line-height:1;padding:2px 5px}

/* ---------- 监听器 ---------- */
.monitor-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}
.rec{display:flex;align-items:center;gap:8px;font-family:var(--mono);font-size:10px;letter-spacing:.22em;color:var(--muted)}
.rec i{width:7px;height:7px;border-radius:50%;background:var(--dim)}
.rec.live i{background:var(--red);box-shadow:0 0 12px rgba(255,107,107,.8);animation:pulse 1s infinite}
#monitorTime{font-family:var(--mono);font-size:10px;color:var(--dim);letter-spacing:.08em}
.wave-wrap{
  position:relative;border:1px solid var(--line);border-radius:11px;
  background:
    linear-gradient(180deg,rgba(5,9,13,.95),rgba(7,11,16,.95));
  overflow:hidden;
}
.wave-wrap::before{
  content:"";position:absolute;inset:0;
  background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px);
  background-size:100% 30px;pointer-events:none;
}
#wave{display:block;width:100%;height:156px;position:relative}
.wave-empty{
  position:absolute;inset:0;display:grid;place-items:center;
  font-family:var(--mono);font-size:10px;letter-spacing:.34em;color:#3c4856;
  pointer-events:none;
}
audio#player{
  width:100%;height:38px;margin-top:12px;
  color-scheme:dark;
  border-radius:8px;
  background:var(--inset);
}
audio#player:not([src]){opacity:.35}
.result-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:8px}
.result-actions .ghost{height:28px;font-size:11px;display:inline-flex;align-items:center;text-decoration:none}
.result-actions .ghost.disabled{opacity:.35;pointer-events:none}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:14px}
.stat{
  padding:9px 10px;border:1px solid var(--line);border-radius:8px;
  background:rgba(255,255,255,.012);min-width:0;
}
.stat .k{display:block;font-family:var(--mono);font-size:9px;letter-spacing:.08em;color:var(--dim);text-transform:uppercase}
.stat .v{display:block;margin-top:3px;font-family:var(--mono);font-size:12.5px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.warnings{
  margin-top:12px;padding:10px 12px;border:1px solid rgba(242,184,75,.35);border-radius:9px;
  background:rgba(242,184,75,.06);color:#e8c88a;font-size:11.5px;line-height:1.6;
}
.warnings strong{display:block;font-family:var(--mono);font-size:9.5px;letter-spacing:.1em;color:var(--amber);margin-bottom:3px}
.warnings ul{margin:0;padding-left:16px}
.raw{margin-top:12px;border-top:1px solid var(--line);padding-top:10px}
.raw summary{cursor:pointer;font-family:var(--mono);font-size:10px;color:var(--dim);letter-spacing:.06em}
.raw pre{
  max-height:220px;overflow:auto;margin:8px 0 0;padding:10px;
  border:1px solid var(--line);border-radius:8px;background:rgba(0,0,0,.22);
  font-family:var(--mono);font-size:10.5px;line-height:1.5;color:var(--muted);
}

/* ---------- 历史 ---------- */
.history{margin-top:18px;border-top:1px solid var(--line);padding-top:14px}
.history-head{display:flex;align-items:center;justify-content:space-between;gap:10px}
.history-head h3{margin:0;font-family:var(--display);font-size:11px;font-weight:600;letter-spacing:.2em;color:var(--muted);text-transform:uppercase}
.link{border:0;background:none;color:var(--dim);font-family:var(--mono);font-size:10px;letter-spacing:.06em}
.link:hover{color:var(--amber)}
#history{list-style:none;margin:8px 0 0;padding:0;display:flex;flex-direction:column;gap:5px;max-height:260px;overflow:auto}
.history-item{
  display:grid;grid-template-columns:26px minmax(0,1fr) auto;align-items:center;gap:9px;
  padding:7px 8px;border:1px solid transparent;border-radius:8px;
  transition:.16s;
}
.history-item:hover{border-color:var(--line);background:rgba(255,255,255,.02)}
.history-play{
  width:26px;height:26px;border:1px solid var(--line-strong);border-radius:6px;
  background:rgba(255,255,255,.02);color:var(--muted);font-size:9px;
}
.history-play:hover{color:var(--amber);border-color:rgba(242,184,75,.5)}
.history-info{min-width:0}
.history-name{display:block;font-family:var(--mono);font-size:10.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.history-meta{display:block;margin-top:1px;font-family:var(--mono);font-size:9px;color:var(--dim);letter-spacing:.03em}
.history-dl{color:var(--dim);text-decoration:none;font-family:var(--mono);font-size:13px;padding:2px 5px}
.history-dl:hover{color:var(--amber)}
.history-empty{padding:10px 2px;font-size:11.5px;color:var(--dim)}

/* ---------- 对话框 / toast / fatal ---------- */
dialog#uploadDialog{
  width:min(430px,calc(100vw - 32px));
  border:1px solid var(--line-strong);border-radius:14px;
  padding:0;background:linear-gradient(180deg,#111922,#0c1219);
  color:var(--text);box-shadow:0 30px 90px rgba(0,0,0,.6);
}
dialog#uploadDialog::backdrop{background:rgba(2,4,7,.7);backdrop-filter:blur(4px)}
#uploadForm{padding:20px}
#uploadForm h3{margin:0 0 5px;font-family:var(--display);font-size:15px;letter-spacing:.1em}
#uploadForm p{margin:0 0 14px;font-family:var(--mono);font-size:10px;color:var(--dim);word-break:break-all}
#uploadForm label{display:block;margin-top:10px;font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:.05em}
#uploadForm input{
  width:100%;margin-top:5px;padding:9px 10px;border:1px solid var(--line);border-radius:8px;
  background:var(--inset);color:var(--text);font-size:13px;
}
#uploadForm input:focus{outline:none;border-color:rgba(242,184,75,.5)}
.dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:17px}
.dialog-actions .ghost,.dialog-actions .primary{height:34px;padding:0 15px;border-radius:8px;font-size:12px}
.dialog-actions .primary{border:1px solid rgba(242,184,75,.6);background:linear-gradient(180deg,#f6c35d,#e2a32f);color:#191204;font-weight:700;letter-spacing:.08em}
.dialog-actions .primary:disabled{opacity:.5;cursor:wait}
.dialog-error{margin-top:10px;padding:8px 10px;border:1px solid rgba(255,107,107,.4);border-radius:8px;background:rgba(255,107,107,.07);color:#ffc2c2;font-size:11.5px;line-height:1.5;white-space:pre-line}
.toasts{position:fixed;right:18px;bottom:18px;z-index:50;display:flex;flex-direction:column;gap:8px;max-width:min(380px,calc(100vw - 36px))}
.toast{
  padding:10px 13px;border:1px solid var(--line-strong);border-left:3px solid var(--amber);
  border-radius:9px;background:rgba(14,20,28,.97);color:var(--text);
  font-size:12px;line-height:1.5;box-shadow:0 14px 40px rgba(0,0,0,.45);
  animation:toastIn .25s ease both;
}
.toast.error{border-left-color:var(--red)}
.toast.ok{border-left-color:var(--green)}
.toast.out{animation:toastOut .25s ease both}
.fatal{
  position:fixed;inset:0;z-index:60;display:grid;place-items:center;
  background:rgba(3,5,8,.85);backdrop-filter:blur(6px);padding:20px;
}
.fatal[hidden]{display:none}
.fatal > div{max-width:440px;padding:24px;border:1px solid var(--line-strong);border-radius:14px;background:var(--panel);box-shadow:0 30px 90px rgba(0,0,0,.6)}
.fatal h2{margin:0 0 8px;font-family:var(--display);font-size:17px;letter-spacing:.08em}
.fatal p{margin:0 0 16px;color:var(--muted);font-size:12.5px;line-height:1.65;white-space:pre-line}
.fatal button{border:1px solid rgba(242,184,75,.6);border-radius:8px;background:linear-gradient(180deg,#f6c35d,#e2a32f);color:#191204;padding:9px 16px;font-weight:700;letter-spacing:.08em}

/* ---------- 动效 ---------- */
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
@keyframes shimmer{to{transform:translateX(100%)}}
@keyframes toastIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes toastOut{to{opacity:0;transform:translateY(6px)}}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}
}

/* ---------- 响应式 ---------- */
@media (max-width:980px){
  .layout{grid-template-columns:minmax(0,1fr)}
  .monitor{position:static}
  .topbar{flex-wrap:wrap}
  .statusline{width:100%;justify-content:space-between}
}
@media (max-width:560px){
  .console,.monitor{padding:15px}
  .param-grid,.extra-params{grid-template-columns:1fr}
  .stats{grid-template-columns:repeat(2,minmax(0,1fr))}
  .vector-grid{grid-template-columns:1fr}
  .emo-grid button span{font-size:12px}
  .brand{font-size:18px}
  .device{display:none}
}
</style>
</head>
<body>

<header class="topbar">
  <div class="wordmark">
    <span class="mark">◈</span>
    <div>
      <div class="brand">INDEX<span>/</span>TTS2</div>
      <div class="sub">VOICE SYNTHESIS STUDIO</div>
    </div>
  </div>
  <div class="statusline">
    <div class="status-pill" id="daemonPill" data-state="off" title="守护进程状态">
      <span class="dot"></span><span id="daemonText">连接中…</span>
    </div>
    <div class="device" id="deviceText">—</div>
  </div>
</header>

<main class="layout">
  <section class="panel console">
    <div class="panel-head"><span class="index">01</span><h2>输入文本</h2><span class="rule"></span></div>
    <div class="text-wrap">
      <textarea id="text" placeholder="输入要合成的文本，中英文混合也可以。长文本会自动分段。" spellcheck="false"></textarea>
      <div class="text-meta"><span id="charCount">0 字符</span><span>Ctrl / ⌘ + Enter 生成</span></div>
      <div class="samples">
        <button type="button" data-sample="zh">中文示例</button>
        <button type="button" data-sample="en">English</button>
        <button type="button" data-sample="emo">情感示例</button>
      </div>
    </div>

    <div class="panel-head"><span class="index">02</span><h2>音色</h2><span class="rule"></span></div>
    <div class="voice-row">
      <div class="select-wrap">
        <select id="voice" aria-label="音色"></select>
        <span class="select-arrow">⌄</span>
      </div>
      <button type="button" class="ghost" id="previewBtn" title="用当前音色试听一句">试听</button>
      <button type="button" class="ghost" id="uploadBtn">上传</button>
      <input id="fileInput" type="file" accept=".wav,.mp3,.flac,.m4a,.aac,.ogg,.opus,.webm" hidden>
    </div>
    <div class="voice-note" id="voiceNote"></div>

    <div class="panel-head"><span class="index">03</span><h2>情感</h2><span class="rule"></span></div>
    <div class="emo-grid" id="emoTabs" role="group" aria-label="情感模式">
      <button type="button" data-mode="0" class="active" aria-pressed="true"><b>00</b><span>音色自带</span></button>
      <button type="button" data-mode="1" aria-pressed="false"><b>01</b><span>情感参考</span></button>
      <button type="button" data-mode="2" aria-pressed="false"><b>02</b><span>八维向量</span></button>
      <button type="button" data-mode="3" aria-pressed="false"><b>03</b><span>文字描述</span></button>
    </div>

    <div class="emo-panel" data-panel="0">
      <p class="panel-hint">直接使用音色参考音频自带的情感；不额外传给模型情感参数。</p>
    </div>

    <div class="emo-panel" data-panel="1" hidden>
      <p class="panel-hint">选一段音频作为情感参考，强度由下方 alpha 控制。</p>
      <div class="select-wrap">
        <select id="emoAudio" aria-label="情感参考音频"></select>
        <span class="select-arrow">⌄</span>
      </div>
    </div>

    <div class="emo-panel" data-panel="2" hidden>
      <p class="panel-hint">八维向量顺序：喜 / 怒 / 哀 / 惧 / 厌恶 / 低落 / 惊喜 / 平静。服务端会做 bias 归一化。</p>
      <div class="vector-grid" id="vectorGrid"></div>
      <label class="check-row"><input type="checkbox" id="emoRandom"> 随机采样情感权重 <small>emo_random</small></label>
    </div>

    <div class="emo-panel" data-panel="3" hidden>
      <p class="panel-hint">用一句话描述情绪，会多跑一次情感分类模型（约多 30% 时间）。留空则使用正文。</p>
      <textarea id="emoText" class="emo-textarea" placeholder="例如：委屈巴巴、压抑着怒火的平静" spellcheck="false"></textarea>
    </div>

    <div class="alpha-row" id="alphaRow" hidden>
      <label for="emoAlpha">情感强度</label>
      <input type="range" id="emoAlpha" min="0" max="1" step="0.01" value="0.65">
      <output id="emoAlphaValue">0.65</output>
    </div>

    <div class="panel-head"><span class="index">04</span><h2>参数</h2><span class="rule"></span></div>
    <details class="advanced" id="advanced">
      <summary>高级生成参数 <span>与上游 webui 默认值一致</span></summary>
      <div class="param-grid" id="paramGrid"></div>
      <div class="extra-params">
        <div>
          <label for="seed">随机种子 seed</label>
          <input id="seed" type="number" step="1" placeholder="留空 = 随机">
        </div>
        <div>
          <label for="outputName">输出文件名</label>
          <input id="outputName" type="text" maxlength="64" placeholder="自动 (webui_…wav)">
        </div>
      </div>
    </details>

    <label class="fast-toggle" title="num_beams 3 → 1，速度约快 3 倍">
      <input type="checkbox" id="fast"> 快速模式 <small>beam 3 → 1 · 约 3x</small>
    </label>

    <div class="transport">
      <button type="button" id="generateBtn" class="transport-btn">
        <span class="play">▶</span><span id="generateLabel">生成语音</span>
      </button>
      <button type="button" id="cancelBtn" class="cancel-btn" hidden>取消</button>
    </div>

    <div class="progress" id="progress" hidden>
      <div class="progress-head"><span id="progressPhase">准备中…</span><span id="progressPct">0%</span></div>
      <div class="track"><div class="fill" id="progressFill"></div></div>
      <div class="queue" id="queueText"></div>
    </div>

    <div class="inline-error" id="inlineError" hidden>
      <button type="button" class="close" id="errorClose" aria-label="关闭">×</button>
      <strong id="errorCode">ERROR</strong>
      <div id="errorMessage"></div>
      <div class="hint" id="errorHint"></div>
    </div>
  </section>

  <aside class="panel monitor">
    <div class="monitor-head">
      <span class="rec" id="rec"><i></i>MONITOR</span>
      <span id="monitorTime">NO SIGNAL</span>
    </div>
    <div class="wave-wrap">
      <canvas id="wave" aria-label="音频波形"></canvas>
      <div class="wave-empty" id="waveEmpty">AWAITING SIGNAL</div>
    </div>
    <audio id="player" controls preload="metadata"></audio>
    <div class="result-actions">
      <a id="downloadLink" class="ghost disabled" href="#" download>下载 wav</a>
      <button type="button" id="copyPath" class="ghost" disabled>复制路径</button>
    </div>
    <div class="stats" id="stats">
      <div class="stat"><span class="k">音频</span><span class="v">—</span></div>
      <div class="stat"><span class="k">耗时</span><span class="v">—</span></div>
      <div class="stat"><span class="k">RTF</span><span class="v">—</span></div>
      <div class="stat"><span class="k">分段</span><span class="v">—</span></div>
      <div class="stat"><span class="k">音色</span><span class="v">—</span></div>
      <div class="stat"><span class="k">模式</span><span class="v">—</span></div>
    </div>
    <div class="warnings" id="warnings" hidden>
      <strong>WARNINGS</strong>
      <ul id="warningList"></ul>
    </div>
    <details class="raw" id="rawDetails" hidden>
      <summary>原始结果 JSON</summary>
      <pre id="rawJson"></pre>
    </details>

    <div class="history">
      <div class="history-head">
        <h3>最近生成</h3>
        <button type="button" id="refreshHistory" class="link">刷新</button>
      </div>
      <ul id="history"></ul>
    </div>
  </aside>
</main>

<dialog id="uploadDialog">
  <form id="uploadForm" method="dialog">
    <h3>上传参考音频</h3>
    <p id="uploadFile">—</p>
    <label for="uploadName">音色名称<input id="uploadName" maxlength="64" required autocomplete="off"></label>
    <label for="uploadNotes">备注（可选）<input id="uploadNotes" maxlength="200" autocomplete="off"></label>
    <div class="dialog-error" id="uploadError" hidden></div>
    <div class="dialog-actions">
      <button type="button" id="uploadCancel" class="ghost">取消</button>
      <button type="submit" id="uploadSave" class="primary">保存音色</button>
    </div>
  </form>
</dialog>

<div class="toasts" id="toasts" aria-live="polite"></div>

<div class="fatal" id="fatal" hidden>
  <div>
    <h2>无法连接 WebUI 服务</h2>
    <p id="fatalMsg">请确认 `indextts2 webui` 仍在运行。</p>
    <button type="button" id="fatalRetry">重试</button>
  </div>
</div>

<script>
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const dom = {
    text: $("#text"),
    charCount: $("#charCount"),
    voice: $("#voice"),
    voiceNote: $("#voiceNote"),
    previewBtn: $("#previewBtn"),
    uploadBtn: $("#uploadBtn"),
    fileInput: $("#fileInput"),
    emoTabs: $("#emoTabs"),
    emoAudio: $("#emoAudio"),
    vectorGrid: $("#vectorGrid"),
    emoRandom: $("#emoRandom"),
    emoText: $("#emoText"),
    alphaRow: $("#alphaRow"),
    emoAlpha: $("#emoAlpha"),
    emoAlphaValue: $("#emoAlphaValue"),
    paramGrid: $("#paramGrid"),
    advanced: $("#advanced"),
    seed: $("#seed"),
    outputName: $("#outputName"),
    fast: $("#fast"),
    generateBtn: $("#generateBtn"),
    generateLabel: $("#generateLabel"),
    cancelBtn: $("#cancelBtn"),
    progress: $("#progress"),
    progressPhase: $("#progressPhase"),
    progressPct: $("#progressPct"),
    progressFill: $("#progressFill"),
    queueText: $("#queueText"),
    inlineError: $("#inlineError"),
    errorCode: $("#errorCode"),
    errorMessage: $("#errorMessage"),
    errorHint: $("#errorHint"),
    errorClose: $("#errorClose"),
    daemonPill: $("#daemonPill"),
    daemonText: $("#daemonText"),
    deviceText: $("#deviceText"),
    rec: $("#rec"),
    monitorTime: $("#monitorTime"),
    canvas: $("#wave"),
    waveEmpty: $("#waveEmpty"),
    player: $("#player"),
    downloadLink: $("#downloadLink"),
    copyPath: $("#copyPath"),
    stats: $("#stats"),
    warnings: $("#warnings"),
    warningList: $("#warningList"),
    rawDetails: $("#rawDetails"),
    rawJson: $("#rawJson"),
    history: $("#history"),
    refreshHistory: $("#refreshHistory"),
    uploadDialog: $("#uploadDialog"),
    uploadForm: $("#uploadForm"),
    uploadFile: $("#uploadFile"),
    uploadName: $("#uploadName"),
    uploadNotes: $("#uploadNotes"),
    uploadError: $("#uploadError"),
    uploadCancel: $("#uploadCancel"),
    uploadSave: $("#uploadSave"),
    toasts: $("#toasts"),
    fatal: $("#fatal"),
    fatalMsg: $("#fatalMsg"),
    fatalRetry: $("#fatalRetry"),
  };

  const state = {
    config: null,
    voices: [],
    mode: 0,
    paramControls: {},
    vectorControls: [],
    currentJob: null,
    jobToken: 0,
    pollFailures: 0,
    lastAudioUrl: null,
    lastResult: null,
    wavePeaks: null,
    waveToken: 0,
    pendingFile: null,
    uploading: false,
    statusTimer: null,
  };

  class ApiError extends Error {
    constructor(data, status) {
      const err = data && typeof data === "object" ? data.error : null;
      super((err && (err.message || err.code)) || (typeof data === "string" ? data : "请求失败") || `HTTP ${status}`);
      this.name = "ApiError";
      this.status = status;
      this.data = data;
    }
    get hint() {
      const err = this.data && typeof this.data === "object" ? this.data.error : null;
      return (err && err.hint) || "";
    }
    get code() {
      const err = this.data && typeof this.data === "object" ? this.data.error : null;
      return (err && err.code) || `HTTP_${this.status}`;
    }
  }

  async function api(path, options = {}) {
    const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
    let resp;
    try {
      resp = await fetch(path, Object.assign({}, options, { headers }));
    } catch (err) {
      throw new ApiError({ error: { code: "NETWORK", message: "无法连接 WebUI 服务", hint: String(err) } }, 0);
    }
    const ctype = resp.headers.get("content-type") || "";
    let data = null;
    if (ctype.includes("application/json")) {
      data = await resp.json().catch(() => null);
    } else {
      data = await resp.text().catch(() => "");
    }
    if (!resp.ok) throw new ApiError(data, resp.status);
    return data;
  }

  function toast(message, kind = "info") {
    const node = document.createElement("div");
    node.className = `toast ${kind}`;
    node.textContent = message;
    dom.toasts.appendChild(node);
    setTimeout(() => {
      node.classList.add("out");
      setTimeout(() => node.remove(), 280);
    }, 4200);
  }

  function showFatal(message) {
    dom.fatalMsg.textContent = message || "请确认 `indextts2 webui` 仍在运行。";
    dom.fatal.hidden = false;
  }

  function hideFatal() {
    dom.fatal.hidden = true;
  }

  function updateCharCount() {
    const n = dom.text.value.length;
    dom.charCount.textContent = `${n} 字符`;
  }

  function formatNumber(value, digits = 2) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return Number(value).toFixed(digits);
  }

  function formatDate(seconds) {
    if (!seconds) return "—";
    try {
      return new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      }).format(new Date(seconds * 1000));
    } catch (e) {
      return "—";
    }
  }

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || seconds === "") return "—";
    const s = Number(seconds);
    if (!Number.isFinite(s) || s < 0) return "—";
    const m = Math.floor(s / 60);
    const rest = (s % 60).toFixed(1);
    return m > 0 ? `${m}:${rest.padStart(4, "0")}` : `${rest}s`;
  }

  function setMode(mode) {
    state.mode = mode;
    $$("#emoTabs button").forEach((btn) => {
      const active = Number(btn.dataset.mode) === mode;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    $$(".emo-panel").forEach((panel) => {
      panel.hidden = Number(panel.dataset.panel) !== mode;
    });
    dom.alphaRow.hidden = mode === 0;
    if (mode > 0) {
      const defaults = state.config?.emotion_default_alpha || {};
      const value = defaults[String(mode)];
      if (value !== null && value !== undefined) {
        dom.emoAlpha.value = String(value);
      }
      dom.emoAlphaValue.textContent = Number(dom.emoAlpha.value).toFixed(2);
    }
  }

  function renderVoices(voices, preferred) {
    state.voices = Array.isArray(voices) ? voices : [];
    const current = preferred || dom.voice.value;
    dom.voice.replaceChildren();
    dom.emoAudio.replaceChildren();

    if (!state.voices.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "（音色库为空，请先上传）";
      dom.voice.appendChild(opt);
    } else {
      for (const voice of state.voices) {
        const opt = document.createElement("option");
        opt.value = voice.name;
        opt.textContent = voice.notes ? `${voice.name} — ${voice.notes}` : voice.name;
        dom.voice.appendChild(opt);

        const emoOpt = document.createElement("option");
        emoOpt.value = voice.name;
        emoOpt.textContent = voice.notes ? `${voice.name} — ${voice.notes}` : voice.name;
        dom.emoAudio.appendChild(emoOpt);
      }

      const names = state.voices.map((v) => v.name);
      let selected = current;
      if (!selected || !names.includes(selected)) {
        const def = state.config?.default_voice;
        selected = def && names.includes(def) ? def : (names.includes("voice_01") ? "voice_01" : names[0]);
      }
      dom.voice.value = selected;

      let emoSelected = dom.emoAudio.value;
      if (!emoSelected || !names.includes(emoSelected)) {
        emoSelected = names.includes("emo_sad") ? "emo_sad" : (names.includes("emo_hate") ? "emo_hate" : names[0]);
      }
      dom.emoAudio.value = emoSelected;
    }
    updateVoiceNote();
  }

  function updateVoiceNote() {
    const voice = state.voices.find((v) => v.name === dom.voice.value);
    if (!voice) {
      dom.voiceNote.textContent = "";
      return;
    }
    const bits = [voice.source ? `source: ${voice.source}` : ""];
    if (voice.id) bits.push(`id: ${voice.id.slice(0, 8)}`);
    if (voice.notes) bits.push(voice.notes);
    dom.voiceNote.textContent = bits.filter(Boolean).join("  ·  ");
  }

  function renderParams(params) {
    dom.paramGrid.replaceChildren();
    state.paramControls = {};
    const entries = Object.entries(params || {});
    for (const [name, meta] of entries) {
      const row = document.createElement("div");
      row.className = "param-row";

      const top = document.createElement("div");
      top.className = "param-top";
      const label = document.createElement("label");
      label.textContent = name;
      label.title = meta.help || name;
      label.htmlFor = `param-${name}`;
      const output = document.createElement("output");
      top.appendChild(label);
      top.appendChild(output);

      const input = document.createElement("input");
      input.type = "range";
      input.id = `param-${name}`;
      input.min = String(meta.min);
      input.max = String(meta.max);
      input.step = String(meta.step || (meta.type === "int" ? 1 : 0.01));
      input.value = String(meta.default);
      input.title = meta.help || name;

      const sync = () => {
        const v = Number(input.value);
        output.textContent = meta.type === "int" ? String(Math.round(v)) : v.toFixed(2);
      };
      input.addEventListener("input", sync);
      sync();

      row.appendChild(top);
      row.appendChild(input);
      dom.paramGrid.appendChild(row);
      state.paramControls[name] = { input, meta };
    }
    syncNumBeamsState();
  }

  function syncNumBeamsState() {
    const beams = state.paramControls.num_beams;
    if (beams) beams.input.disabled = dom.fast.checked;
  }

  function renderVectors(order) {
    dom.vectorGrid.replaceChildren();
    state.vectorControls = [];
    for (const item of order || []) {
      const row = document.createElement("div");
      row.className = "vec-row";
      const label = document.createElement("label");
      label.textContent = item.cn;
      label.title = item.name;
      label.htmlFor = `vec-${item.name}`;
      const input = document.createElement("input");
      input.type = "range";
      input.min = "0";
      input.max = "1";
      input.step = "0.05";
      input.value = "0";
      input.id = `vec-${item.name}`;
      const output = document.createElement("output");
      output.textContent = "0.00";
      input.addEventListener("input", () => {
        output.textContent = Number(input.value).toFixed(2);
      });
      row.appendChild(label);
      row.appendChild(input);
      row.appendChild(output);
      dom.vectorGrid.appendChild(row);
      state.vectorControls.push({ input, name: item.name });
    }
  }

  function renderStatus(data) {
    const preload = data.preload || {};
    const daemon = data.daemon;
    let cls = "off";
    let label = "未启动";
    let title = "首次生成时会自动加载模型";

    if (daemon && daemon.state === "ready") {
      cls = "ok";
      label = "就绪";
      title = `pid ${daemon.pid} · port ${daemon.port}`;
    } else if (daemon && daemon.state === "loading") {
      cls = "load";
      label = `加载中 ${daemon.phase || ""}`.trim();
      title = `阶段 ${daemon.phase_index}/${daemon.phase_total}`;
    } else if (daemon) {
      if (["draining", "stopping", "stopped"].includes(daemon.state)) {
        cls = "off";
        label = "正在退出";
      } else {
        cls = "err";
        const stateNames = { failed: "启动失败", degraded: "已退役" };
        label = stateNames[daemon.state] || daemon.state || "异常";
        title = (daemon.error && daemon.error.message) || "";
      }
    } else if (preload.state === "error") {
      cls = "err";
      label = "启动失败";
      title = (preload.error && (preload.error.message || preload.error.code)) || "守护进程启动失败";
    } else if (preload.state === "loading") {
      cls = "load";
      label = "加载中";
    }

    dom.daemonPill.dataset.state = cls;
    dom.daemonText.textContent = label;
    dom.daemonPill.title = title || "";

    const bits = [];
    if (daemon && daemon.device) bits.push(`${daemon.device} · fp16 ${daemon.fp16 ? "on" : "off"}`);
    if (daemon && daemon.queue_depth > 0) bits.push(`队列 ${daemon.queue_depth}`);
    if (daemon && daemon.running_job) bits.push(`运行 ${String(daemon.running_job).slice(0, 10)}`);
    dom.deviceText.textContent = bits.length ? bits.join("  ·  ") : "—";
  }

  async function refreshStatus() {
    if (state.statusTimer) {
      clearTimeout(state.statusTimer);
      state.statusTimer = null;
    }
    try {
      const data = await api("/api/status");
      renderStatus(data);
    } catch (err) {
      dom.daemonPill.dataset.state = "err";
      dom.daemonText.textContent = "服务不可达";
      dom.daemonPill.title = err.message || "";
      dom.deviceText.textContent = "—";
    } finally {
      state.statusTimer = setTimeout(refreshStatus, 5000);
    }
  }

  function renderStats(result) {
    if (!result) {
      dom.stats.replaceChildren(...[
        ["音频", "—"], ["耗时", "—"], ["RTF", "—"],
        ["分段", "—"], ["音色", "—"], ["模式", "—"],
      ].map(([k, v]) => {
        const node = document.createElement("div");
        node.className = "stat";
        const kk = document.createElement("span");
        kk.className = "k";
        kk.textContent = k;
        const vv = document.createElement("span");
        vv.className = "v";
        vv.textContent = v;
        node.appendChild(kk);
        node.appendChild(vv);
        return node;
      }));
      return;
    }
    const items = [
      ["音频", result.audio_seconds != null ? `${formatNumber(result.audio_seconds)} s` : "—"],
      ["耗时", result.duration_s != null ? `${formatNumber(result.duration_s)} s` : "—"],
      ["RTF", result.rtf != null ? formatNumber(result.rtf) : "—"],
      ["分段", result.text_segments != null ? String(result.text_segments) : "—"],
      ["音色", result.voice ? result.voice.name : "—"],
      ["模式", result.params && result.params.emo_mode != null ? `0${result.params.emo_mode}` : "—"],
    ];
    dom.stats.replaceChildren(...items.map(([k, v]) => {
      const node = document.createElement("div");
      node.className = "stat";
      const kk = document.createElement("span");
      kk.className = "k";
      kk.textContent = k;
      const vv = document.createElement("span");
      vv.className = "v";
      vv.textContent = v;
      vv.title = v;
      node.appendChild(kk);
      node.appendChild(vv);
      return node;
    }));
  }

  function renderWarnings(list) {
    const warnings = Array.isArray(list) ? list.filter(Boolean) : [];
    if (!warnings.length) {
      dom.warnings.hidden = true;
      dom.warningList.replaceChildren();
      return;
    }
    dom.warnings.hidden = false;
    dom.warningList.replaceChildren(...warnings.map((text) => {
      const li = document.createElement("li");
      li.textContent = String(text);
      return li;
    }));
  }

  function renderRaw(result) {
    if (!result) {
      dom.rawDetails.hidden = true;
      dom.rawJson.textContent = "";
      return;
    }
    dom.rawDetails.hidden = false;
    dom.rawJson.textContent = JSON.stringify(result, null, 2);
  }

  function setDownload(url, path) {
    if (url) {
      dom.downloadLink.href = `${url}${url.includes("?") ? "&" : "?"}download=1`;
      dom.downloadLink.classList.remove("disabled");
    } else {
      dom.downloadLink.href = "#";
      dom.downloadLink.classList.add("disabled");
    }
    dom.copyPath.disabled = !path;
    dom.copyPath.dataset.path = path || "";
  }

  function updateMonitorTime(text) {
    dom.monitorTime.textContent = text || "NO SIGNAL";
  }

  function setBusy(busy) {
    dom.generateBtn.disabled = busy;
    dom.previewBtn.disabled = busy;
    dom.uploadBtn.disabled = busy;
    dom.cancelBtn.hidden = !busy;
    dom.generateLabel.textContent = busy ? "生成中…" : "生成语音";
    dom.rec.classList.toggle("live", busy);
  }

  function showProgress(phase, progress, queue) {
    dom.progress.hidden = false;
    dom.progress.classList.remove("failed");
    const pct = Math.max(0, Math.min(1, Number(progress) || 0));
    dom.progressFill.style.width = `${(pct * 100).toFixed(1)}%`;
    dom.progressPhase.textContent = phase || "处理中…";
    dom.progressPct.textContent = `${Math.round(pct * 100)}%`;
    dom.queueText.textContent = queue || "";
  }

  function failProgress(phase) {
    dom.progress.hidden = false;
    dom.progress.classList.add("failed");
    dom.progressPhase.textContent = phase || "失败";
    dom.progressPct.textContent = "ERROR";
    dom.queueText.textContent = "";
  }

  function clearInlineError() {
    dom.inlineError.hidden = true;
    dom.errorCode.textContent = "ERROR";
    dom.errorMessage.textContent = "";
    dom.errorHint.textContent = "";
  }

  function showInlineError(info) {
    const err = info || {};
    dom.inlineError.hidden = false;
    dom.errorCode.textContent = err.code || "ERROR";
    dom.errorMessage.textContent = err.message || String(err) || "未知错误";
    dom.errorHint.textContent = err.hint || "";
  }

  const PHASE_LABELS = {
    starting: "启动中",
    gpt: "加载 GPT",
    semantic_codec: "加载语义编解码器",
    s2mel: "加载 S2Mel",
    campplus: "加载 CAMPPlus",
    bigvgan: "加载 BigVGAN",
    text_normalizer: "加载文本归一化",
    tokenizer: "加载分词器",
    ready: "就绪",
  };

  function mapPhase(phase) {
    if (!phase) return "处理中…";
    if (PHASE_LABELS[phase]) return PHASE_LABELS[phase];
    const text = String(phase);
    if (text.includes("starting inference")) return "开始推理";
    if (text.includes("text processing")) return "文本处理";
    if (text.toLowerCase().includes("gpt")) return "GPT 推理";
    if (text.includes("speech synthesis")) return "语音合成";
    if (text === "done") return "完成";
    return text;
  }

  function buildPayload(kind) {
    const text = dom.text.value.trim();
    if (!text) {
      toast("先输入要合成的文本", "error");
      dom.text.focus();
      return null;
    }

    const params = {};
    for (const [name, control] of Object.entries(state.paramControls)) {
      const value = Number(control.input.value);
      if (Number.isFinite(value)) params[name] = value;
    }

    const payload = {
      text,
      voice: dom.voice.value || undefined,
      emo_mode: Number(state.mode),
      params,
    };

    if (rootFastEnabled()) payload.fast = true;

    const seedRaw = dom.seed.value.trim();
    if (seedRaw) {
      const seed = Number(seedRaw);
      if (!Number.isInteger(seed)) {
        toast("seed 需要整数", "error");
        dom.seed.focus();
        return null;
      }
      payload.seed = seed;
    }

    if (state.mode > 0) {
      payload.emo_alpha = Number(dom.emoAlpha.value);
    }
    if (state.mode === 1) {
      if (!dom.emoAudio.value) {
        toast("模式 1 需要选择情感参考音频", "error");
        return null;
      }
      payload.emo_audio = dom.emoAudio.value;
    } else if (state.mode === 2) {
      payload.emo_vec = state.vectorControls.map((item) => Number(item.input.value));
      payload.emo_random = !!dom.emoRandom.checked;
    } else if (state.mode === 3) {
      payload.emo_text = dom.emoText.value;
    }

    if (kind === "preview") {
      payload.text = "你好，这是当前音色的试听。";
      payload.emo_mode = 0;
      payload.fast = true;
      delete payload.emo_alpha;
      delete payload.emo_audio;
      delete payload.emo_vec;
      delete payload.emo_random;
      delete payload.emo_text;
      delete payload.output_name;
    } else {
      const outputName = dom.outputName.value.trim();
      if (outputName) payload.output_name = outputName;
    }

    return payload;
  }

  function rootFastEnabled() {
    return !!dom.fast.checked;
  }

  async function startJob(kind = "normal") {
    if (state.currentJob) return;
    clearInlineError();
    const payload = buildPayload(kind);
    if (!payload) return;

    state.jobToken += 1;
    const token = state.jobToken;
    setBusy(true);
    showProgress(kind === "preview" ? "提交试听…" : "提交任务…", 0, "");

    try {
      const data = await api("/api/jobs", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (token !== state.jobToken) return;
      state.currentJob = { id: data.job_id, token };
      if (data.queue_position !== undefined && data.queue_position !== null) {
        dom.queueText.textContent = `排队位置 #${data.queue_position}`;
      }
      pollJob();
    } catch (err) {
      if (token !== state.jobToken) return;
      setBusy(false);
      failProgress("提交失败");
      showInlineError({ code: err.code, message: err.message, hint: err.hint });
      toast(err.message || "提交失败", "error");
    }
  }

  async function pollJob() {
    const job = state.currentJob;
    if (!job) return;
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(job.id)}`);
      if (!state.currentJob || state.currentJob.token !== job.token) return;
      state.pollFailures = 0;

      const stateName = data.state;
      if (stateName === "done") {
        const result = data.result || {};
        finishJob(result);
        return;
      }
      if (stateName === "failed") {
        failJob(data.error || { code: "JOB_FAILED", message: "任务失败" });
        return;
      }

      const progress = Number(data.progress) || 0;
      const queue = data.queue_position !== undefined && data.queue_position !== null && stateName === "queued"
        ? `排队位置 #${data.queue_position}`
        : "";
      showProgress(mapPhase(data.phase || stateName), progress, queue);
    } catch (err) {
      state.pollFailures += 1;
      if (state.pollFailures >= 4) {
        const jobId = state.currentJob?.id;
        state.currentJob = null;
        setBusy(false);
        failProgress("状态查询失败");
        showInlineError({
          code: err.code,
          message: err.message,
          hint: (err.hint || "") + (jobId ? `（任务 ${jobId} 可能仍在执行）` : ""),
        });
        return;
      }
    }
    setTimeout(pollJob, 700);
  }

  function finishJob(result) {
    state.currentJob = null;
    setBusy(false);
    showProgress("完成", 1, "");
    setTimeout(() => {
      if (!state.currentJob && dom.progressPhase.textContent === "完成") {
        dom.progress.hidden = true;
      }
    }, 1400);

    state.lastResult = result;
    const url = result.audio_url || (result.job_id ? `/api/audio/${encodeURIComponent(result.job_id)}` : null);
    state.lastAudioUrl = url;

    renderStats(result);
    renderWarnings(result.warnings);
    renderRaw(result);
    setDownload(url, result.output_path || "");
    updateMonitorTime(new Date().toLocaleTimeString("zh-CN", { hour12: false }));

    if (url) {
      dom.player.src = url;
      dom.player.play().catch(() => {});
      drawWaveform(url);
    }
    refreshHistory();
    toast("生成完成", "ok");
  }

  function failJob(error) {
    state.currentJob = null;
    setBusy(false);
    failProgress((error && (error.code || error.message)) || "失败");
    showInlineError(error || { code: "JOB_FAILED", message: "任务失败" });
    toast((error && error.message) || "任务失败", "error");
  }

  async function cancelJob() {
    const job = state.currentJob;
    if (!job) return;
    try {
      await api(`/api/jobs/${encodeURIComponent(job.id)}`, { method: "DELETE" });
      toast("已取消", "ok");
    } catch (err) {
      toast(err.message || "无法取消", "error");
    }
  }

  // ---------- 波形 ----------
  const wave = {
    ctx: dom.canvas.getContext("2d"),
    dpr: 1,
    w: 0,
    h: 0,
  };

  function setupCanvas() {
    const rect = dom.canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.floor(rect.width));
    const h = Math.max(1, Math.floor(rect.height || 156));
    if (dom.canvas.width !== Math.floor(w * dpr) || dom.canvas.height !== Math.floor(h * dpr)) {
      dom.canvas.width = Math.floor(w * dpr);
      dom.canvas.height = Math.floor(h * dpr);
    }
    wave.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    wave.dpr = dpr;
    wave.w = w;
    wave.h = h;
  }

  function drawEmptyWave() {
    setupCanvas();
    const ctx = wave.ctx;
    ctx.clearRect(0, 0, wave.w, wave.h);
    ctx.strokeStyle = "rgba(94,234,212,.14)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, wave.h / 2);
    ctx.lineTo(wave.w, wave.h / 2);
    ctx.stroke();
  }

  function drawPeaks(peaks) {
    setupCanvas();
    const ctx = wave.ctx;
    const w = wave.w;
    const h = wave.h;
    ctx.clearRect(0, 0, w, h);
    const mid = h / 2;

    ctx.strokeStyle = "rgba(94,234,212,.13)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, mid);
    ctx.lineTo(w, mid);
    ctx.stroke();

    if (!peaks || !peaks.length) {
      drawEmptyWave();
      return;
    }

    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, "rgba(255,210,122,.95)");
    grad.addColorStop(0.5, "rgba(242,184,75,.55)");
    grad.addColorStop(1, "rgba(255,210,122,.95)");
    ctx.fillStyle = grad;
    ctx.shadowColor = "rgba(242,184,75,.35)";
    ctx.shadowBlur = 7;

    const step = w / peaks.length;
    const barW = Math.max(1, step * 0.58);
    for (let i = 0; i < peaks.length; i += 1) {
      const amp = Math.max(0.7, peaks[i] * mid * 0.94);
      ctx.fillRect(i * step + (step - barW) / 2, mid - amp, barW, amp * 2);
    }
    ctx.shadowBlur = 0;
  }

  function computePeaks(buffer, buckets = 420) {
    const data = buffer.getChannelData(0);
    const step = Math.max(1, Math.floor(data.length / buckets));
    const peaks = new Float32Array(buckets);
    let maxAll = 0;
    for (let i = 0; i < buckets; i += 1) {
      const start = i * step;
      const end = Math.min(data.length, start + step);
      let max = 0;
      for (let j = start; j < end; j += 4) {
        const v = Math.abs(data[j]);
        if (v > max) max = v;
      }
      peaks[i] = max;
      if (max > maxAll) maxAll = max;
    }
    if (maxAll > 0) {
      for (let i = 0; i < peaks.length; i += 1) peaks[i] /= maxAll;
    }
    return peaks;
  }

  async function drawWaveform(url) {
    const token = ++state.waveToken;
    dom.waveEmpty.hidden = true;
    try {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const declared = Number(resp.headers.get("content-length") || "0");
      if (declared > 50 * 1024 * 1024) {
        throw new Error("waveform-too-large");
      }
      const raw = await resp.arrayBuffer();
      if (raw.byteLength > 50 * 1024 * 1024) {
        throw new Error("waveform-too-large");
      }
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) throw new Error("no-audio-context");
      const ac = new AudioCtx();
      let decoded;
      try {
        decoded = await ac.decodeAudioData(raw.slice(0));
      } finally {
        if (ac.close) ac.close().catch(() => {});
      }
      if (token !== state.waveToken) return;
      state.wavePeaks = computePeaks(decoded);
      drawPeaks(state.wavePeaks);
    } catch (err) {
      if (token !== state.waveToken) return;
      state.wavePeaks = null;
      drawEmptyWave();
      dom.waveEmpty.hidden = false;
      dom.waveEmpty.textContent = err.message === "waveform-too-large" ? "WAVE TOO LARGE" : "NO SIGNAL";
    }
  }

  // ---------- 历史 ----------
  async function refreshHistory() {
    try {
      const data = await api("/api/history?limit=12");
      const files = Array.isArray(data.files) ? data.files : [];
      dom.history.replaceChildren();
      if (!files.length) {
        const li = document.createElement("li");
        li.className = "history-empty";
        li.textContent = "还没有生成记录。";
        dom.history.appendChild(li);
        return;
      }
      for (const item of files) {
        const li = document.createElement("li");
        li.className = "history-item";

        const play = document.createElement("button");
        play.type = "button";
        play.className = "history-play";
        play.textContent = "▶";
        play.title = "播放";
        play.addEventListener("click", () => playHistory(item));

        const info = document.createElement("div");
        info.className = "history-info";
        const name = document.createElement("span");
        name.className = "history-name";
        name.textContent = item.name;
        name.title = item.name;
        const meta = document.createElement("span");
        meta.className = "history-meta";
        meta.textContent = `${formatDate(item.mtime)} · ${formatDuration(item.audio_seconds)} · ${(item.size / 1024).toFixed(0)} KB`;
        info.appendChild(name);
        info.appendChild(meta);

        const dl = document.createElement("a");
        dl.className = "history-dl";
        dl.href = `${item.audio_url}${item.audio_url.includes("?") ? "&" : "?"}download=1`;
        dl.title = "下载";
        dl.textContent = "↓";

        li.appendChild(play);
        li.appendChild(info);
        li.appendChild(dl);
        dom.history.appendChild(li);
      }
    } catch (err) {
      dom.history.replaceChildren();
      const li = document.createElement("li");
      li.className = "history-empty";
      li.textContent = `历史加载失败：${err.message}`;
      dom.history.appendChild(li);
    }
  }

  function playHistory(item) {
    state.lastAudioUrl = item.audio_url;
    state.lastResult = null;
    dom.player.src = item.audio_url;
    dom.player.play().catch(() => {});
    drawWaveform(item.audio_url);
    updateMonitorTime(formatDate(item.mtime));
    renderStats({ audio_seconds: item.audio_seconds, voice: null });
    renderWarnings([]);
    renderRaw(null);
    setDownload(item.audio_url, "");
    toast(`播放：${item.name}`);
  }

  // ---------- 上传 ----------
  function resetUploadDialog() {
    state.pendingFile = null;
    state.uploading = false;
    dom.uploadSave.disabled = false;
    dom.uploadError.hidden = true;
    dom.uploadError.textContent = "";
    dom.uploadName.value = "";
    dom.uploadNotes.value = "";
    dom.fileInput.value = "";
  }

  function openUploadDialog(file) {
    const okExt = /\.(wav|mp3|flac|m4a|aac|ogg|opus|webm)$/i.test(file.name);
    if (!okExt) {
      toast("请选择 wav/mp3/flac/m4a/aac/ogg/opus/webm 音频", "error");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      toast("参考音频超过 20 MiB，请先裁剪/压缩", "error");
      return;
    }
    state.pendingFile = file;
    const stem = file.name.replace(/\.[^.]+$/, "") || "我的音色";
    dom.uploadName.value = stem.slice(0, 64);
    dom.uploadNotes.value = "";
    dom.uploadFile.textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
    dom.uploadError.hidden = true;
    dom.uploadSave.disabled = false;
    if (typeof dom.uploadDialog.showModal === "function") {
      dom.uploadDialog.showModal();
    } else {
      dom.uploadDialog.setAttribute("open", "");
    }
    setTimeout(() => dom.uploadName.focus(), 40);
  }

  async function submitUpload(event) {
    event.preventDefault();
    if (state.uploading) return;
    const file = state.pendingFile;
    if (!file) return;
    const name = dom.uploadName.value.trim();
    if (!name) {
      dom.uploadError.hidden = false;
      dom.uploadError.textContent = "音色名称不能为空。";
      return;
    }
    state.uploading = true;
    dom.uploadSave.disabled = true;
    dom.uploadError.hidden = true;
    try {
      const dataUrl = await readFileAsDataUrl(file);
      const base64 = String(dataUrl).split(",", 2)[1] || "";
      const data = await api("/api/voices", {
        method: "POST",
        body: JSON.stringify({
          name,
          filename: file.name,
          notes: dom.uploadNotes.value.trim(),
          audio_base64: base64,
        }),
      });
      const voice = data.voice || {};
      const selected = voice.name || name;
      const cfg = await api("/api/config");
      state.config = cfg;
      renderVoices(cfg.voices, selected);
      dom.uploadDialog.close();
      resetUploadDialog();
      toast(`音色已添加：${selected}`, "ok");
    } catch (err) {
      dom.uploadError.hidden = false;
      dom.uploadError.textContent = err.message + (err.hint ? `\n${err.hint}` : "");
    } finally {
      state.uploading = false;
      dom.uploadSave.disabled = false;
    }
  }

  function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error || new Error("读取文件失败"));
      reader.readAsDataURL(file);
    });
  }

  // ---------- 采样快捷填充 ----------
  const SAMPLES = {
    zh: "夜色渐深，城市的灯光在窗外一盏一盏亮起来。她把杯子放回桌上，轻轻说了一句：我们回家吧。",
    en: "The rain had stopped by the time we reached the station. In the distance, the last train was waiting under a pale blue sky.",
    emo: "你怎么能这样对我……我等了整整一个晚上，连一句解释都没有。",
  };

  function applySample(key) {
    if (!SAMPLES[key]) return;
    dom.text.value = SAMPLES[key];
    updateCharCount();
    dom.text.focus();
  }

  // ---------- 事件绑定 ----------
  dom.text.addEventListener("input", updateCharCount);
  dom.text.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      startJob("normal");
    }
  });
  dom.voice.addEventListener("change", updateVoiceNote);
  dom.previewBtn.addEventListener("click", () => startJob("preview"));
  dom.uploadBtn.addEventListener("click", () => dom.fileInput.click());
  dom.fileInput.addEventListener("change", () => {
    const file = dom.fileInput.files && dom.fileInput.files[0];
    if (file) openUploadDialog(file);
  });
  $$(".samples button").forEach((btn) => {
    btn.addEventListener("click", () => applySample(btn.dataset.sample));
  });
  dom.emoTabs.addEventListener("click", (event) => {
    const btn = event.target.closest("button[data-mode]");
    if (btn) setMode(Number(btn.dataset.mode));
  });
  dom.emoAlpha.addEventListener("input", () => {
    dom.emoAlphaValue.textContent = Number(dom.emoAlpha.value).toFixed(2);
  });
  dom.fast.addEventListener("change", syncNumBeamsState);
  dom.generateBtn.addEventListener("click", () => startJob("normal"));
  dom.cancelBtn.addEventListener("click", cancelJob);
  dom.errorClose.addEventListener("click", clearInlineError);
  dom.refreshHistory.addEventListener("click", refreshHistory);
  dom.copyPath.addEventListener("click", async () => {
    const path = dom.copyPath.dataset.path || "";
    if (!path) return;
    try {
      await navigator.clipboard.writeText(path);
      toast("路径已复制", "ok");
    } catch (err) {
      toast("复制失败，请手动选择路径", "error");
    }
  });
  dom.downloadLink.addEventListener("click", (event) => {
    if (dom.downloadLink.classList.contains("disabled")) event.preventDefault();
  });
  dom.player.addEventListener("error", () => {
    if (dom.player.getAttribute("src")) toast("音频加载失败，文件可能已被清理", "error");
  });
  dom.uploadForm.addEventListener("submit", submitUpload);
  dom.uploadCancel.addEventListener("click", () => {
    dom.uploadDialog.close();
    resetUploadDialog();
  });
  dom.uploadDialog.addEventListener("close", resetUploadDialog);
  dom.fatalRetry.addEventListener("click", () => window.location.reload());

  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => {
      if (state.wavePeaks) drawPeaks(state.wavePeaks);
      else drawEmptyWave();
    }).observe(dom.canvas);
  } else {
    window.addEventListener("resize", () => {
      if (state.wavePeaks) drawPeaks(state.wavePeaks);
      else drawEmptyWave();
    });
  }

  // ---------- 启动 ----------
  async function boot() {
    updateCharCount();
    drawEmptyWave();
    dom.voice.innerHTML = '<option value="">加载音色…</option>';
    try {
      const cfg = await api("/api/config");
      state.config = cfg;
      hideFatal();
      renderVoices(cfg.voices, cfg.default_voice);
      renderParams(cfg.params);
      renderVectors(cfg.emotion_vector_order);
      setMode(0);
      refreshStatus();
      refreshHistory();
    } catch (err) {
      showFatal(`${err.message}${err.hint ? `\n${err.hint}` : ""}`);
    }
  }

  boot();
})();
</script>
</body>
</html>
'''
