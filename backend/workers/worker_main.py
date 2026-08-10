"""CLI entrypoint for running a single agent worker process.

Usage:
    python -m workers.worker_main --agent requirements

Each invocation runs one worker process for one agent type. The worker class is
imported lazily by string so a broken agent graph in one module does not prevent
launching workers for the other agents.
"""
import argparse
import asyncio
import importlib
import os

_WORKERS = {
    "requirements": "workers.requirements_worker.RequirementsWorker",
    "design": "workers.design_worker.DesignWorker",
    "development": "workers.development_worker.DevelopmentWorker",
}


def _load_worker_class(agent_type: str):
    module_path, class_name = _WORKERS[agent_type].rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an agent worker process.")
    parser.add_argument(
        "--agent",
        required=True,
        choices=list(_WORKERS.keys()),
        help="Agent type whose tasks:{agent} stream this worker consumes.",
    )
    args = parser.parse_args()

    worker_cls = _load_worker_class(args.agent)
    consumer_name = f"{args.agent}-worker-{os.getpid()}"
    worker = worker_cls(consumer_name=consumer_name)
    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
