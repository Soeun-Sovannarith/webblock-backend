// Owner portal settings. Read on the server at request time: the site gets its settings as environment variables
// when it is deployed, never during the build.
export const siteConfig = {
  name: "WebBlock",
  description: "Manage your website's products and business details.",
} as const;

const SITE_CLIENT_PREFIX = "webblock-site-";

/** This website's WebBlock project id, from its single sign-on client (webblock-site-<projectId>), else "site". */
export function siteId(): string {
  const clientId = process.env.KEYCLOAK_CLIENT_ID ?? "";
  return clientId.startsWith(SITE_CLIENT_PREFIX) ? clientId.slice(SITE_CLIENT_PREFIX.length) : "site";
}

/** Links into the WebBlock app; null when WEBBLOCK_APP_URL is not set, and the portal then hides them. */
export type AppLinks = { home: string; editor: string | null; plans: string | null };

export function appLinks(): AppLinks {
  const appUrl = process.env.WEBBLOCK_APP_URL?.trim().replace(/\/+$/, "");
  return {
    home: "/",
    editor: appUrl ? `${appUrl}/websites/${siteId()}/edit` : null,
    plans: appUrl ? `${appUrl}/billing/plans` : null,
  };
}
