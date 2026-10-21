"use client";

import { usePathname } from "next/navigation";
import FluidNav from "./FluidNav";
import AgentConsole from "./AgentConsole";

/** The sign-in landing. It stands alone: a command bar and an assistant offering to
 *  query records are meaningless — and misleading — before anyone has said who they
 *  are. With trailingSlash the root can arrive as "" or "/", and a cold load via the
 *  Catalyst 404 resolver can briefly show "/index.html". */
function isLanding(pathname: string) {
  return pathname === "" || pathname === "/" || pathname === "/index.html";
}

export default function Chrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || "";

  if (isLanding(pathname)) {
    return (
      <main id="main-content" tabIndex={-1} className="relative z-10 min-h-[100dvh] w-full focus:outline-none">
        {children}
      </main>
    );
  }

  // The Copilot page is the assistant; a second floating composer there would compete.
  const onCopilot = pathname.startsWith("/copilot");
  return (
    <>
      {/* A printed page (the case brief) is the record alone, without the command bar. */}
      <div className="print:hidden"><FluidNav /></div>
      <main
        id="main-content"
        tabIndex={-1}
        className="relative z-10 mx-auto w-full max-w-[1500px] px-5 pt-28 pb-40 md:px-8 focus:outline-none print:p-0"
      >
        {children}
      </main>
      {!onCopilot && <div className="print:hidden"><AgentConsole /></div>}
    </>
  );
}
