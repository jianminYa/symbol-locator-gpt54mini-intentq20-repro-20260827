import { defineConfig } from "vitest/config";

const isIntegration = process.env.TEST_INTEGRATION === "1";

export default defineConfig({
  test: {
    include: isIntegration
      ? ["test/*.integration.test.ts"]
      : ["test/**/*.test.ts"],
    exclude: [
      ...(isIntegration ? [] : ["test/*.integration.test.ts"]),
      "test/spike-*.ts",
      "node_modules",
    ],
    pool: "forks",
    isolate: true,
  },
});
