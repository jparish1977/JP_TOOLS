# JP_TOOLS Methodology

*Framework-agnostic architecture, distributed as discipline.*

This is the manual for the toolbox. JP_TOOLS isn't a library, it's a set of practices that travel across languages, frameworks, problem domains, and mediums. The practices are for shipping anything: portfolio sites, research visualizers, interactive fiction, data-recovery tools, archive management, generative art, spoken-word pieces with live-rendered backdrops, hardware driver abstractions.

If it's software you expect to survive the decade, this is how it should be built.

---

## 1. Thesis

**The methodology is the asset. Everything downstream is replaceable.**

Over decades of building things that outlast their first framework, a fractal engine that predates WebGL, art archives that survived multiple blog platforms, tooling that's been ported through three languages, one pattern has held: if the domain is cleanly separated from the delivery mechanism, the domain survives. If it isn't, the rewrite starts from zero.

The rule: **if it can't be reimplemented in Zend 1.x, it's not abstract enough.**

Domain logic belongs in a framework-zero core. Everything that touches the outside world (databases, filesystems, image libraries, rendering surfaces, HTTP, authentication, audio analysers, GPUs) goes through a port interface defined by the domain, with concrete adapters living outside the domain. When the framework changes (Laravel to whatever comes next, Three.js to WebGPU, disk to S3), you rewrite the adapters. The core doesn't move.

This isn't new theory. It's hexagonal architecture / ports-and-adapters, applied strictly enough that the separation actually survives contact with deadlines.

---

## 2. Principles

These are extracted from the `CLAUDE.md` files in each iteration8-* repo and generalized to apply to any project in any language.

### 2.1 Zero framework dependencies in the domain

Your domain package declares only:
- The language runtime (e.g. `php ^8.3`, `python ^3.11`, `typescript ^5`).
- First-party utility libraries you control (e.g. `iteration8/utilities` may depend on `iteration8/core`).

It declares nothing else. No Laravel, no Symfony, no Flask, no React, no Three.js, no numpy, no ImageMagick. If the domain needs to read a file or render a frame or analyse audio, it declares a *port interface* for "thing that reads files" / "thing that renders" / "thing that analyses audio" and accepts an instance of that interface via constructor injection. Concrete implementations live in the infrastructure layer, not the domain.

### 2.2 Ports define all external boundaries

A port is an interface. It names what the domain needs in terms the domain cares about: `ProjectRepositoryInterface`, `HasherInterface`, `ImageProcessorInterface`, `RendererInterface`, `AudioAnalyserInterface`, `DisplacementFormulaInterface`. It does NOT say "MySQL" or "ImageMagick" or "WebGL" or "FFmpeg."

An adapter is a concrete implementation: `EloquentProjectRepository`, `NativeHasher`, `ImageMagickProcessor`, `ThreeJsRenderer`, `WebAudioAnalyser`, `DeterminantDisplacement`. It lives in the infrastructure or delivery layer, not the domain.

The test: can you swap an adapter without touching domain code? If yes, the port is doing its job. If you find yourself reaching into the adapter's guts from domain code, the port is too thin.

### 2.3 Value objects and entities are immutable, but mind the `final` trap

Constructor-promoted properties (PHP 8.1+), `@dataclass(frozen=True)` (Python), `readonly` modifiers (TS). `toArray()` or equivalent for serialization. No setters. No mutation.

Immutability buys two things: thread-safety by default, and the ability to reason locally about state. If you have a `FileEntry` and you need a modified version, you construct a new `FileEntry`. The old one is still valid. In tests this removes a whole class of false positives ("the test mutated the fixture"). In production it removes a whole class of concurrency bugs.

