"""Container backend for executing workflow steps (podman preferred, docker fallback)."""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Mapping from GitHub Actions runs-on labels to container images
RUNS_ON_IMAGES: dict[str, str] = {
    "ubuntu-latest": "ubuntu:24.04",
    "ubuntu-24.04": "ubuntu:24.04",
    "ubuntu-22.04": "ubuntu:22.04",
    "ubuntu-20.04": "ubuntu:20.04",
}

LOCAL_OS_LABELS: dict[str, list[str]] = {
    "Linux": ["ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04", "ubuntu-20.04"],
    "Darwin": ["macos-latest", "macos-14", "macos-13"],
    "Windows": ["windows-latest", "windows-2022", "windows-2019"],
}


@dataclass
class ContainerRuntime:
    """Detected container runtime (podman or docker)."""

    executable: str
    name: str

    def run(
        self,
        image: str,
        script: str,
        *,
        cwd: Path,
        workdir: str = "/workspace",
        env: dict[str, str] | None = None,
        shell: str = "bash",
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run a script inside a container."""
        args = [
            self.executable, "run", "--rm",
            "-v", f"{cwd}:{workdir}",
            "-w", workdir,
        ]

        for key, val in (env or {}).items():
            args.extend(["-e", f"{key}={val}"])

        args.extend([image, shell, "-eo", "pipefail", "-c", script])

        return subprocess.run(
            args,
            capture_output=capture_output,
            text=True,
        )

    def image_exists(self, image: str) -> bool:
        """Check if an image is already pulled."""
        result = subprocess.run(
            [self.executable, "image", "exists", image],
            capture_output=True,
        )
        return result.returncode == 0

    def pull(self, image: str, quiet: bool = True) -> bool:
        """Pull an image. Returns True on success."""
        args = [self.executable, "pull"]
        if quiet:
            args.append("-q")
        args.append(image)
        result = subprocess.run(args, capture_output=quiet, text=True)
        return result.returncode == 0


def detect_runtime() -> ContainerRuntime | None:
    """Detect available container runtime, preferring podman."""
    for name in ("podman", "docker"):
        exe = shutil.which(name)
        if exe:
            result = subprocess.run(
                [exe, "version"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return ContainerRuntime(executable=exe, name=name)
    return None


def local_os_matches(runs_on: str) -> bool:
    """Check if the local OS is compatible with the runs-on label."""
    system = platform.system()
    compatible = LOCAL_OS_LABELS.get(system, [])
    return runs_on in compatible


def resolve_image(runs_on: str) -> str | None:
    """Resolve a runs-on label to a container image."""
    return RUNS_ON_IMAGES.get(runs_on)


@dataclass
class ContainerSession:
    """A reusable container for running multiple steps in sequence.

    Uses a long-lived container instead of creating a new one per step,
    preserving filesystem state between steps (like CI does).
    """

    runtime: ContainerRuntime
    image: str
    container_id: str
    workdir: str = "/workspace"

    @classmethod
    def start(
        cls,
        runtime: ContainerRuntime,
        image: str,
        *,
        mount: Path,
        workdir: str = "/workspace",
        env: dict[str, str] | None = None,
        verbose: bool = False,
    ) -> ContainerSession:
        """Start a long-lived container for the job."""
        args = [
            runtime.executable, "run", "-d",
            "--rm",
            "-v", f"{mount}:{workdir}",
            "-w", workdir,
        ]
        for key, val in (env or {}).items():
            args.extend(["-e", f"{key}={val}"])

        args.extend([image, "sleep", "infinity"])

        if verbose:
            print(f"  → starting container: {image} ({runtime.name})")

        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start container: {result.stderr.strip()}")

        container_id = result.stdout.strip()
        return cls(
            runtime=runtime,
            image=image,
            container_id=container_id,
            workdir=workdir,
        )

    def exec(
        self,
        script: str,
        *,
        shell: str = "bash",
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess:
        """Execute a command inside the running container."""
        args = [self.runtime.executable, "exec"]

        wd = workdir or self.workdir
        args.extend(["-w", wd])

        for key, val in (env or {}).items():
            args.extend(["-e", f"{key}={val}"])

        if shell == "bash":
            args.extend([self.container_id, "bash", "-eo", "pipefail", "-c", script])
        elif shell == "sh":
            args.extend([self.container_id, "sh", "-e", "-c", script])
        elif shell == "python":
            args.extend([self.container_id, "python3", "-c", script])
        else:
            args.extend([self.container_id, "sh", "-e", "-c", script])

        return subprocess.run(args, capture_output=capture_output, text=True)

    def stop(self):
        """Stop and remove the container."""
        subprocess.run(
            [self.runtime.executable, "stop", self.container_id],
            capture_output=True,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop()
