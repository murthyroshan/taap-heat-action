/* Shared helpers for every Taap page: nav, theme, city, API, motion, toasts. */
(function(){
const T = window.T = {};
const $ = T.$ = s => document.querySelector(s);
T.$$ = s => [...document.querySelectorAll(s)];
T.REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
T.FINE = matchMedia('(hover: hover) and (pointer: fine)').matches;
T.store = {get(k){try{return localStorage.getItem(k)}catch(e){return null}}, set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
T.esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
T.cssv = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
T.bandColors = () => ['--b0','--b1','--b2','--b3','--b4'].map(T.cssv);
T.tierColors = () => ({green:T.cssv('--t-green'),yellow:T.cssv('--t-yellow'),orange:T.cssv('--t-orange'),red:T.cssv('--t-red')});
T.TIERS = ['green','yellow','orange','red'];
T.pill = (txt, col, dark) => `<span class="pill${dark?' dark':''}" style="background:${col}">${T.esc(txt)}</span>`;
T.fmtTime = ts => ts ? new Date(ts*1000).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Kolkata'}) : '';
T.todayIST = () => new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Kolkata'});

/* static demo (GitHub Pages): API calls map to pre-exported JSON files */
T.STATIC = !!document.querySelector('meta[name="taap-static"]');
function staticPath(path){
  const [p, qs] = path.split('?'); const q = new URLSearchParams(qs||'');
  const parts = [p.replace(/^\/api\//,'').replace(/\//g,'-')];
  ['city','mode','date','zone','lang','new'].forEach(k => q.get(k) && parts.push(q.get(k)));
  return 'data/' + parts.join('_') + '.json';
}
T.api = async (path, body) => {
  if(T.STATIC){
    if(body) throw new Error('This is a read-only demo. Run Taap locally to use this.');
    const r = await fetch(staticPath(path)); if(!r.ok) throw new Error('not in the demo snapshot'); return r.json();
  }
  const r = await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
  const j = await r.json().catch(()=>({error:'bad response'}));
  if(!r.ok) throw new Error(j.error || r.status);
  return j;
};
if(T.STATIC) document.addEventListener('DOMContentLoaded', () => document.body.insertAdjacentHTML('afterbegin',
  '<div style="position:relative;z-index:1001;text-align:center;padding:7px 12px;font-size:12.5px;font-weight:600;background:linear-gradient(90deg,#ff6a1a,#ff2d55);color:#fff">Read-only demo snapshot · run Taap locally for live alerts, officer actions and the assistant</div>'));
document.addEventListener('DOMContentLoaded', () => {
  if(location.pathname.includes('saathi')) return;
  const a = document.createElement('a'); a.href = T.link('/saathi'); a.className = 'fab'; a.setAttribute('aria-label','Talk to Saathi');
  a.innerHTML = '<span class="fab-dot"></span>🎙️ <b>Saathi</b>'; document.body.append(a);
});
T.link = page => T.STATIC ? (page === '/' ? 'index.html' : page.slice(1) + '.html') : page;

/* toasts */
T.toast = (msg, kind='') => { let box = $('#toasts'); if(!box){ box = document.createElement('div'); box.id='toasts'; box.className='toasts'; document.body.append(box); }
  const t = document.createElement('div'); t.className = 'toast '+kind; t.textContent = msg; box.append(t);
  setTimeout(() => { t.style.transition='opacity .4s'; t.style.opacity=0; setTimeout(()=>t.remove(), 400); }, 4200); };

/* motion */
const io = new IntersectionObserver(es => es.forEach(e => { if(e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); } }), {threshold:.12, rootMargin:'0px 0px -40px 0px'});
T.reveal = () => T.$$('.reveal:not(.in)').forEach(el => io.observe(el));
T.countUp = (el, to, {dec=0, suffix='', dur=1100}={}) => {
  if(!el) return;
  if(T.REDUCED || !isFinite(to)){ el.textContent = (isFinite(to)?Number(to).toFixed(dec):'–')+suffix; return; }
  const from = parseFloat(el.dataset.v || 0), t0 = performance.now(); el.dataset.v = to;
  const step = now => { const p = Math.min(1,(now-t0)/dur), e = 1-Math.pow(1-p,3); el.textContent = (from+(to-from)*e).toFixed(dec)+suffix; if(p<1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
};
T.tilt = (root=document) => {
  if(T.REDUCED || !T.FINE) return;
  root.querySelectorAll('.tilt:not([data-t])').forEach(el => { el.dataset.t = 1;
    el.addEventListener('mousemove', e => { const r = el.getBoundingClientRect(), x = (e.clientX-r.left)/r.width, y = (e.clientY-r.top)/r.height;
      el.style.setProperty('--ry', ((x-.5)*8).toFixed(2)+'deg'); el.style.setProperty('--rx', ((.5-y)*8).toFixed(2)+'deg');
      el.style.setProperty('--mx', (x*100)+'%'); el.style.setProperty('--my', (y*100)+'%'); });
    el.addEventListener('mouseleave', () => { el.style.setProperty('--rx','0deg'); el.style.setProperty('--ry','0deg'); }); });
};
T.buttons = (root=document) => {
  root.querySelectorAll('.primary:not([data-m]),.ghost:not([data-m])').forEach(b => { b.dataset.m = 1;
    if(T.FINE && !T.REDUCED){
      b.addEventListener('mousemove', e => { const r = b.getBoundingClientRect(); b.style.transform = `translate(${(e.clientX-r.left-r.width/2)*.18}px,${(e.clientY-r.top-r.height/2)*.25}px)`; });
      b.addEventListener('mouseleave', () => b.style.transform = ''); }
    b.addEventListener('click', e => { if(T.REDUCED) return; const r = b.getBoundingClientRect(), d = Math.max(r.width, r.height), x = document.createElement('span');
      x.className = 'ripple'; x.style.cssText = `width:${d}px;height:${d}px;left:${e.clientX-r.left-d/2}px;top:${e.clientY-r.top-d/2}px`; b.append(x); setTimeout(() => x.remove(), 650); }); });
};
T.sparks = (el, n=26) => {
  if(T.REDUCED || !el) return; const r = el.getBoundingClientRect(), cx = r.left+r.width/2, cy = r.top+r.height/2;
  for(let i=0;i<n;i++){ const s = document.createElement('span'); s.className = 'spark'; document.body.append(s);
    const a = Math.random()*Math.PI*2, v = 60+Math.random()*120; s.style.background = `hsl(${20+Math.random()*30},100%,60%)`;
    s.animate([{transform:`translate(${cx}px,${cy}px)`,opacity:1},{transform:`translate(${cx+Math.cos(a)*v}px,${cy+Math.sin(a)*v+40}px) scale(.2)`,opacity:0}],{duration:700+Math.random()*500,easing:'cubic-bezier(.2,.8,.2,1)'}).onfinish = () => s.remove(); }
};

/* theme */
T.themeIcon = () => { const b = $('#theme'); if(b) b.textContent = document.documentElement.dataset.theme === 'light' ? '☾' : '☀︎'; };
(function(){ const t = T.store.get('taap.theme'); if(t) document.documentElement.dataset.theme = t; })();

/* shared nav: <nav id="nav" data-page="workers" data-city="1"></nav> */
const NAV = [['/','Home'],['/dashboard','Dashboard'],['/replay','2024 Replay'],['/workers','Workers'],['/saathi','🎙️ Saathi'],['/officers','Officers'],['/planner','Shift planner'],['/ledger','Ledger'],['/plans','Plans'],['/method','Method']];
T.cities = {}; T.bands = [];
T.city = () => (T.$('#city') || {}).value || T.store.get('taap.city') || 'delhi';
T.nav = async ({onCity}={}) => {
  const nav = $('#nav'); const page = nav.dataset.page;
  nav.innerHTML = `<a class="brand" href="${T.link('/')}"><div class="logo"><span>🔥</span></div>Taap</a>
    <button class="ctl menu" id="menuBtn" aria-label="Menu" aria-expanded="false">☰</button>
    <div class="links" id="links">${NAV.map(([h,l]) => `<a href="${T.link(h)}" class="${(h==='/'?'index':h.slice(1))===page?'on':''}">${l}</a>`).join('')}</div>
    <div class="controls">${nav.dataset.city ? '<select id="city" class="ctl" aria-label="City"></select>' : ''}<span id="navExtra"></span>
      <button id="theme" class="ctl" title="Toggle theme" aria-label="Toggle theme">☀︎</button></div>`;
  T.themeIcon();
  $('#theme').onclick = () => { const r = document.documentElement; r.dataset.theme = r.dataset.theme === 'light' ? 'dark' : 'light'; T.store.set('taap.theme', r.dataset.theme); T.themeIcon(); document.dispatchEvent(new Event('taap:theme')); };
  $('#menuBtn').onclick = () => { const l = $('#links'); l.classList.toggle('open'); $('#menuBtn').setAttribute('aria-expanded', l.classList.contains('open')); };
  const d = await T.api('/api/cities'); T.bands = d.bands;
  d.cities.forEach(c => T.cities[c.id] = c);
  const sel = $('#city');
  if(sel){
    d.cities.forEach(c => sel.insertAdjacentHTML('beforeend', `<option value="${c.id}">${T.esc(c.name)}</option>`));
    const saved = T.store.get('taap.city'); sel.value = (saved && T.cities[saved]) ? saved : (T.cities.delhi ? 'delhi' : d.cities[0].id);
    sel.onchange = () => { T.store.set('taap.city', sel.value); onCity && onCity(sel.value); };
  }
  if(!T.REDUCED) addEventListener('scroll', () => { const h = document.documentElement, b = $('#readbar'); if(b) b.style.width = (h.scrollTop/(h.scrollHeight-h.clientHeight)*100)+'%'; }, {passive:true});
  return d;
};
T.footer = () => { const f = $('#foot'); if(f) f.innerHTML = `<div class="foot"><span>🔥 <b>Taap</b> — a Heat Action Plan, running and accountable.</span><span>Weather: <a href="https://open-meteo.com">Open-Meteo</a> (CC-BY 4.0) · Maps: © OpenStreetMap · Agents: Strands Agents (AWS open source) · Emergency: 112</span></div>`; };
T.boot = () => { T.reveal(); T.tilt(); T.buttons(); T.footer(); };
})();
