/**
 * ThemeScript - Initializes theme from localStorage before React hydration
 * This prevents the flash of wrong theme on page load.
 *
 * Must be a Server Component: in Next.js / React 19, <script> tags rendered
 * by Client Components are inert on the client. Rendering it from the server
 * inlines the snippet into the SSR HTML so the browser executes it before
 * hydration.
 */
export default function ThemeScript() {
  const themeScript = `
    (function() {
      try {
        // One-time carry-over of browser preferences saved before the
        // DeepTutor -> PathMind rename (theme, sidebar layout, drafts, ...).
        if (!localStorage.getItem('pathmind-storage-migrated')) {
          const oldKeys = [];
          for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key && key.toLowerCase().indexOf('deeptutor') === 0) oldKeys.push(key);
          }
          oldKeys.forEach(function (key) {
            const value = localStorage.getItem(key);
            if (value !== null) {
              localStorage.setItem('pathmind' + key.slice('deeptutor'.length), value);
            }
            localStorage.removeItem(key);
          });
          localStorage.setItem('pathmind-storage-migrated', '1');
        }

        const stored = localStorage.getItem('pathmind-theme');

        document.documentElement.classList.remove('dark', 'theme-glass', 'theme-snow');

        if (stored === 'dark') {
          document.documentElement.classList.add('dark');
        } else if (stored === 'glass') {
          document.documentElement.classList.add('dark', 'theme-glass');
        } else if (stored === 'snow') {
          document.documentElement.classList.add('theme-snow');
        } else if (stored === 'light') {
          // already clean
        } else {
          // No cached theme (first visit or signed out): the app default.
          // AccountThemeSync then applies the account's own theme, or the
          // deployment default for visitors who are not signed in.
          document.documentElement.classList.add('theme-snow');
        }
      } catch (e) {
        /* localStorage may be disabled */
      }
    })();
  `;

  return (
    <script
      dangerouslySetInnerHTML={{ __html: themeScript }}
      suppressHydrationWarning
    />
  );
}
