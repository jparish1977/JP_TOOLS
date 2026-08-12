# Field notes: recovering from failing drives

Written after an evening spent on four drives at once, three of them with
something wrong. Every number here is measured, not remembered. The tools in
this repo do the work; this is the judgement that decides *which* work.

The single most useful habit: **a check that has not itself been checked is a
confident source of wrong answers.** Most of the mistakes below produced
plausible output that happened to be false.

---

## Order of operations

1. **Inventory** — names and sizes only. Costs directory seeks, not transfer.
2. **Size** what you might want (`ntfs-size.py`). Decide *before* committing.
3. **Compare** against what you already have. This is where the big wins are.
4. **Copy**, highest value first, one pass.
5. **Repair** — and only ever the copy.

Steps 1–3 are nearly free and routinely change the answer to step 4. On this
job they cut a planned 1.1 TB rescue down to 393 GB, because comparing
inventories showed the Wii and PS1 collections already existed elsewhere.

**Never repair before copying.** `fsck`, `ntfsfix`, `chkdsk` all *write*, and
writing to a disk with pending sectors is how a recoverable situation becomes an
unrecoverable one. A read-only check (`fsck.vfat -n`) is fine and tells you
whether the damage is real or just a dirty flag.

---

## Checklist

Work through it in order; the cheap steps keep changing the answer to the
expensive ones.

**Assess — before touching anything**

- [ ] `smartctl -d sat -a` on every drive involved (`-d sat` for USB bridges)
- [ ] Read `Reported_Uncorrect` and `Current_Pending_Sector`, not `PASSED`
- [ ] Check `worst` vs `value` — a moving count means active damage
- [ ] Decide: platter failure, or bridge/cable? (`Medium Error` vs `DID_NO_CONNECT`)
- [ ] Is the enclosure shuckable? Hardware-encrypting bridges are not

**Catalogue — costs seeks, not transfer**

- [ ] `inventory-drive.py` if it mounts; `ntfs-inventory` style listing if not
- [ ] `ntfs-size.py` on anything you might want, before deciding to take it
- [ ] Inventory the destinations too, and diff — most of a rescue is often redundant
- [ ] Identify what exists in exactly **one** place; that is the real payload

**Copy — one pass, highest value first**

- [ ] Somewhere with room, checked in advance
- [ ] Source mounted **read-only**, or read via `ntfsls`/`ntfscat`
- [ ] Errors logged and skipped, never fatal
- [ ] Irreplaceable before large: order by "can I ever get this again"
- [ ] Monitor for a stall, and distinguish "finished" from "died"

**Verify — before trusting or deleting**

- [ ] Compare **bytes** against the source listing; counts alone mislead (below)
- [ ] Check the error log; know exactly what was lost
- [ ] Hash-verify anything whose original you intend to delete
- [ ] Confirm a **second copy exists on different hardware**
- [ ] Verify a rescue someone else did, too — a partial one looks complete

**Only then**

- [ ] Read-only filesystem check (`fsck.vfat -n`, `ntfsfix -n`) to see if damage is real
- [ ] Any repair runs against the copy, never the original
- [ ] Record what failed and why, while it is still fresh

---

## Reading SMART properly

**`PASSED` is close to meaningless.** It means no pre-fail attribute has crossed
its own threshold. Attributes that matter most often have a threshold of 0, so
they can never trigger it.

The four drives, side by side — the failing one is not the one with the most
reallocations:

| | Seagate 2 TB | FreeAgent 1.5 TB | ThinkCentre 1 TB | WD Passport 2 TB |
|---|---|---|---|---|
| Reallocated_Sector_Ct | 204 | **397** | 0 | 0 |
| Current_Pending_Sector | **84** | 0 | 0 | 0 |
| Reported_Uncorrect | **3,751** | 0 | 0 | 0 |
| Power_On_Hours | — | 29,712 | 11,716 | 10,354 |
| verdict | **dying** | old, stable | fine | fine |

