"""Shared display helpers for CLI output formatting."""

from __future__ import annotations


def print_workflows(workflows: dict) -> None:
    """Print workflow info in a standard format (shared by GitHub/Forgejo listers)."""
    for name, wf in workflows.items():
        print(f"  {name}")
        print(f"    file: {wf.path.name}")

        triggers = list(wf.on.keys()) if isinstance(wf.on, dict) else wf.on
        if triggers:
            if isinstance(triggers, list):
                print(f"    triggers: {', '.join(triggers)}")
            else:
                print(f"    triggers: {triggers}")

        dispatch_inputs = wf.dispatch_inputs
        if dispatch_inputs:
            print("    inputs:")
            for inp_name, inp in dispatch_inputs.items():
                req = " (required)" if inp.required else ""
                default = f" [default: {inp.default}]" if inp.default else ""
                print(f"      {inp_name}{req}{default}")

        for job_id, job in wf.jobs.items():
            step_count = len(job.steps)
            needs = f" (needs: {', '.join(job.needs)})" if job.needs else ""
            matrix_info = ""
            if job.strategy and job.strategy.matrix:
                keys = list(job.strategy.matrix.keys())
                combos = job.strategy.expand()
                matrix_info = f" [{len(combos)} combinations: {', '.join(keys)}]"
                for combo in combos:
                    label = ", ".join(f"{k}={v}" for k, v in combo.items())
                    print(f"      - {label}")
            print(f"    • {job.display_name} ({step_count} steps){needs}{matrix_info}")
        print()


def print_gitlab_pipeline(pipeline) -> None:
    """Print GitLab pipeline info."""
    print(f"  Pipeline: {pipeline.name}")
    print(f"  Stages: {' → '.join(pipeline.active_stages())}")

    if pipeline.variables:
        shown = list(pipeline.variables.items())[:5]
        print(f"  Variables: {len(pipeline.variables)}")
        for k, v in shown:
            print(f"    {k} = {v}")
        if len(pipeline.variables) > 5:
            print(f"    ... and {len(pipeline.variables) - 5} more")

    print()
    for stage in pipeline.active_stages():
        jobs = pipeline.jobs_for_stage(stage)
        print(f"  Stage: {stage}")
        for job in jobs:
            flags = []
            if job.allow_failure:
                flags.append("allow_failure")
            if job.when == "manual":
                flags.append("manual")
            if job.image:
                flags.append(f"image: {job.image}")
            if job.services:
                flags.append(f"{len(job.services)} services")
            if job.needs_explicit and job.needs:
                flags.append(f"needs: {', '.join(job.needs)}")
            if job.artifacts:
                flags.append(f"artifacts: {len(job.artifacts.paths)} paths")
            if job.cache:
                flags.append(f"cache: {job.cache.key or 'default'}")
            if job.rules:
                flags.append(f"{len(job.rules)} rules")
            if job.retry:
                flags.append(f"retry: {job.retry}")
            cmd_count = len(job.script)
            flag_str = f" ({', '.join(flags)})" if flags else ""
            print(f"    • {job.name} [{cmd_count} commands]{flag_str}")
        print()
