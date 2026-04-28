import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { onAwardedBadges } from "../src/api/client";

// Re-import the module under test by spying on global fetch.
const realFetch = globalThis.fetch;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  // Each test installs its own fetch stub.
});

afterEach(() => {
  globalThis.fetch = realFetch;
  vi.restoreAllMocks();
});

describe("onAwardedBadges interceptor", () => {
  it("fires once per write response that carries awarded_badges", async () => {
    const seen: string[][] = [];
    const unsub = onAwardedBadges((keys) => seen.push(keys));

    globalThis.fetch = vi.fn(async () =>
      jsonResponse(200, { id: 1, awarded_badges: ["first_light", "hat_trick"] }),
    ) as unknown as typeof fetch;

    const { api } = await import("../src/api/client");
    await api.updateTask(1, { status: "done" });
    unsub();

    expect(seen).toEqual([["first_light", "hat_trick"]]);
  });

  it("does NOT fire for GET responses even if they happen to include the key", async () => {
    const seen: string[][] = [];
    const unsub = onAwardedBadges((keys) => seen.push(keys));

    globalThis.fetch = vi.fn(async () =>
      jsonResponse(200, [{ id: 1, awarded_badges: ["first_light"] }]),
    ) as unknown as typeof fetch;

    const { api } = await import("../src/api/client");
    await api.meActivity({ limit: 5 });
    unsub();

    expect(seen).toEqual([]);
  });

  it("ignores write responses with no awarded_badges or empty array", async () => {
    const seen: string[][] = [];
    const unsub = onAwardedBadges((keys) => seen.push(keys));

    globalThis.fetch = vi.fn(async () =>
      jsonResponse(200, { id: 1, awarded_badges: [] }),
    ) as unknown as typeof fetch;

    const { api } = await import("../src/api/client");
    await api.updateTask(1, { status: "done" });
    unsub();

    expect(seen).toEqual([]);
  });

  it("does not propagate listener exceptions to the request caller", async () => {
    const unsub = onAwardedBadges(() => {
      throw new Error("boom");
    });

    globalThis.fetch = vi.fn(async () =>
      jsonResponse(200, { id: 1, awarded_badges: ["first_light"] }),
    ) as unknown as typeof fetch;

    const { api } = await import("../src/api/client");
    await expect(api.updateTask(1, { status: "done" })).resolves.toBeTruthy();
    unsub();
  });
});
