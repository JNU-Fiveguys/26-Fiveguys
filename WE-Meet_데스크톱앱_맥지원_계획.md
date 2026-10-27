# WE-Meet(유니버스) 데스크톱 앱 전환 · macOS 지원 계획

> 작성 2026-10-04 · 근거: 코드 점검(이 문서 2·5절은 코드를 읽고 판단한 것 — 맥 실기기 실측은 아직 없음)
> 관련 문서: [WE-Meet_서버_플랫폼_검토.md](WE-Meet_서버_플랫폼_검토.md)(안 B 로컬 수집 + 얇은 서버), [요구사항정의서.md](요구사항정의서.md)(G5 서버에 올리지 않는 것)

## 0. 요약

| 질문 | 결론 |
|---|---|
| 맥 사용자가 지금 clone 하면? | **시작 단계부터 막힌다.** 런처가 전부 `.cmd`/`.ps1`, C3 python 경로 하드코딩, DPAPI, 작업 스케줄러 전용. 기능 코드 98%는 이미 OS 무관 |
| Windows 판·맥 판을 따로? 하나로? | **하나의 코드베이스 + 얇은 OS 계층.** OS 전용 코드는 Python 약 3만 줄 중 수백 줄(1~2%), 런처 25개 ≈ 26KB |
| 데스크톱 앱 → 나중에 얇은 서버 + 데스크톱 클라이언트까지 한 저장소? | **한 저장소(모노레포)에서 결과물 3개를 따로 빌드** — Windows 앱 · 맥 앱 · 얇은 서버. 한 저장소 ≠ 한 실행 파일 |
| 앱 껍데기 | **Tauri + Python 사이드카** 권장 (Electron 은 Chromium 을 한 벌 더 넣는다) |
| 첫 개발 단계 | OS 공통 계층 `C0_Platform_agent/osenv` — python 경로·프로세스 확인부터 |

---

## 1. 지금 구조 (출발점)

- `univ_us_local/backend` FastAPI(127.0.0.1:8000)가 기능 폴더(C1~C3·F1~F6)의 라우터를 **붙이기만** 하고, `frontend/out`(Next.js 정적 export)을 같은 origin 으로 서빙한다.
- 브라우저가 필요한 일(e클래스 수집·학사정보시스템)은 `C3_Login_agent/.venv`(playwright·Chromium) python 을 **자식 프로세스로** 띄운다. F1 수집은 `F1_Bachelor_agent/.venv`(requests·bs4). 대시보드 서버는 자기 `.venv`. → **venv 3벌**.
- 데이터·상태는 각 기능 폴더의 `data/`·`state/` 에 쌓인다. 위치는 환경변수(`C1_DATA_DIR`·`F6_DATA_DIR`·`C3_STATE_DIR` 등 30여 개)로 바꿀 수 있다.
- 실행·설치·예약은 `.cmd`/`.ps1` 25개 + Windows 작업 스케줄러.

---

## 2. 맥에서 생기는 서비스 제한 (전수)

### 2.1 시작 자체 불가 (모든 기능 공통)
- 런처 25개가 `.cmd`/`.ps1` — `유니버스 열기/종료.cmd`, 각 폴더 `setup.cmd`·`login.cmd`·`sync.cmd`·`run.cmd`·`register-task.ps1`.
- README 설치 가이드가 `py -3.12`·PowerShell·`C:\Projects` 기준.
- 우회: `univ_us_local/backend` 에서 venv 수동 생성 → `uvicorn app.main:app --port 8000` (대시보드 자체는 뜬다).

### 2.2 코드 수정 없이는 불가
**① C3(학교 로그인)에 기대는 기능 — 가장 큼.** venv python 경로가 `.venv/Scripts/python.exe` 로 하드코딩(맥은 `.venv/bin/python`) → C3 를 맥에 설치해도 대시보드는 "설치되지 않음"으로 판단.
- 하드코딩 위치: `C3_Login_agent/login/config.py`(PYTHON), `C2_Profile_agent/student/config.py`·`F2_Graduation_agent/graduation/config.py`·`F6_Eclass_agent/eclass/config.py`(C3_PYTHON)
- 막히는 것: 로그인 창 열기 · F6 e클래스 동기화 버튼 · C2 학사정보시스템 가져오기(명령줄 `import` 포함) · F2 기이수성적 가져오기
- 연쇄: 캘린더 e클래스 마감 · 할 일 · 마감 D-3/D-1 알림 · F4 강의자료 목록 · F5 공지에서 시험 찾기 · F3 e클래스 공지 자동 휴강

