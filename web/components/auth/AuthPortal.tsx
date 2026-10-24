"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslation } from "react-i18next";
import {
  login,
  register,
  fetchAuthStatus,
  refreshAuthStatus,
  verifyEmailCode,
  resendEmailCode,
  type AuthFailure,
} from "@/lib/auth";
import { normalizeInternalReturnPath } from "@/shared/auth/return-url";
import Modal from "@/components/common/Modal";
import { TermsSections } from "@/components/auth/TermsSections";
import { Eye, EyeOff, Loader2, CheckCircle2, AlertCircle, MailCheck } from "lucide-react";

export interface AuthPortalProps {
  initialTab?: "login" | "register";
}

type View = "login" | "register" | "verify";
type SignupField =
  | "full_name"
  | "email"
  | "username"
  | "password"
  | "confirm_password"
  | "accept_terms"
  | "code";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const USERNAME_RE = /^[A-Za-z0-9_\-.]{3,64}$/;

/** Translation key for each server/client error code (English source text). */
const ERROR_TEXT: Record<string, string> = {
  full_name_invalid: "Please enter your full name (2–120 characters).",
  email_invalid: "Please enter a valid email address.",
  email_taken: "An account with this email already exists.",
  username_invalid: "Usernames use 3–64 letters, digits, dots, dashes or underscores.",
  username_taken: "That username is already taken. Please choose another.",
  password_too_short: "Password must be at least 8 characters.",
  password_weak: "Password must contain both letters and numbers.",
  password_same_as_username: "Password must not be the same as your username or email.",
  password_mismatch: "Passwords do not match.",
  terms_required: "You must accept the Terms of Use to create an account.",
  code_invalid: "That verification code is not correct.",
  code_expired: "That verification code has expired. Request a new one.",
  code_too_many: "Too many incorrect attempts. Request a new code.",
  network: "Could not reach the server. Please try again later.",
};

/** 0–4 strength score: length, mixed case, digits, symbols. */
export function passwordScore(password: string): number {
  if (!password) return 0;
  let score = 0;
  if (password.length >= 8) score += 1;
  if (password.length >= 12) score += 1;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score += 1;
  if (/\d/.test(password) && /[A-Za-z]/.test(password)) score += 1;
  if (/[^A-Za-z0-9]/.test(password)) score += 1;
  if (password.length < 8) score = Math.min(score, 1);
  return Math.min(score, 4);
}

const STRENGTH_LABELS = ["Too weak", "Weak", "Fair", "Good", "Strong"];
const STRENGTH_COLORS = [
  "bg-red-500",
  "bg-red-500",
  "bg-amber-500",
  "bg-emerald-500",
  "bg-emerald-600",
];

const inputClass = (invalid: boolean) =>
  `w-full px-3.5 py-2.5 rounded-xl border bg-[var(--background)] text-[var(--foreground)]
   placeholder:text-[var(--muted-foreground)] focus:outline-none focus:ring-2
   focus:ring-[var(--primary)] focus:border-transparent transition-shadow text-sm ${
     invalid ? "border-red-500/60" : "border-[var(--border)]"
   }`;

const primaryButton =
  "w-full py-2.5 px-4 rounded-xl font-medium text-sm bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 active:opacity-80 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-sm flex items-center justify-center gap-2 cursor-pointer mt-2";

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/20 text-xs text-red-600 dark:text-red-400 flex items-center gap-2">
      <AlertCircle className="w-4 h-4 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return <p className="mt-1 text-[11px] text-red-600 dark:text-red-400">{message}</p>;
}

