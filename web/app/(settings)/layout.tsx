import { CapabilityAccessProvider } from "@/components/access/CapabilityAccessContext";
import CapabilityGate from "@/components/access/CapabilityGate";
import { EntitlementsProvider } from "@/components/access/EntitlementsContext";
import PlanGate from "@/components/access/PlanGate";

/** Settings owns its navigation, independently of the conversation sidebar. */
export default function SettingsRouteLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <CapabilityAccessProvider>
    <EntitlementsProvider>
      <CapabilityGate>
          <PlanGate>{children}</PlanGate>
        </CapabilityGate>
    </EntitlementsProvider>
    </CapabilityAccessProvider>
  );
}