**② 자동 재로그인(자격증명 저장).** → 3단계에서 해결(맥 키체인). `C3_Login_agent/login/auth.py` 가 `ctypes.windll.crypt32`(DPAPI) 직접 호출 → `setup-creds`·`login --auto` 불가, 세션 만료마다 수동 로그인. notice_agent 자동 재인증도 같다. `from ctypes import wintypes` 는 Python 버전에 따라 맥에서 import 단계 오류 가능성 있음(실측 필요).

**③ 예약 실행.** → 3단계에서 해결(launchd). 작업 스케줄러(PowerShell) 전용, `sys.platform == "win32"` 아니면 꺼짐(`F6 jobs.tasks_enabled`, `F1 config.tasks_enabled`).
- 없어지는 것: F6 정각 4시간 자동 수집 · 놓친 주기 따라잡기 · 5·15·45분 재시도 / F1 매일 08:00 + 재시도 / notice_agent 주기 실행
- 화면: "이 서버에서는 작업 스케줄러를 쓰지 않습니다"

### 2.3 수동 설정하면 되는 것
- **F1 학사일정 동기화 버튼**: 맥 분기(`F1 runner._command` → `.venv/bin/python`)는 있으나 venv 자동 생성(run-sync.cmd 몫)이 없다. `F1_Bachelor_agent/.venv` 를 직접 만들지 않으면 백엔드 python 으로 돌아 `requests` 없음으로 실패.
- **F6 수집 명령줄**: C3 venv 수동 생성 + Chromium 을 `.venv/pw-browsers` 에 설치 → `python -m login`, `python -m eclass sync` 직접 실행. 데이터가 생기면 F6·F4·F5 화면은 보인다. 버튼은 여전히 불가.

### 2.4 설치 방식 의존
- python.org 맥 설치판 + `Install Certificates.command` 미실행이면 `urllib` 기본 SSL 컨텍스트가 `CERTIFICATE_VERIFY_FAILED` → **C2 학과 목록(교육과정검색) · F2 교육과정 받기 · F3 시간표 자동 가져오기** 실패. Homebrew python 은 대체로 무방. F1 은 requests + truststore 라 무관.

### 2.5 맥에서도 그대로 되는 것
C1 일정·할 일 · F2 계산(수동 입력·기본 룰셋) · F3 출결 기록(시간표 수동) · F5 시험 직접 추가·역산 · F4 열람(데이터가 있으면) · 앱 내 알림(OS 알림 미사용) · 프론트 개발 · 프로세스 확인(`os.kill`/`pgrep` 분기 이미 있음).
- F6 가 manifest·assignments 에 적는 상대경로(`data\<과목>\…`)는 읽는 쪽(F3·F4·F5·F6 feed)이 전부 `replace("\\", "/")` 로 정규화한다 → 맥에서 `/` 로 적혀도 그대로 읽힌다.
- F4 하드링크(`os.link`)는 APFS 에서도 된다(같은 볼륨일 때).

### 2.6 동작하지만 혼란스러운 것
- 화면·오류 문구가 `.cmd` 와 역슬래시 경로를 안내 (`Header.tsx` "run.cmd 가 실행 중인지", `AppData.tsx` `F1_Bachelor_agent\state\sync.log`, C3 `problem()` 문구).
- notice_agent 초안 열기는 맥에서 경로만 출력.

---

## 3. 결정 ① — Windows/맥 하나의 코드베이스

| 항목 | 따로 만들기 | 하나로 합치기 |
|---|---|---|
| 파일 크기 | 거의 같은 코드 두 벌 | 맥 런처·키체인·launchd 를 더해도 수십 KB. 무거운 것(Chromium·venv)은 원래 커밋하지 않음 |
| 실행 속도 | 같음 | 같음 (`sys.platform` 분기는 시작 시 한 번, 수집 속도는 요청 간격 1.5초가 결정) |
| 설치 크기 | 같음 | 같음 (각 OS 는 자기 Chromium·패키지만 받는다) |
| 유지보수 | 고칠 때마다 두 번, 금방 어긋남 | 한 번 |
| 데이터 호환 | DB·JSON 형식이 어긋날 수 있음 | 항상 같음 |
| 협업 | 승인·리뷰 두 군데 | 지금 main 룰셋 그대로 |
| 초기 비용 | 복사라 쉬움 | 공통 계층 한 번 |

따로 만들기가 나은 경우는 "맥 판을 한 번 만들고 다시 안 고칠 때" 뿐 — 해당 없음.

**OS 별로 달라지는 것 (공통 계층이 숨긴다)**

