import { signIn, status, profile, refreshDashboard, keycloak } from "./screen-context.js";
import { loadDashboard } from "./dashboard.js";
import { setupTicketDetail } from "./ticket-detail.js";
import { setupTicketList } from "./ticket-list.js";
import { loadProfile } from "./profile.js";

if (signIn && status) {
  const button = signIn;
  const message = status;
  const redirectUri = `${window.location.origin}${window.location.pathname}`;
  button.disabled = true;
  message.textContent = "Connecting to sign-in...";

  async function initialise(): Promise<void> {
    try {
      const authenticated = await keycloak.init({
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
        if (profile) void loadProfile(profile);
        setupTicketList();
        button.addEventListener("click", () => {
          button.disabled = true;
          void keycloak.logout({ redirectUri }).catch(() => {
            message.textContent = "We couldn't sign you out. Please try again.";
            button.disabled = false;
          });
        });
      } else {
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
    }
  }

  void initialise();
}
