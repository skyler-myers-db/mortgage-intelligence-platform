import { useApp, type ThemePreference } from '../AppContext';

// Dark / Light are the prototype's two-state control (design_files/index.html:2);
// System is the 2026-09-21 audit's additive OS-following option (css-02).
const THEME_OPTIONS: ReadonlyArray<{ value: ThemePreference; label: string }> = [
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
  { value: 'system', label: 'System' },
];

/**
 * The one theme control, shared by the Console and the Administration
 * appearance section. It edits and marks the stored PREFERENCE, so System
 * stays selected while the painted theme follows the OS; a control that
 * marked the painted theme showed "Light" or "Dark" as chosen when the user
 * had chosen System.
 */
export function ThemePreferenceControl() {
  const { themePreference, setThemePreference } = useApp();
  return (
    <div className="segmented" role="group" aria-label="Theme">
      {THEME_OPTIONS.map((option) => (
        <button
          key={option.value}
          className={themePreference === option.value ? 'is-active' : ''}
          onClick={() => setThemePreference(option.value)}
          type="button"
          aria-pressed={themePreference === option.value}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
