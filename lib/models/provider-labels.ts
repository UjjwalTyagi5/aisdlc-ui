/**
 * Display names for the provider slugs the catalogue ships with.
 *
 * A slug is what LiteLLM routes on ("bedrock", "xai"); a label is what a person
 * reads ("AWS Bedrock", "xAI — Grok"). Onboarding is open — any slug is
 * onboardable — so this is a courtesy table, never a gate: an unknown slug
 * falls through to itself rather than disappearing.
 *
 * One table, shared, because the list page and the provider detail page have to
 * name the same provider identically — two copies drifted apart the moment a
 * fourth provider was added to one of them.
 */
export const PROVIDER_LABEL: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
  azure: "Azure OpenAI",
  bedrock: "AWS Bedrock",
  vertex_ai: "Google Vertex AI",
  xai: "xAI (Grok)",
  mistral: "Mistral",
  cohere: "Cohere",
};

export function providerLabel(kind: string): string {
  return PROVIDER_LABEL[kind] ?? kind;
}
