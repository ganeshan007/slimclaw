"""Memory profiling for slimclaw.

Measures:
1. Bare Python interpreter baseline
2. Import overhead for each module
3. Database initialization + schema creation
4. Bulk message insertion (1000, 5000, 10000 messages)
5. Bulk message query performance
6. Full module import (all modules loaded)
7. Object creation overhead (dataclasses vs dicts)
"""
import gc
import json
import os
import sys
import time
import tracemalloc

# Ensure we're in the right directory
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psutil

process = psutil.Process()


def get_rss_mb():
    """Get current RSS in MB."""
    return process.memory_info().rss / 1024 / 1024


def get_tracemalloc_mb():
    """Get tracemalloc current/peak in MB."""
    current, peak = tracemalloc.get_traced_memory()
    return current / 1024 / 1024, peak / 1024 / 1024


def force_gc():
    """Force garbage collection."""
    gc.collect()
    gc.collect()
    gc.collect()


def measure_import(module_name, display_name=None):
    """Measure memory impact of importing a module."""
    force_gc()
    rss_before = get_rss_mb()
    traced_before = tracemalloc.get_traced_memory()[0]

    __import__(module_name)

    force_gc()
    rss_after = get_rss_mb()
    traced_after = tracemalloc.get_traced_memory()[0]

    name = display_name or module_name
    rss_delta = rss_after - rss_before
    traced_delta = (traced_after - traced_before) / 1024 / 1024
    return {
        "module": name,
        "rss_delta_mb": round(rss_delta, 3),
        "traced_delta_mb": round(traced_delta, 3),
        "rss_after_mb": round(rss_after, 3),
    }


