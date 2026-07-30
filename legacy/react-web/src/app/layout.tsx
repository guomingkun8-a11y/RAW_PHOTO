import type { Metadata, Viewport } from "next";
import { Toaster } from "sonner";

import { ThemeScript } from "@/components/theme-script";
import { TopNav } from "@/components/top-nav";

import "./globals.css";

export const metadata: Metadata = {
  title: "AI Image Studio",
  description: "AI product image generation workspace",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F8FAFC" },
    { media: "(prefers-color-scheme: dark)", color: "#0f1115" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <ThemeScript />
      </head>
      <body
        className="antialiased"
        style={{
          fontFamily:
            '"Inter","PingFang SC","HarmonyOS Sans","SF Pro Display","SF Pro Text","Microsoft YaHei","Helvetica Neue",Arial,sans-serif',
        }}
      >
        <Toaster position="top-center" richColors offset={48} />
        <main className="min-h-screen overflow-x-hidden bg-[#F8FAFC] text-slate-900 transition-colors duration-200 dark:bg-[#0f1115] dark:text-stone-100">
          <div className="box-border min-h-screen pt-[env(safe-area-inset-top)]">
            <TopNav>{children}</TopNav>
          </div>
        </main>
      </body>
    </html>
  );
}
