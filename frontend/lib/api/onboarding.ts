/**
 * Org- and Business-Unit-scope onboarding client. Project scope uses
 * `lib/api/project-members.ts::addProjectMember` instead.
 */
import { OnboardingResult, type OnboardingRequest } from "@/lib/schemas/onboarding";

import { api } from "./client";

export const onboardPerson = (input: OnboardingRequest) =>
  api("/onboarding", { method: "POST", body: input, schema: OnboardingResult });
