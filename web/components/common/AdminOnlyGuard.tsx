"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStatus } from "@/hooks/useAuthStatus";

/**
 * Personalization surfaces (Personas, Skills, Memory) are for
 * administrators only when sign-in is enabled. Single-user installs (auth off)
 * keep full access. Regular accounts are sent back to `fallback`.
 */
export function usePersonalizationAccess(): { ready: boolean; allowed: boolean } {
  const auth = useAuthStatus();
  if (auth.loading) return { ready: false, allowed: false };
  return { ready: true, allowed: !auth.enabled || auth.isAdmin };
}

/**
 * Deployment-wide knowledge settings (engines, server folders, remote
 * retrieval servers) follow the same rule: admins, or single-user installs.
 */
export const useKnowledgeAdminAccess = usePersonalizationAccess;

export default function AdminOnlyGuard({
  children,
  fallback = "/space",
}: Readonly<{ children: React.ReactNode; fallback?: string }>) {
  const router = useRouter();
  const { ready, allowed } = usePersonalizationAccess();

  useEffect(() => {
    if (ready && !allowed) router.replace(fallback);
  }, [ready, allowed, router, fallback]);

  if (!ready || !allowed) return null;
  return <>{children}</>;
}
