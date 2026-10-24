"use client";

import { Suspense } from "react";
import { AuthPortal } from "@/components/auth/AuthPortal";
import { AuthLoading } from "@/components/auth/AuthLoading";

export default function RegisterPage() {
  return (
    <Suspense
      fallback={<AuthLoading />}
    >
      <AuthPortal initialTab="register" />
    </Suspense>
  );
}
