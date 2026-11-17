import { readErrorMessage } from "./api-client.js";
import { api, dashboard, dashboardStatus, dashboardCounts, refreshDashboard, statusFilter, priorityFilter, categoryFilter, state, isRecord, positiveId } from "./screen-context.js";
import { openTicket } from "./ticket-detail.js";

const recentStatus = document.querySelector<HTMLElement>("#recent-tickets-status");
const recentTickets = document.querySelector<HTMLUListElement>("#recent-tickets");
const assignedSection = document.querySelector<HTMLElement>("#assigned-tickets-section");
const assignedStatus = document.querySelector<HTMLElement>("#assigned-tickets-status");
const assignedTickets = document.querySelector<HTMLUListElement>("#assigned-tickets");

export async function loadAssignedTickets(): Promise<void> {
  if (!assignedSection || !assignedStatus || !assignedTickets) return;
  const request = ++state.assignedRequest;
  if (state.userRole !== "agent" && state.userRole !== "admin") {
    assignedSection.hidden = true;
    assignedTickets.replaceChildren();
    return;
  }
  assignedSection.hidden = false;
  assignedTickets.replaceChildren();
  assignedStatus.textContent = "Loading assigned tickets...";
  try {
    const data = await api.get("/tickets?assignee=me&status=OPEN&status=IN_PROGRESS&status=RESOLVED&page=1&page_size=5&sort=-created_at");
    if (request !== state.assignedRequest) return;
    if (!isRecord(data) || !Array.isArray(data.items) || typeof data.total !== "number" ||
        !Number.isSafeInteger(data.total) || data.total < 0) throw new Error("Invalid assigned tickets");
    const rows: HTMLLIElement[] = [];
    for (const ticket of data.items as unknown[]) {
      if (!isRecord(ticket) || !positiveId(ticket.id) || typeof ticket.ticket_number !== "string" ||
          typeof ticket.title !== "string") throw new Error("Invalid assigned ticket");
      const row = document.createElement("li");
      const button = document.createElement("button");
      const ticketId = ticket.id;
      button.type = "button";
      button.className = "ticket-title";
      button.textContent = `${ticket.ticket_number}: ${ticket.title}`;
      button.addEventListener("click", () => { void openTicket(ticketId); });
      row.append(button);
      rows.push(row);
    }
    assignedTickets.replaceChildren(...rows);
    assignedStatus.textContent = data.total === 0
      ? "No open, in-progress or resolved tickets assigned to you."
      : `${data.total} assigned ${data.total === 1 ? "ticket" : "tickets"}${data.total > rows.length ? ` · Showing ${rows.length} newest` : ""}.`;
  } catch (error: unknown) {
    if (request !== state.assignedRequest) return;
    assignedStatus.textContent = readErrorMessage(error, "We couldn't load assigned tickets. Please refresh the dashboard.");
  }
}
async function loadRecentTickets(request: number): Promise<void> {
  if (!recentStatus || !recentTickets) return;
  recentTickets.replaceChildren();
  recentStatus.textContent = "Loading recent tickets...";
  try {
    const data = await api.get("/tickets?page=1&page_size=5&sort=-created_at");
    if (request !== state.dashboardRequest) return;
    if (!isRecord(data) || !Array.isArray(data.items)) throw new Error("Invalid recent tickets");
    const rows: HTMLLIElement[] = [];
    for (const ticket of data.items as unknown[]) {
      if (!isRecord(ticket) || !positiveId(ticket.id) || typeof ticket.ticket_number !== "string" ||
          typeof ticket.title !== "string") throw new Error("Invalid recent ticket");
      const row = document.createElement("li");
      const button = document.createElement("button");
      const ticketId = ticket.id;
      button.type = "button";
      button.className = "ticket-title";
      button.textContent = `${ticket.ticket_number}: ${ticket.title}`;
      button.addEventListener("click", () => { void openTicket(ticketId); });
      row.append(button);
      rows.push(row);
    }
    recentTickets.replaceChildren(...rows);
    recentStatus.textContent = rows.length ? "" : "No recent tickets to show.";
  } catch (error: unknown) {
    if (request !== state.dashboardRequest) return;
    recentStatus.textContent = readErrorMessage(error, "We couldn't load recent tickets. Please refresh the dashboard.");
  }
}

export async function loadDashboard(): Promise<void> {
  if (!dashboard || !dashboardStatus || !dashboardCounts || !refreshDashboard) return;
  const request = ++state.dashboardRequest;
  void loadRecentTickets(request);
  if (state.userRole !== null) void loadAssignedTickets();
  dashboard.hidden = false;
  dashboardCounts.replaceChildren();
  dashboardStatus.textContent = "Loading ticket counts...";
  refreshDashboard.disabled = true;
  try {
    const data = await api.get("/tickets/stats");
    if (request !== state.dashboardRequest) return;
    if (!isRecord(data) || !isRecord(data.by_status) || !isRecord(data.by_priority)) {
      throw new Error("Invalid dashboard data");
    }
    const statuses = ["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"];
    const priorities = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
    const count = (record: Record<string, unknown>, key: string): number => {
      const value = record[key];
      if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
        throw new Error("Invalid ticket count");
      }
      return value;
    };
    const grid = document.createElement("div");
    grid.className = "dashboard-grid";
    let total = 0;
    for (const value of statuses) {
      const amount = count(data.by_status, value);
      total += amount;
      const card = document.createElement("button");
      card.type = "button";
      const label = document.createElement("span");
      label.textContent = value.replaceAll("_", " ");
      const number = document.createElement("strong");
      number.textContent = String(amount);
      card.append(label, number);
      card.addEventListener("click", () => {
        if (!statusFilter) return;
        statusFilter.value = value;
        if (priorityFilter) priorityFilter.value = "";
        if (categoryFilter) categoryFilter.value = "";
        window.dispatchEvent(new Event("helpdesk:clear-search"));
        window.dispatchEvent(new Event("helpdesk:clear-dates"));
        window.dispatchEvent(new Event("helpdesk:clear-assignee"));
        window.dispatchEvent(new Event("helpdesk:clear-requester"));
        statusFilter.dispatchEvent(new Event("change"));
        document.querySelector<HTMLElement>("#tickets-title")?.scrollIntoView();
      });
      grid.append(card);
    }
    const heading = document.createElement("h3");
    heading.textContent = "By priority";
    const list = document.createElement("ul");
    list.className = "dashboard-priorities";
    for (const value of priorities) {
      const row = document.createElement("li");
      row.textContent = `${value}: ${count(data.by_priority, value)}`;
      list.append(row);
    }
    dashboardCounts.replaceChildren(grid, heading, list);
    dashboardStatus.textContent = total === 0 ? "No tickets to show yet." : "";
  } catch (error: unknown) {
    if (request !== state.dashboardRequest) return;
    dashboardStatus.textContent = readErrorMessage(error, "We couldn't load ticket counts. Please try refreshing the dashboard.");
  } finally {
    if (request === state.dashboardRequest) refreshDashboard.disabled = false;
  }
}
