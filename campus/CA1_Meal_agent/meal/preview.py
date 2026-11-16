"""개발용 미리보기 — 채운 조사 양식으로 추천 화면(CA1-S02 · S03 · S05)을 브라우저에서 본다.

    python -m meal.preview            # http://127.0.0.1:8765

진짜 앱은 campus/mobile(프론트엔드 담당)이 만든다. 이 화면은 계산 결과를 눈으로 확인하려는 용도이고,
표준 라이브러리만 쓰며 내 PC(127.0.0.1)에서만 열린다.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .places import AGENT_DIR, DEFAULT_CAMPUS, PRICE_BANDS_NOTE, Campus, read_text
from .ranking import PRICE_BAND_LABEL, MealRequest, Taste, recommend
from .survey import CLOSED_STATES, load_coords, load_survey
from .timing import to_hhmm, to_min
from .basemap import load_basemap
from .schedule import auto_codes, f3_classes, f3_has_times, load_room_codes, room_building, parse_classes_text, slots_from_classes
from .walkgraph import WalkGraph, load_buildings

CENTER = "캠퍼스 중심"


def _points(campus: Campus) -> dict[str, tuple[float, float]]:
    pts = {CENTER: campus.center}
    pts.update({a.name: (a.lat, a.lng) for a in campus.anchors})
    return pts


_MENU_ITEM = re.compile(r"^(?P<name>.*?)\s*(?P<price>\d{3,6})?\s*(?:\((?P<note>[^()]*)\))?$")


def parse_menu(text: str) -> list[dict]:
    """조사 양식의 `대표 메뉴와 가격`(`김밥 3500 / 라면 3500 (점심특선)`) → [{name, price, note}].

    값이 없거나 `가격 변동` 처럼 숫자가 없으면 price 는 None.
    """
    out = []
    for part in (x.strip() for x in text.split(" / ")):
        if not part:
            continue
        m = _MENU_ITEM.match(part)
        name, price, note = m.group("name").strip(), m.group("price"), m.group("note")
        if not name:                       # `1인 11900` 처럼 숫자만 남은 경우는 원문 그대로
            name, price = part, None
        out.append({"name": name, "price": int(price) if price else None, "note": note})
    return out


def _menus(survey_text: str) -> dict[str, list[dict]]:
    """id → 대표 메뉴. 폐업·제외 줄은 건너뛴다(같은 id 의 중복 줄 대비)."""
    out: dict[str, list[dict]] = {}
    for row in csv.DictReader(io.StringIO(survey_text)):
        if any(s in (row.get("영업 상태") or "") for s in CLOSED_STATES):
            continue
        text = (row.get("대표 메뉴와 가격") or "").strip()
        if text and row.get("id"):
            out.setdefault(row["id"].strip(), parse_menu(text))
    return out


def _path(graph: WalkGraph | None, a, b) -> list[list[float]] | None:
    if graph is None or b is None:
        return None
    try:
        return [[round(x, 6), round(y, 6)] for x, y in graph.route(a, b)[1]]
    except ValueError:
        return None


def run_recommend(
    params: dict[str, str], survey: Path, candidates: Path, campus_path: Path,
    graph: WalkGraph | None = None, buildings: dict[str, tuple[float, float]] | None = None,
) -> dict:
    """쿼리 값 → 추천 결과 JSON. 화면과 테스트가 같은 함수를 쓴다.

    graph 가 있으면 도보를 길 기준으로 재고 카드마다 지나는 길(pathTo · pathBack)을 같이 준다.
    buildings 가 있으면 출발·다음 수업을 건물 이름으로 고를 수 있다.
    """
    campus = Campus.load(campus_path)
    gates = _points(campus)
    pts = {**gates, **(buildings or {})}
    text = read_text(survey)
    places, problems, blank = load_survey(text, load_coords(candidates))
    menus = _menus(text)

    start = to_min(params.get("start") or "11:30")
    minutes = int(params.get("minutes") or 90)
    weekday = int(params.get("weekday") if params.get("weekday") not in (None, "") else date.today().weekday())
    origin = params.get("from") or CENTER
    dest = params.get("to") or CENTER
    req = MealRequest(
        slot_start=start, slot_min=minutes, weekday=weekday,
        origin=pts[origin], dest=None if dest == "없음" else pts[dest],
        party_size=int(params.get("party") or 1),
        budget=int(params["budget"]) if params.get("budget") else None,
        rain=params.get("rain") == "1",
        hurry=params.get("hurry") == "1",
        include=frozenset(c.strip() for c in (params.get("cats") or "").split(",") if c.strip()),
    )
    r = recommend(places, req, Taste(), router=graph.meters if graph else None)
    where = {p.id: (p.lat, p.lng) for p in places}
    # 좌표는 미리보기 지도용 — 정의서 7절 MealCandidate 에는 없다
    card = lambda c: {**asdict(c), "priceLabel": PRICE_BAND_LABEL.get(c.priceBand, ""),
                      "lat": where[c.placeId][0], "lng": where[c.placeId][1], "menu": menus.get(c.placeId, []),
                      "pathTo": _path(graph, req.origin, where[c.placeId]),
                      "pathBack": _path(graph, where[c.placeId], req.dest)}
    pin = lambda name: {"name": name, "lat": pts[name][0], "lng": pts[name][1]}
    return {
        "slot": {"start": to_hhmm(start), "end": to_hhmm(start + minutes), "minutes": minutes,
                 "weekday": weekday, "from": origin, "to": dest},
        "top": [card(c) for c in r.top],
        "more": [card(c) for c in r.more],
        "tight": [card(c) for c in r.tight],
        "cafeteria": [card(c) for c in r.cafeteria],
        "excluded": r.excluded,
        "data": {"ready": len(places), "blank": blank, "problems": len(problems)},
        "points": list(gates),
        # 종류 칩 — 추천 가능한 곳이 많은 종류부터(카페 제외)
        "categories": [c for c, _ in Counter(p.category for p in places if p.category != "카페").most_common()],
        "buildings": sorted(buildings or {}),
        "walk": "path" if graph else "straight",
        "pins": {"from": pin(origin), "to": None if dest == "없음" else pin(dest)},
    }


PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>공강 식사 추천 미리보기</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<style>
:root{--bg:#f4f2ed;--card:#fff;--ink:#1c211e;--sub:#737a76;--line:#e8e5de;--soft:#f7f6f2;--accent:#2f5d50;--accent2:#3f7a68;--chip:#edf4f0;--chipink:#2f5d50;--warn:#a15c00;--warnbg:#fff3df;--pin:#e0592a;--shadow:0 1px 2px rgba(28,33,30,.04),0 8px 24px rgba(28,33,30,.06)}
@media (prefers-color-scheme:dark){:root{--bg:#131614;--card:#1d2220;--ink:#eef1ef;--sub:#9da5a1;--line:#2d3431;--soft:#232927;--accent:#8cc5b2;--accent2:#a6d6c6;--chip:#25302c;--chipink:#a6d6c6;--warn:#f0b766;--warnbg:#3a2f1e;--pin:#ff7a45;--shadow:0 1px 2px rgba(0,0,0,.3),0 8px 24px rgba(0,0,0,.25)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 "Pretendard Variable",Pretendard,system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;letter-spacing:-.01em;-webkit-font-smoothing:antialiased}
/* 왼쪽: 시간표·조건 · 오른쪽: 이번 공강에 다녀올 수 있는 곳 → 그 아래 지도.
   두 칸은 폭·위아래 끝이 같다 — 왼쪽(입력) 높이에 맞춰 오른쪽 지도가 늘어난다 */
main{max-width:1480px;margin:0 auto;padding:32px 28px 40px;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:28px;align-items:stretch}
main>section{display:flex;flex-direction:column}
#f{flex:1;display:flex;flex-direction:column;justify-content:space-between;margin-bottom:0}.tt .ghost{align-self:flex-start}
#side{display:flex;flex-direction:column;gap:18px;padding-top:86px}
#mapwrap{flex:1;display:flex;min-height:360px}
#map{flex:1;min-height:360px;border-radius:24px;background:var(--card);box-shadow:var(--shadow);overflow:hidden}
#out>.panel:first-child{margin-top:0}
.grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
@media (max-width:860px){main{grid-template-columns:minmax(0,1fr);padding:16px 16px 32px;gap:12px}#side{padding-top:0}#mapwrap{flex:none;min-height:0}#map{flex:none;height:280px;min-height:0;width:100%}}
.hero{display:flex;align-items:center;gap:14px;margin:0 2px 20px}
.logo{width:52px;height:52px;border-radius:16px;background:var(--accent);color:#fff;display:grid;place-items:center;font-size:28px;flex:none}
h1{font-size:28px;font-weight:800;letter-spacing:-.03em;margin:0;line-height:1.2}.muted{color:var(--sub);font-size:14px}
.panel{background:var(--card);border-radius:24px;padding:24px;margin:12px 0;box-shadow:var(--shadow)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
label{display:block;font-size:13px;font-weight:600;color:var(--sub);margin-bottom:7px}
input,select{width:100%;height:50px;padding:0 14px;border:1px solid transparent;border-radius:12px;background:var(--soft);color:var(--ink);font:inherit;outline:none;transition:border-color .15s,background .15s}
input:focus,select:focus{border-color:var(--accent2);background:var(--card)}
.sec{font-size:13px;font-weight:600;color:var(--sub);margin:20px 0 10px}.sec span{font-weight:500}
.row,.cats{display:flex;gap:8px;flex-wrap:wrap}
.row{margin-top:14px}
.row label,.cats label{display:inline-flex;align-items:center;gap:6px;margin:0;padding:9px 16px;border-radius:999px;background:var(--soft);color:var(--ink);font-size:14px;font-weight:600;cursor:pointer;user-select:none;transition:background .15s,box-shadow .15s}
.row label:has(input:checked),.cats label:has(input:checked){background:var(--chip);color:var(--chipink);box-shadow:inset 0 0 0 1.5px var(--accent2)}
.row input,.cats input{width:auto;height:auto;margin:0;accent-color:var(--accent)}
.cats input{position:absolute;opacity:0;pointer-events:none}
.cats .all{color:var(--sub)}
button{font:700 16px/1.2 inherit;font-family:inherit;cursor:pointer;border-radius:14px;transition:transform .1s,filter .15s}
button:active{transform:scale(.98)}
.primary{width:100%;margin-top:22px;height:58px;border:0;background:var(--accent);color:#fff;font-size:17px}
.primary:hover{filter:brightness(1.08)}
.summary{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:15px;padding:18px 24px}.summary b{font-size:21px;letter-spacing:-.02em}
.pill{background:var(--chip);color:var(--chipink);border-radius:999px;padding:6px 13px;font-size:13px;font-weight:700;white-space:nowrap}
h2{font-size:19px;font-weight:800;letter-spacing:-.02em;margin:26px 4px 12px}
.deck{position:relative;touch-action:pan-y;user-select:none}
.deck::before,.deck::after{content:"";position:absolute;left:14px;right:14px;bottom:-6px;height:20px;border-radius:0 0 20px 20px;background:var(--card);box-shadow:var(--shadow);opacity:.7;z-index:0}
.deck::after{left:28px;right:28px;bottom:-12px;opacity:.4}
.card{position:relative;z-index:1;background:var(--card);border-radius:24px;padding:26px;margin:10px 0;box-shadow:var(--shadow)}
.deck .card{cursor:grab;transition:transform .25s ease,opacity .25s ease}.deck .card.drag{transition:none;cursor:grabbing}
.card .top{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}
.name{font-weight:800;font-size:28px;letter-spacing:-.03em;line-height:1.25}
.cat{color:var(--sub);font-size:15px;margin-top:4px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:20px 0 6px}
.stat{background:var(--soft);border-radius:16px;padding:14px 16px}.stat small{display:block;color:var(--sub);font-size:13px;font-weight:600}
.stat b{font-size:24px;letter-spacing:-.02em}.stat b span{font-size:14px;font-weight:600;color:var(--sub);margin-left:1px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}.chip{background:var(--chip);color:var(--chipink);border-radius:999px;padding:5px 12px;font-size:13px;font-weight:600}
.chip.warn{background:var(--warnbg);color:var(--warn)}
.tight{box-shadow:var(--shadow),inset 0 0 0 2px var(--warn)}.badge{background:var(--warnbg);color:var(--warn);border-radius:999px;padding:3px 9px;font-size:12px;font-weight:700;white-space:nowrap}
.step{font-size:14px;font-weight:600;color:var(--sub);white-space:nowrap;margin-top:4px;font-variant-numeric:tabular-nums}
.acts{display:grid;grid-template-columns:58px 1fr 1fr;gap:10px;margin-top:22px}
.acts button{height:58px;padding:0 12px;border:0;background:var(--card);color:var(--ink);box-shadow:var(--shadow)}
.acts .go{background:var(--pin);color:#fff}
.acts button:disabled{opacity:.35;cursor:default}
.hint{text-align:center;font-size:13px;color:var(--sub);margin-top:10px}
.empty{text-align:center;color:var(--sub);padding:28px 10px}
.mini{display:flex;justify-content:space-between;gap:8px;padding:10px 0;border-top:1px solid var(--line);font-size:14px}.mini:first-child{border-top:0;padding-top:0}.mini:last-child{padding-bottom:0}
.note{font-size:12px;color:var(--sub);margin:20px 4px 0;line-height:1.6}
.menu{margin:18px 0 0;padding:6px 6px 0 0;border-top:1px dashed var(--line);font-size:16px;max-height:140px;overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:var(--line) transparent}
.menu::-webkit-scrollbar{width:6px}.menu::-webkit-scrollbar-thumb{background:var(--line);border-radius:3px}
.menu.more{-webkit-mask-image:linear-gradient(#000 82%,transparent);mask-image:linear-gradient(#000 82%,transparent)}
.menu.more.end{-webkit-mask-image:none;mask-image:none}
.menu div{display:flex;justify-content:space-between;gap:10px;padding:9px 0}.menu .p{font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}
.menu .n{color:var(--sub);font-size:12px}
.star{background:none;border:0;line-height:1;text-align:center;-webkit-text-stroke:1.5px #fff;paint-order:stroke fill;text-shadow:0 1px 3px rgba(0,0,0,.3);cursor:pointer}
.done{display:inline-flex;align-items:center;gap:6px;font-weight:700;color:var(--accent);font-size:13px;background:var(--chip);border-radius:999px;padding:4px 10px;margin-bottom:10px}
.leaflet-tooltip{font:600 13px/1.3 "Pretendard Variable",system-ui,sans-serif;border:0;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.15);padding:4px 8px}
.leaflet-tooltip.pin{font-size:14px;font-weight:700}
.tt textarea{width:100%;min-height:96px;padding:12px 14px;border:1px solid transparent;border-radius:12px;background:var(--soft);color:var(--ink);font:14px/1.7 ui-monospace,Consolas,monospace;resize:vertical;outline:none}
.tt textarea:focus{border-color:var(--accent2);background:var(--card)}
.tt .head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}.tt .head b{font-size:18px}
.ghost{height:42px;padding:0 18px;border:0;background:var(--chip);color:var(--chipink);font-size:14px}
.slots{display:flex;flex-direction:column;gap:6px;margin-top:10px}
.slot{display:flex;justify-content:space-between;align-items:center;gap:8px;width:100%;padding:13px 16px;border:0;border-radius:14px;background:var(--soft);color:var(--ink);text-align:left;font-weight:700;font-size:16px}
.slot small{display:block;color:var(--sub);font-weight:500;font-size:13px}.slot.on{background:var(--chip);box-shadow:inset 0 0 0 1.5px var(--accent2)}
/* 시간표 타임라인 — 수업(초록 회색) · 휴강(점선) · 밥 먹을 공강(주황, 누르면 그 공강으로 추천) */
.tl{margin:6px 0 4px}
.tl-bar{position:relative;height:82px;border-radius:16px;background:var(--soft)}
.tl-c,.tl-s{position:absolute;top:7px;bottom:7px;border-radius:11px;padding:6px 9px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;line-height:1.25;font-size:13px;font-weight:700}
.tl-c{background:#dfe9e4;color:var(--ink);z-index:2}.tl-c small,.tl-s small{display:block;font-size:11px;font-weight:600;opacity:.75;overflow:hidden;text-overflow:ellipsis}
.tl-c.off{top:auto;height:18px;padding:1px 7px;font-size:10px;line-height:16px;background:var(--card);box-shadow:inset 0 0 0 1px var(--sub);color:var(--sub);text-decoration:line-through;z-index:3;opacity:.85}
.tl-c.off small{display:none}
.tl-s{background:#fde3d6;color:#b8461c;border:0;cursor:pointer;z-index:1;text-align:left;font-family:inherit;transition:background .15s,color .15s}
.tl-g{position:absolute;top:7px;bottom:7px;border:0;border-radius:11px;background:transparent;cursor:pointer;z-index:0;padding:6px 9px;text-align:left;font:600 12px/1.25 inherit;font-family:inherit;color:var(--sub);overflow:hidden;white-space:nowrap}
.tl-g:hover{background:var(--card);box-shadow:inset 0 0 0 1.5px var(--accent2);color:var(--chipink)}
.tl-g.on{background:var(--chip);box-shadow:inset 0 0 0 2px var(--accent);color:var(--chipink)}
.tl-s:hover{background:#fbd2bd}.tl-s.on{background:var(--pin);color:#fff}
.tl-ticks{position:relative;height:20px;margin-top:4px;font-size:11px;color:var(--sub);font-variant-numeric:tabular-nums}
.tl-ticks span{position:absolute;transform:translateX(-50%);white-space:nowrap}
.tl-ticks span:first-child{transform:none}.tl-ticks span:last-child{transform:translateX(-100%)}
.tl-legend{display:flex;gap:14px;margin-top:6px;font-size:12px;color:var(--sub)}.tl-legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
@media (prefers-color-scheme:dark){.tl-c{background:#2f3d37}.tl-s{background:#4a2c1f;color:#ffb48f}.tl-s:hover{background:#5a3424}}
.tt details{margin-top:12px}.tt summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--sub);list-style:none}
.tt summary::-webkit-details-marker{display:none}.tt summary::before{content:"✎ "}
.tt details[open] summary{margin-bottom:8px}
/* 탭 · 식당 둘러보기 · 메뉴·영업시간 창 (김종연 CA1 화면에서 가져온 기능) */
.tabs{grid-column:1/-1;display:flex;gap:6px;margin:0 2px -6px}
.tabs button{height:40px;padding:0 18px;border:0;border-radius:999px;background:transparent;color:var(--sub);font-size:15px}
.tabs button[aria-selected=true]{background:var(--card);color:var(--ink);box-shadow:var(--shadow)}
#browse{grid-column:1/-1}
body[data-tab=browse] main>section:not(#browse),body[data-tab=browse] #side{display:none}
body:not([data-tab=browse]) #browse{display:none}
.bhead{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px}.bhead input{flex:1;min-width:180px}.bhead select{width:auto;min-width:130px}
.blist{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.bcard{display:flex;flex-direction:column;gap:6px;padding:16px;border-radius:18px;background:var(--soft);border:0;text-align:left;font:inherit;color:inherit;cursor:pointer;transition:box-shadow .15s}
.bcard:hover{box-shadow:inset 0 0 0 1.5px var(--accent2)}.bcard b{font-size:17px;letter-spacing:-.02em}.bcard small{color:var(--sub);font-size:13px}
.bcard .bm{font-size:13px;color:var(--ink);display:flex;justify-content:space-between;gap:8px}.bcard .bm span:last-child{font-weight:700;white-space:nowrap}
.bpager{display:flex;justify-content:flex-end;align-items:center;gap:8px;margin-top:14px}.bpager button{height:38px;padding:0 14px;border:0;background:var(--soft)}
.linkbtn{border:0;background:none;padding:2px 0;color:var(--accent);font-size:13px;font-weight:600;text-decoration:underline;text-underline-offset:3px}
dialog.pd{border:0;border-radius:22px;padding:0;width:min(520px,calc(100vw - 32px));max-height:calc(100vh - 48px);background:var(--card);color:var(--ink);box-shadow:0 20px 60px rgba(0,0,0,.25)}
dialog.pd::backdrop{background:rgba(20,24,22,.45)}
.pd .pdh{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;padding:22px 22px 6px}.pd .pdh button{border:0;background:var(--soft);height:36px;padding:0 14px}
.pd .pdb{padding:0 22px 22px;overflow:auto}.pd h4{margin:18px 0 6px;font-size:14px;color:var(--sub)}.pd .menu{max-height:none}
.pd dl{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;margin:0;font-size:15px}.pd dt{color:var(--sub)}.pd dd{margin:0}
.leaflet-bar{border:0!important;box-shadow:var(--shadow)!important;border-radius:12px!important;overflow:hidden}
/* UnivUs 앱 안(iframe)에서는 제목·바깥 여백을 앱 페이지가 맡는다 */
.embed body{background:transparent}.embed main{padding:0 0 8px;max-width:none}.embed .hero{display:none}.embed #side{padding-top:12px}
</style></head><body><main>
<nav class="tabs" role="tablist" aria-label="식사 추천 화면"><button type="button" role="tab" id="tab-rec" aria-selected="true">식사 추천</button><button type="button" role="tab" id="tab-browse" aria-selected="false">식당 둘러보기</button></nav>
<section id="browse" class="panel" aria-labelledby="tab-browse">
  <div class="bhead"><input id="bq" type="search" placeholder="식당 이름·종류·메뉴·주소 검색" aria-label="식당 검색"><select id="bcat" aria-label="종류"><option value="">모든 종류</option></select><span class="muted" id="bcount"></span></div>
  <div class="blist" id="blist"></div>
  <div class="bpager"><span class="muted" id="bpage"></span><button type="button" id="bprev">이전</button><button type="button" id="bnext">다음</button></div>
</section>
<section>
<div class="hero"><div class="logo">🍚</div><div><h1>공강 식사 추천</h1></div></div>
<div class="panel tt"><div class="head"><b>📅 오늘 시간표로 공강 찾기</b><span class="muted" id="ttsrc"></span></div>
  <div id="ttline"></div>
  <div class="slots" id="ttslots"></div>
  <details id="ttedit"><summary>시간표 직접 적기 · 고치기</summary>
  <textarea id="tttext" placeholder="한 줄에 수업 하나 — 09:00-10:15 공7-223 운영체제&#10;휴강이면 줄 끝에 '휴강'"></textarea>
  <button type="button" class="ghost" id="ttgo" style="margin-top:8px">공강 찾기</button></details></div>
<form class="panel" id="f">
  <div class="grid">
    <div><label>공강 시작</label><input type="time" name="start" value="11:30"></div>
    <div><label>공강 길이(분)</label><input type="number" name="minutes" value="90" min="10" step="5"></div>
    <div><label>요일</label><select name="weekday"></select></div>
    <div><label>1인 예산(원, 비우면 제한 없음)</label><input type="number" name="budget" step="1000" placeholder="예: 8000"></div>
    <div><label>출발</label><select name="from"></select></div>
    <div><label>다음 수업 건물 쪽</label><select name="to"></select></div>
    <div><label>인원</label><input type="number" name="party" value="1" min="1"></div>
  </div>
    <div class="sec">먹고 싶은 종류 <span>· 여러 개 고를 수 있어요, 안 고르면 전부</span></div><div class="cats" id="cats"></div>
  <button class="primary">식당 추천받기</button>
</form>
</section>
<aside id="side"><div id="results"><div id="out"></div>
<div class="note">가격대 {bands} · <span id="walknote">도보는 직선거리 × 1.3 ÷ 70m/분</span> · 지도·길·건물: © OpenStreetMap contributors (ODbL)</div>
</div>
<div id="mapwrap"><div id="map"></div></div></aside>
</main>
<dialog class="pd" id="pd" aria-labelledby="pd-name"><div class="pdh"><div><div class="name" id="pd-name"></div><div class="cat" id="pd-cat"></div></div><button type="button" onclick="document.getElementById('pd').close()">닫기 ✕</button></div><div class="pdb" id="pd-body"></div></dialog>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
const CLEAN={clean};const API={api};   // 미리보기 서버 '/api/' · UnivUs 안에서는 '/api/meal/'
// UnivUs 앱 페이지(/meal)의 iframe 안이면 — 내 높이를 알려서 앱이 iframe 을 그만큼 늘리게 한다(안쪽 스크롤 없음)
if(window.top!==window){document.documentElement.classList.add('embed');
 let last=0;const tell=()=>{const m=document.querySelector('main');const h=m?m.offsetHeight+8:0;if(h&&h!==last){last=h;parent.postMessage({ca1Height:h},location.origin)}};
 if(window.ResizeObserver)new ResizeObserver(tell).observe(document.body);
 addEventListener('message',e=>{if(e.origin===location.origin&&e.data&&e.data.ca1Ask){last=0;tell()}});   // 앱 페이지가 늦게 떠서 물어보면 다시 알린다
 setInterval(tell,400)}   // 추천·시간표를 받아 내용이 바뀔 때마다 — 바뀐 때만 보낸다
const DAYS="월화수목금토일";const f=document.getElementById('f');const out=document.getElementById('out');
const wd=f.weekday;[...DAYS].forEach((d,i)=>wd.add(new Option(d+'요일',i)));wd.value=(new Date().getDay()+6)%7;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// --- 지도 -------------------------------------------------------------------
let map=null,layer=null,allLayer=null;
if(window.L){map=L.map('map',{zoomControl:true});
 // 바탕 지도 — OSM 길·건물·녹지만 직접 그린다(가게·카페 아이콘이 없어 별표가 잘 보인다). 고르는 칸 없음
 map.attributionControl.addAttribution('&copy; OpenStreetMap contributors');
 map.getContainer().style.background='#efede8';
 if(CLEAN){map.createPane('base').style.zIndex=250;const base=L.layerGroup().addTo(map);
  fetch(API+'basemap.json').then(r=>r.json()).then(b=>{const R=L.canvas({pane:'base',padding:.3});
   const poly=(list,o)=>list.forEach(g=>L.polygon(g,{renderer:R,interactive:false,...o}).addTo(base));
   const line=(list,o)=>list.forEach(g=>L.polyline(g,{renderer:R,interactive:false,...o}).addTo(base));
   poly(b.green,{stroke:false,fillColor:'#dfeccf',fillOpacity:1});
   poly(b.water,{stroke:false,fillColor:'#cfe1f0',fillOpacity:1});
   poly(b.buildings,{color:'#d3cfc6',weight:1,fillColor:'#e4e1da',fillOpacity:1});
   line(b.path,{color:'#c8c3b8',weight:1.5,dashArray:'3 3'});
   line(b.minor,{color:'#ffffff',weight:4});
   line(b.major,{color:'#d9d4ca',weight:9});line(b.major,{color:'#ffffff',weight:7})})}
 allLayer=L.layerGroup().addTo(map);layer=L.layerGroup().addTo(map);map.setView([35.1765,126.9055],16);
 // 화면 폭에 따라 지도 칸 크기가 바뀐다 → 크기를 다시 재고 보던 곳에 다시 맞춘다
 let lastFit=null;map._fit=b=>{lastFit=b;map.invalidateSize();map.fitBounds(b,{padding:[50,50],maxZoom:17})};
 if(window.ResizeObserver)new ResizeObserver(()=>{map.invalidateSize();if(lastFit)map.fitBounds(lastFit,{padding:[50,50],maxZoom:17})}).observe(map.getContainer())}
else{document.getElementById('map').innerHTML='<div class="empty">지도를 불러오지 못했어요(인터넷 연결 확인)</div>'}
const star=(p,color,size)=>L.marker([p.lat,p.lng],{icon:L.divIcon({className:'star',html:`<span style="color:${color};font-size:${size}px">★</span>`,iconSize:[size,size],iconAnchor:[size/2,size/2]})});
// 추천된 가게 전부를 작은 별로 — 누르면 그 가게 카드로 간다
function drawAll(){if(!map)return;allLayer.clearLayers();
 deck.forEach((d,i)=>{if(i===pos&&!chosen)return;
  const col=d.kind==='tight'?css('--warn'):css('--accent');
  star(d,col,20).addTo(allLayer)
   .bindTooltip(`${i+1}. ${d.name} · 도보 ${d.walkToMin}분`,{direction:'top',offset:[0,-8]})
   .on('click',()=>{chosen=false;pos=i;render()})})}
// 메뉴가 칸보다 길면 안에서 스크롤 — 아래가 더 있으면 끝을 흐리게, 끝까지 내리면 또렷하게
function markMenus(){document.querySelectorAll('.menu').forEach(m=>{const more=m.scrollHeight>m.clientHeight+2;m.classList.toggle('more',more);
 m.onscroll=()=>m.classList.toggle('end',m.scrollTop+m.clientHeight>=m.scrollHeight-2);
 m.onpointerdown=e=>e.stopPropagation()})}   // 메뉴를 끌어 스크롤할 때 카드가 옆으로 넘어가지 않게
function showMap(c,pins){markMenus();if(!map)return;layer.clearLayers();drawAll();const pts=[];
 const dot=(p,label,color,r)=>{L.circleMarker([p.lat,p.lng],{radius:r,color:color,weight:2,fillColor:color,fillOpacity:.9}).addTo(layer)
   .bindTooltip(label,{permanent:true,direction:'top',offset:[0,-r],className:'pin'});pts.push([p.lat,p.lng])};
 dot(pins.from,'출발 · '+pins.from.name,css('--accent'),7);
 if(pins.to&&pins.to.name!==pins.from.name)dot(pins.to,'다음 수업 · '+pins.to.name,css('--sub'),6);
 if(c){star(c,css('--pin'),34).setZIndexOffset(1000).addTo(layer)
   .bindTooltip(c.name+' · 도보 '+c.walkToMin+'분',{permanent:true,direction:'top',offset:[0,-14],className:'pin'});pts.push([c.lat,c.lng]);
  const to=c.pathTo||[[pins.from.lat,pins.from.lng],[c.lat,c.lng]];
  const back=pins.to?(c.pathBack||[[c.lat,c.lng],[pins.to.lat,pins.to.lng]]):null;
  L.polyline(to,{color:css('--pin'),weight:4,opacity:.85,dashArray:c.pathTo?null:'6 6'}).addTo(layer);
  if(back)L.polyline(back,{color:css('--sub'),weight:3,opacity:.7,dashArray:'4 6'}).addTo(layer);
  to.forEach(p=>pts.push(p));if(back)back.forEach(p=>pts.push(p))}
 map._fit(L.latLngBounds(pts))}

// --- 카드 한 장씩 -------------------------------------------------------------
let deck=[],pos=0,pins=null,slot=null,chosen=false;
const won=n=>n.toLocaleString('ko-KR')+'원';
function menuHtml(m){if(!m||!m.length)return'';
 return '<div class="menu">'+m.map(x=>`<div><span>${esc(x.name)}${x.note?` <span class="n">${esc(x.note)}</span>`:''}</span><span class="p">${x.price!=null?won(x.price):'가격 확인 필요'}</span></div>`).join('')+'</div>'}
function cardHtml(c){
 const tag=c.kind==='tight'?'<span class="badge">빠듯</span>':'';
 const stat=(k,v,u)=>`<div class="stat"><small>${k}</small><b>${v}<span>${u}</span></b></div>`;
 return `<div class="card ${c.kind==='tight'?'tight':''}" id="cur"><div class="top"><div><div class="name">${esc(c.name)}</div>
 <div class="cat">${esc(c.category)} · ${esc(c.priceLabel)} · ${c.openUntil?esc(c.openUntil)+'까지 영업':'영업시간 확인 필요'}</div>
 <button type="button" class="linkbtn" onpointerdown="event.stopPropagation()" onclick="openPlace('${esc(c.placeId)}')">메뉴·영업시간</button></div>
 <div style="text-align:right">${tag}<div class="step">${pos+1} / ${deck.length}</div></div></div>
 <div class="stats">${stat('도보',c.walkToMin,'분')}${stat('왕복 포함',c.needMin,'분')}${stat('여유',c.slackMin,'분')}</div>
 ${menuHtml(c.menu)}
 ${chipsHtml(c)}</div>`}
// 숫자 칸·가격 줄과 겹치는 칩은 뺀다
function chipsHtml(c){const rs=c.reasons.filter(r=>!/^(도보|여유) [0-9]+분$/.test(r)&&r!==c.priceLabel);if(!rs.length)return'';
 return '<div class="chips">'+rs.map(r=>`<span class="chip ${r==='영업시간 확인 필요'?'warn':''}">${esc(r)}</span>`).join('')+'</div>'}
function render(){chosen=false;const box=document.getElementById('deck');if(!box)return;
 if(pos>=deck.length){box.innerHTML=`<div class="panel empty">추천을 다 봤어요<br><span class="muted">조건을 바꾸거나 처음부터 다시 볼 수 있어요</span></div>`;
  document.getElementById('acts').innerHTML='<button type="button" onclick="pos=0;render()" style="grid-column:1/-1">처음부터 다시 보기</button>';showMap(null,pins);return}
 const c=deck[pos];box.innerHTML=cardHtml(c);
 document.getElementById('acts').innerHTML=`<button type="button" onclick="move(-1)" ${pos?'':'disabled'} aria-label="이전 식당">←</button>
  <button type="button" onclick="move(1)">별로예요, 다음 →</button>
  <button type="button" class="go" onclick="go()">여기 갈게 🍽</button>`;
 showMap(c,pins);swipe(document.getElementById('cur'))}
function move(d){const el=document.getElementById('cur');const n=pos+d;if(n<0)return;
 if(el){el.style.transform=`translateX(${d>0?-120:120}%)`;el.style.opacity='0'}setTimeout(()=>{pos=n;render()},180)}
// '여기 갈게' — 고른 가게와 메뉴만 보여 준다
function go(){chosen=true;const c=deck[pos];
 document.getElementById('deck').innerHTML=`<div class="card"><div class="done">✓ 여기로 정했어요</div><div class="name">${esc(c.name)}</div>
  <div class="cat">${esc(c.category)} · ${esc(c.priceLabel)} · ${c.openUntil?esc(c.openUntil)+'까지 영업':'영업시간 확인 필요'}</div>
  ${menuHtml(c.menu)}</div>`;
 document.getElementById('acts').innerHTML='<button type="button" onclick="render()" style="grid-column:1/-1">다른 곳 다시 보기</button>';
 showMap(c,pins)}
function swipe(el){if(!el)return;let x0=null,dx=0;
 el.addEventListener('pointerdown',e=>{x0=e.clientX;dx=0;el.classList.add('drag');el.setPointerCapture(e.pointerId)});
 el.addEventListener('pointermove',e=>{if(x0==null)return;dx=e.clientX-x0;el.style.transform=`translateX(${dx}px) rotate(${dx/40}deg)`});
 const end=()=>{if(x0==null)return;x0=null;el.classList.remove('drag');
  if(dx<-80)move(1);else if(dx>80&&pos>0)move(-1);else el.style.transform=''};
 el.addEventListener('pointerup',end);el.addEventListener('pointercancel',end)}
document.addEventListener('keydown',e=>{if(e.target.closest('form'))return;if(e.key==='ArrowRight')move(1);if(e.key==='ArrowLeft')move(-1)});

let seq=0;   // 요청이 겹치면 마지막 요청 결과만 그린다
async function run(e){e&&e.preventDefault();const my=++seq;const q=new URLSearchParams(new FormData(f));
 q.delete('cat');q.set('cats',[...f.querySelectorAll('input[name=cat]:checked')].map(x=>x.value).join(','));
 const r=await fetch(API+'recommend?'+q);const d=await r.json();if(my!==seq)return;
 if(!r.ok){out.innerHTML=`<div class="panel empty">${esc(d.error)}</div>`;return}
 if(!f.from.options.length){const grp=(sel,label,names)=>{if(!names.length)return;const g=document.createElement('optgroup');g.label=label;names.forEach(n=>g.appendChild(new Option(n,n)));sel.appendChild(g)};
  for(const sel of [f.from,f.to]){grp(sel,'건물',d.buildings);grp(sel,'캠퍼스 문',d.points)}
  f.to.add(new Option('없음(오늘 마지막 수업 뒤)','없음'));f.from.value=f.to.value=d.points[0];
  // 종류 칩 — 여러 개 고르기, 바꾸면 바로 다시 추천
  document.getElementById('cats').innerHTML=d.categories.map(c=>`<label><input type="checkbox" name="cat" value="${esc(c)}">${esc(c)}</label>`).join('');
  document.getElementById('cats').addEventListener('change',()=>run())}
 document.getElementById('walknote').textContent=d.walk==='path'?'도보는 OSM 길을 따라 잰 거리 ÷ 70m/분':'도보는 직선거리 × 1.3 ÷ 70m/분';
 pins=d.pins;const s=slot=d.slot;
 // 순서: 추천 3곳(종류 겹치지 않게) → 나머지 적합 → 빠듯
 deck=[...d.top.map(c=>({...c,kind:'top'})),...d.more.map(c=>({...c,kind:'more'})),...d.tight.map(c=>({...c,kind:'tight'}))];pos=0;
 let h=`<div class="panel summary"><div><b>${s.start}~${s.end} · ${s.minutes}분</b><div class="muted">${esc(s.from)} 출발 → ${esc(s.to==='없음'?'돌아오지 않음':s.to)}</div></div><span class="pill">${deck.length}곳 갈 수 있어요</span></div>`;
 h+='<h2>이번 공강에 다녀올 수 있는 곳</h2>';
 if(deck.length){h+='<div class="deck" id="deck"></div><div class="acts" id="acts"></div><div class="hint">카드를 왼쪽으로 밀거나 → 키를 누르면 다음 식당 · 지도의 ★ 을 누르면 그 식당으로</div>'}
 else{const why=Object.entries(d.excluded).map(([k,v])=>`${k} ${v}`).join(' · ');
  h+=`<div class="panel empty">${d.data.ready?'조건에 맞는 곳이 없어요<br><span class="muted">'+esc(why)+'</span>':'아직 조사된 식당이 없어요 — survey.csv 의 가격대·영업시간을 채우면 나와요'}</div>`}
 if(d.cafeteria.length)h+='<h2>학식</h2><div class="panel">'+d.cafeteria.map(c=>`<div class="mini"><span>${esc(c.name)}</span><span class="muted">왕복 ${c.needMin}분 · 여유 ${c.slackMin}분</span></div>`).join('')+'</div>';
 out.innerHTML=h;if(deck.length)render();else showMap(null,pins)}
f.addEventListener('submit',run);run();

// --- 탭 · 식당 둘러보기 · 메뉴·영업시간 창 -------------------------------------
function setTab(t){document.body.dataset.tab=t;document.getElementById('tab-rec').setAttribute('aria-selected',String(t!=='browse'));document.getElementById('tab-browse').setAttribute('aria-selected',String(t==='browse'));if(t==='browse'&&!bloaded)loadBrowse();if(t!=='browse'&&map)setTimeout(()=>map.invalidateSize(),0)}
document.getElementById('tab-rec').onclick=()=>setTab('rec');document.getElementById('tab-browse').onclick=()=>setTab('browse');
let bloaded=false,boff=0,bseq=0,btimer=null;const BLIM=24;
async function loadBrowse(){bloaded=true;const my=++bseq;const q=new URLSearchParams({q:document.getElementById('bq').value.trim(),cat:document.getElementById('bcat').value,offset:boff,limit:BLIM});
 const d=await (await fetch(API+'places?'+q)).json();if(my!==bseq)return;
 const sel=document.getElementById('bcat');if(sel.options.length===1)d.categories.forEach(c=>sel.add(new Option(c,c)));
 document.getElementById('bcount').textContent=`추천 가능한 ${d.total}곳`;
 document.getElementById('blist').innerHTML=d.items.length?d.items.map(x=>`<button type="button" class="bcard" data-id="${esc(x.id)}"><b>${esc(x.name)}</b><small>${esc(x.category)} · ${esc(x.priceLabel)}${x.closed&&x.closed!=='없음'?' · '+esc(x.closed)+' 휴무':''}</small><small>${esc(x.hours||'영업시간 확인 필요')}</small>${x.menu.map(m=>`<span class="bm"><span>${esc(m.name)}</span><span>${m.price!=null?won(m.price):''}</span></span>`).join('')}</button>`).join(''):'<div class="empty">찾는 식당이 없어요</div>';
 document.querySelectorAll('#blist .bcard').forEach(b=>b.onclick=()=>openPlace(b.dataset.id));
 const pages=Math.max(1,Math.ceil(d.total/BLIM)),page=Math.floor(boff/BLIM)+1;document.getElementById('bpage').textContent=`${page} / ${pages}`;
 document.getElementById('bprev').disabled=boff===0;document.getElementById('bnext').disabled=boff+BLIM>=d.total}
document.getElementById('bq').oninput=()=>{clearTimeout(btimer);btimer=setTimeout(()=>{boff=0;loadBrowse()},250)};
document.getElementById('bcat').onchange=()=>{boff=0;loadBrowse()};
document.getElementById('bprev').onclick=()=>{boff=Math.max(0,boff-BLIM);loadBrowse()};
document.getElementById('bnext').onclick=()=>{boff+=BLIM;loadBrowse()};
async function openPlace(id){const dlg=document.getElementById('pd');const r=await fetch(API+'places/'+encodeURIComponent(id));const d=await r.json();
 if(!r.ok){toastMsg(d.error||'식당 정보를 읽지 못했어요');return}
 document.getElementById('pd-name').textContent=d.name;document.getElementById('pd-cat').textContent=`${d.category} · ${d.priceLabel}`;
 const row=(k,v)=>v?`<dt>${k}</dt><dd>${esc(v)}</dd>`:'';
 document.getElementById('pd-body').innerHTML=(d.address?`<p class="muted">${esc(d.address)}</p>`:'')+'<h4>메뉴</h4>'+(menuHtml(d.menu)||'<p class="muted">메뉴 정보가 없어요</p>')
  +'<h4>영업시간</h4><dl>'+row('영업',d.hours||'영업시간 확인 필요')+row('브레이크',d.break)+row('주문 마감',d.lastOrder)+row('휴무',d.closed)+'</dl>'
  +(d.checkedAt?`<p class="muted" style="margin-top:14px">${esc(d.checkedAt)} 확인 · 현장과 다를 수 있어요</p>`:'');
 if(!dlg.open)dlg.showModal();markMenus()}
const toastMsg=m=>alert(m);

// --- 시간표 → 공강 (F3 출결 오늘 수업이 있으면 자동으로 채운다) ----------------------
const tt=document.getElementById('tttext'),ttslots=document.getElementById('ttslots');
const setSel=(sel,v)=>{if(v&&[...sel.options].some(o=>o.value===v)){sel.value=v;return true}return false};
let ttSource=null;
async function loadSchedule(useText){
 const q=new URLSearchParams({weekday:f.weekday.value});if(useText)q.set('text',tt.value);
 const d=await (await fetch(API+'schedule?'+q)).json();ttSource=d.source;
 if(!useText)tt.value=d.text;
 const day=DAYS[d.weekday]+'요일';
 document.getElementById('ttsrc').textContent=d.source==='f3'?`UnivUs 시간표 · ${day}`:(d.source==='manual'?'직접 입력':'UnivUs 시간표 없음 — 직접 적어 보세요');
 document.getElementById('ttedit').open=!d.classes.length;
 timeline(d);
 document.querySelectorAll('.tt [data-i]').forEach(b=>b.onclick=()=>pickSlot(d,+b.dataset.i));
 if(!d.slots.length){ttslots.innerHTML=d.source==='none'?'':`<div class="muted">${d.noTimes?'UnivUs 출결에 과목은 있는데 수업 시간이 아직 없어요 — UnivUs 출결 화면에서 시간표를 넣으면 여기 자동으로 나와요':(d.classes.length?'밥 먹을 공강이 없어요':day+'에는 수업이 없어요')}</div>`;return}
 ttslots.innerHTML=d.slots.map((x,i)=>`<button type="button" class="slot" data-i="${i}"><span>${x.start}~${x.end} · ${x.minutes}분<small>${esc(x.from||'건물 모름')} → ${esc(x.lastOfDay?'수업 끝':(x.to||'건물 모름'))}</small></span><span>→</span></button>`).join('');
 document.querySelectorAll('.tt [data-i]').forEach(b=>b.onclick=()=>pickSlot(d,+b.dataset.i))}

function pickSlot(d,i){pick(d,d.slots[i],`[data-i="${i}"]`)}
function pick(d,x,sel){
 document.querySelectorAll('.tt .on').forEach(o=>o.classList.remove('on'));
 document.querySelectorAll('.tt '+sel).forEach(o=>o.classList.add('on'));
 f.start.value=x.start;f.minutes.value=x.minutes;f.weekday.value=d.weekday;
 if(!setSel(f.from,x.from))setSel(f.from,'캠퍼스 중심');
 if(x.lastOfDay)f.to.value='없음';else if(!setSel(f.to,x.to))setSel(f.to,'캠퍼스 중심');
 run();if(innerWidth<860)document.getElementById('out').scrollIntoView({behavior:'smooth',block:'start'})}
// 하루 시간 막대(9시~20시) — 수업 · 휴강 · 밥 먹을 공강. 수업 없는 빈 시간은 어디든 눌러서 그 공강으로 추천
const T0=9*60,T1=20*60;
function gaps(d){const live=d.classes.filter(c=>c.state!=='canceled').map(c=>({a:mins(c.start),b:mins(c.end),bld:c.building})).sort((x,y)=>x.a-y.a);
 const out=[];let t=T0,prev=null;
 for(const c of live){if(c.b<=T0){prev=c;continue}if(c.a>=T1)break;
  if(c.a>t)out.push({a:t,b:c.a,from:prev?prev.bld:null,to:c.bld,last:false});
  if(c.b>t){t=c.b;prev=c}}
 if(t<T1)out.push({a:t,b:T1,from:prev?prev.bld:null,to:null,last:true});
 return out.map(g=>({start:hm(g.a),end:hm(g.b),minutes:g.b-g.a,from:g.from,to:g.to,lastOfDay:g.last}))}
function timeline(d){const box=document.getElementById('ttline'),span=T1-T0;
 const clip=v=>Math.max(T0,Math.min(T1,v));
 const at=(a,b)=>{a=clip(a);b=clip(b);return `left:calc(${(a-T0)/span*100}% + 2px);width:calc(${(b-a)/span*100}% - 4px)`};
 const room=r=>(r||'').split('-')[0];const G=gaps(d);
 let h='<div class="tl"><div class="tl-bar">';
 G.forEach((g,k)=>{h+=`<button type="button" class="tl-g" data-g="${k}" style="${at(mins(g.start),mins(g.end))}" title="${g.start}~${g.end} 빈 시간 ${g.minutes}분 — 눌러서 추천">${g.minutes>=50?'빈 시간 '+g.minutes+'분':''}</button>`});
 d.classes.forEach(c=>{if(mins(c.end)<=T0||mins(c.start)>=T1)return;const off=c.state==='canceled';
  h+=`<div class="tl-c ${off?'off':''}" style="${at(mins(c.start),mins(c.end))}" title="${esc(c.start+'~'+c.end+' '+(c.name||'')+' '+(c.room||'')+(off?' (휴강)':''))}">${esc(c.name||'수업')}${off?' 휴강':''}<small>${esc(room(c.room)||c.start)}</small></div>`});
 h+='</div><div class="tl-ticks">';
 for(let t=T0;t<=T1;t+=60)h+=`<span style="left:${(t-T0)/span*100}%">${t/60}시</span>`;
 h+='</div><div class="tl-legend"><span><i style="background:#dfe9e4"></i>수업</span><span><i style="box-shadow:inset 0 0 0 1.5px var(--sub)"></i>휴강</span><span>빈 곳 어디든 누르면 그 시간으로 추천</span></div></div>';
 box.innerHTML=h;
 box.querySelectorAll('[data-g]').forEach(b=>b.onclick=()=>pick(d,G[+b.dataset.g],`[data-g="${b.dataset.g}"]`))}
const hm=t=>String(Math.floor(t/60)).padStart(2,'0')+':'+String(t%60).padStart(2,'0');
const mins=t=>{const [a,b]=t.split(':').map(Number);return a*60+b};
document.getElementById('ttgo').onclick=()=>loadSchedule(!!tt.value.trim());
f.weekday.addEventListener('change',()=>{if(ttSource!=='manual')loadSchedule(false)});
loadSchedule(false);
</script></body></html>"""


