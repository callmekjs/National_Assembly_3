import { useEffect, useState } from 'react'
import { getCitation } from '../api'

// 원문 모달 — 발언 전문 + 앞뒤 맥락 + 원본 PDF 위치. ESC/바깥 클릭으로 닫기.
function SourceModal({ chunkId, onClose }) {
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
      ? `${data.page_start}~${data.page_end}쪽`
      : `${data.page_start}쪽`)

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <button type="button" className="modal-close" onClick={onClose} aria-label="닫기">
          ×
        </button>

        {error && <p className="error">{error}</p>}
        {!data && !error && <p className="modal-loading">원문을 불러오는 중...</p>}

        {data && (
          <>
            <header className="modal-head">
              <h3>
                {data.speaker}
                {data.role ? ` ${data.role}` : ''}
              </h3>
              <p className="modal-meta">
                {data.committee_full} · {data.meeting_date}
              </p>
            </header>

            {data.context_before && (
              <p className="modal-context">{data.context_before}</p>
            )}
            <div className="modal-text">{data.text}</div>
            {data.context_after && <p className="modal-context">{data.context_after}</p>}

            <footer className="modal-foot">
              원본: {data.file_name} · PDF {pages}
            </footer>
          </>
        )}
      </div>
    </div>
  )
}

export default SourceModal
