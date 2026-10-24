"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useAuthStatus } from "@/hooks/useAuthStatus";

export type StaffConsole = "admin" | "rnd";

/** Where each staff role works; nobody else gets a console. */
export const CONSOLE_HOME: Record<StaffConsole, string> = { admin: "/admin", rnd: "/rnd" };

/**
 * Each console belongs to one role: /admin to administrators, /rnd to R&D.
 * The other staff role is sent to its own console, everyone else to the app.
 * (Without sign-in the single local user may open both.)
 */
export default function StaffGuard({
  console: owner,
  children,
}: {
  console: StaffConsole;
  children: React.ReactNode;
}) {
  const auth = useAuthStatus();
  const router = useRouter();
  const mine: StaffConsole | null = !auth.enabled ? owner : auth.isAdmin ? "admin" : auth.isRnd ? "rnd" : null;
  const allowed = mine === owner;

  useEffect(() => {
    if (auth.loading) return;
    if (auth.enabled && !auth.authenticated) router.replace(`/login?next=${CONSOLE_HOME[owner]}`);
    else if (!allowed) router.replace(mine ? CONSOLE_HOME[mine] : "/chat");
  }, [auth.loading, auth.enabled, auth.authenticated, allowed, mine, owner, router]);

  if (auth.loading || !allowed) return null;
  return <>{children}</>;
}
