import type Keycloak from "keycloak-js";

export class ApiError extends Error {
  constructor(readonly status: number, readonly requestId: string | null = null, readonly code: string | null = null) {
    super("The API request failed.");
  }
}

export function writeErrorMessage(error: unknown, fallback: string): string {
  const authMessage = authRefreshMessage(error);
  if (authMessage) return authMessage;
  if (!(error instanceof ApiError)) return fallback;
  const conflictMessages: Record<string, string> = {
    INVALID_STATUS_TRANSITION: "The ticket status has changed. Reopen the ticket and check the available actions.",
    ASSIGNEE_REQUIRED: "An active assignee is required for an in-progress ticket.",
    INVALID_ASSIGNEE: "Choose an active agent or admin as the assignee.",
    TICKET_CLOSED: "This ticket is closed and can no longer be changed.",
    CATEGORY_INACTIVE: "That category is inactive. Choose an active category.",
    CATEGORY_NAME_EXISTS: "A category with that name already exists.",
    RESOLUTION_REQUIRED: "Enter a resolution comment before resolving the ticket.",
  };
  const message = error.status === 401 ? "Your session could not be verified. Please sign in again." :
    error.status === 403 ? "You don't have permission to perform this action." :
    error.status === 404 ? "This item is unavailable. Reopen the list and try again." :
    error.status === 409 ? (error.code && conflictMessages[error.code]) ||
      "The current data conflicts with this action. Refresh and try again." :
    error.status === 422 ? "Please check the fields and try again." : fallback;
  return withRequestId(message, error);
}

// Token refresh happens before the application API is contacted.
export class AuthRefreshError extends Error {
  constructor() {
    super("Unable to refresh sign-in.");
  }
}

export function authRefreshMessage(error: unknown): string | null {
  return error instanceof AuthRefreshError
    ? "Couldn't refresh your sign-in. Check the identity service and try again, or sign in again."
    : null;
}

export function withRequestId(message: string, error: unknown): string {
  return error instanceof ApiError && error.requestId
    ? `${message} Request ID: ${error.requestId}`
    : message;
}

export function readErrorMessage(error: unknown, fallback: string): string {
  const authMessage = authRefreshMessage(error);
  if (authMessage) return authMessage;
  const message = error instanceof ApiError && error.status === 401
    ? "Your session could not be verified. Please sign in again."
    : error instanceof ApiError && error.status === 403
      ? "You don't have permission to view this information."
      : fallback;
  return withRequestId(message, error);
}

export class ApiClient {
  constructor(
    private readonly keycloak: Keycloak,
    private readonly apiBaseUrl: string,
  ) {}

  async get(path: string): Promise<unknown> {
    return this.request(path, "GET");
  }

  async post(path: string, body: unknown): Promise<unknown> {
    return this.request(path, "POST", body);
  }

  async put(path: string, body: unknown): Promise<unknown> {
    return this.request(path, "PUT", body);
  }

  async patch(path: string, body: unknown): Promise<unknown> {
    return this.request(path, "PATCH", body);
  }

  async delete(path: string): Promise<void> {
    await this.request(path, "DELETE");
  }

  private async request(path: string, method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE", body?: unknown): Promise<unknown> {
    // Only accept paths on the configured local API, never arbitrary URLs.
    if (!path.startsWith("/") || path.startsWith("//")) {
      throw new Error("Invalid API path.");
    }
    try {
      await this.keycloak.updateToken(30);
    } catch {
      throw new AuthRefreshError();
    }
    const token = this.keycloak.token;
    if (!token) throw new AuthRefreshError();

    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(`${this.apiBaseUrl}/api/v1${path}`, {
        method,
        headers: method !== "GET" && method !== "DELETE"
          ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }
          : { Authorization: `Bearer ${token}` },
        body: method !== "GET" && method !== "DELETE" ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
      if (!response.ok) {
        const header = response.headers.get("X-Request-ID");
        // The backend generates UUID request IDs; omit unexpected values.
        const requestId = header && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(header)
          ? header : null;
        let code: string | null = null;
        try {
          const data: unknown = await response.json();
          if (typeof data === "object" && data !== null && "error" in data &&
              typeof data.error === "object" && data.error !== null && "code" in data.error &&
              typeof data.error.code === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(data.error.code)) {
            code = data.error.code;
          }
        } catch {
          // An invalid error body must not hide the HTTP status or request ID.
        }
        throw new ApiError(response.status, requestId, code);
      }
      if (response.status === 204) return null;
      return await response.json() as unknown;
    } finally {
      window.clearTimeout(timeout);
    }
  }
}
