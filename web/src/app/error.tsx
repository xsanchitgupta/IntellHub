"use client";

import { useEffect } from "react";
import Link from "next/link";

/** A script file the page needs failed to arrive. The static host answers a burst of
 *  requests with 429 and a JSON body, which the browser refuses to run; nothing in the
 *  page is wrong, and reset() alone re-renders without fetching the file again. */
const isChunkError = (e: Error) =>
  e.name === "ChunkLoadError" || /Failed to load chunk|Loading chunk [\w-]+ failed/i.test(e.message || "");
const RELOAD_KEY = "ksp.chunk-reload";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const chunk = isChunkError(error);

  useEffect(() => {
    console.error("Global Error Caught:", error);
    if (!chunk) return;
    // Reload once, after the host's rate window has passed. The timestamp stops a file
    // that is genuinely missing from turning into a reload loop.
    let last = 0;
    try {
      last = Number(sessionStorage.getItem(RELOAD_KEY) || 0);
    } catch {}
    if (Date.now() - last < 30_000) return;
    const t = window.setTimeout(() => {
      try {
        sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
      } catch {}
      window.location.reload();
    }, 1500);
    return () => window.clearTimeout(t);
  }, [error, chunk]);

  if (chunk) {
    return (
      <div className="flex min-h-[60vh] w-full items-center justify-center px-4 py-10">
        <div role="status" className="w-full max-w-md rounded-2xl border border-black/[0.08] bg-white p-8 text-center shadow-[var(--shadow-float)]">
          <h2 className="mb-2 text-lg font-semibold text-ink">Reloading this page</h2>
          <p className="text-sm text-muted">Part of the page did not arrive from the server. It is being fetched again.</p>
          <button
            type="button"
            className="mt-5 rounded-full border border-black/10 bg-black/[0.03] px-5 py-2 text-sm font-medium text-ink transition-colors hover:border-brand/40"
            onClick={() => window.location.reload()}
          >
            Reload now
          </button>
        </div>
      </div>
    );
  }

  // The stack is for whoever is fixing the fault, not for an officer mid-shift;
  // in a production build it is logged to the console above instead of shown.
  const showStack = process.env.NODE_ENV !== "production";

  return (
    // Rendered inside <main>, which already pads for the nav. h-screen/w-screen here
    // added a full viewport on top of that padding and produced a horizontal scroll.
    <div className="flex min-h-[60vh] w-full items-center justify-center px-4 py-10">
      <div role="alert" className="w-full max-w-2xl rounded-2xl border border-crimson/25 bg-white p-8 shadow-[var(--shadow-float)]">
        <h2 className="mb-4 text-2xl font-bold text-crimson">Something went wrong</h2>
        <p className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-black/[0.06] bg-black/[0.03] p-4 font-mono text-sm text-muted">
          {error.message || "An unexpected error occurred while rendering this page."}
          {showStack && error.stack ? `\n\n${error.stack}` : ""}
          {error.digest ? `\n\nReference: ${error.digest}` : ""}
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="rounded-full bg-gradient-to-b from-brand-soft to-brand px-5 py-2.5 font-semibold text-black transition-all hover:brightness-105"
            onClick={() => reset()}
          >
            Try Again
          </button>
          {/* The dashboard moved off the root, which is now the sign-in page. */}
          <Link
            prefetch={false}
            href="/overview"
            className="rounded-full border border-black/10 bg-black/[0.03] px-5 py-2.5 font-medium text-ink transition-colors hover:border-brand/40"
          >
            Back to overview
          </Link>
        </div>
      </div>
    </div>
  );
}
