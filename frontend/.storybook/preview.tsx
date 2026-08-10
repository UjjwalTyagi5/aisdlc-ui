import type { Preview } from "@storybook/react";
import { withThemeByClassName } from "@storybook/addon-themes";
import * as React from "react";

import { TooltipProvider } from "../components/ui/tooltip";

import "../app/globals.css";

const preview: Preview = {
  parameters: {
    controls: {
      matchers: { color: /(background|color)$/i, date: /Date$/i },
    },
    a11y: { disable: false },
    backgrounds: { disable: true }, // theme decorator handles bg
    layout: "centered",
  },
  decorators: [
    withThemeByClassName({
      themes: { light: "light", dark: "dark" },
      defaultTheme: "light",
    }),
    (Story) => (
      <div className="bg-background text-foreground font-sans">
        <TooltipProvider>
          <Story />
        </TooltipProvider>
      </div>
    ),
  ],
};

export default preview;
