// Owned by the WebBlock harness and refreshed on every run. Single sign-on for the product portal (/portal).
//
// The site is a confidential OpenID Connect client of the WebBlock Keycloak realm (authorization code flow with
// PKCE). An owner already signed in to WebBlock passes through Keycloak without a login screen. Only users with
// this site's "owner" client role (the "webblock_roles" ID token claim) get a session. The session is an encrypted,
// httpOnly cookie that lasts one hour; Keycloak's back-channel logout (app/portal/backchannel-logout) ends it early.
// Server-side only. Uses Node's crypto, no extra dependencies.
import { createCipheriv, createDecipheriv, createHash, createPublicKey, hkdfSync, randomBytes, timingSafeEqual, verify } from "node:crypto";
import type { JsonWebKey as Jwk, KeyObject } from "node:crypto";
import { cookies, headers } from "next/headers";
import { getSql } from "./db";

const SESSION_COOKIE = "wb_portal_session";
const LOGIN_COOKIE = "wb_portal_login";
const SESSION_SECONDS = 60 * 60;
const LOGIN_SECONDS = 10 * 60;
const CLOCK_SKEW_SECONDS = 60;
export const OWNER_ROLE = "owner";
const ROLES_CLAIM = "webblock_roles"; // set by the client's protocol mapper (portal_sso.py in the AI Code Service)
const BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout";

export type SsoConfig = { issuer: string; clientId: string; clientSecret: string; siteUrl: string | null };
export type Owner = { sub: string; name: string; sid: string };
type Claims = Record<string, unknown>;
type Metadata = { issuer: string; authorization_endpoint: string; token_endpoint: string; jwks_uri: string; end_session_endpoint?: string };

export function ssoConfig(): SsoConfig | null {
  const issuer = process.env.KEYCLOAK_ISSUER?.replace(/\/+$/, "");
  const clientId = process.env.KEYCLOAK_CLIENT_ID;
  const clientSecret = process.env.KEYCLOAK_CLIENT_SECRET;
  if (!issuer || !clientId || !clientSecret) return null;
  return { issuer, clientId, clientSecret, siteUrl: process.env.SITE_URL?.replace(/\/+$/, "") || null };
}

export function portalEnabled() {
  return ssoConfig() !== null && getSql() !== null;
}

/** The site's public origin: SITE_URL, else the request's host (Keycloak only accepts registered redirect URIs).
 * Build every redirect from this, never from request.url: behind Provisioning's proxy that is the server's own bind
 * address (http://0.0.0.0:8080), which the browser can't reach. Works without SSO settings too. */
export async function siteOrigin(config: SsoConfig | null = ssoConfig()) {
  const siteUrl = config ? config.siteUrl : process.env.SITE_URL?.replace(/\/+$/, "") || null;
  if (siteUrl) return siteUrl;
  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host") ?? "localhost";
  const proto = h.get("x-forwarded-proto") ?? (host.startsWith("localhost") || host.startsWith("127.0.0.1") ? "http" : "https");
  return `${proto.split(",")[0].trim()}://${host.split(",")[0].trim()}`;
}

// --- encoding and sealed cookies -------------------------------------------------------------------------------

const b64url = (data: Buffer) => data.toString("base64url");
const sha256 = (value: string) => createHash("sha256").update(value).digest();

function same(a: string, b: string) {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

function cookieKey(config: SsoConfig, purpose: string) {
  return Buffer.from(hkdfSync("sha256", config.clientSecret, "webblock-portal", purpose, 32));
}

/** AES-256-GCM: the browser can neither read nor change the cookie. The key is derived from the client secret. */
function seal(config: SsoConfig, purpose: string, value: Claims) {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", cookieKey(config, purpose), iv);
  const body = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()]);
  return b64url(Buffer.concat([iv, cipher.getAuthTag(), body]));
}

function unseal(config: SsoConfig, purpose: string, token: string | undefined): Claims | null {
  if (!token) return null;
  try {
    const raw = Buffer.from(token, "base64url");
    const decipher = createDecipheriv("aes-256-gcm", cookieKey(config, purpose), raw.subarray(0, 12));
    decipher.setAuthTag(raw.subarray(12, 28));
    const value = JSON.parse(Buffer.concat([decipher.update(raw.subarray(28)), decipher.final()]).toString("utf8")) as Claims;
    return typeof value.exp === "number" && value.exp > Date.now() / 1000 ? value : null;
  } catch {
    return null;
  }
}

const cookieOptions = (maxAge: number) => ({
  httpOnly: true,
  sameSite: "lax" as const,
  secure: process.env.NODE_ENV === "production",
  path: "/portal",
  maxAge,
});

// --- Keycloak metadata, keys and token checks -------------------------------------------------------------------

