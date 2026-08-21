import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";

import { Providers } from "@/components/providers";

import "./globals.css";

// Vendored under app/fonts/ rather than pulled via `next/font/google`, which
// re-fetches from fonts.googleapis.com on every cold compile and silently falls
// back to system fonts when the machine is offline. See app/fonts/README.md.
const bricolage = localFont({
  src: "./fonts/BricolageGrotesque-latin-var.woff2",
  display: "swap",
  variable: "--font-display",
  // opsz axis: 12..96 — the browser picks the right optical size on its own
  weight: "200 800",
});

const hanken = localFont({
  src: "./fonts/HankenGrotesk-latin-var.woff2",
  display: "swap",
  variable: "--font-sans",
  weight: "100 900",
});

const jetbrainsMono = localFont({
  src: "./fonts/JetBrainsMono-latin-var.woff2",
  display: "swap",
  variable: "--font-mono",
  weight: "100 800",
});

export const metadata: Metadata = {
  title: {
    default: "SDLC Platform",
    template: "%s · SDLC Platform",
  },
  description:
    "Agentic SDLC platform — AI agents across requirements, design, development, code review, security, testing, deployment, and documentation.",
  applicationName: "SDLC Platform",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0a0a" },
  ],
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${bricolage.variable} ${hanken.variable} ${jetbrainsMono.variable} font-sans`}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