| | Windows | macOS |
|---|---|---|
| venv python | `.venv\Scripts\python.exe` | `.venv/bin/python` |
| 자격증명 | DPAPI (기존 `cred.bin` 호환 유지) | 키체인 (`keyring` 또는 `security`) |
| 예약 실행 | 작업 스케줄러 | launchd (`~/Library/LaunchAgents`) — 앱 단계에서는 앱 내장 스케줄러로 통일 검토 |
| 프로세스 확인 | `tasklist` | `os.kill(pid, 0)` / `pgrep` |
| 런처 | `.cmd` | `.command`(더블클릭) / `.sh` |

런처는 **venv 만들고 `python -m …` 를 부르는 몇 줄**로만 두고 로직은 Python 한 곳에 둔다.

---

## 4. 결정 ② — 데스크톱 앱 → 얇은 서버 + 데스크톱 클라이언트도 한 저장소

### 4.1 지금 구조가 맞는 점
| 현재 설계 | 데스크톱 앱 | 얇은 서버 |
|---|---|---|
| 정적 export 프론트 + 로컬 FastAPI | 앱 창(웹뷰)이 그대로 띄움, 화면 재작성 불필요 | F16·F17 화면도 같은 컴포넌트·디자인 시스템 |
| 기능 폴더 분리 + 라우터만 붙이는 `main.py` | 그대로 | 필요한 라우터만 골라 붙임 |
| 데이터 경로 환경변수 | 사용자 데이터 폴더로 이전 쉬움 | — |
| G5(서버에 올리지 않는 것) | — | 업로드 스키마를 클라이언트·서버가 **같은 PR 에서** 고치고 테스트 |

### 4.2 크기·속도 (대략값 — 빌드 실측 아님)
| 구성 | 크기 | 비고 |
|---|---|---|
| 앱 껍데기 **Tauri** | 약 5~10MB | OS 웹뷰(Windows WebView2 · 맥 WKWebView) — **권장** |
| 앱 껍데기 Electron | 약 80~120MB | Playwright Chromium 과 중복 |
| Python 런타임 + 패키지(PyInstaller, Playwright 드라이버 포함) | 약 60~100MB | venv 3벌 → 1벌로 |
| Playwright Chromium | 다운로드 약 150MB / 설치 후 약 300MB | 첫 실행 때 받기(현행) 유지, 또는 설치된 Edge·Chrome 사용(`channel="msedge"` — Edge 는 Windows 기본 탑재) |

- 앱 창 ↔ 로컬 백엔드는 루프백이라 1ms 미만. PyInstaller 는 **onedir**(onefile 은 실행마다 압축 해제로 수 초 느림).
- Tailwind v4 는 Safari 16.4+ 필요 → 맥 WKWebView 기준 macOS 13.3+ 를 최소 사양으로 잡는다(확인 필요).
- 서버 코드는 앱에 안 들어가고 크롤러는 서버에 안 들어간다 — 한 저장소여도 결과물 크기에 영향 없음.

### 4.3 앱 포장 전에 고칠 것 (우선순위)
1. **Origin 거절** — `univ_us_local/backend/app/config.py` `ALLOWED_ORIGINS`/`ALLOWED_HOSTS` 가 localhost 만 허용. Tauri origin(`http://tauri.localhost`, `tauri://localhost`)의 변경 요청이 403. → 허용 + **실행마다 바뀌는 토큰** 검사(같은 PC 다른 프로그램의 로컬 API 호출 차단).
2. **포트 8000 고정** — 충돌 시 앱이 안 뜸. 빈 포트를 골라 앱 창에 알려 준다(`UNIVUS_PORT` 는 이미 있음).
3. **프로세스 이름 "python" 의존** — `pid_alive`(F1 runner·pipeline, F6 runs)의 `"python" in out.lower()`, F6 jobs 의 CIM `Name like 'python%'`. 포장하면 이름이 `UnivUs.exe` 등으로 바뀌어 잠금이 깨진다.
4. **venv 3벌 + python 경로 하드코딩** — 실행환경 1벌, 자식 프로세스는 "같은 실행 파일을 다른 모드로" 재실행.
5. **데이터를 저장소 폴더에 씀** — 설치 폴더(Program Files, `.app` 내부)는 권한 오류·맥 코드서명 깨짐. 앱 시작 시 환경변수로 `%LOCALAPPDATA%\kr.univus.desktop\data` / `~/Library/Application Support/kr.univus.desktop/data` 지정. F6·F4 데이터는 같은 볼륨에 둬야 F4 하드링크가 산다.
6. **예약 실행이 저장소 `.cmd` 경로를 등록** — 앱 로그인 항목 + 트레이 상주 스케줄러로.
7. **DPAPI 전용** — `keyring`(Windows 자격 증명 관리자·맥 키체인) + 기존 `cred.bin` 1회 이전. **`auth.py` 의 ENTROPY·설명 문자열 `"eclass-agent"` 는 절대 바꾸지 않는다.**
8. **업데이트·빌드** — `git pull` → 자동 업데이트(Tauri updater), `frontend/out` 커밋 → CI 빌드.

