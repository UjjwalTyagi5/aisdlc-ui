import { redirect } from "next/navigation";

// MCP server management now lives on the Integrations page alongside
// connectors — this route stayed a bare duplicate of the same panel with no
// separate nav entry pointing at it. Redirect any existing links/bookmarks.
export default function McpServersPage() {
  redirect("/integrations");
}
