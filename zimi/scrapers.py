"""Universal Scraper Engine & Scheduler for Zimi.

Manages background execution and recurring schedules for OpenZIM offliners wrapped invisibly in Docker:
  - youtube2zim (YouTube channels, playlists, videos)
  - sotoki (Stack Exchange)
  - gutenberg2zim (Project Gutenberg)
  - ted2zim (TED Talks)
  - devdocs2zim (Developer Documentation)
  - ifixit2zim (iFixit Repair Guides)
  - wikihow2zim (WikiHow)
  - fcc2zim (FreeCodeCamp)
  - wget2zim (Custom static site archiver via wget + warc2zim)
"""

import collections
import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid

import zimi.server as _srv

log = logging.getLogger("zimi.scrapers")

_CONFIG_FILE = "scrapers.json"
_LOCK = threading.Lock()
_SCHEDULER_THREAD = None
_RUNNING_JOBS = {}
_RECENT_RUNS = collections.deque(maxlen=50)

_INTERVAL_MAP = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
    "monthly": 2592000,
}

def _config_path():
    return os.path.join(_srv.ZIMI_DATA_DIR, _CONFIG_FILE)

def load_schedules():
    try:
        with open(_config_path(), encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []

def save_schedules(jobs):
    _srv._atomic_write_json(_config_path(), jobs, indent=2)

def get_available_tools():
    has_docker = shutil.which("docker") is not None
    return {
        "youtube2zim (Docker)": has_docker,
        "sotoki (Docker)": has_docker,
        "gutenberg2zim (Docker)": has_docker,
        "ted2zim (Docker)": has_docker,
        "devdocs2zim (Docker)": has_docker,
        "ifixit2zim (Docker)": has_docker,
        "wikihow2zim (Docker)": has_docker,
        "fcc2zim (Docker)": has_docker,
        "wget (Native)": shutil.which("wget") is not None,
        "warc2zim (Docker)": has_docker,
    }

def build_command(scraper_type, params, run_id):
    """Build a docker run command to execute the scraper invisibly."""
    output_dir = _srv.ZIM_DIR
    os.makedirs(output_dir, exist_ok=True)
    
    custom_args_str = params.get("custom_args", "").strip()
    custom_args = shlex.split(custom_args_str) if custom_args_str else []

    # Map scraper tools to their official OpenZIM docker images
    image_map = {
        "youtube2zim": "ghcr.io/openzim/youtube",
        "sotoki": "ghcr.io/openzim/sotoki",
        "gutenberg2zim": "ghcr.io/openzim/gutenberg",
        "ted2zim": "ghcr.io/openzim/ted",
        "devdocs2zim": "ghcr.io/openzim/devdocs",
        "ifixit2zim": "ghcr.io/openzim/ifixit",
        "wikihow2zim": "ghcr.io/openzim/wikihow",
        "fcc2zim": "ghcr.io/openzim/freecodecamp",
        "warc2zim": "ghcr.io/openzim/warc2zim"
    }

    if scraper_type == "wget2zim":
        url = params.get("url", "").strip()
        name = params.get("name", "").strip()
        warc_prefix = f"/tmp/warc_{run_id}"
        
        # Safely requote custom args for execution inside the bash chain
        bash_custom = " " + shlex.join(custom_args) if custom_args else ""
        
        # Wget runs natively on Debian, warc2zim packs it using Docker
        bash_cmd = (
            f"wget --no-verbose --mirror --page-requisites --adjust-extension "
            f"--no-parent --warc-file={warc_prefix} {shlex.quote(url)} && "
            f"docker run --rm --entrypoint warc2zim -v {output_dir}:/output -v /tmp:/tmp "
            f"ghcr.io/openzim/warc2zim {warc_prefix}.warc.gz --output /output --name {shlex.quote(name)}{bash_custom} && "
            f"rm -f {warc_prefix}.warc.gz"
        )
        return bash_cmd, True

    # Base Docker command for all OpenZIM tools
    image = image_map[scraper_type]
    cmd = ["docker", "run", "--rm", "--entrypoint", scraper_type, "-v", f"{output_dir}:/output", image]

    # Tool-specific arguments
    if scraper_type == "youtube2zim":
        cmd.extend(["--id", params.get("target_id", "").strip(), "--api-key", params.get("api_key", "").strip(), "--name", params.get("name", "").strip(), "--output", "/output"])
        fmt = params.get("format", "webm")
        if fmt: cmd.extend(["--format", fmt])
        if params.get("lower_quality"): cmd.append("--low-quality")
        lang = params.get("language", "").strip()
        if lang: cmd.extend(["--language", lang])
        max_videos = params.get("max_videos")
        if max_videos: cmd.extend(["--max-videos", str(max_videos)])

    elif scraper_type == "sotoki":
        cmd.extend(["--domain", params.get("domain", "").strip(), "--output", "/output"])

    elif scraper_type == "ted2zim":
        cmd.extend(["--lang", params.get("lang", "en").strip(), "--output", "/output"])
        topics = params.get("topics", "").strip()
        if topics: cmd.extend(["--topics", topics])
        fmt = params.get("format", "webm")
        if fmt: cmd.extend(["--format", fmt])
        if params.get("lower_quality"): cmd.append("--low-quality")

    elif scraper_type == "devdocs2zim":
        cmd.extend(["--output", "/output"])
        mode = params.get("mode", "all")
        if mode == "slug":
            slugs = params.get("slugs", "").strip()
            if slugs: cmd.extend(["--slug", slugs])
        elif mode == "first":
            first = params.get("first", "").strip()
            if first: cmd.extend(["--first", str(first)])
        else:
            cmd.append("--all")

    elif scraper_type == "fcc2zim":
        cmd.extend(["--language", params.get("lang", "english").strip(), "--output", "/output"])
        course = params.get("course", "").strip()
        if course: cmd.extend(["--course", course])

    elif scraper_type in ["gutenberg2zim", "ifixit2zim", "wikihow2zim"]:
        lang_arg = "--language" if scraper_type in ["ifixit2zim", "wikihow2zim"] else "--lang"
        cmd.extend([lang_arg, params.get("lang", "en").strip(), "--output", "/output"])

    cmd.extend(custom_args)
    return cmd, False

def run_scraper(scraper_type, params, job_id=None, label=None):
    run_id = str(uuid.uuid4())[:8]
    try:
        cmd, use_shell = build_command(scraper_type, params, run_id)
    except Exception as e:
        return None, str(e)

    run_info = {
        "run_id": run_id,
        "job_id": job_id,
        "type": scraper_type,
        "label": label or f"{scraper_type} run",
        "command": cmd if use_shell else " ".join(cmd),
        "params": params,
        "status": "running",
        "started_at": time.time(),
        "finished_at": None,
        "exit_code": None,
        "logs": [],
    }
    log_buffer = collections.deque(maxlen=2000)
    log_buffer.append(f"=== Starting job {run_id} ===")
    log_buffer.append(f"Command: {run_info['command']}\n")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.Popen(
            cmd,
            shell=use_shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except Exception as e:
        return None, f"Failed to spawn process: {e}"

    with _LOCK:
        _RUNNING_JOBS[run_id] = {
            "process": proc,
            "info": run_info,
            "logs": log_buffer,
        }

    def _stream_output():
        try:
            for line in proc.stdout:
                log_buffer.append(line.rstrip())
        except Exception as err:
            log_buffer.append(f"[Error reading stream: {err}]")
        finally:
            proc.wait()
            run_info["finished_at"] = time.time()
            run_info["exit_code"] = proc.returncode
            run_info["status"] = "completed" if proc.returncode == 0 else "failed"
            log_buffer.append(f"\n=== Process exited with code {proc.returncode} ===")
            run_info["logs"] = list(log_buffer)

            with _LOCK:
                _RUNNING_JOBS.pop(run_id, None)
                _RECENT_RUNS.appendleft(dict(run_info))

            log.info("Scraper [%s] %s finished with code %s", scraper_type, run_id, proc.returncode)

            if proc.returncode == 0:
                try:
                    with _srv._zim_lock:
                        _srv.load_cache(force=True)
                    _srv._search_cache_clear()
                    _srv._suggest_cache_clear()
                    _srv._clean_stale_title_indexes()
                    log.info("Auto-ingested newly generated ZIM files")
                except Exception as ex:
                    log.warning("Failed to refresh library cache: %s", ex)

    threading.Thread(target=_stream_output, daemon=True, name=f"scraper-{run_id}").start()
    return run_info, None

def cancel_run(run_id):
    with _LOCK:
        item = _RUNNING_JOBS.get(run_id)
        if not item:
            return False, "Run not found or already finished"
        try:
            item["process"].terminate()
            item["info"]["status"] = "cancelled"
            return True, None
        except Exception as e:
            return False, str(e)

def get_logs(run_id):
    with _LOCK:
        if run_id in _RUNNING_JOBS:
            return list(_RUNNING_JOBS[run_id]["logs"]), _RUNNING_JOBS[run_id]["info"]
        for past in _RECENT_RUNS:
            if past["run_id"] == run_id:
                return past.get("logs", []), past
    return None, None

def get_status_summary():
    with _LOCK:
        active = [v["info"] for v in _RUNNING_JOBS.values()]
        recent = list(_RECENT_RUNS)
    return {
        "tools": get_available_tools(),
        "schedules": load_schedules(),
        "active_runs": active,
        "recent_runs": recent,
    }

def _scheduler_loop():
    log.info("Zimi Scraper Scheduler daemon started")
    while True:
        try:
            schedules = load_schedules()
            now = time.time()
            changed = False

            for job in schedules:
                if not job.get("enabled", True):
                    continue
                freq = job.get("frequency", "daily")
                interval = _INTERVAL_MAP.get(freq, 86400)
                last_run = job.get("last_run", 0)
                
                if now - last_run >= interval:
                    with _LOCK:
                        already_running = any(r["info"].get("job_id") == job.get("id") for r in _RUNNING_JOBS.values())
                    if not already_running:
                        log.info("Triggering scheduled scraper: %s", job.get("label"))
                        run_scraper(job["type"], job.get("params", {}), job_id=job.get("id"), label=job.get("label"))
                        job["last_run"] = now
                        changed = True

            if changed: save_schedules(schedules)
        except Exception as e:
            log.warning("Scheduler error: %s", e)
        time.sleep(60)

def start_scheduler():
    global _SCHEDULER_THREAD
    if _SCHEDULER_THREAD is None or not _SCHEDULER_THREAD.is_alive():
        _SCHEDULER_THREAD = threading.Thread(target=_scheduler_loop, daemon=True, name="zimi-scraper-scheduler")
        _SCHEDULER_THREAD.start()
