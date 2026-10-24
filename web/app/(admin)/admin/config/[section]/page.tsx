import ConsoleSettingsPage from "@/components/staff/pages/ConsoleSettingsPage";

export default async function AdminConfigPage({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  return <ConsoleSettingsPage section={section} />;
}
