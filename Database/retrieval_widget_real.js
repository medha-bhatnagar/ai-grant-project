/* Adds a real MongoDB retrieval panel to Medha's UNCHANGED dashboard.
   No login/approval/chat features of the original dashboard are replaced. */
(() => {
  'use strict';
  const style = document.createElement('style');
  style.textContent = `
    .t44-search{margin-top:0}
    .t44-search .t44-meta{font-size:12px;color:#4A5565;margin:8px 0;}
    .t44-search .t44-controls{display:flex;gap:8px;align-items:end;flex-wrap:wrap;}
    .t44-search label{font-size:12px;font-weight:600;display:flex;flex-direction:column;gap:4px;flex:1;min-width:200px;}
    .t44-search .t44-results{margin-top:12px;max-height:420px;overflow:auto;}
    .t44-search .t44-entry{padding:10px 0;border-top:1px solid #E6E9EE;}
    .t44-search .t44-entry strong{font-size:13px}
    .t44-search .t44-entry p{margin:7px 0 0;font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}
    .t44-search .t44-count{font-size:13px;font-weight:600;margin-top:10px}
  `;
  document.head.append(style);
  function el(tag, cls, text) { const e = document.createElement(tag); if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e; }
  async function get(url) {
    const res = await fetch(url, {headers:{Accept:'application/json'}});
    const data = await res.json();
    if(!res.ok) throw Error(data.error || 'API error');
    return data;
  }
  function attach(parent, before) {
    if(!parent) return;
    const card = el('section','card t44-search');
    card.append(el('h2','','Document Retrieval — Real MongoDB'));
    const caption = el('p','t44-meta','Checking local MongoDB…'); card.append(caption);
    const controls = el('form','t44-controls');
    const label = el('label','','Search original NIH application text');
    const input = el('input','input');input.placeholder='EmrE, peptides, molecular dynamics…';input.value='EmrE';label.append(input);
    const searchButton=el('button','btn primary','Search');searchButton.type='submit';
    controls.append(label,searchButton);card.append(controls);
    const status=el('div','t44-count');status.setAttribute('role','status');card.append(status);
    const results=el('div','t44-results');results.setAttribute('aria-live','polite');card.append(results);
    const source=el('p','t44-meta');
    const srcLink=el('a','','View unchanged source PDF');srcLink.href='/integration/original_nih_sample.pdf';srcLink.target='_blank';srcLink.rel='noopener';
    source.append(srcLink,document.createTextNode(' · Martin Karplus / Harvard University / NIAID · historical R03 sample (2013), not current NIH instructions.'));
    card.append(source);parent.insertBefore(card,before||null);

    async function run() {
      searchButton.disabled=true;status.textContent='Querying MongoDB…';results.replaceChildren();
      try {
        const query = new URLSearchParams({query:input.value.trim(),collection:'past_submissions',funding_source:'NIH',limit:'6'});
        const data=await get('/api/retrieve?'+query.toString());
        status.textContent=`${data.count} result(s) returned by real MongoDB query`;
        if(!data.count)results.append(el('p','t44-meta','No matching chunks. Try EmrE or peptide.'));
        for(const rec of data.results){
          const line=el('article','t44-entry');
          line.append(el('strong','',rec.doc_link||'NIAID PDF'));
          line.append(el('div','t44-meta',`${rec.funding_source||'NIH'} · ${rec.chunk_id||''}`));
          line.append(el('p','',rec.text||''));results.append(line);
        }
      } catch(err) {status.textContent='Live database error: '+err.message;}
      finally{searchButton.disabled=false;}
    }
    controls.addEventListener('submit',e=>{e.preventDefault();run();});
    get('/api/status').then(data=>{
      caption.textContent=`LIVE MongoDB: ${data.database}.${data.collection} · ${data.indexed_chunks} chunks from the actual NIAID PDF. No fake fallback or vectors.`;
      run();
    }).catch(err=>{caption.textContent='Live MongoDB not ready: '+err.message;});
  }
  function boot(){
    const p=document.querySelector('#view-pi .col.main');attach(p,p?p.children[1]:null);
    const s=document.querySelector('#view-staff .col.center');attach(s,s?s.children[2]:null);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