export function AuthPortal({ initialTab = "login" }: AuthPortalProps) {
  const { t } = useTranslation();
  const searchParams = useSearchParams();

  // Redirect target: /chat unless a safe internal ?next= is given.
  const rawNext = searchParams.get("next");
  const targetDestination = useMemo(() => {
    if (!rawNext) return "/chat";
    const normalized = normalizeInternalReturnPath(rawNext, "/chat");
    if (
      !normalized ||
      normalized === "/" ||
      normalized.startsWith("/login") ||
      normalized.startsWith("/register")
    ) {
      return "/chat";
    }
    return normalized;
  }, [rawNext]);

  const tabFromQuery = searchParams.get("tab");
  const [view, setView] = useState<View>(
    tabFromQuery === "register" || searchParams.get("invite") ? "register" : initialTab,
  );

  // Login
  const [loginUsername, setLoginUsername] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [showLoginPassword, setShowLoginPassword] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);

  // Register
  const [fullName, setFullName] = useState("");
  const inviteToken = searchParams.get("invite") ?? "";
  const [email, setEmail] = useState(() => inviteEmail(inviteToken));
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [acceptTerms, setAcceptTerms] = useState(false);
  const [asEducator, setAsEducator] = useState(false);
  // Terms open in a dialog: a new tab is blocked in embedded browsers, and
  // leaving the page would lose what the user already typed.
  const [termsOpen, setTermsOpen] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<SignupField, string>>>({});
  const [regError, setRegError] = useState("");
  const [regLoading, setRegLoading] = useState(false);

  // Verify
  const [verifyEmail, setVerifyEmail] = useState("");
  const [verifyCode, setVerifyCode] = useState("");
  const [verifyError, setVerifyError] = useState("");
  const [verifyInfo, setVerifyInfo] = useState("");
  const [verifyLoading, setVerifyLoading] = useState(false);
  const [resendIn, setResendIn] = useState(0);
  const [delivery, setDelivery] = useState<"email" | "log" | "none">("email");
  // Credentials kept in memory only so we can sign in right after verifying.
  const pendingLogin = useRef<{ username: string; password: string } | null>(null);

  const [success, setSuccess] = useState(
    searchParams.get("deleted") === "1"
      ? t("Your account has been deleted.")
      : searchParams.get("registered") === "1"
        ? t("Account created. Please sign in to continue.")
        : "",
  );

  useEffect(() => {
    fetchAuthStatus().then((status) => {
      if (status?.authenticated)
        window.location.href = staffLanding(status.role, rawNext, targetDestination);
    });
  }, [targetDestination, rawNext]);

  useEffect(() => {
    if (resendIn <= 0) return;
    const timer = window.setTimeout(() => setResendIn((s) => s - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [resendIn]);

  const errorText = (failure: Pick<AuthFailure, "code" | "error">, fallback: string) => {
    if (failure.code && ERROR_TEXT[failure.code]) return t(ERROR_TEXT[failure.code]);
    return failure.error || fallback;
  };

  function openVerify(address: string, how: "email" | "log" | "none" = "email") {
    setVerifyEmail(address);
    setVerifyCode("");
    setVerifyError("");
    setVerifyInfo("");
    setDelivery(how);
    setResendIn(60);
    setView("verify");
  }

  async function handleLoginSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoginError("");
    setLoginLoading(true);
    const result = await login(loginUsername.trim(), loginPassword);
    if (result.ok) {
      const st = await refreshAuthStatus().catch(() => null);
      window.location.href = staffLanding(st?.role, rawNext, targetDestination);
      return;
    }
    setLoginLoading(false);
    if (result.code === "email_not_verified" && result.email) {
      pendingLogin.current = { username: loginUsername.trim(), password: loginPassword };
      openVerify(result.email);
      setResendIn(0);
      setVerifyInfo(t("Please verify your email address before signing in."));
      return;
    }
    setLoginError(
      result.code === "network"
        ? t(ERROR_TEXT.network)
        : result.error || t("Incorrect username or password"),
    );
  }

  function validateSignup(): Partial<Record<SignupField, string>> {
    const errors: Partial<Record<SignupField, string>> = {};
    const name = fullName.trim().replace(/\s+/g, " ");
    if (name.length < 2 || name.length > 120) errors.full_name = t(ERROR_TEXT.full_name_invalid);
    if (!EMAIL_RE.test(email.trim())) errors.email = t(ERROR_TEXT.email_invalid);
    if (username.trim() && !USERNAME_RE.test(username.trim()) && !EMAIL_RE.test(username.trim()))
      errors.username = t(ERROR_TEXT.username_invalid);
    if (password.length < 8) errors.password = t(ERROR_TEXT.password_too_short);
    else if (!(/[A-Za-z]/.test(password) && /\d/.test(password)))
      errors.password = t(ERROR_TEXT.password_weak);
    if (confirmPassword !== password) errors.confirm_password = t(ERROR_TEXT.password_mismatch);
    if (!acceptTerms) errors.accept_terms = t(ERROR_TEXT.terms_required);
    return errors;
  }

  async function handleRegisterSubmit(e: React.FormEvent) {
    e.preventDefault();
    setRegError("");
    setSuccess("");
    const errors = validateSignup();
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setRegLoading(true);
    const result = await register({
      full_name: fullName.trim(),
      email: email.trim(),
      username: username.trim() || undefined,
      password,
      confirm_password: confirmPassword,
      accept_terms: acceptTerms,
      role: asEducator ? "educator" : "user",
      invite: inviteToken || undefined,
    });
    if (!result.ok) {
      setRegLoading(false);
      const message = errorText(result, t("Registration failed. Please try again."));
      if (result.field) setFieldErrors({ [result.field as SignupField]: message });
      else setRegError(message);
      return;
    }

    const signInAs = { username: result.username || email.trim(), password };
    if (result.email_verification?.required) {
      pendingLogin.current = signInAs;
      setRegLoading(false);
      openVerify(result.email || email.trim(), result.email_verification.delivery);
      return;
    }
    setSuccess(t("Account created. Signing you in…"));
    const loginRes = await login(signInAs.username, signInAs.password);
    if (loginRes.ok) {
      window.location.href = targetDestination;
      return;
    }
    setRegLoading(false);
    setLoginUsername(signInAs.username);
    setView("login");
    setSuccess(t("Account created. Please sign in to continue."));
  }

  async function handleVerifySubmit(e: React.FormEvent) {
    e.preventDefault();
    setVerifyError("");
    const code = verifyCode.replace(/\D/g, "");
    if (code.length !== 6) {
      setVerifyError(t("Enter the 6-digit code."));
      return;
    }
    setVerifyLoading(true);
    const result = await verifyEmailCode(verifyEmail, code);
    if (!result.ok) {
      setVerifyLoading(false);
      setVerifyError(errorText(result, t(ERROR_TEXT.code_invalid)));
      return;
    }
    const creds = pendingLogin.current;
    if (creds) {
      const loginRes = await login(creds.username, creds.password);
      pendingLogin.current = null;
      if (loginRes.ok) {
        window.location.href = targetDestination;
        return;
      }
    }
    setVerifyLoading(false);
    setLoginUsername(creds?.username || verifyEmail);
    setView("login");
    setSuccess(t("Email verified. Please sign in to continue."));
  }

  async function handleResend() {
    setVerifyError("");
    setVerifyInfo("");
    const result = await resendEmailCode(verifyEmail);
    if (result.ok) {
      setResendIn(60);
      setVerifyInfo(t("A new code has been sent if the address needs verification."));
    } else if (result.code === "code_resend_wait" && result.retryAfter) {
      setResendIn(result.retryAfter);
    } else {
      setVerifyError(errorText(result, t("Request failed")));
    }
  }

  const score = passwordScore(password);
  const switchTo = (next: View) => {
    setView(next);
    setLoginError("");
    setRegError("");
    setFieldErrors({});
  };

  return (
    <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] flex flex-col justify-between items-center p-4 sm:p-6 transition-colors">
      <div className="w-full flex-1 flex flex-col items-center justify-center max-w-[440px] mx-auto py-8 sm:py-12 animate-fade-in">
        <div className="text-center mb-8 flex flex-col items-center">
          <div className="mb-4 flex items-center justify-center">
            <img
              src="/logo.png"
              alt="PathMind"
              width={48}
              height={48}
              className="h-12 w-12 object-contain select-none transition-transform hover:scale-105"
              draggable={false}
            />
          </div>
          <h1 className="font-serif text-[32px] sm:text-[36px] font-medium leading-[1.15] tracking-[-0.015em] text-[var(--foreground)]">
            PathMind
          </h1>
          <p className="mt-2 text-sm text-[var(--muted-foreground)]">
            {view === "login"
              ? t("Sign in to continue your learning")
              : view === "register"
                ? t("Create your personal learning account")
                : t("Verify your email")}
          </p>
        </div>

        {view !== "verify" && (
          <div className="w-full grid grid-cols-2 p-1 bg-[var(--muted)] rounded-xl border border-[var(--border)] mb-5">
            {(["login", "register"] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => switchTo(tab)}
                className={`py-2 text-xs sm:text-sm font-medium rounded-lg transition-all cursor-pointer ${
                  view === tab
                    ? "bg-[var(--card)] text-[var(--foreground)] shadow-xs font-semibold"
                    : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                }`}
              >
                {tab === "login" ? t("Sign in") : t("Sign up")}
              </button>
            ))}
          </div>
        )}

        {success && (
          <div className="w-full mb-4 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-xs sm:text-sm text-emerald-700 dark:text-emerald-400 flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            <span>{success}</span>
          </div>
        )}

        <div className="w-full bg-[var(--card)] border border-[var(--border)] rounded-2xl shadow-sm px-6 py-7 sm:px-8 sm:py-8">
          {view === "login" && (
            <form onSubmit={handleLoginSubmit} className="space-y-4">
              <div>
                <label htmlFor="login-username" className="block text-xs font-medium mb-1.5">
                  {t("Username or email")}
                </label>
                <input
                  id="login-username"
                  type="text"
                  required
                  autoComplete="username"
                  value={loginUsername}
                  onChange={(e) => setLoginUsername(e.target.value)}
                  placeholder={t("you@example.com")}
                  className={inputClass(false)}
                />
              </div>
              <div>
                <label htmlFor="login-password" className="block text-xs font-medium mb-1.5">
                  {t("Password")}
                </label>
                <div className="relative">
                  <input
                    id="login-password"
                    type={showLoginPassword ? "text" : "password"}
                    required
                    autoComplete="current-password"
                    value={loginPassword}
                    onChange={(e) => setLoginPassword(e.target.value)}
                    placeholder="••••••••"
                    className={`${inputClass(false)} pr-10`}
                  />
                  <button
                    type="button"
                    aria-label={showLoginPassword ? t("Hide password") : t("Show password")}
                    onClick={() => setShowLoginPassword(!showLoginPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--muted-foreground)] hover:text-[var(--foreground)] cursor-pointer"
                  >
                    {showLoginPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              {loginError && <ErrorBox message={loginError} />}

              <a href="/forgot-password" className="block text-right text-xs text-[var(--muted-foreground)] underline">
                {t("Forgot password?")}
              </a>

              <button type="submit" disabled={loginLoading} className={primaryButton}>
                {loginLoading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>{t("Signing in…")}</span>
                  </>
                ) : (
                  <span>{t("Sign in")}</span>
                )}
              </button>

              <p className="pt-2 text-center text-xs text-[var(--muted-foreground)]">
                {t("Don't have an account?")}{" "}
                <button
                  type="button"
                  onClick={() => switchTo("register")}
                  className="text-[var(--primary)] hover:underline font-medium cursor-pointer"
                >
                  {t("Sign up")}
                </button>
              </p>
            </form>
          )}

          {view === "register" && (
            <form onSubmit={handleRegisterSubmit} className="space-y-4" noValidate>
              <div>
                <label htmlFor="reg-name" className="block text-xs font-medium mb-1.5">
                  {t("Full name")}
                </label>
                <input
                  id="reg-name"
                  type="text"
                  autoComplete="name"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder={t("Your full name")}
                  className={inputClass(Boolean(fieldErrors.full_name))}
                />
                <FieldError message={fieldErrors.full_name} />
              </div>

              <div>
                <label htmlFor="reg-email" className="block text-xs font-medium mb-1.5">
                  {t("Email")}
                </label>
                <input
                  id="reg-email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder={t("you@example.com")}
                  className={inputClass(Boolean(fieldErrors.email))}
                />
                <FieldError message={fieldErrors.email} />
              </div>

              <div>
                <label htmlFor="reg-username" className="block text-xs font-medium mb-1.5">
                  {t("Username")}{" "}
                  <span className="font-normal text-[var(--muted-foreground)]">
                    ({t("optional — your email is used if empty")})
                  </span>
                </label>
                <input
                  id="reg-username"
                  type="text"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="your_username"
                  className={inputClass(Boolean(fieldErrors.username))}
                />
                <FieldError message={fieldErrors.username} />
              </div>

              <div>
                <label htmlFor="reg-password" className="block text-xs font-medium mb-1.5">
                  {t("Password")}
                </label>
                <div className="relative">
                  <input
                    id="reg-password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="new-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className={`${inputClass(Boolean(fieldErrors.password))} pr-10`}
                  />
                  <button
                    type="button"
                    aria-label={showPassword ? t("Hide password") : t("Show password")}
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--muted-foreground)] hover:text-[var(--foreground)] cursor-pointer"
                  >
                    {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
                {password && (
                  <div className="mt-2">
                    <div className="grid grid-cols-4 gap-1">
                      {[1, 2, 3, 4].map((step) => (
                        <div
                          key={step}
                          className={`h-1 rounded-full ${
                            score >= step ? STRENGTH_COLORS[score] : "bg-[var(--muted)]"
                          }`}
                        />
                      ))}
                    </div>
                    <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                      {t("Password strength")}: {t(STRENGTH_LABELS[score])}
                    </p>
                  </div>
                )}
                {!fieldErrors.password && (
                  <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                    {t("At least 8 characters, with letters and numbers.")}
                  </p>
                )}
                <FieldError message={fieldErrors.password} />
              </div>

              <div>
                <label htmlFor="reg-confirm" className="block text-xs font-medium mb-1.5">
                  {t("Confirm password")}
                </label>
                <input
                  id="reg-confirm"
                  type={showPassword ? "text" : "password"}
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="••••••••"
                  className={inputClass(Boolean(fieldErrors.confirm_password))}
                />
                <FieldError message={fieldErrors.confirm_password} />
              </div>

              <div>
                <label className="flex items-start gap-2 text-xs text-[var(--muted-foreground)] cursor-pointer">
                  <input
                    type="checkbox"
                    checked={asEducator}
                    onChange={(e) => setAsEducator(e.target.checked)}
                    className="mt-0.5 h-3.5 w-3.5 accent-[var(--primary)]"
                  />
                  <span>
                    {t("I am an Educator (I want to build and share my own knowledge)")}
                  </span>
                </label>
              </div>

              <div>
                <label className="flex items-start gap-2 text-xs text-[var(--muted-foreground)] cursor-pointer">
                  <input
                    type="checkbox"
                    checked={acceptTerms}
                    onChange={(e) => setAcceptTerms(e.target.checked)}
                    className="mt-0.5 h-3.5 w-3.5 accent-[var(--primary)]"
                  />
                  <span>
                    {t("I agree to the")}{" "}
                    <button
                      type="button"
                      onClick={(event) => {
                        // Inside the <label>: open the dialog, don't toggle the box.
                        event.preventDefault();
                        setTermsOpen(true);
                      }}
                      className="text-[var(--primary)] hover:underline font-medium cursor-pointer"
                    >
                      {t("Terms of Use")}
                    </button>
                  </span>
                </label>
                <FieldError message={fieldErrors.accept_terms} />
              </div>

              {regError && <ErrorBox message={regError} />}

              <button type="submit" disabled={regLoading} className={primaryButton}>
                {regLoading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>{t("Creating account…")}</span>
                  </>
                ) : (
                  <span>{t("Create account")}</span>
                )}
              </button>

              <p className="pt-2 text-center text-xs text-[var(--muted-foreground)]">
                {t("Already have an account?")}{" "}
                <button
                  type="button"
                  onClick={() => switchTo("login")}
                  className="text-[var(--primary)] hover:underline font-medium cursor-pointer"
                >
                  {t("Sign in")}
                </button>
              </p>
            </form>
          )}

          {view === "verify" && (
            <form onSubmit={handleVerifySubmit} className="space-y-4">
              <div className="flex flex-col items-center text-center gap-2">
                <MailCheck className="w-8 h-8 text-[var(--primary)]" />
                <p className="text-sm">
                  {t("We sent a 6-digit code to {{email}}.", { email: verifyEmail })}
                </p>
                {delivery === "log" && (
                  <p className="text-[11px] text-[var(--muted-foreground)]">
                    {t("Email delivery is not configured on this server; ask the administrator for the code.")}
                  </p>
                )}
              </div>
              <div>
                <label htmlFor="verify-code" className="block text-xs font-medium mb-1.5">
                  {t("Verification code")}
                </label>
                <input
                  id="verify-code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={verifyCode}
                  onChange={(e) => setVerifyCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  placeholder="123456"
                  className={`${inputClass(Boolean(verifyError))} text-center tracking-[0.5em] font-mono text-lg`}
                />
              </div>
              {verifyInfo && (
                <p className="text-xs text-[var(--muted-foreground)] text-center">{verifyInfo}</p>
              )}
              {verifyError && <ErrorBox message={verifyError} />}
              <button type="submit" disabled={verifyLoading} className={primaryButton}>
                {verifyLoading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>{t("Verifying…")}</span>
                  </>
                ) : (
                  <span>{t("Verify email")}</span>
                )}
              </button>
              <div className="flex items-center justify-between pt-1 text-xs">
                <button
                  type="button"
                  onClick={() => switchTo("login")}
                  className="text-[var(--muted-foreground)] hover:text-[var(--foreground)] cursor-pointer"
                >
                  {t("Back to sign in")}
                </button>
                <button
                  type="button"
                  disabled={resendIn > 0}
                  onClick={handleResend}
                  className="text-[var(--primary)] hover:underline font-medium disabled:opacity-50 disabled:no-underline cursor-pointer disabled:cursor-not-allowed"
                >
                  {resendIn > 0
                    ? t("Resend code in {{seconds}}s", { seconds: resendIn })
                    : t("Resend code")}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>

      <footer className="w-full py-4 text-center text-xs text-[var(--muted-foreground)]">
        PathMind ·{" "}
        <button type="button" onClick={() => setTermsOpen(true)} className="hover:underline cursor-pointer">
          {t("Terms of Use")}
        </button>
      </footer>

      <Modal
        isOpen={termsOpen}
        onClose={() => setTermsOpen(false)}
        title={t("Terms of Use")}
        width="lg"
        footer={
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setTermsOpen(false)}
              className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--muted)] cursor-pointer"
            >
              {t("Close")}
            </button>
            {view === "register" && (
              <button
                type="button"
                onClick={() => {
                  setAcceptTerms(true);
                  setFieldErrors((prev) => ({ ...prev, accept_terms: undefined }));
                  setTermsOpen(false);
                }}
                className="rounded-lg bg-[var(--primary)] px-3 py-1.5 text-sm font-medium text-[var(--primary-foreground)] hover:opacity-90 cursor-pointer"
              >
                {t("I agree")}
              </button>
            )}
          </div>
        }
      >
        <div className="max-h-[60vh] overflow-y-auto px-6 py-5">
          <p className="mb-5 text-sm text-[var(--muted-foreground)]">
            {t("Please read these terms before creating a PathMind account.")}
          </p>
          <TermsSections />
        </div>
      </Modal>
    </div>
  );
}

/** Administrators land in /admin and R&D in /rnd unless a deep link was given. */
function staffLanding(role: string | undefined, next: string | null, fallback: string) {
  if (!next && role === "admin") return "/admin";
  if (!next && role === "rnd") return "/rnd";
  return fallback;
}

/** The invitation token carries the invited address (not secret); prefill it. */
function inviteEmail(token: string): string {
  try {
    const body = token.split(".")[0] ?? "";
    const json = atob(body.replace(/-/g, "+").replace(/_/g, "/"));
    return String((JSON.parse(json) as { e?: string }).e ?? "");
  } catch {
    return "";
  }
}
