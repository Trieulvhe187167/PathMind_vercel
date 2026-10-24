import { apiFetch, apiUrl, setRuntimeAuthEnabled } from "@/lib/api";
import { clearStoredTheme, DEFAULT_THEME, setTheme } from "@/lib/theme";

// Auth state is resolved at runtime from the backend (`/api/auth/status`),
// not from a build-time/env constant: the browser bundle never sees
// `PATHMIND_AUTH_ENABLED` (not a `NEXT_PUBLIC_` var), and auth is runtime
// config that must not be baked into the bundle. Components observe it via the
// `useAuthStatus` hook (web/hooks/useAuthStatus.ts); `apiFetch`'s redirect gate
// is driven by `setRuntimeAuthEnabled`, which `fetchAuthStatus` calls below.

export interface AuthStatus {
  enabled: boolean;
  authenticated: boolean;
  /** Client-only: the status endpoint could not be reached (never persisted). */
  unavailable?: boolean;
  user_id?: string;
  username?: string;
  role?: string;
  is_admin?: boolean;
  /** Server-side account preset; null for identities without a local account. */
  preset?: "standard" | "learner" | "custom" | null;
  /** Avatar marker: "", "icon:<name>:<color>", or "img:<version>". */
  avatar?: string;
  full_name?: string;
  email?: string;
  email_verified?: boolean;
  /** True when the server can deliver verification emails (SMTP set up). */
  email_verification_available?: boolean;
  learning_policy?: {
    age_band: string;
    locked_persona: string;
    allowed_capabilities: string[];
    default_capability: string;
    allowed_surfaces?: string[];
    reading?: {
      allow_upload: boolean;
      material_ids: string[];
      extensions: string[];
    };
  } | null;
}

const AUTH_STATUS_CACHE_MS = 15_000;
let authStatusRequest: Promise<AuthStatus | null> | null = null;
let cachedAuthStatus: { value: AuthStatus | null; expiresAt: number } | null =
  null;

export function invalidateAuthStatusCache(): void {
  authStatusRequest = null;
  cachedAuthStatus = null;
}

// ---------------------------------------------------------------------------
// Shared auth store
//
// Every component used to start from "unknown / signed out" and wait for its
// own /api/auth/status round trip, so the sidebar, settings and admin-only
// surfaces flashed the wrong state after each navigation, login or logout.
// The last known status is kept in one store (persisted per browser, synced
// across tabs) so the UI renders the right account immediately and is then
// revalidated in the background. The server still enforces every permission;
// this snapshot only drives what the UI shows first.
// ---------------------------------------------------------------------------

const AUTH_SNAPSHOT_KEY = "pathmind-auth-snapshot";
const AUTH_CHANNEL = "pathmind-auth";
type AuthListener = () => void;
const authListeners = new Set<AuthListener>();
/** undefined = not known yet in this browser. */
let authCurrent: AuthStatus | undefined;
let authSnapshotLoaded = false;
let authChannel: BroadcastChannel | null = null;

function publicSnapshot(status: AuthStatus): AuthStatus {
  // Only what the shell needs to render; no learning policy or email.
  return {
    enabled: status.enabled,
    authenticated: status.authenticated,
    user_id: status.user_id,
    username: status.username,
    role: status.role,
    is_admin: status.is_admin,
    avatar: status.avatar,
    preset: status.preset,
  };
}

