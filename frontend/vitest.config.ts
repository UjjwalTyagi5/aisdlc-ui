import { defineConfig } from "vitest/config";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [tsconfigPaths()],
  // AUTOMATIC, matching how Next builds the app. tsconfig says `jsx: "preserve"`
  // because Next owns that transform in the real build; esbuild sees no override and
  // falls back to the CLASSIC runtime, which compiles JSX to `React.createElement`
  // and therefore needs `React` in scope in every file.
  //
  // Most components do not import it — they have not needed to since React 17 — so a
  // test only failed once it rendered one of them, with "React is not defined"
  // pointing at a UI primitive nobody had touched. Rendering `LoadingState` was
  // enough, via `ui/skeleton.tsx`.
  esbuild: { jsx: "automatic" },
  test: {
    environment: "node",
    include: ["**/__tests__/**/*.test.ts", "**/__tests__/**/*.test.tsx"],
    exclude: ["node_modules", ".next", "e2e"],
  },
});
