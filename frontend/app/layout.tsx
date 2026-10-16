import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { ExtractorStatus } from "@/components/extractor-status";
import { FOCUS_RING } from "@/lib/styles";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Due Diligence Desk",
  description: "AI-assisted extraction, validation and analyst review for investment due diligence",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full bg-stone-50 text-stone-900">
        <a href="#main" className="sr-only rounded bg-white px-3 py-2 focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-10 focus:ring-2 focus:ring-sky-600">
          Skip to content
        </a>
        <header className="border-b border-stone-300 bg-white">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
            <Link href="/" className={`rounded font-semibold tracking-tight ${FOCUS_RING}`}>
              Due Diligence Desk
            </Link>
            <nav aria-label="Main" className="flex gap-4 text-sm text-stone-600">
              <Link href="/" className={`rounded hover:text-stone-900 ${FOCUS_RING}`}>Projects</Link>
              <Link href="/ops" className={`rounded hover:text-stone-900 ${FOCUS_RING}`}>Pipeline ops</Link>
            </nav>
            <div className="sm:ml-auto">
              <ExtractorStatus />
            </div>
          </div>
        </header>
        <main id="main" className="mx-auto max-w-7xl px-4 py-6 sm:px-6">{children}</main>
      </body>
    </html>
  );
}
