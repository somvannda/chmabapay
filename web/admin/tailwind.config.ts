import type { Config } from "tailwindcss";
import { chmabaTailwindPreset, chmabaContentGlobs } from "../shared/tailwind-preset";

/**
 * The platform admin console. Same preset as `web/landing` on purpose: the
 * console is the same product and the same Chmaba brand, so it consumes the one
 * shared style template rather than its own copy of it.
 */
const config: Config = {
  presets: [chmabaTailwindPreset as unknown as Config],
  content: chmabaContentGlobs,
};

export default config;
