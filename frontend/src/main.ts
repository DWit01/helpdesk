import { signIn, status, profile, refreshDashboard, keycloak, rememberTicket, rememberedTicket } from "./screen-context.js";
import { loadDashboard } from "./dashboard.js";
import { openTicket, setupTicketDetail } from "./ticket-detail.js";
import { setupTicketList } from "./ticket-list.js";
import { loadProfile } from "./profile.js";
import { setupCategoryAdmin } from "./category-admin.js";

const homeButton = document.querySelector<HTMLButtonElement>(".brand-home");

homeButton?.addEventListener("click", (): void => {
  const ticketDetail = document.querySelector<HTMLElement>("#ticket-detail");
  const backButton = document.querySelector<HTMLButtonElement>("#back-to-tickets");

  // Reuse the existing navigation when a ticket is open.
  if (ticketDetail && !ticketDetail.hidden) {
    backButton?.click();
  }

  // The dashboard is already on index.html, so no page reload is needed.
  window.scrollTo({ top: 0, behavior: "smooth" });
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
          rememberTicket(null);
          void keycloak.logout({ redirectUri }).catch(() => {
            message.textContent = "We couldn't sign you out. Please try again.";
            button.disabled = false;
          });
        });
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
