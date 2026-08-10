import type { Meta, StoryObj } from "@storybook/react";

import { CostMeter } from "@/components/app/cost-meter";
import { GuardrailBanner } from "@/components/app/guardrail-banner";

const meta: Meta = { title: "App/Cost & Guardrails", tags: ["autodocs"] };
export default meta;

export const CostBasic: StoryObj = {
  name: "CostMeter — basic",
  render: () => (
    <div className="w-72">
      <CostMeter cost={{ usd: 0.47, inputTokens: 94_000, outputTokens: 12_400 }} />
    </div>
  ),
};

export const CostWithSeries: StoryObj = {
  name: "CostMeter — sparkline",
  render: () => (
    <div className="w-72">
      <CostMeter
        cost={{ usd: 3.81, inputTokens: 740_000, outputTokens: 98_000 }}
        series={[0.4, 0.7, 1.2, 1.1, 1.9, 2.4, 3.81]}
      />
    </div>
  ),
};

export const CostWithBudget: StoryObj = {
  name: "CostMeter — budget 92%",
  render: () => (
    <div className="w-72">
      <CostMeter
        cost={{ usd: 9.2, inputTokens: 1_640_000, outputTokens: 220_000 }}
        series={[2.1, 3.6, 5.2, 7.3, 8.5, 9.2]}
        budgetUsd={10}
      />
    </div>
  ),
};

export const GuardrailVariants: StoryObj = {
  name: "GuardrailBanner — all kinds",
  render: () => (
    <div className="flex w-[520px] flex-col gap-3">
      <GuardrailBanner
        kind="pii"
        message="An email address was detected in the Requirements output. It was redacted before write-back."
        onReview={() => {}}
      />
      <GuardrailBanner
        kind="prompt_injection"
        message="The Jira issue description contained an instruction that attempted to override the system prompt. The run was stopped."
        blocked
        onReview={() => {}}
      />
      <GuardrailBanner
        kind="policy"
        message="Writing to the `main` branch is blocked by branch-protection policy."
        blocked
      />
      <GuardrailBanner
        kind="budget"
        message="This run is at 92% of its $10 cap. Runs over budget are auto-paused."
        onReview={() => {}}
        onDismiss={() => {}}
      />
    </div>
  ),
};
