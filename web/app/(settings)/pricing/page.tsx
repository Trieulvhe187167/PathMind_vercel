"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowLeft,
  Check,
  CheckCircle2,
  Copy,
  CreditCard,
  QrCode,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { PaymentHistory } from "@/components/billing/PaymentHistory";
import { SubscriptionOverview } from "@/components/billing/SubscriptionOverview";
import {
  PAYMENT_STATUS_LABEL,
  cancelPendingPayment,
  createCheckoutSession,
  fetchBillingConfig,
  fetchModelRates,
  formatMultiplier,
  rateFor,
  fetchPaymentStatus,
  fetchSubscriptionPlans,
  fetchUserQuotaSummary,
  formatBillingDate,
  formatUsd,
  formatVnd,
  listMyPayments,
  simulatePayment,
  switchToFreePlan,
  confirmVnpayReturn,
  type BillingConfig,
  type ModelRate,
  type BillingInterval,
  type CheckoutSession,
  type PaymentGateway,
  type PaymentRecord,
  type SubscriptionPlanItem,
  type UserUsageSummary,
} from "@/lib/billing-api";

const GATEWAY_ICON: Record<PaymentGateway, typeof QrCode> = {
  vnpay: CreditCard,
};

const isPaid = (p: SubscriptionPlanItem) =>
  (p.price_monthly || 0) > 0 || (p.price_yearly || 0) > 0;

function yearlyPrice(p: SubscriptionPlanItem): number {
  return p.price_yearly > 0 ? p.price_yearly : p.price_monthly * 12;
}

