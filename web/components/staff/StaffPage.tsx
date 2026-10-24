"use client";


export default function StaffPage({
  title,
  subtitle,
  children,
  wide = false,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div className="h-full overflow-y-auto bg-[var(--background)] px-4 py-8 text-[var(--foreground)] sm:px-6 lg:px-8">
      <div className={`mx-auto ${wide ? "max-w-6xl" : "max-w-4xl"}`}>
        <h1 className="text-lg font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 text-[13px] text-[var(--muted-foreground)]">{subtitle}</p>}
        <div className="mt-5">{children}</div>
      </div>
    </div>
  );
}
