"use client";

import { useEffect, useSyncExternalStore } from "react";
import {
  fetchAuthStatus,
  peekAuthStatus,
  subscribeAuthStatus,
  type AuthStatus,
} from "@/lib/auth";

export interface AuthStatusState {
  /** Whether auth is enabled on the backend. */
  enabled: boolean;
  /** Whether the current session is authenticated. */
  authenticated: boolean;
  /** Whether the authenticated user is an admin. */
  isAdmin: boolean;
  isRnd: boolean;
  isEducator: boolean;
  /** Username of authenticated account */
  username: string | null;
  /** Stable account id for account-scoped browser state. */
  userId: string | null;
  /** False when the runtime status endpoint could not be reached. */
  statusAvailable: boolean;
  /** True until the first status fetch resolves. */
  loading: boolean;
}

const INITIAL: AuthStatusState = {
  enabled: false,
  authenticated: false,
  isAdmin: false,
  isRnd: false,
  isEducator: false,
  username: null,
  userId: null,
  statusAvailable: false,
  loading: true,
};

/**
 * Resolve auth state at runtime from the backend (`/api/auth/status`).
 *
 * The frontend bundle is URL- and auth-agnostic (see web/lib/api.ts): the auth
 * toggle is a runtime setting read from `data/user/settings/auth.json`, never
 * baked into the build. Components that need to know whether auth is on — to
 * show the Sign-out / Admin affordances — use this hook instead of a build-time
 * constant, so it works identically on Docker (read-only rootfs), the PyPI
 * `pathmind start` launcher, and source dev.
 */
const stateCache = new WeakMap<AuthStatus, AuthStatusState>();

function toState(status: AuthStatus | undefined): AuthStatusState {
  if (!status) return INITIAL;
  let state = stateCache.get(status);
  if (!state) {
    state = {
      enabled: Boolean(status.enabled),
      authenticated: Boolean(status.authenticated),
      isAdmin: status.role === "admin",
      isRnd: status.role === "rnd",
      isEducator: status.role === "educator",
      username: status.username ?? null,
      userId:
        typeof status.user_id === "string" && status.user_id.trim()
          ? status.user_id
          : null,
      statusAvailable: !status.unavailable,
      loading: false,
    };
    stateCache.set(status, state);
  }
  return state;
}

const getClientState = () => toState(peekAuthStatus());
const getServerState = () => INITIAL;

/**
 * Renders the last known account immediately (shared store, persisted per
 * browser) and revalidates it in the background, so navigation, login and
 * logout no longer flash a signed-out UI while /api/auth/status is in flight.
 */
export function useAuthStatus(): AuthStatusState {
  const state = useSyncExternalStore(
    subscribeAuthStatus,
    getClientState,
    getServerState,
  );

  useEffect(() => {
    void fetchAuthStatus();
  }, []);

  return state;
}

const getServerStatus = () => undefined;

/** The raw shared auth status (avatar, role…); ``undefined`` until known. */
export function useAuthSnapshot(): AuthStatus | undefined {
  const status = useSyncExternalStore(
    subscribeAuthStatus,
    peekAuthStatus,
    getServerStatus,
  );

  useEffect(() => {
    void fetchAuthStatus();
  }, []);

  return status;
}
