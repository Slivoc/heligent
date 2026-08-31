import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host") ?? "localhost:3000";
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const metadataBase = new URL(`${protocol}://${host}`);
  const title = "Heligent - Ask the aviation data";
  const description = "Ask plain-English questions about local ADS-B hub traffic, tail activity hours, and aircraft types.";
  return {
    metadataBase,
    title,
    description,
    icons: { icon: "/favicon.svg" },
    openGraph: {
      title,
      description,
      type: "website",
      images: [{ url: "/og-phase5.png", width: 1731, height: 909, alt: "Heligent - Ask the aviation data" }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: ["/og-phase5.png"],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
