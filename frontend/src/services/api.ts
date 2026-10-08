// Talks to the local Auto Poster server. Every request carries the per-launch session token.

export type FieldKind = "text" | "secret" | "textarea" | "select" | "checkbox";

export interface FieldSpec {
  key: string;
  label: string;
  kind: FieldKind;
  help: string;
  placeholder: string;
  required: boolean;
  options: { value: string; label: string }[];
  default: string;
}

export interface PlatformLimits {
  max_chars: number;
  max_chars_with_images: number | null;
  count_method: "chars" | "utf16" | "graphemes" | "mastodon";
  max_images: number;
  max_image_bytes: number;
  image_formats: string[];
  requires_text: boolean;
}

export interface Platform {
  id: string;
  name: string;
  description: string;
  configured: boolean;
  limits: PlatformLimits;
  settings_fields: FieldSpec[];
  post_fields: FieldSpec[];
  setup_guide: { steps: string[]; docs_url: string; notes: string[] };
  actions: { id: string; label: string; help: string }[];
}

export interface Problem {
  platform: string;
  message: string;
  level: "error" | "warning";
  field: string | null;
}

export interface PostResult {
  platform: string;
  success: boolean;
  post_id: string | null;
  post_url: string | null;
  message: string;
  error_code: string | null;
  technical_details: string | null;
  retry_after: number | null;
  sent_text?: string | null;
  deleted_at?: string | null;
}

export interface DeleteOutcome {
  platform: string;
  success: boolean;
  message: string;
  technical_details: string | null;
}

export interface ConnectionStatus {
  ok: boolean;
  message: string;
  account_name: string | null;
  error_code: string | null;
  technical_details: string | null;
}

export interface ImageInfo {
  id: string;
  filename: string;
  format: string;
  size_bytes: number;
  width: number;
  height: number;
}

export type ComposeMode = "quick" | "structured";

export interface PostRequest {
  mode: ComposeMode;
  title: string;
  text: string;
  hashtags: string;
  link: string;
  overrides: Record<string, string>;
  platforms: string[];
  image_ids: string[];
  alt_texts: Record<string, string>;
  options: Record<string, Record<string, string>>;
}

export interface Preview {
  text: string;
  customized: boolean;
  problems: Problem[];
}

export interface Prepared {
  previews: Record<string, Preview>;
  problems: Problem[];
}

export interface Draft {
  id: number;
  created_at: string;
  updated_at: string;
  request: PostRequest;
  images: ImageInfo[];
}

export interface ComposeData {
  request: PostRequest;
  images: ImageInfo[];
  missing_images: number;
}

export interface HashtagSet {
  id: number;
  name: string;
  tags: string[];
}

export interface PublishResponse {
  history_id: number;
  status: string;
  results: PostResult[];
}

export interface HistoryEntry {
  id: number;
  created_at: string;
  text: string;
  image_count: number;
  platforms: string[];
  status: string;
  source: string;
  results: PostResult[];
}

export interface ScheduledPost {
  id: number;
  created_at: string;
  updated_at: string;
  scheduled_at: string;
  mode: ComposeMode;
  title: string;
  text: string;
  hashtags: string;
  link: string;
  overrides: Record<string, string>;
  images: ImageInfo[];
  alt_texts: Record<string, string>;
  platforms: string[];
  options: Record<string, Record<string, string>>;
  enabled: boolean;
  status: string;
  note: string | null;
  history_post_id: number | null;
}

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

let tokenPromise: Promise<string> | null = null;

function sessionToken(): Promise<string> {
  tokenPromise ??= fetch("/api/session")
    .then((r) => {
      if (!r.ok) throw new ApiError("Could not connect to Auto Poster. Is it still running?", r.status);
      return r.json();
    })
    .then((body) => body.token as string)
    .catch((err) => {
      tokenPromise = null;
      throw err;
    });
  return tokenPromise;
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Auto-Poster-Token": await sessionToken() };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { method, headers, body: payload });
  } catch {
    throw new ApiError("Could not reach Auto Poster. Make sure the app is still running.", 0);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : `Something went wrong (code ${response.status}).`;
    throw new ApiError(detail, response.status);
  }
  return data as T;
}

