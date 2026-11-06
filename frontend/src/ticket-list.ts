import { readErrorMessage } from "./api-client.js";
import { api, status, ticketsSection, ticketsStatus, ticketsList, previousPage, nextPage, pageSize, retryTickets, statusFilter, priorityFilter, categoryFilter, categoryStatus, retryCategories, resetFilters, createCategory, createButton, state, isRecord, positiveId, ticketLabel, formatDateTime } from "./screen-context.js";
import { openTicket } from "./ticket-detail.js";
import { setupCreateTicket } from "./create-ticket.js";

const searchForm = document.querySelector<HTMLFormElement>("#ticket-search-form");
const searchInput = document.querySelector<HTMLInputElement>("#ticket-search");
const searchSubmit = document.querySelector<HTMLButtonElement>("#ticket-search-submit");
const sortSelect = document.querySelector<HTMLSelectElement>("#ticket-sort");
const dateForm = document.querySelector<HTMLFormElement>("#ticket-date-form");
const dateFrom = document.querySelector<HTMLInputElement>("#ticket-date-from");
const dateBefore = document.querySelector<HTMLInputElement>("#ticket-date-before");
const dateSubmit = document.querySelector<HTMLButtonElement>("#ticket-date-submit");
const dateStatus = document.querySelector<HTMLElement>("#ticket-date-status");
const assigneeSection = document.querySelector<HTMLElement>("#assignee-filter-section");
const assigneeSelect = document.querySelector<HTMLSelectElement>("#assignee-filter");
const assigneeStatus = document.querySelector<HTMLElement>("#assignee-filter-status");
const assigneeRetry = document.querySelector<HTMLButtonElement>("#assignee-filter-retry");
const requesterSection = document.querySelector<HTMLElement>("#requester-filter-section");
const requesterSelect = document.querySelector<HTMLSelectElement>("#requester-filter");
let assigneesReady = false;
let appliedSearch = "";
let appliedDateFrom = "";
let appliedDateBefore = "";
let ticketRequestNumber = 0;

function clearSearch(): void {
  appliedSearch = "";
  if (searchInput) searchInput.value = "";
}

function clearDates(): void {
  appliedDateFrom = "";
  appliedDateBefore = "";
  if (dateFrom) dateFrom.value = "";
  if (dateBefore) dateBefore.value = "";
  if (dateStatus) dateStatus.textContent = "";
}

function clearAssignee(): void {
  if (assigneeSelect) assigneeSelect.value = "";
}

function clearRequester(): void {
  if (requesterSelect) requesterSelect.value = "";
}

async function loadAssignees(): Promise<void> {
  if (!assigneeSection || !assigneeSelect || !assigneeStatus || !assigneeRetry || !requesterSection || !requesterSelect) return;
  if (state.userRole !== "agent" && state.userRole !== "admin") {
    assigneeSection.hidden = true;
    requesterSection.hidden = true;
    return;
  }
  assigneeSection.hidden = false;
  requesterSection.hidden = false;
  assigneesReady = false;
  assigneeSelect.disabled = true;
  requesterSelect.disabled = true;
  assigneeRetry.hidden = true;
  assigneeStatus.textContent = "Loading users for ticket filters...";
  try {
    const data = await api.get("/users?active_only=false");
    if (!Array.isArray(data)) throw new Error("Invalid users");
    const options: HTMLOptionElement[] = [];
    const requesterOptions: HTMLOptionElement[] = [];
    for (const user of data as unknown[]) {
      if (!isRecord(user) || typeof user.id !== "string" || typeof user.username !== "string" ||
          typeof user.role !== "string" || typeof user.is_active !== "boolean") throw new Error("Invalid user");
      if (user.role === "agent" || user.role === "admin") {
        options.push(new Option(`${user.username}${user.is_active ? "" : " (inactive)"}`, user.id));
      }
      requesterOptions.push(new Option(`${user.username}${user.is_active ? "" : " (inactive)"}`, user.id));
    }
    const selected = assigneeSelect.value;
    assigneeSelect.replaceChildren(new Option("All assignees", ""), new Option("Unassigned", "unassigned"), ...options);
    assigneeSelect.value = Array.from(assigneeSelect.options).some(option => option.value === selected) ? selected : "";
    const selectedRequester = requesterSelect.value;
    requesterSelect.replaceChildren(new Option("All requesters", ""), ...requesterOptions);
    requesterSelect.value = requesterOptions.some(option => option.value === selectedRequester) ? selectedRequester : "";
    if (selectedRequester && !requesterSelect.value) {
      if (state.selectedTicketId === null) requesterSelect.dispatchEvent(new Event("change"));
      else state.needsTicketRefresh = true;
    }
    assigneesReady = true;
    assigneeStatus.textContent = "";
  } catch (error: unknown) {
    assigneeStatus.textContent = readErrorMessage(error, "We couldn't load users for ticket filters. Please try again.");
    assigneeRetry.hidden = false;
  } finally {
    assigneeSelect.disabled = !assigneesReady || state.ticketsLoading;
    requesterSelect.disabled = !assigneesReady || state.ticketsLoading;
  }
}

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
    const selectedFilter = select.value;
    const selectedCreateCategory = createCategory?.value ?? "";
    select.replaceChildren(new Option("All categories", ""), ...options);
    select.value = options.some(option => option.value === selectedFilter) ? selectedFilter : "";
    if (selectedFilter && !select.value) {
      if (state.selectedTicketId === null) select.dispatchEvent(new Event("change"));
      else state.needsTicketRefresh = true;
    }
    if (createCategory) {
      createCategory.replaceChildren(new Option("Choose a category", ""), ...options.map(option => option.cloneNode(true)));
      createCategory.value = options.some(option => option.value === selectedCreateCategory) ? selectedCreateCategory : "";
      createCategory.disabled = options.length === 0;
    }
    if (createButton) createButton.disabled = options.length === 0;
    state.categoriesReady = true;
    message.textContent = options.length === 0 ? "No active categories available." : "";
  } catch (error: unknown) {
    message.textContent = readErrorMessage(error, "We couldn't load categories. Please try again.");
    retry.hidden = false;
  } finally {
    select.disabled = !state.categoriesReady || state.ticketsLoading;
  }
}