- **`Reported_Uncorrect` is the number to look at.** It counts reads the drive
  handed back as failures. 3,751 with a normalised value floored at 1/100 is a
  drive actively losing data. 397 reallocations with *zero* uncorrectables is a
  drive that had a bad patch, remapped it, and stabilised.
- **`Current_Pending_Sector` is the leading indicator** — sectors it cannot read
  *now* and has not yet remapped. Watch whether `worst` differs from `value`;
  that means the count has been moving, so the damage is active.
- **Reallocated alone says little.** It is history, not prognosis.

### Raw values lie, per vendor

**Seagate `Seek_Error_Rate` packs two fields into one number**: the top 16 bits
are an error count, the bottom 32 a total. A raw value of 8,598,366,419 decodes
to **2 errors in 8,431,827 seeks** — negligible, not eight billion failures.
Hitachi's `Raw_Read_Error_Rate` of exactly 65536 (2^16) is the same class of
artefact. Trust the **normalised** value against its threshold; treat raw values
as vendor-specific until decoded.

### Portable drives

`Load_Cycle_Count` is the one that kills 2.5" USB drives — aggressive head
parking against a ~600k rating. 36,403 is nothing; 500,000+ on a five-year-old
drive is the real story even with zero bad sectors.

---

## Is it the disk, the bridge, or the cable?

USB enclosures fail more often than the drives inside them, and the symptoms are
completely different from platter failure.

**Platter/medium failure** — the drive is present and answering, individual
reads fail:

```
Sense Key : Medium Error [current]
Add. Sense: Unrecovered read error
critical medium error, dev sdb, sector 6811649
```

**Link/enclosure/cable failure** — the device disappears entirely:

```
usb 1-2: USB disconnect
usb usb1-port2: Cannot enable. Maybe the USB cable is bad?
usb usb1-port2: unable to enumerate USB device
Synchronize Cache(10) failed: hostbyte=DID_NO_CONNECT
```

`DID_NO_CONNECT` means the device went away, not that a read failed. The kernel
literally suggests the cable, and it is right often enough to check first. A
drive that drops **under load** while reporting clean SMART is usually a
marginal power supply or a tired bridge board, not a dying disk.

Fix order: different port (direct, not through a hub) → different cable →
reseat power → shuck and use SATA.

**Except you cannot shuck everything.** WD My Passport and similar do AES **in
the bridge chip**, always on, even with no password set. Pull the drive out and
a SATA controller sees noise. If the bridge dies the data goes with it, so for
those the cable is the *only* cheap fix and a second copy is the only real
insurance.

---

## When NTFS will not mount

Read the actual error. These are different problems:

```
Failed to read NTFS $MFT     -- the file table. Serious.
Failed to read NTFS $Bitmap  -- the cluster allocation map.
```

**`$Bitmap` is only needed to allocate space.** ntfs-3g reads it at mount time
regardless, but *nothing* needs it in order to read an existing file. So a
volume can be completely unmountable and still fully readable by tools that
parse the `$MFT` directly:

- `ntfsls` — list a directory
- `ntfscat` — extract one file
- `testdisk` — interactive browse and copy

That is the entire basis of `ntfs-extract.py`. On the drive above, mounting
failed every time while **356 GB across 1,183 files** came off cleanly.

`ro,norecover` is worth one attempt — it skips journal replay, which is what
usually blocks a dirty volume — but it does not skip `$Bitmap`.

---

## Comparing drives without attaching both

The highest-leverage step, and the cheapest. `inventory-drive.py` records
path/size/mtime; two inventories then diff offline.

**Match on size multisets, not filenames.** Names differ across collections for
the same content; sizes rarely collide by accident for large media files. Beware
that per-title subdirectories often use the same inner filename (`game.iso`),
so key on the full path or on sizes — keying on basename collapses hundreds of
titles into a handful and produces a confidently wrong comparison.