export const api = {
  info: () => request<{ secret_storage: string }>("GET", "/info"),
  platforms: () => request<Platform[]>("GET", "/platforms"),
  getSettings: (id: string) => request<Record<string, string>>("GET", `/platforms/${id}/settings`),
  saveSettings: (id: string, values: Record<string, string>) =>
    request<Record<string, string>>("PUT", `/platforms/${id}/settings`, values),
  disconnect: (id: string) => request<{ ok: boolean }>("DELETE", `/platforms/${id}/settings`),
  testConnection: (id: string) => request<ConnectionStatus>("POST", `/platforms/${id}/test`),
  runAction: (id: string, action: string) =>
    request<{ ok: boolean; message: string; open_url?: string }>("POST", `/platforms/${id}/actions/${action}`),
  uploadImage: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ImageInfo>("POST", "/media", form);
  },
  deleteImage: (id: string) => request<{ ok: boolean }>("DELETE", `/media/${id}`),
  prepare: (post: PostRequest) => request<Prepared>("POST", "/prepare", post),
  validate: (post: PostRequest) => request<Record<string, Problem[]>>("POST", "/validate", post),
  publish: (post: PostRequest, requestId: string) =>
    request<PublishResponse>("POST", "/publish", { ...post, request_id: requestId }),
  history: (limit = 50, offset = 0, status = "") =>
    request<HistoryEntry[]>("GET", `/history?limit=${limit}&offset=${offset}${status ? `&status=${status}` : ""}`),
  deleteHistory: (id: number) => request<{ ok: boolean }>("DELETE", `/history/${id}`),
  deleteEverywhere: (id: number) => request<DeleteOutcome[]>("POST", `/history/${id}/delete-everywhere`),
  historyCompose: (id: number) => request<ComposeData>("GET", `/history/${id}/compose`),
  drafts: () => request<Draft[]>("GET", "/drafts"),
  getDraft: (id: number) => request<Draft>("GET", `/drafts/${id}`),
  createDraft: (post: PostRequest) => request<Draft>("POST", "/drafts", post),
  updateDraft: (id: number, post: PostRequest) => request<Draft>("PUT", `/drafts/${id}`, post),
  deleteDraft: (id: number) => request<{ ok: boolean }>("DELETE", `/drafts/${id}`),
  hashtagSets: () => request<HashtagSet[]>("GET", "/hashtag-sets"),
  createHashtagSet: (name: string, tags: string) => request<HashtagSet>("POST", "/hashtag-sets", { name, tags }),
  deleteHashtagSet: (id: number) => request<{ ok: boolean }>("DELETE", `/hashtag-sets/${id}`),
  scheduled: () => request<ScheduledPost[]>("GET", "/scheduled"),
  getScheduled: (id: number) => request<ScheduledPost>("GET", `/scheduled/${id}`),
  createScheduled: (post: PostRequest, when: Date) =>
    request<ScheduledPost>("POST", "/scheduled", { ...post, scheduled_at: when.toISOString() }),
  updateScheduled: (id: number, post: PostRequest, when: Date) =>
    request<ScheduledPost>("PUT", `/scheduled/${id}`, { ...post, scheduled_at: when.toISOString() }),
  setScheduledEnabled: (id: number, enabled: boolean) =>
    request<ScheduledPost>("PUT", `/scheduled/${id}/enabled`, { enabled }),
  sendScheduledNow: (id: number) => request<ScheduledPost>("POST", `/scheduled/${id}/send-now`),
  deleteScheduled: (id: number) => request<{ ok: boolean }>("DELETE", `/scheduled/${id}`),
  downloadBackup: async () => {
    const response = await fetch("/api/backup", { headers: { "X-Auto-Poster-Token": await sessionToken() } });
    if (!response.ok) throw new ApiError("Could not create the backup.", response.status);
    const match = /filename="?([^";]+)"?/.exec(response.headers.get("content-disposition") ?? "");
    return { blob: await response.blob(), filename: match?.[1] ?? "auto-poster-backup.zip" };
  },
  restoreBackup: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ posts: number; images: number }>("POST", "/backup/restore", form);
  },
  storage: () => request<{ media_bytes: number }>("GET", "/storage"),
  cleanupStorage: (days: number) => request<{ freed_bytes: number }>("POST", "/storage/cleanup", { days }),
  // <img> tags can't send the session header, so images are fetched and shown as blob URLs.
  imageUrl: async (id: string) => {
    const response = await fetch(`/api/media/${id}`, { headers: { "X-Auto-Poster-Token": await sessionToken() } });
    if (!response.ok) throw new ApiError("Image not found.", response.status);
    return URL.createObjectURL(await response.blob());
  },
};