def run_schedule(text: str, buildings: dict, room_codes: dict, weekday: int | None = None,
                 classes_for=None, has_times=None) -> dict:
    """시간표 → 식사 공강. text 가 있으면 손 입력, 없으면 univ_us F3 출결 시간표에서 그 요일(이번 주) 수업."""
    today = date.today()
    weekday = today.weekday() if weekday is None else weekday
    day = today + timedelta(days=weekday - today.weekday())
    if text.strip():
        classes, source = parse_classes_text(text), "manual"
    else:
        got = (classes_for or f3_classes)(day)
        classes, source = (got or []), ("f3" if got is not None else "none")
    lines = [f"{c['start']}-{c['end']} {c.get('room') or '-'} {c.get('name', '')}{' 휴강' if c.get('state') == 'canceled' else ''}".strip()
             for c in classes]
    codes = {**auto_codes(buildings), **room_codes}
    classes = [{**c, "building": room_building(c.get("room"), buildings, codes)} for c in classes]
    no_times = source == "f3" and not classes and (has_times or f3_has_times)() is False
    return {"source": source, "weekday": weekday, "date": day.isoformat(), "classes": classes, "text": "\n".join(lines),
            "noTimes": no_times, "slots": slots_from_classes(classes, buildings, room_codes)}


def page_html(has_basemap: bool, api: str = "/api/") -> str:
    """추천 화면 HTML. api = 화면이 부르는 계산 주소 앞부분(미리보기 서버 '/api/', UnivUs 앱 '/api/meal/')."""
    return (PAGE.replace("{bands}", PRICE_BANDS_NOTE).replace("{clean}", "true" if has_basemap else "false")
            .replace("{api}", json.dumps(api)))


