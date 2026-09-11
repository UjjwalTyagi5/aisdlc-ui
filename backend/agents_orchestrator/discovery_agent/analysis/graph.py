"""The dependency graph: which modules depend on which, and on what outside packages.

Two kinds of edge, kept apart because they mean different things to a migration:

  project   module → module inside this repository. These decide ORDER — a module
            cannot move before the ones it depends on are reachable from the target —
            and FAN-IN (how many modules break if this one changes) feeds the risk
            score.
  package   module → external package. These decide EFFORT — each one needs a
            target-stack equivalent — and carry the EOL/deprecated/vulnerable flags.

Internal references come from explicit project references (.NET `ProjectReference`)
and from a declared dependency whose name is another module in the same repository
(Maven artifactIds, npm workspace packages).
"""
from __future__ import annotations

from agents_orchestrator.discovery_agent.analysis.inventory import ModuleFacts
from agents_orchestrator.discovery_agent.analysis.manifests import ManifestFacts


def module_node(name: str) -> str:
    return f"module:{name}"


def package_node(ecosystem: str, name: str) -> str:
    return f"package:{ecosystem}:{name}"


def internal_references(
    modules: list[ModuleFacts], manifests: dict[str, ManifestFacts]
) -> dict[str, list[str]]:
    """module name → names of the modules it depends on, within this repository."""
    by_path = {m.path: m.name for m in modules}
    by_short_name = {m.name.split(":")[-1].lower(): m.name for m in modules}
    refs: dict[str, list[str]] = {m.name: [] for m in modules}
    for module in modules:
        facts = manifests.get(module.name) or ManifestFacts()
        found: list[str] = []
        for path in facts.project_references:
            target = by_path.get(path)
            if target and target != module.name and target not in found:
                found.append(target)
        for dep in facts.dependencies:
            if dep.kind != "package":
                continue
            short = dep.name.split(":")[-1].lower()
            target = by_short_name.get(short)
            if target and target != module.name and target not in found:
                found.append(target)
        refs[module.name] = found
    return refs


def fan_in(modules: list[ModuleFacts], manifests: dict[str, ManifestFacts]) -> dict[str, int]:
    """module name → how many OTHER modules in the repository depend on it."""
    counts = {m.name: 0 for m in modules}
    for targets in internal_references(modules, manifests).values():
        for target in targets:
            counts[target] = counts.get(target, 0) + 1
    return counts


def build_dependency_graph(
    modules: list[ModuleFacts], manifests: dict[str, ManifestFacts]
) -> dict:
    """`{"nodes": [...], "edges": [...]}` — the shape the Discovery page renders."""
    internal = internal_references(modules, manifests)
    internal_names = {m.name.split(":")[-1].lower() for m in modules}
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for module in modules:
        nodes[module_node(module.name)] = {
            "id": module_node(module.name), "type": "module", "label": module.name,
            "ecosystem": module.ecosystem,
        }
    for module in modules:
        for target in internal[module.name]:
            edges.append({"from": module_node(module.name), "to": module_node(target), "type": "project"})
        facts = manifests.get(module.name) or ManifestFacts()
        for dep in facts.dependencies:
            if dep.kind != "package" or dep.name.split(":")[-1].lower() in internal_names:
                continue
            node_id = package_node(module.ecosystem, dep.name)
            nodes.setdefault(node_id, {
                "id": node_id, "type": "package", "label": dep.name, "ecosystem": module.ecosystem,
            })
            edges.append({"from": module_node(module.name), "to": node_id, "type": "package",
                          "version": dep.version})
    return {"nodes": list(nodes.values()), "edges": edges}
