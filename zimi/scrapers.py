"""Universal Scraper Engine & Scheduler for Zimi.

Manages background execution and recurring schedules for OpenZIM offliners:
  - mwoffliner (MediaWiki, Wikipedia, WikiHow, ArchWiki, etc.)
  - youtube2zim (Single videos, playlists, channels, users)
  - zimit / warc2zim (Arbitrary web crawl to ZIM)
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
_RUNNING_JOBS = {}  # {run_id: {"process": Popen, "info": dict, "logs": deque}}
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
    """Load scheduled scraping jobs from disk."""
    try:
        with open(_config_path(), encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []


def save_schedules(jobs):
    """Atomically save scheduled scraping jobs to disk."""
    _srv._atomic_write_json(_config_path(), jobs, indent=2)


def get_available_tools():
    """Check which offliner CLI tools are installed and in PATH."""
    return {
        "mwoffliner": shutil.which("mwoffliner") is not None,
        "youtube2zim": shutil.which("youtube2zim") is not None,
        "zimit": shutil.which("zimit") is not None,
        "warc2zim": shutil.which("warc2zim") is not None,
        "aria2c": shutil.which("aria2c") is not None,
    }


def build_command(scraper_type, params):
    """Build command argument list based on user parameters."""
    output_dir = _srv.ZIM_DIR

    if scraper_type == "mwoffliner":
        cmd = ["mwoffliner", "--output", output_dir]
        mw_url = params.get("mw_url", "").strip()
        if mw_url:
            cmd.extend(["--mwUrl", mw_url])
        
        admin_email = params.get("admin_email", "").strip()
        if admin_email:
            cmd.extend(["--adminEmail", admin_email])

        fmt = params.get("format", "full")
        if fmt == "novid":
            cmd.extend(["--format", "novid:maxi"])
        elif fmt == "nopic":
            cmd.extend(["--format", "nopic:nopic"])
        elif fmt == "mini":
            cmd.extend(["--format", "nodet,nopic:mini"])

        article_list = params.get("article_list", "").strip()
        if article_list:
            cmd.extend(["--articleList", article_list])

        custom_args = params.get("custom_args", "").strip()
        if custom_args:
            cmd.extend(shlex.split(custom_args))
        return cmd

    elif scraper_type == "youtube2zim":
        target_type = params.get("target_type", "video")  # video, playlist, channel, user
        target_url = params.get("target_url", "").strip()
        cmd = ["youtube2zim", f"--{target_type}", target_url, "--output", output_dir]

        fmt = params.get("format", "webm")
        if fmt:
            cmd.extend(["--format", fmt])

        if params.get("lower_quality"):
            cmd.append("--lower-quality")

        lang = params.get("language", "").strip()
        if lang:
            cmd.extend(["--language", lang])

        max_videos = params.get("max_videos")
        if max_videos:
            cmd.extend(["--max-videos", str(max_videos)])

        custom_args = params.get("custom_args", "").strip()
        if custom_args:
            cmd.extend(shlex.split(custom_args))
        return cmd

    elif scraper_type == "zimit":
        url = params.get("url", "").strip()
        cmd = ["zimit", "--url", url, "--output", output_dir]

        name = params.get("name", "").strip()
        if name:
            cmd.extend(["--name", name])

        title = params.get("title", "").strip()
        if title:
            cmd.extend(["--title", title])

        desc = params.get("description", "").strip()
        if desc:
            cmd.extend(["--description", desc])

        custom_args = params.get("custom_args", "").strip()
        if custom_args:
            cmd.extend(shlex.split(custom_args))
        return cmd

    else:
        raise ValueError(f"Unknown scraper type: {scraper_type}")


def run_scraper(scraper_type, params, job_id=None, label=None):
    """Spawn a scraper process in the background and track its logs."""
    try:
        cmd = build_command(scraper_type, params)
    except Exception as e:
        return None, str(e)

    run_id = str(uuid.uuid4())[:8]
    run_info = {
        "run_id": run_id,
        "job_id": job_id,
        "type": scraper_type,
        "label": label or f"{scraper_type} run",
        "command": " ".join(cmd),
        "params": params,
        "status": "running",
        "started_at": time.time(),
        "finished_at": None,
        "exit_code": None,
    }
    log_buffer = collections.deque(maxlen=1000)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        return None, f"Tool '{cmd[0]}' is not installed in the system PATH"
    except Exception as e:
        return None, f"Failed to spawn process: {e}"

    with _LOCK:
        _RUNNING_JOBS[run_id] = {
            "process": proc,
            "info": run_info,
            "logs": log_buffer,
        }

    def _stream_output():
        for line in proc.stdout:
            clean_line = line.rstrip()
            log_buffer.append(clean_line)
        proc.wait()
        run_info["finished_at"] = time.time()
        run_info["exit_code"] = proc.returncode
        run_info["status"] = "completed" if proc.returncode == 0 else "failed"

        with _LOCK:
            _RUNNING_JOBS.pop(run_id, None)
            _RECENT_RUNS.appendleft(dict(run_info))

        log.info(
            "Scraper [%s] %s finished with code %s in %.1fs",
            scraper_type,
            run_id,
            proc.returncode,
            run_info["finished_at"] - run_info["started_at"],
        )

        # Auto-ingest new ZIMs instantly without restarting Zimi
        if proc.returncode == 0:
            try:
                with _srv._zim_lock:
                    _srv.load_cache(force=True)
                _srv._search_cache_clear()
                _srv._suggest_cache_clear()
                _srv._clean_stale_title_indexes()
                log.info("Auto-ingested newly generated ZIM files into library")
            except Exception as ex:
                log.warning("Failed to refresh library cache after scrape: %s", ex)

    threading.Thread(target=_stream_output, daemon=True, name=f"scraper-{run_id}").start()
    return run_info, None


def cancel_run(run_id):
    """Terminate an active scraper subprocess."""
    with _LOCK:
        item = _RUNNING_JOBS.get(run_id)
        if not item:
            return False, "Run not found or already finished"
        proc = item["process"]
        try:
            proc.terminate()
            item["info"]["status"] = "cancelled"
            return True, None
        except Exception as e:
            return False, str(e)


def get_logs(run_id):
    """Retrieve output log lines for an active or completed run."""
    with _LOCK:
        if run_id in _RUNNING_JOBS:
            return list(_RUNNING_JOBS[run_id]["logs"]), _RUNNING_JOBS[run_id]["info"]
        for past in _RECENT_RUNS:
            if past["run_id"] == run_id:
                return past.get("logs", []), past
    return None, None


def get_status_summary():
    """Summary of tools, schedules, and active runs for the UI."""
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
    """Background evaluation loop for recurring scrape jobs."""
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
                if freq == "custom":
                    interval = max(60, int(job.get("custom_interval_seconds", 86400)))

                last_run = job.get("last_run", 0)
                if now - last_run >= interval:
                    # Check if already running
                    with _LOCK:
                        already_running = any(
                            r["info"].get("job_id") == job.get("id")
                            for r in _RUNNING_JOBS.values()
                        )
                    if not already_running:
                        log.info("Triggering scheduled scraper: %s (%s)", job.get("label"), job.get("type"))
                        run_scraper(
                            job["type"],
                            job.get("params", {}),
                            job_id=job.get("id"),
                            label=job.get("label"),
                        )
                        job["last_run"] = now
                        changed = True

            if changed:
                save_schedules(schedules)
        except Exception as e:
            log.warning("Error in scraper scheduler loop: %s", e)

        time.sleep(60)


def start_scheduler():
    """Start the background scheduler thread."""
    global _SCHEDULER_THREAD
    if _SCHEDULER_THREAD is None or not _SCHEDULER_THREAD.is_alive():
        _SCHEDULER_THREAD = threading.Thread(
            target=_scheduler_loop, daemon=True, name="zimi-scraper-scheduler"
        )
        _SCHEDULER_THREAD.start()
