import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Don Regalo CRM",
  description: "Inbox WhatsApp — asesores Don Regalo",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
