/**
 * Timezone and locale helpers for PathMind practice and scheduling.
 */

export function getUserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

export function formatTimeElapsed(seconds: number): string {
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hrs > 0) {
    return `${hrs}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

export function formatLocalDateTime(
  dateOrTimestamp: Date | number | string,
  locale?: string,
  options?: Intl.DateTimeFormatOptions
): string {
  const date =
    typeof dateOrTimestamp === 'number'
      ? new Date(dateOrTimestamp * 1000)
      : new Date(dateOrTimestamp);
  const tz = getUserTimezone();
  try {
    return new Intl.DateTimeFormat(locale || undefined, {
      timeZone: tz,
      dateStyle: 'medium',
      timeStyle: 'short',
      ...options,
    }).format(date);
  } catch {
    return date.toLocaleString();
  }
}

export function formatLocalDate(
  dateStr: string,
  locale?: string,
  options?: Intl.DateTimeFormatOptions
): string {
  const date = new Date(`${dateStr}T12:00:00`);
  const tz = getUserTimezone();
  try {
    return new Intl.DateTimeFormat(locale || undefined, {
      timeZone: tz,
      month: 'short',
      day: 'numeric',
      ...options,
    }).format(date);
  } catch {
    return date.toLocaleDateString();
  }
}
