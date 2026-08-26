"""SWE-bench Pro benchmark adapter."""

import sys
import json
import shutil
import argparse
from pathlib import Path
from collections import defaultdict

_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from benchmarks.adapter import BenchmarkAdapter, EvalResult, EnvContext
from tools import docker_bash



DOCKER_WORKDIR = "/app"
DOCKERHUB_USERNAME = "jefzda"

PYTHON_REPOS = {
    "ansible/ansible",
    "internetarchive/openlibrary",
    "qutebrowser/qutebrowser",
}

SPLIT_MAP = {
    "all": None,
    "ansible": {"ansible/ansible"},
    "openlibrary": {"internetarchive/openlibrary"},
    "qutebrowser": {"qutebrowser/qutebrowser"},
}

DATA_DIR = _BASE_DIR / "benchmarks" / "SWE-bench-Pro"
DATA_FILE = DATA_DIR / "helper_code" / "sweap_eval_full_v2.jsonl"
RUN_SCRIPTS_DIR = DATA_DIR / "run_scripts"
DOCKERFILES_DIR = DATA_DIR / "dockerfiles"

AUGMENTATION_CACHE = DATA_DIR / "helper_code" / "augmentation_cache.json"



def get_docker_image(instance_id: str, repo_name: str) -> str:
    repo_base, repo_name_only = repo_name.lower().split("/")
    hsh = instance_id.replace("instance_", "")

    if hsh.endswith("-vnan"):
        hsh = hsh[:-5]

    tag = f"{repo_base}.{repo_name_only}-{hsh}"
    if len(tag) > 128:
        tag = tag[:128]

    return f"{DOCKERHUB_USERNAME}/sweap-images:{tag}"



_REQ_MARKER = "\n\nRequirements:\n"
_IFACE_MARKER = "\n\nNew interfaces introduced:\n"


def _strip_augmentation(problem_statement: str) -> str:
    req_idx = problem_statement.find(_REQ_MARKER)
    if req_idx >= 0:
        return problem_statement[:req_idx]
    return problem_statement