Outcome on this job:

| | on the failing drive | already elsewhere? |
|---|---|---|
| Wii, 592 GB | 279 images | **yes** — 209 size-identical on another drive |
| PS1, 121 GB | 230 images | **yes** — 226 of 230 (98%) |
| GameCube, 393 GB | 359 images | **no** — only 5 elsewhere |

Two thirds of the planned copy was unnecessary. Size-matching is strong evidence
rather than proof; hash before *deleting* anything, but it is more than enough
to decide what to rescue first.

### Size matching has a precondition: the same format on both sides

The advice above holds only while both collections store the content the same
way. It fails completely otherwise, and the failure is silent — every title
reads as unique.

A later pass compared a LaunchBox library of raw `.iso` against the same games
held as `.chd` elsewhere. **CHD is compressed, so no size will ever agree**, and
there was no disc ID in the filenames to fall back on. Names were all that was
left, and names across ROM collections need real normalisation:

- region and language tags — `(Europe) (En,Fr,De,Es,It)`, `[SLUS_21613]`
- TOSEC version tokens — `Crazy Taxi v1.005 (2000)(Sega)(US)[!]`
- the `", The"` suffix convention — `Sopranos, The - Road to Respect` against
  `Sopranos - Road to Respect`
- disc numbers, which must **survive** normalisation, or the discs of a
  multi-disc game collapse into one entry

Each of those, left unhandled, produced a plausible number pointing the wrong
way. The version token alone made **every single** Dreamcast title fail to match
against a set that contained most of them.

Verdict: use sizes when formats agree, names when they do not, and **hashes when
neither works** — one collection was named purely by number (`gba/0458.gba`),
where no amount of normalisation can help.

### Pick identity fields deliberately

Where a stable identifier exists in the path, it beats both. Wii images carry
Nintendo's six-character disc ID in the directory name (`Zoo Hospital [RZHE5G]`),
which made a three-way comparison exact rather than probabilistic — and showed
two of the three libraries were strict subsets of the third, removing 494 GB
from the migration.

Two counting traps came with it. **A `.wbfs` over 4 GB is split into `.wbf1`,
`.wbf2`…** — continuations of one disc, so counting files counts them twice.
And **trimmed formats legitimately differ in size between good copies**: one
drive's images were smaller on 73 discs and larger on none, which is also what
truncation looks like. What distinguished them was that every image on every
drive was a whole number of 2 MiB wbfs blocks — an interrupted copy lands on an
arbitrary offset, not a block boundary.

---

## Verifying a copy: compare bytes, not file counts

Two failures here look identical from a distance, and they point opposite ways.

**A complete copy can be short on file count.** Extractors commonly skip
zero-byte files — `ntfs-extract.py` does, via its minimum-size filter. Three
directories came back short by exactly **1, 10 and 7 files** while the byte
totals matched to the byte. That reads as data loss and is not:

    Pictures            582 files   7,099,491,553 B   exact
    Desktop              55 of 56   561,130,277 B     exact  <- 1 zero-byte file
    projects          1,395 of 1,405  141,239,822 B   exact  <- 10 zero-byte files
    Batocera.PLUS-bios  595 of 602   394,374,931 B    exact  <- 7 zero-byte files

Count the zero-byte entries in the source listing before concluding anything.
If they account for the gap and the bytes match, the copy is complete.

**An incomplete copy can look finished.** A rescue done in an earlier session
had put three trees on a server. Two were byte-exact. The third was not:

    music   9,427 files  31,228,325,650 B  ->  9,686 files  31,228,327,574 B  complete
    porn      139 files   6,362,374,325 B  ->    173 files   6,362,378,413 B  complete
    mame   37,403 files  66,721,041,858 B  -> 35,102 files  66,086,878,805 B  634 MB SHORT

