// A failed module import must not leave the page on the loading screen.
void import("./main.js").catch(() => {
  document.body.classList.remove("auth-checking");

  const signIn = document.querySelector<HTMLButtonElement>("#sign-in");
  if (signIn) signIn.disabled = true;

  const status = document.querySelector<HTMLElement>("#sign-in-status");
  if (status) status.textContent = "Helpdesk could not start. Please contact support.";
});
