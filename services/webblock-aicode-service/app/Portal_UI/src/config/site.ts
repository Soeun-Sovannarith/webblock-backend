export const siteConfig = {
  name: "WebBlock",
  description: "Build a showcase website for your business in minutes.",
  supportEmail: "support@webblock.com",
  /** Website the home page opens (Petal & Stem in src/mocks). */
  demoWebsiteId: "7d3e9b2a-1c4f-4a8e-b6d5-3f2e1a9c8b70",
} as const;

/**
 * Screens that live in the main WebBlock app, not in this standalone portal.
 * Set NEXT_PUBLIC_WEBBLOCK_APP_URL (e.g. "http://localhost:3000") to link to them;
 * without it they fall back to the portal itself.
 */
const appUrl = process.env.NEXT_PUBLIC_WEBBLOCK_APP_URL;

export const appLinks = {
  home: appUrl ? `${appUrl}/websites` : "/",
  editor: (websiteId: string) => (appUrl ? `${appUrl}/websites/${websiteId}/edit` : `/portal/${websiteId}`),
  plans: appUrl ? `${appUrl}/billing/plans` : "/",
};
