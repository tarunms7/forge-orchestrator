"use client";
import { useEffect, useRef, useCallback } from "react";
import { createWebSocket } from "@/lib/ws";

export function useWebSocket(
  pipelineId: string | null,
  token: string | null,
  onMessage: (event: Record<string, unknown>) => void,
) {
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!pipelineId || !token) return;
    const ws = createWebSocket(pipelineId, token);
    wsRef.current = ws;
    ws.onmessage = (e) => onMessage(JSON.parse(e.data));
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      wsRef.current = null;
    };
    return () => {
      ws.close();
    };
  }, [pipelineId, token, onMessage]);

  const send = useCallback((data: unknown) => {
    wsRef.current?.send(JSON.stringify(data));
  }, []);

  return { send };
}
