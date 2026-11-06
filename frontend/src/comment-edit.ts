import { ApiError, authRefreshMessage, withRequestId, writeErrorMessage } from "./api-client.js";
import { api, state } from "./screen-context.js";

export function addCommentEdit(
  row: HTMLLIElement,
  ticketId: number,
  commentId: number,
  authorId: string,
  originalText: string,
  onSaved: () => void,
): void {
  if (state.ticketStatus === "CLOSED" || !state.userId ||
      (state.userRole !== "admin" && state.userId !== authorId)) return;
  const edit = document.createElement("button");
  edit.type = "button";
  edit.textContent = "Edit comment";
  const form = document.createElement("form");
  form.className = "comment-edit-form";
  form.hidden = true;
  const label = document.createElement("label");
  const textarea = document.createElement("textarea");
  textarea.id = `comment-edit-${commentId}`;
  textarea.required = true;
  textarea.maxLength = 2000;
  textarea.rows = 3;
  label.htmlFor = textarea.id;
  label.textContent = "Edit comment text";
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "Save comment";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = "Cancel";
  const feedback = document.createElement("p");
  feedback.className = "status";
  feedback.setAttribute("role", "status");
  form.append(label, textarea, save, cancel, feedback);
  row.append(edit, form);
  edit.addEventListener("click", () => {
    textarea.value = originalText;
    feedback.textContent = "";
    form.hidden = false;
    edit.hidden = true;
    textarea.focus();
  });
  cancel.addEventListener("click", () => { form.hidden = true; edit.hidden = false; });
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (save.disabled || !form.reportValidity()) return;
    const text = textarea.value.trim();
    if (!text || text.length > 2000) { feedback.textContent = "Enter a comment of up to 2000 characters."; return; }
    if (text === originalText) { feedback.textContent = "There are no changes to save."; return; }
    const detailRequest = state.detailRequest, commentsRequest = state.commentsRequest;
    save.disabled = true;
    cancel.disabled = true;
    feedback.textContent = "Saving comment...";
    void (async () => {
      try {
        await api.put(`/comments/${commentId}`, { body: text });
        if (state.selectedTicketId !== ticketId || detailRequest !== state.detailRequest ||
            commentsRequest !== state.commentsRequest) return;
        onSaved();
      } catch (error: unknown) {
        if (state.selectedTicketId !== ticketId || detailRequest !== state.detailRequest ||
            commentsRequest !== state.commentsRequest) return;
        const authMessage = authRefreshMessage(error);
        if (authMessage || (error instanceof ApiError && error.status >= 400 && error.status < 500)) {
          feedback.textContent = authMessage ?? writeErrorMessage(error, "The comment could not be saved.");
          save.disabled = false;
          cancel.disabled = false;
        } else {
          feedback.textContent = withRequestId("We couldn't confirm the change. Reopen the ticket to check before trying again.", error);
          cancel.disabled = false;
        }
      }
    })();
  });
}
