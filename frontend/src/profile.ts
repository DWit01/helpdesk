import { readErrorMessage } from "./api-client.js";
import { keycloak, api, state } from "./screen-context.js";
import { loadAssignedTickets } from "./dashboard.js";
import { loadCategoryAdmin } from "./category-admin.js";

export async function loadProfile(target: HTMLElement): Promise<void> {
  target.hidden = false;
  target.textContent = "Loading your profile...";
  try {
    const data = await api.get("/me");
    if (typeof data !== "object" || data === null || !("username" in data) ||
        !("role" in data) || !("id" in data) || !("email" in data) || !("full_name" in data) ||
        typeof data.username !== "string" || typeof data.role !== "string" ||
        typeof data.id !== "string" || typeof data.email !== "string" ||
        !(data.full_name === null || typeof data.full_name === "string")) throw new Error("Invalid profile");
    target.textContent = `Signed in as ${data.username} (${data.role})`;
    target.append(document.createElement("br"));
    target.append(`Name: ${data.full_name || "Not provided"}`);
    target.append(document.createElement("br"));
    target.append(`Email: ${data.email}`);
    state.userRole = data.role;
    state.userId = data.id;
    window.dispatchEvent(new Event("helpdesk:profile-ready"));
    void loadAssignedTickets();
    void loadCategoryAdmin();
    const expiry = keycloak.tokenParsed?.exp;
    if (typeof expiry === "number" && Number.isFinite(expiry)) {
      const expiresAt = new Date(expiry * 1000);
      if (!Number.isNaN(expiresAt.getTime())) {
        target.append(document.createElement("br"));
        target.append(`Access token expires at ${expiresAt.toLocaleTimeString()}.`);
      }
    }
  } catch (error: unknown) {
    target.textContent = readErrorMessage(error, "We couldn't load your profile. Please try again later.");
  }
}
