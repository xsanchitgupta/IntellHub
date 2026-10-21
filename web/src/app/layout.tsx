import type { Metadata } from "next";
import { Plus_Jakarta_Sans, Space_Grotesk, Noto_Sans_Kannada } from "next/font/google";
import "./globals.css";
import Chrome from "@/components/Chrome";
import { LangProvider } from "@/lib/i18n";

const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-jakarta",
  weight: ["400", "500", "600", "700", "800"],
});
// Neither UI face ships Kannada glyphs, so Kannada text fell back to whatever the
// OS happened to have — different metrics, different weight, often no match at all.
// Loaded as a real face and placed after the Latin ones in the stack, so it is used
// only for the codepoints the others cannot render.
const kannada = Noto_Sans_Kannada({
  subsets: ["kannada"],
  variable: "--font-kannada",
  weight: ["400", "500", "600", "700"],
  display: "swap",
});
const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-space",
  weight: ["500", "600", "700"],
});

export const metadata: Metadata = {
  title: "KSP IntelHub — Karnataka State Police",
  description:
    "Crime Intelligence & Analytical Platform — SCRB, Karnataka State Police.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // `lang` below is only the initial value; LangProvider keeps the live attribute
  // in sync with the chosen language. Hardcoding "en" made a screen reader announce
  // Kannada text with English phonemes, which is worse than not translating at all.
  return (
    <html lang="en">
      <body className={`${jakarta.variable} ${spaceGrotesk.variable} ${kannada.variable} antialiased`}>
        {/* Fixed command-room backdrop */}
        <div className="fx-field print:hidden" />
        <div className="fx-grid print:hidden" />
        <div className="fx-grain print:hidden" />

        {/* First focusable element on the page: a keyboard user should not have to
            tab through the whole command bar to reach what they came for. */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-lg focus:bg-brand focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-white"
        >
          {/* Outside LangProvider by design: it must be the first focusable
              element in the document, before any client provider mounts. */}
          Skip to main content
        </a>

        <LangProvider>
          <Chrome>{children}</Chrome>
        </LangProvider>
      </body>
    </html>
  );
}
