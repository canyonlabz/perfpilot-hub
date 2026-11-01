import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { THEME_CONFIG } from "@/lib/theme-config";
import { THEME_APPLIER_SOURCE } from "@/lib/theme-applier";
import { ThemeRuntime } from "@/components/theme/theme-runtime";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "PerfPilot Agent Framework",
  description: "AI-powered multi-agent performance testing platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <head>
        <script
          id="theme-config"
          type="application/json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(THEME_CONFIG) }}
        />
        <script dangerouslySetInnerHTML={{ __html: THEME_APPLIER_SOURCE }} />
      </head>
      <body className={inter.className}>
        <ThemeRuntime />
        {children}
      </body>
    </html>
  );
}
