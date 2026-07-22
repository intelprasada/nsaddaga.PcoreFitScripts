import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * Unit tests for the #312 project-scoped users client wrappers.
 * Confirms URL construction only; the backend project-scope query is
 * covered end-to-end by ``tests/api/test_users_project_scope.py``.
 */

const fetchMock = vi.fn();
globalThis.fetch = fetchMock as any;

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => [],
    text: async () => "[]",
    headers: new Headers({ "content-type": "application/json" }),
  });
});

describe("users api client project scope (#312)", () => {
  it("GETs /users with no project param when project is undefined", async () => {
    const { api } = await import("../src/api/client");
    await api.users();
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/users");
    expect(url).not.toContain("project=");
  });

  it("GETs /users?project=<name> when project is set", async () => {
    const { api } = await import("../src/api/client");
    await api.users("proj-a");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/users?project=proj-a");
  });

  it("URL-encodes the project name to survive slashes and spaces", async () => {
    const { api } = await import("../src/api/client");
    await api.users("my project/2024");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("project=my%20project%2F2024");
  });

  it("GETs /users?with_display=1 with no project by default", async () => {
    const { api } = await import("../src/api/client");
    await api.usersWithDisplay();
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/users?with_display=1");
    expect(url).not.toContain("project=");
  });

  it("appends project=<name> to /users?with_display=1 when set", async () => {
    const { api } = await import("../src/api/client");
    await api.usersWithDisplay("proj-b");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/users?with_display=1&project=proj-b");
  });

  it("URL-encodes project name for the with_display variant too", async () => {
    const { api } = await import("../src/api/client");
    await api.usersWithDisplay("Val Hiring");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("project=Val%20Hiring");
  });
});
