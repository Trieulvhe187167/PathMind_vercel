"use client";

import { Fragment, useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { fetchAuthStatus } from "@/lib/auth";
import {
  listUsers,
  setUserDisabled,
  setUserRole,
  verifyUserEmail,
  createUser,
  getAdminStats,
  listAdminPlans,
  listUserSubscriptions,
  updateUserSubscriptionPlan,
  cancelUserSubscription,
  type UserRecord,
  type UserRole,
  type AccountPreset,
  type AdminStats,
  type AdminSubscriptionPlan,
  type AdminUserSubscription,
} from "@/lib/admin-api";
import InviteRndCard from "@/components/admin/InviteRndCard";
import { GrantEditor } from "@/features/multi-user/components/GrantEditor";
import { UserAvatar } from "@/components/UserAvatar";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { filterUsersByQuery } from "@/lib/admin-users";
import {
  Search,
  Shield,
  ShieldCheck,
  ShieldOff,
  Lock,
  LockOpen,
  RefreshCw,
  SlidersHorizontal,
  UserPlus,
  Users,
  X,
  CreditCard,
  HardDrive,
  Cpu,
} from "lucide-react";
import { formatDate as formatLocaleDate, type Language } from "@/lib/datetime";

// Delegates to the shared locale mapping so a new UI language only has to be
// taught to lib/datetime; the guard here is for the empty or unparseable
// created_at that Intl would throw on.
function formatDate(iso: string, lang: Language): string {
  if (!iso) return "—";
  try {
    return formatLocaleDate(new Date(iso), lang);
  } catch {
    return "—";
  }
}

/** Translation key naming an account's role (``user`` is a learner). */
function roleLabel(role: string): string {
  if (role === "admin") return "Admin";
  if (role === "rnd") return "R&D";
  if (role === "educator") return "Educator";
  return "Learner";
}

export default function AdminUsersPage() {
  const router = useRouter();
  const { t, i18n } = useTranslation();
  const lang: Language = i18n.language?.startsWith("zh") ? "zh" : "en";
  const [currentUser, setCurrentUser] = useState<string | null>(null);
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [expandedUserId, setExpandedUserId] = useState<string | null>(null);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [query, setQuery] = useState("");
  const [confirmTarget, setConfirmTarget] = useState<{
    kind: "lock" | "unlock" | "promote" | "demote";
    user: UserRecord;
  } | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [createUsername, setCreateUsername] = useState("");
  const [createPassword, setCreatePassword] = useState("");
  const [createPreset, setCreatePreset] = useState<AccountPreset>("standard");
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createError, setCreateError] = useState("");

  const [stats, setStats] = useState<AdminStats | null>(null);
  const [plans, setPlans] = useState<AdminSubscriptionPlan[]>([]);
  const [subs, setSubs] = useState<Record<string, AdminUserSubscription>>({});
  const [planTarget, setPlanTarget] = useState<{
    user: UserRecord;
    planId: string;
    durationDays: string;
  } | null>(null);
  const [planBusy, setPlanBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [data, statsData, planList, subList] = await Promise.all([
        listUsers(),
        getAdminStats().catch(() => null),
        listAdminPlans().catch(() => [] as AdminSubscriptionPlan[]),
        listUserSubscriptions().catch(() => [] as AdminUserSubscription[]),
      ]);
      setUsers(data);
      setStats(statsData);
      setPlans(planList);
      setSubs(Object.fromEntries(subList.map((s) => [s.id, s])));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Failed to load users"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  async function handleAssignPlan() {
    if (!planTarget || planBusy) return;
    setPlanBusy(true);
    setActionError("");
    try {
      const target = plans.find((p) => p.id === planTarget.planId);
      const isFreePlan = !target || (target.price_monthly <= 0 && target.price_yearly <= 0);
      const days = Math.max(0, Number(planTarget.durationDays) || 0);
      await updateUserSubscriptionPlan(planTarget.user.id, planTarget.planId, isFreePlan ? 0 : days);
      setPlanTarget(null);
      await load();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Failed to update plan");
    } finally {
      setPlanBusy(false);
    }
  }

  async function handleCancelPaid(user: UserRecord) {
    setActionError("");
    try {
      await cancelUserSubscription(user.id);
      await load();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Failed to cancel subscription");
    }
  }

  useEffect(() => {
    fetchAuthStatus().then((status) => {
      if (!status?.authenticated) {
        router.replace("/login");
        return;
      }
      if (status.role !== "admin") {
        router.replace("/");
        return;
      }
      setCurrentUser(status.username ?? null);
      void load();
    });
  }, [router, load]);

  function openCreateDialog() {
    setCreateUsername("");
    setCreatePassword("");
    setCreatePreset("standard");
    setCreateError("");
    setShowCreateDialog(true);
  }

  function closeCreateDialog() {
    if (createSubmitting) return;
    setShowCreateDialog(false);
  }

  async function handleCreateSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (createSubmitting) return;
    setCreateError("");
    const username = createUsername.trim();
    if (!username) {
      setCreateError(t("Username is required."));
      return;
    }
    if (createPassword.length < 8) {
      setCreateError(t("Password must be at least 8 characters."));
      return;
    }
    setCreateSubmitting(true);
    try {
      await createUser(username, createPassword, createPreset);
      setShowCreateDialog(false);
      await load();
    } catch (e) {
      setCreateError(
        e instanceof Error ? e.message : t("Failed to create user"),
      );
    } finally {
      setCreateSubmitting(false);
    }
  }

  async function handleConfirmAction() {
    if (!confirmTarget || confirmBusy) return;
    const { kind, user } = confirmTarget;
    setConfirmBusy(true);
    setActionError("");
    try {
      if (kind === "lock" || kind === "unlock") {
        const disabled = kind === "lock";
        await setUserDisabled(user.username, disabled);
        setUsers((prev) =>
          prev.map((u) => (u.username === user.username ? { ...u, disabled } : u)),
        );
      } else {
        const newRole = kind === "promote" ? "admin" : "user";
        await setUserRole(user.username, newRole);
        setUsers((prev) =>
          prev.map((u) =>
            u.username === user.username ? { ...u, role: newRole } : u,
          ),
        );
        if (newRole === "admin") {
          setExpandedUserId((current) =>
            current === user.id ? null : current,
          );
        }
      }
      setConfirmTarget(null);
    } catch (e) {
      setConfirmTarget(null);
      setActionError(
        e instanceof Error
          ? e.message
          : confirmTarget.kind === "lock" || confirmTarget.kind === "unlock"
            ? t("Failed to update account status")
            : t("Failed to update role"),
      );
    } finally {
      setConfirmBusy(false);
    }
  }

  useEffect(() => {
    if (!expandedUserId) return;
    const expanded = users.find((user) => user.id === expandedUserId);
    if (!expanded || expanded.role === "admin") {
      setExpandedUserId(null);
    }
  }, [expandedUserId, users]);

  const normalizedQuery = query.trim().toLowerCase();
  const filteredUsers = filterUsersByQuery(users, query);

  return (
    <div className="h-full overflow-y-auto bg-[var(--background)] px-4 py-10 [scrollbar-gutter:stable]">
      <div className="mx-auto max-w-5xl">
        {/* Header */}
        <div className="mb-8">

          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="font-serif text-xl font-semibold text-[var(--foreground)]">
                {t("User Management")}
              </h1>
              <p className="mt-0.5 text-sm text-[var(--muted-foreground)]">
                {t("Manage registered accounts")}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                onClick={openCreateDialog}
                className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm
                           border border-[var(--border)] text-[var(--foreground)]
                           hover:bg-[var(--card)] transition-colors"
              >
                <UserPlus size={14} />
                {t("Add user")}
              </button>
              <button
                onClick={load}
                disabled={loading}
                className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm
                           border border-[var(--border)] text-[var(--muted-foreground)]
                           hover:text-[var(--foreground)] hover:bg-[var(--card)]
                           disabled:opacity-50 transition-colors"
              >
                <RefreshCw
                  size={14}
                  className={loading ? "animate-spin" : ""}
                />
                {t("Refresh")}
              </button>
            </div>
          </div>
        </div>

        <InviteRndCard />

        {actionError && (
          <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {actionError}
          </div>
        )}

        {stats && (
          <div className="mb-6 grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="p-4 rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-sm">
              <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)] mb-1">
                <span>{t("Total users")}</span>
                <Users size={16} className="text-primary" />
              </div>
              <div className="text-2xl font-bold text-[var(--foreground)]">
                {stats.total_users.toLocaleString()}
              </div>
            </div>
            <div className="p-4 rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-sm">
              <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)] mb-1">
                <span>{t("Paid users")}</span>
                <CreditCard size={16} className="text-success" />
              </div>
              <div className="text-2xl font-bold text-[var(--foreground)]">
                {stats.active_subscriptions.toLocaleString()}
              </div>
            </div>
            <div className="p-4 rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-sm">
              <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)] mb-1">
                <span>{t("API cost this month")}</span>
                <Cpu size={16} className="text-muted-foreground" />
              </div>
              <div className="text-2xl font-bold text-[var(--foreground)] font-mono">
                ${(stats.api_cost_month_usd ?? 0).toFixed(2)}
              </div>
            </div>
            <div className="p-4 rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-sm">
              <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)] mb-1">
                <span>{t("Revenue this month")}</span>
                <HardDrive size={16} className="text-muted-foreground" />
              </div>
              <div className="text-2xl font-bold text-[var(--foreground)] font-mono">
                ${(stats.revenue_this_month_usd ?? 0).toFixed(2)}
              </div>
            </div>
          </div>
        )}

        {!loading && !error && users.length > 0 && (
          <div className="mb-4 flex items-center gap-3">
            <div className="relative flex-1">
              <Search
                size={14}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted-foreground)]"
              />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("Search users…")}
                aria-label={t("Search users")}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--card)] py-2 pl-9 pr-3 text-sm
                           text-[var(--foreground)] placeholder:text-muted-foreground/70
                           outline-none focus:border-[var(--ring)] transition-colors"
              />
            </div>
            <span className="shrink-0 text-xs text-[var(--muted-foreground)]">
              {normalizedQuery
                ? t("{{filtered}} of {{total}}", {
                    filtered: filteredUsers.length,
                    total: users.length,
                  })
                : t(users.length === 1 ? "{{count}} user" : "{{count}} users", {
                    count: users.length,
                  })}
            </span>
          </div>
        )}

        <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] overflow-hidden shadow-sm">
          {loading ? (
            <div className="divide-y divide-[var(--border)]" aria-hidden>
              {[0, 1, 2].map((row) => (
                <div
                  key={row}
                  className="flex animate-pulse items-center gap-3 px-5 py-4"
                >
                  <div className="h-8 w-8 rounded-full bg-muted/60" />
                  <div className="flex-1 space-y-2">
                    <div className="h-3 w-36 rounded bg-muted/60" />
                    <div className="h-2.5 w-24 rounded bg-muted/40" />
                  </div>
                  <div className="h-5 w-16 rounded-full bg-muted/40" />
                </div>
              ))}
            </div>
          ) : error ? (
            <div className="flex items-center justify-center py-16 text-destructive text-sm">
              {error}
            </div>
          ) : users.length === 0 ? (
            <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
              <Users
                size={28}
                strokeWidth={1.5}
                className="text-muted-foreground/50"
              />
              <p className="mt-3 text-sm font-medium text-[var(--foreground)]">
                {t("No users yet")}
              </p>
              <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                {t("Accounts you create will appear here.")}
              </p>
              <button
                onClick={openCreateDialog}
                className="mt-4 flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm
                           border border-[var(--border)] text-[var(--foreground)]
                           hover:bg-background/60 transition-colors"
              >
                <UserPlus size={14} />
                {t("Add user")}
              </button>
            </div>
          ) : filteredUsers.length === 0 ? (
            <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
              <Search
                size={28}
                strokeWidth={1.5}
                className="text-muted-foreground/50"
              />
              <p className="mt-3 text-sm font-medium text-[var(--foreground)]">
                {t("No users match “{{query}}”", { query: query.trim() })}
              </p>
              <button
                onClick={() => setQuery("")}
                className="mt-4 rounded-lg px-3 py-1.5 text-sm border border-[var(--border)]
                           text-[var(--muted-foreground)] hover:text-[var(--foreground)]
                           hover:bg-background/60 transition-colors"
              >
                {t("Clear search")}
              </button>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--muted-foreground)] uppercase tracking-wider">
                  <th className="px-5 py-3 font-medium">{t("Username")}</th>
                  <th className="px-5 py-3 font-medium">{t("Role")}</th>
                  <th className="px-5 py-3 font-medium">{t("Plan", { context: "billing" })}</th>
                  <th className="px-5 py-3 font-medium">{t("Joined")}</th>
                  <th className="px-5 py-3 font-medium text-right">
                    {t("Actions")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)]">
                {filteredUsers.map((user) => {
                  const isSelf = user.username === currentUser;
                  const isAdmin = user.role === "admin";
                  const canManageAssignments = !isAdmin && Boolean(user.id);
                  const userSub = subs[user.id];
                  return (
                    <Fragment key={user.username}>
                      <tr className="group hover:bg-background/50 transition-colors">
                        <td className="px-5 py-3">
                          <div className="flex items-center gap-3">
                            <UserAvatar
                              username={user.username}
                              userId={user.id}
                              avatar={user.avatar}
                              role={user.role}
                              size={32}
                            />
                            <div className="min-w-0">
                              <span className="block truncate font-medium text-[var(--foreground)]">
                                {user.full_name || user.username}
                                {isSelf && (
                                  <span className="ml-2 text-xs font-normal text-[var(--muted-foreground)]">
                                    {t("(you)")}
                                  </span>
                                )}
                                {user.disabled && (
                                  <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400">
                                    <Lock size={10} />
                                    {t("Locked")}
                                  </span>
                                )}
                              </span>
                              {(user.full_name || user.email) && (
                                <span className="flex flex-wrap items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
                                  {user.full_name && <span>@{user.username}</span>}
                                  {user.email && user.email !== user.username && (
                                    <span className="truncate">{user.email}</span>
                                  )}
                                  {user.email &&
                                    (user.email_verified ? (
                                      <span className="text-emerald-600 dark:text-emerald-400">
                                        {t("Verified")}
                                      </span>
                                    ) : (
                                      <button
                                        type="button"
                                        title={t("Mark email as verified")}
                                        onClick={async () => {
                                          try {
                                            await verifyUserEmail(user.username);
                                            setUsers((prev) =>
                                              prev.map((u) =>
                                                u.username === user.username
                                                  ? { ...u, email_verified: true }
                                                  : u,
                                              ),
                                            );
                                          } catch {
                                            /* surfaced by the row staying unverified */
                                          }
                                        }}
                                        className="text-amber-600 hover:underline dark:text-amber-400"
                                      >
                                        {t("Not verified")} · {t("Mark verified")}
                                      </button>
                                    ))}
                                </span>
                              )}
                            </div>
                          </div>
                        </td>
                        <td className="px-5 py-3">
                          <span
                            className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium
                            ${
                              isAdmin
                                ? "bg-warning-surface text-warning"
                                : "bg-muted/50 text-[var(--muted-foreground)]"
                            }`}
                          >
                            {isAdmin && (
                              <ShieldCheck size={11} strokeWidth={2} />
                            )}
                            {t(roleLabel(user.role))}
                          </span>
                          {!isAdmin && user.preset && (
                            <span className="mt-1 block text-[11px] text-[var(--muted-foreground)]">
                              {t("Preset: {{preset}}", {
                                preset: t(
                                  user.preset === "learner"
                                    ? "Learner"
                                    : user.preset === "custom"
                                      ? "Custom"
                                      : "Standard",
                                ),
                              })}
                            </span>
                          )}
                        </td>
                        <td className="px-5 py-3">
                          {isAdmin ? (
                            <span className="text-xs text-[var(--muted-foreground)]">{t("Unlimited")}</span>
                          ) : (
                            <div className="space-y-1">
                              <button
                                onClick={() =>
                                  setPlanTarget({
                                    user,
                                    planId: userSub?.plan_id ?? "free",
                                    durationDays: "30",
                                  })
                                }
                                title={t("Change / assign plan")}
                                className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-xs font-semibold text-[var(--foreground)] hover:border-primary"
                              >
                                {userSub?.plan ?? "Free Starter"}
                              </button>
                              {userSub?.subscription?.current_period_end && (
                                <div className="text-[10px] text-[var(--muted-foreground)]">
                                  {t(userSub.subscription.cancel_at_period_end ? "Ends {{date}}" : "Expires {{date}}", {
                                    date: formatDate(userSub.subscription.current_period_end, lang),
                                  })}
                                </div>
                              )}
                              {userSub && (
                                <div className="text-[10px] text-[var(--muted-foreground)]">
                                  {t("{{used}}/{{limit}} credits today · API ${{cost}}/month", {
                                    used: userSub.tokens_today.toLocaleString(),
                                    limit: userSub.limit_day.toLocaleString(),
                                    cost: (userSub.cost_month_usd ?? 0).toFixed(2),
                                  })}
                                </div>
                              )}
                              {userSub && userSub.plan_id !== "free" && (
                                <button
                                  onClick={() => void handleCancelPaid(user)}
                                  className="text-[10px] text-destructive hover:underline"
                                >
                                  {t("Cancel plan now")}
                                </button>
                              )}
                            </div>
                          )}
                        </td>
                        <td className="px-5 py-3.5 text-[var(--muted-foreground)]">
                          {formatDate(user.created_at, lang)}
                        </td>
                        <td className="px-5 py-3.5">
                          <div className="flex items-center justify-end gap-1.5">
                            {canManageAssignments && (
                              <button
                                onClick={() =>
                                  setExpandedUserId((current) =>
                                    current === user.id ? null : user.id,
                                  )
                                }
                                title={t("Manage assignments")}
                                className="rounded-lg p-1.5 text-[var(--muted-foreground)]
                                         hover:bg-[var(--background)] hover:text-[var(--foreground)]
                                         transition-colors"
                              >
                                <SlidersHorizontal size={15} />
                              </button>
                            )}
                            {!isAdmin && (
                              <select
                                aria-label={t("Role")}
                                value={user.role}
                                disabled={isSelf}
                                onChange={async (e) => {
                                  const newRole = e.target.value as UserRole;
                                  try {
                                    await setUserRole(user.username, newRole);
                                    setUsers((prev) =>
                                      prev.map((u) =>
                                        u.username === user.username
                                          ? { ...u, role: newRole }
                                          : u,
                                      ),
                                    );
                                  } catch {
                                    setActionError(t("Failed to update role"));
                                  }
                                }}
                                className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-1.5 py-1 text-xs"
                              >
                                <option value="user">{t("Learner")}</option>
                                <option value="educator">{t("Educator")}</option>
                                <option value="rnd">{t("R&D")}</option>
                              </select>
                            )}
                            <button
                              onClick={() =>
                                setConfirmTarget({
                                  kind: isAdmin ? "demote" : "promote",
                                  user,
                                })
                              }
                              disabled={isSelf}
                              title={
                                isSelf
                                  ? t("Cannot change your own role")
                                  : user.role === "admin"
                                    ? t("Demote to user")
                                    : t("Promote to admin")
                              }
                              className="rounded-lg p-1.5 text-[var(--muted-foreground)]
                                       hover:bg-[var(--background)] hover:text-[var(--foreground)]
                                       disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                            >
                              {user.role === "admin" ? (
                                <ShieldOff size={15} />
                              ) : (
                                <Shield size={15} />
                              )}
                            </button>
                            <button
                              onClick={() =>
                                setConfirmTarget({
                                  kind: user.disabled ? "unlock" : "lock",
                                  user,
                                })
                              }
                              disabled={isSelf}
                              title={
                                isSelf
                                  ? t("You cannot lock your own account.")
                                  : user.disabled
                                    ? t("Unlock {{username}}", { username: user.username })
                                    : t("Lock {{username}}", { username: user.username })
                              }
                              className={`rounded-lg p-1.5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors ${
                                user.disabled
                                  ? "text-amber-600 hover:bg-[var(--background)] dark:text-amber-400"
                                  : "text-[var(--muted-foreground)] hover:bg-destructive/10 hover:text-destructive"
                              }`}
                            >
                              {user.disabled ? <LockOpen size={15} /> : <Lock size={15} />}
                            </button>
                          </div>
                        </td>
                      </tr>
                      {canManageAssignments && expandedUserId === user.id && (
                        <tr>
                          <td colSpan={5} className="p-0">
                            <GrantEditor
                              key={user.id}
                              userId={user.id}
                              lockLearningPolicy={user.preset === "learner"}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <p className="mt-8 text-center text-xs text-[var(--muted-foreground)]">
          {t("PathMind Admin · User Management")}
        </p>
      </div>

      <ConfirmDialog
        open={confirmTarget !== null}
        title={
          confirmTarget?.kind === "lock"
            ? t("Lock account")
            : confirmTarget?.kind === "unlock"
              ? t("Unlock account")
              : confirmTarget?.kind === "promote"
                ? t("Promote to admin")
                : t("Demote to user")
        }
        tone={confirmTarget?.kind === "lock" ? "danger" : "default"}
        confirmLabel={
          confirmTarget?.kind === "lock"
            ? t("Lock")
            : confirmTarget?.kind === "unlock"
              ? t("Unlock")
              : confirmTarget?.kind === "promote"
                ? t("Promote")
                : t("Demote")
        }
        busyLabel={
          confirmTarget?.kind === "lock" || confirmTarget?.kind === "unlock"
            ? t("Saving…")
            : confirmTarget?.kind === "promote"
              ? t("Promoting…")
              : t("Demoting…")
        }
        busy={confirmBusy}
        onConfirm={handleConfirmAction}
        onCancel={() => setConfirmTarget(null)}
      >
        {confirmTarget && (
          <>
            <div className="flex items-center gap-3 rounded-xl border border-[var(--border)] bg-background/50 px-3 py-2.5">
              <UserAvatar
                username={confirmTarget.user.username}
                userId={confirmTarget.user.id}
                avatar={confirmTarget.user.avatar}
                role={confirmTarget.user.role}
                size={32}
              />
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-[var(--foreground)]">
                  {confirmTarget.user.username}
                </p>
                <p className="text-xs text-[var(--muted-foreground)]">
                  {t("{{role}} · joined {{date}}", {
                    role: t(roleLabel(confirmTarget.user.role)),
                    date: formatDate(confirmTarget.user.created_at, lang),
                  })}
                </p>
              </div>
            </div>
            <p className="mt-3">
              {confirmTarget.kind === "lock"
                ? t(
                    "The user is signed out everywhere and cannot sign in until unlocked. Their data is kept.",
                  )
                : confirmTarget.kind === "unlock"
                  ? t("The user can sign in again.")
                  : confirmTarget.kind === "promote"
                  ? t(
                      "Admins can manage users and assignments, and work in the shared main workspace.",
                    )
                  : t(
                      "They will lose access to the admin area and switch to their own assigned workspace.",
                    )}
            </p>
          </>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={planTarget !== null}
        title={t("Assign / change plan")}
        confirmLabel={t("Apply")}
        busy={planBusy}
        onConfirm={() => void handleAssignPlan()}
        onCancel={() => setPlanTarget(null)}
      >
        {planTarget && (
          <div className="space-y-3 text-[var(--foreground)]">
            <p className="text-[var(--muted-foreground)]">
              {t("User")}: <b className="text-[var(--foreground)]">{planTarget.user.username}</b>.{" "}
              {t("Assigning a plan manually does not create a payment transaction.")}
            </p>
            <label className="block text-xs">
              <span className="mb-1 block text-[var(--muted-foreground)]">{t("Plan", { context: "billing" })}</span>
              <select
                value={planTarget.planId}
                onChange={(e) => setPlanTarget({ ...planTarget, planId: e.target.value })}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-sm"
              >
                {plans.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.is_active ? "" : ` (${t("Not on sale")})`}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs">
              <span className="mb-1 block text-[var(--muted-foreground)]">
                {t("Duration (days) — 0 = no expiry; time is added on top of the same active plan")}
              </span>
              <input
                type="number"
                min={0}
                max={3650}
                value={planTarget.durationDays}
                onChange={(e) => setPlanTarget({ ...planTarget, durationDays: e.target.value })}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-sm"
              />
            </label>
          </div>
        )}
      </ConfirmDialog>

      {showCreateDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] px-4"
          role="dialog"
          aria-modal="true"
          onClick={closeCreateDialog}
        >
          <form
            onClick={(e) => e.stopPropagation()}
            onSubmit={handleCreateSubmit}
            className="w-full max-w-sm rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-xl"
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-[var(--foreground)]">
                {t("Add user")}
              </h2>
              <button
                type="button"
                onClick={closeCreateDialog}
                disabled={createSubmitting}
                className="rounded-md p-1 text-[var(--muted-foreground)] hover:bg-[var(--background)] hover:text-[var(--foreground)] disabled:opacity-40"
                aria-label={t("Close")}
              >
                <X size={16} />
              </button>
            </div>

            <label className="mb-3 block text-xs text-[var(--muted-foreground)]">
              {t("Username (or email)")}
              <input
                type="text"
                value={createUsername}
                onChange={(e) => setCreateUsername(e.target.value)}
                disabled={createSubmitting}
                autoComplete="off"
                autoFocus
                className="mt-1 w-full rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-sm text-[var(--foreground)] outline-none focus:border-[var(--ring)]"
              />
            </label>

            <label className="mb-4 block text-xs text-[var(--muted-foreground)]">
              {t("Password (≥ 8 chars)")}
              <input
                type="password"
                value={createPassword}
                onChange={(e) => setCreatePassword(e.target.value)}
                disabled={createSubmitting}
                autoComplete="new-password"
                className="mt-1 w-full rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-sm text-[var(--foreground)] outline-none focus:border-[var(--ring)]"
              />
            </label>

            <fieldset className="mb-4">
              <legend className="mb-1.5 block text-xs text-[var(--muted-foreground)]">
                {t("Account preset")}
              </legend>
              <div
                className="grid grid-cols-3 gap-1 rounded-lg bg-muted/50 p-1"
                role="group"
                aria-label={t("Account preset")}
              >
                {(["standard", "learner", "custom"] as const).map((preset) => (
                  <button
                    key={preset}
                    type="button"
                    disabled={createSubmitting}
                    aria-pressed={createPreset === preset}
                    onClick={() => setCreatePreset(preset)}
                    className={`rounded-md px-2 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
                      createPreset === preset
                        ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                        : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                    }`}
                  >
                    {t(
                      preset === "learner"
                        ? "Learner"
                        : preset === "custom"
                          ? "Custom"
                          : "Standard",
                    )}
                  </button>
                ))}
              </div>
              <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
                {createPreset === "learner"
                  ? t(
                      "Chat and Immersive Reading only, with uploads and tools disabled until assigned.",
                    )
                  : createPreset === "custom"
                    ? t(
                        "Create an ordinary account, then customize its assignments.",
                      )
                    : t(
                        "Create an ordinary account with the default workspace behavior.",
                      )}
              </p>
            </fieldset>

            {createError && (
              <p className="mb-3 text-xs text-destructive">{createError}</p>
            )}

            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={closeCreateDialog}
                disabled={createSubmitting}
                className="rounded-lg px-3 py-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)] disabled:opacity-40"
              >
                {t("Cancel")}
              </button>
              <button
                type="submit"
                disabled={createSubmitting}
                className="rounded-lg bg-[var(--foreground)] px-3 py-1.5 text-sm font-medium text-[var(--background)] hover:opacity-90 disabled:opacity-40"
              >
                {createSubmitting ? t("Creating…") : t("Create")}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
