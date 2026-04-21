import type { PropsWithChildren } from 'react';
import { AppProvider } from '../AppContext';
import { Rail } from './Rail';
import { Topbar } from './Topbar';
import { Console } from './Console';
import { EvidenceDrawer } from '../mortgage/EvidenceDrawer';
import { GenieChat } from '../mortgage/GenieChat';
import { DataMesh } from '../fx/DataMesh';

/**
 * Module 0 AppShell — rail + topbar + main grid (matches the prototype's
 * `grid-template-areas: "rail topbar" "rail main"`). A single AppProvider
 * wraps the whole tree so Topbar, Console, GenieChat, EvidenceDrawer and
 * every page share theme / accent / density / drawer / genie state.
 *
 * DataMesh sits inside `.main` (pointer-events none, z-index 0) as the
 * always-on subtle depth layer; `.main__inner` already owns z-index 1 so
 * page content stays on top.
 */
export function AppShell({ children }: PropsWithChildren) {
  return (
    <AppProvider>
      <div className="app-shell">
        <Rail />
        <Topbar />
        <main className="main">
          <DataMesh />
          {children}
        </main>
        <EvidenceDrawer />
        <Console />
        <GenieChat />
      </div>
    </AppProvider>
  );
}
