#!/bin/bash
# Barrido fino alrededor de la FI esperada (1220 MHz, LNB universal) para el 70W.
DVB=/home/ruben/tbs-tools/dvb2m3u8/dvb2m3u8
echo "inicio $(date +%H:%M:%S)"
for a in 0 1 2 3; do
  for pol in V H; do
    for f in $(seq 1160 5 1300); do
      $DVB -a $a -t dvbs --freq-mhz $f --symbol-rate 27900000 --pol $pol -T 2 >/dev/null 2>&1
      if [ $? -eq 0 ]; then echo "LOCK adapter=$a freq=$f pol=$pol"; fi
    done
  done
done
echo "fin $(date +%H:%M:%S)"
