import { ApiError } from "./api-client.js";
import { api, status, ticketsSection, ticketsStatus, ticketsList, previousPage, nextPage, pageSize, retryTickets, statusFilter, priorityFilter, categoryFilter, categoryStatus, retryCategories, resetFilters, createCategory, createButton, state, positiveId, ticketLabel } from "./screen-context.js";
import { openTicket } from "./ticket-detail.js";
import { setupCreateTicket } from "./create-ticket.js";

async function loadCategories(select: HTMLSelectElement, message: HTMLElement, retry: HTMLButtonElement): Promise<void> {
  state.categoriesReady = false;
  select.disabled = true;
  retry.hidden = true;
  message.textContent = "Loading categories...";
  try {
    const data = await api.get("/categories");
    if (!Array.isArray(data)) throw new Error("Invalid categories");
    const options: HTMLOptionElement[] = [];
    for (const item of data as unknown[]) {
      if (typeof item !== "object" || item === null || !("id" in item) || !("name" in item) ||
          typeof item.id !== "number" || !Number.isSafeInteger(item.id) || item.id < 1 ||
          typeof item.name !== "string") throw new Error("Invalid category");
      const option = document.createElement("option");
      option.value = String(item.id);
      option.textContent = item.name;
      options.push(option);
    }
    select.replaceChildren(new Option("All categories", ""), ...options);
    if (createCategory) {
      createCategory.replaceChildren(new Option("Choose a category", ""), ...options.map(option => option.cloneNode(true)));
      createCategory.disabled = options.length === 0;
    }
    if (createButton) createButton.disabled = options.length === 0;
    state.categoriesReady = true;
    message.textContent = options.length === 0 ? "No active categories available." : "";
  } catch {
    message.textContent = "We couldn't load categories. Please try again.";
    retry.hidden = false;
  } finally {
    select.disabled = !state.categoriesReady || state.ticketsLoading;
  }
}

async function loadTickets(section: HTMLElement, message: HTMLElement, list: HTMLUListElement, previous: HTMLButtonElement, next: HTMLButtonElement, size: HTMLSelectElement, retry: HTMLButtonElement): Promise<void> {
  section.hidden = false;
  message.textContent = "Loading tickets...";
  list.replaceChildren();
  previous.disabled = true;
  next.disabled = true;
  size.disabled = true;
  state.ticketsLoading = true;
  if (categoryFilter) categoryFilter.disabled = true;
  if (statusFilter) statusFilter.disabled = true;
  if (priorityFilter) priorityFilter.disabled = true;
  if (resetFilters) resetFilters.disabled = true;
  retry.hidden = true;
  try {
    const params = new URLSearchParams({ page: String(state.currentPage), page_size: size.value });
    if (statusFilter?.value) params.set("status", statusFilter.value);
    if (priorityFilter?.value) params.set("priority", priorityFilter.value);
    if (categoryFilter?.value) params.set("category_id", categoryFilter.value);
    const data = await api.get(`/tickets?${params.toString()}`);
    if (typeof data !== "object" || data === null || !("items" in data) ||
        !("total" in data) || !("pages" in data) || !Array.isArray(data.items) ||
        typeof data.total !== "number" || !Number.isInteger(data.total) || data.total < 0 ||
        typeof data.pages !== "number" || !Number.isInteger(data.pages) || data.pages < 0) {
      throw new Error("Invalid ticket page");
    }
    const rows: HTMLLIElement[] = [];
    for (const item of data.items as unknown[]) {
      if (typeof item !== "object" || item === null || !("id" in item) || !positiveId(item.id) || !("ticket_number" in item) ||
          !("title" in item) || !("status" in item) || !("priority" in item) ||
          typeof item.ticket_number !== "string" || typeof item.title !== "string" ||
          typeof item.status !== "string" || typeof item.priority !== "string") {
        throw new Error("Invalid ticket");
      }
      const row = document.createElement("li");
      const title = document.createElement("button");
      title.type = "button";
      title.className = "ticket-title";
      title.textContent = `${item.ticket_number}: ${item.title}`;
      const ticketId = item.id;
      title.addEventListener("click", () => { void openTicket(ticketId); });
      row.append(title, ticketLabel("status", item.status), ticketLabel("priority", item.priority));
      rows.push(row);
    }
    list.replaceChildren(...rows);
    state.totalPages = data.pages;
    message.textContent = data.total === 0
      ? (statusFilter?.value || priorityFilter?.value || categoryFilter?.value ? "No tickets match your filters." : "No tickets to show.")
      : `Page ${state.currentPage} of ${data.pages} · ${data.total} visible tickets.`;
    previous.disabled = state.currentPage <= 1;
    next.disabled = state.currentPage >= state.totalPages;
  } catch (error: unknown) {
    message.textContent = error instanceof ApiError && error.status === 401
      ? "Your session could not be verified. Please sign in again."
      : "We couldn't load tickets. Please try again later.";
    retry.hidden = false;
  } finally {
    size.disabled = false;
    state.ticketsLoading = false;
    if (categoryFilter) categoryFilter.disabled = !state.categoriesReady;
    if (statusFilter) statusFilter.disabled = false;
    if (priorityFilter) priorityFilter.disabled = false;
    if (resetFilters) resetFilters.disabled = false;
  }
}


export function setupTicketList(): void {
if (ticketsSection && ticketsStatus && ticketsList && previousPage && nextPage && pageSize && retryTickets) {
  const section = ticketsSection, ticketMessage = ticketsStatus, list = ticketsList;
  const previous = previousPage, next = nextPage, size = pageSize, retry = retryTickets;
  const load = (): void => { void loadTickets(section, ticketMessage, list, previous, next, size, retry); };
  previous.addEventListener("click", () => { if (state.currentPage > 1) { state.currentPage--; load(); } });
  next.addEventListener("click", () => { if (state.currentPage < state.totalPages) { state.currentPage++; load(); } });
  size.addEventListener("change", () => { state.currentPage = 1; load(); });
  statusFilter?.addEventListener("change", () => { state.currentPage = 1; load(); });
  priorityFilter?.addEventListener("change", () => { state.currentPage = 1; load(); });
  categoryFilter?.addEventListener("change", () => { state.currentPage = 1; load(); });
  resetFilters?.addEventListener("click", () => {
    if (statusFilter) statusFilter.value = "";
    if (priorityFilter) priorityFilter.value = "";
    if (categoryFilter) categoryFilter.value = "";
    state.currentPage = 1;
    load();
  });
  retry.addEventListener("click", load);
  if (categoryFilter && categoryStatus && retryCategories) {
    const categorySelect = categoryFilter, categoryMessage = categoryStatus, categoryRetry = retryCategories;
    categoryRetry.addEventListener("click", () => { void loadCategories(categorySelect, categoryMessage, categoryRetry); });
    void loadCategories(categorySelect, categoryMessage, categoryRetry);
  }
  setupCreateTicket(load);
  load();
}
}