async function loadTickets(section: HTMLElement, message: HTMLElement, list: HTMLTableSectionElement, previous: HTMLButtonElement, next: HTMLButtonElement, size: HTMLSelectElement, retry: HTMLButtonElement): Promise<void> {
  const request = ++ticketRequestNumber;
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
  if (searchSubmit) searchSubmit.disabled = true;
  if (sortSelect) sortSelect.disabled = true;
  if (dateSubmit) dateSubmit.disabled = true;
  if (assigneeSelect) assigneeSelect.disabled = true;
  if (requesterSelect) requesterSelect.disabled = true;
  retry.hidden = true;
  try {
    const params = new URLSearchParams({ page: String(state.currentPage), page_size: size.value });
    if (sortSelect) params.set("sort", sortSelect.value);
    if (statusFilter?.value) params.set("status", statusFilter.value);
    if (priorityFilter?.value) params.set("priority", priorityFilter.value);
    if (categoryFilter?.value) params.set("category_id", categoryFilter.value);
    if (appliedSearch) params.set("q", appliedSearch);
    if (appliedDateFrom) params.set("created_from", appliedDateFrom);
    if (appliedDateBefore) params.set("created_to", appliedDateBefore);
    if (assigneeSelect?.value === "unassigned") params.set("unassigned_only", "true");
    else if (assigneeSelect?.value) params.set("assignee_id", assigneeSelect.value);
    if (requesterSelect?.value) params.set("requester_id", requesterSelect.value);
    const data = await api.get(`/tickets?${params.toString()}`);
    if (request !== ticketRequestNumber) return;
    if (typeof data !== "object" || data === null || !("items" in data) ||
        !("total" in data) || !("pages" in data) || !Array.isArray(data.items) ||
        typeof data.total !== "number" || !Number.isInteger(data.total) || data.total < 0 ||
        typeof data.pages !== "number" || !Number.isInteger(data.pages) || data.pages < 0) {
      throw new Error("Invalid ticket page");
    }
    const rows: HTMLTableRowElement[] = [];
    for (const item of data.items as unknown[]) {
      if (!isRecord(item) || !positiveId(item.id) ||
          typeof item.ticket_number !== "string" || typeof item.title !== "string" ||
          typeof item.status !== "string" || typeof item.priority !== "string" ||
          !isRecord(item.category) || typeof item.category.name !== "string" ||
          !(item.assignee === null || (isRecord(item.assignee) && typeof item.assignee.username === "string")) ||
          typeof item.created_at !== "string") {
        throw new Error("Invalid ticket");
      }
      const row = document.createElement("tr");
      const title = document.createElement("button");
      title.type = "button";
      title.className = "ticket-title";
      title.textContent = item.title;
      const ticketId = item.id;
      title.addEventListener("click", () => { void openTicket(ticketId); });
      const cells: [string, string | HTMLElement][] = [
        ["Ticket", item.ticket_number], ["Title", title], ["Category", item.category.name],
        ["Priority", ticketLabel("priority", item.priority)], ["Status", ticketLabel("status", item.status)],
        ["Assignee", item.assignee === null ? "Unassigned" : item.assignee.username as string],
        ["Created", formatDateTime(item.created_at)],
      ];
      for (const [label, value] of cells) {
        const cell = document.createElement("td");
        cell.dataset.label = label;
        if (typeof value === "string") cell.textContent = value;
        else cell.append(value);
        row.append(cell);
      }
      rows.push(row);
    }
    list.replaceChildren(...rows);
    state.totalPages = data.pages;
    message.textContent = data.total === 0
      ? (statusFilter?.value || priorityFilter?.value || categoryFilter?.value || appliedSearch || appliedDateFrom || appliedDateBefore || assigneeSelect?.value || requesterSelect?.value ? "No tickets match your filters." : "No tickets to show.")
      : `Page ${state.currentPage} of ${data.pages} · ${data.total} visible tickets.`;
    previous.disabled = state.currentPage <= 1;
    next.disabled = state.currentPage >= state.totalPages;
  } catch (error: unknown) {
    if (request !== ticketRequestNumber) return;
    message.textContent = readErrorMessage(error, "We couldn't load tickets. Please try again later.");
    retry.hidden = false;
  } finally {
    if (request !== ticketRequestNumber) return;
    size.disabled = false;
    state.ticketsLoading = false;
    if (categoryFilter) categoryFilter.disabled = !state.categoriesReady;
    if (statusFilter) statusFilter.disabled = false;
    if (priorityFilter) priorityFilter.disabled = false;
    if (resetFilters) resetFilters.disabled = false;
    if (searchSubmit) searchSubmit.disabled = false;
    if (sortSelect) sortSelect.disabled = false;
    if (dateSubmit) dateSubmit.disabled = false;
    if (assigneeSelect) assigneeSelect.disabled = !assigneesReady;
    if (requesterSelect) requesterSelect.disabled = !assigneesReady;
  }
}


