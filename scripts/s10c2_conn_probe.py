# free diagnostic GET of the models list, runs outside the paid wrapper by approved decision S10c2D-05.
import argparse
import concurrent.futures
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time
from urllib.parse import urlparse

import httpx

REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


def redact_key(text: str, key: str | None) -> str:
    if not text or not key:
        return text
    return text.replace(key, "[REDACTED]")


def extract_cause_chain(exc: BaseException | None, key: str | None = None, max_depth: int = 5) -> list[list[str]]:
    chain = []
    curr = exc
    depth = 0
    seen = set()
    while curr is not None and depth < max_depth:
        if id(curr) in seen:
            break
        seen.add(id(curr))
        msg = redact_key(str(curr), key)
        if len(msg) > 300:
            msg = msg[:300]
        chain.append([type(curr).__name__, msg])
        depth += 1
        curr = curr.__cause__ or curr.__context__
    return chain


def phase_at_failure(trace_events: list[list]) -> str | None:
    started_events = []
    completed_events = set()
    for ev in trace_events:
        name = ev[0]
        if name.endswith(".started"):
            started_events.append(name)
        elif name.endswith(".complete"):
            completed_events.add(name[:-9])
    for ev in reversed(started_events):
        base = ev[:-8]
        if base not in completed_events:
            return ev
    return None


def make_trace_callback(t_start: float):
    events = []
    server_addr = [None]

    def callback(name: str, info: dict) -> None:
        t_event = round(time.perf_counter() - t_start, 4)
        events.append([name, t_event])
        if name == "connection.connect_tcp.complete":
            ret = info.get("return_value")
            if ret is not None and hasattr(ret, "get_extra_info"):
                try:
                    server_addr[0] = ret.get_extra_info("server_addr")
                except Exception:
                    pass

    return callback, events, server_addr


def send_probe_request(
    client: httpx.Client,
    url: str,
    key: str,
    phase_name: str,
    seq: int,
    idle_s: int | None = None,
) -> dict:
    t_wall_start = datetime.now(timezone.utc).isoformat()
    t_start = time.perf_counter()
    callback, trace_events, server_addr_holder = make_trace_callback(t_start)
    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = client.get(url, headers=headers, extensions={"trace": callback})
        elapsed_s = round(time.perf_counter() - t_start, 4)
        http_status = resp.status_code
        outcome = "ok" if http_status == 200 else str(http_status)
        error_type = None
        cause_chain = []
        phase_fail = None if outcome == "ok" else phase_at_failure(trace_events)
    except Exception as exc:
        elapsed_s = round(time.perf_counter() - t_start, 4)
        http_status = getattr(exc, "status_code", None)
        outcome = "error"
        error_type = type(exc).__name__
        cause_chain = extract_cause_chain(exc, key=key)
        phase_fail = phase_at_failure(trace_events)

    new_conn = any("connect_tcp" in ev[0] for ev in trace_events)
    return {
        "phase_name": phase_name,
        "seq": seq,
        "idle_s": idle_s,
        "t_start_utc": t_wall_start,
        "elapsed_s": elapsed_s,
        "outcome": outcome,
        "http_status": http_status,
        "error_type": error_type,
        "cause_chain": cause_chain,
        "trace_events": trace_events,
        "phase_at_failure": phase_fail,
        "new_connection": new_conn,
        "server_addr": server_addr_holder[0],
    }


def write_event(events_file, lock: threading.Lock, event: dict) -> None:
    line = json.dumps(event, allow_nan=False) + "\n"
    with lock:
        events_file.write(line)
        events_file.flush()


