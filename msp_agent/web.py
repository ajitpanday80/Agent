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


LOGIN = """<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'><title>Helpdesk sign in</title>
<style>body{font:16px system-ui;background:#10151c;color:#eef2f6;display:grid;place-items:center;min-height:100vh;margin:0}.card{background:#1b2531;padding:32px;border-radius:16px;width:min(380px,85vw)}input,button{box-sizing:border-box;width:100%;padding:12px;margin:8px 0;border-radius:8px;border:1px solid #445363;background:#121a23;color:white}button{background:#4c89e8;border:0;font-weight:700;cursor:pointer}.muted{color:#aab7c4}</style></head>
<body><form class=card method=post><h1>MSP Helpdesk</h1><p class=muted>Staff sign in</p><input name=username autocomplete=username placeholder='Username' required><input name=password type=password autocomplete=current-password placeholder='Password' required><button>Sign in</button><p class=muted>{{ error }}</p></form></body></html>"""


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


PAGE = r"""<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'><title>MSP Helpdesk</title>
<style>
:root{color-scheme:dark;--bg:#0d131b;--panel:#161f2a;--panel2:#1e2a38;--line:#2c3948;--text:#eef3f8;--muted:#a4b2c0;--blue:#6aa6ff;--green:#79d6ad;--red:#ff928d}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,-apple-system,sans-serif}header{height:64px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 24px;background:#111923;position:sticky;top:0;z-index:2}header b{font-size:18px}button,input,select,textarea{font:inherit;color:inherit}button{border:1px solid var(--line);background:var(--panel2);border-radius:8px;padding:9px 12px;cursor:pointer}button.primary{background:#3478dc;border-color:#3478dc}button:hover{filter:brightness(1.12)}.layout{display:grid;grid-template-columns:220px 1fr;min-height:calc(100vh - 64px)}nav{padding:18px 12px;border-right:1px solid var(--line)}nav button{display:block;width:100%;text-align:left;border:0;background:transparent;margin:3px 0;color:var(--muted)}nav button.active,nav button:hover{background:var(--panel2);color:var(--text)}main{padding:24px;max-width:1400px;width:100%;margin:auto}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:18px}h1{font-size:25px;margin:0 0 4px}.muted{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}.metric{font-size:24px;font-weight:700}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{text-align:left;padding:10px;border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:600}tr[data-id]{cursor:pointer}tr[data-id]:hover{background:var(--panel2)}.pill{padding:3px 8px;border-radius:20px;background:#26374a;font-size:12px}.p1{color:var(--red)}.p2{color:#ffc277}.field{display:flex;flex-direction:column;gap:5px;margin:10px 0}.field input,.field select,.field textarea,input,select,textarea{background:#0e151d;border:1px solid var(--line);border-radius:7px;padding:10px}.field textarea{min-height:80px}.formrow{display:grid;grid-template-columns:1fr 1fr;gap:10px}.hidden{display:none!important}.tabs{display:flex;gap:8px;flex-wrap:wrap}.detail{white-space:pre-wrap;line-height:1.55}.result{border-bottom:1px solid var(--line);padding:11px 0}.toast{position:fixed;right:18px;bottom:18px;padding:12px 16px;border-radius:9px;background:#264c3b;display:none}.actions{display:flex;gap:8px;flex-wrap:wrap}a{color:var(--blue)}@media(max-width:760px){.layout{grid-template-columns:1fr}nav{display:flex;overflow:auto;border-right:0;border-bottom:1px solid var(--line);padding:6px}nav button{white-space:nowrap;width:auto}.cards{grid-template-columns:1fr 1fr}.grid,.formrow{grid-template-columns:1fr}main{padding:16px}header{padding:0 14px}}
</style></head><body><header><b>MSP Helpdesk <span class=pill>Zoho Desk</span></b><form method=post action=/logout><input type=hidden name=csrf_token value="{{ csrf }}"><button>Sign out</button></form></header>
<div class=layout><nav id=nav><button class=active data-page=tickets>Ticket queue</button><button data-page=customers>Customers & agents</button><button data-page=handoffs>Voicemail & handoffs</button><button data-page=knowledge>Knowledge base</button><button data-page=incidents>Known incidents</button><button data-page=settings>Routing & system</button><button data-page=audit>Audit log</button></nav><main><div id=content></div></main></div><div id=toast class=toast></div>
<script>const csrf={{ csrf|tojson }}, cats={{ categories|tojson }};let page='tickets';const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,opts={}){opts.headers={...(opts.headers||{}),'Content-Type':'application/json','X-CSRF-Token':csrf};let r=await fetch('/api/'+path,opts),d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||r.statusText);return d}function toast(t){let e=document.querySelector('#toast');e.textContent=t;e.style.display='block';setTimeout(()=>e.style.display='none',2500)}
function setpage(p){page=p;document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===p));render()};document.querySelector('#nav').onclick=e=>{let b=e.target.closest('button[data-page]');if(b)setpage(b.dataset.page)};
async function render(){let root=document.querySelector('#content');try{if(page==='tickets')await ticketPage(root);if(page==='customers')await customerPage(root);if(page==='handoffs')await handoffPage(root);if(page==='knowledge')await kbPage(root);if(page==='incidents')await incidentPage(root);if(page==='settings')await settingsPage(root);if(page==='audit')await auditPage(root)}catch(e){root.innerHTML=`<div class=panel><h2>Could not load ${esc(page)}</h2><p class=muted>${esc(e.message)}</p><button onclick=render()>Retry</button></div>`}}
async function ticketPage(root){let d=await api('tickets'),rows=d.tickets;root.innerHTML=`<div class=top><div><h1>Ticket queue</h1><span class=muted>Tickets and status are managed in Zoho Desk.</span></div><div class=actions><button onclick="window.open('${esc(d.zoho_url||'#')}','_blank')">Open Zoho Desk</button><button class=primary onclick="newTicket()">Create ticket</button></div></div><div class=cards><div class=card><div class=muted>Open in view</div><div class=metric>${rows.filter(x=>!['closed','resolved'].includes(x.status)).length}</div></div><div class=card><div class=muted>P1</div><div class=metric>${rows.filter(x=>x.priority===1).length}</div></div><div class=card><div class=muted>Unassigned</div><div class=metric>${rows.filter(x=>!x.assignee).length}</div></div><div class=card><div class=muted>Handoffs waiting</div><div class=metric>${d.handoff_count}</div></div></div><div class=panel scroll><input id=tsearch placeholder="Filter these latest Zoho tickets" oninput="filterTickets(this.value)" style="width:100%;margin-bottom:10px"><table><thead><tr><th>Ticket</th><th>Priority</th><th>Status</th><th>Customer</th><th>Subject</th><th>Assignee</th></tr></thead><tbody>${rows.map(t=>`<tr data-id="${esc(t.id)}" data-search="${esc([t.ref,t.company_name,t.summary,t.assignee].join(' ')).toLowerCase()}" onclick="showTicket('${esc(t.id)}')"><td>#${esc(t.ref)}</td><td class="p${t.priority}">P${t.priority}</td><td>${esc(t.status.replaceAll('_',' '))}</td><td>${esc(t.company_name)}</td><td>${esc(t.summary)}</td><td>${esc(t.assignee||'Unassigned')}</td></tr>`).join('')}</tbody></table></div>`}
function filterTickets(q){document.querySelectorAll('tr[data-search]').forEach(r=>r.style.display=r.dataset.search.includes(q.toLowerCase())?'':'none')}
async function customerPage(root){let d=await api('customers');root.innerHTML=`<div class=top><div><h1>Customers & agents</h1><span class=muted>Accounts, contacts, and agents are synced from Zoho Desk. VIP routing flags are local policy.</span></div><div class=actions><button onclick="newAccount()">Add account</button><button class=primary onclick="newContact()">Add contact</button></div></div><div class=grid><div class=panel><h2>Zoho Desk accounts</h2><div class=scroll><table><thead><tr><th>Account</th><th>Phone</th><th>Email</th></tr></thead><tbody>${d.accounts.map(a=>`<tr><td>${esc(a.accountName||a.name)}</td><td>${esc(a.phone)}</td><td>${esc(a.email)}</td></tr>`).join('')}</tbody></table></div></div><div class=panel><h2>Zoho Desk contacts</h2><div class=scroll><table><thead><tr><th>Contact</th><th>Account</th><th>Phone</th><th>VIP</th></tr></thead><tbody>${d.contacts.map(c=>`<tr><td>${esc((c.firstName||'')+' '+(c.lastName||''))}</td><td>${esc(c.account?.accountName)}</td><td>${esc(c.phone)}</td><td><input type=checkbox ${c.vip?'checked':''} onchange="vipFlag('${esc(c.id)}',this.checked)"></td></tr>`).join('')}</tbody></table></div></div></div><div class=panel style="margin-top:14px"><h2>Zoho Desk agents</h2>${d.agents.map(a=>`<span class=pill>${esc(a.name)} · ${esc(a.email)}</span> `).join('')}</div>`}
async function newAccount(){let name=prompt('Zoho Desk account name');if(!name)return;let phone=prompt('Main phone (optional)','')||'';await api('customers/accounts',{method:'POST',body:JSON.stringify({accountName:name,phone})});toast('Account added to Zoho Desk');render()}
async function newContact(){let d=await api('customers'),account=prompt('Enter exact Zoho Desk account name:\n'+d.accounts.map(a=>a.accountName||a.name).join('\n'));if(!account)return;let first=prompt('First name')||'',last=prompt('Last name');if(!last)return;let email=prompt('Email (optional)','')||'',phone=prompt('Phone','')||'';await api('customers/contacts',{method:'POST',body:JSON.stringify({firstName:first,lastName:last,email,phone,accountName:account})});toast('Contact added to Zoho Desk');render()}
async function vipFlag(id,is_vip){await api('customers/'+encodeURIComponent(id)+'/flags',{method:'PATCH',body:JSON.stringify({is_vip})});toast('VIP routing updated')}
async function showTicket(id){let [t,staff]=await Promise.all([api('tickets/'+encodeURIComponent(id)),api('agents')]);let root=document.querySelector('#content');root.innerHTML=`<div class=top><div><button onclick="setpage('tickets')">← Queue</button><h1 style="display:inline;margin-left:12px">Ticket #${esc(t.ref)}</h1></div><a target=_blank href="${esc(t.webUrl||'#')}">Open in Zoho Desk ↗</a></div><div class=grid><div class=panel><h2>${esc(t.summary)}</h2><p>${esc(t.company_name)} · ${esc(t.caller_name)}</p><p><span class="pill p${t.priority}">P${t.priority}</span> ${esc(t.status)}</p><div class=detail>${esc(t.details)}</div><hr><div class=formrow><label class=field>Status<select id=tstatus><option>Open</option><option>On Hold</option><option>Escalated</option><option>Closed</option></select></label><label class=field>Assignee<select id=assignee><option value="">Unassigned</option>${staff.agents.map(a=>`<option value="${esc(a.email)}" ${a.email===t.assignee_email?'selected':''}>${esc(a.name)} (${esc(a.email)})</option>`).join('')}</select></label></div><button class=primary onclick="saveTicket('${esc(t.id)}')">Save in Zoho</button><div class=field><label>Private note</label><textarea id=note></textarea></div><button onclick="addNote('${esc(t.id)}')">Add private note</button></div><div class=panel><h2>Conversation</h2>${(t.comments||[]).map(c=>`<div class=result><b>${esc(c.author?.name||'Agent')}</b><div class=muted>${esc(c.createdTime||'')}</div><p>${esc(c.content)}</p></div>`).join('')||'<p class=muted>No comments available.</p>'}</div></div>`;document.querySelector('#tstatus').value=t.status_display||'Open'}
async function saveTicket(id){await api('tickets/'+encodeURIComponent(id),{method:'PATCH',body:JSON.stringify({status:document.querySelector('#tstatus').value,assignee:document.querySelector('#assignee').value})});toast('Updated in Zoho Desk');showTicket(id)}async function addNote(id){let content=document.querySelector('#note').value;await api('tickets/'+encodeURIComponent(id)+'/comments',{method:'POST',body:JSON.stringify({content})});toast('Note added in Zoho Desk');showTicket(id)}
async function newTicket(){let root=document.querySelector('#content');root.innerHTML=`<div class=top><div><button onclick="setpage('tickets')">← Queue</button><h1 style="display:inline;margin-left:12px">Create ticket in Zoho Desk</h1></div></div><form id=newticket class=panel><div class=formrow><label class=field>Caller name<input name=name required></label><label class=field>Company<input name=company required></label><label class=field>Callback number<input name=phone></label><label class=field>Category<select name=category>${cats.map(c=>`<option>${esc(c)}</option>`).join('')}</select></label><label class=field>Scope<select name=scope><option>single_user</option><option>several_users</option><option>whole_company</option></select></label><label class=field>Severity<select name=severity><option>minor</option><option>degraded</option><option>stopped</option></select></label></div><label class=field>Summary<input name=summary required></label><label class=field>Details<textarea name=details></textarea></label><label><input style="width:auto" type=checkbox name=security> Security incident / suspected data loss</label><p><button class=primary>Create in Zoho</button></p></form>`;root.querySelector('form').onsubmit=async e=>{e.preventDefault();let o=Object.fromEntries(new FormData(e.target));o.security=!!e.target.security.checked;await api('tickets',{method:'POST',body:JSON.stringify(o)});toast('Created in Zoho Desk');setpage('tickets')}}
async function handoffPage(root){let d=await api('handoffs');root.innerHTML=`<div class=top><div><h1>Voicemail & handoffs</h1><span class=muted>Follow up calls, failed transfers, and caller messages alongside Zoho tickets.</span></div><button class=primary onclick="intakeForm()">Log voicemail / callback</button></div><div class=panel scroll><table><thead><tr><th>Received</th><th>Type</th><th>State</th><th>Ticket</th><th>Callback</th><th>Summary / message</th><th>Action</th></tr></thead><tbody>${d.items.map(h=>`<tr><td>${esc(h.created_at)}</td><td>${esc(h.kind)}</td><td>${esc(h.status)}</td><td>${h.ticket_id?`#${esc(h.ticket_ref||h.ticket_id)}`:'—'}</td><td>${esc(h.callback_number)}</td><td>${esc(h.summary)}<details><summary>Message</summary><div class=detail>${esc(h.transcript)}</div></details></td><td><button onclick="handoffState(${h.id},'in_progress')">Start</button> <button onclick="handoffState(${h.id},'completed')">Complete</button></td></tr>`).join('')}</tbody></table></div>`}
function intakeForm(){let r=document.querySelector('#content');r.insertAdjacentHTML('afterbegin',`<form id=intake class=panel><h2>Voicemail / callback intake</h2><div class=formrow><label class=field>Caller name<input name=name required></label><label class=field>Company<input name=company required></label><label class=field>Callback number<input name=phone></label><label class=field>Type<select name=kind><option value=voicemail>Voicemail</option><option value=callback>Callback request</option><option value=human_request>Human agent request</option></select></label></div><label class=field>Summary<input name=summary required></label><label class=field>Transcript or message<textarea name=transcript></textarea></label><button class=primary>Save and create Zoho ticket</button> <button type=button onclick="this.form.remove()">Cancel</button></form>`);r.querySelector('#intake').onsubmit=async e=>{e.preventDefault();let d=await api('handoffs',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});toast(d.ticket_created?'Zoho ticket and handoff created':'Saved to intake; Zoho ticket not created');render()}}
async function handoffState(id,status){await api('handoffs/'+id,{method:'PATCH',body:JSON.stringify({status})});toast('Handoff updated');render()}
async function kbPage(root){let d=await api('knowledge'),a=d.articles;root.innerHTML=`<div class=top><div><h1>Knowledge base</h1><span class=muted>Articles are drafts until an admin reviews them. Only enabled, sourced articles reach the voice agent.</span></div><div class=actions><button onclick="importKb()">Import runbook text</button><button class=primary onclick="kbEditor()">New article</button></div></div><div class=panel><div class=actions><input id=kbquery placeholder='Search troubleshooting library' style='flex:1'><button onclick="kbSearch()">Search</button><button onclick="kbSearch(true)">Preview matches</button></div><div id=kbresults></div></div><div class=panel style="margin-top:14px"><table><thead><tr><th>Article</th><th>Category</th><th>Client</th><th>Review</th><th>Sources</th><th></th></tr></thead><tbody>${a.map(x=>`<tr><td>${esc(x.title)}</td><td>${esc(x.category)}</td><td>${esc(x.company_name||'All clients')}</td><td>${x.enabled?'Approved / enabled':esc(x.review_status||'draft')}</td><td>${x.sources.length}</td><td><button onclick="kbEditor('${esc(x.id)}')">Edit / review</button></td></tr>`).join('')}</tbody></table></div>`}
function importKb(){let root=document.querySelector('#content');root.insertAdjacentHTML('afterbegin',`<form id=importkb class=panel><h2>Import runbook text</h2><label class=field>Title<input name=title required></label><label class=field>Category<select name=category>${cats.map(c=>`<option>${esc(c)}</option>`).join('')}</select></label><label class=field>Client scope (blank means shared)<input name=company_name></label><label class=field>Source URLs (one per line)<textarea name=sources></textarea></label><label class=field>Paste text<textarea name=content required style="min-height:220px"></textarea></label><button class=primary>Import and split into searchable chunks</button> <button type=button onclick="this.form.remove()">Cancel</button></form>`);root.querySelector('#importkb').onsubmit=async e=>{e.preventDefault();let o=Object.fromEntries(new FormData(e.target));o.sources=o.sources.split('\n').filter(Boolean);let d=await api('knowledge/import',{method:'POST',body:JSON.stringify(o)});toast(`Imported ${d.chunks} chunks`);render()}}
async function kbSearch(preview=false){let q=encodeURIComponent(document.querySelector('#kbquery').value),d=await api('knowledge/search?q='+q),el=document.querySelector('#kbresults');el.innerHTML=d.results.map(x=>`<div class=result><b>${esc(x.title)}</b> <span class=pill>${esc(x.category)}</span><p>${esc(x.content)}</p>${(x.steps||[]).map((s,i)=>`<p>${i+1}. ${esc(s)}</p>`).join('')}<small class=muted>Relevance ${x.score}; sources: ${esc(x.sources.join(', '))}</small></div>`).join('')||'<p class=muted>No relevant article found.</p>'}
async function kbEditor(id){let root=document.querySelector('#content'),a=id?await api('knowledge/'+encodeURIComponent(id)):null;root.insertAdjacentHTML('afterbegin',`<form id=kbform class=panel><h2>${a?'Edit article':'New KB article'}</h2><p class=muted>Enabling an article records your admin account as reviewer. Add authoritative source URLs and confirm the client scope and safe steps before enabling.</p><input type=hidden name=id value="${esc(a?.id||'')}"><div class=formrow><label class=field>Title<input name=title required value="${esc(a?.title||'')}"></label><label class=field>Category<select name=category>${cats.map(c=>`<option ${a?.category===c?'selected':''}>${esc(c)}</option>`).join('')}</select></label></div><label class=field>Client scope (blank means shared) <input name=company_name value="${esc(a?.company_name||'')}"></label><label class=field>Search content / symptom description<textarea name=content required>${esc(a?.content||'')}</textarea></label><label class=field>Step-by-step safe instructions (one per line)<textarea name=steps>${esc((a?.steps||[]).join('\n'))}</textarea></label><label class=field>Source URLs (one per line)<textarea name=sources>${esc((a?.sources||[]).join('\n'))}</textarea></label><label><input style="width:auto" type=checkbox name=enabled ${a?.enabled?'checked':''}> Approve and enable for agent</label><p><button class=primary>Save article</button> <button type=button onclick="this.form.remove()">Cancel</button></p></form>`);root.querySelector('#kbform').onsubmit=async e=>{e.preventDefault();let f=e.target,o=Object.fromEntries(new FormData(f));o.steps=o.steps.split('\n').filter(Boolean);o.sources=o.sources.split('\n').filter(Boolean);o.enabled=f.enabled.checked;await api('knowledge',{method:'POST',body:JSON.stringify(o)});toast('Knowledge article saved');render()}}
async function incidentPage(root){let d=await api('incidents');root.innerHTML=`<div class=top><div><h1>Known incidents</h1><span class=muted>Local operational notices surfaced to the voice agent.</span></div><button class=primary onclick="incidentForm()">Add incident</button></div><div class=panel><table><thead><tr><th>Incident</th><th>Category</th><th>Status</th><th>Created</th><th></th></tr></thead><tbody>${d.items.map(i=>`<tr><td>${esc(i.title)}</td><td>${esc(i.category)}</td><td>${esc(i.status)}</td><td>${esc(i.created_at)}</td><td>${i.status==='active'?`<button onclick="resolveIncident(${i.id})">Resolve</button>`:''}</td></tr>`).join('')}</tbody></table></div>`}
function incidentForm(){let t=prompt('Incident title');if(!t)return;let c=prompt('Category: '+cats.join(', '),'network_vpn');api('incidents',{method:'POST',body:JSON.stringify({title:t,category:c})}).then(()=>{toast('Incident added');render()}).catch(e=>toast(e.message))}async function resolveIncident(id){await api('incidents/'+id,{method:'PATCH',body:JSON.stringify({status:'resolved'})});render()}
async function settingsPage(root){let d=await api('settings');let slas=[1,2,3,4].map(p=>`<div class=formrow><label class=field>P${p} first response (minutes)<input type=number min=1 name=p${p}_ack value="${esc(d.settings['p'+p+'_ack'])}"></label><label class=field>P${p} target resolution (hours)<input type=number min=1 name=p${p}_resolve value="${esc(d.settings['p'+p+'_resolve'])}"></label></div>`).join('');root.innerHTML=`<div class=top><div><h1>Routing & system</h1><span class=muted>Escalation, contract targets, and connectivity</span></div></div><div class=grid><form id=settings class=panel><h2>Call escalation policy</h2><label class=field>Time zone<input name=timezone value="${esc(d.settings.timezone||'Australia/Sydney')}"></label><label class=field>Coverage<select name=coverage_mode><option value=weekdays>Weekdays during configured hours</option><option value=24x7>24/7 including weekends</option></select></label><label class=field>Weekday hours (24h, local time)<input name=hours value="${esc(d.settings.hours||'00:00-24:00')}"></label><label class=field>After-hours urgent ticket route<select name=after_hours_mode><option value=on_call>Staff follow-up queue</option><option value=voicemail>Voicemail transfer destination</option></select></label><h2>LiveKit call destinations</h2><p class=muted>Use E.164 numbers (for example +61255550123) or SIP URIs. Your LiveKit SIP trunk/provider must allow call transfers (SIP REFER). Transfers are attempted only when a caller requests them or policy routes urgent calls.</p><label class=field>Human agent destination<input name=human_destination placeholder="+61255550123 or sip:user@example.com" value="${esc(d.settings.human_destination||'')}"></label><label class=field>Voicemail destination<input name=voicemail_destination placeholder="+61255550124 or sip:mailbox@example.com" value="${esc(d.settings.voicemail_destination||'')}"></label><h2>SLA targets</h2><p class=muted>Enter targets from your MSP contracts. These are decision support only; configure SLA policies in Zoho Desk for enforcement and notifications. The app cannot confirm staff response or resolution.</p>${slas}<p><button class=primary>Save routing settings</button></p></form><div class=panel><h2>Zoho Desk connection</h2><p>${d.zoho.configured?'Configured':'Not configured'}</p><p class=muted>${esc(d.zoho.message||'')}</p><p>Tickets remain in Zoho Desk.</p><h2>LiveKit phone line</h2><p>Inbound calls use your LiveKit dispatch rule. A caller request creates a Zoho ticket, then transfers the active SIP call to the configured destination. If voicemail transfer fails, the agent can collect a spoken message and save its transcript in Zoho Desk and the staff handoff queue.</p><p class=muted>LiveKit transfer requires working API credentials and SIP REFER support on the provider trunk. Confirm this with your LiveKit/provider configuration before relying on transfers.</p></div></div>`;root.querySelector('[name=coverage_mode]').value=d.settings.coverage_mode||'24x7';root.querySelector('[name=after_hours_mode]').value=d.settings.after_hours_mode||'on_call';root.querySelector('#settings').onsubmit=async e=>{e.preventDefault();let o=Object.fromEntries(new FormData(e.target));await api('settings',{method:'POST',body:JSON.stringify(o)});toast('Routing settings saved')}}
async function auditPage(root){let d=await api('audit');root.innerHTML=`<div class=top><div><h1>Audit log</h1><span class=muted>Recent admin changes made in this console.</span></div></div><div class=panel scroll><table><thead><tr><th>Time UTC</th><th>Actor</th><th>Action</th><th>Object</th><th>Details</th></tr></thead><tbody>${d.items.map(x=>`<tr><td>${esc(x.created_at)}</td><td>${esc(x.actor)}</td><td>${esc(x.action)}</td><td>${esc(x.entity_type)} ${esc(x.entity_id)}</td><td>${esc(x.details)}</td></tr>`).join('')}</tbody></table></div>`}
render();
</script></body></html>"""


