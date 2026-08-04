// 첫 방문 소개 섹션 — 질의 탭의 "결과 없음" 상태에서만 보인다.
// 숫자는 실측치 그대로(과장 없음) — 데모 배포의 부분집합 코퍼스 고지는 푸터가 담당하므로 여기선 중복하지 않는다.
const METRICS = [
  { label: '발언 기록', value: '42만 청크', sub: '22대 국회 2024-05 ~ 2026-06' },
  { label: '검색 정확도', value: 'R@5 98.3%', sub: '리랭커 적용 실측' },
  { label: '답변 정확도', value: '89.3%', sub: '75문항 사람 검수 기준선' },
  { label: '정치 분석', value: '쟁점 24개 · 의원 320명', sub: '입장·구도·시계열 분석' },
]

const DIFFERENTIATORS = [
  '하이브리드 검색(키워드+벡터) + LLM 리랭커로 관련 발언을 찾습니다',
  '모든 답변에 근거를 인용하고, 근거가 없으면 답을 거절하는 grounding 게이트를 둡니다',
  'eval 주도 개발 — 자동채점을 사람이 재검수한 기준선으로 품질을 관리합니다',
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
