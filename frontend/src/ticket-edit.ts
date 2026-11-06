import { ApiError, authRefreshMessage, readErrorMessage, withRequestId, writeErrorMessage } from "./api-client.js";
import { api, isRecord, positiveId, state } from "./screen-context.js";

interface EditableTicket {
  id: number;
  title: string;
  description: string;
  status: string;
  priority: string;
  categoryId: number;
  categoryName: string;
}

const section = document.querySelector<HTMLElement>("#ticket-edit");
const form = document.querySelector<HTMLFormElement>("#ticket-edit-form");
const title = document.querySelector<HTMLInputElement>("#ticket-edit-title");
const description = document.querySelector<HTMLTextAreaElement>("#ticket-edit-description");
const category = document.querySelector<HTMLSelectElement>("#ticket-edit-category");
const priority = document.querySelector<HTMLSelectElement>("#ticket-edit-priority");
const priorityField = document.querySelector<HTMLElement>("#ticket-edit-priority-field");
const submit = document.querySelector<HTMLButtonElement>("#ticket-edit-submit");
const message = document.querySelector<HTMLElement>("#ticket-edit-status");
let generation = 0;
let original: EditableTicket | null = null;

export function clearTicketEdit(): void {
  generation++;
  original = null;
  if (section) section.hidden = true;
}

export async function showTicketEdit(ticket: EditableTicket): Promise<void> {
  if (!section || !title || !description || !category || !priority || !priorityField || !submit || !message) return;
  const role = state.userRole;
  const staff = role === "agent" || role === "admin";
  if (ticket.status === "CLOSED" || !(staff || (role === "requester" && ticket.status === "OPEN"))) {
    section.hidden = true;
    return;
  }
  const request = ++generation;
  original = ticket;
  section.hidden = false;
  title.value = ticket.title;
  description.value = ticket.description;
  priority.value = ticket.priority;
  priorityField.hidden = !staff;
  category.disabled = true;
  submit.disabled = true;
  message.textContent = "Loading categories...";
  try {
    const data = await api.get("/categories");
    if (request !== generation || state.selectedTicketId !== ticket.id) return;
    if (!Array.isArray(data)) throw new Error("Invalid categories");
    const options: HTMLOptionElement[] = [];
    for (const item of data as unknown[]) {
      if (!isRecord(item) || !positiveId(item.id) || typeof item.name !== "string") throw new Error("Invalid category");
      options.push(new Option(item.name, String(item.id)));
    }
    if (!options.some(option => option.value === String(ticket.categoryId))) {
      options.unshift(new Option(`${ticket.categoryName} (current, inactive)`, String(ticket.categoryId)));
    }
    category.replaceChildren(...options);
    category.value = String(ticket.categoryId);
    category.disabled = false;
    submit.disabled = false;
    message.textContent = "";
  } catch (error: unknown) {
    if (request !== generation || state.selectedTicketId !== ticket.id) return;
    message.textContent = readErrorMessage(error, "We couldn't load categories. Reopen the ticket to try again.");
  }
}

export function setupTicketEdit(onSaved: (id: number) => void): void {
  if (!form || !title || !description || !category || !priority || !submit || !message) return;
  const editForm = form, titleInput = title, descriptionInput = description;
  const categoryInput = category, priorityInput = priority, saveButton = submit, feedback = message;
  editForm.addEventListener("submit", event => {
    event.preventDefault();
    const ticket = original;
    if (!ticket || state.selectedTicketId !== ticket.id || saveButton.disabled || !editForm.reportValidity()) return;
    const newTitle = titleInput.value.trim(), newDescription = descriptionInput.value.trim();
    if (newTitle.length < 5 || newTitle.length > 120 || newDescription.length < 10 || newDescription.length > 5000) {
      feedback.textContent = "Enter a title of 5 to 120 characters and a description of 10 to 5000 characters.";
      return;
    }
    const changes: Record<string, string | number> = {};
    if (newTitle !== ticket.title) changes.title = newTitle;
    if (newDescription !== ticket.description) changes.description = newDescription;
    const categoryId = Number(categoryInput.value);
    if (!positiveId(categoryId)) { feedback.textContent = "Choose a category."; return; }
    if (categoryId !== ticket.categoryId) changes.category_id = categoryId;
    if ((state.userRole === "agent" || state.userRole === "admin") && priorityInput.value !== ticket.priority) {
      changes.priority = priorityInput.value;
    }
    if (Object.keys(changes).length === 0) { feedback.textContent = "There are no changes to save."; return; }
    const request = generation;
    saveButton.disabled = true;
    feedback.textContent = "Saving ticket...";
    void (async () => {
      try {
        await api.patch(`/tickets/${ticket.id}`, changes);
        if (request !== generation || state.selectedTicketId !== ticket.id) return;
        state.needsTicketRefresh = true;
        onSaved(ticket.id);
      } catch (error: unknown) {
        if (request !== generation || state.selectedTicketId !== ticket.id) return;
        const authMessage = authRefreshMessage(error);
        if (authMessage) {
          feedback.textContent = authMessage;
          saveButton.disabled = false;
        } else if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          feedback.textContent = writeErrorMessage(error, "The ticket could not be saved. Reopen the ticket and try again.");
          saveButton.disabled = false;
        } else {
          feedback.textContent = withRequestId("We couldn't confirm the change. Reopen the ticket to check before trying again.", error);
        }
      }
    })();
  });
}