### 4.4 얇은 서버를 붙일 때
- 프론트 API 주소가 `"/api"` 하나로 고정(`frontend/src/lib/api.ts`) → **로컬 클라이언트(개인 데이터) / 원격 클라이언트(F16·F17·배달)** 로 분리.
- 같은 백엔드를 `MODE=local/server` 로 돌리지 않는다 — 서버 프로세스에 크롤러·자격증명 코드가 들어갈 여지가 생기면 G5 가 코드로 보장되지 않는다. **서버는 별도 앱, 공유는 업로드 스키마뿐.** CI 에서 `server/` 가 `C3_*`·`F6_*` 를 import 하면 실패.
- LLM API 키는 앱 배포 순간 사용자 PC 에 노출 → 서버 프록시로(검토 문서 8.2).
- 저장소를 나눌 유일한 이유: 지금 저장소가 **Public**. 서버 배포 설정·운영 코드를 비공개로 하려면 그 부분만 private 저장소.
- 서버 식별은 익명 기기 키 + 표시 이름(G5) 유지.

---

## 5. 목표 구조 (지금 폴더는 옮기지 않고 추가만)

```
C0_Platform_agent/osenv/   OS 차이 계층 (python 경로 · 프로세스 확인 · 이후 자격증명 · 예약 · 데이터 폴더)
C1~C3 · F1~F6 _agent/      기능 폴더 (그대로)
univ_us_local/backend      로컬 백엔드 (앱에 들어감)
univ_us_local/frontend     화면 (앱·서버 공용)
shared/schema/             클라이언트 ↔ 서버 업로드 형식 (서버 단계)
desktop/                   Tauri 껍데기 + Python 사이드카 빌드 설정
server/                    얇은 서버 (나중에)
```

- 폴더를 옮기면 하드코딩 경로가 줄줄이 깨지므로 **추가만** 한다.
- 패키지 이름을 `platform` 으로 하면 표준 라이브러리 `platform` 을 가린다(C1 의 `calendar` 와 같은 함정) → **`osenv`**.
- 다른 기능 폴더를 빌려 쓰는 기존 방식(폴더 경로 환경변수 + `sys.path.insert`)을 그대로 따른다: `C0_AGENT_DIR`.

---

## 6. 단계별 계획 (체크리스트)

### 1단계 — OS 공통 계층 + 경로 정리 (맥에서 버튼 기능 살리기) ← **시작**
- [x] `C0_Platform_agent/osenv` — `venv_python()`, `pid_alive()`, `NO_WINDOW`, `IS_WINDOWS`/`IS_MAC`
- [x] C3 `PYTHON`, C2·F2·F6 `C3_PYTHON` → `venv_python()`
- [x] F1 runner·pipeline, F6 runs 의 `pid_alive` 3벌 → `osenv.pid_alive`
- [x] F1 runner 맥 분기 → `venv_python()`
- [x] 화면·오류 문구의 `.cmd`·역슬래시 안내를 OS 중립으로 (2단계에서 처리 — 백엔드 문구는 `osenv.script()`, 화면은 `/` 경로·"유니버스 열기")

### 2단계 — 맥 런처·설치
- [x] `.cmd` 와 1:1 인 `.command` 16개 — 루트 `유니버스 열기/종료`, `univ_us_local/backend/run`, C3 `setup`·`login`, F1 `setup`·`sync`·`run`, F6 `sync`·`run`, C1·C2·F2·F3·F4·F5 `run`
- [x] 공용 셸 함수 `C0_Platform_agent/univus.sh`(python 3.10+ 찾기 → `python -m osenv venv`) — 로직은 Python 쪽
- [x] `osenv.venv.ensure()` — venv·패키지·Chromium, requirements 해시로 바뀌었을 때만 다시 설치
- [x] F1 학사일정 동기화 버튼: 맥에서 `.venv` 를 `ensure()` 로 자동 생성(pip 출력은 sync.log)
- [x] `truststore` — 백엔드 requirements + C2 master_crawl · F2 curriculum · F3 timetable 에 F1 과 같은 주입 (없으면 그대로)
- [x] C3 `auth.py` 가 맥에서 import 되게(`wintypes` 제거, DWORD=c_ulong 그대로 — ENTROPY·설명 문자열 불변, 기존 cred.bin 복호화 확인), `setup-creds` 는 맥에서 안내 후 종료
- [x] README 0-1절(맥), `.gitattributes` `*.command`·`*.sh` eol=lf, 실행 권한(100755), `server.log` gitignore
- [ ] 맥 실기기에서 처음부터 끝까지(설치 → 로그인 → 수집 → 대시보드) — 팀 맥 사용자
- [ ] notice_agent 맥 런처 (선택 기능이라 미룸)

