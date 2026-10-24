import UtilitySidebar from "@/components/sidebar/UtilitySidebar";
import AppShell from "@/components/layout/AppShell";
import { CapabilityAccessProvider } from "@/components/access/CapabilityAccessContext";
import CapabilityGate from "@/components/access/CapabilityGate";
import { EntitlementsProvider } from "@/components/access/EntitlementsContext";
import PlanGate from "@/components/access/PlanGate";

export default function UtilityLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <CapabilityAccessProvider>
    <EntitlementsProvider>
      <AppShell sidebar={<UtilitySidebar />}>
        <CapabilityGate>
          <PlanGate>{children}</PlanGate>
        </CapabilityGate>
      </AppShell>
    </EntitlementsProvider>
    </CapabilityAccessProvider>
  );
}
