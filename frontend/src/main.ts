import { signIn, status, profile, refreshDashboard, keycloak, rememberTicket, rememberedTicket, state, expandSection } from "./screen-context.js";
import { loadDashboard } from "./dashboard.js";
import { openTicket, setupTicketDetail } from "./ticket-detail.js";
import { setupTicketList } from "./ticket-list.js";
import { loadProfile } from "./profile.js";
import { setupCategoryAdmin } from "./category-admin.js";

const homeButton = document.querySelector<HTMLButtonElement>(".brand-home");
const workspaceHome = document.querySelector<HTMLButtonElement>(".workspace-brand");
const menuToggle = document.querySelector<HTMLButtonElement>(".workspace-menu-toggle");
const workspaceSignOut = document.querySelector<HTMLButtonElement>("#workspace-sign-out");
const workspaceSignOutStatus = document.querySelector<HTMLElement>("#workspace-sign-out-status");
const workspaceLinks = document.querySelectorAll<HTMLAnchorElement>(".workspace-nav a");
const categoryNav = document.querySelector<HTMLAnchorElement>("#category-nav");

function resetSections(): void {
  document.querySelectorAll<HTMLDetailsElement>("details.workspace-section").forEach((section) => {
    section.open = false;
  });
}

resetSections();
window.addEventListener("pageshow", resetSections);

function closeMenu(): void {
  menuToggle?.setAttribute("aria-expanded", "false");
  menuToggle?.setAttribute("aria-label", "Open navigation menu");
}

menuToggle?.addEventListener("click", () => {
  const open = menuToggle.getAttribute("aria-expanded") !== "true";
  menuToggle.setAttribute("aria-expanded", String(open));
  menuToggle.setAttribute("aria-label", open ? "Close navigation menu" : "Open navigation menu");
});
menuToggle?.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMenu();
});
document.querySelector<HTMLElement>("#workspace-menu")?.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && menuToggle?.getAttribute("aria-expanded") === "true") {
    closeMenu();
    menuToggle.focus();
  }
});
workspaceHome?.addEventListener("click", () => { closeMenu(); homeButton?.click(); });

window.addEventListener("helpdesk:profile-ready", () => {
  if (categoryNav) categoryNav.hidden = state.userRole !== "admin";
});

workspaceLinks.forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    const target = document.querySelector<HTMLElement>(link.hash);
    const detail = document.querySelector<HTMLElement>("#ticket-detail");
    if (detail && !detail.hidden) {
      document.querySelector<HTMLButtonElement>("#back-to-tickets")?.click();
    }
    if (!target || target.hidden) return;
    expandSection(target);
    closeMenu();
    target.querySelector<HTMLElement>(":scope > details > summary")?.focus({ preventScroll: true });
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

homeButton?.addEventListener("click", (): void => {
  rememberTicket(null);
  window.location.assign(new URL("./index.html", window.location.href).href);
});

if (signIn && status) {
  const button = signIn;
  const message = status;
  const redirectUri = `${window.location.origin}${window.location.pathname}`;
  button.disabled = true;
  message.textContent = "Connecting to sign-in...";

  async function initialise(): Promise<void> {
    try {
      const authenticated = await keycloak.init({
        onLoad: "check-sso",
        pkceMethod: "S256",
        redirectUri,
        checkLoginIframe: false,
      });

      document.body.classList.toggle("signed-in", authenticated);

      if (authenticated) {
        button.textContent = "Sign out";
        message.textContent = "You are signed in.";
        refreshDashboard?.addEventListener("click", () => { void loadDashboard(); });
        void loadDashboard();
        setupTicketDetail();
        setupCategoryAdmin();
        setupTicketList();
        if (profile) await loadProfile(profile);
        const ticketId = rememberedTicket();
        if (ticketId !== null) void openTicket(ticketId);
        button.addEventListener("click", () => {
          button.disabled = true;
          if (workspaceSignOut) workspaceSignOut.disabled = true;
          if (workspaceSignOutStatus) workspaceSignOutStatus.textContent = "";
          rememberTicket(null);
          void keycloak.logout({ redirectUri }).catch(() => {
            message.textContent = "We couldn't sign you out. Please try again.";
            if (workspaceSignOutStatus) workspaceSignOutStatus.textContent = message.textContent;
            if (workspaceSignOut) workspaceSignOut.disabled = false;
            button.disabled = false;
          });
        });
        workspaceSignOut?.addEventListener("click", () => button.click());
      } else {
        button.textContent = "Sign in with Keycloak";
        message.textContent = "Sign in to continue.";
        button.addEventListener("click", () => {
          button.disabled = true;
          void keycloak.login({ redirectUri }).catch(() => {
            message.textContent = "We couldn't start sign-in. Please try again.";
            button.disabled = false;
          });
        });
      }
      button.disabled = false;
    } catch {
      message.textContent = "Sign-in is unavailable. Please try again later.";
    } finally {
      document.body.classList.remove("auth-checking");
    }
  }

  void initialise();
}
