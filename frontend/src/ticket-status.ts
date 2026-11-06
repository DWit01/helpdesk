import { ApiError, authRefreshMessage, withRequestId, writeErrorMessage } from "./api-client.js";
import { api, state } from "./screen-context.js";

const section = document.querySelector<HTMLElement>("#status-actions");
const buttons = document.querySelector<HTMLElement>("#status-buttons");
const message = document.querySelector<HTMLElement>("#status-actions-message");
const resolutionFields = document.querySelector<HTMLElement>("#resolution-fields");
const resolution = document.querySelector<HTMLTextAreaElement>("#resolution-comment");
let generation = 0;

export function clearStatusActions(): void {
  generation++;
  if (section) section.hidden = true;
  buttons?.replaceChildren();
  if (resolution) { resolution.value = ""; resolution.disabled = false; }
}

export function showStatusActions(
  ticketId: number,
  status: string,
  hasAssignee: boolean,
  onChanged: (id: number) => void,
): void {
  if (!section || !buttons || !message || !resolutionFields || !resolution) return;
  const role = state.userRole;
  const staff = role === "agent" || role === "admin";
  const requester = role === "requester";
  const actions: { target: string; label: string; needsAssignee?: boolean }[] = [];
  if (status === "OPEN" && staff) {
    actions.push({ target: "IN_PROGRESS", label: "Start work", needsAssignee: true });
    if (role === "admin") actions.push({ target: "CLOSED", label: "Close ticket" });
  } else if (status === "IN_PROGRESS" && staff) {
    actions.push({ target: "OPEN", label: "Return to open" }, { target: "RESOLVED", label: "Resolve ticket" });
  } else if (status === "RESOLVED" && (staff || requester)) {
    actions.push({ target: "IN_PROGRESS", label: "Reopen ticket", needsAssignee: true },
      { target: "CLOSED", label: "Close ticket" });
  }
  if (actions.length === 0) { section.hidden = true; return; }
  section.hidden = false;
  message.textContent = "";
  resolutionFields.hidden = !actions.some(action => action.target === "RESOLVED");
  resolution.value = "";
  const request = ++generation;
  const actionButtons: HTMLButtonElement[] = [];
  for (const action of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = action.label;
    if (action.needsAssignee && !hasAssignee) {
      button.disabled = true;
      button.title = "An active assignee is required.";
    }
    button.addEventListener("click", () => {
      if (request !== generation || state.selectedTicketId !== ticketId || !message || !resolution) return;
      const comment = resolution.value.trim();
      if (action.target === "RESOLVED" && (!comment || comment.length > 2000)) {
        message.textContent = "Enter a resolution comment of up to 2000 characters.";
        resolution.focus();
        return;
      }
      const priorDisabled = actionButtons.map(item => item.disabled);
      actionButtons.forEach(item => { item.disabled = true; });
      resolution.disabled = true;
      message.textContent = "Updating ticket status...";
      const body = action.target === "RESOLVED"
        ? { status: action.target, resolution_comment: comment }
        : { status: action.target };
      void (async () => {
        try {
          await api.post(`/tickets/${ticketId}/status`, body);
          if (request !== generation || state.selectedTicketId !== ticketId) return;
          state.needsTicketRefresh = true;
          onChanged(ticketId);
        } catch (error: unknown) {
          if (request !== generation || state.selectedTicketId !== ticketId || !message || !resolution) return;
          const authMessage = authRefreshMessage(error);
          if (authMessage) {
            message.textContent = authMessage;
            actionButtons.forEach((item, index) => { item.disabled = priorDisabled[index]; });
            resolution.disabled = false;
          } else if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
            message.textContent = writeErrorMessage(error, "Status could not be changed. Reopen the ticket and try again.");
            actionButtons.forEach((item, index) => { item.disabled = priorDisabled[index]; });
            resolution.disabled = false;
          } else {
            message.textContent = withRequestId("We couldn't confirm the status change. Reopen the ticket to check before trying again.", error);
          }
        }
      })();
    });
    actionButtons.push(button);
  }
  buttons.replaceChildren(...actionButtons);
  if (actions.some(action => action.needsAssignee) && !hasAssignee) {
    message.textContent = "Assign an active staff member before starting or reopening work.";
  }
}
