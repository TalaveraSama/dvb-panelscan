CC      ?= gcc
CFLAGS  ?= -O2 -Wall -Wextra
LDFLAGS ?=

TARGET  := dvb2m3u8
SRC     := dvb2m3u8.c

$(TARGET): $(SRC)
	$(CC) $(CFLAGS) -o $@ $(SRC) $(LDFLAGS)

clean:
	rm -f $(TARGET)

install: $(TARGET)
	install -m 0755 $(TARGET) /usr/local/bin/$(TARGET)

.PHONY: clean install
