import { useEffect, useState } from 'react'
import { getCitation } from '../api'

// 원문 모달 — 발언 전문 + 앞뒤 맥락 + 원본 PDF 위치. ESC/바깥 클릭으로 닫기.
// n·cited 는 표시용(번호 칩·인용 배지) — 없으면 해당 요소만 생략한다.
function SourceModal({ chunkId, n, cited = false, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    getCitation(chunkId)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [chunkId])

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const pages =
    data &&
    (data.page_end && data.page_end !== data.page_start
      ? `p.${data.page_start}~${data.page_end}`
      : `p.${data.page_start}`)

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="modal-head">
          <div className="modal-head-main">
            <div className="modal-head-line">
              {n != null && <span className={`source-n${cited ? '' : ' is-uncited'}`}>{n}</span>}
              <h3>
                {data ? `${data.speaker}${data.role ? ` ${data.role}` : ''}` : '원문'}
              </h3>
              {n != null && (
                <span className={`cited-badge${cited ? '' : ' is-uncited'}`}>
                  {cited ? '인용됨' : '미인용'}
                </span>
              )}
            </div>
            {data && (
              <div className="modal-meta">
                {data.committee_full} · {data.meeting_date} · {pages} ·{' '}
                <span className="num">chunk {data.chunk_id}</span>
              </div>
            )}
          </div>
          <button type="button" className="modal-close" onClick={onClose}>
            닫기
          </button>
        </div>

        <div className="modal-body">
          {error && <p className="error">{error}</p>}
          {!data && !error && <p className="modal-loading">원문을 불러오는 중...</p>}

          {data && (
            <>
              {data.context_before && <p className="modal-context">{data.context_before}</p>}
              <div className="modal-text">{data.text}</div>
              {data.context_after && <p className="modal-context">{data.context_after}</p>}

              <div className="modal-foot">
                원본: {data.file_name}
                <br />
                원문은 국회 회의록 PDF에서 추출한 텍스트입니다 — 인용 시 원본 회의록
                페이지를 함께 확인하시기 바랍니다.
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default SourceModal
