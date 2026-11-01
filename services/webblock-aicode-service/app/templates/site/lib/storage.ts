// Owned by the WebBlock harness and refreshed on every run. Object storage for product images and the business logo:
// any S3-compatible service (AWS S3, MinIO, Cloudflare R2), set by the WebBlock harness at deploy time through
// STORAGE_ENDPOINT, STORAGE_REGION, STORAGE_BUCKET, STORAGE_ACCESS_KEY_ID and STORAGE_SECRET_ACCESS_KEY. The database
// keeps only each file's key and size (catalog.product_images, business_profiles.logo_*); keys start with
// websites/<website id>/. Requests are signed with AWS Signature Version 4 using Node's crypto, with path-style
// addresses (<endpoint>/<bucket>/<key>), which every S3-compatible service accepts. Settings and signatures are
// never logged.
import { createHash, createHmac } from "node:crypto";

type StorageConfig = { endpoint: URL; region: string; bucket: string; accessKeyId: string; secretAccessKey: string };

export type StoredFile = { contentType: string; bytes: Buffer };

const TIMEOUT_MS = 10_000;
const EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

function storageConfig(env: NodeJS.ProcessEnv = process.env): StorageConfig | null {
  const { STORAGE_ENDPOINT, STORAGE_BUCKET, STORAGE_ACCESS_KEY_ID, STORAGE_SECRET_ACCESS_KEY } = env;
  if (!STORAGE_ENDPOINT || !STORAGE_BUCKET || !STORAGE_ACCESS_KEY_ID || !STORAGE_SECRET_ACCESS_KEY) return null;
  try {
    return {
      endpoint: new URL(STORAGE_ENDPOINT),
      region: env.STORAGE_REGION || "us-east-1",
      bucket: STORAGE_BUCKET,
      accessKeyId: STORAGE_ACCESS_KEY_ID,
      secretAccessKey: STORAGE_SECRET_ACCESS_KEY,
    };
  } catch {
    console.error("[webblock] storage settings are invalid: STORAGE_ENDPOINT (not a URL)");
    return null;
  }
}

export function storageConfigured(): boolean {
  return storageConfig() !== null;
}

const sha256Hex = (data: string | Buffer) => createHash("sha256").update(data).digest("hex");
const hmac = (key: string | Buffer, data: string) => createHmac("sha256", key).update(data).digest();

// RFC 3986 encoding of one path segment, as Signature Version 4 requires (encodeURIComponent leaves !'()* alone).
const encodeSegment = (segment: string) =>
  encodeURIComponent(segment).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);

/** Signature Version 4 headers for one request: every header passed is signed, plus x-amz-date and
 * x-amz-content-sha256. Exported for its test against AWS's published example. */
export function signRequest(
  method: string,
  url: URL,
  headers: Record<string, string>,
  payloadSha256: string,
  credentials: { accessKeyId: string; secretAccessKey: string; region: string },
  now: Date = new Date(),
): Record<string, string> {
  const amzDate = now.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const day = amzDate.slice(0, 8);
  const all: Record<string, string> = {
    ...Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v.trim()])),
    host: url.host,
    "x-amz-content-sha256": payloadSha256,
    "x-amz-date": amzDate,
  };
  const names = Object.keys(all).sort();
  const query = [...url.searchParams.entries()]
    .map(([k, v]) => [encodeSegment(k), encodeSegment(v)])
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${k}=${v}`)
    .join("&");
  const canonical = [method, url.pathname, query, names.map((n) => `${n}:${all[n]}\n`).join(""), names.join(";"), payloadSha256].join("\n");
  const scope = `${day}/${credentials.region}/s3/aws4_request`;
  const toSign = ["AWS4-HMAC-SHA256", amzDate, scope, sha256Hex(canonical)].join("\n");
  const key = hmac(hmac(hmac(hmac(`AWS4${credentials.secretAccessKey}`, day), credentials.region), "s3"), "aws4_request");
  const signature = createHmac("sha256", key).update(toSign).digest("hex");
  const { host: _host, ...signed } = all;
  return {
    ...signed,
    authorization: `AWS4-HMAC-SHA256 Credential=${credentials.accessKeyId}/${scope}, SignedHeaders=${names.join(";")}, Signature=${signature}`,
  };
}

async function send(method: "GET" | "PUT" | "DELETE", key: string, body?: Buffer, contentType?: string): Promise<Response> {
  const config = storageConfig();
  if (!config) throw Object.assign(new Error("Object storage is not set up for this website."), { code: "NO_STORAGE" });
  const base = config.endpoint.pathname.replace(/\/+$/, "");
  const url = new URL(config.endpoint.origin);
  url.pathname = `${base}/${encodeSegment(config.bucket)}/${key.split("/").map(encodeSegment).join("/")}`;
  const headers = signRequest(method, url, contentType ? { "content-type": contentType } : {},
    body ? sha256Hex(body) : EMPTY_SHA256, config);
  return fetch(url, { method, headers, body: body ? new Uint8Array(body) : undefined, signal: AbortSignal.timeout(TIMEOUT_MS) });
}

function failure(action: string, response: Response): Error {
  return Object.assign(new Error(`Object storage ${action} failed (HTTP ${response.status}).`), { code: `STORAGE_${response.status}` });
}

export async function putFile(key: string, bytes: Buffer, contentType: string): Promise<void> {
  const response = await send("PUT", key, bytes, contentType);
  if (!response.ok) throw failure("upload", response);
}

/** The file, or null when it doesn't exist. */
export async function getFile(key: string): Promise<StoredFile | null> {
  const response = await send("GET", key);
  if (response.status === 404) return null;
  if (!response.ok) throw failure("download", response);
  return {
    contentType: response.headers.get("content-type") ?? "application/octet-stream",
    bytes: Buffer.from(await response.arrayBuffer()),
  };
}

/** Best effort: a file left behind costs storage, not correctness, so a failure is only logged. */
export async function deleteFile(key: string): Promise<void> {
  try {
    const response = await send("DELETE", key);
    if (!response.ok && response.status !== 404) console.error(`[webblock] ${failure("delete", response).message}`);
  } catch (error) {
    console.error("[webblock] Object storage delete failed:", (error as { code?: string }).code ?? "error");
  }
}

const EXTENSIONS: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/svg+xml": "svg" };

/** The storage key for a new file of this website: websites/<website id>/<folder>/<random>.<extension>. */
export function fileKey(websiteId: string, folder: string, contentType: string): string {
  return `websites/${websiteId}/${folder}/${crypto.randomUUID()}.${EXTENSIONS[contentType] ?? "bin"}`;
}
