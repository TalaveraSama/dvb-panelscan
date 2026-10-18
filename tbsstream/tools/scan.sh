#!/bin/bash
# Barrido rápido de transpondedores Ku comunes (IF 950-2150 MHz) con dvb2m3u8.
# Informa qué frecuencias hacen LOCK (señal presente).
DVB=/home/ruben/tbs-tools/dvb2m3u8/dvb2m3u8
FREQS="950 990 1030 1070 1110 1150 1190 1230 1270 1310 1350 1390 1430 1470 1510 1550 1590 1630 1670 1710 1750 1790 1830 1870 1910 1950 1990 2030 2070 2110"
echo "inicio $(date +%H:%M:%S)"
for pol in H V; do
  for f in $FREQS; do
    $DVB -t dvbs --freq-mhz $f --symbol-rate 27500000 --pol $pol -T 2 >/dev/null 2>&1
    if [ $? -eq 0 ]; then
      echo "LOCK: $f MHz pol $pol"
    fi
  done
done
echo "fin $(date +%H:%M:%S)"
