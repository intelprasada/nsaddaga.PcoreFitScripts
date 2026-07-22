import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * Unit tests for the frontend archive API client wrappers (#304 PR 5).
 * Only exercises URL/method construction; server-side behaviour is
 * covered by the backend PRs (#305/#306/#307/#308).
 */

const fetchMock = vi.fn();
globalThis.fetch = fetchMock as any;

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({}),
    text: async () => "{}",
    headers: new Headers({ "content-type": "application/json" }),
  });
});

describe("archive api client", () => {
  it("POSTs /notes/:id/archive on archiveNote", async () => {
    const { api } = await import("../src/api/client");
    await api.archiveNote(7);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/notes/7/archive");
    expect((init as RequestInit).method).toBe("POST");
  });

  it("POSTs /notes/:id/unarchive on unarchiveNote", async () => {
    const { api } = await import("../src/api/client");
    await api.unarchiveNote(9);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/notes/9/unarchive");
    expect((init as RequestInit).method).toBe("POST");
  });

  it("POSTs /projects/:name/archive on archiveProject (URL-encoded)", async () => {
    const { api } = await import("../src/api/client");
    await api.archiveProject("Proj With Space");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/projects/Proj%20With%20Space/archive");
  });

  it("passes archive summary project scope as query param", async () => {
    const { api } = await import("../src/api/client");
    await api.archiveSummary("Alpha");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/archive/summary?project=Alpha");
  });

  it("omits query string when summary called without project", async () => {
    const { api } = await import("../src/api/client");
    await api.archiveSummary();
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/archive/summary");
    expect(url).not.toContain("?");
  });

  it("builds archivedTasks query string from filter object", async () => {
    const { api } = await import("../src/api/client");
    await api.archivedTasks({ project: "P", owner: "u", status: "done",
                              q: "search", limit: 5, offset: 10 });
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/archive/tasks?");
    expect(url).toContain("project=P");
    expect(url).toContain("owner=u");
    expect(url).toContain("status=done");
    expect(url).toContain("q=search");
    expect(url).toContain("limit=5");
    expect(url).toContain("offset=10");
  });

  it("GETs bare /archive/tasks when no filters", async () => {
    const { api } = await import("../src/api/client");
    await api.archivedTasks();
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/archive/tasks");
    expect(url).not.toContain("?");
  });

  it("POSTs /archive/reconcile without body", async () => {
    const { api } = await import("../src/api/client");
    await api.archiveReconcile();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/archive/reconcile");
    expect((init as RequestInit).method).toBe("POST");
  });
});