def main():
    results = {"runtime": "python", "python_version": sys.version, "tests": []}

    tracemalloc.start()

    # --- Test 1: Bare interpreter baseline ---
    force_gc()
    baseline_rss = get_rss_mb()
    baseline_traced, _ = get_tracemalloc_mb()
    results["tests"].append({
        "name": "bare_interpreter",
        "rss_mb": round(baseline_rss, 3),
        "traced_mb": round(baseline_traced, 3),
    })
    print(f"[1] Bare interpreter:          RSS={baseline_rss:.1f} MB")

    # --- Test 2: Import overhead per module ---
    print("\n[2] Import overhead per module:")
    modules = [
        ("slimclaw.types", "types"),
        ("slimclaw.env", "env"),
        ("slimclaw.config", "config"),
        ("slimclaw.logger", "logger"),
        ("slimclaw.router", "router"),
        ("slimclaw.container_runtime", "container_runtime"),
        ("slimclaw.db", "db"),
        ("slimclaw.mount_security", "mount_security"),
        ("slimclaw.group_queue", "group_queue"),
        ("slimclaw.ipc", "ipc"),
        ("slimclaw.task_scheduler", "task_scheduler"),
        ("slimclaw.container_runner", "container_runner"),
    ]

    import_results = []
    for mod, name in modules:
        r = measure_import(mod, name)
        import_results.append(r)
        print(f"    {name:25s}  RSS delta={r['rss_delta_mb']:+.3f} MB  traced delta={r['traced_delta_mb']:+.3f} MB  RSS total={r['rss_after_mb']:.1f} MB")

    results["tests"].append({
        "name": "import_overhead",
        "modules": import_results,
    })

    # Snapshot after all imports
    force_gc()
    post_import_rss = get_rss_mb()
    post_import_traced, post_import_peak = get_tracemalloc_mb()
    results["tests"].append({
        "name": "all_modules_imported",
        "rss_mb": round(post_import_rss, 3),
        "traced_mb": round(post_import_traced, 3),
        "traced_peak_mb": round(post_import_peak, 3),
    })
    print(f"\n    All modules imported:       RSS={post_import_rss:.1f} MB  traced={post_import_traced:.2f} MB  peak={post_import_peak:.2f} MB")

    # --- Test 3: Database initialization ---
    print("\n[3] Database initialization:")
    force_gc()
    rss_before = get_rss_mb()
    traced_before = tracemalloc.get_traced_memory()[0]

    from slimclaw.db import _init_test_database
    _init_test_database()

    force_gc()
    rss_after = get_rss_mb()
    traced_after = tracemalloc.get_traced_memory()[0]
    db_init_result = {
        "name": "db_init",
        "rss_delta_mb": round(rss_after - rss_before, 3),
        "traced_delta_mb": round((traced_after - traced_before) / 1024 / 1024, 3),
    }
    results["tests"].append(db_init_result)
    print(f"    DB init (in-memory):        RSS delta={db_init_result['rss_delta_mb']:+.3f} MB  traced delta={db_init_result['traced_delta_mb']:+.3f} MB")

    # --- Test 4: Bulk message insertion ---
    print("\n[4] Bulk message insertion:")
    from slimclaw.db import store_chat_metadata, store_message, get_messages_since, get_new_messages
    from slimclaw.types import NewMessage

    for count in [1000, 5000, 10000]:
        _init_test_database()
        store_chat_metadata("bench@g.us", "2024-01-01T00:00:00.000Z")

        force_gc()
        rss_before = get_rss_mb()
        traced_before = tracemalloc.get_traced_memory()[0]
        t_start = time.perf_counter()

        for i in range(count):
            store_message(NewMessage(
                id=f"msg-{i}",
                chat_jid="bench@g.us",
                sender=f"user{i % 10}@s.whatsapp.net",
                sender_name=f"User{i % 10}",
                content=f"Message number {i} with some content to simulate real messages",
                timestamp=f"2024-01-01T{i // 3600:02d}:{(i % 3600) // 60:02d}:{i % 60:02d}.000Z",
            ))

        t_elapsed = time.perf_counter() - t_start
        force_gc()
        rss_after = get_rss_mb()
        traced_after = tracemalloc.get_traced_memory()[0]

        insert_result = {
            "name": f"insert_{count}_messages",
            "count": count,
            "time_ms": round(t_elapsed * 1000, 1),
            "rss_delta_mb": round(rss_after - rss_before, 3),
            "traced_delta_mb": round((traced_after - traced_before) / 1024 / 1024, 3),
            "msgs_per_sec": round(count / t_elapsed),
        }
        results["tests"].append(insert_result)
        print(f"    {count:>5} messages:  {t_elapsed*1000:7.1f} ms  ({insert_result['msgs_per_sec']:,} msg/s)  RSS delta={insert_result['rss_delta_mb']:+.3f} MB")

    # --- Test 5: Query performance ---
    print("\n[5] Query performance (10000 messages in DB):")

    # DB already has 10000 messages from last iteration
    for label, fn in [
        ("getMessagesSince (all)", lambda: get_messages_since("bench@g.us", "", "Andy")),
        ("getMessagesSince (last 100)", lambda: get_messages_since("bench@g.us", "2024-01-01T02:43:19.000Z", "Andy")),
        ("getNewMessages (1 group)", lambda: get_new_messages(["bench@g.us"], "", "Andy")),
    ]:
        force_gc()
        rss_before = get_rss_mb()
        traced_before = tracemalloc.get_traced_memory()[0]
        t_start = time.perf_counter()

        result_data = fn()

        t_elapsed = time.perf_counter() - t_start
        force_gc()
        rss_after = get_rss_mb()
        traced_after = tracemalloc.get_traced_memory()[0]

        row_count = len(result_data) if isinstance(result_data, list) else len(result_data[0])
        query_result = {
            "name": f"query_{label}",
            "rows": row_count,
            "time_ms": round(t_elapsed * 1000, 2),
            "rss_delta_mb": round(rss_after - rss_before, 3),
            "traced_delta_mb": round((traced_after - traced_before) / 1024 / 1024, 3),
        }
        results["tests"].append(query_result)
        print(f"    {label:35s}  {row_count:>5} rows  {t_elapsed*1000:7.2f} ms  RSS delta={query_result['rss_delta_mb']:+.3f} MB")

    # --- Test 6: Dataclass creation overhead ---
    print("\n[6] Object creation overhead (10000 NewMessage dataclasses):")
    force_gc()
    rss_before = get_rss_mb()
    traced_before = tracemalloc.get_traced_memory()[0]
    t_start = time.perf_counter()

    objects = []
    for i in range(10000):
        objects.append(NewMessage(
            id=f"obj-{i}", chat_jid="bench@g.us", sender=f"user{i}@s.whatsapp.net",
            sender_name=f"User{i}", content=f"Content {i}",
            timestamp=f"2024-01-01T00:00:{i % 60:02d}.000Z",
        ))

    t_elapsed = time.perf_counter() - t_start
    force_gc()
    rss_after = get_rss_mb()
    traced_after = tracemalloc.get_traced_memory()[0]

    obj_result = {
        "name": "create_10000_dataclasses",
        "time_ms": round(t_elapsed * 1000, 2),
        "rss_delta_mb": round(rss_after - rss_before, 3),
        "traced_delta_mb": round((traced_after - traced_before) / 1024 / 1024, 3),
    }
    results["tests"].append(obj_result)
    print(f"    10000 dataclasses:          {t_elapsed*1000:7.2f} ms  RSS delta={obj_result['rss_delta_mb']:+.3f} MB  traced delta={obj_result['traced_delta_mb']:+.3f} MB")
    del objects

    # --- Test 7: Final snapshot ---
    force_gc()
    final_rss = get_rss_mb()
    final_traced, final_peak = get_tracemalloc_mb()
    results["tests"].append({
        "name": "final_snapshot",
        "rss_mb": round(final_rss, 3),
        "traced_mb": round(final_traced, 3),
        "traced_peak_mb": round(final_peak, 3),
    })
    print(f"\n[7] Final snapshot:             RSS={final_rss:.1f} MB  traced={final_traced:.2f} MB  peak={final_peak:.2f} MB")

    # --- Test 8: Top memory allocations ---
    print("\n[8] Top 10 memory allocations:")
    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics("lineno")
    alloc_list = []
    for stat in top_stats[:10]:
        print(f"    {stat}")
        alloc_list.append(str(stat))
    results["tests"].append({"name": "top_allocations", "allocations": alloc_list})

    tracemalloc.stop()

    # Write results
    output_path = os.path.join(os.path.dirname(__file__), "results_python.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults written to {output_path}")


if __name__ == "__main__":
    main()
