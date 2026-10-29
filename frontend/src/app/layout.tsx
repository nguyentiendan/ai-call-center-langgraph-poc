import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Sakura Mobile サポート", description: "AI Call Center PoC" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}
