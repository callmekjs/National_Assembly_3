// 첫 방문 소개 섹션 — 질의 탭의 "결과 없음" 상태에서만 보인다.
// 데모 배포의 부분집합 코퍼스 고지는 푸터가 담당하므로 여기선 중복하지 않는다.
//
// 숫자에는 측정 근거를 함께 적는다 (감사 2026-08-05).
//
// 2026-08-07 전면 교체. 두 가지를 함께 고쳤다.
//  ① **낡은 수치를 내렸다.** `R@5 98.3%` 는 폐기된 잣대의 값이다 — 채점 기준이
//     "결과 본문에 질문 키워드가 있는가"인데 키워드 검색 축이 같은 연산을 하는
//     동어반복이었다. `답변 정확도 89.3%` 도 마찬가지로 "준 근거만 썼나"를 쟀을 뿐
//     "답이 맞았나"를 묻지 않았다. 둘 다 새 평가셋으로 다시 쟀다.
//  ② **전문용어를 뺐다.** `R@5`·`nDCG` 는 이 분야 밖에서는 읽히지 않는다.
//     방문자는 README 를 읽지 않고 이 화면을 보므로, 여기서 이해되지 않으면
//     그 숫자는 없는 것과 같다. 지표 이름 대신 **무엇을 물었는지**를 쓴다.
//     정확한 지표명·측정 조건은 README "알려진 한계"와 docs/progress.md 에 남긴다.
const METRICS = [
  { label: '회의록 분량', value: '42만 발언', sub: '22대 국회 2024-05 ~ 2026-06' },
  {
    label: '근거를 찾아내는가',
    value: '20문제 중 20개',
    sub: '질문마다 회의록 5건을 고르게 해, 그 안에 진짜 근거가 있는지 확인',
  },
  {
    label: '답이 맞는가',
    value: '18문제 중 16개',
    sub: '사람이 만든 모범답안과 대조 — 지어낸 내용 1건',
  },
  { label: '정치 분석', value: '쟁점 24개 · 의원 320명', sub: '입장·구도·시계열 분석' },
]

const DIFFERENTIATORS = [
  '단어가 겹치는 발언과 뜻이 통하는 발언을 함께 찾은 뒤, AI 가 관련 순서로 다시 줄 세웁니다',
  '모든 문장에 근거 번호를 답니다. 회의록에 없으면 지어내지 않고 "확인할 수 없다"고 답합니다',
  '문제를 미리 만들어 놓고 점수를 재면서 고칩니다 — 고치기 전에 숨겨 둔 문제로 실제로 나아졌는지 확인합니다',
]

// IBM Plex Mono 에는 한글 글리프가 없다 — 한글이 한 글자라도 섞이면 Pretendard 로
const HANGUL = /[가-힣]/

function Hero({ examples = [], onExample }) {
  return (
    <section className="hero" aria-label="서비스 소개">
      <p className="hero-lead">
        22대 국회 회의록 767건을 근거로 답하는 RAG 서비스입니다 — 정책 의제와 행위자,
        여야 입장 차이를 근거 문장과 함께 보여줍니다.
      </p>

      <div className="hero-metrics">
        {METRICS.map((m) => (
          <div className="hero-metric-card" key={m.label}>
            <div className="hero-metric-label">{m.label}</div>
            <div className={`hero-metric-value${HANGUL.test(m.value) ? '' : ' is-num'}`}>
              {m.value}
            </div>
            <div className="hero-metric-sub">{m.sub}</div>
          </div>
        ))}
      </div>

      <div className="hero-cols">
        <div>
          <h2 className="hero-col-title">무엇이 다른가</h2>
          <ul className="hero-diff-list">
            {DIFFERENTIATORS.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        </div>
        <div>
          <h2 className="hero-col-title">이런 질문을 해보세요</h2>
          <div className="hero-examples">
            {examples.map((q) => (
              <button key={q} type="button" onClick={() => onExample(q)}>
                {q}
              </button>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}

export default Hero
