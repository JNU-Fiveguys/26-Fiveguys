/* 건물 선택 목록 — 브라우저 기본 datalist 대신 위아래로 스크롤되는 목록을 띄운다.
 *  - list="buildings" 인 입력칸에 자동으로 붙는다 (옵션은 그 datalist 에서 읽는다)
 *  - 클릭·포커스로 열고, 입력하면 코드·이름으로 거른다
 *  - 마우스 휠·터치로 목록 스크롤, ↑↓ 이동 · Enter 선택 · Esc 닫기
 *  - 선택하면 input · change 이벤트를 보내 기존 코드(meal-form.js)가 그대로 반응한다
 */
(() => {
  const MAX_ITEMS = 200;

  // 목록 구분 제목 — 건물 이름 앞부분으로 정한다. 순서대로 보여 주고, 맞는 것이 없으면 '기타'
  const GROUPS = [
    ['공과대', /^공과대/],
    ['경영대', /^경영대/],
    ['인문대', /^인문대/],
    ['AI융합대', /^AI융합/i],
    ['사범대', /^사범대/],
    ['자연과학대', /^자연(과학)?대/],
    ['사회과학대', /^사회과학대/],
    ['생활과학대', /^생활과학대/],
  ];
  const OTHER = '기타';
  const groupOf = name => (GROUPS.find(([, re]) => re.test(name)) || [OTHER])[0];
  const groupRank = g => { const i = GROUPS.findIndex(([n]) => n === g); return i < 0 ? GROUPS.length : i; };

  function attach(input) {
    const listId = input.getAttribute('list');
    const source = listId && document.getElementById(listId);
    if (!source) return;
    input.removeAttribute('list'); // 기본 자동완성 끄기
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');

    const wrap = document.createElement('div');
    wrap.className = 'picker';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    const list = document.createElement('ul');
    const id = `${input.id || 'picker'}-options`;
    list.id = id;
    list.className = 'picker-list';
    list.setAttribute('role', 'listbox');
    list.hidden = true;
    input.setAttribute('aria-controls', id);
    wrap.appendChild(list);

    let items = [];
    let active = -1;

    // 화면에는 건물 이름만 보인다. 코드·별칭(공7, 공대7호관 …)은 검색에만 쓴다
    const options = () => [...source.options]
      .map((o, order) => ({ value: o.value, group: groupOf(o.value), order, search: `${o.value} ${o.dataset.search || ''}`.toLowerCase() }))
      .sort((a, b) => groupRank(a.group) - groupRank(b.group) || a.order - b.order);

    function render() {
      const q = input.value.trim().toLowerCase();
      const all = options();
      // 입력값이 목록의 값과 정확히 같으면(이미 선택됨) 전체를 보여 다른 건물로 바꾸기 쉽게
      const exact = all.some(o => o.value.toLowerCase() === q);
      items = (q && !exact ? all.filter(o => o.search.includes(q) || o.search.replace(/\s+/g, '').includes(q.replace(/\s+/g, ''))) : all).slice(0, MAX_ITEMS);
      list.replaceChildren();
      if (!items.length) {
        const li = document.createElement('li');
        li.className = 'picker-empty';
        li.textContent = all.length ? '맞는 건물이 없어요' : '건물 목록을 불러오는 중…';
        list.appendChild(li);
      }
      let lastGroup = null;
      items.forEach((o, i) => {
        if (o.group !== lastGroup) {
          lastGroup = o.group;
          const head = document.createElement('li');
          head.className = 'picker-group';
          head.setAttribute('role', 'presentation');
          head.textContent = o.group;
          list.appendChild(head);
        }
        const li = document.createElement('li');
        li.id = `${id}-${i}`;
        li.className = 'picker-option';
        li.setAttribute('role', 'option');
        li.dataset.index = i;
        const name = document.createElement('strong');
        name.textContent = o.value;
        li.appendChild(name);
        if (o.value === input.value) li.setAttribute('aria-selected', 'true');
        list.appendChild(li);
      });
      setActive(items.findIndex(o => o.value === input.value));
    }

    function setActive(i) {
      active = i;
      list.querySelectorAll('.picker-option').forEach((li, n) => li.classList.toggle('active', n === i));
      const li = i >= 0 ? list.querySelectorAll('.picker-option')[i] : null;
      if (li) {
        input.setAttribute('aria-activedescendant', li.id);
        li.scrollIntoView({ block: 'nearest' });
      } else {
        input.removeAttribute('aria-activedescendant');
      }
    }

    function open() {
      if (input.disabled || input.readOnly) return;
      render();
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    }

    function close() {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      active = -1;
    }

    function choose(i) {
      const o = items[i];
      if (!o) return;
      input.value = o.value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      close();
    }

    input.addEventListener('focus', open);
    input.addEventListener('click', () => { if (list.hidden) open(); });
    input.addEventListener('input', () => { if (!list.hidden || document.activeElement === input) { list.hidden = false; input.setAttribute('aria-expanded', 'true'); render(); } });
    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (list.hidden) { open(); return; }
        if (!items.length) return;
        const step = e.key === 'ArrowDown' ? 1 : -1;
        setActive(active < 0 ? (step > 0 ? 0 : items.length - 1) : (active + step + items.length) % items.length);
      } else if (e.key === 'Enter' && !list.hidden && active >= 0) {
        e.preventDefault();
        choose(active);
      } else if (e.key === 'Escape' && !list.hidden) {
        e.preventDefault();
        close();
      } else if (e.key === 'Tab') {
        close();
      }
    });
    // 목록을 누르는 동안 입력칸이 포커스를 잃어 닫히지 않게
    list.addEventListener('mousedown', e => e.preventDefault());
    list.addEventListener('click', e => {
      const li = e.target.closest('.picker-option');
      if (li) choose(Number(li.dataset.index));
    });
    list.addEventListener('mousemove', e => {
      const li = e.target.closest('.picker-option');
      if (li && Number(li.dataset.index) !== active) setActive(Number(li.dataset.index));
    });
    input.addEventListener('blur', () => setTimeout(close, 0));
  }

  function init() {
    document.querySelectorAll('input[list]').forEach(attach);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
