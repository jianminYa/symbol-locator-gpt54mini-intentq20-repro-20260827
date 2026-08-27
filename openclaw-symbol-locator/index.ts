// Symbol Locator — OpenClaw plugin entrypoint.
import { definePluginEntry } from "./api.js";
import { symbolLocatorPluginConfigSchema } from "./src/config.js";
import { registerSymbolLocatorPlugin } from "./src/plugin.js";

export default definePluginEntry({
  id: "symbol-locator",
  name: "Symbol Locator",
  description:
    "Precise Python symbol location for agents — LSP-powered workspace indexing with LLM-based disambiguation.",
  configSchema: symbolLocatorPluginConfigSchema,
  register: registerSymbolLocatorPlugin,
});
