import type { Config } from "tailwindcss";
import { chmabaTailwindPreset, chmabaContentGlobs } from "../shared/tailwind-preset";

/**
 * The user-facing landing + portal surface. The design tokens live in one place
 * (`web/shared/tailwind-preset.ts`, sourced from `web/shared/theme.ts`) so this
 * app and `web/admin` cannot drift apart — see `web/DESIGN_TOKENS_AND_UX_LAWS.md`.
 */
const config: Config = {
  presets: [chmabaTailwindPreset as unknown as Config],
  content: chmabaContentGlobs,
};

export default config;
