import { ApiError, authRefreshMessage, withRequestId, writeErrorMessage } from "./api-client.js";
import { api, isRecord, positiveId, state } from "./screen-context.js";

const section = document.querySelector<HTMLElement>("#category-admin");
const message = document.querySelector<HTMLElement>("#category-admin-status");
const list = document.querySelector<HTMLUListElement>("#category-admin-list");
const retry = document.querySelector<HTMLButtonElement>("#category-admin-retry");
const createForm = document.querySelector<HTMLFormElement>("#category-create-form");
const createSubmit = document.querySelector<HTMLButtonElement>("#category-create-submit");
const createStatus = document.querySelector<HTMLElement>("#category-create-status");
const editForm = document.querySelector<HTMLFormElement>("#category-edit-form");
const editName = document.querySelector<HTMLInputElement>("#category-edit-name");
const editDescription = document.querySelector<HTMLTextAreaElement>("#category-edit-description");
const editActive = document.querySelector<HTMLInputElement>("#category-edit-active");
const editSubmit = document.querySelector<HTMLButtonElement>("#category-edit-submit");
const editCancel = document.querySelector<HTMLButtonElement>("#category-edit-cancel");
const editStatus = document.querySelector<HTMLElement>("#category-edit-status");
let editingId: number | null = null;
let requestNumber = 0;

export function hideCategoryAdmin(): void {
  if (section) section.hidden = true;
}

export function showCategoryAdmin(): void {
  if (section && state.userRole === "admin") section.hidden = false;
}

export async function loadCategoryAdmin(): Promise<void> {
  if (!section || !message || !list || !retry) return;
  const request = ++requestNumber;
  if (state.userRole !== "admin") {
    section.hidden = true;
    list.replaceChildren();
    return;
  }
  if (state.selectedTicketId === null) section.hidden = false;
  list.replaceChildren();
  retry.hidden = true;
  message.textContent = "Loading categories...";
  try {
    const data = await api.get("/categories?include_inactive=true");
    if (request !== requestNumber) return;
    if (!Array.isArray(data)) throw new Error("Invalid categories");
    const rows: HTMLLIElement[] = [];
    for (const category of data as unknown[]) {
      if (!isRecord(category) || !positiveId(category.id) || typeof category.name !== "string" ||
          typeof category.is_active !== "boolean" ||
          !(category.description === null || typeof category.description === "string")) {
        throw new Error("Invalid category");
      }
      const row = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = `${category.name} (${category.is_active ? "Active" : "Inactive"})`;
      row.append(title);
      if (category.description) {
        const description = document.createElement("p");
        description.textContent = category.description;
        row.append(description);
      }
      const edit = document.createElement("button");
      edit.type = "button";
      edit.textContent = `Edit ${category.name}`;
      const id = category.id, name = category.name, descriptionText = category.description, active = category.is_active;
      edit.addEventListener("click", () => {
        if (!editForm || !editName || !editDescription || !editActive || !editSubmit || !editStatus || editSubmit.disabled) return;
        editingId = id;
        editName.value = name;
        editDescription.value = descriptionText ?? "";
        editActive.checked = active;
        editStatus.textContent = "";
        editForm.hidden = false;
        editName.focus();
      });
      row.append(edit);
      rows.push(row);
    }
    list.replaceChildren(...rows);
    message.textContent = rows.length === 0 ? "No categories to show." : "";
  } catch (error: unknown) {
    if (request !== requestNumber) return;
    message.textContent = authRefreshMessage(error) ?? withRequestId(error instanceof ApiError && error.status === 401
      ? "Your session could not be verified. Please sign in again."
      : "We couldn't load categories. Please try again.", error);
    retry.hidden = false;
  }
}

export function setupCategoryAdmin(): void {
  retry?.addEventListener("click", () => { void loadCategoryAdmin(); });
  if (editForm && editName && editDescription && editActive && editSubmit && editCancel && editStatus) {
    const form = editForm, nameInput = editName, descriptionInput = editDescription;
    const activeInput = editActive, submit = editSubmit, cancel = editCancel, feedback = editStatus;
    cancel.addEventListener("click", () => {
      editingId = null;
      form.hidden = true;
      form.reset();
      feedback.textContent = "";
    });
    form.addEventListener("submit", event => {
      event.preventDefault();
      const id = editingId;
      if (state.userRole !== "admin" || id === null || submit.disabled || !form.reportValidity()) return;
      const name = nameInput.value.trim(), description = descriptionInput.value.trim();
      if (name.length < 2 || name.length > 60 || description.length > 255) {
        feedback.textContent = "Enter a name of 2 to 60 characters and a description of up to 255 characters.";
        return;
      }
      const body = { name, description: description || null, is_active: activeInput.checked };
      submit.disabled = true;
      cancel.disabled = true;
      feedback.textContent = "Saving category...";
      void (async () => {
        try {
          await api.put(`/categories/${id}`, body);
          editingId = null;
          form.hidden = true;
          form.reset();
          submit.disabled = false;
          cancel.disabled = false;
          void loadCategoryAdmin();
          window.dispatchEvent(new Event("helpdesk:categories-changed"));
        } catch (error: unknown) {
          const authMessage = authRefreshMessage(error);
          if (authMessage) {
            feedback.textContent = authMessage;
            submit.disabled = false;
            cancel.disabled = false;
          } else if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
            feedback.textContent = writeErrorMessage(error, "The category could not be saved. Check the fields and try again.");
            submit.disabled = false;
            cancel.disabled = false;
          } else {
            feedback.textContent = withRequestId("We couldn't confirm the change. Reload the page and check the category before trying again.", error);
            cancel.disabled = false;
          }
        }
      })();
    });
  }
  if (!createForm || !createSubmit || !createStatus) return;
  const form = createForm, submit = createSubmit, feedback = createStatus;
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (state.userRole !== "admin" || submit.disabled || !form.reportValidity()) return;
    const fields = new FormData(form);
    const name = String(fields.get("name") ?? "").trim();
    const description = String(fields.get("description") ?? "").trim();
    if (name.length < 2 || name.length > 60 || description.length > 255) {
      feedback.textContent = "Enter a name of 2 to 60 characters and a description of up to 255 characters.";
      return;
    }
    submit.disabled = true;
    feedback.textContent = "Creating category...";
    void (async () => {
      try {
        await api.post("/categories", { name, description: description || null });
        form.reset();
        feedback.textContent = "Category created.";
        submit.disabled = false;
        void loadCategoryAdmin();
        window.dispatchEvent(new Event("helpdesk:categories-changed"));
      } catch (error: unknown) {
        const authMessage = authRefreshMessage(error);
        if (authMessage) {
          feedback.textContent = authMessage;
          submit.disabled = false;
        } else if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          feedback.textContent = writeErrorMessage(error, "The category could not be created. Check the fields and try again.");
          submit.disabled = false;
        } else {
          feedback.textContent = withRequestId("We couldn't confirm whether the category was created. Reload the page and check the list before trying again.", error);
        }
      }
    })();
  });
}