export function setupTicketList(): void {
if (ticketsSection && ticketsStatus && ticketsList && previousPage && nextPage && pageSize && retryTickets) {
  const section = ticketsSection, ticketMessage = ticketsStatus, list = ticketsList;
  const previous = previousPage, next = nextPage, size = pageSize, retry = retryTickets;
  const load = (): void => { void loadTickets(section, ticketMessage, list, previous, next, size, retry); };
  searchForm?.addEventListener("submit", event => {
    event.preventDefault();
    if (searchSubmit?.disabled) return;
    appliedSearch = searchInput?.value.trim() ?? "";
    state.currentPage = 1;
    load();
  });
  window.addEventListener("helpdesk:clear-search", clearSearch);
  window.addEventListener("helpdesk:clear-dates", clearDates);
  window.addEventListener("helpdesk:clear-assignee", clearAssignee);
  window.addEventListener("helpdesk:clear-requester", clearRequester);
  window.addEventListener("helpdesk:profile-ready", () => { void loadAssignees(); });
  assigneeRetry?.addEventListener("click", () => { void loadAssignees(); });
  assigneeSelect?.addEventListener("change", () => { state.currentPage = 1; load(); });
  requesterSelect?.addEventListener("change", () => { state.currentPage = 1; load(); });
  dateForm?.addEventListener("submit", event => {
    event.preventDefault();
    if (dateSubmit?.disabled || !dateForm.reportValidity()) return;
    const from = dateFrom?.value ?? "", before = dateBefore?.value ?? "";
    if (from && before && from >= before) {
      if (dateStatus) dateStatus.textContent = "Dates not applied: 'created before' must be later than the start date.";
      return;
    }
    appliedDateFrom = from;
    appliedDateBefore = before;
    if (dateStatus) dateStatus.textContent = "";
    state.currentPage = 1;
    load();
  });
  previous.addEventListener("click", () => { if (state.currentPage > 1) { state.currentPage--; load(); } });
  next.addEventListener("click", () => { if (state.currentPage < state.totalPages) { state.currentPage++; load(); } });
  size.addEventListener("change", () => { state.currentPage = 1; load(); });
  sortSelect?.addEventListener("change", () => { state.currentPage = 1; load(); });
  statusFilter?.addEventListener("change", () => { state.currentPage = 1; load(); });
  priorityFilter?.addEventListener("change", () => { state.currentPage = 1; load(); });
  categoryFilter?.addEventListener("change", () => { state.currentPage = 1; load(); });
  resetFilters?.addEventListener("click", () => {
    clearSearch();
    clearDates();
    clearAssignee();
    clearRequester();
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
    window.addEventListener("helpdesk:categories-changed", () => { void loadCategories(categorySelect, categoryMessage, categoryRetry); });
    void loadCategories(categorySelect, categoryMessage, categoryRetry);
  }
  setupCreateTicket(load);
  load();
}
}
