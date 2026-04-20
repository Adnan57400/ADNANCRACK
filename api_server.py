# api_server.py — CRACK SMS API Server v3.0
"""
FastAPI server:
  • /                   — Public live OTP dashboard (no token required)
  • /api/public/otps    — Public JSON OTPs endpoint (no token)
  • /api/public/stats   — Public stats (no token)
  • /api/sms            — Authenticated, panel-filtered (token required)
  • /api/stats          — Authenticated stats (token required)
  • /health             — Health check (public)
"""

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select, func

sys.path.insert(0, os.path.dirname(__file__))

from database import (
    AsyncSessionLocal, APIToken, Number, History,
    get_api_token, update_api_token_last_used,
)

try:
    from logging_system import bootstrap, get_logger, audit_api
    bootstrap()
    logger = get_logger("api_server")
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("api_server")
    async def audit_api(**kwargs): pass

app = FastAPI(title="CRACK SMS API", version="3.0.0", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


# ── Auth helper ────────────────────────────────────────────────────────────────
async def validate_token(token: str) -> Optional[APIToken]:
    if not token:
        return None
    t = await get_api_token(token)
    if not t or t.status != "ACTIVE":
        return None
    await update_api_token_last_used(token)
    return t


# ── Shared OTP fetch logic ─────────────────────────────────────────────────────
async def _fetch_otps(limit=120, allowed_panels=None, date_str=None):
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(History).order_by(History.created_at.desc()).limit(limit * 3)
        )).scalars().all()
    out = []
    for r in rows:
        if date_str:
            try:
                if r.created_at.date() != datetime.strptime(date_str, "%Y-%m-%d").date():
                    continue
            except ValueError:
                pass
        if allowed_panels:
            cat = r.category or ""
            if not any(str(p).lower() in cat.lower() for p in allowed_panels):
                continue
        cat = r.category or ""
        if " - " in cat:
            country, service = cat.split(" - ", 1)[0].strip(), cat.split(" - ", 1)[1].strip()
        else:
            country, service = "Unknown", cat or "Unknown"
        out.append({
            "number":      f"+{r.phone_number}" if r.phone_number else "—",
            "service":     service,
            "country":     country,
            "otp":         r.otp or "—",
            "message":     f"OTP for {service}: {r.otp}" if r.otp else "—",
            "received_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if len(out) >= limit:
            break
    return out


async def _fetch_stats():
    async with AsyncSessionLocal() as s:
        total    = await s.scalar(select(func.count(History.id))) or 0
        today    = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_ct = await s.scalar(select(func.count(History.id)).where(History.created_at >= today)) or 0
        rows     = (await s.execute(
            select(History.category, func.count(History.id))
            .group_by(History.category).order_by(func.count(History.id).desc()).limit(10)
        )).all()
        services = {}
        for cat, cnt in rows:
            svc = cat.split(" - ", 1)[1].strip() if cat and " - " in cat else (cat or "Unknown")
            services[svc] = services.get(svc, 0) + cnt
        since   = datetime.now() - timedelta(hours=24)
        h_rows  = (await s.execute(
            select(History.created_at).where(History.created_at >= since)
        )).scalars().all()
        hourly  = {}
        for ts in h_rows:
            b = ts.strftime("%Y-%m-%d %H:00")
            hourly[b] = hourly.get(b, 0) + 1
    return {"total_otps": total, "otps_today": today_ct,
            "by_service": services, "hourly_last_24h": hourly}


# ── PUBLIC endpoints (no token) ────────────────────────────────────────────────

@app.get("/api/public/otps")
async def public_otps(limit: int = Query(120, ge=1, le=500)):
    try:
        data = await _fetch_otps(limit)
        return {"status": "success", "total_records": len(data), "data": data}
    except Exception as e:
        logger.exception("public_otps: %s", e)
        return {"status": "error", "message": str(e), "data": []}


@app.get("/api/public/stats")
async def public_stats_ep():
    try:
        return {"status": "success", **(await _fetch_stats())}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── AUTHENTICATED endpoints ────────────────────────────────────────────────────

@app.get("/api/sms")
async def get_otps(
    request: Request,
    token: str = Query(...),
    date: Optional[str]  = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    api_token = await validate_token(token)
    if not api_token:
        raise HTTPException(401, "Not authorized")
    try:
        allowed: list = json.loads(api_token.panels_data or "[]")
        otps = await _fetch_otps(limit, allowed_panels=allowed or None, date_str=date)
        ip   = request.client.host if request.client else None
        await audit_api(token_name=api_token.name, endpoint="/api/sms",
                        records_returned=len(otps), ip=ip)
        return {"status": "success", "token_name": api_token.name,
                "api_dev": api_token.api_dev or "Anonymous",
                "total_records": len(otps), "data": otps}
    except Exception as e:
        logger.exception("get_otps: %s", e)
        return {"status": "error", "message": str(e), "data": []}


@app.get("/api/stats")
async def get_stats_ep(token: str = Query(...)):
    t = await validate_token(token)
    if not t:
        raise HTTPException(401, "Not authorized")
    try:
        return {"status": "success", **(await _fetch_stats())}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "CRACK SMS API v3",
            "timestamp": datetime.now().isoformat()}


# ══════════════════════════════════════════════════════════════════════════════
#  BEAUTIFUL PUBLIC HOMEPAGE — shows all OTPs, no token needed
# ══════════════════════════════════════════════════════════════════════════════
_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CRACK SMS — Live OTP Feed</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#08090d;--s0:#0e0f16;--s1:#13151f;--s2:#1a1d2a;--bdr:#252836;
  --acc:#6c63ff;--acc2:#ff6584;--grn:#00d4aa;--yel:#ffbe0b;--red:#ff4757;
  --txt:#dde1f0;--sub:#6b7194;
  --mono:'JetBrains Mono',monospace;--sans:'Space Grotesk',sans-serif;--r:14px;
}
body{font-family:var(--sans);background:var(--bg);color:var(--txt);min-height:100vh;overflow-x:hidden}
body::before{content:'';position:fixed;inset:0;
  background-image:linear-gradient(rgba(108,99,255,.025) 1px,transparent 1px),
    linear-gradient(90deg,rgba(108,99,255,.025) 1px,transparent 1px);
  background-size:44px 44px;pointer-events:none;z-index:0}
.orb{position:fixed;border-radius:50%;filter:blur(130px);pointer-events:none;z-index:0}
.orb1{width:700px;height:700px;background:rgba(108,99,255,.06);top:-250px;right:-150px}
.orb2{width:500px;height:500px;background:rgba(0,212,170,.04);bottom:-150px;left:-150px}
/* topbar */
.tb{position:sticky;top:0;z-index:100;display:flex;align-items:center;
  justify-content:space-between;padding:13px 30px;
  background:rgba(8,9,13,.88);backdrop-filter:blur(24px);
  border-bottom:1px solid var(--bdr)}
.logo{display:flex;align-items:center;gap:10px;font-size:1.05rem;font-weight:700;letter-spacing:-.3px}
.logo-box{width:33px;height:33px;border-radius:9px;
  background:linear-gradient(135deg,var(--acc),var(--acc2));
  display:grid;place-items:center;font-size:.95rem}
.logo-txt{background:linear-gradient(135deg,#a89dff,#ff8fab);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.tb-r{display:flex;align-items:center;gap:10px}
.pill{font-size:.7rem;font-family:var(--mono);padding:5px 12px;border-radius:20px;
  border:1px solid;display:flex;align-items:center;gap:5px}
.pill-live{color:var(--grn);border-color:rgba(0,212,170,.3);background:rgba(0,212,170,.07)}
.pill-live::before{content:'';width:6px;height:6px;background:var(--grn);border-radius:50%;
  animation:blink 1.6s infinite}
.pill-cnt{color:var(--sub);border-color:var(--bdr);background:var(--s1)}
@keyframes blink{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(0,212,170,.5)}
  50%{opacity:.6;box-shadow:0 0 0 5px transparent}}
/* layout */
.wrap{position:relative;z-index:1;max-width:1420px;margin:0 auto;padding:28px 22px}
/* stats strip */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:26px}
@media(max-width:860px){.stats{grid-template-columns:repeat(2,1fr)}}
@media(max-width:480px){.stats{grid-template-columns:1fr}}
.s-card{background:var(--s1);border:1px solid var(--bdr);border-radius:var(--r);
  padding:19px 22px;position:relative;overflow:hidden;transition:.2s}
.s-card:hover{border-color:var(--acc);transform:translateY(-2px)}
.s-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:var(--line,var(--acc))}
.s-card.g{--line:var(--grn)}.s-card.y{--line:var(--yel)}.s-card.r{--line:var(--red)}
.s-ico{position:absolute;top:14px;right:14px;font-size:1.5rem;opacity:.12}
.s-val{font-size:2rem;font-weight:700;font-family:var(--mono);margin-bottom:3px}
.s-lbl{font-size:.68rem;color:var(--sub);letter-spacing:.8px;text-transform:uppercase}
/* charts */
.charts{display:grid;grid-template-columns:3fr 2fr;gap:16px;margin-bottom:26px}
@media(max-width:860px){.charts{grid-template-columns:1fr}}
.c-box{background:var(--s1);border:1px solid var(--bdr);border-radius:var(--r);padding:20px 22px}
.c-box h4{font-size:.68rem;letter-spacing:.8px;text-transform:uppercase;color:var(--sub);margin-bottom:16px}
.c-box canvas{max-height:195px}
/* toolbar */
.bar{display:flex;gap:10px;margin-bottom:18px;flex-wrap:wrap;align-items:center}
.bar input,.bar select{background:var(--s1);border:1px solid var(--bdr);border-radius:9px;
  color:var(--txt);font-family:var(--sans);font-size:.8rem;padding:9px 14px;
  outline:none;transition:border-color .2s}
