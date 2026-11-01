/** Mock api.ts only: a short pause so loading states are visible. Spring replaces the calls. */
export function delay(ms = 250): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
