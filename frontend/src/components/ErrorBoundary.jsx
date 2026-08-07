import { Component } from 'react'

/**
 * 에러 바운더리 — 렌더 중 예외를 잡아 화면 전체가 백지가 되는 것을 막는다.
 *
 * 없을 때 생기던 문제 (감사 2026-08-05): React 는 렌더 중 예외를 잡지 않으면
 * 트리 전체를 언마운트한다. 컴포넌트 하나가 예상 못 한 응답 형태(예: sources 가
 * null, citations 가 문자열)를 만나면 **화면이 통째로 하얘지고** 사용자는 새로고침
 * 말고는 할 수 있는 게 없다. 배포 후 방문자에게 가장 먼저 보이는 실패 형태다.
 *
 * 설계:
 *  - 클래스 컴포넌트여야 한다 — componentDidCatch 에 대응하는 훅이 없다.
 *  - 오류 내용을 화면에 노출하되(개발·디버깅용) 접어 둔다. 공개 배포에서 스택이
 *    첫 화면에 펼쳐져 있으면 사용자에게 무의미하고 내부 구조만 드러낸다.
 *  - "다시 시도"는 상태만 초기화한다. 새로고침을 강요하면 입력이 날아간다.
 *  - resetKey 가 바뀌면 자동으로 복구한다 (탭 이동 등 — 한 화면의 오류가 다른
 *    화면까지 잠그지 않게).
 */
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidUpdate(prevProps) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  componentDidCatch(error, info) {
    // 콘솔에는 남긴다 — 사용자 화면은 조용하되 개발자 도구에서는 추적 가능해야 한다
    console.error('[ErrorBoundary]', error, info?.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="error-boundary" role="alert">
        <h2>화면을 표시하지 못했습니다</h2>
        <p>
          이 부분에서 오류가 발생했습니다. 다른 기능은 계속 사용할 수 있습니다.
        </p>
        <button type="button" onClick={() => this.setState({ error: null })}>
          다시 시도
        </button>
        <details>
          <summary>오류 내용</summary>
          <pre>{String(error?.message || error)}</pre>
        </details>
      </div>
    )
  }
}
