/* Date/time, automatic origin preview and budget controls. No timetable is stored here. */
window.MealForm = {
  create({isBusy = () => false} = {}) {
    const $ = id => document.getElementById(id);
    const kstNow = () => new Date(Date.now() + 9 * 3600000).toISOString().slice(0, 16);
    const today = () => kstNow().slice(0, 10);
    const selectedDate = () => $('meal-date').value;
    const selectedAt = () => `${selectedDate()}T${$('at').value}:00+09:00`;
    const publicDemo = 'publicDemo' in document.documentElement.dataset;  // 공개 링크: 개인 시간표 없음
    let useAcademic = !publicDemo, requestToken = 0, loading = false, preview = null;
    let calendarMonth, calendarYear;

    function setNow() {
      const now = kstNow();
      $('meal-date').value = now.slice(0, 10);
      $('at').value = now.slice(11, 16);
      updateDateLabel();
    }
    function updateDateLabel() {
      const changed = selectedDate() !== today();
      const label = `출발 날짜 선택 · ${selectedDate()}`;
      $('calendar-toggle').title = label;
      $('calendar-toggle').setAttribute('aria-label', label);
      $('calendar-toggle').dataset.changed = String(changed);
      $('date-selection-note').hidden = !changed;
      $('date-selection-note').textContent = changed ? '오늘이 아닌 날짜를 선택했어요. 달력에서 확인할 수 있습니다.' : '';
    }
    function closeCalendar(restoreFocus = false) {
      $('meal-calendar').hidden = true;
      $('calendar-toggle').setAttribute('aria-expanded', 'false');
      if (restoreFocus) $('calendar-toggle').focus();
    }
    function chooseDate(value) {
      $('meal-date').value = value;
      updateDateLabel();
      closeCalendar(true);
      refresh();
    }
    const isoDate = (year, month, day) => `${String(year).padStart(4, '0')}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    function renderCalendar(focusDay) {
      $('calendar-month').textContent = `${calendarYear}년 ${calendarMonth + 1}월`;
      $('calendar-days').replaceChildren();
      const first = new Date(calendarYear, calendarMonth, 1).getDay();
      const count = new Date(calendarYear, calendarMonth + 1, 0).getDate();
      for (let i = 0; i < first; i++) $('calendar-days').append(document.createElement('span'));
      for (let day = 1; day <= count; day++) {
        const value = isoDate(calendarYear, calendarMonth, day);
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = day;
        button.dataset.date = value;
        button.setAttribute('aria-label', `${calendarYear}년 ${calendarMonth + 1}월 ${day}일`);
        button.setAttribute('aria-pressed', String(value === selectedDate()));
        if (value === today()) button.setAttribute('aria-current', 'date');
        button.addEventListener('click', () => chooseDate(value));
        button.addEventListener('keydown', event => {
          const offsets = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7};
          if (!(event.key in offsets)) return;
          event.preventDefault();
          const target = new Date(calendarYear, calendarMonth, day + offsets[event.key]);
          calendarYear = target.getFullYear(); calendarMonth = target.getMonth();
          renderCalendar(target.getDate());
        });
        $('calendar-days').append(button);
      }
      if (focusDay) $('calendar-days').querySelector(`[data-date="${isoDate(calendarYear, calendarMonth, focusDay)}"]`)?.focus();
    }
    $('calendar-toggle').addEventListener('click', () => {
      if (!$('meal-calendar').hidden) return closeCalendar();
      const [year, month, day] = selectedDate().split('-').map(Number);
      calendarYear = year; calendarMonth = month - 1;
      $('meal-calendar').hidden = false;
      $('calendar-toggle').setAttribute('aria-expanded', 'true');
      renderCalendar(day);
    });
    for (const [id, offset] of [['calendar-prev', -1], ['calendar-next', 1]]) {
      $(id).addEventListener('click', () => {
        const date = new Date(calendarYear, calendarMonth + offset, 1);
        calendarYear = date.getFullYear(); calendarMonth = date.getMonth();
        renderCalendar();
      });
    }
    $('calendar-today').addEventListener('click', () => chooseDate(today()));
    $('calendar-close').addEventListener('click', () => closeCalendar(true));
    document.addEventListener('click', event => {
      if (!event.target.closest('.time-field')) closeCalendar();
    });
    $('meal-calendar').addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); closeCalendar(true); }
      if (event.key === 'Tab') {
        const buttons = [...$('meal-calendar').querySelectorAll('button:not(:disabled)')];
        if (event.shiftKey && document.activeElement === buttons[0]) {event.preventDefault(); buttons.at(-1).focus();}
        else if (!event.shiftKey && document.activeElement === buttons.at(-1)) {event.preventDefault(); buttons[0].focus();}
      }
    });

    function originHelp(hint = '') {
      $('origin').placeholder = preview ? preview.name : '출발 건물을 선택하세요';
      $('origin-help').textContent = $('origin').value.trim() ? '입력한 장소에서 출발합니다.' : (preview ? (hint || '직전 수업 기준 · 다른 곳에서 출발하면 변경하세요.') : hint);
    }
    let currentHint = '';
    function showSummary(summary) {
      if (Object.hasOwn(summary, 'originPreview')) {
        preview = summary.originPreview;
        currentHint = summary.originHint || '';
        originHelp(currentHint);
      }
      const state = summary.status || 'unavailable';
      $('meal-form').dataset.timetableStatus = state;
      const labels = {
        incomplete: '시간표에 빠진 정보가 있습니다. 유니버스 앱에서 확인해 주세요.',
        no_timetable: '등록된 시간표가 없습니다. 출발지와 다음 수업을 직접 입력할 수 있어요.',
        missing: '저장된 시간표를 찾지 못했습니다.',
        outside_semester: '선택한 날짜가 등록된 학기 범위 밖입니다.',
        unavailable: '시간표를 불러오지 못했습니다. 다시 확인하거나 직접 입력해 주세요.',
        manual: '출발지와 다음 수업을 직접 입력하고 있어요.'
      };
      $('academic-notice').hidden = state === 'ready' || publicDemo;
      $('academic-message').textContent = labels[state] || labels.unavailable;
      $('academic-retry').hidden = !useAcademic;
      $('academic-manual').textContent = useAcademic ? '직접 입력하기' : '시간표로 돌아가기';
    }
    async function refresh() {
      const token = ++requestToken;
      document.dispatchEvent(new CustomEvent('meal:datechange', {detail: {date: selectedDate(), automatic: useAcademic}}));
      if (!useAcademic) {loading = false; preview = null; currentHint = '건물 이름을 입력하세요.'; originHelp(currentHint); showSummary({status: 'manual'}); return;}
      if (!$('at').value) {loading = false; preview = null; currentHint = '출발시간을 선택하세요.'; originHelp(currentHint); $('academic-retry').disabled = false; return;}
      loading = true; preview = null; currentHint = '직전 수업 건물을 확인하고 있습니다…';
      originHelp(currentHint); $('origin').placeholder = '시간표 확인 중…';
      $('meal-form').dataset.timetableStatus = 'loading';
      $('academic-retry').disabled = true;
      try {
        const response = await fetch('v1/meal/timetable?' + new URLSearchParams({at: selectedAt()}), {cache: 'no-store'});
        const summary = await response.json();
        if (token !== requestToken) return;
        if (!response.ok) throw Error('시간표를 확인하지 못했습니다.');
        showSummary(summary);
      } catch (error) {
        if (token === requestToken) showSummary({status: 'unavailable', originPreview: null, originHint: '출발 건물을 직접 선택할 수 있습니다.'});
      } finally {
        if (token === requestToken) {loading = false; $('academic-retry').disabled = false;}
      }
    }
    function updateMode() {
      const automaticOption = $('next-mode').querySelector('option[value="auto"]');
      automaticOption.disabled = !useAcademic;
      automaticOption.hidden = publicDemo;
      if (!useAcademic && $('next-mode').value === 'auto') $('next-mode').value = 'manual';
      const manualNext = $('next-mode').value === 'manual';
      $('manual-fields').hidden = !manualNext;
      $('next-room').required = manualNext;
      $('origin').required = !useAcademic;
      $('next-start').required = !useAcademic && manualNext;
      $('next-start-help').textContent = useAcademic ? '비워두면 시간표의 다음 수업 시각을 사용합니다.' : '다음 수업의 시작 시각을 입력하세요.';
    }
    $('academic-manual').addEventListener('click', () => {
      useAcademic = !useAcademic;
      if (useAcademic) $('next-mode').value = 'auto';
      updateMode(); refresh();
    });
    $('academic-retry').addEventListener('click', refresh);
    $('origin').addEventListener('input', () => originHelp(currentHint));
    $('next-mode').addEventListener('change', updateMode);
    $('at').addEventListener('change', refresh);
    $('time-now').addEventListener('click', () => {setNow(); closeCalendar(); refresh();});
    window.addEventListener('focus', () => {if (useAcademic && !isBusy() && !loading) refresh();});

    function updateBudget() {
      const min = $('budget-min').value, max = $('budget-max').value;
      const errors = {};
      for (const [id, label] of [['budget-min', '최소 금액'], ['budget-max', '최대 금액']]) {
        const input = $(id), value = input.value;
        input.setCustomValidity('');
        errors[id] = input.validity.badInput || (value !== '' && (!Number.isSafeInteger(Number(value)) || Number(value) < 0))
          ? `${label}은 0 이상의 정수로 입력해 주세요.` : '';
      }
      if (!errors['budget-min'] && !errors['budget-max'] && min !== '' && max !== '' && Number(min) > Number(max)) {
        errors['budget-max'] = '최대 금액은 최소 금액 이상으로 입력해 주세요.';
      }
      for (const id of ['budget-min', 'budget-max']) {
        $(id).setCustomValidity(errors[id]);
        $(id).setAttribute('aria-invalid', String(Boolean(errors[id])));
      }
      const error = errors['budget-min'] || errors['budget-max'];
      $('budget-error').textContent = error;
      $('budget-error').hidden = !error;
      document.querySelectorAll('[data-budget]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.budget === `${min},${max}`)));
    }
    document.querySelectorAll('[data-budget]').forEach(button => button.addEventListener('click', () => {
      const [min, max] = button.dataset.budget.split(',');
      $('budget-min').value = min; $('budget-max').value = max; updateBudget();
    }));
    for (const id of ['budget-min', 'budget-max']) {
      $(id).addEventListener('input', updateBudget);
      $(id).addEventListener('change', updateBudget);
    }
    setNow(); updateMode(); updateBudget(); refresh();
    return {
      selectedAt, selectedDate, refresh,
      chooseSlot: async slot => {
        if (isBusy() || !slot.enabled) return;
        useAcademic = true;
        $('at').value = slot.start; $('origin').value = '';
        $('next-room').value = ''; $('next-start').value = ''; $('next-mode').value = 'auto';
        $('next-mode').dispatchEvent(new Event('change'));
        const requestedAt = selectedAt();
        updateMode(); await refresh();
        if (!isBusy() && requestedAt === selectedAt() && useAcademic) $('meal-form').requestSubmit();
      },
      autoTimetable: () => useAcademic,
      budget: () => ({budgetMin: $('budget-min').value === '' ? null : Number($('budget-min').value), budgetMax: $('budget-max').value === '' ? null : Number($('budget-max').value)}),
      acceptSummary: summary => {if (useAcademic && summary?.date === selectedDate()) showSummary(summary);}
    };
  }
};
