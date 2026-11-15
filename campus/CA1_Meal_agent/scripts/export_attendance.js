// 기존 학사 앱의 인증된 개발자 도구 Console에서 실행하세요.
// 현재 시간표 API를 정상 호출하고, 과목명·성적·메모 없이 JSON 파일로 내려받습니다.
// 앱 소스 변경·인증 우회·다른 서버 전송을 하지 않습니다.
(async () => {
  const response = await fetch('/api/attendance/summary', {credentials: 'same-origin'});
  if (!response.ok) throw new Error(`시간표를 읽지 못했습니다: HTTP ${response.status}`);
  const data = await response.json();
  if (!Array.isArray(data.courses)) throw new Error('시간표 응답 형식이 다릅니다.');
  const result = {
    semester: {id: data.semester?.id, start: data.semester?.start, end: data.semester?.end},
    courses: data.courses.map(c => ({
      excluded: !!c.excluded,
      hasTimetable: c.hasTimetable ?? (c.timetable ? !!c.timetable.meetings?.length : undefined),
      sessions: (c.sessions || []).map(s => ({
        date: s.date, start: s.start, end: s.end, room: s.room, state: s.state, kind: s.kind
      }))
    }))
  };
  const blob = new Blob([JSON.stringify(result, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = 'attendance-summary.json';
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
})();