function readStoredSnapshot(): AuthStatus | undefined {
  try {
    const raw = window.localStorage.getItem(AUTH_SNAPSHOT_KEY);
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as AuthStatus;
    return parsed && typeof parsed.enabled === "boolean" ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function writeStoredSnapshot(status: AuthStatus | undefined): void {
  try {
    if (status) {
      window.localStorage.setItem(
        AUTH_SNAPSHOT_KEY,
        JSON.stringify(publicSnapshot(status)),
      );
    } else {
      window.localStorage.removeItem(AUTH_SNAPSHOT_KEY);
    }
  } catch {
    // Storage disabled: the in-memory store still works for this page.
  }
}

function sameStatus(a: AuthStatus | undefined, b: AuthStatus | undefined): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return JSON.stringify(publicSnapshot(a)) === JSON.stringify(publicSnapshot(b));
}

function setAuthCurrent(status: AuthStatus | undefined, broadcast: boolean): void {
  if (sameStatus(authCurrent, status)) {
    // Keep the richer object (e.g. learning policy) without re-rendering.
    if (status) authCurrent = status;
    return;
  }
  authCurrent = status;
  if (!status?.unavailable) writeStoredSnapshot(status);
  for (const listener of Array.from(authListeners)) listener();
  if (broadcast) {
    ensureAuthChannel();
    try {
      authChannel?.postMessage("changed");
    } catch {
      // ignore
    }
  }
}

function ensureAuthChannel(): void {
  if (authChannel || typeof window === "undefined") return;
  try {
    authChannel = new BroadcastChannel(AUTH_CHANNEL);
    authChannel.onmessage = () => {
      // Another tab signed in or out: show its answer now, then re-check.
      invalidateAuthStatusCache();
      setAuthCurrent(readStoredSnapshot(), false);
      void fetchAuthStatus();
    };
  } catch {
    authChannel = null;
  }
}

/**
 * Last known auth status, synchronously (no network). ``undefined`` until
 * this browser has resolved the account at least once.
 */
export function peekAuthStatus(): AuthStatus | undefined {
  if (typeof window === "undefined") return undefined;
  if (!authSnapshotLoaded) {
    authSnapshotLoaded = true;
    if (authCurrent === undefined) authCurrent = readStoredSnapshot();
  }
  return authCurrent;
}

/** Subscribe to auth changes (login, logout, other tabs, revalidation). */
export function subscribeAuthStatus(listener: AuthListener): () => void {
  ensureAuthChannel();
  authListeners.add(listener);
  return () => {
    authListeners.delete(listener);
  };
}

/** Re-read the account now (after changing the avatar, name, …). */
export function refreshAuthStatus(): Promise<AuthStatus | null> {
  invalidateAuthStatusCache();
  return fetchAuthStatus();
}

/** Forget the signed-in account (sign-out, account deletion). */
export function clearAuthSnapshot(): void {
  invalidateAuthStatusCache();
  const enabled = peekAuthStatus()?.enabled ?? true;
  setAuthCurrent(enabled ? { enabled: true, authenticated: false } : undefined, true);
}

/**
 * Call the backend to check whether the current session is authenticated.
 * Returns null on network error so callers can decide how to handle it.
 */
export function fetchAuthStatus(): Promise<AuthStatus | null> {
  if (cachedAuthStatus && cachedAuthStatus.expiresAt > Date.now()) {
    return Promise.resolve(cachedAuthStatus.value);
  }
  if (!authStatusRequest) {
    authStatusRequest = (async () => {
      try {
        const res = await apiFetch(apiUrl("/api/auth/status"));
        if (!res.ok) return null;
        const status: AuthStatus = await res.json();
        // Record the real auth state so apiFetch's in-session 401 → /login redirect
        // fires only when auth is actually enabled.
        setRuntimeAuthEnabled(Boolean(status.enabled));
        return status;
      } catch {
        return null;
      }
    })()
      .then((status) => {
        // Keep the last good answer while the backend is briefly unreachable.
        if (status) setAuthCurrent(status, true);
        else if (peekAuthStatus() === undefined)
          setAuthCurrent({ enabled: false, authenticated: false, unavailable: true }, false);
        cachedAuthStatus = {
          value: status,
          // Retry unavailable backends quickly; stable answers can be shared
          // across the shell and Settings providers for one navigation.
          expiresAt: Date.now() + (status === null ? 1_000 : AUTH_STATUS_CACHE_MS),
        };
        return status;
      })
      .finally(() => {
        authStatusRequest = null;
      });
  }
  return authStatusRequest;
}

/**
 * POST credentials to the backend. Returns true on success.
 */
export interface AuthFailure {
  ok: false;
  error: string;
  /** Machine-readable reason, e.g. "email_not_verified", "email_taken". */
  code?: string;
  /** Form field the error belongs to (sign-up / verification). */
  field?: string;
  /** For "email_not_verified": the address awaiting verification. */
  email?: string;
  retryAfter?: number;
}

function failureFrom(detail: unknown, fallback: string): AuthFailure {
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const d = detail as Record<string, unknown>;
    return {
      ok: false,
      error: typeof d.message === "string" ? d.message : fallback,
      code: typeof d.code === "string" ? d.code : undefined,
      field: typeof d.field === "string" ? d.field : undefined,
      email: typeof d.email === "string" ? d.email : undefined,
      retryAfter: typeof d.retry_after === "number" ? d.retry_after : undefined,
    };
  }
  return { ok: false, error: detail === undefined ? fallback : extractDetail(detail) };
}

export async function login(
  username: string,
  password: string,
): Promise<{ ok: true } | AuthFailure> {
  try {
    const res = await apiFetch(apiUrl("/api/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
      // A 401 here means "wrong credentials", not an expired session — handle it
      // inline as a form error instead of triggering the global login redirect.
      skipAuthRedirect: true,
    });

    if (res.ok) {
      invalidateAuthStatusCache();
      // Resolve the new account before the caller navigates, so the next page
      // renders signed in instead of flashing the previous (signed-out) state.
      await fetchAuthStatus();
      return { ok: true };
    }

    const data = await res.json().catch(() => ({}));
    return failureFrom(data.detail, "Login failed");
  } catch {
    return { ok: false, error: "Could not reach the server", code: "network" };
  }
}

/**
 * Normalise a FastAPI error detail to a plain string.
 * FastAPI can return detail as a string (HTTPException) or as an array of
 * validation error objects (422 Unprocessable Entity).
 */
function extractDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    if (typeof first === "object" && first !== null && "msg" in first)
      return String((first as { msg: unknown }).msg);
  }
  return "Request failed";
}

