// Run with jsdom@26.1.0 available on NODE_PATH (CI-only dependency).
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const dom = new JSDOM(fs.readFileSync('panel/static/index.html', 'utf8'), {
  url: 'http://panel.test/', runScripts: 'outside-only',
});
const w = dom.window;
const sources = [];
let hidden = false;
Object.defineProperty(w.document, 'hidden', {get:()=>hidden});
w.EventSource = class {
  constructor(url) { this.url=url; this.listeners={}; this.closed=false; sources.push(this); }
  addEventListener(type, callback) { this.listeners[type]=callback; }
  close() { this.closed=true; }
};
const poll = [];
w.setInterval = callback => { poll.push(callback); return poll.length; };
w.confirm = () => true;
w.alert = message => { throw Error(message); };
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
const profile = {id:'p1', name:'HD', host:'server.test', port:5000, user:'test', pass:'test-only', deskey:'0102030405060708091011121314'};
const service = {sid:672, name:'Discovery', channel_id:'tp:672', newcamd_profile:'p1', profile_name:'HD'};
const tp = {id:'tp', name:'Mux A', adapter:0, freq_mhz:10970, pol:'V', type:'dvbs2', scan:{services:[service]}};
let health = {state:'ok', label:'ECM/CW continuas', latency_ms:185, successes_15s:3, window_seconds:15, last_result:0};
let offline = false;
const requests = [];
w.fetch = async (url, options={}) => {
  if (offline && url === '/api/stream/status') throw Error('offline');
  let value;
  if (options.method === 'POST') {
    const body = JSON.parse(options.body); requests.push([url,body]);
    if (url === '/api/channels/tp%3A672/edit') Object.assign(service,body);
    value = {ok:true,id:body.id || 'new-profile'};
  } else if (url === '/api/status') value = {platform:'Linux',adapters:[]};
  else if (url === '/api/transponders') value = [tp];
  else if (url === '/api/epg/settings') value = {days:1};
  else if (url === '/api/epg') value = [{transponder:'Mux A',transponder_id:'tp',services:[service]}];
  else if (url === '/api/newcamd/profiles') value = [{id:'default',name:'Predeterminado'}, {id:'p1',name:'HD'}];
  else if (url.startsWith('/api/newcamd/profiles/')) value = profile;
  else if (url === '/api/stream/status') value = {running:true,sessions:[{channel_id:'tp:672',running:true,bytes_received:188,queue_bytes:0,ecm:health}]};
  else throw Error('Unexpected URL '+url);
  return {ok:true,status:200,json:async()=>JSON.parse(JSON.stringify(value))};
};
const tick = () => new Promise(resolve=>setTimeout(resolve,10));
(async () => {
  w.eval(fs.readFileSync('panel/static/app.js','utf8') + '\nwindow.__testLoadStream = loadStream;');
  await tick();
  assert.equal(w.document.querySelector('[data-health]').textContent,'ECM/CW continuas');
  assert(w.document.querySelector('[data-health]').classList.contains('ecm-ok'));
  w.document.querySelector('[data-edit-channel]').click(); await tick();
  assert(w.document.getElementById('channelEditor').open);
  assert.equal(w.document.getElementById('ch_profile').value,'p1');
  w.document.getElementById('ch_name').value='Edited HD';
  w.document.getElementById('ch_group').value='Kids';
  w.document.getElementById('ch_number').value='42';
  w.document.getElementById('channelForm').dispatchEvent(new w.Event('submit',{cancelable:true}));
  await tick();
  assert.equal(requests[0][0],'/api/channels/tp%3A672/edit');
  assert.equal(requests[0][1].number,42);
  assert.equal(requests[0][1].newcamd_profile,'p1');
  assert.equal(w.document.getElementById('channelEditor').open,false);
  health = {...health,state:'error',label:'ECM/CW no disponible',last_result:-3};
  await w.__testLoadStream();
  assert(w.document.querySelector('[data-health]').classList.contains('ecm-error'));
  offline=true; await poll[0](); await tick();
  assert(w.document.querySelector('[data-health]').classList.contains('ecm-idle'));
  assert.equal(w.document.querySelector('[data-health]').textContent,'Sin telemetría');
  assert(!w.document.querySelector('.ecm-ok'));
  assert(w.document.querySelector('#epgBody [data-edit-channel="tp:672"]'));
  w.document.getElementById('epgDays').value='7';
  w.document.getElementById('epgSettingsForm').dispatchEvent(new w.Event('submit',{cancelable:true}));
  await tick();
  assert(requests.some(([url,body])=>url==='/api/epg/settings' && body.days===7));
  assert.equal(sources.length,0);
  w.document.querySelector('[data-tab="events"]').click();
  assert.equal(sources.length,1);
  sources[0].listeners.ready();
  sources[0].onmessage({data:JSON.stringify({time:Date.now()/1000,kind:'ecm',level:'info',channel:'tp:672',message:'ECM/CW recibida',latency_ms:185,result:0,cw_available:true})});
  await new Promise(resolve=>setTimeout(resolve,150));
  assert(w.document.getElementById('eventsBody').textContent.includes('ECM/CW recibida'));
  hidden=true; w.document.dispatchEvent(new w.Event('visibilitychange'));
  assert(sources[0].closed);
  assert.equal(w.document.getElementById('eventsBody').textContent,'');
  hidden=false; w.document.dispatchEvent(new w.Event('visibilitychange'));
  assert.equal(sources.length,2); sources[1].listeners.ready();
  assert(!w.document.getElementById('eventsBody').textContent.includes('ECM/CW recibida'));
  w.document.querySelector('[data-tab="dashboard"]').click();
  assert(sources[1].closed);
  assert.equal(w.document.getElementById('eventsBody').textContent,'');
  console.log('UI: editor, profile selection, numeric metadata, green/red and stale telemetry reset and live-only events lifecycle passed');
  dom.window.close();
})().catch(error=>{console.error(error);dom.window.close();process.exitCode=1;});
