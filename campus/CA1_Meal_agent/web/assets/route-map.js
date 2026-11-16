/* Route coordinates from the meal API are [latitude, longitude], as Leaflet expects. */
(() => {
  'use strict';
  const COLORS = ['#33644d', '#3b7fb0'];
  const CREDIT_URL = 'https://www.openstreetmap.org/copyright';
  const createElement = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const distanceText = meters => Number.isFinite(meters)
    ? meters >= 1000 ? `${(meters / 1000).toFixed(2)}km` : `${Math.round(meters)}m`
    : '거리 미확인';
  const validPoint = point => Array.isArray(point) && point.length === 2
    && point.every(Number.isFinite) && Math.abs(point[0]) <= 90 && Math.abs(point[1]) <= 180;

  function create() {
    const byId = id => document.getElementById(id);
    const panel = byId('map-details');
    const viewport = byId('route-map');
    const diagram = byId('route-diagram');
    const legend = byId('route-legend');
    const status = byId('route-map-status');
    const fitButton = byId('route-fit');
    const retryButton = byId('route-retry');
    const isDialog = panel.tagName === 'DIALOG';  // 창으로 띄우는 경우: 열고 닫기는 창이 맡는다
    let map = null, bounds = null, selected = null, context = null;
    let frame = null, timeout = null, revision = 0;

    function setState(state, message) {
      panel.dataset.state = state;
      status.textContent = message;
    }

    function disposeMap() {
      clearTimeout(timeout);
      if (frame !== null) cancelAnimationFrame(frame);
      frame = timeout = null;
      const previous = map;
      map = bounds = null;
      if (previous) previous.remove();
    }

    function reset() {
      revision += 1;
      disposeMap();
      selected = context = null;
      if (!isDialog) {
        panel.hidden = true;
        panel.open = false;
      }
      delete panel.dataset.state;
      viewport.replaceChildren();
      viewport.hidden = false;
      diagram.replaceChildren();
      diagram.hidden = true;
      legend.replaceChildren();
      status.textContent = '';
      fitButton.hidden = retryButton.hidden = true;
    }

    function routesFor(candidate) {
      return [candidate.walkRoutes?.to, candidate.walkRoutes?.back]
        .map((route, index) => route && ({...route, index,
          path: Array.isArray(route.path) ? route.path.filter(validPoint) : []}))
        .filter(route => route && route.path.length);
    }

    function stopsFor(candidate, currentContext, routes) {
      const outbound = routes.find(route => route.index === 0);
      const onward = routes.find(route => route.index === 1);
      const stops = [];
      if (outbound) {
        stops.push({point: outbound.path[0], role: '출발', name: currentContext?.origin?.name || '출발 건물', kind: 'origin'});
        stops.push({point: outbound.path.at(-1), role: '식당', name: candidate.name, kind: 'meal'});
      } else if (onward) {
        stops.push({point: onward.path[0], role: '식당', name: candidate.name, kind: 'meal'});
      }
      if (onward) stops.push({point: onward.path.at(-1), role: '다음 강의실',
        name: currentContext?.destination?.name || '다음 강의실', kind: 'destination'});
      return stops;
    }

    function renderLegend(routes, currentContext, candidate) {
      legend.replaceChildren();
      for (const route of routes) {
        const row = createElement('div', undefined, `route-leg${route.index ? ' route-leg-back' : ''}${route.source !== 'osm' ? ' route-leg-estimate' : ''}`);
        const symbol = createElement('span', undefined, 'route-leg-symbol');
        symbol.setAttribute('aria-hidden', 'true');
        const text = createElement('div');
        const from = route.index ? candidate.name : currentContext?.origin?.name || '출발 건물';
        const to = route.index ? currentContext?.destination?.name || '다음 강의실' : candidate.name;
        text.append(createElement('strong', `${from} → ${to}`));
        const source = route.source === 'osm' ? 'OSM 보행길' : '직선거리 추정 · 점선';
        text.append(createElement('p', `도보 ${route.durationMin}분 · ${distanceText(route.distanceM)} · ${source}`));
        if (route.source === 'osm' && route.connectorM > 0) {
          text.append(createElement('p', `보행길까지의 연결 거리 ${distanceText(route.connectorM)} 추정 포함`));
        }
        row.append(symbol, text);
        legend.append(row);
      }
    }

    function renderDiagram(routes, stops) {
      const points = routes.flatMap(route => route.path);
      const ns = 'http://www.w3.org/2000/svg';
      const svg = document.createElementNS(ns, 'svg');
      svg.setAttribute('viewBox', '0 0 600 340');
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', `${selected.name} 이동 경로 도식. 지도 배경을 불러오지 못했습니다.`);
      const lat = (Math.min(...points.map(p => p[0])) + Math.max(...points.map(p => p[0]))) / 2;
      const longitudeScale = Math.max(.01, Math.cos(lat * Math.PI / 180));
      const xs = points.map(p => p[1] * longitudeScale), ys = points.map(p => p[0]);
      const xmin = Math.min(...xs), ymin = Math.min(...ys);
      const dx = Math.max(...xs) - xmin, dy = Math.max(...ys) - ymin;
      const scale = Math.min(470 / (dx || .001), 240 / (dy || .001));
      const project = point => [65 + (470 - dx * scale) / 2 + (point[1] * longitudeScale - xmin) * scale,
        290 - (240 - dy * scale) / 2 - (point[0] - ymin) * scale];
      for (const route of routes) {
        const polyline = document.createElementNS(ns, 'polyline');
        polyline.setAttribute('points', route.path.map(point => project(point).join(',')).join(' '));
        polyline.setAttribute('fill', 'none');
        polyline.setAttribute('stroke', COLORS[route.index]);
        polyline.setAttribute('stroke-width', '4');
        polyline.setAttribute('stroke-linecap', 'round');
        if (route.source !== 'osm') polyline.setAttribute('stroke-dasharray', '7 6');
        svg.append(polyline);
      }
      stops.forEach((stop, index) => {
        const [x, y] = project(stop.point);
        const circle = document.createElementNS(ns, 'circle');
        circle.setAttribute('cx', x); circle.setAttribute('cy', y);
        circle.setAttribute('r', '7'); circle.setAttribute('fill', '#1f2b25');
        const label = document.createElementNS(ns, 'text');
        label.setAttribute('x', x); label.setAttribute('y', y - 14);
        label.setAttribute('text-anchor', 'middle'); label.setAttribute('font-size', '12');
        label.textContent = `${index + 1}. ${stop.role}`;
        svg.append(circle, label);
      });
      const credit = createElement('p', undefined, 'route-diagram-credit');
      const link = createElement('a', '© OpenStreetMap contributors');
      link.href = CREDIT_URL; link.target = '_blank'; link.rel = 'noopener';
      credit.append(link);
      diagram.replaceChildren(svg, credit);
      diagram.hidden = false;
    }

    function fallback(routes, stops) {
      disposeMap();
      viewport.hidden = true;
      fitButton.hidden = true;
      retryButton.hidden = false;
      renderDiagram(routes, stops);
      setState('fallback', '지도 배경을 불러오지 못했습니다. 이동 경로 도식으로 표시합니다.');
    }

    function addArrows(L, instance, route) {
      if (route.path.length < 2) return;
      const layer = L.layerGroup().addTo(instance);
      const draw = () => {
        layer.clearLayers();
        const pts = route.path.map(p => instance.latLngToLayerPoint(p));
        const gap = 70;  // px
        let carry = gap / 2;
        for (let i = 1; i < pts.length; i++) {
          const a = pts[i - 1], b = pts[i];
          const len = a.distanceTo(b);
          if (!len) continue;
          const angle = Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI;
          for (let d = carry; d < len; d += gap) {
            const t = d / len;
            const at = instance.layerPointToLatLng(L.point(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t));
            L.marker(at, {interactive: false, keyboard: false, icon: L.divIcon({className: 'route-arrow',
              html: `<span style="transform:rotate(${angle}deg)">›</span>`, iconSize: [14, 14], iconAnchor: [7, 7]})}).addTo(layer);
          }
          carry = (carry - len) % gap;
          if (carry < 0) carry += gap;
        }
      };
      instance.whenReady(draw);  // 지도 위치가 정해진 뒤 그린다
      instance.on('zoomend', draw);
    }

    function fit() {
      if (!map || !bounds) return;
      map.invalidateSize({pan: false});
      map.fitBounds(bounds, {padding: [38, 48], maxZoom: 17, animate: false});
    }

    function mountMap(routes, stops, token) {
      if (token !== revision) return;
      if (!window.L) { fallback(routes, stops); return; }
      const L = window.L;
      try {
        const instance = L.map(viewport, {scrollWheelZoom: isDialog, attributionControl: true, inertia: false,
          zoomControl: true, maxZoom: 19, zoomAnimation: false, fadeAnimation: false,
          markerZoomAnimation: false});
        map = instance;
        instance.attributionControl.setPrefix(false);
        instance.zoomControl.setPosition('topright');
        const zoomIn = viewport.querySelector('.leaflet-control-zoom-in');
        const zoomOut = viewport.querySelector('.leaflet-control-zoom-out');
        for (const [control, label] of [[zoomIn, '지도 확대'], [zoomOut, '지도 축소']]) {
          if (control) { control.title = label; control.setAttribute('aria-label', label); }
        }
        let loaded = 0, cycleLoaded = 0, cycleFailed = 0;
        // Only visible map tiles are requested. Keep browser referrer and caching defaults.
        const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
          maxZoom: 19, updateWhenIdle: true, keepBuffer: 0,
          attribution: '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap contributors</a>'
        });
        tiles.on('loading', () => {
          if (map !== instance) return;
          cycleLoaded = cycleFailed = 0;
          clearTimeout(timeout);
          timeout = setTimeout(() => {
            if (map !== instance) return;
            if (!loaded) fallback(routes, stops);
            else setState('partial', '일부 지도 배경을 불러오지 못했습니다. 경로와 도보 시간은 그대로 표시합니다.');
          }, 10000);
        });
        tiles.on('tileload', () => {
          if (map !== instance) return;
          loaded += 1; cycleLoaded += 1;
          if (!cycleFailed) setState('map', isDialog ? '지도를 끌어 이동하고, 마우스 휠이나 + / − 버튼으로 확대·축소할 수 있습니다.' : '지도를 끌어 이동하거나 + / − 버튼으로 확대·축소할 수 있습니다.');
        });
        tiles.on('tileerror', () => { if (map === instance) cycleFailed += 1; });
        tiles.on('load', () => {
          if (map !== instance) return;
          clearTimeout(timeout);
          if (!loaded && cycleFailed) {
            // Leaflet still touches its map after firing this event. Dispose only
            // once its tile callback finishes, and never dispose a newer selection.
            queueMicrotask(() => {
              if (map === instance && token === revision) fallback(routes, stops);
            });
            return;
          }
          if (cycleFailed) setState('partial', '일부 지도 배경을 불러오지 못했습니다. 경로와 도보 시간은 그대로 표시합니다.');
          else if (cycleLoaded || loaded) setState('map', isDialog ? '지도를 끌어 이동하고, 마우스 휠이나 + / − 버튼으로 확대·축소할 수 있습니다.' : '지도를 끌어 이동하거나 + / − 버튼으로 확대·축소할 수 있습니다.');
        });
        tiles.addTo(instance);
        for (const route of routes) {
          // An estimate is not a pedestrian road: preserve its dashed presentation.
          L.polyline(route.path, {color: '#ffffff', weight: 11, opacity: 1, interactive: false,
            lineCap: 'round', lineJoin: 'round', dashArray: route.source === 'osm' ? undefined : '8 8'}).addTo(instance);
          L.polyline(route.path, {color: COLORS[route.index], weight: 6, opacity: 1, lineCap: 'round', lineJoin: 'round',
            dashArray: route.source === 'osm' ? undefined : '8 8', interactive: false,
            className: `meal-route-line meal-route-${route.index ? 'back' : 'to'}${route.source !== 'osm' ? ' meal-route-estimate' : ''}`}).addTo(instance);
        }
        for (const route of routes) addArrows(L, instance, route);
        // 말풍선 핀: 역할(출발·식당·다음 강의실) + 이름, 꼬리가 정확한 위치를 가리킨다
        stops.forEach(stop => {
          const pin = createElement('div', undefined, `route-pin route-pin-${stop.kind}`);
          pin.append(createElement('span', stop.role, 'route-pin-role'), createElement('strong', stop.name, 'route-pin-name'));
          const icon = L.divIcon({className: 'route-pin-wrap', html: pin.outerHTML, iconSize: null, iconAnchor: [0, 0]});
          L.marker(stop.point, {icon, title: `${stop.role} · ${stop.name}`, keyboard: false, interactive: false,
            zIndexOffset: stop.kind === 'meal' ? 1000 : 500}).addTo(instance);
          L.circleMarker(stop.point, {radius: 5, color: '#ffffff', weight: 2, fillColor: stop.kind === 'meal' ? '#e0663a' : COLORS[stop.kind === 'destination' ? 1 : 0],
            fillOpacity: 1, interactive: false}).addTo(instance);
        });
        bounds = L.latLngBounds(routes.flatMap(route => route.path));
        fitButton.hidden = false;
        fit();
      } catch (error) {
        console.error('route map', error);
        if (token === revision) fallback(routes, stops);
      }
    }

    function show(candidate, currentContext) {
      reset();
      selected = candidate;
      context = currentContext;
      const routes = routesFor(candidate);
      const stops = stopsFor(candidate, currentContext, routes);
      byId('route-map-summary').textContent = `${candidate.name} · 전체 도보 ${candidate.totalWalkMin}분`;
      if (isDialog) {
        if (!panel.open) panel.showModal();
      } else {
        panel.hidden = false;
        panel.open = true;
      }
      renderLegend(routes, currentContext, candidate);
      if (!routes.length) {
        viewport.hidden = true;
        setState('fallback', '표시할 경로 좌표가 없습니다. 추천 카드에서 도보 시간을 확인하세요.');
        return;
      }
      setState('loading', '지도 배경을 불러오고 있습니다…');
      const token = revision;
      frame = requestAnimationFrame(() => { frame = null; mountMap(routes, stops, token); });
    }

    fitButton.addEventListener('click', fit);
    retryButton.addEventListener('click', () => { if (selected) show(selected, context); });
    panel.addEventListener('toggle', () => { if (panel.open && !panel.hidden && map) fit(); });
    if (isDialog) panel.addEventListener('close', reset);  // 창을 닫으면 지도를 정리한다
    if ('ResizeObserver' in window) {
      new ResizeObserver(() => { if (map && panel.open && !panel.hidden) map.invalidateSize({pan: false}); }).observe(viewport);
    }
    return {show, reset};
  }

  window.MealRouteMap = {create};
})();