.bar input{flex:1;min-width:180px}
.bar input:focus,.bar select:focus{border-color:var(--acc)}
.btn{padding:9px 18px;border-radius:9px;font-size:.8rem;font-weight:600;
  font-family:var(--sans);cursor:pointer;border:none;outline:none;transition:.2s}
.btn-a{background:var(--acc);color:#fff}.btn-a:hover{filter:brightness(1.15);transform:translateY(-1px)}
.btn-o{background:transparent;color:var(--sub);border:1px solid var(--bdr)}
.btn-o:hover{border-color:var(--acc);color:var(--acc)}
.ml{margin-left:auto}
/* grid */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
/* card */
.card{background:var(--s1);border:1px solid var(--bdr);border-radius:var(--r);
  padding:17px 19px;transition:all .22s;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;left:0;top:10px;bottom:10px;width:3px;
  border-radius:0 2px 2px 0;background:linear-gradient(var(--acc),var(--acc2))}
.card::after{content:'';position:absolute;inset:0;border-radius:var(--r);
  background:linear-gradient(135deg,rgba(108,99,255,.05),transparent);
  opacity:0;transition:.22s;pointer-events:none}
.card:hover{border-color:var(--acc);transform:translateY(-3px);
  box-shadow:0 14px 44px rgba(108,99,255,.13)}
.card:hover::after{opacity:1}
.c-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:13px}
.svc{font-size:.67rem;font-weight:700;letter-spacing:.6px;text-transform:uppercase;
  padding:3px 9px;border-radius:5px;background:rgba(108,99,255,.12);
  color:var(--acc);border:1px solid rgba(108,99,255,.18)}