From a directory listing all three look done. Only the byte comparison shows
2,301 files and 634 MB missing from the third. **A partial rescue is the most
dangerous kind, because it reads as complete** — and the destination having
*more* files than the source (zero-byte entries again) makes a naive count
comparison actively misleading in both directions.

---

## Throughput: it is usually the destination

The instinct on a slow rescue is to blame the failing drive, and then to move it
to a different machine. Measure the link speeds first — `lsusb -t`, and the
`speed` file in the device's sysfs path:

    failing drive   /dev/sdb   usb2/2-1   5000 Mbps    <- full USB 3.0
    destination     /dev/sdc   usb1/1-2    480 Mbps    <- USB 2.0, ~33 MB/s
    over wifi       5 GHz                              <- 12 MB/s measured

The dying drive was never the constraint. And note the destination at USB 2.0 is
still **2.75x faster than the network**, so "stage it to the fast server
instead" would have made the rescue slower, not faster. Keep rescue targets on
local USB; move data machine-to-machine afterwards.

A USB 3.0 drive negotiating at 480 Mbps is usually the **cable**. WD My Passport
and similar use a USB 3.0 Micro-B socket, and an ordinary USB 2.0 micro cable
fits the left half of it and works — at USB 2.0 speed, with nothing to suggest
anything is wrong.

**Do not relocate the failing drive to another machine** unless you have
measured that it is the bottleneck. It costs a power cycle, and a marginal drive
failing to spin back up is a normal way for these to end. It also usually costs
you your tooling: an unmountable NTFS volume is readable from Linux via
`ntfsls`/`ntfscat` and not much else, so moving it to a Windows box trades a
working rescue for a dead end.

---

## Things that produced confident wrong answers

Each of these looked like a result:

- **`dmesg | wc -l` to detect new events.** The ring buffer was full, so new
  messages pushed old ones out and the count never changed. Reported "no kernel
  activity" while a drive attached successfully.
- **A snap flooding the kernel log.** MongoDB's `ftdc` thread hit an apparmor
  denial three times a second, so *any* device-attach message aged out of the
  buffer within minutes. `dmesg` was useless on that machine until it was
  disabled; `journalctl -k --since` was not.
- **`pgrep -f "somethin[g]"` matching its own command line** when the same string
  appeared in another argument of the same command. Write the pid to a file and
  kill by pid.
- **`ps comm` truncating at 15 characters** — grepping for a 16-character process
  name finds nothing and the process looks dead while it is serving traffic.
- **A tool that is not installed returns empty, which reads exactly like a clean
  negative.** An `xwininfo` window count reported zero throughout — because
  `xwininfo` was absent. The giveaway was that the *baseline* sample was also
  zero when it certainly should not have been. Always include a case you know
  should be non-empty.
- **`Permission denied` and `I/O error` look identical** through a failed
  command. Check which one you got before concluding the disk is unreadable.
- **A shell glob missing case variants** — `*.v64` found 84 files where a
  case-insensitive match found 88.

---

## Non-obvious environment traps

- **A flatpak has its own `/tmp`.** Writing a report there leaves it invisible to
  the host. Write to a path the sandbox explicitly shares.
- **`which php` finds nothing when PHP is a flatpak.** "Not installed" and "not
  on PATH" are different claims.
- **FAT32 caps a single file at 4 GB**, so it cannot hold a disk image of
  anything meaningful — fine for many small files, useless for one big one.
  The cap also leaves a **signature worth recognising in old data**: a file of
  exactly **4,294,967,295 bytes** (2^32-1) was truncated when it crossed a FAT32
  volume at some point in its history. Five DVD images on one drive shared that
  byte count exactly, and they were the only titles that appeared unique in a
  library comparison — so they read as "the last five things worth rescuing"
  when in fact they were the five things already broken. Sort candidates by size
  and look for repeated identical values near a power of two.
- **Filesystems flagged dirty after a link drop** may have no real damage. A
  read-only check distinguishes the two; a repair pass assumes the answer.
