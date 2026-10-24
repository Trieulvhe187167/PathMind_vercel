"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createElement } from "react";
import {
  ArrowRight,
  BadgeCheck,
  CreditCard,
  ImageUp,
  LogOut,
  MailWarning,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { SettingsPageHeader } from "@/components/settings/shared";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useTranslation } from "react-i18next";
import Modal from "@/components/common/Modal";
import {
  deleteOwnAccount,
  fetchAuthStatus,
  logout,
  refreshAuthStatus,
  resendEmailCode,
  updateProfileDetails,
  verifyEmailCode,
} from "@/lib/auth";
import {
  getProfile,
  removeAvatarImage,
  setAvatarMarker,
  uploadAvatarImage,
  type ProfileInfo,
} from "@/lib/profile-api";
import {
  AVATAR_COLOR_NAMES,
  AVATAR_COLORS,
  AVATAR_ICON_NAMES,
  AVATAR_ICONS,
  fallbackAvatarFor,
  UserAvatar,
} from "@/components/UserAvatar";
import { parseAvatarMarker } from "@/lib/avatar";
import { formatDate, type Language } from "@/lib/datetime";
import {
  fetchUserQuotaSummary,
  formatBillingDate,
  formatNumber,
  type UserUsageSummary,
} from "@/lib/billing-api";

const AVATAR_OUTPUT_SIZE = 256;
// Decoding a huge photo just to throw away most pixels wastes memory; the
// server enforces its own 1 MB cap on the (much smaller) cropped result.
const MAX_SOURCE_BYTES = 20 * 1024 * 1024;

/** Center-crop to a square and downscale; canvas re-encode also strips EXIF. */
async function cropToSquareBlob(file: File): Promise<Blob> {
  let source: CanvasImageSource;
  let width: number;
  let height: number;
  try {
    const bitmap = await createImageBitmap(file, {
      imageOrientation: "from-image",
    });
    source = bitmap;
    width = bitmap.width;
    height = bitmap.height;
  } catch {
    // Older Safari: fall back to decoding via an <img> element.
    const url = URL.createObjectURL(file);
    try {
      const image = await new Promise<HTMLImageElement>((resolve, reject) => {
        const el = new Image();
        el.onload = () => resolve(el);
        el.onerror = () => reject(new Error("Could not decode image"));
        el.src = url;
      });
      source = image;
      width = image.naturalWidth;
      height = image.naturalHeight;
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  if (!width || !height) throw new Error("Could not decode image");

  const side = Math.min(width, height);
  const canvas = document.createElement("canvas");
  canvas.width = AVATAR_OUTPUT_SIZE;
  canvas.height = AVATAR_OUTPUT_SIZE;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Could not decode image");
  ctx.drawImage(
    source,
    (width - side) / 2,
    (height - side) / 2,
    side,
    side,
    0,
    0,
    AVATAR_OUTPUT_SIZE,
    AVATAR_OUTPUT_SIZE,
  );
  // Release the decoder/GPU memory now instead of waiting for GC.
  if (typeof ImageBitmap !== "undefined" && source instanceof ImageBitmap) {
    source.close();
  }

  const toBlob = (type: string, quality?: number) =>
    new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, type, quality),
    );
  // WebP keeps avatars tiny; browsers without a WebP encoder return null.
  const blob =
    (await toBlob("image/webp", 0.85)) ?? (await toBlob("image/png"));
  if (!blob) throw new Error("Could not encode image");
  return blob;
}

