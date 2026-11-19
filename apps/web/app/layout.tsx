import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "PROJECT APEX", template: "%s · PROJECT APEX" },
  description: "PROJECT APEX - Futures Market Intelligence (analysis only).",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#060a12",
  colorScheme: "dark",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
