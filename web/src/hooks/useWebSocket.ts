"use client";
import { useEffect, useRef, useCallback, useState } from "react";
import { createWebSocket } from "@/lib/ws";
import { useAuthStore } from "@/stores/authStore";

export type WsStatus = "connecting" | "connected" | "reconnecting" | "disconnected";

export function useWebSocket(
  pipelineId: string | null,
  token: string | null,
  onMessage: (event: unknown) => void,
) {
  const wsRef = useRef<WebSocket | null>(null);
  const onMessageRef = useRef(onMessage);
  useEffect(() => { onMessageRef.current = onMessage; });
  const retriesRef = useRef(0);
  const maxRetries = 10;
  const [status, setStatus] = useState<WsStatus>("connecting");
  const authenticatedRef = useRef(false);
  const tokenRef = useRef(token);

  // Increment this to force a reconnect (used by the retry() callback)
  const [retryTrigger, setRetryTrigger] = useState(0);

  // Keep tokenRef in sync so reconnects use the latest token
  useEffect(() => { tokenRef.current = token; }, [token]);

  useEffect(() => {
    if (!pipelineId || !token) return;

    let disposed = false;
    let reconnectTimer: ReturnType<typeof setTimeout>;
    authenticatedRef.current = false;

    function connect() {
      if (disposed) return;
      setStatus(retriesRef.current > 0 ? "reconnecting" : "connecting");

      // On reconnect, use fresh token from the auth store
      // (the token from the closure may have expired)
      const currentToken = retriesRef.current > 0
        ? useAuthStore.getState().token ?? token
        : token;

      const ws = createWebSocket(pipelineId!);
      wsRef.current = ws;

      ws.onopen = () => {
        ws.send(JSON.stringify({ token: currentToken }));
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

      ws.onclose = (event) => {
        wsRef.current = null;
        authenticatedRef.current = false;
        if (disposed) return;

        // Auth failure (close code 4001) — skip further retries with stale token
        if (event.code === 4001) {
          setStatus("disconnected");
          useAuthStore.getState().logout();
          return;
        }

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
  }, [pipelineId, retryTrigger]); // eslint-disable-line react-hooks/exhaustive-deps -- token accessed via tokenRef to avoid reconnect on refresh

  const send = useCallback((data: unknown) => {
    wsRef.current?.send(JSON.stringify(data));
  }, []);

  // Manual retry — resets retry count and triggers a fresh connection
  const retry = useCallback(() => {
    retriesRef.current = 0;
    setRetryTrigger((t) => t + 1);
  }, []);

  return { send, status, retry };
}
