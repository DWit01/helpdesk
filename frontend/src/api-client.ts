import type Keycloak from "keycloak-js";

export class ApiError extends Error {
  constructor(readonly status: number) {
    super("The API request failed.");
  }
}

export class ApiClient {
  constructor(private readonly keycloak: Keycloak) {}

  async get(path: string): Promise<unknown> {
    return this.request(path, "GET");
  }

  async post(path: string, body: unknown): Promise<unknown> {
    return this.request(path, "POST", body);
  }

  private async request(path: string, method: "GET" | "POST", body?: unknown): Promise<unknown> {
    // Only accept paths on the configured local API, never arbitrary URLs.
    if (!path.startsWith("/") || path.startsWith("//")) {
      throw new Error("Invalid API path.");
    }
    await this.keycloak.updateToken(30);
    const token = this.keycloak.token;
    if (!token) throw new Error("No access token.");

    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/v1${path}`, {
        method,
        headers: method === "POST"
          ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }
          : { Authorization: `Bearer ${token}` },
        body: method === "POST" ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
      if (!response.ok) throw new ApiError(response.status);
      return await response.json() as unknown;
    } finally {
      window.clearTimeout(timeout);
    }
  }
}
