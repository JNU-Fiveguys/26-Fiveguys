# PR #6 지도 배경

`basemap.json`은 팀의 PR #6 커밋 `1f238c837e8934832190c1101e60f30e96fed05a`에 추가된 같은 경로의 파일을 변경 없이 가져온 공개 지도 자료입니다.

- 출처: OpenStreetMap contributors, ODbL 1.0 — https://www.openstreetmap.org/copyright
- 크기: 1,689,555 bytes
- SHA-256: `8be208b47bd9d66f02031726025a1d2e50ec8e9dca44165b292ab11dab2de1b1`
- 좌표 순서: 위도, 경도
- 내용: major 381, minor 2,915, path 2,191, buildings 5,071, green 884, water 21
- 원본 자료 및 생성기는 위 커밋의 `data/osm/base.raw.json`, `data/osm/walk.raw.json`, `meal/basemap.py`에 있습니다. 이 통합에서는 지도를 새로 수집하거나 원본을 재생성하지 않았습니다.

화면은 이 파일을 앱의 `/api/meal/v1/meal/basemap`에서 읽습니다. 추천 및 경로 계산은 별도의 기존 `data/routing/walk_network.json`을 그대로 사용합니다. 지도 그림을 도보 경로 계산 데이터로 취급하지 않습니다.
