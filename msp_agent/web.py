"""Flask admin console. Tickets are always read from and written to Zoho Desk."""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, g, jsonify, redirect, render_template_string, request, session, url_for
from werkzeug.exceptions import HTTPException

from . import db, escalation, kb, tickets, zoho

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("HELPDESK_SESSION_SECRET") or secrets.token_urlsafe(48)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                  SESSION_COOKIE_SECURE=os.getenv("HELPDESK_COOKIE_SECURE", "false").lower() == "true",
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
KB_LICENSE_CHOICES = [
    {"id": "CC-BY-4.0", "label": "CC BY 4.0"},
    {"id": "CC-BY-SA-4.0", "label": "CC BY-SA 4.0"},
    {"id": "CC0-1.0", "label": "CC0 1.0"},
    {"id": "Public-Domain", "label": "Public domain (verify source)"},
    {"id": "Internal-Original", "label": "Original MSP content"},
    {"id": "Permission-Confirmed", "label": "Other permission confirmed"},
]


@app.errorhandler(Exception)
def api_error(error):
    if isinstance(error, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify(error=error.description), error.code
        return error
    app.logger.exception("Helpdesk request failed", exc_info=error)
    if request.path.startswith("/api/"):
        message = str(error) if isinstance(error, RuntimeError) else "The request could not be completed."
        return jsonify(error=message), 502 if isinstance(error, RuntimeError) else 500
    return "The request could not be completed.", 500


def _conn():
    if "db" not in g:
        g.db = db.connect()
        settings = {r["key"]: r["value"] for r in g.db.execute("SELECT key,value FROM app_settings")}
        os.environ["MSP_TZ"] = settings.get("timezone", os.getenv("MSP_TZ", "Australia/Sydney"))
        os.environ["MSP_HOURS"] = settings.get("hours", os.getenv("MSP_HOURS", "08:00-18:00"))
        os.environ["MSP_COVERAGE_MODE"] = settings.get("coverage_mode", os.getenv("MSP_COVERAGE_MODE", "24x7"))
        os.environ["MSP_AFTER_HOURS_MODE"] = settings.get("after_hours_mode", "on_call")
    return g.db


@app.teardown_appcontext
def _close_db(_error):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _audit(action: str, kind: str, entity_id: str, details: str = ""):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _conn().execute("INSERT INTO audit_log(actor,action,entity_type,entity_id,details,created_at) "
                    "VALUES(?,?,?,?,?,?)", (session.get("user", "admin"), action, kind,
                                            str(entity_id), details[:2000], now))
    _conn().commit()


def _login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            if request.path.startswith("/api/"):
                return jsonify(error="Authentication required"), 401
            return redirect(url_for("login"))
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
            if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
                return jsonify(error="Invalid CSRF token"), 403
        return fn(*args, **kwargs)
    return wrapped


LOGIN = """<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'><meta name=theme-color content='#f4f6fa'><link rel=icon type=image/svg+xml href=/static/relaydesk-icon.svg><title>RelayDesk · Staff sign in</title>
<style>:root{color-scheme:light;--brand:#3158a6;--line:#dbe4ec}*{box-sizing:border-box}body{font:15px Inter,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:radial-gradient(ellipse at 50% 0,#e7edfb 0,transparent 55%),#f4f6fa;color:#17263c;display:grid;place-items:center;min-height:100vh;margin:0}.card{background:white;padding:38px;border:1px solid var(--line);box-shadow:0 18px 50px rgba(23,43,77,.09);border-radius:18px;width:min(420px,90vw)}.brand{margin:0 0 20px}.brand img{display:block;width:min(250px,100%);height:auto}h1{font-size:22px;letter-spacing:-.03em;margin:0}.muted{color:#64748b}input,button{box-sizing:border-box;width:100%;padding:12px;margin:9px 0;border-radius:9px;border:1px solid #cbd5e1;background:white;color:#172b4d}input:focus{outline:3px solid #3158a622;border-color:#6886c5}button{background:var(--brand);border:0;color:white;font-weight:650;cursor:pointer}button:hover{background:#244789}</style></head>
<body><form class=card method=post><div class=brand><img src=/static/relaydesk-logo.svg alt="RelayDesk — MSP Service Desk"></div><h1>Staff sign in</h1><p class=muted>Sign in to your service operations workspace.</p><input name=username autocomplete=username placeholder='Username' required><input name=password type=password autocomplete=current-password placeholder='Password' required><button>Sign in</button><p class=muted>{{ error }}</p></form></body></html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template_string(LOGIN, error="")
    body = request.get_data(as_text=True)
    from urllib.parse import parse_qs
    fields = parse_qs(body)
    user = fields.get("username", [""])[0]
    password = fields.get("password", [""])[0]
    expected_user = os.getenv("HELPDESK_ADMIN_USER", "admin")
    expected_password = os.getenv("HELPDESK_ADMIN_PASSWORD", "")
    if not expected_password:
        return render_template_string(LOGIN, error="Set HELPDESK_ADMIN_PASSWORD before signing in."), 503
    if not secrets.compare_digest(user, expected_user) or not secrets.compare_digest(password, expected_password):
        return render_template_string(LOGIN, error="Invalid username or password."), 401
    session.clear()
    session.permanent = True
    session["user"] = user
    session["csrf"] = secrets.token_urlsafe(32)
    return redirect(url_for("dashboard"))


@app.post("/logout")
@_login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


PAGE = r"""<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'><meta name=theme-color content='#f4f6fa'><link rel=icon type=image/svg+xml href=/static/relaydesk-icon.svg><title>RelayDesk · MSP Service Desk</title>
<style>
:root{color-scheme:light;--ink:#18263d;--muted:#75839a;--line:#e7ebf2;--surface:#fff;--canvas:#f5f7fb;--nav:#17243b;--nav-muted:#a4b0c2;--brand:#4569bd;--brand-dark:#34549f;--brand-soft:#edf2ff;--green:#157347;--amber:#9a6700;--red:#bd2c2c;--shadow:0 7px 24px rgba(26,42,70,.055)}
*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font:14px Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.55}header{height:72px;background:#fff;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 28px;position:sticky;top:0;z-index:5}.header-left,.header-right,.brand-lockup{display:flex;align-items:center;gap:16px}.brand-lockup img{display:block;width:190px;height:auto}.brand-lockup .pill{background:var(--brand-soft);color:var(--brand);padding:5px 10px;letter-spacing:.04em}header form{margin:0}.header-right{gap:14px}.connection{display:flex;align-items:center;gap:8px;color:#52627a;font-size:12px;font-weight:650}.connection:before{content:"";width:8px;height:8px;border-radius:50%;background:#27a878;box-shadow:0 0 0 3px #27a87820}.menu-toggle{width:38px;height:38px;padding:0;display:grid;place-items:center;border:1px solid var(--line);border-radius:10px;color:#4c5c73}.menu-toggle svg{width:19px;height:19px}
button,input,select,textarea{font:inherit;color:inherit}button{border:1px solid #d8e0ea;background:#fff;border-radius:9px;padding:9px 14px;cursor:pointer;transition:all .16s}button:hover{background:#f6f8fc;border-color:#aebbd0}button:active{transform:translateY(1px)}button.primary{background:var(--brand);border-color:var(--brand);color:#fff;font-weight:650;box-shadow:0 3px 8px rgba(49,88,166,.16)}button.primary:hover{background:var(--brand-dark)}
.layout{display:grid;grid-template-columns:258px minmax(0,1fr);min-height:calc(100vh - 72px);transition:grid-template-columns .22s ease}#nav{background:var(--nav);color:#fff;padding:24px 14px 16px;display:flex;flex-direction:column;min-width:0;height:calc(100vh - 72px);position:sticky;top:72px;overflow:auto;transition:padding .22s,border .22s}.nav-section-label{padding:0 13px 12px;color:#8290a5;font-size:10px;letter-spacing:.14em;font-weight:750}.nav-items{display:grid;gap:5px}#nav button{display:flex;align-items:center;gap:13px;width:100%;min-height:45px;text-align:left;border:0;background:transparent;margin:0;color:var(--nav-muted);border-radius:10px;padding:10px 13px;font-weight:560;white-space:nowrap}#nav button svg{width:19px;height:19px;flex:none;stroke:currentColor}#nav button:hover{background:#ffffff10;color:#fff}#nav button.active{background:#4968ad35;color:#fff;box-shadow:inset 3px 0 #91aaff}#nav button.active svg{color:#a9bcff}.nav-footer{margin-top:auto;padding:15px 12px 4px;border-top:1px solid #ffffff18;color:#a4b0c2;font-size:12px}.nav-footer strong{display:block;color:#f4f6fb;font-size:12px;margin-bottom:3px}.layout.sidebar-hidden{grid-template-columns:0 minmax(0,1fr)}.layout.sidebar-hidden #nav{padding-left:0;padding-right:0;border:0;overflow:hidden;visibility:hidden}
main{padding:34px clamp(20px,3.2vw,52px);min-width:0;width:100%;max-width:1660px;margin:0 auto}.top{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:25px}h1{font-size:28px;line-height:1.2;letter-spacing:-.04em;margin:0 0 6px;font-weight:740}h2{font-size:16px;letter-spacing:-.015em;margin:0 0 13px}.muted{color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin-bottom:20px}.card,.panel{background:var(--surface);border:1px solid var(--line);border-radius:15px;padding:21px;box-shadow:var(--shadow)}.cards .card{min-height:108px;position:relative;overflow:hidden}.cards .card:after{content:"";position:absolute;right:-24px;top:-30px;width:86px;height:86px;border-radius:50%;background:var(--brand-soft)}.metric{position:relative;z-index:1;font-size:30px;line-height:1.2;font-weight:740;margin-top:9px;letter-spacing:-.04em}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:17px}.scroll{overflow:auto;border-radius:10px}
table{width:100%;border-collapse:separate;border-spacing:0;white-space:nowrap}th,td{text-align:left;padding:13px 14px;border-bottom:1px solid #edf0f5}th{position:sticky;top:0;background:#f8f9fc;color:#78869a;font-size:10px;text-transform:uppercase;letter-spacing:.09em;font-weight:750}tbody tr:last-child td{border-bottom:0}tr[data-id]{cursor:pointer}tr[data-id]:hover{background:#f8faff}.pill{display:inline-flex;align-items:center;padding:4px 9px;border-radius:999px;background:#eef1f6;color:#596a82;font-size:11px;font-weight:700}.p1{color:var(--red);font-weight:750}.p2{color:var(--amber);font-weight:700}
.field{display:flex;flex-direction:column;gap:7px;margin:13px 0;font-weight:620;color:#39485c}.field input,.field select,.field textarea,input,select,textarea{width:100%;background:#fff;border:1px solid #d5deea;border-radius:10px;padding:11px 13px;outline:none;transition:border .15s,box-shadow .15s}.field input:focus,.field select:focus,.field textarea:focus,input:focus,select:focus,textarea:focus{border-color:#6886c5;box-shadow:0 0 0 3px rgba(49,88,166,.11)}select{appearance:none;-webkit-appearance:none;padding-right:40px!important;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%236b7890' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 13px center;cursor:pointer}select option{padding:10px;background:#fff;color:var(--ink)}.field textarea{min-height:92px;line-height:1.55}.formrow{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.hidden{display:none!important}.tabs{display:flex;gap:8px;flex-wrap:wrap}.detail{white-space:pre-wrap;line-height:1.65;background:#f7f9fc;border:1px solid #edf0f5;border-radius:10px;padding:14px}.result{border-bottom:1px solid var(--line);padding:14px 0}.toast{position:static;margin:0 0 16px;padding:10px 14px;border-radius:9px;background:#e9f4ef;color:#236247;display:none}.actions{display:flex;gap:9px;flex-wrap:wrap}a{color:var(--brand)}details{margin-top:5px}hr{border:0;border-top:1px solid var(--line);margin:20px 0}input[type=checkbox]{width:auto;accent-color:var(--brand);vertical-align:middle}
@media(max-width:1050px){.layout{grid-template-columns:1fr}#nav{height:auto;position:static;display:flex;align-items:center;gap:6px;overflow:auto;padding:9px 14px;border-bottom:1px solid #ffffff18}#nav .nav-section-label,#nav .nav-footer{display:none}#nav .nav-items{display:flex;gap:5px}#nav button{width:auto;min-height:40px;padding:9px 11px;gap:8px}#nav button svg{width:17px;height:17px}.layout.sidebar-hidden{grid-template-columns:0 minmax(0,1fr)}.layout.sidebar-hidden #nav{display:none}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.grid{grid-template-columns:1fr}main{padding:25px 20px}}
@media(max-width:600px){header{height:62px;padding:0 12px}.header-left,.header-right{gap:8px}.brand-lockup img{width:150px}.brand-lockup .pill,.connection{display:none}.layout{min-height:calc(100vh - 62px)}#nav{top:62px}.cards,.formrow{grid-template-columns:1fr}.card,.panel{padding:16px}h1{font-size:23px}.top{align-items:flex-start;flex-direction:column}table{font-size:12px}th,td{padding:10px}main{padding:20px 13px}}
</style></head><body><header><div class=header-left><button id=menu-toggle class=menu-toggle type=button aria-label="Hide navigation" aria-expanded=true title="Hide navigation"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button><div class=brand-lockup><img src=/static/relaydesk-logo.svg alt="RelayDesk — MSP Service Desk"><span class=pill>ZOHO DESK</span></div></div><div class=header-right><span class=connection>Connected workspace</span><form method=post action=/logout><input type=hidden name=csrf_token value="{{ csrf }}"><button>Sign out</button></form></div></header>
<div class=layout id=app-layout><nav id=nav aria-label="Main navigation"><div class=nav-section-label>WORKSPACE</div><div class=nav-items><button class=active data-page=tickets><svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 8h8M8 12h8M8 16h4"/></svg><span>Ticket queue</span></button><button data-page=customers><svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3"/><path d="M3 19v-1a6 6 0 0 1 12 0v1zM16 5a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 4v1h-3"/></svg><span>Customers & agents</span></button><button data-page=handoffs><svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/></svg><span>Voicemail & handoffs</span></button><button data-page=knowledge><svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v17H6.5A2.5 2.5 0 0 0 4 22z"/><path d="M4 5.5V22M8 7h8M8 11h8"/></svg><span>Knowledge base</span></button><button data-page=incidents><svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2.8 19h18.4z"/><path d="M12 9v4M12 16h.01"/></svg><span>Known incidents</span></button><button data-page=settings><svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.7a8 8 0 0 1-1.6.9l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.6-.9l-1.7.7-1.4-2.4 1.4-1.1a7 7 0 0 1 0-1.9l-1.4-1.1 1.4-2.4 1.7.7a8 8 0 0 1 1.6-.9l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.6.9l1.7-.7 1.4 2.4-1.4 1.1a7 7 0 0 1 0 1.8z"/></svg><span>Routing & system</span></button><button data-page=audit><svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16v16H4zM8 8h8M8 12h8M8 16h5"/></svg><span>Audit log</span></button></div><div class=nav-footer><strong>RelayDesk</strong>MSP Service Operations</div></nav><main><div id=toast class=toast role=status aria-live=polite></div><div id=content></div></main></div>
<script>const csrf={{ csrf|tojson }}, cats={{ categories|tojson }}, kbLicenses={{ kb_licenses|tojson }};let page='tickets';const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const appLayout=document.querySelector('#app-layout'),menuToggle=document.querySelector('#menu-toggle');function setNavigationHidden(hidden){appLayout.classList.toggle('sidebar-hidden',hidden);menuToggle.setAttribute('aria-expanded',String(!hidden));menuToggle.setAttribute('aria-label',hidden?'Show navigation':'Hide navigation');menuToggle.title=hidden?'Show navigation':'Hide navigation';localStorage.setItem('relaydesk-navigation-hidden',String(hidden))}setNavigationHidden(localStorage.getItem('relaydesk-navigation-hidden')==='true');menuToggle.onclick=()=>setNavigationHidden(!appLayout.classList.contains('sidebar-hidden'));
async function api(path,opts={}){opts.headers={...(opts.headers||{}),'Content-Type':'application/json','X-CSRF-Token':csrf};let r=await fetch('/api/'+path,opts),d=await r.json().catch(()=>({}));if(!r.ok){toast(d.error||r.statusText);throw Error(d.error||r.statusText)}return d}function toast(t){let e=document.querySelector('#toast');e.textContent=t;e.style.display=t?'block':'none'}
function setpage(p){page=p;document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===p));render()};document.querySelector('#nav').onclick=e=>{let b=e.target.closest('button[data-page]');if(b)setpage(b.dataset.page)};
async function render(){let root=document.querySelector('#content');try{if(page==='tickets')await ticketPage(root);if(page==='customers')await customerPage(root);if(page==='handoffs')await handoffPage(root);if(page==='knowledge')await kbPage(root);if(page==='incidents')await incidentPage(root);if(page==='settings')await settingsPage(root);if(page==='audit')await auditPage(root)}catch(e){root.innerHTML=`<div class=panel><h2>Could not load ${esc(page)}</h2><p class=muted>${esc(e.message)}</p><button onclick=render()>Retry</button></div>`}}
async function ticketPage(root){let d=await api('tickets'),rows=d.tickets;root.innerHTML=`<div class=top><div><h1>Ticket queue</h1><span class=muted>Tickets are in Zoho Desk; new tickets go to the active agent with the fewest open tickets.</span></div><div class=actions><button class=primary onclick="newTicket()">Create ticket</button></div></div><div class=cards><div class=card><div class=muted>Open in view</div><div class=metric>${rows.filter(x=>!['closed','resolved'].includes(x.status)).length}</div></div><div class=card><div class=muted>P1</div><div class=metric>${rows.filter(x=>x.priority===1).length}</div></div><div class=card><div class=muted>Unassigned</div><div class=metric>${rows.filter(x=>!x.assignee).length}</div></div><div class=card><div class=muted>Handoffs waiting</div><div class=metric>${d.handoff_count}</div></div></div><div class=panel scroll><input id=tsearch placeholder="Filter these latest Zoho tickets" oninput="filterTickets(this.value)" style="width:100%;margin-bottom:10px"><table><thead><tr><th>Ticket</th><th>Priority</th><th>Status</th><th>Customer</th><th>Subject</th><th>Assignee</th></tr></thead><tbody>${rows.map(t=>`<tr data-id="${esc(t.id)}" data-search="${esc([t.ref,t.company_name,t.summary,t.assignee].join(' ')).toLowerCase()}" onclick="showTicket('${esc(t.id)}')"><td>#${esc(t.ref)}</td><td class="p${t.priority}">P${t.priority}</td><td>${esc(t.status.replaceAll('_',' '))}</td><td>${esc(t.company_name)}</td><td>${esc(t.summary)}</td><td>${esc(t.assignee||'Unassigned')}</td></tr>`).join('')}</tbody></table></div>`}
function filterTickets(q){document.querySelectorAll('tr[data-search]').forEach(r=>r.style.display=r.dataset.search.includes(q.toLowerCase())?'':'none')}
async function customerPage(root){let d=await api('customers');root.innerHTML=`<div class=top><div><h1>Customers & agents</h1><span class=muted>Accounts, contacts, and agents are synced from Zoho Desk. VIP routing flags are local policy.</span></div><div class=actions><button onclick="newAccount()">Add account</button><button class=primary onclick="newContact()">Add contact</button></div></div><div class=grid><div class=panel><h2>Zoho Desk accounts</h2><div class=scroll><table><thead><tr><th>Account</th><th>Phone</th><th>Email</th></tr></thead><tbody>${d.accounts.map(a=>`<tr><td>${esc(a.accountName||a.name)}</td><td>${esc(a.phone)}</td><td>${esc(a.email)}</td></tr>`).join('')}</tbody></table></div></div><div class=panel><h2>Zoho Desk contacts</h2><div class=scroll><table><thead><tr><th>Contact</th><th>Account</th><th>Phone</th><th>VIP</th></tr></thead><tbody>${d.contacts.map(c=>`<tr><td>${esc((c.firstName||'')+' '+(c.lastName||''))}</td><td>${esc(c.account?.accountName)}</td><td>${esc(c.phone)}</td><td><input type=checkbox ${c.vip?'checked':''} onchange="vipFlag('${esc(c.id)}',this.checked)"></td></tr>`).join('')}</tbody></table></div></div></div><div class=panel style="margin-top:14px"><h2>Zoho Desk agents</h2>${d.agents.map(a=>`<span class=pill>${esc(a.name)} · ${esc(a.email)}</span> `).join('')}</div>`}
async function newAccount(){let root=document.querySelector('#content');root.insertAdjacentHTML('afterbegin',`<form id=accountform class=panel><h2>Add Zoho Desk account</h2><label class=field>Account name<input name=accountName required></label><label class=field>Main phone<input name=phone type=tel></label><button class=primary>Add account</button> <button type=button onclick="render()">Cancel</button></form>`);root.querySelector('#accountform').onsubmit=async e=>{e.preventDefault();try{await api('customers/accounts',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});toast('Account added to Zoho Desk');render()}catch(error){toast(error.message)}}}
async function newContact(){let d=await api('customers'),root=document.querySelector('#content');root.insertAdjacentHTML('afterbegin',`<form id=contactform class=panel><h2>Add Zoho Desk contact</h2><div class=formrow><label class=field>First name<input name=firstName></label><label class=field>Last name<input name=lastName required></label><label class=field>Email<input name=email type=email></label><label class=field>Phone<input name=phone type=tel></label></div><label class=field>Account<select name=accountName required><option value="">Select account</option>${d.accounts.map(a=>`<option>${esc(a.accountName||a.name)}</option>`).join('')}</select></label><button class=primary>Add contact</button> <button type=button onclick="render()">Cancel</button></form>`);root.querySelector('#contactform').onsubmit=async e=>{e.preventDefault();try{await api('customers/contacts',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});toast('Contact added to Zoho Desk');render()}catch(error){toast(error.message)}}}
async function vipFlag(id,is_vip){await api('customers/'+encodeURIComponent(id)+'/flags',{method:'PATCH',body:JSON.stringify({is_vip})});toast('VIP routing updated')}
async function showTicket(id){let [t,staff]=await Promise.all([api('tickets/'+encodeURIComponent(id)),api('agents')]);let root=document.querySelector('#content');root.innerHTML=`<div class=top><div><button onclick="setpage('tickets')">← Queue</button><h1 style="display:inline;margin-left:12px">Ticket #${esc(t.ref)}</h1></div></div><div class=grid><div class=panel><h2>${esc(t.summary)}</h2><p>${esc(t.company_name)} · ${esc(t.caller_name)}</p><p><span class="pill p${t.priority}">P${t.priority}</span> ${esc(t.status)}</p><div class=detail>${esc(t.details)}</div><hr><div class=formrow><label class=field>Status<select id=tstatus><option>Open</option><option>On Hold</option><option>Escalated</option><option>Closed</option></select></label><label class=field>Assignee<select id=assignee><option value="">Unassigned</option>${staff.agents.map(a=>`<option value="${esc(a.email)}" ${a.email===t.assignee_email?'selected':''}>${esc(a.name)} (${esc(a.email)})</option>`).join('')}</select></label></div><button class=primary onclick="saveTicket('${esc(t.id)}')">Save in Zoho</button><div class=field><label>Private note</label><textarea id=note></textarea></div><button onclick="addNote('${esc(t.id)}')">Add private note</button></div><div class=panel><h2>Conversation</h2>${(t.comments||[]).map(c=>`<div class=result><b>${esc(c.author?.name||'Agent')}</b><div class=muted>${esc(c.createdTime||'')}</div><p>${esc(c.content)}</p></div>`).join('')||'<p class=muted>No comments available.</p>'}</div></div>`;document.querySelector('#tstatus').value=t.status_display||'Open'}
async function saveTicket(id){await api('tickets/'+encodeURIComponent(id),{method:'PATCH',body:JSON.stringify({status:document.querySelector('#tstatus').value,assignee:document.querySelector('#assignee').value})});toast('Updated in Zoho Desk');showTicket(id)}async function addNote(id){let content=document.querySelector('#note').value;await api('tickets/'+encodeURIComponent(id)+'/comments',{method:'POST',body:JSON.stringify({content})});toast('Note added in Zoho Desk');showTicket(id)}
async function newTicket(){let root=document.querySelector('#content');root.innerHTML=`<div class=top><div><button onclick="setpage('tickets')">← Queue</button><h1 style="display:inline;margin-left:12px">Create ticket in Zoho Desk</h1></div></div><form id=newticket class=panel><div class=formrow><label class=field>Caller name<input name=name required></label><label class=field>Company<input name=company required></label><label class=field>Callback number<input name=phone></label><label class=field>Category<select name=category>${cats.map(c=>`<option>${esc(c)}</option>`).join('')}</select></label><label class=field>Scope<select name=scope><option>single_user</option><option>several_users</option><option>whole_company</option></select></label><label class=field>Severity<select name=severity><option>minor</option><option>degraded</option><option>stopped</option></select></label></div><label class=field>Summary<input name=summary required></label><label class=field>Details<textarea name=details></textarea></label><label><input style="width:auto" type=checkbox name=security> Security incident / suspected data loss</label><p><button class=primary>Create in Zoho</button></p></form>`;root.querySelector('form').onsubmit=async e=>{e.preventDefault();let o=Object.fromEntries(new FormData(e.target));o.security=!!e.target.security.checked;await api('tickets',{method:'POST',body:JSON.stringify(o)});toast('Created in Zoho Desk');setpage('tickets')}}
async function handoffPage(root){let d=await api('handoffs');root.innerHTML=`<div class=top><div><h1>Voicemail & handoffs</h1><span class=muted>Automatically recorded call requests and transfer results. Configure LiveKit destinations under Routing & system; failed or unavailable transfers appear here.</span></div></div><div class=panel scroll><table><thead><tr><th>Received</th><th>Type</th><th>State</th><th>Ticket</th><th>Callback</th><th>Summary / message</th></tr></thead><tbody>${d.items.map(h=>`<tr><td>${esc(h.created_at)}</td><td>${esc(h.kind)}</td><td>${esc(h.status)}</td><td>${h.ticket_id?`#${esc(h.ticket_ref||h.ticket_id)}`:'—'}</td><td>${esc(h.callback_number)}</td><td>${esc(h.summary)}<details><summary>Message</summary><div class=detail>${esc(h.transcript)}</div></details></td></tr>`).join('')}</tbody></table>${d.items.length?'':`<p class=muted>No call handoffs have been recorded yet. Requests and transfer failures will appear here automatically.</p>`}</div>`}
async function kbPage(root){let d=await api('knowledge'),a=d.articles;root.innerHTML=`<div class=top><div><h1>Knowledge base</h1><span class=muted>Local BM25 RAG retrieves reviewed articles and passes grounded steps and sources to the voice agent.</span></div><div class=actions><button onclick="importKb()">Import sourced content</button><button class=primary onclick="kbEditor()">New article</button></div></div><div class=panel><div class=actions><input id=kbquery placeholder='Search troubleshooting library' style='flex:1'><button onclick="kbSearch()">Search</button><button onclick="kbSearch(true)">Preview matches</button></div><div id=kbresults></div></div><div class=panel style="margin-top:14px"><div class=scroll><table><thead><tr><th>Article</th><th>Category</th><th>Client</th><th>Review</th><th>License</th><th>Sources</th><th></th></tr></thead><tbody>${a.map(x=>`<tr><td>${esc(x.title)}</td><td>${esc(x.category)}</td><td>${esc(x.company_name||'All clients')}</td><td>${x.enabled?'Approved / enabled':esc(x.review_status||'draft')}</td><td>${esc(x.license_id||'Not reviewed')}</td><td>${x.sources.length}</td><td><button onclick="kbEditor('${esc(x.id)}')">Edit / review</button></td></tr>`).join('')}</tbody></table></div></div>`}
function importKb(){let root=document.querySelector('#content');root.insertAdjacentHTML('afterbegin',`<form id=importkb class=panel><h2>Import sourced content</h2><p class=muted>Paste only content whose license permits your use. Imports stay disabled drafts until an admin reviews accuracy, license, attribution, and client fit.</p><label class=field>Title<input name=title required></label><label class=field>Category<select name=category>${cats.map(c=>`<option>${esc(c)}</option>`).join('')}</select></label><label class=field>Client scope (blank means shared)<input name=company_name></label><label class=field>License / reuse basis<select name=license_id><option value="">Choose after review</option>${kbLicenses.map(l=>`<option value="${esc(l.id)}">${esc(l.label)}</option>`).join('')}</select></label><label class=field>Attribution / creator<textarea name=attribution placeholder="Creator or publisher; article title; modifications made"></textarea></label><label class=field>Upstream repository or content URL<input name=upstream_url type=url></label><label class=field>Source URLs (one per line)<textarea name=sources></textarea></label><label class=field>Paste text<textarea name=content required style="min-height:220px"></textarea></label><button class=primary>Import as review drafts</button> <button type=button onclick="this.form.remove()">Cancel</button></form>`);root.querySelector('#importkb').onsubmit=async e=>{e.preventDefault();let o=Object.fromEntries(new FormData(e.target));o.sources=o.sources.split('\n').filter(Boolean);let d=await api('knowledge/import',{method:'POST',body:JSON.stringify(o)});toast(`Imported ${d.chunks} draft chunks`);render()}}
async function kbSearch(preview=false){let q=encodeURIComponent(document.querySelector('#kbquery').value),d=await api('knowledge/search?q='+q),el=document.querySelector('#kbresults');el.innerHTML=d.results.map(x=>`<div class=result><b>${esc(x.title)}</b> <span class=pill>${esc(x.category)}</span><p>${esc(x.content)}</p>${(x.steps||[]).map((s,i)=>`<p>${i+1}. ${esc(s)}</p>`).join('')}<small class=muted>Relevance ${x.score}; ${esc(x.license_id||'license not recorded')}; attribution: ${esc(x.attribution||'not recorded')}; sources: ${esc(x.sources.join(', '))}</small></div>`).join('')||'<p class=muted>No relevant article found.</p>'}
async function kbEditor(id){let root=document.querySelector('#content'),a=id?await api('knowledge/'+encodeURIComponent(id)):null;root.insertAdjacentHTML('afterbegin',`<form id=kbform class=panel><h2>${a?'Edit article':'New KB article'}</h2><p class=muted>Review safe steps, client scope, source, license, and attribution. Changes to upstream content must be noted before enabling.</p><input type=hidden name=id value="${esc(a?.id||'')}"><div class=formrow><label class=field>Title<input name=title required value="${esc(a?.title||'')}"></label><label class=field>Category<select name=category>${cats.map(c=>`<option ${a?.category===c?'selected':''}>${esc(c)}</option>`).join('')}</select></label></div><label class=field>Client scope (blank means shared) <input name=company_name value="${esc(a?.company_name||'')}"></label><label class=field>Search content / symptom description<textarea name=content required>${esc(a?.content||'')}</textarea></label><label class=field>Step-by-step safe instructions (one per line)<textarea name=steps>${esc((a?.steps||[]).join('\n'))}</textarea></label><label class=field>Source URLs (one per line)<textarea name=sources>${esc((a?.sources||[]).join('\n'))}</textarea></label><div class=formrow><label class=field>License / reuse basis<select name=license_id><option value="">Choose license</option>${kbLicenses.map(l=>`<option value="${esc(l.id)}" ${a?.license_id===l.id?'selected':''}>${esc(l.label)}</option>`).join('')}</select></label><label class=field>Upstream repository or source URL<input name=upstream_url type=url value="${esc(a?.upstream_url||'')}"></label></div><label class=field>Attribution / creator<textarea name=attribution placeholder="Creator or publisher; article title; modifications made">${esc(a?.attribution||'')}</textarea></label><label><input style="width:auto" type=checkbox name=enabled ${a?.enabled?'checked':''}> Approve and enable for agent</label><p><button class=primary>Save article</button> <button type=button onclick="this.form.remove()">Cancel</button></p></form>`);root.querySelector('#kbform').onsubmit=async e=>{e.preventDefault();let f=e.target,o=Object.fromEntries(new FormData(f));o.steps=o.steps.split('\n').filter(Boolean);o.sources=o.sources.split('\n').filter(Boolean);o.enabled=f.enabled.checked;await api('knowledge',{method:'POST',body:JSON.stringify(o)});toast('Knowledge article saved');render()}}
async function incidentPage(root){let d=await api('incidents');root.innerHTML=`<div class=top><div><h1>Known incidents</h1><span class=muted>Local operational notices surfaced to the voice agent.</span></div><button class=primary onclick="incidentForm()">Add incident</button></div><div class=panel><table><thead><tr><th>Incident</th><th>Category</th><th>Status</th><th>Created</th><th></th></tr></thead><tbody>${d.items.map(i=>`<tr><td>${esc(i.title)}</td><td>${esc(i.category)}</td><td>${esc(i.status)}</td><td>${esc(i.created_at)}</td><td>${i.status==='active'?`<button onclick="resolveIncident(${i.id})">Resolve</button>`:''}</td></tr>`).join('')}</tbody></table></div>`}
function incidentForm(){let root=document.querySelector('#content');root.insertAdjacentHTML('afterbegin',`<form id=incidentform class=panel><h2>Add known incident</h2><label class=field>Incident title<input name=title required></label><label class=field>Category<select name=category>${cats.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('')}</select></label><button class=primary>Add incident</button> <button type=button onclick="render()">Cancel</button></form>`);root.querySelector('#incidentform').onsubmit=async e=>{e.preventDefault();try{await api('incidents',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});toast('Incident added');render()}catch(error){toast(error.message)}}}async function resolveIncident(id){try{await api('incidents/'+id,{method:'PATCH',body:JSON.stringify({status:'resolved'})});toast('Incident resolved');render()}catch(error){toast(error.message)}}
async function settingsPage(root){let d=await api('settings');let slas=[1,2,3,4].map(p=>`<div class=formrow><label class=field>P${p} first response (minutes)<input type=number min=1 name=p${p}_ack value="${esc(d.settings['p'+p+'_ack'])}"></label><label class=field>P${p} target resolution (hours)<input type=number min=1 name=p${p}_resolve value="${esc(d.settings['p'+p+'_resolve'])}"></label></div>`).join('');root.innerHTML=`<div class=top><div><h1>Routing & system</h1><span class=muted>Escalation, contract targets, and connectivity</span></div></div><div class=grid><form id=settings class=panel><h2>Call escalation policy</h2><label class=field>Time zone<input name=timezone value="${esc(d.settings.timezone||'Australia/Sydney')}"></label><label class=field>Coverage<select name=coverage_mode><option value=weekdays>Weekdays during configured hours</option><option value=24x7>24/7 including weekends</option></select></label><label class=field>Weekday hours (24h, local time)<input name=hours value="${esc(d.settings.hours||'00:00-24:00')}"></label><label class=field>After-hours urgent ticket route<select name=after_hours_mode><option value=on_call>Staff follow-up queue</option><option value=voicemail>Voicemail transfer destination</option></select></label><h2>LiveKit call destinations</h2><p class=muted>Use E.164 numbers (for example +61255550123) or SIP URIs. Your LiveKit SIP trunk/provider must allow call transfers (SIP REFER). Transfers are attempted only when a caller requests them or policy routes urgent calls.</p><label class=field>Human agent destination<input name=human_destination placeholder="+61255550123 or sip:user@example.com" value="${esc(d.settings.human_destination||'')}"></label><label class=field>Voicemail destination<input name=voicemail_destination placeholder="+61255550124 or sip:mailbox@example.com" value="${esc(d.settings.voicemail_destination||'')}"></label><h2>SLA targets</h2><p class=muted>Enter targets from your MSP contracts. These are decision support only; configure SLA policies in Zoho Desk for enforcement and notifications. The app cannot confirm staff response or resolution.</p>${slas}<p><button class=primary>Save routing settings</button></p></form><div class=panel><h2>Zoho Desk connection</h2><p>${d.zoho.configured?'Configured':'Not configured'}</p><p class=muted>${esc(d.zoho.message||'')}</p><p>Tickets remain in Zoho Desk.</p><h2>LiveKit phone line</h2><p>Inbound calls use your LiveKit dispatch rule. A caller request creates a Zoho ticket, then transfers the active SIP call to the configured destination. If voicemail transfer fails, the agent can collect a spoken message and save its transcript in Zoho Desk and the staff handoff queue.</p><p class=muted>LiveKit transfer requires working API credentials and SIP REFER support on the provider trunk. Confirm this with your LiveKit/provider configuration before relying on transfers.</p></div></div>`;root.querySelector('[name=coverage_mode]').value=d.settings.coverage_mode||'24x7';root.querySelector('[name=after_hours_mode]').value=d.settings.after_hours_mode||'on_call';root.querySelector('#settings').onsubmit=async e=>{e.preventDefault();let o=Object.fromEntries(new FormData(e.target));await api('settings',{method:'POST',body:JSON.stringify(o)});toast('Routing settings saved')}}
async function auditPage(root){let d=await api('audit');root.innerHTML=`<div class=top><div><h1>Audit log</h1><span class=muted>Recent admin changes made in this console.</span></div></div><div class=panel scroll><table><thead><tr><th>Time UTC</th><th>Actor</th><th>Action</th><th>Object</th><th>Details</th></tr></thead><tbody>${d.items.map(x=>`<tr><td>${esc(x.created_at)}</td><td>${esc(x.actor)}</td><td>${esc(x.action)}</td><td>${esc(x.entity_type)} ${esc(x.entity_id)}</td><td>${esc(x.details)}</td></tr>`).join('')}</tbody></table></div>`}
render();
</script></body></html>"""


@app.get("/")
@_login_required
def dashboard():
    return render_template_string(PAGE, csrf=session.get("csrf", ""), categories=escalation.CATEGORIES,
                                  kb_licenses=KB_LICENSE_CHOICES)


@app.get("/api/health")
@_login_required
def health():
    return jsonify(zoho_configured=zoho.enabled(), ticket_source="Zoho Desk")


@app.get("/api/tickets")
@_login_required
def api_tickets():
    rows = tickets.list_tickets(_conn())
    handoffs = _conn().execute("SELECT count(*) FROM handoffs WHERE status='queued'").fetchone()[0]
    return jsonify(tickets=rows, handoff_count=handoffs, zoho_url=os.getenv("ZOHO_DESK_WEB_URL", ""))


@app.post("/api/tickets")
@_login_required
def api_create_ticket():
    data = request.get_json(force=True)
    name, company, summary = (str(data.get(k, "")).strip() for k in ("name", "company", "summary"))
    if not name or not company or not summary:
        return jsonify(error="Name, company, and summary are required"), 400
    category, scope, severity = data.get("category", "other"), data.get("scope", "single_user"), data.get("severity", "degraded")
    if category not in escalation.CATEGORIES or scope not in escalation.SCOPES or severity not in escalation.SEVERITIES:
        return jsonify(error="Invalid category, scope, or severity"), 400
    result = tickets.create(_conn(), caller_name=name, company_name=company,
                            callback_number=str(data.get("phone", "")), category=category, summary=summary,
                            details=str(data.get("details", "")), scope=scope, severity=severity,
                            security_concern=bool(data.get("security")))
    _audit("ticket_created", "zoho_ticket", result["ticket"]["id"], summary)
    return jsonify(ticket=result["ticket"]), 201


@app.get("/api/tickets/<ticket_id>")
@_login_required
def api_ticket(ticket_id):
    raw = zoho.get_ticket(ticket_id)
    comments = zoho.list_comments(ticket_id)
    obj = tickets._from_zoho(raw)
    obj.update({"webUrl": raw.get("webUrl", ""), "assignee_email": (raw.get("assignee") or {}).get("email", ""),
                "status_display": raw.get("status") or raw.get("statusType", "Open"), "comments": comments})
    return jsonify(obj)


@app.patch("/api/tickets/<ticket_id>")
@_login_required
def api_update_ticket(ticket_id):
    data = request.get_json(force=True)
    body = {}
    if "status" in data:
        allowed = {"Open", "On Hold", "Closed", "Escalated"}
        if data["status"] not in allowed:
            return jsonify(error="Invalid Zoho ticket status"), 400
        body["status"] = data["status"]
    if data.get("assignee"):
        agent = next((a for a in zoho.list_agents() if a.get("email", "").lower() == str(data["assignee"]).strip().lower()), None)
        if not agent:
            return jsonify(error="No Zoho agent with that email was found"), 400
        body["assigneeId"] = agent["id"]
    zoho.update_ticket(ticket_id, body)
    _audit("ticket_updated", "zoho_ticket", ticket_id, json.dumps(body))
    return jsonify(ok=True)


@app.post("/api/tickets/<ticket_id>/comments")
@_login_required
def api_comment(ticket_id):
    data = request.get_json(force=True)
    content = str(data.get("content", "")).strip()
    if not content:
        return jsonify(error="Comment cannot be empty"), 400
    result = zoho.add_comment(ticket_id, content, public=False)
    _audit("private_comment_added", "zoho_ticket", ticket_id, content[:300])
    return jsonify(result)


@app.get("/api/agents")
@_login_required
def api_agents():
    agents = zoho.list_agents()
    return jsonify(agents=[{"id": a.get("id"), "name": a.get("name", ""), "email": a.get("email", "")}
                           for a in agents if a.get("isActive", True)])


@app.get("/api/customers")
@_login_required
def api_customers():
    contacts = zoho.list_contacts()
    flags = {r["zoho_contact_id"]: bool(r["is_vip"]) for r in
             _conn().execute("SELECT zoho_contact_id,is_vip FROM contact_flags")}
    for contact in contacts:
        contact["vip"] = flags.get(str(contact.get("id")), bool(contact.get("isVIP", False)))
    return jsonify(accounts=zoho.list_accounts(), contacts=contacts, agents=zoho.list_agents())


@app.post("/api/customers/accounts")
@_login_required
def api_customer_account_create():
    data = request.get_json(force=True)
    name = str(data.get("accountName", "")).strip()
    if not name:
        return jsonify(error="Account name is required"), 400
    result = zoho.create_account({"accountName": name, **({"phone": data["phone"]} if data.get("phone") else {})})
    _audit("account_created", "zoho_account", result.get("id", ""), name)
    return jsonify(result), 201


@app.post("/api/customers/contacts")
@_login_required
def api_customer_contact_create():
    data = request.get_json(force=True)
    last = str(data.get("lastName", "")).strip()
    account_name = str(data.get("accountName", "")).strip().lower()
    account = next((a for a in zoho.list_accounts() if str(a.get("accountName", "")).strip().lower() == account_name), None)
    if not last or not account:
        return jsonify(error="Last name and an existing Zoho account are required"), 400
    payload = {"lastName": last, "accountId": account["id"]}
    for field in ("firstName", "email", "phone"):
        if data.get(field):
            payload[field] = str(data[field]).strip()
    result = zoho.create_contact(payload)
    _audit("contact_created", "zoho_contact", result.get("id", ""), last)
    return jsonify(result), 201


@app.patch("/api/customers/<contact_id>/flags")
@_login_required
def api_customer_flags(contact_id):
    data = request.get_json(force=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _conn().execute("INSERT INTO contact_flags(zoho_contact_id,is_vip,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(zoho_contact_id) DO UPDATE SET is_vip=excluded.is_vip,updated_at=excluded.updated_at",
                    (contact_id, int(bool(data.get("is_vip"))), now))
    _conn().commit()
    _audit("vip_flag_updated", "zoho_contact", contact_id, str(bool(data.get("is_vip"))))
    return jsonify(ok=True)


@app.get("/api/knowledge")
@_login_required
def api_knowledge():
    rows = _conn().execute("SELECT * FROM kb_articles ORDER BY category,title").fetchall()
    return jsonify(articles=[_article(r) for r in rows])


def _article(row):
    return {"id": row["id"], "title": row["title"], "category": row["category"],
            "company_name": row["company_name"],
            "content": row["content"], "steps": json.loads(row["steps"]),
            "sources": json.loads(row["sources"]), "enabled": bool(row["enabled"]),
            "review_status": row["review_status"], "reviewed_by": row["reviewed_by"],
            "license_id": row["license_id"], "attribution": row["attribution"],
            "upstream_url": row["upstream_url"]}


@app.get("/api/knowledge/<article_id>")
@_login_required
def api_knowledge_get(article_id):
    row = _conn().execute("SELECT * FROM kb_articles WHERE id=?", (article_id,)).fetchone()
    if not row:
        return jsonify(error="Article not found"), 404
    return jsonify(_article(row))


@app.post("/api/knowledge")
@_login_required
def api_knowledge_save():
    data = request.get_json(force=True)
    title, category = str(data.get("title", "")).strip(), data.get("category", "other")
    company_name = str(data.get("company_name", "")).strip()
    content = str(data.get("content", "")).strip()
    steps = [str(step).strip() for step in data.get("steps", []) if str(step).strip()]
    sources = [str(source).strip() for source in data.get("sources", []) if str(source).strip()]
    enabled = bool(data.get("enabled", False))
    license_id = str(data.get("license_id", "")).strip()
    attribution = str(data.get("attribution", "")).strip()
    upstream_url = str(data.get("upstream_url", "")).strip()
    allowed_licenses = {item["id"] for item in KB_LICENSE_CHOICES}
    if not title or not content or category not in escalation.CATEGORIES:
        return jsonify(error="Title, content, and a valid category are required"), 400
    if any(urlparse(source).scheme not in ("http", "https") or not urlparse(source).netloc for source in sources):
        return jsonify(error="Sources must be complete http or https URLs"), 400
    if enabled and (not sources or not steps):
        return jsonify(error="To approve an article, add at least one source URL and one safe step"), 400
    if (upstream_url and (urlparse(upstream_url).scheme not in ("http", "https") or not urlparse(upstream_url).netloc)):
        return jsonify(error="Upstream URL must be a complete http or https URL"), 400
    if enabled and (license_id not in allowed_licenses or not attribution):
        return jsonify(error="To approve an article, select a reuse basis and record attribution, creator, and any changes"), 400
    if license_id and license_id not in allowed_licenses:
        return jsonify(error="Choose a supported license or permission basis"), 400
    if company_name and not any(str(a.get("accountName", "")).lower() == company_name.lower()
                                for a in zoho.list_accounts()):
        return jsonify(error="Client scope must exactly match a Zoho Desk account"), 400
    article_id = str(data.get("id") or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"))[:100]
    if not article_id:
        article_id = secrets.token_hex(8)
    old = _conn().execute("SELECT created_at FROM kb_articles WHERE id=?", (article_id,)).fetchone()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _conn().execute("INSERT INTO kb_articles(id,title,category,company_name,content,steps,sources,enabled,review_status,reviewed_by,reviewed_at,license_id,attribution,upstream_url,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,category=excluded.category,"
                    "company_name=excluded.company_name,"
                    "content=excluded.content,steps=excluded.steps,sources=excluded.sources,enabled=excluded.enabled,"
                    "review_status=excluded.review_status,reviewed_by=excluded.reviewed_by,reviewed_at=excluded.reviewed_at,"
                    "license_id=excluded.license_id,attribution=excluded.attribution,upstream_url=excluded.upstream_url,updated_at=excluded.updated_at",
                    (article_id, title, category, company_name, content, json.dumps(steps),
                     json.dumps(sources), int(enabled), "approved" if enabled else "draft",
                     session.get("user", "admin") if enabled else "", now if enabled else None,
                     license_id, attribution, upstream_url, old["created_at"] if old else now, now))
    _conn().commit()
    _audit("knowledge_saved", "kb_article", article_id, title)
    return jsonify(id=article_id), 201


@app.get("/api/knowledge/search")
@_login_required
def api_knowledge_search():
    query = request.args.get("q", "")
    return jsonify(results=kb.search_index(query, _conn(), limit=8, company_name="*"))


@app.post("/api/knowledge/import")
@_login_required
def api_knowledge_import():
    """Import pasted runbook text into searchable chunks. Uploads are not executed or fetched remotely."""
    data = request.get_json(force=True)
    title, content = str(data.get("title", "")).strip(), str(data.get("content", "")).strip()
    category = data.get("category", "other")
    company_name = str(data.get("company_name", "")).strip()
    license_id = str(data.get("license_id", "")).strip()
    attribution = str(data.get("attribution", "")).strip()
    upstream_url = str(data.get("upstream_url", "")).strip()
    allowed_licenses = {item["id"] for item in KB_LICENSE_CHOICES}
    if not title or not content or category not in escalation.CATEGORIES:
        return jsonify(error="Title, content, and valid category are required"), 400
    if license_id and license_id not in allowed_licenses:
        return jsonify(error="Choose a supported license or permission basis"), 400
    sources = [str(source).strip() for source in data.get("sources", []) if str(source).strip()]
    if any(urlparse(source).scheme not in ("http", "https") or not urlparse(source).netloc for source in sources):
        return jsonify(error="Sources must be complete http or https URLs"), 400
    if upstream_url and (urlparse(upstream_url).scheme not in ("http", "https") or not urlparse(upstream_url).netloc):
        return jsonify(error="Upstream URL must be a complete http or https URL"), 400
    if company_name and not any(str(a.get("accountName", "")).lower() == company_name.lower()
                                for a in zoho.list_accounts()):
        return jsonify(error="Client scope must exactly match a Zoho Desk account"), 400
    chunks, current = [], ""
    for paragraph in re.split(r"\n\s*\n", content):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(current) + len(paragraph) > 1800 and current:
            chunks.append(current)
            current = current[-200:] + "\n" + paragraph
        else:
            current = (current + "\n" + paragraph).strip()
    if current:
        chunks.append(current)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = []
    for i, chunk in enumerate(chunks, 1):
        aid = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:70] + f"-{i}"
        _conn().execute("INSERT OR REPLACE INTO kb_articles(id,title,category,company_name,content,steps,sources,enabled,review_status,reviewed_by,reviewed_at,license_id,attribution,upstream_url,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (aid, f"{title} (part {i})", category, company_name, chunk,
                        "[]", json.dumps(sources), 0, "draft", "", None, license_id, attribution, upstream_url, now, now))
        ids.append(aid)
    _conn().commit()
    _audit("knowledge_imported", "kb_article", ",".join(ids), f"{len(chunks)} text chunks")
    return jsonify(ids=ids, chunks=len(chunks)), 201


@app.get("/api/handoffs")
@_login_required
def api_handoffs():
    rows = _conn().execute("SELECT * FROM handoffs ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END,id DESC LIMIT 300").fetchall()
    items = [dict(r) for r in rows]
    for item in items:
        item["ticket_ref"] = item.get("ticket_id")
    return jsonify(items=items)


@app.get("/api/incidents")
@_login_required
def api_incidents():
    rows = _conn().execute("SELECT * FROM incidents ORDER BY status,created_at DESC").fetchall()
    return jsonify(items=[dict(r) for r in rows])


@app.post("/api/incidents")
@_login_required
def api_incident_create():
    data = request.get_json(force=True)
    title, category = str(data.get("title", "")).strip(), data.get("category")
    if not title or category not in escalation.CATEGORIES:
        return jsonify(error="Incident title and valid category are required"), 400
    iid = tickets.create_incident(_conn(), title, category)
    _audit("incident_created", "incident", iid, title)
    return jsonify(id=iid), 201


@app.patch("/api/incidents/<int:incident_id>")
@_login_required
def api_incident_resolve(incident_id):
    tickets.resolve_incident(_conn(), incident_id)
    _audit("incident_resolved", "incident", incident_id)
    return jsonify(ok=True)


DEFAULT_SETTINGS = {"timezone": "Australia/Sydney", "coverage_mode": "24x7", "hours": "00:00-24:00", "after_hours_mode": "on_call",
                    "human_destination": "", "voicemail_destination": "",
                    "p1_ack": "10", "p1_resolve": "4", "p2_ack": "15", "p2_resolve": "8",
                    "p3_ack": "60", "p3_resolve": "48", "p4_ack": "240", "p4_resolve": "120"}


def _settings():
    vals = dict(DEFAULT_SETTINGS)
    vals.update({r["key"]: r["value"] for r in _conn().execute("SELECT key,value FROM app_settings")})
    return vals


@app.get("/api/settings")
@_login_required
def api_settings():
    settings = _settings()
    livekit_credentials = bool(os.getenv("LIVEKIT_URL") and os.getenv("LIVEKIT_API_KEY") and os.getenv("LIVEKIT_API_SECRET"))
    return jsonify(settings=settings, livekit={"credentials_configured": livekit_credentials,
                                                "human_destination_configured": bool(settings.get("human_destination")),
                                                "voicemail_destination_configured": bool(settings.get("voicemail_destination"))},
                   zoho={"configured": zoho.enabled(),
                                               "message": "Zoho Desk connection ready" if zoho.enabled() else
                                               "Configure the ZOHO_* credentials in the service environment."})


@app.post("/api/settings")
@_login_required
def api_settings_save():
    data = request.get_json(force=True)
    values = {"timezone": str(data.get("timezone", "Australia/Sydney")),
              "coverage_mode": str(data.get("coverage_mode", "24x7")),
              "hours": str(data.get("hours", "00:00-24:00")),
              "after_hours_mode": data.get("after_hours_mode", "on_call"),
              "human_destination": str(data.get("human_destination", "")).strip(),
              "voicemail_destination": str(data.get("voicemail_destination", "")).strip()}
    try:
        for priority in range(1, 5):
            ack = int(data[f"p{priority}_ack"])
            resolve = int(data[f"p{priority}_resolve"])
            if not (1 <= ack <= 10080 and 1 <= resolve <= 8760):
                raise ValueError
            values[f"p{priority}_ack"] = str(ack)
            values[f"p{priority}_resolve"] = str(resolve)
    except (KeyError, TypeError, ValueError):
        return jsonify(error="SLA values must be whole numbers within valid ranges"), 400
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(values["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        return jsonify(error="Enter a valid IANA time zone, such as America/New_York"), 400
    hour_match = re.fullmatch(r"((?:[01][0-9]|2[0-3]):[0-5][0-9])-((?:(?:[01][0-9]|2[0-3]):[0-5][0-9])|24:00)", values["hours"])
    destination_pattern = r"(?:\+[1-9][0-9]{7,14}|sip:[^@\s]+@[^@\s]+)"
    invalid_destination = any(value and not re.fullmatch(destination_pattern, value, re.IGNORECASE)
                              for value in (values["human_destination"], values["voicemail_destination"]))
    if (not hour_match or hour_match.group(1) >= hour_match.group(2)
            or values["coverage_mode"] not in ("weekdays", "24x7")
            or values["after_hours_mode"] not in ("on_call", "voicemail") or invalid_destination):
        return jsonify(error="Invalid business hours, after-hours route, or transfer destination. Use E.164 phone numbers or sip:user@domain URIs."), 400
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for key, val in values.items():
        _conn().execute("INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (key, val, now))
    _conn().commit()
    os.environ["MSP_TZ"] = values["timezone"]
    os.environ["MSP_COVERAGE_MODE"] = values["coverage_mode"]
    os.environ["MSP_HOURS"] = values["hours"]
    os.environ["MSP_AFTER_HOURS_MODE"] = values["after_hours_mode"]
    _audit("routing_settings_updated", "settings", "routing", json.dumps(values))
    return jsonify(ok=True)


@app.get("/api/audit")
@_login_required
def api_audit():
    rows = _conn().execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 500").fetchall()
    return jsonify(items=[dict(r) for r in rows])


def run():
    host = os.getenv("HELPDESK_HOST", "127.0.0.1")
    port = int(os.getenv("HELPDESK_PORT", "5000"))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run()
