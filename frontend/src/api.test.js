// api.js 스모크 테스트 — fetch 를 목킹해 에러 매핑 로직만 검증 (DOM 불필요).
// 백엔드 계약(502 → 친화 메시지, 4xx detail 표시, 타임아웃)이 프론트에서
// 유지되는지 확인. 실행: npm test
import { afterEach, describe, expect, it, vi } from 'vitest'
import { postQuery } from './api'

afterEach(() => vi.unstubAllGlobals())

function stubFetch(impl) {
  vi.stubGlobal('fetch', vi.fn(impl))
}

describe('postQuery 에러 매핑', () => {
  it('정상 응답은 JSON 을 반환한다', async () => {
    stubFetch(async () => ({ ok: true, status: 200, json: async () => ({ answer: 'ok' }) }))
    expect(await postQuery('질문', 'qa')).toEqual({ answer: 'ok' })
  })

  it('502 는 친화 메시지로 (프론트가 이 코드에 의존)', async () => {
    stubFetch(async () => ({ ok: false, status: 502, json: async () => ({}) }))
    await expect(postQuery('질문', 'qa')).rejects.toThrow('답변 생성에 실패')
  })

  it('4xx 는 백엔드 detail 을 표시한다', async () => {
    stubFetch(async () => ({
      ok: false, status: 422,
      json: async () => ({ detail: 'query_id 형식이 UUID 가 아닙니다' }),
    }))
    await expect(postQuery('질문', 'qa')).rejects.toThrow('UUID')
  })

  it('연결 실패는 안내 메시지로', async () => {
    stubFetch(async () => { throw new TypeError('Failed to fetch') })
    await expect(postQuery('질문', 'qa')).rejects.toThrow('서버에 연결할 수 없습니다')
  })

  it('타임아웃은 안내 메시지로', async () => {
    stubFetch(async () => {
      const e = new Error('timeout')
      e.name = 'TimeoutError'
      throw e
    })
    await expect(postQuery('질문', 'qa')).rejects.toThrow('너무 오래')
  })
})