export default function PricingPage() {
  const { t } = useTranslation();
  const [plans, setPlans] = useState<SubscriptionPlanItem[]>([]);
  const [quota, setQuota] = useState<UserUsageSummary | null>(null);
  const [config, setConfig] = useState<BillingConfig | null>(null);
  const [payments, setPayments] = useState<PaymentRecord[]>([]);
  const [rates, setRates] = useState<ModelRate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [billingCycle, setBillingCycle] = useState<BillingInterval>("monthly");

  // Checkout
  const [selectedPlan, setSelectedPlan] = useState<SubscriptionPlanItem | null>(
    null,
  );
  const [gateway, setGateway] = useState<PaymentGateway>("vnpay");
  const [checkout, setCheckout] = useState<CheckoutSession | null>(null);
  const [checkoutBusy, setCheckoutBusy] = useState(false);
  const [checkoutError, setCheckoutError] = useState("");
  const [paid, setPaid] = useState(false);
  const [copied, setCopied] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Confirmations
  const [confirm, setConfirm] = useState<
    null | { kind: "downgrade"; plan: SubscriptionPlanItem }
  >(null);
  const [confirmBusy, setConfirmBusy] = useState(false);

  const loggedIn = quota !== null;

  const loadData = useCallback(async () => {
    setError("");
    try {
      const [fetchedPlans, fetchedQuota, fetchedConfig, fetchedRates] =
        await Promise.all([
          fetchSubscriptionPlans(),
          fetchUserQuotaSummary(),
          fetchBillingConfig().catch(() => null),
          fetchModelRates().catch(() => null),
        ]);
      setPlans(fetchedPlans);
      setRates(fetchedRates?.rates ?? []);
      setQuota(fetchedQuota);
      setConfig(fetchedConfig);
      if (fetchedQuota) setPayments(await listMyPayments().catch(() => []));
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : t("Could not load pricing"),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  // Back from VNPAY: /pricing?vnp_TxnRef=DT…&vnp_ResponseCode=…&vnp_SecureHash=…
  useEffect(() => {
    const params = Object.fromEntries(new URLSearchParams(window.location.search));
    if (!params.vnp_TxnRef || !params.vnp_SecureHash) return;
    window.history.replaceState(null, "", window.location.pathname);
    const vnpParams: Record<string, string> = {};
    for (const [key, value] of Object.entries(params))
      if (key.startsWith("vnp_")) vnpParams[key] = value;
    setNotice(t("Confirming your payment…"));
    void confirmVnpayReturn(vnpParams)
      .then((payment) => {
        if (payment.status === "completed") {
          setNotice(t("Payment received — your plan is active."));
          void loadData();
        } else if (payment.vnpay_response_code === "24") {
          setNotice(t("Payment canceled. You were not charged."));
        } else {
          setNotice("");
          setError(
            t("The payment was not completed (VNPAY code {{code}}).", {
              code: payment.vnpay_response_code || "?",
            }),
          );
        }
      })
      .catch((err: unknown) => {
        setNotice("");
        setError(err instanceof Error ? err.message : t("Could not check the payment"));
      });
  }, [loadData, t]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }, []);
  useEffect(() => stopPolling, [stopPolling]);

  const enabledGateways = useMemo(
    () => (config?.gateways ?? []).filter((g) => g.enabled),
    [config],
  );

  const yearlySaving = useMemo(() => {
    const paidPlans = plans.filter(isPaid);
    const savings = paidPlans
      .map((p) =>
        p.price_monthly > 0 ? 1 - yearlyPrice(p) / (p.price_monthly * 12) : 0,
      )
      .filter((s) => s > 0.005);
    return savings.length ? Math.round(Math.max(...savings) * 100) : 0;
  }, [plans]);

  const sub = quota?.subscription ?? null;
  const currentPlanId = quota?.plan?.id;

  function openCheckout(plan: SubscriptionPlanItem) {
    setSelectedPlan(plan);
    setCheckout(null);
    setCheckoutError("");
    setPaid(false);
    setGateway(enabledGateways[0]?.id ?? "vnpay");
  }

  function closeCheckout() {
    stopPolling();
    setSelectedPlan(null);
    setCheckout(null);
    if (paid) void loadData();
  }

  async function handleCreateCheckout() {
    if (!selectedPlan) return;
    setCheckoutBusy(true);
    setCheckoutError("");
    try {
      const session = await createCheckoutSession(
        selectedPlan.id,
        billingCycle,
        gateway,
      );
      if (session.checkout_url) {
        // VNPAY: pay on VNPAY's page (QR / ATM / international card), then
        // come back to /pricing with the signed result.
        window.location.assign(session.checkout_url);
        return;
      }
      setCheckout(session);
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const status = await fetchPaymentStatus(session.transaction_id);
          if (status.status === "completed") {
            stopPolling();
            setPaid(true);
          } else if (status.status !== "pending") {
            stopPolling();
            setCheckoutError(
              t("The payment is no longer pending ({{status}}).", {
                status: t(PAYMENT_STATUS_LABEL[status.status]),
              }),
            );
          }
        } catch {
          /* keep polling */
        }
      }, 5000);
    } catch (err: unknown) {
      setCheckoutError(
        err instanceof Error ? err.message : t("Could not create the payment"),
      );
    } finally {
      setCheckoutBusy(false);
    }
  }

  async function handleSimulate() {
    if (!checkout) return;
    setCheckoutBusy(true);
    try {
      await simulatePayment(checkout.transaction_id);
      stopPolling();
      setPaid(true);
    } catch (err: unknown) {
      setCheckoutError(
        err instanceof Error ? err.message : t("Payment simulation failed"),
      );
    } finally {
      setCheckoutBusy(false);
    }
  }

  async function handleCancelCheckout() {
    if (checkout && !paid) {
      await cancelPendingPayment(checkout.transaction_id).catch(
        () => undefined,
      );
    }
    closeCheckout();
    void loadData();
  }

  async function handleConfirm() {
    if (!confirm) return;
    setConfirmBusy(true);
    try {
      const res = await switchToFreePlan(confirm.plan.id);
      setNotice(res.message);
      setConfirm(null);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("Action failed"));
      setConfirm(null);
    } finally {
      setConfirmBusy(false);
    }
  }

  function planAction(p: SubscriptionPlanItem) {
    if (!loggedIn) {
      return {
        label: t("Sign in to subscribe"),
        disabled: false,
        onClick: () => (window.location.href = "/login?next=/pricing"),
      };
    }
    if (quota?.is_unlimited)
      return {
        label: t("Administrator — unlimited"),
        disabled: true,
        onClick: () => {},
      };
    const isCurrent = currentPlanId === p.id;
    if (!isPaid(p)) {
      if (isCurrent)
        return { label: t("Current plan"), disabled: true, onClick: () => {} };
      if (sub?.cancel_at_period_end)
        return {
          label: t("Switching to this plan"),
          disabled: true,
          onClick: () => {},
        };
      return {
        label: t("Switch to the free plan"),
        disabled: false,
        onClick: () => setConfirm({ kind: "downgrade", plan: p }),
      };
    }
    if (isCurrent) {
      return sub?.current_period_end
        ? {
            label: t("Renew"),
            disabled: false,
            onClick: () => openCheckout(p),
          }
        : { label: t("Current plan"), disabled: true, onClick: () => {} };
    }
    return {
      label: t("Upgrade now"),
      disabled: false,
      onClick: () => openCheckout(p),
    };
  }

  const checkoutAmount = selectedPlan
    ? billingCycle === "yearly"
      ? yearlyPrice(selectedPlan)
      : selectedPlan.price_monthly
    : 0;

  return (
    <div className="h-screen overflow-y-auto bg-[var(--background)] px-4 py-10 text-[var(--foreground)] sm:px-6 lg:px-8">
      {/* Top bar */}
      <div className="mx-auto mb-8 flex max-w-6xl items-center justify-between">
        <Link
          href={loggedIn ? "/settings/billing" : "/"}
          className="inline-flex items-center gap-2 text-sm text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
        >
          <ArrowLeft className="h-4 w-4" />
          {loggedIn ? t("Back to Plans & billing") : t("Home")}
        </Link>
        {quota && (
          <span className="rounded-full border border-primary/30 bg-primary/15 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-primary">
            {quota.is_unlimited ? t("Admin") : quota.plan.name}
          </span>
        )}
      </div>

      {/* Hero */}
      <div className="mx-auto mb-10 max-w-4xl text-center">
        <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 text-xs font-semibold text-primary">
          <Sparkles className="h-3.5 w-3.5" />
          {t("Learn smarter with PathMind AI")}
        </div>
        <h1 className="font-serif text-[34px] font-medium leading-[1.15] tracking-[-0.015em] sm:text-[42px]">
          {t("Plans & pricing")}
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-base text-[var(--muted-foreground)]">
          {t(
            "Choose the plan that fits how you learn. You can upgrade, renew or cancel auto-renewal at any time.",
          )}
        </p>

        <div className="mt-8 inline-flex items-center gap-1 rounded-xl border border-[var(--border)] bg-[var(--card)] p-1">
          {(["monthly", "yearly"] as const).map((cycle) => (
            <button
              key={cycle}
              onClick={() => setBillingCycle(cycle)}
              className={`flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium transition-all ${
                billingCycle === cycle
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              }`}
            >
              {cycle === "monthly" ? t("Monthly") : t("Yearly")}
              {cycle === "yearly" && yearlySaving > 0 && (
                <span className="rounded border border-warning/40 bg-warning-surface px-1.5 py-0.5 text-[10px] font-bold uppercase text-warning">
                  {t("Save up to {{percent}}%", { percent: yearlySaving })}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {(error || notice) && (
        <div className="mx-auto mb-6 max-w-6xl">
          {error && (
            <div className="mb-2 flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span className="flex-1">{error}</span>
              <button onClick={() => setError("")} aria-label={t("Close")}>
                <X className="h-4 w-4" />
              </button>
            </div>
          )}
          {notice && (
            <div className="flex items-center gap-2 rounded-xl border border-success/30 bg-success-surface p-3 text-sm text-success">
              <Check className="h-4 w-4 shrink-0" />
              <span className="flex-1">{notice}</span>
              <button onClick={() => setNotice("")} aria-label={t("Close")}>
                <X className="h-4 w-4" />
              </button>
            </div>
          )}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-20 text-sm text-[var(--muted-foreground)]">
          <RefreshCw className="h-4 w-4 animate-spin" /> {t("Loading…")}
        </div>
      ) : (
        <>
          {/* Subscription + usage */}
          {quota && (
            <div className="mx-auto mb-12 max-w-6xl">
              <SubscriptionOverview
                quota={quota}
                onChanged={loadData}
                onNotice={setNotice}
                onError={setError}
              />
            </div>
          )}

          {/* Plan cards */}
          <div
            className={`mx-auto mb-14 grid grid-cols-1 gap-6 ${
              plans.length >= 4
                ? "max-w-7xl md:grid-cols-2 xl:grid-cols-4"
                : "max-w-6xl md:grid-cols-3"
            }`}
          >
            {plans.map((p) => {
              const highlight = p.id === "pro";
              const isCurrent = currentPlanId === p.id && !quota?.is_unlimited;
              const monthly =
                billingCycle === "yearly"
                  ? yearlyPrice(p) / 12
                  : p.price_monthly;
              const action = planAction(p);
              return (
                <div
                  key={p.id}
                  className={`relative flex flex-col justify-between rounded-2xl border bg-[var(--card)] p-7 transition-all ${
                    highlight
                      ? "border-primary surface-raised "
                      : "border-[var(--border)]"
                  }`}
                >
                  {highlight && (
                    <div className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-primary px-4 py-1 text-[11px] font-semibold uppercase tracking-wider text-primary-foreground">
                      {t("Most popular")}
                    </div>
                  )}
                  <div>
                    <div className="mb-3 flex items-center justify-between">
                      <h3 className="text-lg font-semibold">{p.name}</h3>
                      {isCurrent && (
                        <span className="rounded-full border border-success/30 bg-success-surface px-2.5 py-0.5 text-xs font-semibold text-success">
                          {t("Current plan")}
                        </span>
                      )}
                    </div>
                    <div className="mb-1 flex items-baseline gap-1">
                      <span className="font-serif text-4xl font-medium tracking-tight">
                        {formatUsd(monthly)}
                      </span>
                      <span className="text-sm text-[var(--muted-foreground)]">
                        {t("/month")}
                      </span>
                    </div>
                    <p className="mb-4 h-4 text-xs text-[var(--muted-foreground)]">
                      {isPaid(p) && billingCycle === "yearly"
                        ? t("Billed {{amount}} per year", {
                            amount: formatUsd(yearlyPrice(p)),
                          })
                        : ""}
                      {isPaid(p) && billingCycle === "monthly" && config
                        ? `≈ ${formatVnd(Math.round(p.price_monthly * config.usd_vnd_rate))}${t("/month")}`
                        : ""}
                    </p>
                    {p.description && (
                      <p className="mb-5 text-xs text-[var(--muted-foreground)]">
                        {p.description}
                      </p>
                    )}
                    <div className="mb-6 space-y-2.5">
                      {p.features.map((f) => (
                        <div
                          key={f}
                          className="flex items-start gap-2.5 text-sm"
                        >
                          <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                          <span>{f}</span>
                        </div>
                      ))}
                      {p.allowed_models.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 border-t border-[var(--border)] pt-3">
                          {p.allowed_models.slice(0, 5).map((m) => {
                            const rate = rateFor(rates, m);
                            return (
                              <span
                                key={m}
                                title={
                                  rate
                                    ? t("{{model}} — each token costs {{rate}} credits", {
                                        model: rate.name,
                                        rate: formatMultiplier(rate.multiplier),
                                      })
                                    : m
                                }
                                className="rounded-md border border-[var(--border)] bg-[var(--muted)] px-2 py-0.5 font-mono text-[10px]"
                              >
                                {m}
                                {rate && (
                                  <span className="ml-1 text-primary">
                                    {formatMultiplier(rate.multiplier)}
                                  </span>
                                )}
                              </span>
                            );
                          })}
                          {p.allowed_models.length > 5 && (
                            <span className="px-1 text-[10px] text-[var(--muted-foreground)]">
                              +{p.allowed_models.length - 5}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                  <button
                    disabled={action.disabled}
                    onClick={action.onClick}
                    className={`w-full rounded-xl px-4 py-3 text-sm font-semibold transition-all ${
                      action.disabled
                        ? "cursor-not-allowed border border-[var(--border)] bg-[var(--muted)] text-[var(--muted-foreground)]"
                        : highlight
                          ? "bg-primary text-primary-foreground hover:opacity-90"
                          : "border border-[var(--border)] bg-[var(--background)] hover:bg-[var(--muted)]"
                    }`}
                  >
                    {action.label}
                  </button>
                </div>
              );
            })}
          </div>

          {/* Credits explainer */}
          {rates.length > 0 && (
            <div className="mx-auto mb-10 max-w-6xl rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6">
              <h2 className="mb-1 font-serif text-lg font-medium">
                {t("How are credits counted?")}
              </h2>
              <p className="mb-4 text-xs text-[var(--muted-foreground)]">
                {t(
                  "One token of the cheapest model costs about 1 credit. Stronger models cost more credits in proportion to their API price, so with the same plan you can ask many questions with a fast model or fewer with a strong one.",
                )}
                {quota?.default_model && (
                  <>
                    {" "}
                    {t("The current default model is {{model}} ({{rate}}).", {
                      model: quota.default_model.name,
                      rate: formatMultiplier(quota.default_model.multiplier),
                    })}
                  </>
                )}
              </p>
              <div className="flex flex-wrap gap-2">
                {rates.map((r) => (
                  <span
                    key={r.id}
                    className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-2.5 py-1 text-xs"
                  >
                    {r.name}{" "}
                    <b className="text-primary">
                      {formatMultiplier(r.multiplier)}
                    </b>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Payment history */}
          <div className="mx-auto max-w-6xl">
            <PaymentHistory payments={payments} />
          </div>
        </>
      )}

      {/* Checkout modal */}
      {selectedPlan && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] p-4"
          role="dialog"
          aria-modal="true"
        >
          <div className="relative max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6 surface-raised sm:p-8">
            <button
              onClick={() => void handleCancelCheckout()}
              className="absolute right-5 top-5 text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              aria-label={t("Close")}
            >
              <X className="h-5 w-5" />
            </button>

            {paid ? (
              <div className="py-6 text-center">
                <CheckCircle2 className="mx-auto mb-4 h-16 w-16 text-success" />
                <h3 className="mb-2 font-serif text-2xl font-medium">
                  {t("Payment successful!")}
                </h3>
                <p className="mb-6 text-sm text-[var(--muted-foreground)]">
                  {t("{{plan}} is now active. Your new limits apply right away.", {
                    plan: selectedPlan.name,
                  })}
                </p>
                <button
                  onClick={closeCheckout}
                  className="w-full rounded-xl bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground hover:opacity-90"
                >
                  {t("Start learning")}
                </button>
              </div>
            ) : !checkout ? (
              <>
                <h3 className="mb-1 font-serif text-2xl font-medium">
                  {t("Subscribe to {{plan}}", { plan: selectedPlan.name })}
                </h3>
                <p className="mb-6 text-sm text-[var(--muted-foreground)]">
                  {billingCycle === "yearly" ? t("12-month period") : t("1-month period")} ·{" "}
                  <span className="text-lg font-semibold text-[var(--foreground)]">
                    {formatUsd(checkoutAmount)}
                  </span>
                  {config && (
                    <>
                      {" "}
                      ≈{" "}
                      {formatVnd(
                        Math.round(checkoutAmount * config.usd_vnd_rate),
                      )}
                    </>
                  )}
                </p>

                {enabledGateways.length === 0 ? (
                  <div className="mb-6 rounded-xl border border-warning/30 bg-warning-surface p-4 text-sm text-warning">
                    {t(
                      "No payment method is configured on the server. Please contact an administrator to activate a plan.",
                    )}
                  </div>
                ) : (
                  <div className="mb-6">
                    <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--muted-foreground)]">
                      {t("Payment method")}
                    </div>
                    <div className="grid grid-cols-3 gap-3">
                      {enabledGateways.map((g) => {
                        const Icon = GATEWAY_ICON[g.id];
                        return (
                          <button
                            key={g.id}
                            onClick={() => setGateway(g.id)}
                            className={`flex flex-col items-center gap-2 rounded-xl border p-3 text-xs font-semibold transition-all ${
                              gateway === g.id
                                ? "border-primary bg-primary/10 text-primary"
                                : "border-[var(--border)] text-[var(--muted-foreground)] hover:border-primary"
                            }`}
                          >
                            <Icon className="h-5 w-5" />
                            {g.label}
                            {g.demo_only && (
                              <span className="text-[9px] font-normal uppercase opacity-70">
                                {t("demo")}
                              </span>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {checkoutError && (
                  <p className="mb-4 text-sm text-destructive">
                    {checkoutError}
                  </p>
                )}

                <div className="flex gap-3">
                  <button
                    onClick={closeCheckout}
                    className="flex-1 rounded-xl border border-[var(--border)] px-4 py-3 text-sm font-semibold hover:bg-[var(--muted)]"
                  >
                    {t("Cancel")}
                  </button>
                  <button
                    disabled={checkoutBusy || enabledGateways.length === 0}
                    onClick={() => void handleCreateCheckout()}
                    className="flex-1 rounded-xl bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-50"
                  >
                    {checkoutBusy ? t("Creating…") : t("Continue to payment")}
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3 className="mb-1 font-serif text-xl font-medium">
                  {t("Complete your payment")}
                </h3>
                <p className="mb-5 text-xs text-[var(--muted-foreground)]">
                  {checkout.message}
                </p>

                <div className="mb-5 rounded-2xl border border-[var(--border)] bg-[var(--background)] p-4 text-center">
                  {checkout.qr_image_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={checkout.qr_image_url}
                      alt="VietQR"
                      className="mx-auto mb-3 h-56 w-56 rounded-xl bg-white object-contain p-2"
                    />
                  ) : (
                    <QrCode className="mx-auto mb-3 h-12 w-12 text-[var(--muted-foreground)]" />
                  )}
                  <dl className="space-y-1.5 text-left text-sm">
                    {checkout.bank && (
                      <>
                        <div className="flex justify-between gap-2">
                          <dt className="text-[var(--muted-foreground)]">
                            {t("Bank")}
                          </dt>
                          <dd>
                            {checkout.bank.bank_name || checkout.bank.bank_bin}
                          </dd>
                        </div>
                        <div className="flex justify-between gap-2">
                          <dt className="text-[var(--muted-foreground)]">
                            {t("Account number")}
                          </dt>
                          <dd className="font-mono">
                            {checkout.bank.account_no}
                          </dd>
                        </div>
                        <div className="flex justify-between gap-2">
                          <dt className="text-[var(--muted-foreground)]">
                            {t("Account holder")}
                          </dt>
                          <dd>{checkout.bank.account_name}</dd>
                        </div>
                      </>
                    )}
                    <div className="flex justify-between gap-2">
                      <dt className="text-[var(--muted-foreground)]">
                        {t("Amount")}
                      </dt>
                      <dd className="font-semibold">
                        {formatUsd(checkout.amount)} ·{" "}
                        {formatVnd(checkout.amount_vnd)}
                      </dd>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <dt className="text-[var(--muted-foreground)]">
                        {t("Transfer note")}
                      </dt>
                      <dd className="flex items-center gap-1.5 font-mono font-semibold text-primary">
                        {checkout.transfer_content}
                        <button
                          onClick={() => {
                            void navigator.clipboard?.writeText(
                              checkout.transfer_content,
                            );
                            setCopied(true);
                            setTimeout(() => setCopied(false), 1500);
                          }}
                          aria-label={t("Copy")}
                        >
                          {copied ? (
                            <Check className="h-3.5 w-3.5" />
                          ) : (
                            <Copy className="h-3.5 w-3.5" />
                          )}
                        </button>
                      </dd>
                    </div>
                  </dl>
                </div>

                <p className="mb-4 flex items-center justify-center gap-2 text-xs text-[var(--muted-foreground)]">
                  <RefreshCw className="h-3.5 w-3.5 animate-spin" />{" "}
                  {t("Waiting for payment confirmation…")}
                </p>
                {checkoutError && (
                  <p className="mb-4 text-center text-sm text-destructive">
                    {checkoutError}
                  </p>
                )}

                <div className="flex flex-col gap-2 sm:flex-row">
                  <button
                    onClick={() => void handleCancelCheckout()}
                    className="flex-1 rounded-xl border border-[var(--border)] px-4 py-2.5 text-sm font-semibold hover:bg-[var(--muted)]"
                  >
                    {t("Cancel payment")}
                  </button>
                  {checkout.demo_mode && (
                    <button
                      disabled={checkoutBusy}
                      onClick={() => void handleSimulate()}
                      className="flex-1 rounded-xl border border-warning/40 bg-warning-surface px-4 py-2.5 text-sm font-semibold text-warning hover:bg-warning/15 disabled:opacity-50"
                    >
                      {t("Simulate payment (demo)")}
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirm !== null}
        title={t("Switch to the free plan?")}
        tone="danger"
        confirmLabel={t("Confirm")}
        cancelLabel={t("Keep it")}
        busy={confirmBusy}
        onConfirm={() => void handleConfirm()}
        onCancel={() => setConfirm(null)}
      >
        {sub?.current_period_end
          ? t(
              "You keep {{plan}} until {{date}}, then your account moves to the free plan.",
              {
                plan: sub.plan_name,
                date: formatBillingDate(sub.current_period_end),
              },
            )
          : t("Your account will move to the free plan.")}
      </ConfirmDialog>
    </div>
  );
}
