/* 시간 선택창 — 브라우저 기본 시간 선택 대신 [오전/오후 · 시 · 분] 세 열을 위아래로 스크롤해서 고른다.
 *  - #meal-form 안의 input[type=time] 에 자동으로 붙는다 (값 형식은 그대로 'HH:MM')
 *  - 고를 때마다 input 이벤트, 닫을 때 값이 바뀌었으면 change 이벤트 (출발시간 change → 시간표 다시 읽기)
 *  - 칸을 누르면 열리고, 바깥 클릭 · Esc · 완료로 닫힌다. 칸에 숫자를 직접 입력하는 것도 그대로 된다
 */
(() => {
  const pad = n => String(n).padStart(2, '0');

  function parse(value) {
    const m = /^(\d{2}):(\d{2})$/.exec(value || '');
    if (!m) return null;
    return { h: Number(m[1]), m: Number(m[2]) };
  }

  function attach(input) {
    const wrap = document.createElement('div');
    wrap.className = 'tp';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    input.setAttribute('aria-haspopup', 'dialog');
    input.setAttribute('aria-expanded', 'false');

    const panel = document.createElement('div');
    panel.className = 'tp-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', '시간 선택');
    panel.hidden = true;

    const cols = document.createElement('div');
    cols.className = 'tp-cols';
    const makeCol = (label, values, fmt) => {
      const ul = document.createElement('ul');
      ul.className = 'tp-col';
      ul.setAttribute('role', 'listbox');
      ul.setAttribute('aria-label', label);
      values.forEach(v => {
        const li = document.createElement('li');
        li.className = 'tp-item';
        li.setAttribute('role', 'option');
        li.dataset.value = v;
        li.textContent = fmt(v);
        ul.appendChild(li);
      });
      cols.appendChild(ul);
      return ul;
    };
    const colAp = makeCol('오전 오후', ['am', 'pm'], v => (v === 'am' ? '오전' : '오후'));
    const colH = makeCol('시', [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], v => `${v}시`);
    const colM = makeCol('분', Array.from({ length: 60 }, (_, i) => i), v => `${pad(v)}분`);
    panel.appendChild(cols);

    const foot = document.createElement('div');
    foot.className = 'tp-foot';
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'tp-btn tp-clear';
    clear.textContent = '지우기';
    const done = document.createElement('button');
    done.type = 'button';
    done.className = 'tp-btn tp-done';
    done.textContent = '완료';
    foot.append(clear, done);
    panel.appendChild(foot);
    wrap.appendChild(panel);

    let before = '';

    function current() {
      const t = parse(input.value);
      if (t) return t;
      // 빈 칸이면: 다른 칸(다음 수업 시작)은 출발시간의 오전/오후·정각부터, 출발시간 자체는 지금 시각부터
      const at = input.id !== 'at' ? parse(document.getElementById('at')?.value) : null;
      if (at) return { h: at.h, m: 0 };
      const now = new Date();
      return { h: now.getHours(), m: input.id === 'at' ? now.getMinutes() : 0 };
    }

    function mark(scroll) {
      const t = parse(input.value);
      const ap = t ? (t.h < 12 ? 'am' : 'pm') : null;
      const h12 = t ? (t.h % 12 || 12) : null;
      const set = (ul, val) => {
        ul.querySelectorAll('.tp-item').forEach(li => {
          const on = val !== null && li.dataset.value === String(val);
          li.classList.toggle('selected', on);
          li.setAttribute('aria-selected', on ? 'true' : 'false');
          if (on && scroll) ul.scrollTop = li.offsetTop - (ul.clientHeight - li.offsetHeight) / 2;
        });
      };
      set(colAp, ap);
      set(colH, h12);
      set(colM, t ? t.m : null);
    }

    function update(part, raw) {
      const t = current();
      let { h, m } = t;
      const pm = h >= 12;
      if (part === 'ap') h = (h % 12) + (raw === 'pm' ? 12 : 0);
      if (part === 'h') h = (Number(raw) % 12) + (pm ? 12 : 0);
      if (part === 'm') m = Number(raw);
      input.value = `${pad(h)}:${pad(m)}`;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      mark(false);
    }

    function open() {
      if (!panel.hidden || input.disabled) return;
      before = input.value;
      clear.hidden = input.required;
      panel.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      mark(true);
    }

    function close() {
      if (panel.hidden) return;
      panel.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      if (input.value !== before) input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    input.addEventListener('click', e => { e.preventDefault(); open(); });
    input.addEventListener('keydown', e => {
      if (e.key === 'Escape' && !panel.hidden) { e.preventDefault(); close(); }
      if ((e.key === 'Enter' || e.key === ' ') && panel.hidden) { e.preventDefault(); open(); }
    });
    input.addEventListener('input', () => { if (!panel.hidden) mark(false); });
    panel.addEventListener('mousedown', e => e.preventDefault()); // 칸 포커스 유지
    cols.addEventListener('click', e => {
      const li = e.target.closest('.tp-item');
      if (!li) return;
      const part = li.parentElement === colAp ? 'ap' : li.parentElement === colH ? 'h' : 'm';
      update(part, li.dataset.value);
    });
    clear.addEventListener('click', () => {
      input.value = '';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      mark(false);
      close();
    });
    done.addEventListener('click', close);
    document.addEventListener('mousedown', e => { if (!wrap.contains(e.target)) close(); });
    input.addEventListener('blur', () => setTimeout(() => { if (!wrap.contains(document.activeElement)) close(); }, 0));
  }

  function init() {
    document.querySelectorAll('#meal-form input[type=time]').forEach(attach);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