class MealApp:
    """화면 하나가 쓰는 계산 묶음 — 미리보기 서버(http.server)와 UnivUs 백엔드(FastAPI 어댑터)가 같은 것을 부른다."""

    def __init__(self, survey: Path, candidates: Path, campus: Path, graph: WalkGraph | None = None,
                 buildings: dict[str, tuple[float, float]] | None = None, basemap: bytes | None = None,
                 timetable: bool = True):
        self.survey, self.candidates, self.campus = survey, candidates, campus
        self.graph, self.buildings, self.basemap = graph, buildings or {}, basemap
        self.room_codes = load_room_codes()
        self.timetable = timetable      # False = 내 UnivUs 시간표를 읽지 않는다(--share: 링크로 남에게 보여 줄 때)

    @classmethod
    def default(cls) -> "MealApp":
        """CA1 폴더의 기본 파일들(조사 양식 · 후보 · 캠퍼스 · 길 · 건물 · 바탕 지도)로."""
        return cls(AGENT_DIR / "data" / "survey.csv", AGENT_DIR / "data" / "places.candidates.json", DEFAULT_CAMPUS,
                   WalkGraph.load(), load_buildings(), load_basemap())

    def page(self, api: str = "/api/") -> str:
        return page_html(bool(self.basemap), api)

    def recommend(self, params: dict[str, str]) -> tuple[dict, int]:
        try:
            return run_recommend(params, self.survey, self.candidates, self.campus, self.graph, self.buildings), 200
        except (ValueError, KeyError) as e:
            return {"error": f"입력을 확인해 주세요: {e}"}, 400
        except FileNotFoundError as e:
            return {"error": f"파일이 없습니다: {e.filename} — python -m meal.places 를 먼저 돌려 주세요"}, 500

    def schedule(self, params: dict[str, str]) -> dict:
        wd = params.get("weekday") or ""
        return run_schedule(params.get("text") or "", self.buildings, self.room_codes, int(wd) if wd.isdigit() else None,
                            classes_for=None if self.timetable else (lambda day: None))

    def places(self, params: dict[str, str]) -> dict:
        """식당 둘러보기 목록 — 추천 가능한 곳만, 이름·종류·주소·메뉴로 찾기."""
        def num(k, d, hi):
            v = params.get(k) or ""
            return max(0, min(hi, int(v))) if v.isdigit() else d
        return browse_places(read_text(self.survey), load_coords(self.candidates), params.get("q") or "",
                             params.get("cat") or "", num("offset", 0, 10_000), num("limit", 20, 100))

    def place(self, place_id: str) -> tuple[dict, int]:
        """메뉴·영업시간 상세 창."""
        d = place_detail(read_text(self.survey), load_coords(self.candidates), place_id)
        return (d, 200) if d else ({"error": "그런 식당이 없습니다"}, 404)


