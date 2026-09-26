import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

// next-intl without locale routing: the locale comes from the NEXT_LOCALE cookie (src/i18n/request.ts).
const withNextIntl = createNextIntlPlugin();

const nextConfig: NextConfig = {
  transpilePackages: ["@careflow/ui", "@careflow/contracts"],
  poweredByHeader: false,
  // No generated AGENTS.md/CLAUDE.md: agent rules live in docs/AGENT_RUNBOOK.md.
  agentRules: false,
};

export default withNextIntl(nextConfig);
