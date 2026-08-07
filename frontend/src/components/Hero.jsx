// 첫 방문 소개 섹션 — 질의 탭의 "결과 없음" 상태에서만 보인다.
// 데모 배포의 부분집합 코퍼스 고지는 푸터가 담당하므로 여기선 중복하지 않는다.
//
// 숫자에는 측정 근거를 함께 적는다 (감사 2026-08-05).
//
// 2026-08-07 전문용어를 뺐다. `R@5`·`nDCG` 는 이 분야 밖에서는 읽히지 않는다.
// 방문자는 README 를 읽지 않고 이 화면을 보므로, 여기서 이해되지 않으면 그 숫자는
// 없는 것과 같다. 지표 이름 대신 **무엇을 물었는지**를 쓴다. 정확한 지표명·측정
// 조건은 README "알려진 한계"와 docs/progress.md 에 남긴다.
//
// 2026-08-07 (배포 검증 중) 검색 카드를 실측값으로 교체했다. 직전까지 `98.3%` 였는데
// 이건 **폐기된 잣대(R@5)** 의 값이다 — 채점 기준이 "결과 본문에 질문 키워드가
// 있는가"인데 키워드 검색 축이 같은 연산을 하는 동어반복이었다. 위 주석은 "새
// 평가셋으로 다시 쟀다"고 적어놓고 값은 그대로 두어, **화면이 폐기 수치를 계속
// 노출**하고 있었다.
//
// 그리고 교체하면서 같은 함정을 한 번 더 밟았다. 처음엔 `18/18` 을 올렸는데 그건
// **전체 코퍼스 로컬 측정**이고, 이 화면을 보는 사람이 쓰는 건 **배포 축소본**이다.
// 방문자가 만지는 것과 다른 대상을 잰 숫자를 띄우는 것은 폐기 수치를 띄우는 것과
// 같은 종류의 거짓이다. 두 코퍼스를 같은 잣대로 각각 재서 둘 다 적는다:
//   전체(로컬):  strict@5 18/18 · nDCG@5 0.762  (reports/..._130144.json)
//   배포 축소본: strict@5 15/18 · nDCG@5 0.468  (reports/..._171232.json)
//
// 차이의 원인은 검색 성능이 아니라 **근거의 부재**다. 라벨된 핵심 근거(등급2)
// 142개 중 73개만 축소본에 있다(48.6% 누락). 없는 것을 찾으라고 시킨 셈이라
// 점수가 떨어진다. 그래도 배포본 숫자를 앞에 둔다 — 방문자의 경험을 설명하는 건
// 이쪽이고, 변명은 그 다음이다.
//
// 백분율 대신 `15/18` 을 쓴다 — 표본이 18문항인데 `83%` 로 적으면 표본 크기가
// 숨는다. 숨기지 않는 편이 방어 가능하다.
//
// `답변 정확도 89.3%` 는 그대로 둔다. 이건 폐기 대상이 아니라 **75문항 답변
// 평가셋의 사람 검수 기준선**(67/75, progress.md 2026-07-15)이다. 검색 평가셋
// (20문항)과 별개 자산이므로 문항 수가 다른 것은 모순이 아니다.
const METRICS = [
  { label: '회의록 분량', value: '42만 발언', sub: '22대 국회 2024-05 ~ 2026-06' },
  { label: '근거 찾기', value: '15/18', sub: '이 사이트(축소본) 기준 · 전체 데이터로는 18/18 · 한계는 README' },
  { label: '답변 정확도', value: '89.3%', sub: '75문항 사람 검수 · 측정 한계는 README 참조' },
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