@app.get("/")
@_login_required
def dashboard():
    return render_template_string(PAGE, csrf=session.get("csrf", ""), categories=escalation.CATEGORIES)


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
            "review_status": row["review_status"], "reviewed_by": row["reviewed_by"]}


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
    if not title or not content or category not in escalation.CATEGORIES:
        return jsonify(error="Title, content, and a valid category are required"), 400
    if any(urlparse(source).scheme not in ("http", "https") or not urlparse(source).netloc for source in sources):
        return jsonify(error="Sources must be complete http or https URLs"), 400
    if enabled and (not sources or not steps):
        return jsonify(error="To approve an article, add at least one source URL and one safe step"), 400
    if company_name and not any(str(a.get("accountName", "")).lower() == company_name.lower()
                                for a in zoho.list_accounts()):
        return jsonify(error="Client scope must exactly match a Zoho Desk account"), 400
    article_id = str(data.get("id") or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"))[:100]
    if not article_id:
        article_id = secrets.token_hex(8)
    old = _conn().execute("SELECT created_at FROM kb_articles WHERE id=?", (article_id,)).fetchone()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _conn().execute("INSERT INTO kb_articles(id,title,category,company_name,content,steps,sources,enabled,review_status,reviewed_by,reviewed_at,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,category=excluded.category,"
                    "company_name=excluded.company_name,"
                    "content=excluded.content,steps=excluded.steps,sources=excluded.sources,enabled=excluded.enabled,"
                    "review_status=excluded.review_status,reviewed_by=excluded.reviewed_by,reviewed_at=excluded.reviewed_at,updated_at=excluded.updated_at",
                    (article_id, title, category, company_name, content, json.dumps(steps),
                     json.dumps(sources), int(enabled), "approved" if enabled else "draft",
                     session.get("user", "admin") if enabled else "", now if enabled else None,
                     old["created_at"] if old else now, now))
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
    if not title or not content or category not in escalation.CATEGORIES:
        return jsonify(error="Title, content, and valid category are required"), 400
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
        _conn().execute("INSERT OR REPLACE INTO kb_articles(id,title,category,company_name,content,steps,sources,enabled,review_status,reviewed_by,reviewed_at,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (aid, f"{title} (part {i})", category, company_name, chunk,
                        "[]", json.dumps(data.get("sources", [])), 0, "draft", "", None, now, now))
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


