# DRAFT, NOT FILED: chdman createraw --outputparent sometimes never finishes

Kept so this can be filed with MAME (or a fix offered) later. Not sent
anywhere. Everything it cites is in this directory or `tools/lab/`.

---

## chdman: createraw with --outputparent sometimes spins forever; workers idle

**Versions:** 0.289 (built from the mame0289 tag) and 0.264 (Ubuntu 24.04
`mame-tools`). Linux x86_64, 32 hardware threads.

**What happens:** making a small child CHD against a parent now and then
never finishes. The output stays at its 124-byte header; the main thread
uses 100% of a core; every worker thread is idle. Left alone it does not
recover (one run was killed after 8 minutes).

**How often:** with eight runs at once on a 32-thread machine, 13 of 1600
children hung on 0.289 and 7 of 1600 on 0.264. One run at a time on an idle
machine: 0 of 100 (0.264).

**Repro:** a 128 KiB image, and the same image with 7 bytes changed:

```
python3 -c '
base = bytes((i * 5 + i // 7) % 251 for i in range(64 * 2048))
changed = bytearray(base); changed[5000:5007] = b"changed"
open("a.img", "wb").write(base); open("b.img", "wb").write(changed)'
chdman createraw --input a.img --output a.chd --hunksize 2048 --unitsize 512
# repeat, several copies at once; now and then this one never returns:
chdman createraw --input b.img --output b.chd --outputparent a.chd
```

**Where it is (0.289 with symbols, four hangs, all alike):**

- Thread 1, the main thread, is in the compression loop, calling
  `progress()` again and again:

  ```
  #2  clock () at ../sysdeps/unix/sysv/linux/clock.c:31
  #3  progress<char const (&) [50], double, double> () at src/tools/chdman.cpp:970
  #4  compress_common () at src/tools/chdman.cpp:1513
  #5  do_create_raw () at src/tools/chdman.cpp:1946
  #6  main () at src/tools/chdman.cpp:3525
  ```

- All 17 other threads are waiting for work:

  ```
  #10 osd_event::wait () at src/osd/osdsync.h:91
  #11 worker_thread_entry () at src/osd/osdsync.cpp:686
  ```

So the loop keeps getting "still compressing" from `compress_continue`
while no worker is running anything: work that is never picked up, or
completion that is never seen. Full backtraces: `backtrace-0.289-1.txt`
to `-4.txt` (every thread, `thread apply all bt`).

**Not established:** which side loses the wakeup (the CHD compressor's
handling of hunks found in the parent, or the work queue in `osdsync.cpp`);
whether `--numprocessors` changes the rate; whether it happens without a
parent CHD.

---

## Notes for whoever files this or works on a fix

- The repro and rate: `tools/lab/chdman-delta-hang.sh` (`CHDMAN=` picks the
  binary). The backtraces: `tools/lab/chdman-hang-backtrace.sh`, against a
  chdman built with symbols by `tools/lab/chdman-build.sh` (`SYMBOLS=1`).
- The backtrace files here were captured on a WSL2 kernel (their headers
  say so); the 0.264 counts came from both that and a native Ubuntu kernel.
  The home directory in their paths is written as `~`.
- Related upstream, in `CHDMAN-NOTE.md`: MAME Testers #04709 (open, another
  hang site), PR #9006 (a 2021 work-queue wakeup race fix, already in both
  versions).