def _load_augmentation_map() -> dict[str, dict[str, str]]:
    if not AUGMENTATION_CACHE.exists():
        raise FileNotFoundError(
            f"Augmentation cache not found: {AUGMENTATION_CACHE}\n"
            f"Run the following to generate:\n"
            f"  python3 -c \"\n"
            f"from datasets import load_dataset; import json\n"
            f"ds = load_dataset('ScaleAI/SWE-bench_Pro', split='test')\n"
            f"aug = {{r['instance_id']: {{'requirements': r.get('requirements',''), 'interface': r.get('interface','')}} for r in ds}}\n"
            f"json.dump(aug, open('{AUGMENTATION_CACHE}','w'), ensure_ascii=False, indent=1)\n"
            f"print(f'Done: {{len(aug)}} entries')\n"
            f"\""
        )

    with open(AUGMENTATION_CACHE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_swebench_pro_data(
    split: str = "all",
    max_items: int | None = None,
    augment: bool = False,
) -> list[dict]:
    if not DATA_FILE.exists():
        raise FileNotFoundError(
            f"SWE-bench Pro data file not found: {DATA_FILE}\n"
            f"Ensure benchmarks/SWE-bench-Pro/ directory is complete."
        )

    repo_filter = SPLIT_MAP.get(split)
    if repo_filter is None:
        repo_filter = PYTHON_REPOS

    items = []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            repo = item.get("repo", "")
            if repo in repo_filter:
                items.append(item)

    if max_items:
        items = items[:max_items]

    if not augment:
        stripped = 0
        for item in items:
            ps = item.get("problem_statement", "")
            clean_ps = _strip_augmentation(ps)
            if clean_ps != ps:
                item["problem_statement"] = clean_ps
                stripped += 1
        if stripped:
            print(f"  [augment=off] stripped augmentation from {stripped}/{len(items)} items")
    else:
        print(f"  [augment=on] keeping pre-joined augmentation ({len(items)} items)")

    return items



def _build_image_locally(instance_id: str) -> str:
    import docker
    client = docker.from_env(timeout=600)

    base_dir = DOCKERFILES_DIR / "base_dockerfile" / instance_id
    inst_dir = DOCKERFILES_DIR / "instance_dockerfile" / instance_id

    if not base_dir.exists() or not inst_dir.exists():
        raise FileNotFoundError(
            f"Dockerfiles not found for {instance_id}")

    with open(inst_dir / "Dockerfile", "r") as f:
        for line in f:
            if line.strip().startswith("FROM"):
                base_image_name = line.strip().split()[1]
                break
        else:
            raise ValueError(f"No FROM in instance Dockerfile for {instance_id}")

    try:
        client.images.get(base_image_name)
        print(f"  Base image exists: {base_image_name}")
    except docker.errors.ImageNotFound:
        print(f"  Building base image: {base_image_name}...")
        client.images.build(
            path=str(base_dir),
            tag=base_image_name,
            rm=True,
        )

    local_tag = f"swebench-pro-local/{instance_id}:latest"
    try:
        client.images.get(local_tag)
        print(f"  Instance image exists: {local_tag[:60]}...")
    except docker.errors.ImageNotFound:
        print(f"  Building instance image: {local_tag[:60]}...")
        client.images.build(
            path=str(inst_dir),
            tag=local_tag,
            rm=True,
        )

    return local_tag



def _ensure_image(instance_id: str, repo_name: str) -> str:
    import subprocess as _sp
    image = get_docker_image(instance_id, repo_name)

    ret = _sp.run(
        ["docker", "image", "inspect", image],
        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
    )
    if ret.returncode == 0:
        return image

    print(f"  Pulling Docker image: {image[-80:]}...")
    ret = _sp.run(
        ["docker", "pull", image],
        capture_output=True, text=True, timeout=600,
    )
    if ret.returncode == 0:
        return image

    print(f"  Image not on DockerHub, building locally...")
    return _build_image_locally(instance_id)


def create_pro_container(
    container_name: str,
    instance_id: str,
    repo_name: str,
    workspace: str,
):
    import subprocess as _sp
    import time

    image = _ensure_image(instance_id, repo_name)

    max_retries = 3
    for attempt in range(max_retries):
        _sp.run(["docker", "rm", "-f", container_name],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)

        cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-w", DOCKER_WORKDIR,
            "-v", f"{workspace}:/workspace:rw",
            "--user", "root",
            "--entrypoint", "",
            image,
            "sleep", "86400",
        ]
        try:
            result = _sp.run(
                cmd, capture_output=True, text=True,
                timeout=300, check=True,
            )
            container_id = result.stdout.strip()
            return ContainerHandle(container_id, container_name)
        except (_sp.CalledProcessError, _sp.TimeoutExpired) as e:
            if attempt < max_retries - 1:
                wait = 3 * (attempt + 1)
                err_msg = str(e)[:120]
                print(f"  [retry {attempt+1}/{max_retries}] "
                      f"{e.__class__.__name__}: {err_msg}, "
                      f"retrying in {wait}s...")
                _sp.run(["docker", "rm", "-f", container_name],
                        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                time.sleep(wait)
            else:
                raise


class ContainerHandle:

    def __init__(self, container_id: str, name: str):
        self.id = container_id
        self.name = name
        self._client_shim = None

    @property
    def short_id(self) -> str:
        return self.id[:12]

    def exec_run(self, cmd, *, workdir=None, demux=False, detach=False, **kwargs):
        import subprocess as _sp

        docker_cmd = ["docker", "exec"]
        if workdir:
            docker_cmd.extend(["-w", workdir])
        if detach:
            docker_cmd.append("-d")
        docker_cmd.append(self.id)
        docker_cmd.extend(cmd)

        if detach:
            _sp.Popen(docker_cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            return (0, (b"", b"")) if demux else (0, b"")

        result = _sp.run(
            docker_cmd,
            capture_output=True,
            timeout=600,
        )
        if demux:
            return (result.returncode, (result.stdout, result.stderr))
        else:
            return (result.returncode, result.stdout + result.stderr)

    def stop(self, timeout=5):
        import subprocess as _sp
        _sp.run(
            ["docker", "stop", "-t", str(timeout), self.id],
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            timeout=timeout + 10,
        )

    def remove(self, force=False):
        import subprocess as _sp
        cmd = ["docker", "rm"]
        if force:
            cmd.append("-f")
        cmd.append(self.id)
        _sp.run(cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=30)

    @property
    def client(self):
        if self._client_shim is None:
            self._client_shim = _ContainerClientShim(self)
        return self._client_shim

    def put_archive(self, path: str, data) -> bool:
        import subprocess as _sp

        if hasattr(data, 'read'):
            tar_bytes = data.read()
        elif isinstance(data, bytes):
            tar_bytes = data
        else:
            tar_bytes = bytes(data)

        try:
            result = _sp.run(
                ["docker", "exec", "-i", self.id,
                 "tar", "xf", "-", "-C", path],
                input=tar_bytes,
                capture_output=True,
                timeout=60,
            )
            return result.returncode == 0
        except _sp.TimeoutExpired:
            return False


class _ContainerClientShim:

    def __init__(self, handle: ContainerHandle):
        self._handle = handle
        self.api = self

    def exec_create(self, container_id, cmd, workdir=None, **kwargs):
        import subprocess as _sp
        import uuid
        exec_id = uuid.uuid4().hex[:12]
        self._pending_exec = {
            "id": exec_id,
            "cmd": cmd,
            "workdir": workdir,
            "container_id": container_id,
        }
        return {"Id": exec_id}

    def exec_start(self, exec_id, stream=False, **kwargs):
        import subprocess as _sp
        info = self._pending_exec
        docker_cmd = ["docker", "exec"]
        if info.get("workdir"):
            docker_cmd.extend(["-w", info["workdir"]])
        docker_cmd.append(info["container_id"])
        docker_cmd.extend(info["cmd"])

        if stream:
            proc = _sp.Popen(
                docker_cmd,
                stdout=_sp.PIPE, stderr=_sp.STDOUT,
            )
            self._current_proc = proc
            return _stream_chunks(proc)
        else:
            result = _sp.run(docker_cmd, capture_output=True, timeout=1800)
            self._last_exit_code = result.returncode
            return result.stdout

    def exec_inspect(self, exec_id):
        exit_code = getattr(self, "_last_exit_code", 0)
        proc = getattr(self, "_current_proc", None)
        if proc is not None:
            proc.wait()
            exit_code = proc.returncode
            self._last_exit_code = exit_code
        return {"ExitCode": exit_code, "Pid": 0}


def _stream_chunks(proc):
    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        yield chunk


def destroy_container(container):
    if container is None:
        return
    import subprocess as _sp
    if isinstance(container, ContainerHandle):
        cmd = f"(timeout 60 docker stop {container.id} || docker rm -f {container.id}) >/dev/null 2>&1 &"
        _sp.Popen(cmd, shell=True)
    else:
        try:
            container.stop(timeout=5)
        except Exception:
            pass
        try:
            container.remove(force=True)
        except Exception:
            pass


def get_patch_from_container(container) -> str:
    try:
        container.exec_run(
            ["git", "add", "-A"],
            workdir=DOCKER_WORKDIR,
        )
        exit_code, output = container.exec_run(
            ["git", "diff", "--cached", "HEAD"],
            workdir=DOCKER_WORKDIR,
            demux=True,
        )
        stdout = output[0].decode(errors="replace") if output[0] else ""
        patch = stdout.rstrip()
        if patch and not patch.endswith("\n"):
            patch += "\n"
        return patch
    except Exception:
        return ""



def _copy_scripts_to_workspace(instance_id: str, workspace: str) -> bool:
    scripts_dir = RUN_SCRIPTS_DIR / instance_id
    if not scripts_dir.exists():
        print(f"  [warn] run_scripts not found: {scripts_dir}")
        return False

    dst = Path(workspace)
    for fname in ("run_script.sh", "parser.py"):
        src = scripts_dir / fname
        if src.exists():
            shutil.copy2(src, dst / fname)
        else:
            print(f"  [warn] missing script: {src}")
            return False

    return True


def _extract_env_exports(instance_id: str) -> str:
    env_cmds = []
    for layer in ("base_dockerfile", "instance_dockerfile"):
        dockerfile = DOCKERFILES_DIR / layer / instance_id / "Dockerfile"
        if not dockerfile.exists():
            continue
        with open(dockerfile, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ENV"):
                    env_cmds.append(line.replace("ENV", "export", 1))
    return "\n".join(env_cmds)


def _make_error_result(patch: str, fail_to_pass: set, pass_to_pass: set,
                       reason: str) -> dict:
    return {
        "resolved": False,
        "patch": patch,
        "reason": reason,
        "f2p_pass": 0,
        "f2p_total": len(fail_to_pass),
        "p2p_pass": 0,
        "p2p_total": len(pass_to_pass),
        "passed_tests": 0,
        "total_tests": 0,
        "f2p_failed_names": sorted(fail_to_pass),
        "p2p_regressed_names": [],
        "failed_details": [],
    }


def evaluate_patch_in_container(
    container,
    instance: dict,
    workspace: str,
    timeout: int = 1800,
) -> dict:
    instance_id = instance.get("instance_id", "")
    base_commit = instance.get("base_commit", "")
    before_cmd = instance.get("before_repo_set_cmd", "").strip()

    f2p_raw = instance.get("FAIL_TO_PASS", [])
    p2p_raw = instance.get("PASS_TO_PASS", [])
    if isinstance(f2p_raw, str):
        f2p_raw = json.loads(f2p_raw)
    if isinstance(p2p_raw, str):
        p2p_raw = json.loads(p2p_raw)
    fail_to_pass = set(f2p_raw)
    pass_to_pass = set(p2p_raw)

    pre_ref_patch_path = Path(workspace) / "patch_pre_reflection.diff"
    if pre_ref_patch_path.exists():
        patch = pre_ref_patch_path.read_text(encoding="utf-8").rstrip()
        if patch and not patch.endswith("\n"):
            patch += "\n"
    else:
        patch = get_patch_from_container(container)
    if not patch:
        return _make_error_result(
            "", fail_to_pass, pass_to_pass,
            f"No changes made for {instance_id}")

    patch_path = Path(workspace) / "patch.diff"
    patch_path.write_text(patch, encoding="utf-8")

    test_files_raw = instance.get("selected_test_files_to_run", "[]")
    if isinstance(test_files_raw, str):
        test_files_raw = json.loads(test_files_raw)
    test_files_csv = ",".join(test_files_raw)

    env_exports = _extract_env_exports(instance_id)

    before_last = before_cmd.split("\n")[-1] if before_cmd else ""

    import re as _re
    if base_commit and not _re.match(r'^[a-fA-F0-9]{7,40}$', base_commit):
        if not _re.match(r'^[a-zA-Z0-9._\-/]+$', base_commit):
            return _make_error_result(
                patch[:5000], fail_to_pass, pass_to_pass,
                f"Invalid base_commit format: {base_commit[:50]}")

    eval_script = f"""#!/bin/bash
{env_exports}

# Clean old eval results to prevent stale data
rm -f /workspace/output.json /workspace/stdout.log /workspace/stderr.log

# Reset to base commit and apply agent patch
cd /app
git reset --hard {base_commit}
git checkout {base_commit}


git apply -v /workspace/patch.diff || echo "[EVAL WARN] git apply had errors, continuing anyway" >&2

# Execute before_repo_set_cmd
{before_last}

bash /workspace/run_script.sh {test_files_csv} > /workspace/stdout.log 2> /workspace/stderr.log || true

# Parse results
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
"""
    eval_script_path = Path(workspace) / "eval_script.sh"
    eval_script_path.write_text(eval_script, encoding="utf-8")

    try:
        import threading as _threading

        _exec_result = b""
        _exec_id = None
        _exec_exception = None

        def _run_eval():
            nonlocal _exec_result, _exec_id, _exec_exception
            try:
                _exec_id = container.client.api.exec_create(
                    container.id,
                    ["bash", "/workspace/eval_script.sh"],
                    workdir=DOCKER_WORKDIR,
                )["Id"]
                stream = container.client.api.exec_start(_exec_id, stream=True)
                for chunk in stream:
                    _exec_result += chunk
            except Exception as exc:
                _exec_exception = exc

        thread = _threading.Thread(target=_run_eval)
        thread.start()
        thread.join(timeout)

        if _exec_exception:
            raise _exec_exception

        timed_out = thread.is_alive()
        if timed_out:
            if _exec_id is not None:
                try:
                    pid = container.client.api.exec_inspect(_exec_id).get("Pid", 0)
                    if pid and pid > 1:
                        container.exec_run(["kill", "-9", str(pid)], detach=True)
                except Exception:
                    pass
            return _make_error_result(
                patch[:5000], fail_to_pass, pass_to_pass,
                f"Evaluation timed out after {timeout}s")

        raw_output = _exec_result.decode(errors="replace")
        exit_code_info = container.client.api.exec_inspect(_exec_id)
        exit_code = exit_code_info.get("ExitCode", -1)
        stdout = raw_output
        stderr = ""

        if exit_code != 0:
            print(f"  [eval] script exited with code {exit_code}")
            if stdout:
                print(f"  [eval] output (last 500): ...{stdout[-500:]}")
            if exit_code == 1:
                return _make_error_result(
                    patch[:5000], fail_to_pass, pass_to_pass,
                    f"Eval script failed (exit {exit_code}): {stdout[-300:]}"
                )
    except Exception as e:
        return _make_error_result(
            patch[:5000], fail_to_pass, pass_to_pass,
            f"Evaluation execution error: {e}")

    output_json_path = Path(workspace) / "output.json"
    if not output_json_path.exists():
        try:
            exit_code, out = container.exec_run(
                ["cat", "/workspace/output.json"],
                demux=True,
            )
            if exit_code == 0 and out[0]:
                output_json_path.write_bytes(out[0])
        except Exception:
            pass

    if not output_json_path.exists():
        return _make_error_result(
            patch[:5000], fail_to_pass, pass_to_pass,
            "output.json not found — tests may have crashed")

    try:
        with open(output_json_path, "r", encoding="utf-8") as f:
            eval_output = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return _make_error_result(
            patch[:5000], fail_to_pass, pass_to_pass,
            f"Failed to parse output.json: {e}")

    all_tests = eval_output.get("tests", [])
    passed_tests = {t["name"] for t in all_tests if t.get("status") == "PASSED"}
    all_required = fail_to_pass | pass_to_pass
    resolved = all_required <= passed_tests and len(fail_to_pass) > 0

    f2p_pass = len(fail_to_pass & passed_tests)
    p2p_pass = len(pass_to_pass & passed_tests)

    f2p_failed = sorted(fail_to_pass - passed_tests)
    p2p_regressed = sorted(pass_to_pass - passed_tests)

    test_status_map = {t["name"]: t.get("status", "UNKNOWN") for t in all_tests}
    failed_details = []
    for tname in f2p_failed[:10]:
        status = test_status_map.get(tname, "NOT_RUN")
        failed_details.append({"name": tname, "status": status, "category": "FAIL_TO_PASS"})
    for tname in p2p_regressed[:5]:
        status = test_status_map.get(tname, "NOT_RUN")
        failed_details.append({"name": tname, "status": status, "category": "PASS_TO_PASS_regression"})

    return {
        "resolved": resolved,
        "patch": patch[:5000],
        "reason": (
            "RESOLVED" if resolved
            else f"F2P: {f2p_pass}/{len(fail_to_pass)}, "
                 f"P2P: {p2p_pass}/{len(pass_to_pass)}"
        ),
        "f2p_pass": f2p_pass,
        "f2p_total": len(fail_to_pass),
        "p2p_pass": p2p_pass,
        "p2p_total": len(pass_to_pass),
        "passed_tests": len(passed_tests),
        "total_tests": len(all_tests),
        "f2p_failed_names": f2p_failed,
        "p2p_regressed_names": p2p_regressed,
        "failed_details": failed_details,
        "test_output": stdout[-3000:] if stdout else "",
    }


def _build_validation_details(result: dict) -> str:
    lines = []
    lines.append(f"FAIL_TO_PASS: {result.get('f2p_pass', 0)}/{result.get('f2p_total', 0)}")
    lines.append(f"PASS_TO_PASS: {result.get('p2p_pass', 0)}/{result.get('p2p_total', 0)}")
    lines.append(f"Total tests: {result.get('passed_tests', 0)} passed / {result.get('total_tests', 0)} total")
    lines.append("")

    f2p_failed = result.get("f2p_failed_names", [])
    if f2p_failed:
        lines.append("STILL FAILING (FAIL_TO_PASS tests that did not pass):")
        for name in f2p_failed[:8]:
            lines.append(f"  - {name}")
        if len(f2p_failed) > 8:
            lines.append(f"  ... and {len(f2p_failed) - 8} more")
        lines.append("")

    p2p_regressed = result.get("p2p_regressed_names", [])
    if p2p_regressed:
        lines.append("REGRESSIONS (PASS_TO_PASS tests that now fail):")
        for name in p2p_regressed[:5]:
            lines.append(f"  - {name}")
        if len(p2p_regressed) > 5:
            lines.append(f"  ... and {len(p2p_regressed) - 5} more")
        lines.append("")

    failed_details = result.get("failed_details", [])
    if failed_details:
        lines.append("FAILED TEST STATUS:")
        for td in failed_details:
            name = td.get("name", "?")
            status = td.get("status", "?")
            category = td.get("category", "?")
            lines.append(f"  {name}: {status} ({category})")

    test_output = result.get("test_output", "")
    if test_output and (f2p_failed or p2p_regressed):
        lines.append("")
        lines.append("TEST OUTPUT (last 500 chars):")
        lines.append(test_output[-500:])

    detail_str = "\n".join(lines)
    return detail_str[:1950]



def _augment_problem_statement(
    problem: str, requirements: str, interface: str,
) -> str:
    augmented = problem
    if requirements:
        augmented += f"\n\nRequirements:\n{requirements}"
    if interface:
        augmented += f"\n\nNew interfaces introduced:\n{interface}"
    return augmented


# ----- Adapter -----

class SWEBenchProAdapter(BenchmarkAdapter):
    benchmark_name = "swebench_pro"
    default_team = "pool_SWE_Pro"
    default_timeout = 1200.0
    default_evolve_timeout = 2400.0
    default_max_cost = 50.0
    default_split = "all"
    results_subdir = "swebench-pro-results"
    split_choices = list(SPLIT_MAP.keys())

    def __init__(self):
        super().__init__()

    def add_extra_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--max-items", type=int, default=None,
            help="Max instances to load")
        parser.add_argument(
            "--repo", type=str, default=None,
            help="Filter by repo (e.g. ansible/ansible)")
        parser.add_argument(
            "--augment", action="store_true", default=True,
            help="Keep pre-joined augmentation data. Default on.")
        parser.add_argument(
            "--no-augment", action="store_true", default=False,
            help="Strip augmentation, use raw problem_statement only.")

    def load_dataset(self, args: argparse.Namespace) -> list[dict]:
        max_items = getattr(args, "max_items", None)
        augment = not getattr(args, "no_augment", False)
        items = load_swebench_pro_data(
            args.split, max_items=max_items, augment=augment)

        repo_filter = getattr(args, "repo", None)
        if repo_filter:
            items = [it for it in items if it.get("repo") == repo_filter]

        return items

    def get_item_id(self, item: dict) -> str:
        return item.get("instance_id", "unknown")

    def build_task(self, item: dict, session, workspace_files: list[str]) -> str:
        problem = item.get("problem_statement", "")
        hints = item.get("hints_text", "")

        task = (
            "<uploaded_files>\n/app\n</uploaded_files>\n"
            "I've uploaded a code repository in the directory /app. "
            "Consider the following PR description:\n\n"
            "<pr_description>\n"
            f"{problem}\n"
        )
        if hints:
            task += f"\nHints:\n{hints}\n"
        task += (
            "</pr_description>\n\n"
            "Can you help me implement the necessary changes to the repository "
            "so that the requirements specified in the <pr_description> are met?\n"
            "I've already taken care of all changes to any of the test files described "
            "in the <pr_description>. This means you DON'T have to modify the testing "
            "logic or any of the tests in any way!\n"
            "Your task is to make the minimal changes to non-tests files in the /app "
            "directory to ensure the <pr_description> is satisfied.\n"
            "Follow these steps to resolve the issue:\n"
            "1. As a first step, it might be a good idea to find and read code relevant "
            "to the <pr_description>\n"
            "2. Create a script to reproduce the error and execute it with "
            "`python <filename.py>` using the bash tool, to confirm the error\n"
            "3. Edit the source code of the repo to resolve the issue\n"
            "4. Rerun your reproduce script and confirm that the error is fixed!\n"
            "5. Think about edgecases and make sure your fix handles them as well\n"
            "Your thinking should be thorough and so it's fine if it's very long."
        )
        return task

    def build_team_selection_task(self, item: dict, full_task: str) -> str:
        return json.dumps({
            "work_request": item.get("problem_statement", ""),
            "verification_hints": item.get("hints_text", ""),
        }, ensure_ascii=False)

    def setup_environment(self, item: dict, session) -> EnvContext:
        instance_id = item.get("instance_id", "")
        repo_name = item.get("repo", "")

        import uuid
        container_name = f"mt-pro-{uuid.uuid4().hex[:8]}"

        container = create_pro_container(
            container_name, instance_id, repo_name, session.workspace)

        from benchmarks.adapter import register_container
        register_container(container_name, container)

        docker_bash.set_container(container, DOCKER_WORKDIR)

        image = get_docker_image(instance_id, repo_name)
        print(f"  Docker container: {container.short_id} "
              f"(image: ...{image[-60:]})")

        scripts_ok = _copy_scripts_to_workspace(
            instance_id, session.workspace)
        if not scripts_ok:
            print(f"  [warn] evaluation scripts missing for {instance_id}")

        container.exec_run(
            ["git", "config", "--global", "--add",
             "safe.directory", DOCKER_WORKDIR],
        )

        env_inject_cmd = (
            "echo '"
            "\nexport PAGER=cat"
            "\nexport GIT_PAGER=cat"
            "\nexport MANPAGER=cat"
            "\nexport LESS=-R"
            "\nexport PIP_PROGRESS_BAR=off"
            "\nexport TQDM_DISABLE=1"
            "' >> /root/.bashrc"
        )
        container.exec_run(["bash", "-c", env_inject_cmd])

        return EnvContext(data={
            "container": container,
            "container_name": container_name,
            "instance_id": instance_id,
            "repo_name": repo_name,
            "scripts_ok": scripts_ok,
        })

    def teardown_environment(self, env_ctx: EnvContext) -> None:
        docker_bash.clear_container()
        container = env_ctx.data.get("container")
        container_name = env_ctx.data.get("container_name", "")
        if container_name:
            from benchmarks.adapter import unregister_container
            unregister_container(container_name)
        destroy_container(container)

    def get_patch_for_snapshot(self, env_ctx) -> str:
        container = env_ctx.data.get("container")
        if container is None:
            return ""
        return get_patch_from_container(container)

    def build_task_validator(self, item: dict, session, **ctx):
        from core.types import TaskValidation

        container = ctx.get("container")
        if container is None:
            return None

        async def _validator(output: str) -> TaskValidation:
            try:
                import asyncio
                result = await asyncio.to_thread(
                    evaluate_patch_in_container,
                    container, item, session.workspace, timeout=600)
                pre_ref_patch = Path(session.workspace) / "patch_pre_reflection.diff"
                patch_file = Path(session.workspace) / "patch.diff"
                if patch_file.exists():
                    import shutil
                    shutil.copy2(str(patch_file), str(pre_ref_patch))
                if result["resolved"]:
                    return TaskValidation(
                        success=True,
                        summary="All target tests pass, no regressions.",
                        details=_build_validation_details(result),
                    )
                else:
                    reason = result.get("reason", "Unknown")
                    return TaskValidation(
                        success=False,
                        summary=f"Not resolved: {reason}",
                        details=_build_validation_details(result),
                    )
            except Exception as e:
                return TaskValidation(
                    success=False,
                    summary=f"Validation error: {e}",
                )

        return _validator

    def evaluate(self, item: dict, predicted: str, session, **ctx) -> EvalResult:
        container = ctx.get("container")
        if container is None:
            return EvalResult(
                success=False, score=0.0,
                summary="No Docker container available for evaluation.",
            )

        try:
            result = evaluate_patch_in_container(
                container, item, session.workspace, timeout=600)
            resolved = result.get("resolved", False)
            f2p_total = result.get("f2p_total", 0)
            f2p_pass = result.get("f2p_pass", 0)
            p2p_total = result.get("p2p_total", 0)
            p2p_pass = result.get("p2p_pass", 0)

            if f2p_total > 0:
                f2p_score = f2p_pass / f2p_total
            else:
                f2p_score = 0.0
            if p2p_total > 0:
                p2p_score = p2p_pass / p2p_total
            else:
                p2p_score = 1.0
            score = f2p_score * 0.7 + p2p_score * 0.3

            return EvalResult(
                success=resolved,
                score=1.0 if resolved else score,
                summary=result.get("reason", "")[:200],
                details=json.dumps({
                    "f2p": f"{f2p_pass}/{f2p_total}",
                    "p2p": f"{p2p_pass}/{p2p_total}",
                    "passed_tests": result.get("passed_tests", 0),
                    "total_tests": result.get("total_tests", 0),
                })[:500],
                extra={
                    "repo": item.get("repo", ""),
                    "patch": result.get("patch", "")[:3000],
                    "resolved": resolved,
                },
            )
        except Exception as e:
            return EvalResult(
                success=False, score=0.0,
                summary=f"Evaluation error: {e}",
            )

    def build_record(self, item, idx, predicted, eval_result, session, error):
        return {
            "idx": idx,
            "instance_id": item.get("instance_id", ""),
            "repo": item.get("repo", ""),
            "resolved": eval_result.extra.get("resolved", False),
            "score": eval_result.score,
            "patch": eval_result.extra.get("patch", "")[:3000],
            "eval_summary": eval_result.summary[:200],
            "eval_details": eval_result.details[:500] if eval_result.details else "",
            "model_output": predicted[:2000],
            "run_error": error,
            "session_id": session.id,
        }

    def compute_summary(self, records, args):
        total = len(records)
        resolved = sum(1 for r in records if r.get("resolved"))
        return {
            "total": total,
            "correct": resolved,
            "avg_score": resolved / total if total else 0.0,
        }

    def print_summary(self, records: list[dict]) -> None:
        total = len(records)
        if total == 0:
            print("No results.")
            return
        resolved = sum(1 for r in records if r["resolved"])
        print(f"\n{'=' * 60}")
        print(f"SWE-bench Pro Results: {resolved}/{total} resolved "
              f"({100 * resolved / total:.1f}%)")
        print(f"{'=' * 60}")

        by_repo = defaultdict(list)
        for r in records:
            by_repo[r["repo"]].append(r["resolved"])
        print("\nBy Repository:")
        for repo in sorted(by_repo):
            vals = by_repo[repo]
            c = sum(vals)
            print(f"  {repo:40s}: {c}/{len(vals)} "
                  f"({100 * c / len(vals):.1f}%)")

        print("\nDetails:")
        for r in records:
            icon = "✅" if r["resolved"] else "❌"
            err = (f" [ERR: {r['run_error'][:30]}]"
                   if r.get("run_error") else "")
            summary = r.get("eval_summary", "")[:50]
            print(f"  {icon} [{r['idx']:3d}] {r['instance_id'][:50]:50s} "
                  f"{summary}{err}")

    def dry_run_print(self, items, args):
        print(f"SWE-bench Pro ({args.split}): {len(items)} items\n")
        by_repo = defaultdict(int)
        for i, item in enumerate(items):
            repo = item.get("repo", "?")
            by_repo[repo] += 1
            if i < 10 or args.cases:
                iid = item.get("instance_id", "?")
                problem = item.get("problem_statement", "")
                first_line = problem.strip().split("\n")[0][:80]
                f2p = item.get("FAIL_TO_PASS", [])
                if isinstance(f2p, str):
                    f2p = json.loads(f2p)
                p2p = item.get("PASS_TO_PASS", [])
                if isinstance(p2p, str):
                    p2p = json.loads(p2p)
                image = get_docker_image(iid, repo)
                print(f"  [{i}] {iid}")
                print(f"      repo: {repo}")
                print(f"      {first_line}...")
                print(f"      F2P: {len(f2p)} tests, P2P: {len(p2p)} tests")
                print(f"      image: {image}")
                print()
        if len(items) > 10 and not args.cases:
            print(f"  ... and {len(items) - 10} more\n")
        print("By repository:")
        for repo, cnt in sorted(by_repo.items(), key=lambda x: -x[1]):
            print(f"  {repo}: {cnt}")


if __name__ == "__main__":
    SWEBenchProAdapter().cli()
