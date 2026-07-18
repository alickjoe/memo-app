export function createWebSocket(path: string): WebSocket {
  // WebSocket URL 从 HTTP URL 推导
  const httpUrl = localStorage.getItem('backend_url') || 'http://127.0.0.1:8765'
  const wsUrl = httpUrl.replace('http', 'ws') + path
  return new WebSocket(wsUrl)
}
