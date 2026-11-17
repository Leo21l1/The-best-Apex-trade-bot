import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../api";

afterEach(() => vi.unstubAllGlobals());

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue(
    new Response(body === null ? null : JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("api client", () => {
  it("sends CSRF header only on state-changing requests", async () => {
    const fn = mockFetch(200, { ok: true });
    await api("/api/x");
    expect(fn.mock.calls[0]![1].headers["X-Requested-With"]).toBeUndefined();
    await api("/api/x", { method: "POST", body: { a: 1 } });
    expect(fn.mock.calls[1]![1].headers["X-Requested-With"]).toBe("apex");
    expect(fn.mock.calls[1]![1].credentials).toBe("same-origin");
  });

  it("maps FastAPI error details", async () => {
    mockFetch(401, { detail: "Invalid email or password" });
    await expect(api("/api/auth/login")).rejects.toMatchObject({
      status: 401,
      message: "Invalid email or password",
    });
    mockFetch(422, { detail: [{ msg: "too short" }, { msg: "bad email" }] });
    await expect(api("/api/x")).rejects.toThrow("too short; bad email");
  });

  it("reports unreachable API as status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
    const err = await api("/api/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(0);
  });

  it("handles 204", async () => {
    mockFetch(204, null);
    await expect(api("/api/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });
});
