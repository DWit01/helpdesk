import { ApiError } from "./api-client.js";
import { api, status, dashboard, ticketsSection, createSection, detailSection, detailTitle, detailStatus, detailContent, retryDetail, backToTickets, commentsSection, commentsStatus, commentsList, retryComments, commentForm, commentBody, commentSubmit, commentSubmitStatus, commentClosed, state, isRecord, positiveId, formatDateTime, ticketLabel } from "./screen-context.js";
import { clearAssignment, setupAssignment, showAssignment } from "./ticket-assignment.js";
import { loadAssignedTickets } from "./dashboard.js";

async function loadComments(id: number): Promise<void> {
  if (!commentsSection || !commentsStatus || !commentsList || !retryComments) return;
  const request = ++state.commentsRequest;
  commentsSection.hidden = false;
  commentsList.replaceChildren();
  commentsStatus.textContent = "Loading comments...";
  retryComments.hidden = true;
  try {
    const data = await api.get(`/tickets/${id}/comments`);
    if (request !== state.commentsRequest || state.selectedTicketId !== id) return;
    if (!Array.isArray(data)) throw new Error("Invalid comments");
    const rows: HTMLLIElement[] = [];
    for (const comment of data as unknown[]) {
      if (!isRecord(comment) || !positiveId(comment.id) || comment.ticket_id !== id ||
          typeof comment.author_id !== "string" || !isRecord(comment.author) ||
          typeof comment.author.username !== "string" || typeof comment.body !== "string" ||
          typeof comment.is_internal !== "boolean" || typeof comment.created_at !== "string") {
        throw new Error("Invalid comment");
      }
      const row = document.createElement("li");
      const meta = document.createElement("p");
      meta.className = "comment-meta";
      meta.textContent = `${comment.author.username} · ${formatDateTime(comment.created_at)}`;
      if (comment.is_internal) {
        const badge = document.createElement("span");
        badge.className = "comment-internal";
        badge.textContent = " · Internal";
        meta.append(badge);
      }
      const body = document.createElement("p");
      body.className = "comment-body";
      body.textContent = comment.body;
      row.append(meta, body);
      rows.push(row);
    }
    commentsList.replaceChildren(...rows);
    commentsStatus.textContent = rows.length === 0 ? "No comments to show." : "";
  } catch (error: unknown) {
    if (request !== state.commentsRequest || state.selectedTicketId !== id) return;
    commentsStatus.textContent = error instanceof ApiError && error.status === 401
      ? "Your session could not be verified. Please sign in again."
      : "We couldn't load comments. Please try again.";
    retryComments.hidden = false;
  }
}

