import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, setAuthToken } from "./client";

function mockFetchOnce(status: number, body: unknown, ok = status < 400) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
  }) as unknown as typeof fetch;
}

describe("api client", () => {
  beforeEach(() => {
    setAuthToken(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("throws ApiError with the server-provided detail message on failure", async () => {
    mockFetchOnce(401, { detail: "Invalid email or password" });
    await expect(api.auth.login("a@example.com", "wrongpass")).rejects.toMatchObject({
      status: 401,
      message: "Invalid email or password",
    });
  });

  it("wraps failures in ApiError instances", async () => {
    mockFetchOnce(500, { detail: "Internal server error" });
    await expect(api.health.get()).rejects.toBeInstanceOf(ApiError);
  });

  it("falls back to a generic message when the server sends no detail", async () => {
    mockFetchOnce(500, null);
    await expect(api.health.get()).rejects.toMatchObject({
      message: "Request failed with status 500",
    });
  });

  it("attaches the Authorization header once a token is set", async () => {
    setAuthToken("test-token-123");
    mockFetchOnce(200, { version: "0.1.0", mode: "demo", database_ok: true, integrations: {} });
    await api.health.get();
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const headers = call[1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer test-token-123");
  });

  it("does not attach an Authorization header when no token is set", async () => {
    mockFetchOnce(200, { version: "0.1.0", mode: "demo", database_ok: true, integrations: {} });
    await api.health.get();
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const headers = call[1].headers as Headers;
    expect(headers.get("Authorization")).toBeNull();
  });

  it("returns parsed JSON on success", async () => {
    mockFetchOnce(200, { version: "0.1.0", mode: "demo", database_ok: true, integrations: {} });
    const result = await api.health.get();
    expect(result.mode).toBe("demo");
  });
});
