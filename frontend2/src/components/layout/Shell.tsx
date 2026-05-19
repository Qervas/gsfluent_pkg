import type { ReactNode } from "react";
import { TopBar } from "./TopBar";

export function Shell({ children }: { children: ReactNode }): JSX.Element {
  return (
    <div className="min-h-screen flex flex-col">
      <TopBar />
      <main className="flex-1 px-6 pt-20 pb-6 max-w-screen-2xl w-full mx-auto">
        {children}
      </main>
    </div>
  );
}