@app.post("/api/handoffs")
@_login_required
def api_handoff_create():
    data = request.get_json(force=True)
    name, company, summary = (str(data.get(k, "")).strip() for k in ("name", "company", "summary"))
    if not name or not company or not summary:
        return jsonify(error="Caller name, company, and message summary are required"), 400
    callback = str(data.get("phone", ""))
    transcript = str(data.get("transcript", ""))
    try:
        result = tickets.create(_conn(), caller_name=name, company_name=company, callback_number=callback,
                                category="other", summary=summary,
                                details="Voicemail or callback intake. " + transcript,
                                scope="single_user", severity="degraded")
        ticket_id = result["ticket"]["id"]
        ticket = result["ticket"]
        kind = str(data.get("kind", "voicemail"))
    except Exception as error:
        app.logger.exception("Could not create Zoho ticket for handoff")
        ticket_id, ticket, kind = None, None, "zoho_unavailable"
        transcript = f"Caller {name} at {company}. {transcript}. Zoho error: {error}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = _conn().execute("INSERT INTO handoffs(ticket_id,kind,status,summary,callback_number,transcript,created_at,updated_at) "
                          "VALUES(?,?, 'queued', ?,?,?,?,?)",
                          (ticket_id, kind, summary, callback, transcript, now, now))
    _conn().commit()
    _audit("handoff_created", "handoff", cur.lastrowid, summary)
    return jsonify(id=cur.lastrowid, ticket=ticket, ticket_created=bool(ticket)), 201


@app.patch("/api/handoffs/<int:handoff_id>")
@_login_required
def api_handoff_update(handoff_id):
    data = request.get_json(force=True)
    status = data.get("status", "queued")
    if status not in ("queued", "in_progress", "completed", "cancelled"):
        return jsonify(error="Invalid handoff state"), 400
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _conn().execute("UPDATE handoffs SET status=?,assignee=?,updated_at=? WHERE id=?",
                    (status, data.get("assignee"), now, handoff_id))
    _conn().commit()
    _audit("handoff_updated", "handoff", handoff_id, status)
    return jsonify(ok=True)


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
