import { SettingsTabsShell } from "./SettingsTabsShell";

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return <SettingsTabsShell>{children}</SettingsTabsShell>;
}