def _browse_rows(survey_text: str, coords: dict) -> list[dict]:
    """추천 가능한(조사 완료 · 영업) 줄만, 이름 순. 같은 id 가 두 번 있으면 처음 것."""
    ready = {p.id: p for p in load_survey(survey_text, coords)[0]}
    out, seen = [], set()
    for row in csv.DictReader(io.StringIO(survey_text)):
        rid = (row.get("id") or "").strip()
        if rid in ready and rid not in seen:
            seen.add(rid)
            out.append((row, ready[rid]))
    return sorted(out, key=lambda rp: rp[1].name)


def _place_json(row: dict, p, full: bool) -> dict:
    g = lambda k: (row.get(k) or "").strip()
    menus = parse_menu(g("대표 메뉴와 가격")) if g("대표 메뉴와 가격") else []
    d = {"id": p.id, "name": p.name, "category": p.category, "priceLabel": PRICE_BAND_LABEL.get(p.price_band, ""),
         "address": g("주소"), "hours": g("영업시간"), "closed": g("휴무"), "menu": menus if full else menus[:2]}
    if full:
        d.update({"break": g("브레이크"), "lastOrder": g("라스트오더"), "checkedAt": g("확인일"),
                  "lat": p.lat, "lng": p.lng})
    return d


def browse_places(survey_text: str, coords: dict, query: str = "", category: str = "",
                  offset: int = 0, limit: int = 20) -> dict:
    rows = _browse_rows(survey_text, coords)
    cats = Counter(p.category for _, p in rows)
    q = query.replace(" ", "").lower()
    if category:
        rows = [(r, p) for r, p in rows if p.category == category]
    if q:
        rows = [(r, p) for r, p in rows
                if q in "".join([p.name, p.category, r.get("주소") or "", r.get("대표 메뉴와 가격") or ""]).replace(" ", "").lower()]
    return {"total": len(rows), "offset": offset, "limit": limit, "categories": [c for c, _ in cats.most_common()],
            "items": [_place_json(r, p, False) for r, p in rows[offset:offset + limit]]}


