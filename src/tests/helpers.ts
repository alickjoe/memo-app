import { vi } from 'vitest'

type RouteHandler = (url: string, init?: RequestInit) => unknown

/**
 * 按 URL 分发 mock fetch 响应。
 * handler 返回 undefined 表示未匹配 → 返回 404；
 * 否则返回 { ok: true, json: async () => body }。
 */
export function mockFetchRoutes(handler: RouteHandler) {
  const fetchMock = vi.mocked(global.fetch)
  fetchMock.mockImplementation(async (input, init) => {
    const url = getRequestUrl(input)
    const body = handler(url, init)
    if (body === undefined) {
      return { ok: false, status: 404, json: async () => ({ error: 'not found' }) } as Response
    }
    return { ok: true, status: 200, json: async () => body } as Response
  })
}

export function getRequestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.href
  if (input instanceof Request) return input.url
  return ''
}
