# 26-Fiveguys — 유니버스 캠퍼스 에이전트

전남대 학생의 **학사**(e클래스 과제·학사일정·졸업요건·출결·시험·장학)와 **캠퍼스 생활**(식사·공부 자리·이동·생활 질문)을 함께 돕는 AI 에이전트입니다.
2026학년도 2학기 **산학협력프로젝트(캡스톤디자인) Five guys** 팀 저장소입니다.

| | 학사 에이전트 | 캠퍼스 에이전트 |
|---|---|---|
| 무엇 | 내 PC 의 유니버스(Univ-Us) 데스크톱 앱 | 폰에서 쓰는 캠퍼스 앱(PWA) + 캠퍼스 서버 |
| 어디서 왔나 | WE-Meet 프로젝트 [AIM-Output/26-WE-Meet-Project](https://github.com/AIM-Output/26-WE-Meet-Project) 를 **포크해서 그대로** 받아 쓴다 | 이 저장소에서 **새로 만든다** — 전부 [`campus/`](../campus/) 안 |
| 상태 | 동작 중 (v0.2.0) | 기능 정의 완료 · 개발 시작 |

> 학사 앱을 설치해서 쓰는 방법은 루트의 [README.md](../README.md), 학사 앱 개발은 [DEVELOPMENT.md](../DEVELOPMENT.md) — 둘 다 원본 문서 그대로입니다.
> 이 문서(`.github/README.md`)가 저장소 첫 화면에 보이는 **프로젝트 소개**입니다.

---

## 한눈에

```
[유니버스 PC 앱 — 학사]                 [캠퍼스 서버]                         [캠퍼스 앱 — 폰]
 e클래스·시간표·시험 (PC 안에만)           공개 데이터 수집 (날씨·학식·식당·건물)    홈 / 식사 / 에이전트 / 설정
   └ 오늘 맥락 ──────────────────▶        추천 계산 · 생활 지식 검색 · LLM     ◀──▶  위치·취향·대화는 폰에만 저장
     (제목 없는 시각 + 건물 코드)          API 키 보관 · 푸시
```

- **학사는 PC, 캠퍼스는 폰.** 과제·학사 관리는 PC 앞에서, 식사·이동은 돌아다니며 정한다.
- 두 쪽을 잇는 것은 **오늘 맥락** 하나뿐이다 — 오늘의 수업·시험·내 일정 **시각**과 수업 **건물 코드**. 일정 제목·과목명·성적·이름은 보내지 않는다.

## 기능

| 구분 | 기능 | 상태 |
|---|---|---|
| 학사 (PC 앱) | e클래스 과제·마감 · 학사일정 · 졸업요건 · 출결 · 강의자료 · 시험 공부 계획 · 과제 우선순위 · 공강 학습 플랜 · 장학 매칭 · 대외활동 · 아침 브리핑 | 원본에서 동작 중 |
| **CA1** | 공강 맞춤 식사 추천 — 이번 공강에 **다녀올 수 있는** 식당·학식 3곳 (공강·다음 강의실·예산·날씨·취향) | 정의 완료 · 개발 예정 |
| **CA2** | 공부 자리 찾기 + 도서관 좌석 예측 | 정의 예정 |
| **CA3** | 등하교·이동 도우미 (버스·건물 간 이동·날씨) | 정의 예정 |
| **CA4** | 캠퍼스 생활 Q&A + 에이전트 대화 — 공식 자료를 근거로 답하고, 추천·메뉴는 알맞은 기능을 대신 불러 준다 | 정의 완료 · 개발 예정 |

자세한 정의: [campus/docs/캠퍼스에이전트_기능정의서_CA1_CA4.md](../campus/docs/캠퍼스에이전트_기능정의서_CA1_CA4.md)

---

## 저장소 구조 — 학사 코드와 캠퍼스 코드

```
26-Fiveguys/
├── campus/                        ★ 캠퍼스 에이전트 — 이 팀이 만드는 것은 전부 여기
│   ├── README.md                    캠퍼스 개발 안내 (규칙·스택·브랜치)
│   ├── docs/                        기능 정의서
│   ├── shared/schema/               학사 ↔ 캠퍼스 계약 (오늘 맥락 JSON Schema)
│   ├── server/                      CA0 캠퍼스 서버 (FastAPI)
│   ├── mobile/                      CA0 캠퍼스 앱 (Next.js PWA)
│   ├── CA1_Meal_agent/              CA1 공강 맞춤 식사 추천
│   └── CA4_Assistant_agent/         CA4 캠퍼스 생활 Q&A + 에이전트 대화
│
├── .github/
│   ├── README.md                    ★ 이 문서 (포크 전용)
│   ├── pull_request_template.md     ★ PR 체크리스트 (포크 전용)
│   ├── workflows/campus-boundary.yml ★ 경계 검사 (포크 전용)
│   ├── workflows/macos.yml · release.yml   원본 — 학사 앱 빌드
│   └── CODEOWNERS                   원본 — 모든 PR 은 팀장 승인
│
│   ── 아래는 학사 에이전트: 원본 WE-Meet 그대로, 이 저장소에서 고치지 않는다 ──
├── C0~C4_*_agent/ · F1~F12_*_agent/  학사 기능 (공통 기반 C · 기능 F)
├── desktop/ · univ_us_local/         데스크톱 앱 (Tauri + FastAPI 사이드카 + Next.js)
├── notice_agent/                     참고 코드
├── README.md · DEVELOPMENT.md        학사 앱 사용·개발 안내
└── 요구사항정의서.md · Frontend-*.md · WE-Meet_*.md   학사 문서
```

### 구분하는 법 (규칙 5개)

| # | 규칙 | 이유 |
|---|---|---|
| 1 | **캠퍼스 코드는 `campus/` 안에만** 만든다 | 무엇을 이 팀이 만들었는지가 폴더 하나로 보인다. 기여는 `git log --author=<이름> -- campus/` 로 확인된다 |
| 2 | **학사 코드(`campus/` 밖 전부)는 이 저장소에서 고치지 않는다.** 고칠 일이 생기면 원본 [26-WE-Meet-Project](https://github.com/AIM-Output/26-WE-Meet-Project) 에서 고친 뒤 동기화로 받는다 | 원본을 따라 받을 때 충돌이 나지 않는다 |
| 3 | 포크 전용 설정은 **원본에 없는 새 파일**로만 둔다 — `.github/README.md` · `.github/pull_request_template.md` · `.github/workflows/campus-*.yml` | 같은 이유. 루트 `README.md` 대신 `.github/README.md` 를 쓰는 것도 이 때문이다(GitHub 는 `.github/README.md` 를 먼저 보여 준다) |
| 4 | 캠퍼스 코드는 **학사 폴더를 import 하지 않는다.** 학사 정보는 [오늘 맥락](../campus/shared/schema/daily-context.schema.json)으로만 받는다. 학사 앱 쪽 내보내기·폰 연결 기능은 학사 범위라 원본에 만들고 동기화로 받는다 | 두 쪽이 따로 개발·배포돼도 깨지지 않는다 |
| 5 | 서버는 **공용 데이터와 키만** 보관한다 — 이름·학번·성적·학교 로그인 정보는 받지 않고, 위치·취향·대화는 폰에 둔다 | 학사 앱의 "로그인 정보 서버 미보관" 원칙을 그대로 잇는다 |

**자동 점검**: PR 이 `campus/` 와 포크 전용 파일 밖을 고치면 [`campus-boundary`](workflows/campus-boundary.yml) 검사가 실패한다(저장소 Actions 를 켜야 돈다).

---

## 원본 따라 받기 (동기화)

원본 WE-Meet 에 새 커밋이 생기면 **팀장**이 맞춘다(main 보호 규칙 때문에 직접 반영은 관리자만 된다).

| 방법 | 하는 법 |
|---|---|
| 웹 | 저장소 첫 화면 `This branch is N commits behind …` → **Sync fork** → **Update branch** |
| 명령 | `gh repo sync JNU-Fiveguys/26-Fiveguys --source AIM-Output/26-WE-Meet-Project --branch main` |
| 충돌이 날 때 | 내 PC 에서 `git remote add upstream https://github.com/AIM-Output/26-WE-Meet-Project.git`(처음 한 번) → `git fetch upstream` → `git merge upstream/main` → `git push origin main` |

- 웹의 **`Discard commits` 는 누르지 않는다** — 이 저장소에만 있는 커밋(캠퍼스 작업)이 지워진다.
- 규칙 1~3 을 지키면 원본과 고친 파일이 겹치지 않아 충돌이 거의 나지 않는다.
- 동기화한 뒤 팀원은 각자 `git pull`.

## 함께 개발하기

1. **받기** — 경로에 한글·공백이 없는 짧은 곳 권장(학사 앱 빌드 도구가 긴 경로를 못 다룬다)
   ```bash
   git clone https://github.com/JNU-Fiveguys/26-Fiveguys.git
   ```
2. **캠퍼스 개발** — [campus/README.md](../campus/README.md) (폴더·스택·코드 규칙)
3. **브랜치** — `ca1/…` · `ca4/…` · `server/…` · `mobile/…` 처럼 맡은 곳 이름으로 시작
4. **PR** — `main` 으로. [PR 체크리스트](pull_request_template.md)를 채우고, 팀장 승인 뒤 머지

## 팀

| 역할 | 맡는 것 |
|---|---|
| 팀장 · 학사·통합 | 학사 에이전트(원본), 오늘 맥락 내보내기·PC–폰 연결, 통합·동기화 |
| AI·데이터 1 | CA1 공강 맞춤 식사 추천 — 조건 해석·순위·취향 학습·평가 |
| AI·데이터 2 | CA4 캠퍼스 생활 Q&A + 에이전트 대화 — 지식 베이스·검색·도구 호출·평가 |
| 데이터·인프라 | 캠퍼스 서버 — 기기 등록·공공 데이터 수집·푸시·색인 |
| 프론트엔드 | 캠퍼스 앱(PWA) — 홈·식사·에이전트·설정·지도·관리자 화면 |

## 일정 (산학협력프로젝트)

| 주차 | 목표 |
|---|---|
| 8 중간발표 | 구조 · 오늘 맥락 계약 · CA1·CA4 기능 정의 · 화면 시안 |
| 9~10 | 서버·앱 골격, CA1 규칙 추천 시제품, CA4 FAQ·검색 |
| 11 진도 점검 | CA1 추천 v1, CA4 대화 + 도구 호출, PC–폰 연결 |
| 12~14 | 취향 학습·알림·간단 모드, 팀 실사용 2주, 평가 |
| 15 기말발표 | 통합 시연 + 평가 결과 |

## 문서

| 문서 | 내용 |
|---|---|
| [campus/docs/캠퍼스에이전트_기능정의서_CA1_CA4.md](../campus/docs/캠퍼스에이전트_기능정의서_CA1_CA4.md) | 캠퍼스 공통 전제(CA0) · CA1 · CA4 요구사항·API·화면·평가 |
| [campus/README.md](../campus/README.md) | 캠퍼스 개발 규칙 |
| [요구사항정의서.md](../요구사항정의서.md) | 학사 기능 요구사항 (원본) — 캠퍼스 대화 도구는 여기 F9 의 도구 계약 방식을 따른다 |
| [WE-Meet_서버_플랫폼_검토.md](../WE-Meet_서버_플랫폼_검토.md) | 왜 "내 PC 안에서만" 구조인지 (원본) |
