"use client";
import { useEffect, useRef, useCallback, useState } from "react";
import { createWebSocket } from "@/lib/ws";

export type WsStatus = "connecting" | "connected" | "reconnecting" | "disconnected";

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
  const [status, setStatus] = useState<WsStatus>("connecting");
  const authenticatedRef = useRef(false);

  useEffect(() => {
    if (!pipelineId || !token) return;

    let disposed = false;
    let reconnectTimer: ReturnType<typeof setTimeout>;
    authenticatedRef.current = false;

    function connect() {
      if (disposed) return;
      setStatus(retriesRef.current > 0 ? "reconnecting" : "connecting");
      const ws = createWebSocket(pipelineId!);
      wsRef.current = ws;

      ws.onopen = () => {
        ws.send(JSON.stringify({ token }));
      };

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          // Track auth_ok to know we're fully connected
          if (msg.type === "auth_ok") {
            authenticatedRef.current = true;
            retriesRef.current = 0;
            setStatus("connected");
          }
          onMessageRef.current(msg);
        } catch (err) {
          console.error("Failed to parse WebSocket message:", err);
        }
      };

      ws.onerror = () => {
        // Don't call ws.close() here — onclose will fire automatically
        // Calling close() from onerror was causing a tight reconnect loop
      };

      ws.onclose = () => {
        wsRef.current = null;
        authenticatedRef.current = false;
        if (disposed) return;
        if (retriesRef.current < maxRetries) {
          setStatus("reconnecting");
          const delay = Math.min(1000 * Math.pow(2, retriesRef.current), 30000);
          const jitter = delay * (0.8 + Math.random() * 0.4);
          retriesRef.current++;
          reconnectTimer = setTimeout(connect, jitter);
        } else {
          setStatus("disconnected");
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

  return { send, status };
}
