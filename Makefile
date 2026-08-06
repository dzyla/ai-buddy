CC = gcc
CFLAGS = -O2 -Wall -Wextra
LDFLAGS = -lcurl -lssl -lcrypto -lpthread -lm

SRCS = ai.c remote_harness.c cJSON.c
TARGET = ai

.PHONY: all test clean

all: $(TARGET)

$(TARGET): $(SRCS) remote_harness.h
	$(CC) $(CFLAGS) -o $@ $(SRCS) $(LDFLAGS)

test: $(TARGET)
	$(CC) $(CFLAGS) -o test_remote_harness tests/test_remote_harness.c remote_harness.c $(LDFLAGS)
	./test_remote_harness
	pytest

clean:
	rm -f $(TARGET) test_remote_harness
