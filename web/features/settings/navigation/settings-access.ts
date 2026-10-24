import type { AuthStatus } from "@/lib/auth";

export interface SettingsAccess {
  /** False until the backend has resolved the runtime auth mode and account. */
  resolved: boolean;
  /** Admin-owned settings stay hidden on auth failures and for ordinary users. */
  hideAdminOnly: boolean;
  /** Account pages (profile, billing) need a signed-in user with auth on. */
  authEnabled: boolean;
  /** The account may configure the system (it does so in the admin console). */
  isAdmin: boolean;
}

/**
 * Where settings are shown: the app's own Settings keeps personal pages only
 * (system pages moved to the admin console at /admin/config/<page>); the
 * console shows the system pages to administrators.
 */
export type SettingsMode = "app" | "console";

export const PENDING_SETTINGS_ACCESS: SettingsAccess = {
  resolved: false,
  hideAdminOnly: true,
  authEnabled: false,
  isAdmin: false,
};

/** Convert the backend's account identity into the settings visibility model. */
export function settingsAccessFromAuthStatus(
  authStatus: AuthStatus | null,
  mode: SettingsMode = "app",
): SettingsAccess {
  if (!authStatus) {
    return { ...PENDING_SETTINGS_ACCESS, resolved: true };
  }
  const isAdmin = !authStatus.enabled || Boolean(authStatus.is_admin);
  return {
    resolved: true,
    // System pages only appear in the admin console now, never in the app's Settings.
    hideAdminOnly: mode === "app" || !isAdmin,
    authEnabled: Boolean(authStatus.enabled && authStatus.authenticated),
    isAdmin,
  };
}