export default function ProfileSettingsSection() {
  const router = useRouter();
  const { t, i18n } = useTranslation();
  const [profile, setProfile] = useState<ProfileInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [billing, setBilling] = useState<UserUsageSummary | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [nameSaved, setNameSaved] = useState(false);
  const [canVerify, setCanVerify] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [code, setCode] = useState("");
  const [verifyMessage, setVerifyMessage] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const status = await fetchAuthStatus();
      if (cancelled) return;
      if (!status?.enabled) {
        router.replace("/");
        return;
      }
      if (!status.authenticated) {
        router.replace("/login");
        return;
      }
      setCanVerify(Boolean(status.email_verification_available));
      try {
        const info = await getProfile();
        if (!cancelled) {
          setProfile(info);
          setNameDraft(info.full_name ?? "");
        }
        fetchUserQuotaSummary()
          .then((summary) => {
            if (!cancelled) setBilling(summary);
          })
          .catch(() => undefined);
      } catch {
        if (!cancelled) setError(t("Failed to load profile"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router, t]);

  const applyMarker = useCallback(async (marker: string) => {
    setBusy(true);
    setError(null);
    try {
      const saved = await setAvatarMarker(marker);
      setProfile((prev) => (prev ? { ...prev, avatar: saved } : prev));
      void refreshAuthStatus(); // sidebar account card follows immediately
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, []);

  const handleUpload = useCallback(
    async (file: File) => {
      setBusy(true);
      setError(null);
      try {
        if (file.size > MAX_SOURCE_BYTES) {
          throw new Error(t("Image is too large"));
        }
        const blob = await cropToSquareBlob(file);
        const marker = await uploadAvatarImage(blob);
        setProfile((prev) => (prev ? { ...prev, avatar: marker } : prev));
        void refreshAuthStatus();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [t],
  );

  const handleRemoveImage = useCallback(async () => {
    setConfirmRemove(false);
    setBusy(true);
    setError(null);
    try {
      await removeAvatarImage();
      setProfile((prev) => (prev ? { ...prev, avatar: "" } : prev));
      void refreshAuthStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, []);

  const saveName = useCallback(async () => {
    setBusy(true);
    setError(null);
    setNameSaved(false);
    const result = await updateProfileDetails(nameDraft.trim());
    setBusy(false);
    if (result.ok) {
      setProfile((prev) => (prev ? { ...prev, full_name: nameDraft.trim() } : prev));
      setNameSaved(true);
    } else {
      setError(result.error);
    }
  }, [nameDraft]);

  const sendCode = useCallback(async () => {
    if (!profile?.email) return;
    setVerifyMessage(null);
    const result = await resendEmailCode(profile.email);
    if (result.ok) {
      setCodeSent(true);
      setVerifyMessage(t("We sent a 6-digit code to {{email}}.", { email: profile.email }));
    } else if (result.code === "code_resend_wait") {
      setCodeSent(true);
      setVerifyMessage(result.error);
    } else {
      setVerifyMessage(result.error);
    }
  }, [profile?.email, t]);

  const submitCode = useCallback(async () => {
    if (!profile?.email) return;
    setBusy(true);
    const result = await verifyEmailCode(profile.email, code.trim());
    setBusy(false);
    if (result.ok) {
      setProfile((prev) => (prev ? { ...prev, email_verified: true } : prev));
      setVerifyMessage(null);
      setCodeSent(false);
    } else {
      setVerifyMessage(result.error);
    }
  }, [code, profile?.email]);

  const handleDeleteAccount = useCallback(async () => {
    setDeleting(true);
    setDeleteError(null);
    const result = await deleteOwnAccount(deletePassword, deleteConfirm);
    if (result.ok) {
      window.location.href = "/login?deleted=1";
      return;
    }
    setDeleting(false);
    setDeleteError(result.error);
  }, [deletePassword, deleteConfirm]);

  const handleSignOut = useCallback(async () => {
    await logout();
    router.replace("/login");
  }, [router]);

  const descriptor = parseAvatarMarker(profile?.avatar);
  const hasImage = descriptor.kind === "image";
  const fallback = fallbackAvatarFor(profile?.username ?? "");
  const selectedIcon =
    descriptor.kind === "icon"
      ? descriptor.icon
      : hasImage
        ? null
        : fallback.icon;
  const selectedColor =
    descriptor.kind === "icon"
      ? descriptor.color
      : hasImage
        ? null
        : fallback.color;
  const isAdmin = profile?.role === "admin";
  const lang: Language = i18n.language?.startsWith("zh") ? "zh" : "en";
  const joinedDate = profile?.created_at ? new Date(profile.created_at) : null;
  const joined =
    joinedDate && !Number.isNaN(joinedDate.getTime())
      ? formatDate(joinedDate, lang)
      : null;

  return (
    <div className="max-w-2xl">
        <SettingsPageHeader
          title={t("My profile")}
          description={t("View your account and personalize your avatar")}
        />

        {error && (
          <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center rounded-2xl border border-[var(--border)] bg-[var(--card)] py-16 text-sm text-[var(--muted-foreground)] shadow-sm">
            {t("Loading…")}
          </div>
        ) : !profile ? null : (
          <>
            {/* Account card */}
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6 shadow-sm">
              <div className="flex items-center gap-5">
                <UserAvatar
                  username={profile.username}
                  userId={profile.id}
                  avatar={profile.avatar}
                  role={profile.role}
                  size={72}
                />
                <div className="min-w-0">
                  <div className="flex items-center gap-2.5">
                    <span className="truncate text-lg font-semibold text-[var(--foreground)]">
                      {profile.full_name || profile.username}
                    </span>
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                        isAdmin
                          ? "bg-warning-surface text-warning"
                          : "bg-muted/70 text-[var(--muted-foreground)]"
                      }`}
                    >
                      {isAdmin && <ShieldCheck size={11} strokeWidth={2} />}
                      {isAdmin ? t("Administrator") : t("User")}
                    </span>
                  </div>
                  {profile.full_name && (
                    <p className="mt-0.5 truncate text-sm text-[var(--muted-foreground)]">
                      @{profile.username}
                    </p>
                  )}
                  {joined && (
                    <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                      {t("Joined")}: {joined}
                    </p>
                  )}
                </div>
              </div>
            </div>

            {/* Account details */}
            <div className="mt-4 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6 shadow-sm">
              <h2 className="text-sm font-semibold text-[var(--foreground)]">
                {t("Account details")}
              </h2>
              <div className="mt-4 space-y-4">
                <div>
                  <label
                    htmlFor="profile-full-name"
                    className="mb-1.5 block text-xs font-medium text-[var(--muted-foreground)]"
                  >
                    {t("Full name")}
                  </label>
                  <div className="flex gap-2">
                    <input
                      id="profile-full-name"
                      value={nameDraft}
                      maxLength={120}
                      onChange={(event) => {
                        setNameDraft(event.target.value);
                        setNameSaved(false);
                      }}
                      placeholder={t("Your full name")}
                      className="min-w-0 flex-1 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
                    />
                    <button
                      onClick={() => void saveName()}
                      disabled={busy || nameDraft.trim() === (profile.full_name ?? "")}
                      className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--foreground)] hover:bg-background/60 disabled:opacity-50 transition-colors"
                    >
                      {nameSaved ? t("Saved") : t("Save")}
                    </button>
                  </div>
                </div>
                <div>
                  <p className="mb-1.5 text-xs font-medium text-[var(--muted-foreground)]">
                    {t("Email")}
                  </p>
                  {profile.email ? (
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="text-[var(--foreground)]">{profile.email}</span>
                      {profile.email_verified ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-400">
                          <BadgeCheck size={12} />
                          {t("Verified")}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400">
                          <MailWarning size={12} />
                          {t("Not verified")}
                        </span>
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-[var(--muted-foreground)]">
                      {t("No email on this account")}
                    </p>
                  )}
                  {profile.email && !profile.email_verified && canVerify && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {codeSent && (
                        <>
                          <input
                            inputMode="numeric"
                            autoComplete="one-time-code"
                            maxLength={6}
                            value={code}
                            onChange={(event) =>
                              setCode(event.target.value.replace(/\D/g, "").slice(0, 6))
                            }
                            placeholder="123456"
                            aria-label={t("Verification code")}
                            className="w-28 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-center font-mono text-sm tracking-widest focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
                          />
                          <button
                            onClick={() => void submitCode()}
                            disabled={busy || code.length !== 6}
                            className="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
                          >
                            {t("Verify email")}
                          </button>
                        </>
                      )}
                      <button
                        onClick={() => void sendCode()}
                        className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--foreground)] hover:bg-background/60 transition-colors"
                      >
                        {codeSent ? t("Resend code") : t("Send verification code")}
                      </button>
                    </div>
                  )}
                  {verifyMessage && (
                    <p className="mt-2 text-xs text-[var(--muted-foreground)]">{verifyMessage}</p>
                  )}
                </div>
              </div>
            </div>

            {/* Plan summary — details live in Settings → Plans & billing */}
            <div className="mt-4 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6 shadow-sm">
              <div className="flex items-center gap-3.5">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/15 text-primary">
                  <CreditCard size={20} />
                </div>
                <div>
                  <h2 className="text-sm font-semibold text-[var(--foreground)]">
                    {t("Plans & billing")}
                  </h2>
                  <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                    {billing
                      ? billing.is_unlimited
                        ? t("Administrator — unlimited")
                        : [
                            billing.plan.name,
                            t("{{used}}/{{limit}} credits today", {
                              used: formatNumber(billing.usage_today.credits),
                              limit: formatNumber(billing.usage_today.limit),
                            }),
                            billing.subscription?.current_period_end
                              ? billing.subscription.cancel_at_period_end
                                ? t("Ends on {{date}}", {
                                    date: formatBillingDate(billing.subscription.current_period_end),
                                  })
                                : t("Renews on {{date}}", {
                                    date: formatBillingDate(billing.subscription.current_period_end),
                                  })
                              : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")
                      : t("Credits, document storage and the AI models your plan includes")}
                  </p>
                </div>
              </div>
              <Link
                href="/settings/billing"
                className="inline-flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground shadow-sm hover:opacity-90 transition-colors"
              >
                <span>{t("Manage plan")}</span>
                <ArrowRight size={14} />
              </Link>
            </div>

            {/* Avatar card */}
            <div className="mt-4 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6 shadow-sm">
              <h2 className="text-sm font-semibold text-[var(--foreground)]">
                {t("Avatar")}
              </h2>
              <p className="mt-0.5 text-sm text-[var(--muted-foreground)]">
                {t("Upload a picture or pick an icon")}
              </p>

              <div className="mt-4 flex flex-wrap items-center gap-2.5">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void handleUpload(file);
                  }}
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={busy}
                  className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm
                             border border-[var(--border)] text-[var(--foreground)]
                             hover:bg-background/60 disabled:opacity-50 transition-colors"
                >
                  <ImageUp size={14} />
                  {t("Upload image")}
                </button>
                {hasImage && (
                  <button
                    onClick={() => setConfirmRemove(true)}
                    disabled={busy}
                    className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm
                               border border-[var(--border)] text-[var(--muted-foreground)]
                               hover:text-destructive disabled:opacity-50 transition-colors"
                  >
                    <Trash2 size={14} />
                    {t("Remove photo")}
                  </button>
                )}
              </div>

              {/* Icon grid */}
              <div className="mt-5">
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-[var(--muted-foreground)]">
                  {t("Or pick an icon")}
                </p>
                <div className="flex flex-wrap gap-2">
                  {AVATAR_ICON_NAMES.map((name) => {
                    const active = !hasImage && name === selectedIcon;
                    const color = selectedColor ?? fallback.color;
                    return (
                      <button
                        key={name}
                        onClick={() =>
                          void applyMarker(`icon:${name}:${color}`)
                        }
                        disabled={busy}
                        aria-label={name}
                        aria-pressed={active}
                        className={`flex h-10 w-10 items-center justify-center rounded-full text-white transition-all disabled:opacity-50 ${
                          active
                            ? "ring-2 ring-[var(--foreground)] ring-offset-2 ring-offset-[var(--card)]"
                            : "opacity-75 hover:opacity-100"
                        }`}
                        style={{ backgroundColor: AVATAR_COLORS[color] }}
                      >
                        {createElement(AVATAR_ICONS[name], {
                          size: 18,
                          strokeWidth: 1.8,
                        })}
                      </button>
                    );
                  })}
                </div>
                <p className="mb-2 mt-4 text-xs font-medium uppercase tracking-wider text-[var(--muted-foreground)]">
                  {t("Color")}
                </p>
                <div className="flex flex-wrap gap-2">
                  {AVATAR_COLOR_NAMES.map((name) => {
                    const active = !hasImage && name === selectedColor;
                    const icon = selectedIcon ?? fallback.icon;
                    return (
                      <button
                        key={name}
                        onClick={() => void applyMarker(`icon:${icon}:${name}`)}
                        disabled={busy}
                        aria-label={name}
                        aria-pressed={active}
                        className={`h-7 w-7 rounded-full transition-all disabled:opacity-50 ${
                          active
                            ? "ring-2 ring-[var(--foreground)] ring-offset-2 ring-offset-[var(--card)]"
                            : "opacity-75 hover:opacity-100"
                        }`}
                        style={{ backgroundColor: AVATAR_COLORS[name] }}
                      />
                    );
                  })}
                </div>
              </div>
            </div>

            {/* Sign out card */}
            <div className="mt-4 flex items-center justify-between rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6 shadow-sm">
              <div>
                <h2 className="text-sm font-semibold text-[var(--foreground)]">
                  {t("Sign out")}
                </h2>
                <p className="mt-0.5 text-sm text-[var(--muted-foreground)]">
                  {t("End your session on this device")}
                </p>
              </div>
              <button
                onClick={() => void handleSignOut()}
                className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm
                           border border-destructive/40 text-destructive
                           hover:bg-destructive/10 transition-colors"
              >
                <LogOut size={14} />
                {t("Sign out")}
              </button>
            </div>

            {/* Danger zone — permanent account deletion */}
            <div className="mt-4 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-destructive/30 bg-[var(--card)] p-6 shadow-sm">
              <div className="max-w-md">
                <h2 className="text-sm font-semibold text-destructive">{t("Delete account")}</h2>
                <p className="mt-0.5 text-sm text-[var(--muted-foreground)]">
                  {t("Permanently delete your account and everything in it. This cannot be undone.")}
                </p>
              </div>
              <button
                onClick={() => {
                  setDeletePassword("");
                  setDeleteConfirm("");
                  setDeleteError(null);
                  setDeleteOpen(true);
                }}
                className="flex items-center gap-1.5 rounded-lg bg-destructive px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 transition-opacity"
              >
                <Trash2 size={14} />
                {t("Delete account")}
              </button>
            </div>
          </>
        )}
      <Modal
        isOpen={deleteOpen}
        onClose={() => (deleting ? undefined : setDeleteOpen(false))}
        title={t("Delete your account?")}
        width="md"
        footer={
          <div className="flex justify-end gap-2">
            <button
              type="button"
              disabled={deleting}
              onClick={() => setDeleteOpen(false)}
              className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--muted)] disabled:opacity-50"
            >
              {t("Cancel")}
            </button>
            <button
              type="button"
              disabled={deleting || !deletePassword || deleteConfirm.trim().toUpperCase() !== "DELETE"}
              onClick={() => void handleDeleteAccount()}
              className="rounded-lg bg-destructive px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {deleting ? t("Deleting…") : t("Delete permanently")}
            </button>
          </div>
        }
      >
        <div className="space-y-4 px-6 py-5 text-sm">
          <div>
            <p className="font-medium">{t("This will permanently delete:")}</p>
            <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-[var(--muted-foreground)]">
              <li>{t("Your profile, sign-in details and avatar")}</li>
              <li>{t("All chats, notes, notebooks, books, knowledge bases and uploaded files")}</li>
              <li>{t("Your partners, memory, friends and study-room memberships")}</li>
            </ul>
            <p className="mt-2 text-[var(--muted-foreground)]">
              {t("An active paid plan ends immediately and is not refunded. Payment records are kept without your name, as required for accounting.")}
            </p>
          </div>
          <div>
            <label htmlFor="delete-password" className="mb-1.5 block text-xs font-medium">
              {t("Password")}
            </label>
            <input
              id="delete-password"
              type="password"
              autoComplete="current-password"
              value={deletePassword}
              onChange={(event) => setDeletePassword(event.target.value)}
              className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
            />
          </div>
          <div>
            <label htmlFor="delete-confirm" className="mb-1.5 block text-xs font-medium">
              {t("Type DELETE to confirm")}
            </label>
            <input
              id="delete-confirm"
              value={deleteConfirm}
              onChange={(event) => setDeleteConfirm(event.target.value)}
              placeholder="DELETE"
              className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
            />
          </div>
          {deleteError && <p className="text-xs text-destructive">{deleteError}</p>}
        </div>
      </Modal>
      <ConfirmDialog
        open={confirmRemove}
        title={t("Remove your profile photo?")}
        tone="danger"
        confirmLabel={t("Remove photo")}
        busy={busy}
        onConfirm={() => void handleRemoveImage()}
        onCancel={() => setConfirmRemove(false)}
      >
        {t("Your avatar goes back to the default icon.")}
      </ConfirmDialog>
    </div>
  );
}
