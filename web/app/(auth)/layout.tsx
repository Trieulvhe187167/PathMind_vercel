export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--background)] text-[var(--foreground)]">
      {children}
    </div>
  );
}
