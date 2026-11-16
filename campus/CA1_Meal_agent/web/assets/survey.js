'use strict';
(() => {
  const $=id=>document.getElementById(id), days=[...'월화수목금토일'];
  let rows=[], selected=null, revision=0, dirty=false, loading=0, saving=false;
  let needed=new Set(), savedEntry={};
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const error=(id,message)=>{$(id).textContent=message||'';$(id).hidden=!message;};
  const stateLabel=s=>({review:'검토 준비',partial:'일부 저장',empty:'미입력'}[s]||'미입력');
  const setDirty=()=>{dirty=true;$('save-status').textContent='저장하지 않은 변경사항이 있습니다.';};
  async function api(url,body){
    const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();
    if(!response.ok){const detail=data.detail;throw new Error(Array.isArray(detail)?detail.map(e=>e.msg.replace(/^Value error, /,'')).join('\n'):detail||'요청을 처리하지 못했습니다.');}
    return data;
  }
  function textInput(label,value,kind='text',cls){
    const wrap=el('label',label),input=el('input');input.type=kind;input.value=value??'';if(cls)input.className=cls;wrap.append(input);return [wrap,input];
  }
  function addMenu(value={}){
    const row=el('div',undefined,'menu-row');
    const [nameLabel,name]=textInput('메뉴명',value.name);name.maxLength=40;name.dataset.field='name';
    const [priceLabel,price]=textInput('매장 가격 (원)',value.price,'number');price.min='1';price.max='200000';price.step='1';price.placeholder='미확인';price.dataset.field='price';
    const mainLabel=el('label',undefined,'check'),main=el('input');main.type='checkbox';main.checked=value.main===true;main.dataset.field='main';mainLabel.append(main,document.createTextNode('주 식사 메뉴'));
    const remove=el('button','삭제','remove');remove.type='button';remove.setAttribute('aria-label','메뉴 행 삭제');remove.addEventListener('click',()=>{row.remove();setDirty();});
    const [noteLabel,note]=textInput('분량·가격 조건 메모',value.note);note.maxLength=500;note.placeholder='예: 2인분 / 200g / 점심 11~15시';note.dataset.field='note';noteLabel.className='menu-note';
    row.append(nameLabel,priceLabel,mainLabel,remove,noteLabel);$('menu-rows').append(row);
  }
  function dayChecks(container,values=[],prefix='영업'){
    for(const day of days){const label=el('label'),input=el('input');input.type='checkbox';input.value=day;input.checked=values.includes(day);input.setAttribute('aria-label',`${prefix} ${day}요일`);label.append(input,document.createTextNode(day));container.append(label);}
  }
  function addHours(value={}){
    const row=el('div',undefined,'hour-row'),head=el('div',undefined,'hour-heading'),week=el('div',undefined,'days');dayChecks(week,value.days||[]);week.dataset.field='days';
    const remove=el('button','삭제','remove');remove.type='button';remove.setAttribute('aria-label','영업시간 구간 삭제');remove.addEventListener('click',()=>{row.remove();setDirty();});head.append(week,remove);row.append(head);
    const times=el('div',undefined,'time-grid');
    for(const [key,label] of [['open','개점'],['close','마감'],['lastOrder','마지막 주문 · 선택']]){
      const [wrap,input]=textInput(label,value[key],key==='close'?'text':'time');input.dataset.field=key;
      if(key==='close'){input.placeholder='예: 21:00 / 24:00';input.pattern='(?:(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00)';input.inputMode='text';}
      times.append(wrap);
    }
    row.append(times);const pause=el('details'),title=el('summary','브레이크 타임 · 확인한 경우만');pause.append(title);const grid=el('div',undefined,'break-grid');
    for(const [key,label] of [['breakStart','브레이크 시작'],['breakEnd','브레이크 종료']]){const [wrap,input]=textInput(label,value[key],'time');input.dataset.field=key;grid.append(wrap);}
    pause.append(grid);if(value.breakStart||value.breakEnd)pause.open=true;row.append(pause);$('hour-rows').append(row);
  }
  function visibleRows(){
    const query=$('search').value.trim().toLowerCase(),district=$('district').value,filter=$('filter').value;
    return rows.filter(p=>{
      if(query&&!p.name.toLowerCase().includes(query)||district&&p.district!==district)return false;
      const keys=new Set(p.missing.map(m=>m.key));
      if(filter==='hours-only')return keys.has('hours')&&!keys.has('menus');
      if(filter==='menus-only')return keys.has('menus')&&!keys.has('hours');
      if(filter==='both')return keys.has('menus')&&keys.has('hours');
      if(filter==='saved')return p.revision>0;
      if(filter==='empty')return !p.revision;
      return !filter||keys.has(filter);
    });
  }
  function renderList(){
    const visible=visibleRows();
    $('list-count').textContent=`${visible.length}곳`;$('place-list').replaceChildren();
    for(const p of visible){const button=el('button',undefined,'place-option');button.type='button';button.setAttribute('aria-current',String(selected===p.id));button.append(el('strong',p.name),el('span',`${p.district} · ${p.missing.map(m=>m.label).join(' / ')}`,'meta'),el('span',stateLabel(p.entryStatus),'meta'+(p.revision?' saved':'')));button.addEventListener('click',()=>openPlace(p.id));$('place-list').append(button);}
    if(!visible.length)$('place-list').append(el('p','조건에 맞는 식당이 없습니다.','muted'));
  }
  function overview(){
    $('overview-body').replaceChildren();
    const visible=visibleRows();$('overview-title').textContent=`식당별 미확인 항목 · ${visible.length}곳`;
    for(const p of visible){const tr=el('tr'),name=el('td'),button=el('button',p.name);button.type='button';button.addEventListener('click',()=>{$('overview').close();openPlace(p.id);});name.append(button);
      const missing=el('td');for(const m of p.missing){const line=el('div',m.label);line.append(el('span',m.detail,'table-note'));missing.append(line);}
      tr.append(name,el('td',p.district),missing,el('td',stateLabel(p.entryStatus)));$('overview-body').append(tr);}
  }
  function editableSections(){
    for(const key of ['menus','hours']){
      const section=$(key+'-section');section.hidden=!needed.has(key);
      section.querySelectorAll('input,select,textarea,button').forEach(c=>c.disabled=section.hidden||saving);
    }
  }
  async function refreshQueue(){const data=await api('v1/meal/survey');rows=data.places;for(const key of ['total','menus','hours','saved'])$('count-'+key).textContent=data.counts[key]+'곳';renderList();overview();}
  function reference(p){
    const box=$('reference-body');box.replaceChildren();const m=p.menuObservation,h=p.hoursObservation;
    if(m){box.append(el('h3','메뉴 참고 · '+({in_store:'매장 게시값',delivery:'배달 가격 · 매장가로 사용하지 않음',unknown:'가격 구분 미확인'}[m.priceContext]||'미확인')));const list=el('div',undefined,'reference-list');for(const item of m.menus||[])list.append(el('p',`${item.name} · ${Number.isInteger(item.price)?item.price.toLocaleString()+'원':item.priceText||'가격 미확인'}${item.main?'':' · 식사 기준 미확인/보조 메뉴'}`));if(!m.menus?.length)list.append(el('p','확인된 메뉴가 없습니다.'));box.append(list);if(m.sourceUrl){const a=el('a','메뉴 원문 ↗');a.href=m.sourceUrl;a.target='_blank';a.rel='noreferrer';box.append(a);}}
    box.append(el('h3','영업시간 · '+(h?.status==='confirmed'?'게시값 확인':'요일별 시간 미확인')));
    for(const hour of p.effectiveHours||[])box.append(el('p',`${hour.days.join('·')} ${hour.open}–${hour.close}${hour.break?' / 휴게 '+hour.break.start+'–'+hour.break.end:''}${hour.lastOrder?' / 마지막 주문 '+hour.lastOrder:''}`));
    if(h?.monthlyClosed?.length)for(const rule of h.monthlyClosed)box.append(el('p',`매월 ${rule.weeks.join('·')}번째 ${rule.day}요일 휴무`));
    if(!p.effectiveHours?.length)box.append(el('p','월~일의 개점·마감·휴무를 추가로 확인해야 합니다.'));
    for(const reason of p.reasons||[])box.append(el('p',reason,'help'));
  }
  async function openPlace(pid){
    if(saving)return;
    if(dirty&&!window.confirm('저장하지 않은 입력이 있습니다. 저장 없이 다른 식당으로 이동할까요?'))return;
    const request=++loading;error('page-error','');
    try{
      const data=await api('v1/meal/survey/'+encodeURIComponent(pid));if(request!==loading)return;
      selected=pid;revision=data.draft?.revision||0;const p=data.place,e=data.draft?.entry||{};
      savedEntry=e;needed=new Set(p.missing.map(m=>m.key));
      $('empty').hidden=true;$('editing').hidden=false;$('place-name').textContent=p.name;$('place-district').textContent=p.district+' 식당가';$('place-address').textContent=p.address;$('map-link').href=p.mapUrl;
      $('missing-info').replaceChildren();for(const m of p.missing){const item=el('div');item.append(el('strong',m.label+' 확인 필요'),el('span',m.detail));$('missing-info').append(item);}
      reference(p);$('reference').open=false;$('checked-at').value=e.checkedAt||new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(new Date());$('checked-at').max=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(new Date());
      $('method').value=e.method||'unknown';$('price-context').value=e.priceContext||'unknown';$('sources').value=(e.sources||[]).join('\n');$('notes').value=e.notes||'';$('hours-note').value=e.hoursNote||'';
      $('menus-confirmed').checked=e.menusConfirmed||false;$('hours-confirmed').checked=e.hoursConfirmed||false;
      $('menu-rows').replaceChildren();(e.menus?.length?e.menus:[{}]).forEach(addMenu);$('hour-rows').replaceChildren();(e.hours?.length?e.hours:[{}]).forEach(addHours);$('closed-days').replaceChildren();dayChecks($('closed-days'),e.closedDays||[],'휴무');
      editableSections();
      $('input-scope').textContent=needed.size===1?(needed.has('hours')?'메뉴·가격은 확인되어 있습니다. 영업시간만 입력해 주세요.':'영업시간은 확인되어 있습니다. 메뉴·가격만 입력해 주세요.'):'메뉴·가격과 영업시간을 입력해 주세요. 일부만 작성해도 저장할 수 있습니다.';
      $('save-status').textContent=data.draft?`저장됨 · ${new Date(data.draft.savedAt).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})}`:'아직 저장하지 않았습니다.';dirty=false;error('form-error','');renderList();$('place-name').focus({preventScroll:true});
      history.replaceState(null,'','survey?place='+encodeURIComponent(pid));
    }catch(e){error('page-error',e.message);}
  }
  function body(){
    // 표시되지 않는 항목은 기존 초안을 그대로 보존한다.
    const menus=needed.has('menus')?[]:(savedEntry.menus||[]);
    if(needed.has('menus'))for(const row of $('menu-rows').children){const name=row.querySelector('[data-field=name]').value.trim(),price=row.querySelector('[data-field=price]').value,note=row.querySelector('[data-field=note]').value.trim(),main=row.querySelector('[data-field=main]').checked;if(!name&&!price&&!note&&!main)continue;if(!name)throw new Error('메뉴명을 입력하세요.');menus.push({name,price:price===''?null:Number(price),main,note});}
    const hours=needed.has('hours')?[]:(savedEntry.hours||[]);
    if(needed.has('hours'))for(const row of $('hour-rows').children){const value=key=>row.querySelector('[data-field='+key+']').value;const week=[...row.querySelectorAll('[data-field=days] input:checked')].map(x=>x.value);if(!week.length&&!['open','close','lastOrder','breakStart','breakEnd'].some(k=>value(k)))continue;if(!week.length||!value('open')||!value('close'))throw new Error('영업 요일과 개점·마감 시간을 함께 입력하세요.');hours.push({days:week,open:value('open'),close:value('close'),lastOrder:value('lastOrder')||null,breakStart:value('breakStart')||null,breakEnd:value('breakEnd')||null});}
    return {expectedRevision:revision,checkedAt:$('checked-at').value||null,method:$('method').value,
      priceContext:needed.has('menus')?$('price-context').value:savedEntry.priceContext||'unknown',
      menus,menusConfirmed:needed.has('menus')?$('menus-confirmed').checked:savedEntry.menusConfirmed||false,
      hours,hoursConfirmed:needed.has('hours')?$('hours-confirmed').checked:savedEntry.hoursConfirmed||false,
      closedDays:needed.has('hours')?[...document.querySelectorAll('#closed-days input:checked')].map(x=>x.value):savedEntry.closedDays||[],
      hoursNote:needed.has('hours')?$('hours-note').value.trim():savedEntry.hoursNote||'',
      sources:$('sources').value.split('\n').map(s=>s.trim()).filter(Boolean),notes:$('notes').value.trim()};
  }
  $('survey-form').addEventListener('input',setDirty);$('survey-form').addEventListener('change',setDirty);
  $('add-menu').addEventListener('click',()=>{addMenu();setDirty();});$('add-hours').addEventListener('click',()=>{addHours();setDirty();});
  $('survey-form').addEventListener('submit',async event=>{
    event.preventDefault();if(!selected||saving||!$('survey-form').reportValidity())return;error('form-error','');
    let payload;try{payload=body();}catch(e){error('form-error',e.message);return;}
    saving=true;const controls=[...$('survey-form').querySelectorAll('input,select,textarea,button')];controls.forEach(c=>c.disabled=true);
    try{const result=await api('v1/meal/survey/'+encodeURIComponent(selected),payload);revision=result.revision;savedEntry=result.entry;dirty=false;$('save-status').textContent=`저장됨 · ${new Date(result.savedAt).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})}`;try{await refreshQueue();}catch(e){error('page-error','입력은 저장됐습니다. 목록 갱신에 실패해 새로고침이 필요합니다.');}}
    catch(e){error('form-error',e.message);}finally{saving=false;controls.forEach(c=>c.disabled=false);editableSections();}
  });
  for(const id of ['search','district','filter'])$(id).addEventListener(id==='search'?'input':'change',renderList);
  $('overview-open').addEventListener('click',()=>{overview();$('overview').showModal();});$('overview-close').addEventListener('click',()=>$('overview').close());
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  refreshQueue().then(()=>{const pid=new URLSearchParams(location.search).get('place');if(pid&&rows.some(p=>p.id===pid))openPlace(pid);}).catch(e=>error('page-error',e.message));
})();