const cache = globalThis as unknown as { webblockOidc?: { issuer: string; at: number; metadata: Metadata; keys: Map<string, KeyObject>; keysAt: number } };

async function fetchJson(url: string, init?: RequestInit) {
  const response = await fetch(url, { ...init, cache: "no-store", signal: AbortSignal.timeout(10_000) });
  if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`);
  return response.json();
}

export async function metadata(config: SsoConfig): Promise<Metadata> {
  const hit = cache.webblockOidc;
  if (hit && hit.issuer === config.issuer && Date.now() - hit.at < 60 * 60 * 1000) return hit.metadata;
  const md = (await fetchJson(`${config.issuer}/.well-known/openid-configuration`)) as Metadata;
  if (md.issuer !== config.issuer) throw new Error(`Keycloak reports issuer ${md.issuer}, expected ${config.issuer}`);
  cache.webblockOidc = { issuer: config.issuer, at: Date.now(), metadata: md, keys: new Map(), keysAt: 0 };
  return md;
}

async function signingKey(config: SsoConfig, kid: string) {
  const md = await metadata(config);
  const state = cache.webblockOidc!;
  // Unknown key id: Keycloak may have rotated its keys. Refetch, at most every 30 seconds.
  if (!state.keys.has(kid) && Date.now() - state.keysAt > 30_000) {
    const jwks = (await fetchJson(md.jwks_uri)) as { keys: (Record<string, unknown> & { kid?: string; use?: string })[] };
    state.keys = new Map(
      jwks.keys.filter((k) => k.kid && k.use !== "enc").map((k) => [k.kid!, createPublicKey({ key: k as Jwk, format: "jwk" })]),
    );
    state.keysAt = Date.now();
  }
  const key = state.keys.get(kid);
  if (!key) throw new Error(`Unknown signing key ${kid}`);
  return key;
}

/** Verify a Keycloak-signed JWT (RS256 or ES256) issued to this client, and return its claims. */
export async function verifyToken(config: SsoConfig, token: string): Promise<Claims> {
  const [h, p, s] = token.split(".");
  if (!h || !p || !s) throw new Error("Malformed token");
  const header = JSON.parse(Buffer.from(h, "base64url").toString("utf8")) as { alg?: string; kid?: string };
  if (!header.kid || (header.alg !== "RS256" && header.alg !== "ES256")) throw new Error(`Unsupported token algorithm ${header.alg}`);
  const key = await signingKey(config, header.kid);
  if (key.asymmetricKeyType !== (header.alg === "RS256" ? "rsa" : "ec")) throw new Error("Token algorithm does not match its key");
  const signed = Buffer.from(`${h}.${p}`);
  const signature = Buffer.from(s, "base64url");
  const ok = header.alg === "RS256"
    ? verify("sha256", signed, key, signature)
    : verify("sha256", signed, { key, dsaEncoding: "ieee-p1363" }, signature);
  if (!ok) throw new Error("Bad token signature");

  const claims = JSON.parse(Buffer.from(p, "base64url").toString("utf8")) as Claims;
  const now = Date.now() / 1000;
  const audience = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (claims.iss !== config.issuer) throw new Error("Token from another issuer");
  if (!audience.includes(config.clientId)) throw new Error("Token issued to another client");
  if (claims.azp !== undefined && claims.azp !== config.clientId) throw new Error("Token authorized for another client");
  if (typeof claims.exp === "number" && claims.exp < now - CLOCK_SKEW_SECONDS) throw new Error("Token expired");
  if (typeof claims.iat !== "number" || claims.iat > now + CLOCK_SKEW_SECONDS) throw new Error("Token issued in the future");
  return claims;
}

// --- sign-in flow (app/portal/login and app/portal/callback) -----------------------------------------------------

/** Start sign-in: remember state, nonce and PKCE verifier in a sealed cookie and return Keycloak's authorize URL. */
export async function beginLogin(config: SsoConfig, options: { forceLogin?: boolean } = {}) {
  const md = await metadata(config);
  const redirectUri = `${await siteOrigin(config)}/portal/callback`;
  const state = b64url(randomBytes(24));
  const nonce = b64url(randomBytes(24));
  const verifier = b64url(randomBytes(32));
  (await cookies()).set(LOGIN_COOKIE, seal(config, "login", { state, nonce, verifier, redirectUri, exp: Date.now() / 1000 + LOGIN_SECONDS }), cookieOptions(LOGIN_SECONDS));
  const url = new URL(md.authorization_endpoint);
  url.search = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: redirectUri,
    scope: "openid profile email",
    state,
    nonce,
    code_challenge: b64url(sha256(verifier)),
    code_challenge_method: "S256",
    ...(options.forceLogin ? { prompt: "login" } : {}),
  }).toString();
  return url.toString();
}

export type LoginResult = "ok" | "forbidden" | "failed";

/** Finish sign-in: exchange the code, verify the ID token and start a session if the user owns this site. */
export async function completeLogin(config: SsoConfig, params: URLSearchParams): Promise<LoginResult> {
  const jar = await cookies();
  const login = unseal(config, "login", jar.get(LOGIN_COOKIE)?.value);
  jar.delete({ name: LOGIN_COOKIE, path: "/portal" });
  const code = params.get("code");
  const state = params.get("state");
  if (!login || !code || !state || typeof login.state !== "string" || !same(state, login.state)) return "failed";

  const md = await metadata(config);
  const response = await fetch(md.token_endpoint, {
    method: "POST",
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      authorization: "Basic " + Buffer.from(`${encodeURIComponent(config.clientId)}:${encodeURIComponent(config.clientSecret)}`).toString("base64"),
    },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: String(login.redirectUri),
      code_verifier: String(login.verifier),
    }),
  });
  if (!response.ok) {
    console.error("[webblock] Portal sign-in: token request failed with HTTP", response.status);
    return "failed";
  }
  const tokens = (await response.json()) as { id_token?: string };
  if (!tokens.id_token) return "failed";
  const claims = await verifyToken(config, tokens.id_token);
  if (typeof claims.nonce !== "string" || !same(claims.nonce, String(login.nonce))) return "failed";
  if (typeof claims.sub !== "string" || typeof claims.sid !== "string" || typeof claims.exp !== "number") return "failed";
  // A Keycloak session that already ended (back-channel logout) must not start a new portal session; refusing it
  // here also keeps /portal -> login -> /portal from looping.
  if (await sessionEnded(claims.sid)) return "failed";

  const roles = Array.isArray(claims[ROLES_CLAIM]) ? (claims[ROLES_CLAIM] as unknown[]) : [];
  if (!roles.includes(OWNER_ROLE)) return "forbidden";

  const name = [claims.name, claims.preferred_username, claims.email].find((v) => typeof v === "string") as string | undefined;
  jar.set(SESSION_COOKIE, seal(config, "session", { sub: claims.sub, sid: claims.sid, name: name ?? claims.sub, exp: Date.now() / 1000 + SESSION_SECONDS }), cookieOptions(SESSION_SECONDS));
  return "ok";
}

// --- sessions -------------------------------------------------------------------------------------------------

async function sessionEnded(sid: string) {
  const sql = getSql();
  if (!sql) return true;
  try {
    const rows = await sql`SELECT 1 FROM portal_revoked_sessions WHERE sid = ${sid}`;
    return rows.length > 0;
  } catch (error) {
    console.error("[webblock] Could not check the portal session:", error);
    return true; // fail closed
  }
}

/** The signed-in owner, or null. Checked on every portal request and every product write. */
export async function currentOwner(): Promise<Owner | null> {
  const config = ssoConfig();
  if (!config) return null;
  const session = unseal(config, "session", (await cookies()).get(SESSION_COOKIE)?.value);
  if (!session || typeof session.sub !== "string" || typeof session.sid !== "string") return null;
  if (await sessionEnded(session.sid)) return null;
  return { sub: session.sub, sid: session.sid, name: String(session.name ?? session.sub) };
}

export async function isSignedIn() {
  return (await currentOwner()) !== null;
}

/** Sign out of the portal and of Keycloak (so the WebBlock session ends too); returns where to send the browser. */
export async function endSession() {
  (await cookies()).delete({ name: SESSION_COOKIE, path: "/portal" });
  const config = ssoConfig();
  if (!config) return "/";
  const md = await metadata(config);
  if (!md.end_session_endpoint) return "/";
  const url = new URL(md.end_session_endpoint);
  url.search = new URLSearchParams({ client_id: config.clientId, post_logout_redirect_uri: `${await siteOrigin(config)}/` }).toString();
  return url.toString();
}

/** Keycloak's back-channel logout: verify the logout token and record its session as ended. */
export async function handleBackchannelLogout(config: SsoConfig, logoutToken: string) {
  const claims = await verifyToken(config, logoutToken);
  const events = claims.events as Record<string, unknown> | undefined;
  if (!events || !(BACKCHANNEL_LOGOUT_EVENT in events)) throw new Error("Not a logout token");
  if (claims.nonce !== undefined) throw new Error("A logout token must not carry a nonce");
  if (typeof claims.sid !== "string") throw new Error("Logout token without a session id");
  const sql = getSql();
  if (!sql) return;
  await sql`INSERT INTO portal_revoked_sessions (sid) VALUES (${claims.sid}) ON CONFLICT DO NOTHING`;
  // Sessions last an hour, so older entries can go.
  await sql`DELETE FROM portal_revoked_sessions WHERE revoked_at < now() - interval '1 day'`;
}
