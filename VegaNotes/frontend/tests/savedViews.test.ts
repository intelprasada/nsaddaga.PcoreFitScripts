import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api } from "../src/api/client";

// Regression: #236 — FE expects ``Record<name, chips>`` but backend
// persists ``list[{name, query}]``. The client wrappers bridge the two
// so the SavedViews dropdown can save/load chip sets cleanly.

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  // @ts-expect-error - overriding global fetch in tests is fine
  global.fetch = fetchMock;
});

afterEach(() => {
  // @ts-expect-error
  delete global.fetch;
});

function ok(json: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => json,
  } as unknown as Response;
}

describe("client.savedViews / saveViews (#236)", () => {
  it("savedViews decodes list-of-named-views into a name→chips dict", async () => {
    fetchMock.mockResolvedValueOnce(
      ok([
        { name: "mine", query: { chips: ["owner=alice", "not_status=done"] } },
        { name: "empty", query: {} },
        { name: "legacy", query: { chips: [] } },
      ]),
    );
    const views = await api.savedViews();
    expect(views).toEqual({
      mine: ["owner=alice", "not_status=done"],
      empty: [],
      legacy: [],
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/me\/views$/);
    expect((init as RequestInit | undefined)?.method ?? "GET").toBe("GET");
  });

  it("saveViews encodes a dict as the list shape the backend accepts", async () => {
    fetchMock.mockResolvedValueOnce(ok({ status: "ok", count: 2 }));
    const input = { mine: ["owner=alice"], empty: [] as string[] };
    const echoed = await api.saveViews(input);

    // Returns input as-is so optimistic cache writes keep working.
    expect(echoed).toEqual(input);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toMatch(/\/me\/views$/);
    expect(init.method).toBe("PUT");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual([
      { name: "mine", query: { chips: ["owner=alice"] } },
      { name: "empty", query: { chips: [] } },
    ]);
  });

  it("savedViews tolerates malformed entries", async () => {
    fetchMock.mockResolvedValueOnce(
      ok([
        { name: "good", query: { chips: ["a=1"] } },
        { query: { chips: ["x"] } }, // missing name → dropped
        null, // garbage → dropped
        { name: "noquery" }, // missing query → empty chip list
      ]),
    );
    const views = await api.savedViews();
    expect(views).toEqual({ good: ["a=1"], noquery: [] });
  });
});
