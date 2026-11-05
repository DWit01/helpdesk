import { ApiError } from "./api-client.js";
import { api, createSection, ticketForm, createCategory, createButton, createStatus, positiveId, state } from "./screen-context.js";
import { openTicket } from "./ticket-detail.js";
import { loadDashboard } from "./dashboard.js";

export function setupCreateTicket(load: () => void): void {
  if (createSection && ticketForm && createCategory && createButton && createStatus) {
    const form = ticketForm, submit = createButton, feedback = createStatus, category = createCategory;
    createSection.hidden = false;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (submit.disabled || !form.reportValidity()) return;
      const fields = new FormData(form);
      const title = String(fields.get("title") ?? "").trim();
      const description = String(fields.get("description") ?? "").trim();
      if (title.length < 5 || description.length < 10 || !category.value) {
        feedback.textContent = "Enter a title of at least 5 characters, a description of at least 10, and a category.";
        return;
      }
      submit.disabled = true;
      feedback.textContent = "Creating ticket...";
      void (async () => {
        try {
          const result = await api.post("/tickets", {
            title, description, category_id: Number(category.value), priority: fields.get("priority"),
          });
          if (typeof result !== "object" || result === null || !("id" in result) || !positiveId(result.id) || !("ticket_number" in result) ||
              typeof result.ticket_number !== "string") throw new Error("Invalid response");
          feedback.textContent = `Created ticket ${result.ticket_number}.`;
          form.reset();
          state.currentPage = 1;
          load();
          void loadDashboard();
          submit.disabled = false;
          void openTicket(result.id);
        } catch (error: unknown) {
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
            feedback.textContent = error.status === 401 ? "Please sign in again." :
              error.status === 422 ? "Please check the ticket fields and try again." :
              "The ticket could not be created. Please check your details and permissions.";
            submit.disabled = false;
          } else {
            feedback.textContent = "We couldn't confirm whether the ticket was created. Check your ticket list before trying again.";
          }
        }
      })();
    });
  }
}
