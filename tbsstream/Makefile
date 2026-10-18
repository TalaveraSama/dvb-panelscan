CC      ?= gcc
CFLAGS  ?= -O2 -Wall -Wextra
LDFLAGS ?=
LIBS     = -ldvbcsa -lcrypto -lpthread

TARGET  := tbsstream
OBJS    := main.o newcamd.o csa.o

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS) $(LIBS)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(TARGET) *.o

install: $(TARGET)
	install -m 0755 $(TARGET) /usr/local/bin/$(TARGET)

.PHONY: clean install
