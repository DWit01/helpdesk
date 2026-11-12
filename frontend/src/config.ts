type HelpdeskConfig = Readonly<{
  apiBaseUrl: string;
  keycloakUrl: string;
}>;

declare global {
  interface Window {
    HELPDESK_CONFIG?: HelpdeskConfig;
  }
}

/**
 * Validate one configured service address and return its origin.
 *
 * HTTPS is accepted for remote services. HTTP is accepted only for
 * local loopback addresses used during development.
 */
function validateOrigin(value: unknown, name: string): string {
  if (typeof value !== "string" || value.length === 0 || value !== value.trim()) {
    throw new Error(`${name} must be a non-empty URL without surrounding spaces.`);
  }

  let url: URL;

  try {
    url = new URL(value);
  } catch {
    throw new Error(`${name} must be a valid URL.`);
  }

  const isLoopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  const isAllowedProtocol =
    url.protocol === "https:" || (url.protocol === "http:" && isLoopback);

  if (
    !isAllowedProtocol ||
    url.username !== "" ||
    url.password !== "" ||
    url.pathname !== "/" ||
    url.search !== "" ||
    url.hash !== ""
  ) {
    throw new Error(`${name} must be an HTTPS origin or a local HTTP origin.`);
  }

  return url.origin;
}

/**
 * Read and validate the public configuration loaded by config.js.
 */
export function getConfig(): HelpdeskConfig {
  const config = window.HELPDESK_CONFIG;

  if (!config) {
    throw new Error("Helpdesk configuration is missing.");
  }

  return Object.freeze({
    apiBaseUrl: validateOrigin(config.apiBaseUrl, "apiBaseUrl"),
    keycloakUrl: validateOrigin(config.keycloakUrl, "keycloakUrl"),
  });
}