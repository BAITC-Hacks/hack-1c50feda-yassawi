'use strict';
const $ = id => document.getElementById(id);
const fmt = (n, digits=0) => n == null ? '—' : new Intl.NumberFormat('ru-RU',{maximumFractionDigits:digits}).format(n);
const signed = n => `${n >= 0 ? '+' : '−'}${fmt(Math.abs(n))}`;
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let current = null, timer = null, busy = false;
const pages = {overview:['Шолу','Әр байланыс — есеппен.','Пилоттан дәлелге. Дәлелден тиімді науқанға.'],campaigns:['Науқандар','Дәлелге сүйенген жоспар.','Әр сегментке — бір шешім. Әр шешімге — түсіндірме.'],pilots:['Пилот зертханасы','Алдымен шағын сынақ.','Гипотезалар, бақылаулар және агенттің келесі қадамы.'],data:['Деректер','Шешімнің бастапқы нүктесі.','Аудитория, тарифтер және дерек сапасының ашық есебі.'],guide:['Әдіс','QADAM қалай ойлайды?','Тексерілетін тәсіл. Қайталанатын нәтиже.']};
function navigate(view){
  if(!pages[view]) return;
  document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==`view-${view}`);
  document.querySelectorAll('.nav-item').forEach(el=>{el.classList.toggle('active',el.dataset.view===view);if(el.dataset.view===view)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  [$('crumb').textContent,$('page-title').textContent,$('page-subtitle').textContent]=pages[view];
}
document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>navigate(el.dataset.view)));
async function api(path, options){const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw Error(data.error||`HTTP ${r.status}`);return data;}
function status(message,error=false,running=false){const el=$('status-box');el.hidden=!message;el.textContent=message;el.className=`status-box${error?' error':''}${running?' running':''}`;}
function setBusy(value){busy=value;['run-top','run-settings','seed','risk','pilots','stability'].forEach(id=>$(id).disabled=value);$('run-top').textContent=value?'◌ Есептелуде…':'✦ Агентті іске қосу';}
function emptyTables(){ $('campaign-rows').innerHTML='<tr><td colspan="7" class="zero-state">Жоспар әлі жасалған жоқ. «Агентті іске қосу» батырмасын басыңыз.</td></tr>'; $('pilot-rows').innerHTML='<tr><td colspan="7" class="zero-state">Пилот нәтижелері осы жерде көрінеді.</td></tr>'; }
async function loadOverview(){
  const o=await api('/api/overview');
  $('audience-total').textContent=fmt(o.audience);if(!current)$('kpi-reach-note').textContent=`${fmt(o.audience)} абоненттің ішінен`;
  $('data-audience').textContent=fmt(o.audience);$('data-history').textContent=fmt(o.history_rows);
  const issues=Object.values(o.quality).reduce((a,b)=>a+b,0);$('data-quality').textContent=issues===0?'Тексерілді':`${issues} мәселе`;
  $('quality-note').textContent=`Бос: ${o.quality.missing_required} · Дубликат: ${o.quality.duplicate_ids} · ARPU қатесі: ${o.quality.invalid_arpu}`;
  $('audience-bars').innerHTML=o.segments.map(s=>`<div class="audience-row"><span>${escapeHtml(s.name)}</span><div class="bar-track"><div class="bar-fill" style="width:${100*s.n/o.audience}%"></div></div><b>${fmt(s.n)}</b></div>`).join('');
  $('tariff-rows').innerHTML=o.tariffs.map(t=>`<tr><td><strong>${escapeHtml(t.tariff_plan_code)}</strong></td><td>${fmt(t.price_tariff,1)}</td><td>${fmt(t.Data_in_PKG)}</td><td>${fmt(t.Min_another_operator_in_PKG)}</td><td>${fmt(t.Min_another_operator_and_city_in_PKG)}</td></tr>`).join('');
}
function renderCampaigns(){
 if(!current)return;
 const query=$('search').value.toLowerCase(),channel=$('channel-filter').value;
 const rows=current.agent.campaigns.map((c,i)=>({...c,index:i})).filter(c=>(!channel||c.channel===channel)&&`${c.source} ${c.target} ${c.segment} ${c.campaign_name}`.toLowerCase().includes(query));
 $('campaign-rows').innerHTML=rows.length?rows.map(c=>`<tr><td><strong>${escapeHtml(c.source)}</strong><small>${escapeHtml(c.segment)} · n=${fmt(c.pilot_n)} пилот</small></td><td>${escapeHtml(c.target)}</td><td><span class="channel">${escapeHtml(c.channel)}</span></td><td>${fmt(c.n)}</td><td>${fmt(c.cost)}</td><td class="${c.lower>=0?'positive':'negative'}">${signed(c.lower)}</td><td><button class="text-button detail" data-index="${c.index}">Неге? ↗</button></td></tr>`).join(''):'<tr><td colspan="7" class="zero-state">Бұл сүзгіге сәйкес науқан жоқ.</td></tr>';
 $('campaign-rows').querySelectorAll('.detail').forEach(b=>b.addEventListener('click',()=>showDetail(Number(b.dataset.index))));
}
function showDetail(index){
 const c=current.agent.campaigns[index];
 $('detail-content').innerHTML=`<p class="eyebrow">ШЕШІМ ПАСПОРТЫ</p><h2>${escapeHtml(c.source)} → ${escapeHtml(c.target)}</h2><span class="tag">${escapeHtml(c.channel)} · ${escapeHtml(c.segment)}</span><p>Агент ${fmt(c.pilot_n)} пилоттық контакт нәтижесін пайдаланды. Сегмент бюджетке және контакт лимитіне сәйкес келеді. Соңғы жоспардағы сегменттер өзара қиылыспайды.</p><dl><dt>Аудитория</dt><dd>${fmt(c.n)}</dd><dt>Байланыс шығыны</dt><dd>${fmt(c.cost)}</dd><dt>Болжамды таза өсім</dt><dd>${signed(c.expected)}</dd><dt>Сақ бағалау</dt><dd>${signed(c.lower)}</dd><dt>Орташа әсер</dt><dd>${fmt(c.ratio*100,2)}%</dd><dt>Стандарттық қате</dt><dd>${fmt(c.se*100,2)} пайыздық тармақ</dd></dl><p>${c.measured_channel?'Бұл канал пилотта тексерілді.':'Бұл канал тікелей тексерілмеді: болжам қоғамдық канал көбейткіші арқылы ауыстырылды. Бұл жорамал қате болуы мүмкін.'}</p><p>Баға пилот шуына арналған жуық модельге сүйенеді. Бұл кепілді табыс немесе ресми confidence interval емес.</p><code>${escapeHtml(JSON.stringify(c.filters))}</code>`;
 $('detail-dialog').showModal();
}
function render(result){
 current=result;const a=result.agent,s=result.score;
 $('seed').value=result.config.seed;$('risk').value=String(result.config.risk);$('pilots').value=result.config.pilots;$('stability').checked=result.config.stability;
 $('kpi-net').textContent=signed(s.net_arpu_gain);$('kpi-net').className=s.net_arpu_gain>=0?'positive':'negative';
 $('kpi-net-note').textContent=`${s.status} · ${fmt(s.growth_vs_baseline_pct,2)}% baseline-ға`;
 $('kpi-cost').textContent=fmt(s.total_cost);$('budget-progress').value=s.total_cost;
 $('kpi-reach').textContent=fmt(s.unique_customers_targeted);$('kpi-reach-note').textContent=`${fmt(s.total_contacts)} контакт · ${fmt(s.coverage_pct,1)}% аудитория`;
 $('kpi-campaigns').innerHTML=`${a.campaigns.length} <em>/ 10</em>`;$('nav-count').textContent=a.campaigns.length;
 $('kpi-pilots-note').textContent=`${a.pilots.length} пилот · ${fmt(a.pilot_contacts)} контакт`;
 $('elapsed').textContent=`${fmt(a.seconds,2)} сек · seed ${result.config.seed}`;
 const amounts=[['ARPU өсімі',s.gross_arpu_lift,''],['Байланыс шығыны',s.total_cost,'cost'],['Таза нәтиже',s.net_arpu_gain,'net']];
 const max=Math.max(...amounts.map(x=>Math.abs(x[1])),1);$('gain-chart').className='gain-chart';
 $('gain-chart').innerHTML=amounts.map(([label,value,cls])=>`<div><div class="bar-label"><span>${label}</span><b>${fmt(value)} <small>у.е.</small></b></div><div class="bar-track"><div class="bar-fill ${cls} ${value<0?'negative':''}" style="width:${Math.max(1,Math.abs(value)/max*100)}%"></div></div></div>`).join('');
 $('pilot-count').textContent=`${a.pilots.length} / 20`;
 $('pilot-rows').innerHTML=a.pilots.map(p=>`<tr><td>${String(p.step).padStart(2,'0')} <small>${escapeHtml(p.reason)}</small></td><td>${escapeHtml(p.source)} → ${escapeHtml(p.target)}</td><td>${escapeHtml(p.segment)}</td><td><span class="channel">${escapeHtml(p.channel)}</span></td><td>${fmt(p.n)}</td><td class="${p.ratio>=0?'positive':'negative'}">${fmt(p.ratio*100,2)}%</td><td>${fmt(p.cost)}</td></tr>`).join('');
 $('warnings').innerHTML=a.warnings.map(w=>`<p>Ескерту: ${escapeHtml(w)}</p>`).join('');
 $('stability-card').hidden=!result.stability.length;
 if(result.stability.length){const maximum=Math.max(1,...result.stability.map(r=>Math.abs(r.net)));$('stability-summary').textContent=`${result.stability.filter(r=>r.net>0).length} / ${result.stability.length} оң нәтиже`;$('stability-chart').innerHTML=result.stability.map(r=>`<div class="stability-item"><span class="stability-value">${fmt(r.net/1000000,2)}M</span><div class="stability-bar ${r.net<0?'negative':''}" style="height:${Math.max(4,Math.abs(r.net)/maximum*115)}px"></div><span>seed ${r.seed}</span></div>`).join('');}
 $('export-top').disabled=false;$('export-audit').disabled=false;renderCampaigns();
}
async function poll(){
 try{const s=await api('/api/status');setBusy(s.running);
   if(s.running){status(s.message,false,true);timer=setTimeout(poll,600);return;}
   if(s.result){render(s.result);if(s.error)status(`${s.error} · Соңғы сәтті есеп көрсетіліп тұр.`,true);else status(`Жоспар дайын · ${s.result.created_at} · seed ${s.result.config.seed}`);}
   else if(s.error)status(s.error,true);else status('');
 }catch(e){setBusy(false);status(`Серверге қосылу мүмкін болмады: ${e.message}. Терминалда python server.py іске қосылғанын тексеріңіз.`,true);}
}
async function run(){
 if(busy)return;
 const seed=$('seed'),pilots=$('pilots');if(!seed.reportValidity()||!pilots.reportValidity())return;
 setBusy(true);status('Есеп басталып жатыр…',false,true);navigate('overview');clearTimeout(timer);
 try{await api('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({seed:Number(seed.value),pilots:Number(pilots.value),risk:Number($('risk').value),stability:$('stability').checked})});await poll();}
 catch(e){setBusy(false);status(e.message,true);}
}
$('run-top').addEventListener('click',run);$('run-settings').addEventListener('click',run);
$('search').addEventListener('input',renderCampaigns);$('channel-filter').addEventListener('change',renderCampaigns);
$('export-top').addEventListener('click',()=>{window.location.href='/api/export.csv';});$('export-audit').addEventListener('click',()=>{window.location.href='/api/export.json';});
$('close-dialog').addEventListener('click',()=>$('detail-dialog').close());
$('detail-dialog').addEventListener('click',e=>{if(e.target===$('detail-dialog'))$('detail-dialog').close();});
emptyTables();loadOverview().catch(e=>status(`Дерек жүктелмеді: ${e.message}`,true));poll();