### 3단계 — 자격증명·예약
- [x] `osenv.creds` — DPAPI 호출을 auth.py 에서 그대로 옮김(ENTROPY·설명 문자열은 auth.py 에 그대로, 기존 cred.bin 복호화 확인) + 맥 로그인 키체인(keyring, 맥에서만 설치)
- [x] C3 `auth.py` — Windows `state/cred.bin` / 맥 키체인 + `state/cred.keychain.json`(항목 이름만 — 백엔드가 파일로 hasCreds 판단), `setup-creds.command`
- [x] `osenv.launchd` — `~/Library/LaunchAgents/kr.univus.f6-eclass-sync.plist`·`kr.univus.f1-academic-sync.plist`, 시각마다(StartCalendarInterval) + 로그인 시(RunAtLoad), 재시도·겹침 방지는 기존 tick
- [x] F6 jobs · F1 runner — `tasks_enabled()` Windows·맥 둘 다, 맥이면 task_info·register·unregister 가 launchd 로 (화면·API 모양은 그대로). 화면 문구 "작업 스케줄러" → "예약 실행"
- [x] GitHub Actions `macOS` 워크플로 — 실제 macOS 러너에서 단위 테스트 + 런처 문법·권한·줄바꿈 + `run.command` 로 서버 띄워 `/api/status`
- [ ] 맥 실기기 검증 (팀 맥 사용자) — 키체인 허용 창, launchd 가 잠자기 뒤·로그인 시 실제로 tick 을 부르는지, Gatekeeper 경고