**The test-suite trap.** Marking these classes `final` (or `final readonly class` in PHP 8.2+, which blocks subclassing even when properties aren't themselves final) has bitten us repeatedly. `final` breaks:

- **PHPUnit `createMock()` / `getMockBuilder()`**, which generate subclasses under the hood. Final classes fatal-error at mock-creation.
- **Test-specific subclasses for IO leaves.** The `protected` + `@codeCoverageIgnore` pattern in §2.6 explicitly relies on subclassing the adapter to fake the IO method. If the adapter is `final`, that escape hatch is gone.
- **Lightweight stubs** in higher-level tests. A service test that wants a cheap stand-in for a dependency may reach for a subclass over constructing the full real object.

**Rules of thumb:**

| Target | `final`? | Why |
|---|---|---|
| Value objects (`FileEntry`, `DuplicateGroup`, data carriers) | **Yes** | Cheap to construct real ones in tests; no test needs to subclass them. |
| Entities (domain objects with identity) | **Yes** | Same, build real ones in tests. If a test needs a fake, the port around the entity's *repository* is the mock target, not the entity itself. |
| Services (constructor takes ports, orchestrates domain logic) | **No** (or yes only if there's a clear reason) | You test these against faked ports. But leaving them non-final preserves the escape hatch for higher-level tests that want to stub one. |
| Infrastructure adapters (hasher, cache, repository impls) | **No** | The `protected` IO-leaf pattern in §2.6 requires subclassing. Keep adapters open. |
| Port interfaces | N/A | Interfaces can't be final in the blocking sense. |

If you find yourself wanting to mock a `final` value object, the smell isn't the `final`, it's that the test is at the wrong abstraction level. Construct the real object. If the real object requires an expensive graph, that's a separate design problem.

If you find yourself wanting to mock a `final` service, drop the `final`. The immutability argument applies to data, not orchestration code.

### 2.4 Constructor injection, no globals, no service location

Every dependency comes in through the constructor. No `ServiceLocator::get()`, no global `Container::resolve()`, no module-level mutable state. The class declares what it needs; the caller (usually a service provider or composition root) wires it up. This makes the code trivially testable, since fakes are just constructor arguments.

### 2.5 Quality gates are not optional

PHPStan level 8 / mypy strict / tsc strict. PHPCS PSR-12 / ruff / eslint. PHPUnit / pytest / jest. Coverage threshold at 95%+, and the iteration8 core package is at 100%.

Enforcement is mechanical: pre-commit hook (`python ~/JP_TOOLS/install-hooks.py /path/to/repo`) runs `check.py` before every commit. CI (`python ~/JP_TOOLS/init-ci.py /path/to/repo`) runs the same checks on push and PR. A failed check blocks the commit and the merge.

These gates are not tech-debt mitigation. They are architectural enforcement: if a piece of code can't pass static analysis at level 8, it is probably structured wrong.

### 2.6 IO leaves are allowed to be untested

At the boundary between your code and the OS, there's always a line that actually opens the file / dispatches the HTTP request / invokes the subprocess / writes to the GPU. Don't chase 100% coverage through that line with brittle mocks. Instead, factor the adapter so the IO-touching method is a thin `protected` wrapper tagged `@codeCoverageIgnore` (`pragma: no cover` in Python), and unit-test subclasses that fake it.

Business logic stays at 100%. OS-level error handling is acknowledged as an integration concern, not a unit-test concern.

### 2.7 The silence rule

In domain code: when a field or feature is absent, let it be absent. No placeholder strings, no "no description available." Absence is part of the design. This comes from the portfolio aesthetic ("the column exists but goes quiet... no placeholder text... the silence is the content") and applies equally to APIs, CLIs, and internal data models.

---

## 3. The canonical exemplar: FileScanner

`FileScanner` is the reference implementation, and everything in iteration8-utilities and the quality-tooling scripts follows this pattern. It's in PHP, but the pattern translates directly to any language.

**Where it lives.** FileScanner is *not* in this repo. It ships in `iteration8/utilities` under the `Iteration8\Utilities\FileScanner` namespace, and JP_TOOLS consumes it through composer as an optional dependency (`composer require iteration8/utilities:dev-master`). `find-dupes.php` here is the composition root that wires it up. Keeping the library out of the toolbox is itself the methodology working: the toolbox depends on the contract, not on a vendored copy.

```
Iteration8\Utilities\FileScanner\
├── Contract/                    # PORTS
│   ├── HasherInterface            # "something that hashes a file"
│   ├── FilesystemInterface        # "something that lists files"
│   ├── HashCacheInterface         # "something that stores hashes"
│   ├── OutputInterface            # "something that emits results"
│   ├── ScannerInterface           # "something that scans"
│   ├── SchedulerInterface         # "something that runs work"
│   └── DuplicateFinderInterface
├── Hasher/                      # ADAPTERS for HasherInterface
│   ├── NativeHasher               # PHP hash_file(), cross-platform
│   └── ShellHasher                # md5sum / sha256sum, parallel-friendly
├── Cache/                       # ADAPTERS for HashCacheInterface
│   ├── MemoryCache                # in-memory, single-run
│   ├── FilesystemCache            # file-per-hash on disk
│   └── SqliteCache                # persistent DB with dupe queries
├── Filesystem/LocalFilesystem   # adapter for FilesystemInterface
├── Output/{Console,Json}Output
├── Scheduler/
│   ├── SequentialScheduler        # one-at-a-time default
│   └── WorkerPoolScheduler        # proc_open parallel
├── Scanner                      # orchestrator, uses ports only
├── DirectoryWalker
├── FileHasher
├── IgnoreFilter
├── DuplicateFinder
├── FileEntry                    # value object (final readonly)
├── DuplicateGroup               # value object
└── ComparisonResult             # value object
```

**Read the structure:** `Scanner` accepts `HasherInterface`, `HashCacheInterface`, `SchedulerInterface`, `OutputInterface` via its constructor. It never imports `NativeHasher` or `SqliteCache`. Changing from single-threaded MD5 on disk to parallel SHA-256 in memory is a wiring change at the composition root, not a code change in `Scanner`.

**Read the tests:** the scanner test fakes the ports. The hasher and cache tests exercise each adapter against the port contract. No integration test needs the entire OS.

**Read the CLI entry point:** `find-dupes.php` is the composition root. It reads CLI flags, picks adapters based on those flags (`--workers 4` gives `WorkerPoolScheduler`, `--db path` gives `SqliteCache`), and hands them to `Scanner`. All framework-specific concerns (CLI parsing, argv handling, output formatting) live here. The library knows nothing about argv.

This pattern is what `iteration8-utilities/CLAUDE.md` means when it says "follows the FileScanner pattern." Use it as the template for any new library or tool.

---

## 4. The layering discipline

For any non-trivial project, this is the minimum layering:

```
┌─────────────────────────────────────────────────────┐
│  DELIVERY  (site, CLI, SDK binding, executable)     │
│  - Framework code (Laravel, Express, argparse, ...) │
│  - Composition root: wire ports to adapters         │
│  - Thin: hand data to the domain, render response   │
│  - DISPOSABLE. If the framework dies, rewrite this  │
└─────────────────────────────────────────────────────┘
              │ depends on
              ▼
┌─────────────────────────────────────────────────────┐
│  INFRASTRUCTURE  (utilities, adapters)              │
│  - Concrete implementations of domain ports         │
│  - Talks to database, filesystem, network, GPU, etc │
│  - Hexagonal internally (FileScanner pattern)       │
│  - Reusable across delivery variants                │
└─────────────────────────────────────────────────────┘
              │ depends on
              ▼
┌─────────────────────────────────────────────────────┐
│  DOMAIN  (core)                                     │
│  - Entities, value objects, services                │
│  - Port interfaces (the contract with the world)    │
│  - Zero framework dependencies                      │
│  - THE ASSET. Survives everything else.             │
└─────────────────────────────────────────────────────┘
```

Dependencies flow downward only. Domain depends on nothing but the language. Infrastructure depends only on domain (for port interfaces). Delivery depends on both.

**Cycles are a smell.** If you find infrastructure importing from delivery, or domain importing from infrastructure, one of two things is true: (a) you've placed the code in the wrong layer, or (b) the port interface is missing or too narrow.

### Reference application: the iteration8 stack

- `iteration8-core`, the domain. 100% coverage. PHP 8.3 plus nothing. Entities (`Project`, `Piece`, `BlogPost`), port interfaces (`ProjectRepositoryInterface`, `AuthenticatorInterface`, `SessionInterface`, `ImageProcessorInterface`), services.
- `iteration8-utilities`, the infrastructure. 97.7%+ coverage. FileScanner, ImageProcessor (ImageMagick adapter). Depends only on core's ports.
- `iteration8-site`, the delivery. 99.2% coverage. Laravel 12. `IterationServiceProvider` binds ports to Eloquent/Laravel adapters in `Infrastructure/`. Thin controllers, Blade views, REST API.

If Laravel is ever replaced, core and utilities don't move. You write new adapters in a new delivery package.

### Reference application: the JP_TOOLS toolbox itself

This repo is methodology applied to itself. `find-dupes.php` is a delivery layer over the FileScanner library in `iteration8/utilities`, and it depends on that package's contracts rather than vendoring a copy. `check.py` and `fix.py` are language-agnostic delivery layers that dispatch to adapters per language (`ruff` for Python, `phpstan` for PHP, `eslint` for JS). `install-hooks.py` and `init-ci.py` are the propagation mechanism: they install the methodology into other repos.

---

## 5. Applying the methodology across mediums

The principles are medium-agnostic. The same structure works for:

### Portfolio & content CMS (iteration8-site)
Domain = pieces, projects, blog posts, voice text, piece layouts derived from aspect ratio. Ports = repositories, image processor, authenticator, session. Adapters = Eloquent, ImageMagick, GitHub OAuth, Laravel session. Delivery = Laravel + Blade + a carefully-constrained CSS token system where `tokens-base.css` / `tokens-theme.css` / `typography.css` are the PORT (plain CSS, survives any framework) and Tailwind `@theme` in `app.css` is the ADAPTER (disposable). `tools/lint-css-layers.sh` fails commits that leak hex colors outside the theme tokens.

### Fractal research engine (MandelbrotExplorer)
Domain = pure 2D Mandelbrot iteration `z = z² + c` (never modified) and its 2D escape trajectory. Ports = displacement formulas (`escapingZ`, `cloudLengthFilter`, `cloudIterationFilter`, `dualZMultiplier`, `particleFilter`), rendering backend, audio analyser (planned). Adapters = concrete displacement implementations (magnitude / determinant / angular / sine), Three.js/WebGL renderer, future non-Three.js renderer, future `AudioAnalyserAdapter` feeding GLSL uniforms. Delivery = the web UI, eval-compiled formula slots editable live.

The novelty of MBE is that the displacement formula IS a port: each formula is a research hypothesis about what structure is hidden in the 2D data, and swapping them is a one-line change. The domain math doesn't move. The hypothesis-testing methodology becomes the structure of the code.

### Generative art / living simulation (Brots Alive)
Domain = pure 2D Mandelbrot + cellular automata + wave equation. Ports = rendering backend, audio sampling point, clock-to-iteration coupling, displacement formulas applied to bodies-as-skins. Adapters = matplotlib (current POC), future Three.js / native / VR. The Python POC (`~/projects/brots-alive/`) is a delivery layer disguised as a research sandbox: same math core as MBE, different rendering aesthetic.

### Interactive fiction (Telnet Dungeon Crawler)
Domain = game state, player session, room graph, message bus. Ports = input/output, session store, persistence, broadcast. Adapters = telnet protocol, WebSocket protocol, in-memory store, SQLite persistence. Delivery = the systemd service + Apache WS proxy + web client, OR raw telnet. Two delivery surfaces on the same domain. (The WS disconnect bug that cost a session to fix was exactly a missing port: session lifecycle cleanup was entangled with WS lifecycle, and factoring that out is the hexagonal move.)

### Data recovery & archive management (JP_TOOLS recovery tools)
Domain = file integrity, hunk-level dedup, provenance tracking. Ports = raw disk access, hash algorithm, CHD operations, photorec profile. Adapters = `ddrescue`, native PHP hashing vs shell hashing, `chdman`, photorec. Delivery = `recover.py`, `image-disk.py`, `chd.py`, `undelete.py`. Each delivery is a thin composition root picking adapters per invocation.

### Spoken-word work (planned convergence with MBE)
Domain = an audio artifact + optional transcript/score + optional reactive visual. Ports = audio source, audio analyser (FFT / envelope / onset), visual renderer, transcript renderer. Adapters = Web Audio analyser, MBE as visual adapter (with audio feeding GLSL uniforms), plain-text transcript. The iteration8 design doc explicitly flags this: *"the visualizer is MBE with audio as a new input source, not a separate tool."* The port/adapter discipline is how that convergence happens without retrofitting: the `AudioAnalyserAdapter` slot is planned into MBE's module boundaries now, years before the spoken-word visualizer ships.

### Hardware rehab / firmware tooling
Domain = device capabilities + driver abstraction + boot-mode state. Ports = USB / ACPI / fingerprint sensor / webcam / touchscreen driver. Adapters = kernel modules, userspace drivers, vendor blobs. Delivery = installer scripts, systemd units. The U810 project, SA510 firmware rescue, and retro-VM consolidation on x3550 Proxmox all fit this pattern, because hardware is another framework.

---

## 6. Cross-language application

The methodology isn't PHP-specific. Pattern translation:

| Concept | PHP | Python | JS / TS |
|---|---|---|---|
| Port interface | `interface HasherInterface` | `class HasherInterface(Protocol)` or ABC | `interface HasherInterface` |
| Value object | `final readonly class FileEntry` | `@dataclass(frozen=True)` | `readonly class FileEntry` |
| Constructor injection | Constructor-promoted properties | `__init__` with typed args | Constructor with typed params |
| Composition root | Service provider | `main()` / factory module | App entry point |
| Adapter | Class implementing interface | Class implementing protocol | Class implementing interface |

The quality-tool translation (what `check.py` dispatches to):

| Language | Static | Style | Tests |
|---|---|---|---|
| PHP | PHPStan level 8 | PHPCS PSR-12 | PHPUnit |
| Python | mypy strict | ruff | pytest |
| JS/TS | tsc strict | eslint + prettier | vitest / jest |
| CSS | stylelint | stylelint | |

Principles are language-agnostic. Tools are language-specific. `check.py` auto-detects the language and runs the right tool stack.

---

## 7. Starting a new project

Checklist for greenfield work that should inherit this discipline:

1. **Three-or-fewer packages.** Minimum: domain. If the project has IO, add infrastructure. If the project has a UI or CLI, add delivery. Don't start with one mega-package that mixes layers.

2. **Declare zero framework deps in the domain package.** Check `composer.json` / `pyproject.toml` / `package.json` after scaffolding.

3. **Port interfaces before implementations.** Sketch the interfaces first. The first adapter can be `InMemoryFooRepository`, good enough to run the domain tests without touching a real database.

4. **Install the hook**: `python ~/JP_TOOLS/install-hooks.py /path/to/repo`. Commits fail until the code is clean.

5. **Install the CI**: `python ~/JP_TOOLS/init-ci.py /path/to/repo`. PR blocks on the same gates.

6. **Set coverage gate to 95%** in your test config. Use `@codeCoverageIgnore` / `pragma: no cover` sparingly and only on IO leaves.

7. **Write the CLAUDE.md** for each package. Copy from `iteration8-core/CLAUDE.md` and adapt. This is the prescriptive doc that future-you and future-collaborators read before touching the code.

8. **Composition root is a single file.** Service provider in Laravel, `main()` in Python, app entry in JS. One place where "this project uses these adapters" is declared.

---

## 8. Adopting this in a codebase you inherited

§7 is for a repo that starts clean. This section is for the case that covers
most working days: a codebase that is already valuable, already load-bearing,
has never met the bar, and cannot be stopped while it learns.

Written from batocera-watch: 14,000 lines across 31 Python scripts and 11 PHP
files, four separate products sharing one directory, one test, and a 604-line
`CLAUDE.md` of accumulated traps. `python3 check.py .` reported **924 errors**.
Every rule in §7 assumes a gate that can be switched on in a single move, and
two separate plans for that repo were wrong before this section existed, both
of them because they treated adoption as an event rather than a rate.

1. **Make the gate incremental before you make it mandatory.** A whole-repo
   gate on a repo with 924 pre-existing errors has two possible outcomes: a
   cleanup nobody scheduled, or `--no-verify` as a daily habit, which the
   anti-patterns section lists for good reason. Check the **staged files**, so the only
   code that has to meet the bar is code you were already editing. The number
   then falls as a side effect of doing the work, and no calendar entry is
   required.

   This was a defect in the toolbox itself, fixed in `9fa6803`.
   `install-hooks.py` stated in its own docstring that it "runs check.py
   against staged files", collected them into `$STAGED`, used that list only to
   test whether anything was staged at all, and then ran `check.py .` against
   the repo root. On greenfield the difference is invisible, because the whole
   tree is clean either way. On anything inherited it is the difference between
   an on-ramp and a wall.

   It is left recorded here rather than deleted, because the same mismatch came
   straight back through step 2: the hook was fixed to measure per-file while
   the instruction for recording a baseline still said to measure the whole
   repo. Fixing one half of a scope mismatch leaves the other half looking
   correct.

2. **Record the baseline the same way the gate measures, and hold the line at
   "no worse".** Ratcheting needs a number to ratchet against. Commit a
   **per-file** run — `check.py --record-baseline` — and have the gate compare
   against it rather than against zero. Without that stored number, a file that
   got worse and a file that was always bad are indistinguishable, so no one
   can tell progress from noise and the effort stops being visible to anyone
   including you.

   **Per-file, because that is what step 1 made the gate do**, and the two
   measurements are different quantities in the same units. This sentence used
   to read "commit the output of a full run", which is how the batocera-watch
   baseline came to be recorded at the repo root while the hook it had to be
   compared against ran file by file. The result looked exactly like tool
   drift: 2472 recorded against 2870 measured, +398 across seven files that
   nobody had touched since the baseline was taken.

   The signature is worth knowing, because it will happen again to someone.
   Only the tools that **resolve across files** move between the two modes —
   `mypy` and `phpstan`. The ones that judge a file in isolation — `ruff`,
   `phpcs`, `rector` — were byte-identical on every file. If a baseline
   disagrees with a fresh run on exactly the import-sensitive tools and on
   nothing else, suspect scope before you suspect versions.

   Note also that "a full run" reads two ways, and both are natural: a run over
   the full **repo**, and a full run of every **tool** rather than a partial
   one. Whoever records a baseline tends to read it the first way, which is
   also the way that produces the larger and more impressive number, and
   nothing downstream contradicts them. Say per-file, and say why, or it gets
   helpfully "improved" back.

   A per-file baseline is the **worse** measurement for `mypy` and `phpstan` —
   in isolation they cannot resolve imports and report more. That is the right
   trade anyway: the gate can only compare against what it can reproduce, and
   the excess is real findings rather than noise. On `audit_roms.py` the extra
   172 were `no-untyped-call` and `no-untyped-def`, not import errors.

3. **Find out which checks are not running before believing the total.** Those
   924 errors covered Python only. `phpstan`, `phpcs` and `rector` all reported
   `unavailable` and contributed zero, so 11 PHP files were entirely unmeasured
   while the summary read as authoritative. An inherited repo will be missing
   toolchains that a greenfield one installs on day one. A check that did not
   run looks exactly like a check that passed, and that costs more here than
   anywhere else, because on unfamiliar code you have no intuition to
   contradict the number.

4. **Map the dependency graph before planning the order.** One grep for
   cross-imports settles whether piecemeal is cheap or a fantasy. In
   batocera-watch, 31 scripts held **three** internal edges between them, which
   made extraction close to free; both plans drawn before that measurement had
   assumed it would be expensive and were built around avoiding a cost that
   did not exist. Measure the coupling first. It is the input that decides the
   entire strategy, and it takes one command.

5. **Let the work choose the order.** The component that most deserves a
   rewrite and the component you have open are rarely the same one, and only
   the second rewrite is free, because you were paying to understand that code
   anyway. On a 400-line script the gap between editing it and rebuilding it
   properly is small. `git log --since=<a month ago> --name-only` names the
   real order in one command: in batocera-watch the finished, quiet component
   was proposed first, while the four files carrying 26 commits in the previous
   month were proposed last.

6. **One component leaves at a time, and the tree runs between every step.** No
   freeze, no big-bang split, no branch that lives for a month. If a
   component's rewrite cannot be finished in a sitting, the unit is too large;
   split it again until it can. The test of a good unit is that abandoning the
   project immediately after it lands leaves the repo in a better state than
   before, with nothing half-migrated.

7. **Extract when you touch, never on a schedule.** The new repo gets created
   the day real work lands on that component, with the hook and CI on from its
   first commit. Six months later, whatever never moved is precisely what
   nobody needed, and the effort spent on it is correctly zero. A migration
   plan that names all components up front commits you to finishing the ones
   that turn out not to matter.

8. **A component leaves by moving, and leaves nothing behind.** Copying it
   forward produces two versions that both keep receiving fixes.
   `ntfs-inventory.py` exists in both JP_TOOLS and batocera-watch, diverged by
   119 lines, each copy holding a repair the other lacks: one has the usage
   block and the repo's first test, the other has the fix for the 285,471 sudo
   invocations that wrote 857,000 lines into `auth.log`. Neither is the version
   to keep, which is the cost of having allowed two. This is the vendoring
   anti-pattern arriving through the side door, one convenient copy at a time.

9. **Treat the gotchas document as a coverage report.** A long file of "do not
   step here" is a list of defects that no test was in a position to catch, so
   the mitigation had to live in prose and be re-read by a human before every
   session. batocera-watch's ran to 604 lines. Each entry is a test case
   waiting for a seam: `/runningGame` returns 201 when idle, the audit cache
   keys on `(size, mtime)` so a fix outside the file replays the old verdict,
   `ok` means two different things in the same summary. Port them into the
   suite as the components move, and let the shrinking of that file be the
   measure of whether the seams actually arrived.

10. **Say in the README which packages meet the bar and which are
    grandfathered.** Adoption is a rate, so at any moment part of the repo does
    not comply, and a reader cannot tell an exempt package from a neglected one
    by looking. Name them. JP_TOOLS carries its own "this repo does not yet
    meet its own bar" section for the same reason: a gap that is written down
    is a decision, and a gap that is discovered is a surprise.

11. **When a component cannot be extracted, strangle it instead.** Some
    components are one program rather than a collection, and there is no
    version of "take a third of it out" that leaves the tree running. The
    strangler fig pattern (Fowler, 2004) routes call by call: find a point
    where the program already dispatches, send one case to a new
    implementation, leave the rest on the old path, and repeat until the old
    path has no callers and can be deleted. The whole technique rests on
    finding that dispatch point, and everything after it is bookkeeping.

    Both of batocera-watch's large files have one already. `batocerawatch.py`
    switches on `route` in `do_GET` (`/ping`, `/running`, `/audit`,
    `/controls`), so `/manual` can move onto a layered package while
    `/controls` still runs the old module-level functions in the same process,
    serving the same handheld, with no cutover. `audit_roms.py` switches on
    file extension in `structural()`, so `.chd` and `.cue` can move behind
    ports one format at a time. Deleting the host is part of the pattern, not
    an optional finish: a strangler that never completes leaves two systems and
    a facade, which is three things to maintain instead of one.

12. **Pin current behaviour before rewriting it, including the parts that look
    wrong.** A characterisation test (Feathers, *Working Effectively with
    Legacy Code*, 2004) asserts what the code does today rather than what it
    should do. It is a tripwire, not a certificate. The mechanic is to write an
    assertion you know is false, run it, read the real value out of the failure
    message, and paste that in as expected, which turns "I cannot test this
    because I do not understand it yet" into a mechanical job. This matters on
    inherited code because some of its surprising behaviour is load-bearing,
    and you will not know which parts until later.

    The gotchas file from item 9 is the shortlist. `/runningGame` returning 201
    when idle is not a note to remember, it is an assertion: rewrite the client
    to read the body instead of the status and the bug comes straight back,
    while the change feels like an improvement. The bulk form, a golden master,
    is often already lying around: a full `audit_roms.py` cache is a recorded
    verdict for every ROM in the library, so freezing a copy and diffing after
    a rewrite turns every changed verdict into a question that has to be
    answered out loud. Two limits. Determinism has to be manufactured first,
    since timestamps, dict ordering and anything with a live box in the loop
    will differ on every run, which is why this suits the structural pass and
    not the 25-minute launch pass. And these tests freeze bugs deliberately, so
    deleting one the day you decide the behaviour was wrong is correct
    behaviour rather than a lapse.

The through-line: **the discipline arrives one file at a time, or it does not
arrive.** Every rule in §7 is affordable on a repo with no history. On a repo
with history, the only budget that reliably exists is the file already open on
the screen, and any adoption plan costing more than that will be abandoned
while still looking, from the commit log, like it is going fine.

---

## 9. Anti-patterns

Things that look like they save time but defeat the methodology:

- **Framework imports in the domain.** `use Illuminate\...` in `iteration8-core` is a bug, not a shortcut. Once it's there, the package is no longer framework-agnostic.
- **Service location inside domain code.** `$container->get(...)` inside a service means the service has a hidden dependency. Constructor inject it.
- **Mutable value objects.** If `FileEntry` has setters, every piece of code that ever saw it is a suspect when state looks wrong. Make it `readonly`.
- **Framework-coupled tests in the core suite.** If `iteration8-core/tests/` needs Laravel to run, the core isn't pure. Move the test to `iteration8-site/tests/`.
- **God adapters.** An adapter implementing six port interfaces at once is a sign the ports are carved wrong. Split the adapter or merge the ports.
- **Vendoring a shared library instead of depending on it.** Copying FileScanner into a consumer would give two divergent copies and no contract. Depend on `iteration8/utilities` and let composer resolve it.
- **Skipping the hook.** `git commit --no-verify` is sometimes legitimate (you're mid-refactor, CI will catch it). Make it rare. If you're doing it daily, the quality gate is misconfigured or the code has accumulated debt that should be paid.
- **One mega-package.** Mixing domain, infrastructure, and delivery in one package makes the layering invisible. The layers are the point.
- **Placeholder content where silence would do.** The aesthetic rule from the portfolio applies to APIs too. Don't synthesize fake data to fill a slot that's meant to be optional.

---

## 10. Related reading

- `~/projects/iteration8-core/CLAUDE.md`, domain layer rules (24 lines, prescriptive).
- `~/projects/iteration8-utilities/CLAUDE.md`, infrastructure layer rules (30 lines), and the FileScanner reference implementation itself.
- `~/projects/iteration8-site/CLAUDE.md`, delivery layer rules, CSS token/adapter pattern (55 lines).
- `find-dupes.php` in this repo, the composition root that consumes FileScanner.
- `~/portfolio_notes/iteration8-design-doc.md`, the full content-and-aesthetic model for the portfolio site, including the silence rule and the planned MBE/spoken-word convergence.
- `~/portfolio_notes/TODO.md`, live task state across all projects.
- `~/mandelbrotexplorer/docs/JULIA_TUNNEL_LINEAGE.md`, displacement formulas as research hypotheses (domain example from MBE).
- `~/projects/brots-alive/CONTINUATION.md`, same-math-different-medium example (domain example from Brots Alive).
- `~/projects/iteration8-continuation.md`, live state of the production stack applying this methodology.

---

*The toolbox comes with a manual. Read it, apply it, improve it.*
