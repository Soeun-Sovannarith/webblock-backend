// Owned by the WebBlock harness and refreshed on every run. The business profile in WebBlock's shared catalog (the
// "business_profiles" table in db/schema.ts, one row per website, created by the harness at deploy): edited in the
// owner portal (/portal/info). Once the owner has saved it there, the storefront route shows it in place of the details
// written into the pages. The logo is a file in object storage (lib/storage.ts), served at /media/logo.
import { currentWebsiteId, getSql } from "./db";
import { isSignedIn } from "./portal-auth";
import type { Photo } from "./products";
import { deleteFile, fileKey, getFile, putFile, type StoredFile } from "./storage";

/** One day as stored, in the onboarding format: "Monday", 12-hour times such as "9:00 AM". */
export type StoredHours = { day: string; isOpen: boolean; opens: string; closes: string };

export type BusinessProfile = {
  businessName: string;
  /** The website's business type, by name (catalog.business_types). */
  businessType: string;
  description: string | null;
  phone: string | null;
  email: string | null;
  address: string | null;
  mapsUrl: string | null;
  /** /media/logo when a logo was uploaded, else null. */
  logoUrl: string | null;
  hours: StoredHours[] | null;
  timezone: string;
  /** True once the owner saved the profile in the portal; only then does it replace the page content. */
  editedInPortal: boolean;
  updatedAt: string;
};

export type BusinessProfileInput = {
  description: string;
  phone: string;
  email: string;
  address: string;
  mapsUrl: string | null;
  hours: StoredHours[];
  /** A new logo; undefined keeps the current one. */
  logo?: Photo;
};

type Row = Omit<BusinessProfile, "updatedAt"> & { updatedAt: Date };

/** The website's business profile; null without a database or a profile. */
export async function getBusinessProfile(): Promise<BusinessProfile | null> {
  const sql = getSql();
  if (!sql) return null;
  try {
    const [row] = await sql<Row[]>`
      SELECT bp.business_name AS "businessName", bt.name AS "businessType", bp.description, bp.phone, bp.email,
             bp.address, bp.maps_url AS "mapsUrl", bp.timezone, bp.opening_hours AS hours,
             CASE WHEN bp.logo_storage_key IS NULL THEN NULL ELSE '/media/logo?v=' || md5(bp.logo_storage_key) END AS "logoUrl",
             bp.portal_edited_at IS NOT NULL AS "editedInPortal", bp.updated_at AS "updatedAt"
      FROM business_profiles bp
      JOIN websites w ON w.website_id = bp.website_id
      JOIN business_types bt ON bt.business_type_id = w.business_type_id
      LIMIT 1`;
    return row ? { ...row, hours: Array.isArray(row.hours) ? row.hours : null, updatedAt: row.updatedAt.toISOString() } : null;
  } catch (error) {
    console.error("[webblock] Could not load the business profile:", error);
    return null;
  }
}

/** The uploaded logo, or null. Public, like the site that shows it. */
export async function businessLogo(): Promise<StoredFile | null> {
  const sql = getSql();
  if (!sql) return null;
  const [row] = await sql<{ key: string | null }[]>`SELECT logo_storage_key AS key FROM business_profiles LIMIT 1`;
  return row?.key ? getFile(row.key) : null;
}

/** Saves the profile (the owner only). The harness creates the website's profile when it deploys the site. */
export async function saveBusinessProfile(input: BusinessProfileInput) {
  if (!(await isSignedIn())) throw new Error("Only the signed-in owner can change the business profile.");
  const sql = getSql();
  if (!sql) throw new Error("The site has no database settings.");
  const hours = sql.json(input.hours);
  let logoKey: string | undefined;
  if (input.logo) {
    const websiteId = await currentWebsiteId();
    if (!websiteId) throw new Error("This website has no active catalog.");
    logoKey = fileKey(websiteId, "logo", input.logo.contentType);
    await putFile(logoKey, input.logo.bytes, input.logo.contentType);
  }
  try {
    const [previous] = await sql<{ key: string | null }[]>`SELECT logo_storage_key AS key FROM business_profiles LIMIT 1`;
    const updated = logoKey === undefined
      ? await sql`
          UPDATE business_profiles
          SET description = ${input.description}, phone = ${input.phone}, email = ${input.email}, address = ${input.address},
              maps_url = ${input.mapsUrl}, opening_hours = ${hours}, portal_edited_at = now()
          RETURNING website_id`
      : await sql`
          UPDATE business_profiles
          SET description = ${input.description}, phone = ${input.phone}, email = ${input.email}, address = ${input.address},
              maps_url = ${input.mapsUrl}, opening_hours = ${hours}, portal_edited_at = now(),
              logo_storage_key = ${logoKey}, logo_byte_size = ${input.logo!.bytes.length}
          RETURNING website_id`;
    if (updated.length === 0) throw new Error("This website has no business profile yet; it is created when WebBlock deploys the site.");
    if (logoKey && previous?.key) await deleteFile(previous.key);
  } catch (error) {
    if (logoKey) await deleteFile(logoKey);
    throw error;
  }
}

/** The Location block text, in the format onboarding writes (business_layout._format_schedule_text). */
export function locationText(profile: BusinessProfile): string {
  const parts: string[] = [];
  if (profile.address) parts.push(profile.address);
  const contact = [profile.phone && `Tel: ${profile.phone}`, profile.email && `Email: ${profile.email}`].filter(Boolean);
  if (contact.length > 0) parts.push(contact.join(" | "));
  if (profile.hours?.length) {
    const lines = profile.hours.map((d) => `• ${d.day}: ${d.isOpen ? `${d.opens} - ${d.closes}` : "Closed"}`);
    parts.push(["\nOpening Hours:", ...lines].join("\n"));
  }
  return parts.length > 0 ? parts.join("\n\n") : "Visit us or reach out online!";
}
