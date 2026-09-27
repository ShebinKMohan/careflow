import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

// next-intl without locale routing: the locale comes from the NEXT_LOCALE cookie (src/i18n/request.ts).
const withNextIntl = createNextIntlPlugin();

const nextConfig: NextConfig = {
  transpilePackages: ["@careflow/ui", "@careflow/contracts", "@careflow/laya-ts"],
  poweredByHeader: false,
  // No generated AGENTS.md/CLAUDE.md: agent rules live in docs/AGENT_RUNBOOK.md.
  agentRules: false,
  // Cross-origin isolation (M3-02): onnxruntime-web runs the kiosk urgency model on several WASM
  // threads only when SharedArrayBuffer is available. Every kiosk asset is same-origin.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          { key: "Cross-Origin-Embedder-Policy", value: "require-corp" },
        ],
      },
    ];
  },
};

export default withNextIntl(nextConfig);
