import type { MetadataRoute } from "next";

/** Installable PWA manifest (served at /manifest.webmanifest). */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "CareFlow companion",
    short_name: "CareFlow",
    description: "Your visit on your phone after the kiosk hand-over. Research prototype.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#f3f5f7",
    theme_color: "#0a4f94",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
    ],
  };
}
