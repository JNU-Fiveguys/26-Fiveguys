/* 선택칸 목록 — 브라우저 기본 <select> 목록 대신 건물 목록(building-picker.js)과 같은 모양으로 띄운다.
 *  - #meal-form 안의 <select> 에 붙는다. 실제 값은 원래 select 가 그대로 가진다(폼·기존 코드 호환)
 *  - 고르면 select 의 change 이벤트를 보낸다. 코드가 select.value 를 바꿔도 표시가 따라간다
 *  - ↑↓ 이동 · Enter/Space 선택 · Esc 닫기, 비활성 옵션은 건너뛴다
 */
(() => {
  function attach(select) {
    const wrap = document.createElement('div');
    wrap.className = 'picker select-picker';
    select.parentNode.insertBefore(wrap, select);

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'select-display';
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    const label = select.id && document.querySelector(`label[for="${select.id}"]`);
    if (label) {
      if (!label.id) label.id = `${select.id}-label`;
      label.htmlFor = `${select.id}-display`;
      button.setAttribute('aria-labelledby', `${label.id} ${select.id}-display`);
    }
    button.id = `${select.id}-display`;

    const list = document.createElement('ul');
    list.className = 'picker-list';
    list.id = `${select.id}-options`;
    list.setAttribute('role', 'listbox');
    list.hidden = true;
    button.setAttribute('aria-controls', list.id);

    select.classList.add('select-native');
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');
    wrap.append(button, list, select);

    let active = -1;
    const opts = () => [...select.options];

    function sync() {
      const o = select.options[select.selectedIndex];
      button.textContent = o ? o.text : '';
      button.disabled = select.disabled;
    }

    // 코드가 select.value / selectedIndex 를 바꿀 때도 표시를 맞춘다
    for (const prop of ['value', 'selectedIndex']) {
      const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, prop);
      Object.defineProperty(select, prop, {
        configurable: true,
        get() { return desc.get.call(this); },
        set(v) { desc.set.call(this, v); sync(); },
      });
    }
    select.addEventListener('change', sync);

    function render() {
      list.replaceChildren();
      opts().forEach((o, i) => {
        if (o.hidden) return;
        const li = document.createElement('li');
        li.id = `${list.id}-${i}`;
        li.className = 'picker-option';
        li.setAttribute('role', 'option');
        li.dataset.index = i;
        if (o.disabled) li.setAttribute('aria-disabled', 'true');
        if (i === select.selectedIndex) li.setAttribute('aria-selected', 'true');
        const text = document.createElement('strong');
        text.textContent = o.text;
        li.appendChild(text);
        list.appendChild(li);
      });
      setActive(select.selectedIndex);
    }

    function setActive(i) {
      active = i;
      list.querySelectorAll('.picker-option').forEach(li => li.classList.toggle('active', Number(li.dataset.index) === i));
      const li = list.querySelector(`.picker-option[data-index="${i}"]`);
      if (li) button.setAttribute('aria-activedescendant', li.id);
      else button.removeAttribute('aria-activedescendant');
    }

    function open() {
      if (select.disabled) return;
      render();
      list.hidden = false;
      button.setAttribute('aria-expanded', 'true');
    }

    function close() {
      list.hidden = true;
      button.setAttribute('aria-expanded', 'false');
      button.removeAttribute('aria-activedescendant');
    }

    function choose(i) {
      const o = opts()[i];
      if (!o || o.disabled || o.hidden) return;
      const changed = select.selectedIndex !== i;
      select.selectedIndex = i;
      close();
      if (changed) select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function move(step) {
      const all = opts();
      let i = active;
      for (let n = 0; n < all.length; n++) {
        i = (i + step + all.length) % all.length;
        if (!all[i].disabled && !all[i].hidden) { setActive(i); return; }
      }
    }

    button.addEventListener('click', () => (list.hidden ? open() : close()));
    button.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (list.hidden) open(); else move(e.key === 'ArrowDown' ? 1 : -1);
      } else if ((e.key === 'Enter' || e.key === ' ') && !list.hidden) {
        e.preventDefault();
        choose(active);
      } else if (e.key === 'Escape' && !list.hidden) {
        e.preventDefault();
        close();
      } else if (e.key === 'Tab') {
        close();
      }
    });
    list.addEventListener('mousedown', e => e.preventDefault());
    list.addEventListener('click', e => {
      const li = e.target.closest('.picker-option');
      if (li) choose(Number(li.dataset.index));
    });
    list.addEventListener('mousemove', e => {
      const li = e.target.closest('.picker-option');
      if (li && li.getAttribute('aria-disabled') !== 'true') setActive(Number(li.dataset.index));
    });
    button.addEventListener('blur', () => setTimeout(close, 0));
    sync();
  }

  function init() {
    document.querySelectorAll('#meal-form select').forEach(attach);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