export interface SignupPayload {
  full_name: string;
  email: string;
  /** Optional — the server uses the email when empty. */
  username?: string;
  password: string;
  confirm_password: string;
  accept_terms: boolean;
  /** "user" (learner, default) or "educator" — no approval needed. */
  role?: "user" | "educator";
  /** Signed R&D invitation token from the invitation email. */
  invite?: string;
}

export interface SignupResult {
  ok: true;
  username: string;
  email: string;
  role?: string;
  is_first_user?: boolean;
  email_verification?: { required: boolean; delivery: "email" | "log" | "none" };
}

/**
 * Register a new account (detailed sign-up). The first user becomes admin.
 * Field errors come back with ``field`` / ``code`` so the form can mark them.
 */
export async function register(payload: SignupPayload): Promise<SignupResult | AuthFailure> {
  try {
    const res = await apiFetch(apiUrl("/api/auth/register"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      // Registration validation failures (e.g. 400/401) should surface inline
      // rather than bounce the user through the global login redirect.
      skipAuthRedirect: true,
    });

    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      invalidateAuthStatusCache();
      return {
        ok: true,
        username: data.username,
        email: data.email,
        role: data.role,
        is_first_user: data.is_first_user,
        email_verification: data.email_verification,
      };
    }
    return failureFrom(data.detail, "Registration failed");
  } catch {
    return { ok: false, error: "Could not reach the server", code: "network" };
  }
}

async function postPublic(path: string, body: unknown): Promise<{ ok: true } | AuthFailure> {
  try {
    const res = await apiFetch(apiUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      skipAuthRedirect: true,
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) return { ok: true };
    return failureFrom(data.detail, "Request failed");
  } catch {
    return { ok: false, error: "Could not reach the server", code: "network" };
  }
}

/** Confirm a 6-digit email verification code. */
export function verifyEmailCode(email: string, code: string) {
  invalidateAuthStatusCache();
  return postPublic("/api/auth/email/verify", { email, code });
}

/** Ask for a new verification code (always "ok" unless rate-limited). */
export function resendEmailCode(email: string) {
  return postPublic("/api/auth/email/resend", { email });
}

/**
 * Permanently delete the signed-in user's own account (password + "DELETE").
 * On success the session cookie is already cleared by the server.
 */
export async function deleteOwnAccount(
  password: string,
  confirm: string,
): Promise<{ ok: true } | AuthFailure> {
  try {
    const res = await apiFetch(apiUrl("/api/auth/account/delete"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password, confirm }),
      skipAuthRedirect: true,
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      clearAuthSnapshot();
      setTheme(DEFAULT_THEME);
      clearStoredTheme();
      return { ok: true };
    }
    return failureFrom(data.detail, "Request failed");
  } catch {
    return { ok: false, error: "Could not reach the server", code: "network" };
  }
}

/** Update the signed-in user's display name. */
export async function updateProfileDetails(fullName: string): Promise<{ ok: true } | AuthFailure> {
  try {
    const res = await apiFetch(apiUrl("/api/auth/profile/details"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ full_name: fullName }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      invalidateAuthStatusCache();
      return { ok: true };
    }
    return failureFrom(data.detail, "Request failed");
  } catch {
    return { ok: false, error: "Could not reach the server", code: "network" };
  }
}

/**
 * Check whether the user store is empty (first user will become admin).
 */
export async function checkIsFirstUser(): Promise<boolean> {
  try {
    const res = await apiFetch(apiUrl("/api/auth/is_first_user"));
    if (!res.ok) return false;
    const data = await res.json();
    return Boolean(data.is_first_user);
  } catch {
    return false;
  }
}

/**
 * POST to the logout endpoint to clear the session cookie.
 */
export async function logout(): Promise<void> {
  try {
    await apiFetch(apiUrl("/api/auth/logout"), {
      method: "POST",
    });
  } catch {
    // Ignore — we'll redirect regardless
  } finally {
    clearAuthSnapshot();
    // The theme belongs to the account: the sign-in page goes back to the
    // default instead of showing the previous user's choice.
    setTheme(DEFAULT_THEME);
    clearStoredTheme();
  }
}

/** Ask for a password-reset email. Always resolves ok (the server never reveals if the address exists). */
export async function requestPasswordReset(email: string): Promise<boolean> {
  try {
    const res = await apiFetch(apiUrl("/api/auth/password/forgot"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
      skipAuthRedirect: true,
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function resetPassword(token: string, password: string): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    const res = await apiFetch(apiUrl("/api/auth/password/reset"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, password }),
      skipAuthRedirect: true,
    });
    if (res.ok) return { ok: true };
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const d = data.detail;
    const msg =
      typeof d === "string"
        ? d
        : d && typeof d === "object" && "message" in d
          ? String((d as { message: unknown }).message)
          : "Could not reset the password";
    return { ok: false, error: msg };
  } catch {
    return { ok: false, error: "Could not reach the server" };
  }
}
