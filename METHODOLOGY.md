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

## 8. Before opening a PR

Section 7 inverted. Those are the claims you make at the start of a project;
these are the same claims **verified** at the end of a change, which is the only
point at which anyone finds out whether they were true.

Written from JP_TOOLS PR #24, where fourteen review rounds produced about 112
findings and `ruff` plus `mypy` together caught **none of them**. Every item
below is here because its absence cost a round.

1. **Read your own diff as if someone else wrote it.** Most of what those
   fourteen rounds found was visible in the diff. A reviewer's attention is the
   scarcest thing in the loop and it was spent three times on work that had not
   been read once.

2. **Run the real gate, not a convenient approximation.** `python check.py
   <file>`, not `ruff check <file>`. They disagree: check.py enables a stricter
   ruleset, and "ruff clean" was claimed all session on a file the gate failed.
   Two tools disagreeing about one file is a bug signal on its own.

3. **Read the exemptions**: `python list-exemptions.py <file>`. §7.6 says
   `pragma: no cover` belongs "only on IO leaves". On this branch it covered
   430 lines of 1350, holding 51 branch, loop and try statements, and every
   serious defect came from inside it. The `no-cover` check refuses an
   unjustified branchy exemption, but **a reason is not a justification**: two
   of the first four written here said three words and justified nothing.
   Nothing but a person reading the list catches that.

4. **Confirm CI actually covers the file, by name.** The list in
   `.github/workflows/check.yml` is manual. `spool-audit.py` was merge-ready
   with a green badge while no job in that file touched it, so "CI is green"
   was true and said nothing about the code. Green on a file nobody checks is
   not a pass, it is a check that did not run.

5. **Run the thing itself, including its destructive paths.** Against a real
   fixture, and on a real machine if it touches one. Every real-machine run on
   this branch found something the fixtures did not, because a fixture you
   invented can only contain bugs you already suspect.

6. **Make sure the decisions have seams.** If a function mixes a decision with
   the IO around it, the decision cannot be tested and that is where the bugs
   will be. Extract the decision; leave a wrapper with no branches in it. That
   is what §2.6 has always meant by "thin".

7. **Push, then confirm CI by SHA.** Not by badge, not by the last run you
   remember. A force-push leaves `gh pr checks` reporting a stale commit.

8. **Have nothing outstanding before you request review, and freeze while it
   runs.** If the same message that launches a review also proposes more work,
   the launch was premature. A round that reports on a tree that has moved is
   worth nothing, and cancelling it costs nothing.

The through-line, and the one worth keeping if only one survives: **a check
that did not run looks exactly like a check that passed.** Twelve instances in
a single session, in twelve different tools, three of them committed while
actively writing about that failure mode.

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
