"use client";

import { createContext, useContext, useMemo } from "react";

import { useAuthSnapshot } from "@/hooks/useAuthStatus";
import {
  PENDING_SETTINGS_ACCESS,
  settingsAccessFromAuthStatus,
  type SettingsAccess,
  type SettingsMode,
} from "@/features/settings/navigation/settings-access";

const SettingsAccessContext = createContext<SettingsAccess>(
  PENDING_SETTINGS_ACCESS,
);

/** Resolve settings access once for the entire persistent settings document. */
export function SettingsAccessProvider({
  children,
  mode = "app",
}: Readonly<{ children: React.ReactNode; mode?: SettingsMode }>) {
  // Shared auth store: settings open with the right pages for this account
  // instead of waiting on a fresh /api/auth/status round trip every visit.
  const status = useAuthSnapshot();
  const value = useMemo<SettingsAccess>(
    () =>
      status === undefined
        ? PENDING_SETTINGS_ACCESS
        : settingsAccessFromAuthStatus(status.unavailable ? null : status, mode),
    [status, mode],
  );
  return (
    <SettingsAccessContext.Provider value={value}>
      {children}
    </SettingsAccessContext.Provider>
  );
}

export function useSettingsAccess(): SettingsAccess {
  return useContext(SettingsAccessContext);
}
