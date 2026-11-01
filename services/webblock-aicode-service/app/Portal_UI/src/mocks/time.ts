// Mock dates are relative to "now" so screens always read "Edited 2 hours ago", "9 days left"…

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

export const hoursAgo = (h: number) => new Date(Date.now() - h * HOUR).toISOString();
export const daysAgo = (d: number) => new Date(Date.now() - d * DAY).toISOString();
export const daysFromNow = (d: number) => new Date(Date.now() + d * DAY).toISOString();
