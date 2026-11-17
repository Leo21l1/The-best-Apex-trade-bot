import { act, render } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RealtimeProvider, useRealtime } from "../useRealtime";

class FakeSocket {
  static instances: FakeSocket[] = [];
  static OPEN = 1;
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
  open() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }
  drop() {
    this.readyState = 3;
    this.onclose?.();
  }
  push(frame: object) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

function Listener({ topic, onData }: { topic: string; onData: (d: unknown) => void }) {
  const { subscribe } = useRealtime();
  useEffect(() => subscribe(topic, onData), [subscribe, topic, onData]);
  return null;
}

describe("RealtimeProvider", () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeSocket);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("subscribes to market topics, reconnects after a drop and re-subscribes", async () => {
    const got: unknown[] = [];
    const onData = (d: unknown) => got.push(d);
    render(<RealtimeProvider><Listener topic="candles:ES" onData={onData} /></RealtimeProvider>);
    const first = FakeSocket.instances[0]!;
    act(() => first.open());
    expect(first.sent.map((s) => JSON.parse(s))).toContainEqual({ type: "subscribe", topics: ["candles:ES"] });

    act(() => first.push({ topic: "candles:ES", seq: 1, ts: "", data: { c: 1 } }));
    act(() => first.push({ topic: "candles:NQ", seq: 2, ts: "", data: { c: 2 } }));
    act(() => first.onmessage?.({ data: "not json" }));
    expect(got).toEqual([{ c: 1 }]); // only the subscribed topic; malformed frames ignored

    act(() => first.drop());
    expect(FakeSocket.instances).toHaveLength(1);
    await act(async () => { vi.advanceTimersByTime(1000); }); // backoff (<= 500ms for attempt 0)
    const second = FakeSocket.instances[1]!;
    expect(second).toBeDefined();
    act(() => second.open());
    expect(second.sent.map((s) => JSON.parse(s))).toContainEqual({ type: "subscribe", topics: ["candles:ES"] });
  });

  it("does not ask the server to subscribe to non-market topics", () => {
    render(<RealtimeProvider><Listener topic="system.status" onData={() => undefined} /></RealtimeProvider>);
    const ws = FakeSocket.instances[0]!;
    act(() => ws.open());
    expect(ws.sent).toEqual([]);
  });
});
