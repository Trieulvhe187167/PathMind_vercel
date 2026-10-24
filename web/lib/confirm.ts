/**
 * App-wide, promise-based confirmation — the replacement for
 * ``window.confirm()``.
 *
 * Native browser dialogs are unreliable: embedded browsers (desktop app
 * panes, some webviews) and pages the user has told to "stop showing
 * dialogs" make ``window.confirm`` return ``false`` immediately, so the
 * guarded action silently never runs (this is what broke "Delete chat").
 *
 * Usage (from any async handler, React or not):
 *
 *     if (!(await confirmAction(t("Delete this entry?"), { tone: "danger" }))) return;
 *
 * A single ``<ConfirmHost />`` (mounted in the root layout) renders the
 * dialog. Without a mounted host the call falls back to ``window.confirm``.
 */

export interface ConfirmOptions {
  /** Dialog title; defaults to the message itself when no body is given. */
  title?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
}

export interface ConfirmRequest extends ConfirmOptions {
  id: number;
  message: string;
  resolve: (ok: boolean) => void;
}

type Host = (request: ConfirmRequest) => void;

let host: Host | null = null;
let counter = 0;

export function registerConfirmHost(next: Host): () => void {
  host = next;
  return () => {
    if (host === next) host = null;
  };
}

export function confirmAction(
  message: string,
  options: ConfirmOptions = {},
): Promise<boolean> {
  if (!host) {
    return Promise.resolve(
      typeof window !== "undefined" ? window.confirm(message) : false,
    );
  }
  const current = host;
  return new Promise<boolean>((resolve) => {
    counter += 1;
    current({ id: counter, message, resolve, ...options });
  });
}