export async function openTicket(id: number): Promise<void> {
  if (!detailSection || !detailStatus || !detailContent || !retryDetail) return;
  const request = ++state.detailRequest;
  clearAssignment();
  state.commentsRequest++;
  if (commentsSection) commentsSection.hidden = true;
  commentsList?.replaceChildren();
  if (commentForm) { commentForm.hidden = true; commentForm.reset(); }
  if (commentSubmit) commentSubmit.disabled = false;
  if (commentSubmitStatus) commentSubmitStatus.textContent = "";
  if (commentClosed) commentClosed.hidden = true;
  state.selectedTicketId = id;
  detailSection.hidden = false;
  if (ticketsSection) ticketsSection.hidden = true;
  if (createSection) createSection.hidden = true;
  if (dashboard) dashboard.hidden = true;
  detailTitle?.focus();
  detailContent.replaceChildren();
  detailStatus.textContent = "Loading ticket...";
  retryDetail.hidden = true;
  try {
    const ticket = await api.get(`/tickets/${id}`);
    if (request !== state.detailRequest) return;
    if (!isRecord(ticket) || ticket.id !== id || typeof ticket.ticket_number !== "string" ||
        typeof ticket.title !== "string" || typeof ticket.description !== "string" ||
        typeof ticket.status !== "string" || typeof ticket.priority !== "string" ||
        !isRecord(ticket.category) || typeof ticket.category.name !== "string" ||
        !isRecord(ticket.requester) || typeof ticket.requester.username !== "string" ||
        !(ticket.assignee === null || (isRecord(ticket.assignee) && typeof ticket.assignee.id === "string" && typeof ticket.assignee.username === "string")) ||
        typeof ticket.created_at !== "string" || typeof ticket.updated_at !== "string" ||
        !(ticket.resolved_at === null || typeof ticket.resolved_at === "string") ||
        !(ticket.closed_at === null || typeof ticket.closed_at === "string")) {
      throw new Error("Invalid ticket detail");
    }
    const fields: [string, string][] = [
      ["Ticket", `${ticket.ticket_number}: ${ticket.title}`],
      ["Description", ticket.description], ["Status", ticket.status.replaceAll("_", " ")],
      ["Priority", ticket.priority], ["Category", ticket.category.name],
      ["Requester", ticket.requester.username],
      ["Assignee", ticket.assignee === null ? "Unassigned" : ticket.assignee.username as string],
      ["Created", formatDateTime(ticket.created_at)], ["Updated", formatDateTime(ticket.updated_at)],
      ["Resolved", ticket.resolved_at === null ? "Not resolved" : formatDateTime(ticket.resolved_at)],
      ["Closed", ticket.closed_at === null ? "Not closed" : formatDateTime(ticket.closed_at)],
    ];
    const details = document.createElement("dl");
    details.className = "detail-fields";
    for (const [label, value] of fields) {
      const field = document.createElement("div");
      field.className = "detail-field";
      if (label === "Ticket" || label === "Description") field.classList.add("detail-field-wide");
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = label;
      if (label === "Status" || label === "Priority") {
        const badge = ticketLabel(label === "Status" ? "status" : "priority", label === "Status" ? ticket.status : ticket.priority);
        badge.textContent = value;
        description.append(badge);
      } else {
        description.textContent = value;
      }
      field.append(term, description);
      details.append(field);
    }
    detailContent.replaceChildren(details);
    detailStatus.textContent = "";
    if (commentForm) commentForm.hidden = ticket.status === "CLOSED";
    if (commentClosed) commentClosed.hidden = ticket.status !== "CLOSED";
    void showAssignment(id, ticket.assignee === null ? null : ticket.assignee.id as string, ticket.status);
    void loadComments(id);
  } catch (error: unknown) {
    if (request !== state.detailRequest) return;
    detailStatus.textContent = error instanceof ApiError && error.status === 401
      ? "Your session could not be verified. Please sign in again."
      : error instanceof ApiError && error.status === 404
        ? "This ticket is unavailable."
        : "We couldn't load this ticket. Please try again.";
    retryDetail.hidden = false;
  }
}


export function setupTicketDetail(): void {
setupAssignment(ticketId => { void openTicket(ticketId); void loadAssignedTickets(); });
retryDetail?.addEventListener("click", () => {
  if (state.selectedTicketId !== null) void openTicket(state.selectedTicketId);
});
retryComments?.addEventListener("click", () => {
  if (state.selectedTicketId !== null) void loadComments(state.selectedTicketId);
});
if (commentForm && commentBody && commentSubmit && commentSubmitStatus) {
  const form = commentForm, body = commentBody, submit = commentSubmit, feedback = commentSubmitStatus;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const ticketId = state.selectedTicketId;
    const text = body.value.trim();
    if (ticketId === null || submit.disabled) return;
    if (!text || text.length > 2000) {
      feedback.textContent = "Enter a comment of up to 2000 characters.";
      return;
    }
    const request = state.detailRequest;
    submit.disabled = true;
    feedback.textContent = "Adding comment...";
    void (async () => {
      try {
        await api.post(`/tickets/${ticketId}/comments`, { body: text, is_internal: false });
        if (request !== state.detailRequest || state.selectedTicketId !== ticketId) return;
        form.reset();
        feedback.textContent = "Comment added.";
        submit.disabled = false;
        void loadComments(ticketId);
      } catch (error: unknown) {
        if (request !== state.detailRequest || state.selectedTicketId !== ticketId) return;
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          feedback.textContent = error.status === 401 ? "Please sign in again." :
            "The comment could not be added. Check the ticket and your permissions.";
          submit.disabled = false;
        } else {
          feedback.textContent = "We couldn't confirm whether the comment was added. Check the comments before trying again.";
        }
      }
    })();
  });
}
backToTickets?.addEventListener("click", () => {
  clearAssignment();
  state.detailRequest++;
  state.commentsRequest++;
  state.selectedTicketId = null;
  if (detailSection) detailSection.hidden = true;
  if (ticketsSection) ticketsSection.hidden = false;
  if (createSection) createSection.hidden = false;
  if (dashboard) dashboard.hidden = false;
  document.querySelector<HTMLElement>("#tickets-title")?.scrollIntoView();
});
}
