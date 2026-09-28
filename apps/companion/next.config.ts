import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

// next-intl without locale routing: the locale comes from the NEXT_LOCALE cookie (src/i18n/request.ts).
const withNextIntl = createNextIntlPlugin();

const nextConfig: NextConfig = {
  transpilePackages: ["@careflow/ui", "@careflow/contracts"],
  poweredByHeader: false,
  // A real phone reaches the dev server at the computer's address (CAREFLOW_PUBLIC_HOST, M2-06).
  allowedDevOrigins: process.env.CAREFLOW_PUBLIC_HOST ? [process.env.CAREFLOW_PUBLIC_HOST] : [],
  // The phone reaches the API through the same LAN-visible Next server; device credentials
  // and authentication still terminate at the API. Local-only and mock builds stay unchanged.
  ...(process.env.CAREFLOW_PUBLIC_HOST && process.env.CAREFLOW_API_URL ? {
    async rewrites() {
      return [{ source: "/v1/:path*", destination: `${process.env.CAREFLOW_API_URL}/v1/:path*` }];
    },
  } : {}),
  // No generated AGENTS.md/CLAUDE.md: agent rules live in docs/AGENT_RUNBOOK.md.
  agentRules: false,
};

export default withNextIntl(nextConfig);
