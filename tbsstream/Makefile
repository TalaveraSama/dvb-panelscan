CC       ?= gcc
CFLAGS   ?= -O2 -Wall -Wextra
CPPFLAGS ?=
LDFLAGS  ?=
LIBS     ?= -ldvbcsa -lcrypto -lpthread
PREFIX   ?= /usr/local
DESTDIR  ?=

TARGET := tbsstream
OBJS   := main.o newcamd.o csa.o
TESTS  := ../.cache/c-tests/test-csa ../.cache/c-tests/test-newcamd ../.cache/c-tests/test-broker

all: $(TARGET) tools/ncdiag

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(OBJS) $(LIBS)

tools/ncdiag: tools/ncdiag.c csa.o csa.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ tools/ncdiag.c csa.o $(LIBS)

main.o: ../dvb_lnb.h pacing.h newcamd.h csa.h
newcamd.o: newcamd.h
csa.o: csa.h

%.o: %.c
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

../.cache/c-tests:
	mkdir -p $@

../.cache/c-tests/test-csa: ../tests/test_csa.c csa.c csa.h | ../.cache/c-tests
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ ../tests/test_csa.c csa.c $(LIBS)

../.cache/c-tests/test-%: ../tests/test_%.c newcamd.c newcamd.h | ../.cache/c-tests
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LIBS)

test: all $(TESTS)
	@set -e; for test in $(TESTS); do "$$test"; done
	tools/ncdiag --selftest --json

clean:
	rm -f $(TARGET) tools/ncdiag *.o $(TESTS)

install: all
	install -d "$(DESTDIR)$(PREFIX)/bin"
	install -m 0755 $(TARGET) "$(DESTDIR)$(PREFIX)/bin/$(TARGET)"
	install -m 0755 tools/ncdiag "$(DESTDIR)$(PREFIX)/bin/dvb-ncdiag"

.PHONY: all test clean install
