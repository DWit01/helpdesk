import Keycloak from "keycloak-js";
import { ApiClient } from "./api-client.js";

export const keycloak = new Keycloak({
  url: "http://127.0.0.1:8080",
  realm: "helpdesk",
  clientId: "helpdesk-ui",
});
export const api = new ApiClient(keycloak);

export const signIn = document.querySelector<HTMLButtonElement>("#sign-in");
export const status = document.querySelector<HTMLElement>("#sign-in-status");
export const profile = document.querySelector<HTMLElement>("#profile");
export const dashboard = document.querySelector<HTMLElement>("#dashboard");
export const dashboardStatus = document.querySelector<HTMLElement>("#dashboard-status");
export const dashboardCounts = document.querySelector<HTMLElement>("#dashboard-counts");
export const refreshDashboard = document.querySelector<HTMLButtonElement>("#refresh-dashboard");
export const ticketsSection = document.querySelector<HTMLElement>("#tickets");
export const ticketsStatus = document.querySelector<HTMLElement>("#tickets-status");
export const ticketsList = document.querySelector<HTMLUListElement>("#tickets-list");
export const previousPage = document.querySelector<HTMLButtonElement>("#previous-page");
export const nextPage = document.querySelector<HTMLButtonElement>("#next-page");
export const pageSize = document.querySelector<HTMLSelectElement>("#page-size");
export const retryTickets = document.querySelector<HTMLButtonElement>("#retry-tickets");
export const statusFilter = document.querySelector<HTMLSelectElement>("#status-filter");
export const priorityFilter = document.querySelector<HTMLSelectElement>("#priority-filter");
export const categoryFilter = document.querySelector<HTMLSelectElement>("#category-filter");
export const categoryStatus = document.querySelector<HTMLElement>("#category-status");
export const retryCategories = document.querySelector<HTMLButtonElement>("#retry-categories");
export const resetFilters = document.querySelector<HTMLButtonElement>("#reset-filters");
export const createSection = document.querySelector<HTMLElement>("#create-ticket");
export const ticketForm = document.querySelector<HTMLFormElement>("#ticket-form");
export const createCategory = document.querySelector<HTMLSelectElement>("#ticket-category");
export const createButton = document.querySelector<HTMLButtonElement>("#create-submit");
export const createStatus = document.querySelector<HTMLElement>("#create-status");
export const detailSection = document.querySelector<HTMLElement>("#ticket-detail");
export const detailTitle = document.querySelector<HTMLElement>("#detail-title");
export const detailStatus = document.querySelector<HTMLElement>("#detail-status");
export const detailContent = document.querySelector<HTMLElement>("#detail-content");
export const retryDetail = document.querySelector<HTMLButtonElement>("#retry-detail");
export const backToTickets = document.querySelector<HTMLButtonElement>("#back-to-tickets");
export const commentsSection = document.querySelector<HTMLElement>("#comments");
export const commentsStatus = document.querySelector<HTMLElement>("#comments-status");
export const commentsList = document.querySelector<HTMLOListElement>("#comments-list");
export const retryComments = document.querySelector<HTMLButtonElement>("#retry-comments");
export const commentForm = document.querySelector<HTMLFormElement>("#comment-form");
export const commentBody = document.querySelector<HTMLTextAreaElement>("#comment-body");
export const commentSubmit = document.querySelector<HTMLButtonElement>("#comment-submit");
export const commentSubmitStatus = document.querySelector<HTMLElement>("#comment-submit-status");
export const commentClosed = document.querySelector<HTMLElement>("#comment-closed");
export const state = {
  currentPage: 1,
  totalPages: 0,
  categoriesReady: false,
  ticketsLoading: false,
  detailRequest: 0,
  commentsRequest: 0,
  dashboardRequest: 0,
  assignedRequest: 0,
  userRole: null as string | null,
  selectedTicketId: null as number | null,
};


export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function positiveId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

export function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).format(date);
}

export function ticketLabel(kind: "status" | "priority", value: string): HTMLSpanElement {
  const knownValues = kind === "status"
    ? ["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"]
    : ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
  const label = document.createElement("span");
  label.classList.add("ticket-label");
  if (knownValues.includes(value)) label.classList.add(`${kind}-${value.toLowerCase().replaceAll("_", "-")}`);
  label.textContent = `${kind === "status" ? "Status" : "Priority"}: ${value.replaceAll("_", " ")}`;
  return label;
}
