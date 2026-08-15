CC = gcc
CFLAGS = -O2 -Wall -Wextra -fPIC
LDFLAGS = -lcurl -lssl -lcrypto -lpthread -lm

SRCS = ai.c ai_git.c ai_terminal.c ai_session.c cJSON.c
TARGET = ai
SHARED_LIB = libremote_harness.so

.PHONY: all test bench bench-quick clean

all: $(SHARED_LIB) $(TARGET)

$(SHARED_LIB): remote_harness.c remote_harness.h cJSON.c
	$(CC) $(CFLAGS) -shared -o $@ remote_harness.c cJSON.c $(LDFLAGS)

$(TARGET): $(SRCS) $(SHARED_LIB)
	$(CC) $(CFLAGS) -o $@ $(SRCS) -L. -Wl,--no-as-needed -lremote_harness -Wl,-rpath,. -Wl,-rpath,$(HOME)/.local/lib $(LDFLAGS)

test: $(SHARED_LIB) $(TARGET)
	$(CC) $(CFLAGS) -o test_remote_harness tests/test_remote_harness.c -L. -lremote_harness -Wl,-rpath,. $(LDFLAGS)
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
