import type { StorybookConfig } from "@storybook/react-vite";

const config: StorybookConfig = {
  stories: ["../stories/**/*.stories.@(ts|tsx)"],
  addons: [
    "@storybook/addon-essentials",
    "@storybook/addon-interactions",
    "@storybook/addon-a11y",
    "@storybook/addon-themes",
    "@chromatic-com/storybook",
  ],
  framework: {
    name: "@storybook/react-vite",
    options: {},
  },
  typescript: {
    check: false,
    reactDocgen: "react-docgen-typescript",
    reactDocgenTypescriptOptions: {
      shouldExtractLiteralValuesFromEnum: true,
      propFilter: (prop) =>
        prop.parent ? !/node_modules/.test(prop.parent.fileName) : true,
    },
  },
  docs: {},
  staticDirs: ["../public"],
  viteFinal: async (config) => {
    const { default: tsconfigPaths } = await import("vite-tsconfig-paths");
    const tailwindcss = (await import("@tailwindcss/vite")).default;
    config.plugins = [...(config.plugins ?? []), tsconfigPaths(), tailwindcss()];
    return config;
  },
};

export default config;
