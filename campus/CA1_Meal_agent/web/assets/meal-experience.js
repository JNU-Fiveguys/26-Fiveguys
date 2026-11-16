/* PR #6 interaction ideas on the existing CA1 API and public data. */
(() => {
  'use strict';
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  function create({onSlot, isBusy = () => false}) {
    const $ = id => document.getElementById(id);
    let generation = 0, active = 0, candidates = [], pointer = null, map = null, markers = [];
    const minute = value => {const [h, m] = value.split(':').map(Number); return h * 60 + m;};
    function position(node, start, end) {
      const left = Math.max(540, minute(start)), right = Math.min(1200, minute(end));
      if (right <= left) return false;
      node.style.left = `${(left - 540) / 660 * 100}%`;
      node.style.width = `${(right - left) / 660 * 100}%`;
      return true;
    }
    async function schedule(event) {
      const token = ++generation, {date, automatic} = event.detail;
      $('meal-timeline').hidden = !automatic;
      $('timeline-track').replaceChildren(); $('timeline-slots').replaceChildren();
      if (!automatic) return;
      $('timeline-status').textContent = '시간표를 확인하고 있습니다…';
      try {
        const response = await fetch('v1/meal/timeline?' + new URLSearchParams({date}), {cache: 'no-store'});
        const data = await response.json();
        if (token !== generation) return;
        if (!response.ok) throw Error();
        $('timeline-status').textContent = data.status !== 'ready'
          ? '시간표를 확인할 수 없습니다. 아래에서 장소와 시간을 직접 입력할 수 있어요.'
          : data.slots.length ? `${date} · 공강을 누르면 해당 시간으로 추천합니다.` : `${date} · 표시할 30분 이상 공강이 없습니다.`;
        for (const item of data.classes) {
          const label = {class: '수업', exam: '시험', busy: '일정'}[item.kind] || '일정';
          const block = el('div', `${label} ${item.start}`, 'timeline-class');
          block.title = `${label} ${item.start}~${item.end} · ${item.building}`;
          if (position(block, item.start, item.end)) $('timeline-track').append(block);
        }
        for (const slot of data.slots) {
          const label = slot.lastOfDay ? `${slot.start} 이후 · 다음 수업 없음` : `${slot.start}~${slot.end} · ${minute(slot.end) - minute(slot.start)}분`;
          const button = el('button', label, 'timeline-slot');
          button.type = 'button'; button.disabled = !slot.enabled;
          button.title = slot.enabled ? `${slot.origin} → ${slot.destination || '다음 수업 없음'}` : slot.reason;
          button.addEventListener('click', () => {if (!isBusy()) onSlot(slot);});
          $('timeline-slots').append(button);
          const gap = el('button', '공강', 'timeline-gap');
          gap.type = 'button'; gap.disabled = !slot.enabled; gap.title = button.title;
          gap.setAttribute('aria-label', label);
          gap.addEventListener('click', () => {if (!isBusy()) onSlot(slot);});
          if (position(gap, slot.start, slot.end)) $('timeline-track').append(gap);
        }
      } catch (_) {
        if (token === generation) $('timeline-status').textContent = '시간표를 읽지 못했습니다. 다시 확인하거나 직접 입력해 주세요.';
      }
    }
    document.addEventListener('meal:datechange', schedule);

    function select(index) {
      if (!candidates.length) return;
      active = Math.max(0, Math.min(candidates.length - 1, index));
      [...$('cards').children].forEach((card, i) => {card.hidden = i !== active;});
      $('deck-count').textContent = `${active + 1} / ${candidates.length}`;
      $('deck-prev').disabled = active === 0;
      $('deck-next').disabled = active === candidates.length - 1;
      markers.forEach((marker, i) => {
        marker.getElement()?.classList.toggle('selected', i === active);
        marker.setZIndexOffset(i === active ? 1000 : 0);
      });
    }
    $('deck-prev').addEventListener('click', () => select(active - 1));
    $('deck-next').addEventListener('click', () => select(active + 1));
    $('cards').addEventListener('keydown', event => {
      if (event.target.closest('button,a,input,select')) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault(); select(active + (event.key === 'ArrowLeft' ? -1 : 1));
      }
    });
    $('cards').addEventListener('pointerdown', event => {
      pointer = event.target.closest('button,a,input,select') ? null : {x: event.clientX, y: event.clientY};
    });
    $('cards').addEventListener('pointerup', event => {
      if (!pointer) return;
      const dx = event.clientX - pointer.x, dy = event.clientY - pointer.y;
      pointer = null;
      if (Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy) * 1.5) select(active + (dx < 0 ? 1 : -1));
    });
    $('cards').addEventListener('pointercancel', () => {pointer = null;});

    function reset() {
      if (map) map.remove();
      map = null; markers = [];
      candidates = []; pointer = null;
      $('deck-controls').hidden = true;
      $('recommendation-map-panel').hidden = true;
      delete $('recommendation-map-panel').dataset.state;
    }
    function render(items, context) {
      reset(); candidates = items;
      $('deck-controls').hidden = !items.length;
      select(0);
      if (!items.length) return;
      $('recommendation-map-panel').hidden = false;
      $('recommendation-map-panel').dataset.state = 'loading';
      const status = $('recommendation-map-status');
      status.textContent = '지도의 별을 누르면 해당 식당 카드로 이동합니다.';
      if (!window.L) {status.textContent = '지도를 읽지 못했습니다. 카드에서 식당을 선택할 수 있어요.'; return;}
      const L = window.L;
      const instance = L.map('recommendation-map', {scrollWheelZoom: false, maxZoom: 19, preferCanvas: true});
      map = instance;
      instance.attributionControl.setPrefix(false);
      instance.attributionControl.addAttribution('<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a>');
      const points = [];
      items.forEach((candidate, index) => {
        const point = candidate.walkRoutes?.to?.path?.at(-1) || candidate.walkRoutes?.back?.path?.[0];
        if (!point) return;
        points.push(point);
        const icon = L.divIcon({className: 'meal-star', html: '★', iconSize: [36, 36], iconAnchor: [18, 18]});
        const marker = L.marker(point, {icon, title: candidate.name, alt: candidate.name, keyboard: true})
          .addTo(instance).bindTooltip(el('span', candidate.name));
        marker.on('click', () => select(index)); markers[index] = marker;
      });
      for (const [point, label] of [[context.origin, '출발'], [context.destination, '다음 수업']]) {
        if (!point) continue;
        const latlng = [point.lat, point.lng]; points.push(latlng);
        L.circleMarker(latlng, {radius: 6, color: '#33644d', fillOpacity: 1})
          .addTo(instance).bindTooltip(el('span', `${label} · ${point.name}`));
      }
      if (points.length) instance.fitBounds(points, {padding: [35, 35], maxZoom: 17});
      else instance.setView([35.1765, 126.9055], 15);
      window.MealBasemap.draw(instance, () => map === instance).then(() => {
        if (map === instance) $('recommendation-map-panel').dataset.state = 'map';
      }).catch(() => {
        if (map === instance) {status.textContent = '지도 배경을 읽지 못했습니다. 식당 위치는 별로 표시합니다.'; $('recommendation-map-panel').dataset.state = 'fallback';}
      });
      select(0);
      requestAnimationFrame(() => {if (map === instance) instance.invalidateSize();});
    }
    if ('ResizeObserver' in window) new ResizeObserver(() => {if (map) map.invalidateSize({pan: false});}).observe($('recommendation-map'));
    return {render, reset};
  }
  window.MealExperience = {create};
})();
