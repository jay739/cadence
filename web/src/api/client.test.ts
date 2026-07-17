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

  it("adminSettings.update PUTs to the per-integration endpoint with the given fields", async () => {
    mockFetchOnce(200, { prowlarr: {}, qbittorrent: {}, slskd: {}, spotify: {}, library: {} });
    await api.adminSettings.update("prowlarr", { prowlarr_url: "http://prowlarr:9696" });
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe("/api/admin/settings/prowlarr");
    expect(call[1].method).toBe("PUT");
    expect(JSON.parse(call[1].body)).toEqual({ prowlarr_url: "http://prowlarr:9696" });
  });

  it("adminSettings.testConnection POSTs to the integration's /test endpoint", async () => {
    mockFetchOnce(200, { ok: true, detail: "Connected" });
    const result = await api.adminSettings.testConnection("slskd", {
      slskd_url: "http://slskd:5030",
    });
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe("/api/admin/settings/slskd/test");
    expect(call[1].method).toBe("POST");
    expect(result.ok).toBe(true);
  });

  it("adminSettings.get fetches the settings summary", async () => {
    mockFetchOnce(200, { prowlarr: {}, qbittorrent: {}, slskd: {}, spotify: {}, library: {} });
    await api.adminSettings.get();
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe("/api/admin/settings");
  });
});
