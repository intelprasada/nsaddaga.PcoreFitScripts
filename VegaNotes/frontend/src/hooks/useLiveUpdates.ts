import { useEffect, useRef } from "react";

/** Subscribes to the backend WebSocket for live index updates. */
export function useLiveUpdates(onMessage: (msg: unknown) => void): void {
  const ref = useRef<WebSocket | null>(null);
  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws`);
    ref.current = ws;
    ws.onmessage = (e) => {
      try { onMessage(JSON.parse(e.data)); } catch { /* ignore */ }
    };
    return () => ws.close();
  }, [onMessage]);
}
