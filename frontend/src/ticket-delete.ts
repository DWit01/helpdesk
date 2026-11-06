import { ApiError, authRefreshMessage, withRequestId, writeErrorMessage } from "./api-client.js";
import { api, state } from "./screen-context.js";

const section = document.querySelector<HTMLElement>("#ticket-delete");
const start = document.querySelector<HTMLButtonElement>("#ticket-delete-start");
const confirmation = document.querySelector<HTMLElement>("#ticket-delete-confirmation");
const confirm = document.querySelector<HTMLButtonElement>("#ticket-delete-confirm");
const cancel = document.querySelector<HTMLButtonElement>("#ticket-delete-cancel");
const feedback = document.querySelector<HTMLElement>("#ticket-delete-status");
let generation = 0;

export function clearTicketDelete(): void {
  generation++;
  if (section) section.hidden = true;
  if (confirmation) confirmation.hidden = true;
  if (start) start.hidden = false;
  if (confirm) confirm.disabled = false;
  if (cancel) cancel.disabled = false;
  if (feedback) feedback.textContent = "";
}

export function showTicketDelete(): void {
  if (section) section.hidden = state.userRole !== "admin";
}

export function setupTicketDelete(onDeleted: () => void): void {
  if (!start || !confirmation || !confirm || !cancel || !feedback) return;
  const openButton = start, panel = confirmation, confirmButton = confirm, cancelButton = cancel, message = feedback;
  openButton.addEventListener("click", () => {
    panel.hidden = false;
    openButton.hidden = true;
    confirmButton.focus();
  });
  cancelButton.addEventListener("click", () => {
    panel.hidden = true;
    openButton.hidden = false;
    openButton.focus();
  });
  confirmButton.addEventListener("click", () => {
    const id = state.selectedTicketId;
    if (id === null || state.userRole !== "admin" || confirmButton.disabled) return;
    const request = generation;
    confirmButton.disabled = true;
    cancelButton.disabled = true;
    message.textContent = "Deleting ticket...";
    void (async () => {
      try {
        await api.delete(`/tickets/${id}`);
        if (request !== generation || state.selectedTicketId !== id) return;
        state.needsTicketRefresh = true;
        onDeleted();
      } catch (error: unknown) {
        if (request !== generation || state.selectedTicketId !== id) return;
        const authMessage = authRefreshMessage(error);
        if (authMessage || (error instanceof ApiError && error.status >= 400 && error.status < 500)) {
          message.textContent = authMessage ?? writeErrorMessage(error, "The ticket could not be deleted.");
          confirmButton.disabled = false;
          cancelButton.disabled = false;
        } else {
          message.textContent = withRequestId("We couldn't confirm deletion. Return to the ticket list and check before trying again.", error);
          cancelButton.disabled = false;
        }
      }
    })();
  });
}
