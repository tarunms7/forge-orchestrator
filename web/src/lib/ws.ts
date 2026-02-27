export function createWebSocket(pipelineId: string): WebSocket {
  const wsBase = process.env.NEXT_PUBLIC_WS_URL || `ws://${window.location.host}/api`;
  return new WebSocket(`${wsBase}/ws/${pipelineId}`);
}
