import AppShell from "@/components/layout/AppShell";
import StaffGuard from "@/components/admin/StaffGuard";
import StaffSidebar from "@/components/staff/StaffSidebar";

// The R&D console: the main knowledge graph, samples, branch reviews.
// Administrators have their own at /admin.
export default function RndLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <StaffGuard console="rnd">
      <AppShell sidebar={<StaffSidebar console="rnd" />}>{children}</AppShell>
    </StaffGuard>
  );
}
