export function createWebSocket(pipelineId: string): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsBase = process.env.NEXT_PUBLIC_WS_URL || `${protocol}//${window.location.host}/api`;
  return new WebSocket(`${wsBase}/ws/${pipelineId}`);
}
