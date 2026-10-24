import AppShell from "@/components/layout/AppShell";
import StaffGuard from "@/components/admin/StaffGuard";
import StaffSidebar from "@/components/staff/StaffSidebar";

// The administrators' console. R&D has its own at /rnd; neither shares the
// learner/educator sidebar.
export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <StaffGuard console="admin">
      <AppShell sidebar={<StaffSidebar console="admin" />}>{children}</AppShell>
    </StaffGuard>
  );
}
