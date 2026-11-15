# -*- mode: python ; coding: utf-8 -*-
# 유니버스 사이드카 — univ_us_local/backend/desktop.py 를 실행 파일 하나(onedir)로. 빌드: desktop/sidecar/build.py
#
# 번들 안 배치는 저장소와 같은 모양이다:  _internal/univus/{C0…F6 기능 폴더, univ_us_local/backend/app, univ_us_local/frontend/out}
#   기능 코드는 PYZ(압축 모듈)가 아니라 **원본 .py 파일**로 넣는다 — 기능 config 가 __file__ 기준으로 자기 폴더(번들 자료:
#   F2 rulesets·curriculum, F1 directory, C2 master, F5 web …)와 PROJECT_ROOT 를 찾기 때문이다. 백엔드가 지금처럼
#   sys.path 에 기능 폴더를 붙여 import 한다.
#   대신 그 코드가 쓰는 표준 라이브러리·외부 패키지는 PyInstaller 가 못 보므로, 기능 코드의 import 를 AST 로 훑어 hiddenimports 로 준다.
# 넣지 않는 것: data/·state/·.venv/·tests/, .cmd/.command/.ps1 런처(예약 등록용 register-task.ps1 · C0 OCR 의 ocr_win.ps1 만 넣는다),
#   **.env(LLM 키 등 비밀)**.
import ast
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

REPO = Path(SPECPATH).resolve().parents[1]
AGENTS = ["C0_Platform_agent", "C1_Calendar_agent", "C2_Profile_agent", "C3_Login_agent", "C4_Briefing_agent", "F1_Bachelor_agent",
          "F2_Graduation_agent", "F3_Attendance_agent", "F4_Textbook_agent", "F5_Test_agent", "F6_Eclass_agent",
          "F7_Task_agent", "F8_Plan_agent", "F11_Scholarship_agent", "F12_Activity_agent"]
TREES = [(REPO / a, f"univus/{a}") for a in AGENTS] + [
    (REPO / "campus" / "CA2_Assistant_agent", "univus/campus/CA2_Assistant_agent"),    # CA2 캠퍼스 생활 Q&A·대화
    (REPO / "univ_us_local" / "backend" / "app", "univus/univ_us_local/backend/app"),
    (REPO / "univ_us_local" / "frontend" / "out", "univus/univ_us_local/frontend/out"),
]
SKIP_DIRS = {"data", "state", ".venv", "tests", "__pycache__", "node_modules", ".pytest_cache", "pw-browsers"}
SKIP_FILES = {".env", ".gitignore"}
SKIP_SUFFIX = {".cmd", ".command", ".ps1", ".pyc", ".lnk", ".log", ".db"}
KEEP_FILES = {"register-task.ps1",                         # F1·F6 예약 켜기가 앱 안에서도 이 스크립트를 부른다 (-Command 로 앱 실행 파일)
              "ocr_win.ps1"}                               # F11·F12 수집기의 Windows 내장 OCR (osenv/ocr.py 가 -File 로)

datas, sources = [], []
for src, dest in TREES:
    if not src.exists():
        raise SystemExit(f"없음: {src} (frontend/out 이면 npm run build 먼저)")
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            p = Path(dirpath) / f
            if f not in KEEP_FILES and (f in SKIP_FILES or p.suffix in SKIP_SUFFIX):
                continue
            datas.append((str(p), str(Path(dest) / p.parent.relative_to(src))))
            if p.suffix == ".py":
                sources.append(p)

# 기능 폴더 안의 패키지 이름(osenv·eclass·login·bachelor …) — 원본 파일로 쓰므로 PYZ 에 넣지 않는다.
# osenv 도 마찬가지다: PYZ 에 넣으면 진입점이 import 한 모듈(__init__·appdata)만 들어가고 그것이 원본보다 먼저 잡혀서,
# 나중에 부르는 osenv.creds·launchd 를 못 찾는다(2026-10-08 e클래스 재로그인 ImportError). desktop.py 가 C0 폴더를 sys.path 에 붙인다.
local = {d.name for src, _ in TREES if src.is_dir() for d in src.iterdir() if (d / "__init__.py").exists()}
local |= {"app", "desktop"}
mods = set()
for py in sources:
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module)
hidden = sorted(m for m in mods if m.split(".")[0] not in local)

pw_datas, pw_bins, pw_hidden = collect_all("playwright")       # Playwright 드라이버(node) 포함
binaries = list(pw_bins)
datas += pw_datas
hidden += pw_hidden + collect_submodules("uvicorn")

a = Analysis(
    [str(REPO / "univ_us_local" / "backend" / "desktop.py")],
    pathex=[str(REPO / "C0_Platform_agent")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=sorted(local | {"tkinter", "pytest", "_pytest"}),
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [("X utf8", None, "OPTION")],                        # python -X utf8 (한글 경로·로그)
    exclude_binaries=True,
    name="univus-backend",
    console=True,                                        # 표준입출력으로 Tauri 와 이야기한다 (창은 Tauri 가 숨긴다)
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="univus-backend", upx=False)
