/* PR #6's POI-free background; the team-supplied snapshot ships with the app. */
(() => {
  'use strict';
  let pending;
  async function load() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('v1/meal/basemap', {signal: controller.signal});
      if (!response.ok) throw Error('지도 자료를 읽지 못했습니다.');
      const data = await response.json();
      if (!['green', 'water', 'buildings', 'path', 'minor', 'major'].every(key => Array.isArray(data[key]))) throw Error('지도 자료 형식이 잘못되었습니다.');
      return data;
    } finally {clearTimeout(timeout);}
  }
  async function draw(map, isCurrent) {
    if (!pending) pending = load().catch(error => {pending = null; throw error;});
    const data = await pending;
    if (!isCurrent()) return;
    const L = window.L;
    map.getContainer().style.background = '#efede8';
    (map.getPane('mealBase') || map.createPane('mealBase')).style.zIndex = '250';
    const renderer = L.canvas({pane: 'mealBase', padding: .3});
    const layer = L.layerGroup().addTo(map);
    const polygons = (rows, options) => rows.forEach(points => L.polygon(points, {renderer, interactive: false, ...options}).addTo(layer));
    const lines = (rows, options) => rows.forEach(points => L.polyline(points, {renderer, interactive: false, ...options}).addTo(layer));
    polygons(data.green, {stroke: false, fillColor: '#dfeccf', fillOpacity: 1});
    polygons(data.water, {stroke: false, fillColor: '#cfe1f0', fillOpacity: 1});
    polygons(data.buildings, {color: '#d3cfc6', weight: 1, fillColor: '#e4e1da', fillOpacity: 1});
    lines(data.path, {color: '#c8c3b8', weight: 1.5, dashArray: '3 3'});
    lines(data.minor, {color: '#ffffff', weight: 4});
    lines(data.major, {color: '#d9d4ca', weight: 9});
    lines(data.major, {color: '#ffffff', weight: 7});
  }
  window.MealBasemap = {draw};
})();
