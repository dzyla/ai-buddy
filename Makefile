CC = gcc
CFLAGS = -O2 -Wall -Wextra -fPIC
LDFLAGS = -lcurl -lssl -lcrypto -lpthread -lm

SRCS = ai.c ai_git.c ai_terminal.c ai_session.c ai_toolclient.c ai_frontend.c cJSON.c
HDRS = $(wildcard *.h)
TARGET = ai
SHARED_LIB = libremote_harness.so

.PHONY: all ci test bench bench-quick clean

all: $(SHARED_LIB) $(TARGET)

$(SHARED_LIB): remote_harness.c remote_harness.h cJSON.c
	$(CC) $(CFLAGS) -shared -o $@ remote_harness.c cJSON.c $(LDFLAGS)

$(TARGET): $(SRCS) $(HDRS) $(SHARED_LIB)
	$(CC) $(CFLAGS) -o $@ $(SRCS) -L. -Wl,--no-as-needed -lremote_harness -Wl,-rpath,. -Wl,-rpath,'$$ORIGIN' -Wl,-rpath,$(HOME)/.local/lib $(LDFLAGS)

# CI build: clean rebuild of the library, the binary and the harness test with -Werror
# (the sources are kept warning-free under -Wall -Wextra; tests/test_build.py checks it).
ci:
	$(MAKE) clean
	$(MAKE) CFLAGS='$(CFLAGS) -Werror' all test_remote_harness
	./test_remote_harness

test_remote_harness: tests/test_remote_harness.c $(SHARED_LIB)
	$(CC) $(CFLAGS) -o $@ tests/test_remote_harness.c -L. -lremote_harness -Wl,-rpath,. $(LDFLAGS)

test: $(SHARED_LIB) $(TARGET) test_remote_harness
	./test_remote_harness
	pytest

# End-to-end agent benchmark: runs real jobs through `ai` against the
# configured local LLM and scores them (answer + file + tool-use + harness
# checks). Offline and deterministic; needs a running model endpoint.
bench: $(TARGET)
	python3 dev/bench_harness.py --json dev/bench_report.json

bench-quick: $(TARGET)
	python3 dev/bench_harness.py --quick

clean:
	rm -f $(TARGET) $(SHARED_LIB) test_remote_harness *.o
