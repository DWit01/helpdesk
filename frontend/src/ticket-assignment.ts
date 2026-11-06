import { ApiError, authRefreshMessage, readErrorMessage, withRequestId, writeErrorMessage } from "./api-client.js";
import { api, isRecord, state } from "./screen-context.js";

const section = document.querySelector<HTMLElement>("#assignment-section");
const form = document.querySelector<HTMLFormElement>("#assignment-form");
const select = document.querySelector<HTMLSelectElement>("#assignment-user");
const submit = document.querySelector<HTMLButtonElement>("#assignment-submit");
const message = document.querySelector<HTMLElement>("#assignment-status");
const retry = document.querySelector<HTMLButtonElement>("#assignment-retry");
let assignmentRequest = 0;
let currentAssignee: string | null = null;
let ticketStatus = "";

export function clearAssignment(): void {
  assignmentRequest++;
  if (section) section.hidden = true;
}

export async function showAssignment(ticketId: number, assigneeId: string | null, status: string): Promise<void> {
  if (!section || !select || !submit || !message || !retry) return;
  const request = ++assignmentRequest;
  if ((state.userRole !== "agent" && state.userRole !== "admin") || status === "CLOSED") {
    section.hidden = true;
    return;
  }
  currentAssignee = assigneeId;
  ticketStatus = status;
  section.hidden = false;
  select.disabled = true;
  submit.disabled = true;
  retry.hidden = true;
  message.textContent = "Loading available assignees...";
  try {
    const users = await api.get("/users?active_only=true");
    if (request !== assignmentRequest || state.selectedTicketId !== ticketId) return;
    if (!Array.isArray(users)) throw new Error("Invalid users");
    const options: HTMLOptionElement[] = [];
    for (const user of users as unknown[]) {
      if (!isRecord(user) || typeof user.id !== "string" || typeof user.username !== "string" ||
          typeof user.role !== "string" || typeof user.is_active !== "boolean") throw new Error("Invalid user");
      if (user.is_active && (user.role === "agent" || user.role === "admin")) {
        options.push(new Option(user.username, user.id));
      }
    }
    const unassigned = new Option("Unassigned", "");
    unassigned.disabled = status === "IN_PROGRESS";
    select.replaceChildren(unassigned, ...options);
    if (assigneeId && !options.some(option => option.value === assigneeId)) {
      select.add(new Option("Current assignee unavailable", assigneeId, true, true));
      select.options[select.options.length - 1].disabled = true;
    }
    select.value = assigneeId ?? "";
    select.disabled = false;
    submit.disabled = false;
    message.textContent = "";
  } catch (error: unknown) {
    if (request !== assignmentRequest || state.selectedTicketId !== ticketId) return;
    message.textContent = readErrorMessage(error, "We couldn't load assignees. Please try again.");
    retry.hidden = false;
  }
}

export function setupAssignment(onSaved: (ticketId: number) => void): void {
  retry?.addEventListener("click", () => {
    if (state.selectedTicketId !== null) void showAssignment(state.selectedTicketId, currentAssignee, ticketStatus);
  });
  if (!form || !select || !submit || !message) return;
  const selected = select, button = submit, feedback = message;
  form.addEventListener("submit", event => {
    event.preventDefault();
    const ticketId = state.selectedTicketId;
    if (ticketId === null || button.disabled || selected.disabled) return;
    const request = assignmentRequest;
    button.disabled = true;
    selected.disabled = true;
    feedback.textContent = "Saving assignment...";
    void (async () => {
      try {
        await api.post(`/tickets/${ticketId}/assign`, { assignee_id: selected.value || null });
        if (request !== assignmentRequest || state.selectedTicketId !== ticketId) return;
        onSaved(ticketId);
      } catch (error: unknown) {
        if (request !== assignmentRequest || state.selectedTicketId !== ticketId) return;
        const authMessage = authRefreshMessage(error);
        if (authMessage) {
          feedback.textContent = authMessage;
          selected.disabled = false;
          button.disabled = false;
        } else if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          feedback.textContent = writeErrorMessage(error, "Assignment could not be saved. Reopen the ticket and try again.");
          selected.disabled = false;
          button.disabled = false;
        } else {
          feedback.textContent = withRequestId("We couldn't confirm the assignment. Reopen the ticket to check before trying again.", error);
        }
      }
    })();
  });
}
