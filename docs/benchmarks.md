# Benchmarks & Empirical Timing

All timing numbers are reproducible and measured on physical hardware.

---

## Reproducible Pre-Test Filter Benchmark

Grounded includes an automated benchmark harness under `examples/bench/bench.py`. 

### Running the Benchmark

```bash
python3 examples/bench/bench.py
```

### Measured Latency

| Evaluation Scenario | Measured Latency | Purpose |
| :--- | :--- | :--- |
| **Grounded In-Process (Single File)** | **0.6 ms** | Agent tool execution loop (`edit_file` pre-flight) |
| **Grounded Cold CLI (Single File)** | **56.8 ms** | Fast Git pre-commit hook / terminal command |
| **Cached Full-Tree Scan** | **~2.7 s** | Repeat full-repo scans (roughly 2x faster than cold) |

---

## Empirical Verification Across Open-Source Corpora

Grounded is continuously validated against real-world, large-scale open-source repositories. Remaining false positives fall in documented classes (vendored code, kernel idioms, platform APIs, prose verbs); see the Limitations section of the README:

* **Axios (JavaScript/TypeScript)**: 1 valid finding (an intentionally broken internal test fixture). Zero false alarms across the codebase.
* **Django (Python)**: Tested against star-import chains, relative modules, and PEP 562 dynamic attributes; only intentionally-broken fixtures report.
* **Requests (Python)**: Clean reference resolution.