.c-ts{font-size:.67rem;font-family:var(--mono);color:var(--sub)}
.c-num{font-family:var(--mono);font-size:.87rem;font-weight:500;margin-bottom:2px}
.c-loc{font-size:.73rem;color:var(--sub);margin-bottom:13px}
.otp-row{display:flex;align-items:center;justify-content:space-between;
  background:var(--s2);border:1px solid var(--bdr);border-radius:9px;
  padding:10px 13px;margin-bottom:11px}
.otp{font-family:var(--mono);font-size:1.4rem;font-weight:700;letter-spacing:5px;
  background:linear-gradient(135deg,var(--grn),#00ffcc);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.cp{background:rgba(0,212,170,.1);color:var(--grn);border:1px solid rgba(0,212,170,.22);
  border-radius:6px;padding:5px 11px;font-size:.68rem;font-weight:700;
  font-family:var(--mono);cursor:pointer;transition:.2s}
.cp:hover{background:rgba(0,212,170,.2)}.cp.ok{background:rgba(0,212,170,.25);color:#fff}
.c-msg{font-size:.72rem;color:var(--sub);line-height:1.5;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.new-tag{position:absolute;top:11px;right:11px;font-size:.58rem;font-weight:700;
  letter-spacing:.5px;text-transform:uppercase;padding:2px 6px;border-radius:4px;
  background:rgba(255,190,11,.14);color:var(--yel);border:1px solid rgba(255,190,11,.28);
  animation:fadeOut 5s forwards}
@keyframes fadeOut{0%,70%{opacity:1}100%{opacity:0}}
/* skeleton */
.sk{background:var(--s1);border:1px solid var(--bdr);border-radius:var(--r);padding:18px}
.sk-l{height:11px;border-radius:4px;margin:8px 0;
  background:linear-gradient(90deg,var(--s2) 25%,var(--bdr) 50%,var(--s2) 75%);
  background-size:300%;animation:shim 1.9s infinite}
@keyframes shim{0%{background-position:100% 0}100%{background-position:-100% 0}}
.w40{width:40%}.w60{width:60%}.w80{width:80%}
/* empty */
.empty{grid-column:1/-1;text-align:center;padding:70px 20px;color:var(--sub)}
.e-ico{font-size:2.8rem;margin-bottom:14px;opacity:.35}
/* toast */
.toast{position:fixed;bottom:26px;right:26px;z-index:999;
  background:var(--grn);color:#08090d;font-weight:700;
  padding:10px 18px;border-radius:10px;font-size:.8rem;
  opacity:0;transform:translateY(8px);transition:.28s;pointer-events:none}
.toast.on{opacity:1;transform:translateY(0)}
/* footer */
footer{text-align:center;padding:24px;color:var(--sub);font-size:.73rem;
  border-top:1px solid var(--bdr);margin-top:36px;position:relative;z-index:1}
footer a{color:var(--acc);text-decoration:none}
@media(max-width:580px){.tb{padding:11px 14px}.wrap{padding:14px 12px}}
</style>
</head>
<body>
<div class="orb orb1"></div><div class="orb orb2"></div>

<div class="tb">
  <div class="logo">
    <div class="logo-box">🔐</div>
    <span class="logo-txt">CRACK SMS</span>
  </div>
  <div class="tb-r">
    <div class="pill pill-live">LIVE</div>
    <div class="pill pill-cnt" id="cntBadge">— OTPs</div>
  </div>
</div>

<div class="wrap">
  <div class="stats">
    <div class="s-card">  <div class="s-ico">🔑</div><div class="s-val" id="sT">—</div><div class="s-lbl">Total OTPs</div></div>
    <div class="s-card g"><div class="s-ico">📅</div><div class="s-val" id="sTd">—</div><div class="s-lbl">Today</div></div>
    <div class="s-card y"><div class="s-ico">📱</div><div class="s-val" id="sS">—</div><div class="s-lbl">Top Service</div></div>
    <div class="s-card r"><div class="s-ico">⏱</div><div class="s-val" id="sH">—</div><div class="s-lbl">Last Hour</div></div>
  </div>

  <div class="charts">
    <div class="c-box"><h4>OTPs per hour — last 24 h</h4><canvas id="hChart"></canvas></div>
    <div class="c-box"><h4>Top services</h4><canvas id="sChart"></canvas></div>
  </div>

  <div class="bar">
    <input id="q" placeholder="🔍  Search service, number or OTP…" oninput="render()">
    <select id="sf" onchange="render()"><option value="">All services</option></select>
    <button class="btn btn-a ml" onclick="load()">↻ Refresh</button>
    <button class="btn btn-o" onclick="openApi()">API Docs</button>
  </div>

  <div class="grid" id="grid">
    <div class="sk"><div class="sk-l w60"></div><div class="sk-l w80"></div><div class="sk-l w40"></div></div>
    <div class="sk"><div class="sk-l w80"></div><div class="sk-l w60"></div><div class="sk-l w40"></div></div>
    <div class="sk"><div class="sk-l w40"></div><div class="sk-l w80"></div><div class="sk-l w60"></div></div>
    <div class="sk"><div class="sk-l w60"></div><div class="sk-l w40"></div><div class="sk-l w80"></div></div>
  </div>
</div>

<footer>CRACK SMS &copy; 2024 &nbsp;·&nbsp; <a href="/api/docs">API Docs</a> &nbsp;·&nbsp; <a href="/health">Health</a></footer>
<div class="toast" id="toast"></div>

<script>
let all=[], seen=new Set(), hC=null, sC=null;

window.onload=()=>{
  initCharts();
  load(); loadStats();
  setInterval(load, 5000);
  setInterval(loadStats, 30000);
};

async function load(){
  try{
    const r=await fetch('/api/public/otps?limit=120');
    const d=await r.json();
    if(d.status==='success'){
      const fresh=new Set(d.data.map(o=>o.number+o.received_at));
      const isNew=o=>!seen.has(o.number+o.received_at);
      const newKeys=new Set(d.data.filter(isNew).map(o=>o.number+o.received_at));
      all=d.data; seen=fresh;
      document.getElementById('cntBadge').textContent=all.length+' OTPs';
      buildFilter();
      render(newKeys);
    }
  }catch(e){console.error(e)}
}

async function loadStats(){
  try{
    const r=await fetch('/api/public/stats');
    const d=await r.json();
    if(d.status==='success'){
      document.getElementById('sT').textContent=d.total_otps.toLocaleString();
      document.getElementById('sTd').textContent=d.otps_today.toLocaleString();
      const top=Object.entries(d.by_service||{}).sort((a,b)=>b[1]-a[1])[0];
      document.getElementById('sS').textContent=top?top[0]:'—';
      const now=new Date(), b=`${now.getFullYear()}-${p(now.getMonth()+1)}-${p(now.getDate())} ${p(now.getHours())}:00`;
      document.getElementById('sH').textContent=(d.hourly_last_24h?.[b]||0).toLocaleString();
      updateCharts(d);
    }
  }catch{}
}

function render(newKeys=new Set()){
  const q=document.getElementById('q').value.toLowerCase();
  const sf=document.getElementById('sf').value;
  const items=all.filter(o=>{
    if(sf&&o.service!==sf)return false;
    if(q&&!(o.number+o.otp+o.service+o.country).toLowerCase().includes(q))return false;
    return true;
  });
  const g=document.getElementById('grid');
  if(!items.length){
    g.innerHTML='<div class="empty"><div class="e-ico">📭</div>No OTPs match your filters</div>';
    return;
  }
  g.innerHTML=items.map((o,i)=>`
  <div class="card" style="animation:ci .32s ease ${Math.min(i,12)*.035}s both">
    ${newKeys.has(o.number+o.received_at)?'<span class="new-tag">NEW</span>':''}
    <div class="c-top">
      <span class="svc">${e(o.service)}</span>
      <span class="c-ts">${e(o.received_at)}</span>
    </div>
    <div class="c-num">${e(o.number)}</div>
    <div class="c-loc">📍 ${e(o.country)}</div>
    <div class="otp-row">
      <span class="otp">${fmt(e(o.otp))}</span>
      <button class="cp" onclick="cp('${e(o.otp)}',this)">COPY</button>
    </div>
    <div class="c-msg" title="${e(o.message)}">${e(o.message)}</div>
  </div>`).join('');
}

function buildFilter(){
  const sel=document.getElementById('sf'), cur=sel.value;
  const svcs=[...new Set(all.map(o=>o.service))].sort();
  sel.innerHTML='<option value="">All services</option>'+svcs.map(s=>`<option${s===cur?' selected':''}>${e(s)}</option>`).join('');
}

function initCharts(){
  const fg='#6b7194', grid='#1a1d2a';
  Chart.defaults.color=fg; Chart.defaults.font.family="'JetBrains Mono'";
  hC=new Chart(document.getElementById('hChart'),{type:'line',
    data:{labels:[],datasets:[{label:'OTPs',data:[],borderColor:'#6c63ff',
      backgroundColor:'rgba(108,99,255,.1)',fill:true,tension:.45,
      pointRadius:3,pointHoverRadius:7,borderWidth:2}]},
    options:{plugins:{legend:{display:false}},scales:{
      x:{grid:{color:grid},ticks:{maxTicksLimit:8}},
      y:{grid:{color:grid},beginAtZero:true,ticks:{precision:0}}},animation:{duration:350}}
  });
  sC=new Chart(document.getElementById('sChart'),{type:'doughnut',
    data:{labels:[],datasets:[{data:[],
      backgroundColor:['#6c63ff','#ff6584','#00d4aa','#ffbe0b','#5ce1e6','#ff4757','#a29bfe'],
      borderWidth:2,borderColor:'#13151f'}]},
    options:{plugins:{legend:{position:'bottom',labels:{boxWidth:10,padding:14}}},
      cutout:'68%',animation:{duration:350}}
  });
}

function updateCharts(d){
  if(!hC||!sC)return;
  const h=d.hourly_last_24h||{}, keys=Object.keys(h).slice(-24);
  hC.data.labels=keys.map(k=>k.slice(11,16));
  hC.data.datasets[0].data=keys.map(k=>h[k]);
  hC.update('none');
  const sv=d.by_service||{};
  sC.data.labels=Object.keys(sv);
  sC.data.datasets[0].data=Object.values(sv);
  sC.update('none');
}

function cp(otp,btn){
  navigator.clipboard.writeText(otp).then(()=>{
    btn.textContent='✓ DONE';btn.classList.add('ok');
    setTimeout(()=>{btn.textContent='COPY';btn.classList.remove('ok')},1800);
    toast('OTP copied!');
  });
}
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('on');setTimeout(()=>t.classList.remove('on'),2200)}
function e(s){const d=document.createElement('div');d.textContent=String(s||'');return d.innerHTML}
function fmt(s){if(!s||s==='—')return s;if(s.length===6)return s.slice(0,3)+'-'+s.slice(3);return s}
function p(n){return String(n).padStart(2,'0')}
function openApi(){window.open('/api/docs','_blank')}
const st=document.createElement('style');
st.textContent='@keyframes ci{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}';
document.head.appendChild(st);
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def home():
    return _HTML


@app.get("/api/docs", response_class=HTMLResponse)
async def api_docs():
    return """<!DOCTYPE html><html><head><title>CRACK SMS API</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono&family=Space+Grotesk:wght@400;700&display=swap" rel="stylesheet">
<style>*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Space Grotesk',sans-serif;background:#08090d;color:#dde1f0;padding:40px 24px}
.w{max-width:860px;margin:0 auto}h1{font-size:2rem;margin-bottom:8px}
h1 span{color:#6c63ff}sub{color:#6b7194;font-size:.85rem}
.ep{background:#13151f;border:1px solid #252836;border-radius:12px;padding:24px;margin:20px 0}
.ep h3{color:#6c63ff;margin-bottom:6px}.ep p{color:#6b7194;font-size:.83rem;margin-bottom:12px}
pre{background:#0e0f16;border:1px solid #252836;border-radius:8px;padding:14px;
  font-family:'JetBrains Mono',monospace;font-size:.73rem;overflow-x:auto;color:#a29bfe}
.b{display:inline-block;padding:2px 9px;border-radius:5px;font-size:.67rem;font-weight:700;margin-right:6px}
.get{background:rgba(0,212,170,.12);color:#00d4aa}.pub{background:rgba(108,99,255,.12);color:#6c63ff}
.auth{background:rgba(255,190,11,.12);color:#ffbe0b}
a{color:#6c63ff;text-decoration:none}</style></head><body><div class="w">
<h1>CRACK SMS <span>API</span></h1>
<p style="color:#6b7194;margin:8px 0 28px">Base URL: <code style="color:#a29bfe">https://mywebsite.com</code></p>
<div class="ep"><span class="b get">GET</span><span class="b pub">PUBLIC</span>
<h3>/api/public/otps</h3><p>All recent OTPs — no token required.</p>
<pre>GET /api/public/otps?limit=100</pre></div>
<div class="ep"><span class="b get">GET</span><span class="b pub">PUBLIC</span>
<h3>/api/public/stats</h3><p>Aggregated statistics — no token required.</p>
<pre>GET /api/public/stats</pre></div>
<div class="ep"><span class="b get">GET</span><span class="b auth">AUTH</span>
<h3>/api/sms</h3><p>OTPs filtered to token's panels.</p>
<pre>GET /api/sms?token=YOUR_TOKEN&amp;limit=100&amp;date=2024-01-15</pre></div>
<div class="ep"><span class="b get">GET</span><span class="b auth">AUTH</span>
<h3>/api/stats</h3><p>Authenticated statistics.</p>
<pre>GET /api/stats?token=YOUR_TOKEN</pre></div>
<p style="margin-top:28px;color:#6b7194"><a href="/">← Back to dashboard</a></p>
</div></body></html>"""


@app.exception_handler(HTTPException)
async def http_ex(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code,
                        content={"status": "error", "message": exc.detail})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
