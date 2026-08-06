CC = gcc
CFLAGS = -O2 -Wall -Wextra
LDFLAGS = -lcurl -lssl -lcrypto -lpthread -lm

SRCS = ai.c remote_harness.c cJSON.c
TARGET = ai

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(SRCS) remote_harness.h
	$(CC) $(CFLAGS) -o $@ $(SRCS) $(LDFLAGS)

clean:
	rm -f $(TARGET)
