#!/bin/sh
# Let the PerfArena runner's group read the RAPL energy counters. The kernel
# makes them root-only (0400) at every boot, and without read access the
# runner's energy falls back to CodeCarbon's estimate (about 12 W, not measured).
# Group read, not world read, limits exposure to the RAPL power side channel
# (CVE-2020-8694). perfarena-rapl-grant.service runs this script at boot.
#
#   PERFARENA_RAPL_GROUP   the group of the user that runs the runner (default: perfrunner)
GROUP=${PERFARENA_RAPL_GROUP:-perfrunner}
for f in /sys/devices/virtual/powercap/intel-rapl/intel-rapl:*/energy_uj \
         /sys/devices/virtual/powercap/intel-rapl/intel-rapl:*/intel-rapl:*/energy_uj; do
  [ -e "$f" ] && chgrp "$GROUP" "$f" && chmod 0440 "$f"
done