### 4단계 — 데스크톱 앱 (Tauri)
- [x] 데이터 폴더 → `osenv.appdata`: `%LOCALAPPDATA%\kr.univus.desktop\data` / `~/Library/Application Support/kr.univus.desktop/data` 에 저장소와 같은 모양(앱 식별자 폴더 — 설치 폴더 `%LOCALAPPDATA%\UnivUs` 와 분리, 제거할 때 '앱 데이터 삭제'가 지우는 곳)(F6 상대경로 규칙 유지). 기존 데이터 이전은 자동이 아니라 `python -m osenv migrate-data`(복사·덮어쓰지 않음 — SQLite 를 두 실행 방식이 같이 열지 않게)
- [x] 실행 토큰·빈 포트 — Origin 을 늘리는 대신 **창이 사이드카와 같은 origin(127.0.0.1:<빈 포트>)을 연다**. `/desktop/launch?code=` 한 번 쓰는 코드 → HttpOnly·SameSite=Strict 세션 쿠키(맥은 Lax — WKWebView 가 tauri:// 시작 화면에서 넘어온 303 에 Strict 쿠키를 붙이지 않아 첫 화면이 403 이 됐다, 2026-10-27), 쿠키 없으면 모든 요청 403. 저장소 실행은 검사 없음(그대로)
- [x] 실행환경 1벌 — `desktop.py` 를 PyInstaller onedir(`univus-backend`, 약 153MB)로. 기능 코드는 원본 .py 로 넣고 import 를 AST 로 훑어 hiddenimports. 자식 프로세스는 `osenv.module_cmd` → `<실행 파일> --run-module eclass sync`. pid_alive·프로세스 찾기(CIM/pgrep)·작업 경로 확인이 묶인 실행 파일 이름도 안다. 작업 스케줄러는 `register-task.ps1 -Command` 로 앱 실행 파일을 등록
- [x] Chromium — **첫 실행 때 앱 데이터 폴더로 백그라운드 다운로드**(지금까지 검증된 Playwright Chromium·신뢰 기기 쿠키를 그대로 쓰려고. Edge 채널은 지문이 달라져 2차 인증이 다시 뜰 수 있다). 받는 동안 '내려받는 중' 안내(`osenv.chromium_state`)
- [x] 트레이 상주(창 닫기 = 숨기기) · 로그인할 때 실행(autostart, `--hidden`) · 단일 인스턴스 · 바깥 링크는 기본 브라우저 · 종료 시 사이드카 정리(stdin 닫기 → 5초 뒤 강제)
- [ ] 코드 서명(Windows SmartScreen · 맥 공증) — 인증서·Apple 개발자 계정이 필요해 사용자 결정
- [ ] 자동 업데이트(tauri-plugin-updater) — 서명 키·배포 위치(GitHub Releases) 결정 필요
- [x] `frontend/out` 커밋 중단 — 사이드카 빌드(`build.py`)가 `npm run build` 부터 한다 (2026-10-08)
- [x] CI 에서 설치 파일 빌드 — `release.yml`: 태그 `v*` → Windows nsis·맥 dmg(Apple Silicon, ad-hoc 서명) → Releases 초안 (2026-10-09)

### 5단계 — 얇은 서버 + 데스크톱 클라이언트
- [ ] `shared/schema` 업로드 형식, `server/` 별도 앱(FastAPI + Postgres)
- [ ] 프론트 API 클라이언트 로컬/원격 분리
- [ ] CI import 경계 검사(서버 → C3·F6 금지), LLM 키 서버 프록시
- [ ] 서버 코드 공개 범위 결정(Public 저장소)

---

## 7. 지키는 선 (변경 금지)
- `C3_Login_agent/login/auth.py` 의 `ENTROPY` 와 `CryptProtectData` 설명 문자열 `"eclass-agent"` — 바꾸면 저장된 비밀번호를 못 푼다.
- `state/`·`data/` 는 어떤 OS·어떤 앱 단계에서도 커밋·업로드 금지. 서버에는 SSO 자격증명·세션·강의자료·일정 제목·성적·이름/학번을 올리지 않는다(G5).
- F6 `REQUEST_INTERVAL`(1.5초) 줄이지 않기.

## 8. 진행 기록
- 2026-10-04 — 맥 제한 전수 점검, 하나의 코드베이스·모노레포 결정, 이 문서 작성. 브랜치 `feat/desktop-mac-platform`.
  - 1단계 착수: `C0_Platform_agent/osenv` 추가(`venv_python`·`pid_alive`·`NO_WINDOW`, 테스트 4개).
  - C3 `PYTHON`, C2·F2·F6 `C3_PYTHON` → `venv_python()`. F1 runner 맥 분기도 `venv_python()`.
  - `pid_alive` 3벌(F1 runner·pipeline, F6 runs) → `osenv.pid_alive` 하나. Windows `tasklist` 호출에 `NO_WINDOW` 를 붙였다(대시보드에서 부를 때 콘솔 창 깜빡임 방지).
  - 각 config 는 `C0_AGENT_DIR`(환경변수로 바꿀 수 있음)를 `sys.path` 에 **append** 하고 `osenv` 를 import.
  - 검증(Windows): C0 4 · C2 22 · F1 51 · F2 60 · F3 74 · F4 57 · F5 164 · F6 54 · notice 34 = **520 통과**. 백엔드 import·API 82개 경로·C3 `login_status`(installed=True) 정상. 맥 실기기 검증은 아직 없음.
- 2026-10-04 — **2단계(맥 런처·설치)**. 같은 브랜치.
  - `osenv/venv.py`(ensure) · `osenv/__main__.py`(`python -m osenv venv|python`) · `osenv.script()` · `univus.sh` · `.command` 16개(100755, LF).
  - F1 runner `_prepare()` — Windows 는 run-sync.cmd 그대로, 그 외 OS 는 `ensure()` 후 `.venv/bin/python -m bachelor sync`.
  - 백엔드에 보이는 안내 문구(C3 problem·C2/F2 jobs·C2 hakstd·F6 collect·C3 session)를 OS 별 런처 이름으로. 화면 문구 4곳(Header·AppData·EventDetailHost·EclassFeedPage) 수정 후 `npm run build` → `frontend/out` 갱신.
  - 검증(Windows): C0 8 · C2 22 · F1 51 · F2 60 · F3 74 · F4 57 · F5 164 · F6 54 · notice 34 = **524 통과**, `tsc --noEmit`·`next build` 통과, `python -m osenv venv` 실제 생성·재실행 건너뛰기 확인, 기존 cred.bin 복호화·DPAPI 왕복 확인, 셸 스크립트 `bash -n` 통과.
  - 맥에서 남는 제한(3단계): 자동 재로그인(DPAPI), 예약 실행(작업 스케줄러), notice_agent. Windows 백엔드 `.venv` 는 처음 만들 때만 설치하므로 기존 PC 엔 truststore 가 없다 — 없으면 예전 동작 그대로라 문제없음.
- 2026-10-04 — **3단계(자격증명·예약)**. 같은 브랜치.
  - `osenv/creds.py`(DPAPI 이전 + 키체인), `osenv/launchd.py`, C3 `auth.py`·`creds.py`·`config.py`(CRED_FILE OS 별), C3 requirements `keyring; sys_platform == "darwin"`, `setup-creds.command`.
  - F6 `jobs.py`·F1 `runner.py` 맥 분기(launchd), `LAUNCHD_LABEL`. plist 에는 격리용 환경변수만 넘긴다(F6: C0/C3/F6 폴더, F1: C0/C2/F1 폴더·F1_SCHEDULE_AT).
  - 테스트 추가: C0 launchd·키체인 4 · C3 auth 3 · F6 맥 예약 2 · F1 맥 예약 1 (launchctl·keyring 은 가짜).
  - 검증(Windows): C0 12 · C3 3 · C2 22 · F1 52 · F2 60 · F3 74 · F4 57 · F5 164 · F6 56 · notice 34 = **534 통과**, `tsc`·`next build` 통과. 이 PC 의 작업 스케줄러 상태(F6·F1 registered·pathOk)와 저장된 자격증명 읽기가 그대로인 것 확인.
  - 맥 실측은 아직 없다 — macOS 워크플로가 push 되면 러너에서 처음 돈다. 남은 맥 제한: notice_agent.
- 2026-10-05 — **4단계(데스크톱 앱)**. 브랜치 `feat/desktop-app` (main 에서, PR #3 머지 뒤).
  - C0: `osenv.appdata`(앱 데이터 폴더·`migrate-data`), `FROZEN`·`module_cmd`·`python_ready`·`browsers_dir`·`chromium_state`, pid_alive 가 묶인 실행 파일 이름도 앎.
  - C2·F2 가 C3 state 를 `C3_AGENT_DIR/state` 로 직접 짚던 것을 `C3_STATE_DIR` 규칙으로(맥 자격증명 표시 이름도). F2 의 C2 hakstd state 도 `C2_STATE_DIR`.
  - F6·C2·F2·F1 자식 프로세스 → `module_cmd`, F6 프로세스 찾기(CIM/pgrep)가 `--run-module` 도 앎, 작업 스케줄러 `register-task.ps1 -Command`(F6·F1), 경로 확인(pathOk)이 묶인 앱이면 실행 파일 기준.
  - 백엔드: `/desktop/launch` + 세션 쿠키 미들웨어(desktop.py 가 띄울 때만). `univ_us_local/backend/desktop.py` 사이드카 진입점(`--run-module`·준비 줄·stdin 닫히면 종료·첫 실행 Chromium·기본 로그).
  - `desktop/`: PyInstaller spec(원본 .py + AST hiddenimports, `.env` 제외)·build.py, Tauri 2.12 껍데기(main.rs — 사이드카·트레이·자동 실행·단일 인스턴스·바깥 링크·종료 정리), 시작 화면, 아이콘(표준 라이브러리로 만든 원본 → `tauri icon`).
  - 산출물(Windows): 사이드카 onedir 약 153MB, **설치 파일 `UnivUs_0.1.0_x64-setup.exe` 45.8MB**(NSIS, 현재 사용자 설치·관리자 권한 불필요).
  - 검증(Windows): 테스트 C0 16 · C3 3 · C2 22 · F1 52 · F2 60 · F3 74 · F4 57 · F5 164 · F6 58 · 백엔드 세션 1 · notice 34 = **541 통과**.
    묶인 사이드카: 준비 1.9초, 쿠키 없으면 403·실행 코드 1회·잘못된 쿠키 403, `--run-module` 로 C1·F6·playwright 실행, 예약 실행처럼 환경변수 없이 불러도 앱 데이터 폴더를 잡음.
    **실제 앱 실행**(임시 LOCALAPPDATA): UnivUs.exe → 사이드카 자식 프로세스, 로그에 '앱 창이 연결됨', 바깥 요청 403, DB 가 앱 데이터 폴더에, 앱을 강제 종료하면 사이드카도 끝남.
  - 확인 못 한 것: 창 안 화면을 눈으로 보기(데스크톱 창 캡처 도구 없음), 트레이 메뉴·창 닫기→숨기기·자동 실행 클릭, 맥 Tauri 빌드, 설치 파일로 설치해 보기, 첫 실행 Chromium 다운로드(시험에서는 껐다).
  - 남은 것: 코드 서명 · 자동 업데이트 · CI 설치 파일 빌드(사용자 결정 필요 — 4단계 체크리스트).
- 2026-10-08 — **데스크톱 앱 전용으로 전환** (사용자 결정: 로컬 서버 웹·저장소 실행 방식은 없앤다). 브랜치 `feat/desktop-only-dev`.
  - 데이터: 저장소 `data/`·`state/` → 설치된 앱의 데이터 폴더로 복사(`migrate-data`), **앱 데이터 폴더가 원본**. 저장소 원본은 저장소 밖 백업 폴더로 옮김(삭제는 사용자).
    Claude 데스크톱 앱(MSIX) 안에서 띄운 프로세스가 `%LOCALAPPDATA%` 에 쓰는 것은 패키지 가상 폴더로 가서, 이전·설치는 WMI 로 패키지 밖에서 다시 했다.
  - 예약 작업(F6·F1)을 앱 실행 파일로 다시 등록(사용자가 앱에서 `켜기`). spec 이 `.ps1` 을 전부 빼서 앱의 `켜기`가 실패하던 것 수정(`register-task.ps1` 만 넣음).
  - **개발 모드** `desktop/` 에서 `npm run dev` — 디버그 빌드가 `desktop.py --dev --port 8020 --frontend-url http://127.0.0.1:3000 --data-root desktop/.dev-data` 를 띄우고
    창은 next dev(즉시 반영)를 연다(`/desktop/launch`·`/api` rewrites). 식별자 `kr.univus.desktop.dev`, 예약 실행 꺼짐.
  - **venv 하나**: `desktop/sidecar/.venv`(빌드·개발·테스트). `osenv.module_cmd(mod, …)` 는 지금 도는 python(묶인 앱이면 같은 실행 파일), `browsers_dir()` 는 앱 데이터 폴더 / venv 의 `pw-browsers`,
    `osenv.task_command` 로 작업 스케줄러 명령 공용화. `python_ready`·`script()`·C3 `PYTHON`·C2/F2/F6 `C3_PYTHON`·`C3_VENV`·F1 `_prepare`·C1 옛 DB 옮기기·`migrate-data`(→ `copy-data --to`) 제거.
  - 없앤 것: 런처 43개(`유니버스 열기/종료`, 기능별 `run`·`setup`·`sync`·`login`·`setup-creds`·`run-sync`·`run-scheduled`, `univus.sh`), `univ_us_local/backend/requirements.txt`,
    `.claude/launch.json`(브라우저 미리보기), `frontend/out` 추적(.gitignore). `register-task.ps1` 은 `-Command` 필수. `notice_agent` 는 참고 코드로 그대로 둠.
  - 새로 만든 것: 앱의 **자동 로그인 정보** 저장·지우기(`C3_Login_agent/login/api.py` `/api/login/creds` + 수집 원천 화면 모달 `?creds=1`) — 비밀번호는 돌려주지 않는다.
    개발용 명령줄 `desktop/cli.py <패키지> …`(개발 데이터 / `--app`). macOS CI 를 런처 검사 → 단일 venv 테스트 + 사이드카 빌드·실행(준비 줄·403)으로.
  - F1 `task_info.scheduled` 가 `run-scheduled.cmd` 문자열을 찾아, 앱이 등록한 작업을 늘 '옛 등록'으로 보던 것 수정(`bachelor tick`).
  - (같은 날) 설치본 e클래스 수집이 세션 만료 → 재로그인 단계에서 `ImportError: cannot import name 'creds' from 'osenv'` — spec 이 osenv 만 PYZ 에 넣어 진입점이 import 한 모듈만 묶였다. osenv 도 원본으로(excludes), `desktop.py --self-check`(모든 모듈 import)를 build.py 마지막에. 설치본에서 재로그인 → 수집 성공 확인.
  - (같은 날) 마지막 점검: 묶인 사이드카 ↔ 소스 백엔드 API 135개 일치, 기능별 available 9개 참, 화면 20개·공부 캘린더 200, F1 학사일정 동기화(자식 프로세스) 성공, 데이터가 있는 상태에서 기능마다 실제 응답·F4 원문 열기/내려받기 확인. 앱 창 차이로 F4 '파일 끌어다 추가'가 Windows 에서 막히던 것 → `disable_drag_drop_handler()`.
  - (같은 날) 앱 창에서 '원문 보기'(target="_blank" 링크 — e클래스·학사일정·강의자료 원문)가 열리지 않던 것: tauri-plugin-opener 가 넣는 스크립트가 그 클릭을 막고 IPC 로 열려 했는데 대시보드에는 Tauri API 가 없어 조용히 실패. `open_js_links_on_click(false)` 로 끄고 on_new_window 가 받게 — 바깥 주소는 기본 브라우저, 로컬 주소는 새 앱 창. WebView2 원격 디버깅(CDP)으로 개발 모드·설치본 둘 다 확인.
- 2026-10-09 — 배포: `.github/workflows/release.yml`(태그 → tauri-action → Releases 초안, 태그=tauri.conf 버전 검사), 맥 `signingIdentity: "-"`(ad-hoc). README 를 사용 안내로, 개발 내용은 `DEVELOPMENT.md` 로 나눔.
