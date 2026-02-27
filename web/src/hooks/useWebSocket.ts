"use client";
import { useEffect, useRef, useCallback } from "react";
import { createWebSocket } from "@/lib/ws";

export function useWebSocket(
  pipelineId: string | null,
  token: string | null,
  onMessage: (event: unknown) => void,
) {
  const wsRef = useRef<WebSocket | null>(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;
  const retriesRef = useRef(0);
  const maxRetries = 10;

  useEffect(() => {
    if (!pipelineId || !token) return;

    let disposed = false;
    let reconnectTimer: ReturnType<typeof setTimeout>;

    function connect() {
      if (disposed) return;
      const ws = createWebSocket(pipelineId!);
      wsRef.current = ws;

      ws.onopen = () => {
        ws.send(JSON.stringify({ token }));
        retriesRef.current = 0;
      };

      ws.onmessage = (e) => {
        try {
          onMessageRef.current(JSON.parse(e.data));
        } catch (err) {
          console.error("Failed to parse WebSocket message:", err);
        }
      };

      ws.onerror = () => ws.close();

      ws.onclose = () => {
        wsRef.current = null;
        if (disposed) return;
        if (retriesRef.current < maxRetries) {
          const delay = Math.min(1000 * Math.pow(2, retriesRef.current), 30000);
          const jitter = delay * (0.8 + Math.random() * 0.4);
          retriesRef.current++;
          reconnectTimer = setTimeout(connect, jitter);
        }
      };
    }

    connect();

    return () => {
      disposed = true;
      clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, [pipelineId, token]);

  const send = useCallback((data: unknown) => {
    wsRef.current?.send(JSON.stringify(data));
  }, []);

  return { send };
}