def place_detail(survey_text: str, coords: dict, place_id: str) -> dict | None:
    return next((_place_json(r, p, True) for r, p in _browse_rows(survey_text, coords) if p.id == place_id), None)


def make_handler(survey: Path, candidates: Path, campus: Path, graph: WalkGraph | None = None,
                 buildings: dict[str, tuple[float, float]] | None = None, basemap: bytes | None = None,
                 timetable: bool = True):
    svc = MealApp(survey, candidates, campus, graph, buildings, basemap, timetable)
    page = svc.page().encode("utf-8")
    js = lambda body: json.dumps(body, ensure_ascii=False).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/":
                return self._send(200, page, "text/html; charset=utf-8")
            if url.path == "/api/schedule":
                return self._send(200, js(svc.schedule(params)), "application/json; charset=utf-8")
            if url.path == "/api/basemap.json" and svc.basemap:
                return self._send(200, svc.basemap, "application/json; charset=utf-8")
            if url.path == "/api/places":
                return self._send(200, js(svc.places(params)), "application/json; charset=utf-8")
            if url.path.startswith("/api/places/"):
                body, code = svc.place(unquote(url.path.rsplit("/", 1)[1]))
                return self._send(code, js(body), "application/json; charset=utf-8")
            if url.path == "/api/recommend":
                body, code = svc.recommend(params)
                return self._send(code, js(body), "application/json; charset=utf-8")
            self._send(404, b"not found", "text/plain")

        def log_message(self, *args) -> None:   # 요청 로그를 남기지 않는다(코드 규칙 7)
            pass

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m meal.preview", description="추천 화면 미리보기 (내 PC 에서만)")
    ap.add_argument("--survey", type=Path, default=AGENT_DIR / "data" / "survey.csv")
    ap.add_argument("--candidates", type=Path, default=AGENT_DIR / "data" / "places.candidates.json")
    ap.add_argument("--campus", type=Path, default=DEFAULT_CAMPUS)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--share", action="store_true",
                    help="남에게 링크로 보여 줄 때 — 내 UnivUs 시간표를 읽지 않는다(시간표는 손으로 적는 칸만)")
    args = ap.parse_args(argv)
    graph = WalkGraph.load()
    buildings = load_buildings()
    print("도보: " + (f"길 기준(OSM 길 점 {len(graph.nodes)}개)" if graph else "직선 × 1.3 — 길 기준은 python -m meal.walkgraph fetch"), flush=True)
    basemap = load_basemap()
    print("바탕 지도: " + ("OSM 길·건물(아이콘 없음)" if basemap else "없음 — python -m meal.basemap fetch 후 build"), flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.survey, args.candidates, args.campus, graph, buildings, basemap,
                                                                     timetable=not args.share))
    if args.share:
        print("공유 모드: 내 UnivUs 시간표를 읽지 않는다", flush=True)
    print(f"미리보기: http://127.0.0.1:{args.port}  (끄려면 Ctrl+C)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
