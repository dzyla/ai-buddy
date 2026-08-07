# Development Directory

This directory contains development-only files that are not part of the core project or installed package.

## Contents

### Benchmarks
- `benchmark.py` - Main benchmarking script
- `benchmark_assistant.py` - Assistant benchmarking
- `benchmark_hard.py` - Hard benchmark tests

### Debugging Utilities
- `clean_cli.py` - CLI cleanup utility
- `fix_print.py` - Print debugging helper
- `full_cleanup.py` - Full cleanup utility
- `meta_harness.py` - Meta harness for development

### Report Generators
- `generate_boltz2_report.py` - Boltz2 report generation
- `generate_proteinbase_report.py` - ProteinBase report generation

### Standalone Tests
- `test_agentic_harness.py` - Agentic harness tests
- `test_deep_research.py` - Deep research tests
- `test_improvements.py` - Improvement tests
- `test_local_ai_verification.py` - Local AI verification tests
- `test_zulip_ai_bridge.py` - Zulip AI bridge tests

### GPU Testing
- `run_single_gpu.sh` - Single GPU testing script

### Visualization
- `update_visuals.py` - Visual update utility
- `particle_stats.png` - Generated particle statistics image

### Test Data
- `uniref90__subsampled_1000.fasta` - Subsampled UniRef90 FASTA file

### Development Servers
- `advanced_mcp_server.py` - Advanced MCP server for testing

## Notes

These files are not installed to `~/.local/bin` and are only used during development. The core project files and installed tools are in the root directory.
