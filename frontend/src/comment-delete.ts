import { ApiError, authRefreshMessage, withRequestId, writeErrorMessage } from "./api-client.js";
import { api, state } from "./screen-context.js";

export function addCommentDelete(
  row: HTMLLIElement,
  ticketId: number,
  commentId: number,
  authorId: string,
  onDeleted: () => void,
): void {
  if (state.ticketStatus === "CLOSED" || !state.userId ||
      (state.userRole !== "admin" && state.userId !== authorId)) return;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "Delete comment";
  const confirmation = document.createElement("div");
  confirmation.className = "comment-delete-confirm";
  confirmation.hidden = true;
  const prompt = document.createElement("p");
  prompt.textContent = "Delete this comment? This cannot be undone.";
  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.textContent = "Confirm delete";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = "Cancel";
  const feedback = document.createElement("p");
  feedback.className = "status";
  feedback.setAttribute("role", "status");
  confirmation.append(prompt, confirm, cancel, feedback);
  row.append(remove, confirmation);
  remove.addEventListener("click", () => {
    confirmation.hidden = false;
    remove.hidden = true;
    confirm.focus();
  });
  cancel.addEventListener("click", () => {
    confirmation.hidden = true;
    remove.hidden = false;
    remove.focus();
  });
  confirm.addEventListener("click", () => {
    if (confirm.disabled || state.selectedTicketId !== ticketId) return;
    const detailRequest = state.detailRequest, commentsRequest = state.commentsRequest;
    confirm.disabled = true;
    cancel.disabled = true;
    feedback.textContent = "Deleting comment...";
    void (async () => {
      try {
        await api.delete(`/comments/${commentId}`);
        if (state.selectedTicketId !== ticketId || detailRequest !== state.detailRequest ||
            commentsRequest !== state.commentsRequest) return;
        onDeleted();
      } catch (error: unknown) {
        if (state.selectedTicketId !== ticketId || detailRequest !== state.detailRequest ||
            commentsRequest !== state.commentsRequest) return;
        const authMessage = authRefreshMessage(error);
        if (authMessage || (error instanceof ApiError && error.status >= 400 && error.status < 500)) {
          feedback.textContent = authMessage ?? writeErrorMessage(error, "The comment could not be deleted.");
          confirm.disabled = false;
          cancel.disabled = false;
        } else {
          feedback.textContent = withRequestId("We couldn't confirm deletion. Reopen the ticket to check before trying again.", error);
          cancel.disabled = false;
        }
      }
    })();
  });
}
