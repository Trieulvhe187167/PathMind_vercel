"use client";

import { useEffect } from "react";
import { apiFetch, apiUrl } from "@/lib/api";
import { getStoredTheme, setTheme, type Theme } from "@/lib/theme";

const THEMES: ReadonlySet<string> = new Set(["light", "dark", "glass", "snow"]);

/**
 * Makes the theme follow the signed-in account.
 *
 * The theme is saved per account on the server (the user's own
 * ``settings/interface.json``, changed in Settings → Appearance). The browser
 * keeps a copy in localStorage only so ``ThemeScript`` can paint the right
 * colours before React loads. On every app load we ask the server which theme
 * applies — the account's own when signed in, the deployment default
 * (PATHMIND_DEFAULT_THEME) otherwise — and apply it if this browser's copy
 * differs (another account used this browser, the theme was changed on
 * another device, or nobody is signed in).
 */
export default function AccountThemeSync() {
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await apiFetch(apiUrl("/api/settings/ui"), { skipAuthRedirect: true });
        if (!res.ok || cancelled) return;
        const payload = (await res.json()) as { theme?: unknown };
        const theme = typeof payload.theme === "string" ? payload.theme : "";
        if (THEMES.has(theme) && theme !== getStoredTheme()) setTheme(theme as Theme);
      } catch {
        // Offline: keep the cached theme; the next load retries.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return null;
}
