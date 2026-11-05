import { ApiError } from "./api-client.js";
import { keycloak, api, state } from "./screen-context.js";
import { loadAssignedTickets } from "./dashboard.js";

export async function loadProfile(target: HTMLElement): Promise<void> {
  target.hidden = false;
  target.textContent = "Loading your profile...";
  try {
    const data = await api.get("/me");
    if (typeof data !== "object" || data === null || !("username" in data) ||
        !("role" in data) || typeof data.username !== "string" ||
        typeof data.role !== "string") throw new Error("Invalid profile");
    target.textContent = `Signed in as ${data.username} (${data.role}).`;
    state.userRole = data.role;
    void loadAssignedTickets();
    const expiry = keycloak.tokenParsed?.exp;
    if (typeof expiry === "number" && Number.isFinite(expiry)) {
      const expiresAt = new Date(expiry * 1000);
      if (!Number.isNaN(expiresAt.getTime())) {
        target.append(document.createElement("br"));
        target.append(`Access token expires at ${expiresAt.toLocaleTimeString()}.`);
      }
    }
  } catch (error: unknown) {
    target.textContent = error instanceof ApiError && error.status === 401
      ? "Your session could not be verified. Please sign in again."
      : "We couldn't load your profile. Please try again later.";
  }
}