def run_probe(
    args,
    endpoint: str,
    key: str,
    transport_factory=None,
    sleep_fn=time.sleep,
    getaddrinfo_fn=socket.getaddrinfo,
) -> int:
    out_dir = Path(args.out).resolve()
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print("refused: output folder exists")
        return 2

    utc_start = datetime.now(timezone.utc).isoformat()
    parsed_url = urlparse(endpoint)
    host = parsed_url.hostname or ""
    models_url = f"{endpoint.rstrip('/')}/openai/v1/models"

    try:
        dns_records = [item[4][0] for item in getaddrinfo_fn(host, 443)]
    except Exception as exc:
        dns_records = [f"lookup_failed: {type(exc).__name__}"]

    win_build = None
    if hasattr(sys, "getwindowsversion"):
        w = sys.getwindowsversion()
        win_build = dict(major=w.major, minor=w.minor, build=w.build, platform=w.platform)

    import httpcore
    import openai

    socket_consts = {
        k: hasattr(socket, k)
        for k in ["SO_KEEPALIVE", "TCP_KEEPIDLE", "TCP_KEEPINTVL", "TCP_KEEPCNT", "SIO_KEEPALIVE_VALS"]
    }

    ka_on_options_check = {}
    ka_opts_to_test = [
        ("SO_KEEPALIVE", socket.SOL_SOCKET, getattr(socket, "SO_KEEPALIVE", None), 1),
        ("TCP_KEEPIDLE", socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPIDLE", None), 30),
        ("TCP_KEEPINTVL", socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPINTVL", None), 10),
        ("TCP_KEEPCNT", socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPCNT", None), 5),
    ]
    for opt_name, level, opt_val, val in ka_opts_to_test:
        if opt_val is None:
            ka_on_options_check[opt_name] = "attribute_missing"
            continue
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.setsockopt(level, opt_val, val)
                ka_on_options_check[opt_name] = "ok"
            finally:
                s.close()
        except Exception as exc:
            ka_on_options_check[opt_name] = type(exc).__name__

    settings_data = {
        "label": args.label,
        "t_start_utc": utc_start,
        "python_version": sys.version,
        "windows_build": win_build,
        "package_versions": {
            "openai": openai.__version__,
            "httpx": httpx.__version__,
            "httpcore": httpcore.__version__,
        },
        "socket_constants": socket_consts,
        "ka_on_options_check": ka_on_options_check,
        "endpoint_host": host,
        "dns_addresses": sorted(set(dns_records)),
        "wrapper_effective_client_settings": {
            "max_retries": "0 at azure_openai.py:84",
            "tries": "3 tries at :99",
            "backoff": "exponential with jitter at :136",
            "custom_transport": "no custom transport at :80-87",
            "keepalive_expiry_s": "5 s default",
        },
        "cli_flags": {
            "soak_minutes": args.soak_minutes,
            "soak_interval_s": args.soak_interval_s,
            "idle_list": [int(x.strip()) for x in args.idle_list.split(",") if x.strip()],
            "skip_idle": args.skip_idle,
            "skip_soak": args.skip_soak,
        },
    }

    settings_path = out_dir / "settings.json"
    with settings_path.open("x", encoding="utf-8") as sf:
        sf.write(json.dumps(settings_data, indent=2) + "\n")
    print(json.dumps(settings_data, indent=2))

    events_path = out_dir / "events.jsonl"
    events_file = events_path.open("x", encoding="utf-8")
    file_lock = threading.Lock()
    all_events = []
    seq_counter = [0]
    interrupted = [False]

    def record_event(ev: dict):
        all_events.append(ev)
        write_event(events_file, file_lock, ev)

    def next_seq() -> int:
        with file_lock:
            seq_counter[0] += 1
            return seq_counter[0]

    STOP_NOTICE = b"stop requested: finishing the current step; press Ctrl+C again to abort now\n"

    def sigint_handler(signum, frame):
        interrupted[0] = True
        signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            os.write(2, STOP_NOTICE)
        except OSError:
            pass

    prev_sigint = signal.signal(signal.SIGINT, sigint_handler)

    def default_transport_maker(arm: str, socket_opts=None, limits=None):
        if transport_factory is not None:
            return transport_factory(arm=arm, socket_options=socket_opts, limits=limits)
        opts = socket_opts or []
        lims = limits or httpx.Limits()
        return httpx.HTTPTransport(socket_options=opts, limits=lims)

    try:
        # P1 Check Request
        check_transport = default_transport_maker("P1")
        with httpx.Client(transport=check_transport, timeout=REQUEST_TIMEOUT) as check_client:
            p1_event = send_probe_request(check_client, models_url, key, "P1", next_seq())
            record_event(p1_event)

        if p1_event["outcome"] != "ok":
            summary = {
                "settings_file": "settings.json",
                "utc_start": utc_start,
                "utc_end": datetime.now(timezone.utc).isoformat(),
                "total_requests": len(all_events),
                "reason": "check_failed",
                "status": p1_event["http_status"] or p1_event["outcome"],
                "error_type": p1_event["error_type"],
            }
            summary_path = out_dir / "summary.json"
            with summary_path.open("x", encoding="utf-8") as sumf:
                sumf.write(json.dumps(summary, indent=2) + "\n")
            print(json.dumps(summary, indent=2))
            return 2

        # P2 Soak
        if not args.skip_soak and not interrupted[0]:
            total_soak_steps = max(1, int((args.soak_minutes * 60) / args.soak_interval_s))
            for _ in range(total_soak_steps):
                if interrupted[0]:
                    break
                t_step_start = time.perf_counter()
                p2_transport = default_transport_maker("P2")
                with httpx.Client(transport=p2_transport, timeout=REQUEST_TIMEOUT) as client:
                    ev = send_probe_request(client, models_url, key, "P2", next_seq())
                    record_event(ev)
                elapsed = time.perf_counter() - t_step_start
                remain = max(0.0, args.soak_interval_s - elapsed)
                sleep_fn(remain)

        # P3 Idle
        p3_table = {}
        if not args.skip_idle and not interrupted[0]:
            idle_values = settings_data["cli_flags"]["idle_list"]
            has_keepidle = hasattr(socket, "TCP_KEEPIDLE")
            arms = ["ka_off"]
            if has_keepidle:
                arms.append("ka_on")
            else:
                p3_table["ka_on"] = "ka_on_unavailable"

            def run_idle_arm(arm_name: str):
                sock_opts = []
                if arm_name == "ka_on":
                    sock_opts.append((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1))
                    if hasattr(socket, "TCP_KEEPIDLE"):
                        sock_opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30))
                    if hasattr(socket, "TCP_KEEPINTVL"):
                        sock_opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10))
                    if hasattr(socket, "TCP_KEEPCNT"):
                        sock_opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5))

                arm_limits = httpx.Limits(keepalive_expiry=400.0)
                for idle_sec in idle_values:
                    if interrupted[0]:
                        break
                    arm_trans = default_transport_maker(arm_name, socket_opts=sock_opts, limits=arm_limits)
                    with httpx.Client(transport=arm_trans, limits=arm_limits, timeout=REQUEST_TIMEOUT) as client:
                        ev1 = send_probe_request(client, models_url, key, arm_name, next_seq(), idle_s=idle_sec)
                        record_event(ev1)
                        sleep_fn(idle_sec)
                        ev2 = send_probe_request(client, models_url, key, arm_name, next_seq(), idle_s=idle_sec)
                        record_event(ev2)

                    if ev2["outcome"] != "ok":
                        classification = "failed"
                    elif ev2["new_connection"]:
                        classification = "reconnected_ok"
                    else:
                        classification = "reused_ok"

                    key_name = f"{arm_name}:{idle_sec}"
                    p3_table[key_name] = {
                        "classification": classification,
                        "http_status": ev2["http_status"],
                        "error_type": ev2["error_type"],
                        "phase_at_failure": ev2["phase_at_failure"],
                    }

            futures = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(arms)) as executor:
                for arm in arms:
                    futures[executor.submit(run_idle_arm, arm)] = arm
                concurrent.futures.wait(list(futures.keys()))

            for future, arm in futures.items():
                try:
                    future.result()
                except Exception as exc:
                    msg = redact_key(str(exc), key)
                    if len(msg) > 300:
                        msg = msg[:300]
                    p3_table[f"{arm}:error"] = {
                        "error_type": type(exc).__name__,
                        "message": msg,
                    }

        # Build Summary
        p2_events = [e for e in all_events if e["phase_name"] == "P2"]
        n_p2 = len(p2_events)
        ok_p2 = sum(1 for e in p2_events if e["outcome"] == "ok")
        failed_p2 = n_p2 - ok_p2

        failures_by_type = {}
        failures_by_phase = {}
        failure_timestamps = []
        longest_streak = 0
        cur_streak = 0
        for e in p2_events:
            if e["outcome"] != "ok":
                cur_streak += 1
                if cur_streak > longest_streak:
                    longest_streak = cur_streak
                t = e["error_type"] or str(e["http_status"]) or "unknown"
                failures_by_type[t] = failures_by_type.get(t, 0) + 1
                p = e["phase_at_failure"] or "unknown"
                failures_by_phase[p] = failures_by_phase.get(p, 0) + 1
                failure_timestamps.append(e["t_start_utc"])
            else:
                cur_streak = 0

        p2_summary = {
            "n": n_p2,
            "ok": ok_p2,
            "failed": failed_p2,
            "failures_by_error_type": failures_by_type,
            "failures_by_phase_at_failure": failures_by_phase,
            "failure_timestamps": failure_timestamps,
            "longest_consecutive_failures": longest_streak,
        }
        if failed_p2 == 0 and n_p2 > 0:
            p2_summary["rule_of_three_95_upper_bound"] = f"3/{n_p2} ({3.0 / n_p2:.4f})"

        summary = {
            "settings_file": "settings.json",
            "utc_start": utc_start,
            "utc_end": datetime.now(timezone.utc).isoformat(),
            "total_requests": len(all_events),
            "interrupted": interrupted[0],
            "P2": p2_summary,
            "P3": p3_table,
        }

        summary_path = out_dir / "summary.json"
        with summary_path.open("x", encoding="utf-8") as sumf:
            sumf.write(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        return 130 if interrupted[0] else 0

    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        events_file.close()


def main(
    argv=None,
    transport_factory=None,
    sleep_fn=time.sleep,
    getaddrinfo_fn=socket.getaddrinfo,
) -> int:
    parser = argparse.ArgumentParser(description="Diagnostic connection probe for Azure OpenAI v1 endpoint.")
    parser.add_argument("--out", required=True, help="Output folder (must not exist)")
    parser.add_argument("--label", required=True, help="Network label (e.g. wifi, hotspot)")
    parser.add_argument("--soak-minutes", type=float, default=25, help="Soak duration in minutes")
    parser.add_argument("--soak-interval-s", type=float, default=15, help="Seconds between soak requests")
    parser.add_argument("--idle-list", default="30,90,180,300", help="Comma-separated idle seconds for P3")
    parser.add_argument("--skip-idle", action="store_true", help="Skip P3 idle phase")
    parser.add_argument("--skip-soak", action="store_true", help="Skip P2 soak phase")
    args = parser.parse_args(argv)

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    if not endpoint or not key:
        print("refused: missing env")
        return 2

    return run_probe(
        args,
        endpoint,
        key,
        transport_factory=transport_factory,
        sleep_fn=sleep_fn,
        getaddrinfo_fn=getaddrinfo_fn,
    )


if __name__ == "__main__":
    sys.exit(main())
