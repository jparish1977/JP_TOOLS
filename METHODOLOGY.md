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

### This document is expected to be tested

Joe's rule, 2026-09-01: **like everything else we do, the methodology is expected to be tested.** Everything above is stated with more confidence than any of it has earned permanently, and that is deliberate -- a rule hedged into vagueness cannot be checked. The confidence is a claim, not a status.

So a conflict with this document is **a test, not a verdict.** When a proposal clashes with something written here, that encounter has two possible outcomes and nobody gets to assume which: either the proposal is wrong, or this is. Saying no therefore carries two burdens -- demonstrate that the conflict is real, and demonstrate *which side of it fails*. "It goes against the methodology" is an appeal to authority wearing a rule's clothes, and on its own it is not an answer.

It is proven by use and disproven the same way. That is not a slogan; it is the only test this document has ever passed. §2.8 exists because Joe stated it and this file's author argued it down three times before measuring anything -- the measurement then agreed with him. The fleet's verification ledger was refused by roughly a dozen sessions on reasoning, force-fed once, and adopted so hard it needed a brake threat. In both cases the argument lost to a trial, and in both cases the losing argument was the careful one.

Which is why an unproven rule here says so. A claim with no measured instance is written as a claim with no measured instance, and stays in that state until someone produces one or kills it. A document that only ever wins its arguments is not being tested by them.

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

**And the exemption is a CLAIM, not a permission -- which is §1's rule applied to the smallest thing in this document.** It claims the function is a thin wrapper with nothing in it worth testing. Nothing rechecks that claim, so it goes stale silently as the function grows and the exempt region becomes the region nobody looks at. Measured on `spool-audit.py`, 2026-08-13: `# pragma: no cover` covered **430 lines of 1350 -- 32% of the file, holding 51 branch, loop and try statements.** A thin wrapper has none. Every serious defect on that branch came from inside that region, and ruff and mypy together caught 0 of 112 findings.

The rule is therefore not "no exemptions". It is that **an exemption must say why, so the claim is visible and can be disagreed with**:

    def _unlink(p): ...    # pragma: no cover
    def restart():  ...    # pragma: no cover -- reason: runs systemctl

A pragma on a function with no branches needs no reason: it is self-evidently a wrapper. One with branches is making a decision, and decisions are testable. `check.py` has enforced exactly this since 2026-08-13 and `list-exemptions.py` enumerates them; this paragraph exists because the rule lived only in that tool's source, so a reader following §2.6 wrote a bare pragma and was then failed by a gate whose reasoning they could not see.

### 2.7 The silence rule

In domain code: when a field or feature is absent, let it be absent. No placeholder strings, no "no description available." Absence is part of the design. This comes from the portfolio aesthetic ("the column exists but goes quiet... no placeholder text... the silence is the content") and applies equally to APIs, CLIs, and internal data models.

---

### 2.8 Parallel first

**Do not run a serial test 900 times if you can run it in parallel once.**

Joe's rule, 2026-09-01. It is not "make everything concurrent", and it is not a
performance principle wearing a correctness hat. It is a claim about the SHAPE of
an investigation, in two phases that a serial loop collapses into one:

1. **Sweep in parallel, over everything, cheap and uniform.** Its job is
   CLASSIFICATION, not analysis: which units are interesting.
2. **Triage what it found, in an order you choose.** Severity, or cheapness, or
   likelihood. That ordering is only available because you hold the complete set.

The argument that this replaces: if 20 of 900 files are bad, nobody hand-tests
900 to find 20. And a serial scan does not merely cost more, it makes you
investigate in ARRIVAL ORDER and calls that a priority -- stopping at the first
bug found rather than at the worst one.

**Worked instance, and it is this methodology's own author getting it wrong.**
The nine-runner exit-code table in §10 was measured serially, one tool at a time.
The result was three successive corrections to the same conclusion over two hours
-- "9 of 10", then "2 of 10 defeat the standard fix", then "3 of 10" -- each sent
out as a revision. A parallel sweep of all twelve runners would have produced the
distribution in one pass and one report. The first-bug-first pathology landed
too: the design implication was declared at cppcheck, before phpcs was measured,
and phpcs turned out to be the worst of the three.

**The precondition, and it is the whole cost of the rule: THREE OUTCOMES PER
UNIT, NEVER TWO.** Clean, finding, and *could not determine*. A worker that
crashed is not a clean unit. With two buckets, a 900-to-20 sieve quietly becomes
900-to-20-plus-3-nobody-looked-at, and the sweep has manufactured the exact defect
§10 is about -- an absence encoded as a pass. With three, it has not, and the
aggregate is trustworthy at any width.

That precondition is not overhead added by parallelism. It is §2.7 and the
denominator rule, which were already required; parallelism only removes your
ability to survive without them, because a serial run shows you each failure as it
happens and an aggregate does not.

Applies to test suites, corpus scans, fleet sweeps, and any check run over more
than a handful of units. JP_TOOLS has no concurrency in any tool as of
2026-09-01, and every scan-shaped script in it loops serially, so this is a
forward rule rather than a description of the code.

### 2.9 Keep the tool, and make it carry its own evidence

Good tools take work, and the default outcome is that the work is thrown away. A review round, an incident, an afternoon of measurement -- each produces apparatus that answered a real question, and all of it sits in a scratch directory that dies with the session. Joe's rule, 2026-09-01: **"good tools take work, i hate to see work wasted."**

**A landing zone with NO BAR AT ALL.** `claude-config/tools/lab/` is the working example, 16 files. Nothing is required to put something there. That matters more than it looks: the failure mode is work being LOST, so any bar at entry costs you tools. **Dusty is fine, lost is not.** A scruffy script somebody hesitates to write up is a script that gets deleted.

**A DEPENDENCY IS THE SIGNAL TO PROMOTE, and the reason is not seniority.** `tools/forgejo.py` states it, on being promoted 2026-08-24: *"the wrapper exists to be whitelisted, and whitelisting a wrapper over an untested tool whitelists the untested tool."* Dependency is the moment the untestedness stops being yours and starts being inherited by something else. Promotion cost a test file that pins the three lessons the tool was built from -- invariants, not coverage, and explicitly not the happy path.

**Promotion is where the bar lives, so a lab tool must still say what it IS.** A directory is a blanket claim -- "lesser tested" -- with no per-tool justification, which is §2.6's defect at directory scale: an exemption that does not say why cannot be disagreed with. So the header carries three things, and nothing else is required:

- what question it answers
- what it was tested against, including the positive control
- **what it was NOT tested against**

That third line is the one that earns its place, and it is written naturally rather than under duress. Two seats produced apparatus on 2026-09-01 and both volunteered it unprompted: a containment harness whose docstring records *"unprivileged, scratch dir, no live cupsd"*, and whose author self-tested it end to end **and then broke it on purpose** to confirm it went red -- *"a harness that has only ever passed is not evidence."* The same docstring carries what the tool COST rather than what it does: three wrong attempts, named, so nobody repeats them.

**PROPOSED, NOT SETTLED:** that writing the evidence down should ALSO be a promotion trigger, not only a dependency. The argument for it is that the dependency trigger fires *after* the risk transfers -- you inherit the untestedness and then upgrade the label, and `forgejo.py` was caught because somebody noticed rather than because anything made noticing reliable. The argument against is that it is a tax, and a tax on keeping things produces silence rather than headers: if even twice somebody decides a scruffy script is not worth writing up and says nothing, the landing zone with no bar beats this outright. Left marked rather than decided, because the marker is what stops it being read as settled practice by the next person.

**And the principle AS A WHOLE is untested. Joe's verdict on it is the one to record: "it should work, guess we see."** It is written from a single day -- one review round, five tools, two seats -- and has not been through a cycle. Per §1 that makes it a claim rather than a status, and it will be proven by use or disproven the same way.

### 2.10 Check the record before you build

Every other principle here governs code that is being written. This one fires before that, and it is the only one whose failure costs the whole piece of work rather than a review round.

Joe's rule, 2026-09-08: **"pbq and pbq-grep should be part if methodolgy in spirit if not in name, they are enforment tools, check the record is the methodology."**

**The failure is building something that already exists, and it never announces itself, because the thing you build works.** No gate goes red. Static analysis passes, coverage passes, the tests you wrote pass. A duplicate is not a defect in any of the properties §2.5 measures, so nothing in the mechanical half of this document can see it. It is found by another person, or not at all.

**This repo's own instance, and it is the most expensive one on record here.** `spool-audit.py` grew `--fix` and `--purge` halves that set `PreserveJobFiles No`, restarted cupsd and deleted the leftovers. That is `cancel -a -x` plus one config line, and CUPS already does it correctly. Fifteen review rounds found roughly 112 things, of which ruff and mypy caught none. Joe cut the feature himself on 2026-08-14, 1,573 lines to 1,018 -- commit `d36c661`, whose subject is this principle in six words: *Cut spool-audit.py to the half that had no equivalent*. The deletion removed the exemption region, the seams it needed, and every dangerous bug at once: a `--fix` that could destroy a device node, one that truncated `cupsd.conf` to zero bytes, a `--purge` that followed a symlinked TempDir out of the audited directory.

**Not one of the fifteen rounds proposed it.** A review asks whether the code is correct. It does not ask whether the code should exist, and by round fifteen nobody is asking either.

**Enforcement is mechanical, on the same footing as §2.5, which is what makes this a principle here rather than advice.** `hook-book-first` refuses bare `grep`, `rg`, `ugrep` and `ack` fleet-wide, with no flag, marker or comment that lifts it -- a person edits the hook. The ladder is `~/projects/projectbook/bin/pbq-grep`, **by full path**, since it is not on `PATH` even though `pbq` is:

    ~/projects/projectbook/bin/pbq-grep --phrase TERM -- <your command>

**The exit codes, measured on 2026-09-08 rather than copied from the description, because they are not what the description says.** `2` means pbq COULD NOT LOOK -- a bad root, a missing collection, a filter matching no book -- and the fallthrough command does not run. That code is the important one, and treating it as "found nothing" converts *I could not look* into *it is not there*, the same collapse §2.8 forbids in a parallel sweep and §10 forbids in a denominator.

Otherwise the wrapper **passes your command's own exit status through verbatim**: a fallthrough exiting 3 yields 3, exiting 1 yields 1, exiting 0 yields 0. Verified in all three directions. It does NOT return a reserved `1` meaning "the book looked and found nothing", which is what `search-the-record-first` currently documents; that reads as true because a `grep` fallthrough returns 1 for no-match anyway, so the description and the behaviour agree on the commonest case and diverge silently everywhere else.

**So `0` is ambiguous and must not be branched on.** It means either *the book answered and your command never ran*, or *your command ran and succeeded*. Those are different facts about the world and the exit code cannot tell them apart. Distinguish them by whether the fallthrough produced output, not by the status. A caller that treats `0` as "the book had it" will read its own successful grep as a corpus hit.

That ambiguity is worth stating plainly, because it is the enforcement tool for this principle exhibiting the two-outcomes-where-three-are-needed shape the principle exists to catch. Reported to the tool's owners; recorded here as measured behaviour rather than as a defect ruling, which is theirs to make.

**A zero is not an absence until you have checked the shelf.** `pbq scope --root <collection>` says what is staged and when it was built. A class that was never staged returns exactly the clean nothing a real absence returns, and a collection built before the thing you are asking about cannot answer at any confidence. Measured on this fleet: a seat searched one root holding 2 books, read `NOTHING MATCHED` as a fact about the world, and the four roots together held 1,011.

**IN SPIRIT IF NOT IN NAME is doing real work in Joe's sentence, and the spool-audit case is why.** `pbq` searches this fleet's own record. It would not have found `cancel -a -x`, because that is CUPS' manual, not ours. The principle is **find out what already exists before you build it**, and the instrument varies with the question: the fleet record for anything we have done before, the platform's own documentation for anything the platform might already do, the tracker for anything already decided and disposed of. Reaching for the wrong one of those and getting a clean nothing is the failure mode, not an exemption from the rule.

**Status per §1, stated because this principle arrives with more evidence behind it than §2.9 and less than §2.5.** The mechanism is proven: the refusal is wired, unbypassable, and it fired on the author of this section while the section was being researched, naming its own fix in the refusal text. What is NOT established is a JP_TOOLS-native cycle. The spool-audit instance is this repo's and it is real, but it was caught by Joe reading his own tool rather than by any search, so it demonstrates the cost of the failure and not yet the value of the cure. The fleet instances that demonstrate the cure are session tooling, not this codebase. That gap is the test this principle has still to pass.

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

0. **Search before you scaffold (§2.10).** Ask the fleet record, the platform's own documentation, and the tracker whether this exists already. Numbered zero because it is the only step that can make the other nine unnecessary, and because it is the step whose omission never produces a failure -- a duplicate passes every gate below. `~/projects/projectbook/bin/pbq-grep --phrase TERM -- <command>`, by full path, and `pbq scope` before believing any zero.

1. **Three-or-fewer packages.** Minimum: domain. If the project has IO, add infrastructure. If the project has a UI or CLI, add delivery. Don't start with one mega-package that mixes layers.

2. **Declare zero framework deps in the domain package.** Check `composer.json` / `pyproject.toml` / `package.json` after scaffolding.

3. **Port interfaces before implementations.** Sketch the interfaces first. The first adapter can be `InMemoryFooRepository`, good enough to run the domain tests without touching a real database.

4. **Install the hook**: `python ~/JP_TOOLS/install-hooks.py /path/to/repo`. Commits fail until the code is clean.

5. **Install the CI**: `python ~/JP_TOOLS/init-ci.py /path/to/repo`. PR blocks on the same gates.

6. **Set coverage gate to 95%** in your test config. Use `@codeCoverageIgnore` / `pragma: no cover` sparingly and only on IO leaves.

7. **Write the CLAUDE.md** for each package. Copy from `iteration8-core/CLAUDE.md` and adapt. This is the prescriptive doc that future-you and future-collaborators read before touching the code.

8. **Composition root is a single file.** Service provider in Laravel, `main()` in Python, app entry in JS. One place where "this project uses these adapters" is declared.

9. **Declare the tools the GATE itself runs, in a manifest, pinned.** Not in a CI `pip install` line, not in a README, not in a wiki. A manifest a person can read, install from, and diff. Then add the check that every declared pin is actually installed at that version, because a manifest nobody verifies is a second thing that can be quietly wrong.

### Declare the instrument's own dependencies

Joe's call, 2026-09-01: guessing at dependencies is a PITA nobody wants to deal
with. It earns a checklist item because JP_TOOLS itself got two of the three
languages right and the third had nothing at all, which is the shape this fails
in -- not total neglect, but one arm nobody noticed was undeclared.

    composer.json    declares phpstan, phpcs, rector          DECLARED
    package.json     declares eslint, prettier, stylelint     DECLARED
    python runners   ruff, mypy, pip-audit                    NOWHERE

The Python pins existed only inside a `pip install` line in a CI workflow, which
is not a declaration: you cannot install from it locally, and it does not diff
against anything. **Two copies of that line had already drifted.** JP_TOOLS' own
`check.yml` pins `ruff`, `mypy` and `coverage`; the template `init-ci.py` ships
to every adopting project pins `ruff`, `mypy` and `pip-audit`. Neither is a
superset of the other, and the divergence is visible only by opening both files.

**The cost is silent, and it is the denominator problem from §10.** A runner
whose tool is missing returns `"unavailable"`, which contributes no issues, and
`_summarize` counts only issues. So a run where an arm never executed is
indistinguishable -- in the summary, and in the exit code -- from a run where
everything passed. The gate does not lie about it. It simply never mentions it,
and nobody reads a per-tool block when the total says zero.

**Declaring is not enough on its own: the declaration and the resolution have to
agree.** JP_TOOLS gets this right for PHP, where `_php_bin` prefers
`vendor/bin/<tool>` and falls back to `PATH`, so `composer install` is sufficient
and the error message even says so. It gets it wrong for JS, where
`package.json` declares prettier and stylelint while `run_prettier` resolves
through `shutil.which` alone and never looks in `node_modules/.bin`. **Running
`npm install` therefore satisfies the manifest and leaves the gate reporting the
tool as unavailable** -- the worst of both, because the manifest now says the
dependency is handled.

**Over-declaring is the mirror error and it is easier to commit.** The first
draft of `requirements-dev.txt` pinned `coverage==7.15.4`, copied from CI. It is
not installed on a working dev box, and `check.py`'s own comment says nothing in
the repo measures coverage at all -- so the manifest would have asserted a
dependency nothing consumes. A manifest that over-claims is not safer than one
that under-claims; it is the same defect pointed the other way, and harder to
notice later because installing more than you need always succeeds.

The check that catches both directions costs about fifteen lines: walk the
manifest, run each tool's `--version`, and assert the pin matches. It caught the
phantom `coverage` entry within a minute of the file being written, which is the
only reason it is described here as an error rather than as a rule.
### The first from-inception adoption, and what it bought on day one

Everything above was written as a prescription and applied, until 2026-09-01, only
as a retrofit -- which is section 9, a different and more forgiving problem. A
retrofit gets a baseline and an exemption list, so the discipline is measured
against where the code already was. `romtools` is the first project to take this
from inception, with no baseline to hide behind, and the checklist stopped being
theory the same day.

It paid before the first review round, on item 6 and on the rule under it. The
project's `ruff.toml` records the decision it nearly made instead:

> NOTHING IS IGNORED HERE ON PURPOSE. The first draft of this file was going to
> silence UP031 (113 hits) and E701/E702 (126), on the grounds that percent format
> and one-line statements are style rather than substance. Joe, the same day:
> "poor style hides bugs"

And the repo had already proved it, in `prior-art/scan.py`:

    except Exception as e: return ('unreadable',[])    # E701
    ...
    e = head[root+i*32:root+i*32+32]                   # `e` reused as a loop var

mypy flagged the reuse. It is not a crash, it is genuinely confusing, and **the
confusion is invisible BECAUSE the except was folded onto one line.** The style
rule and the legibility problem were one finding, so silencing the first would have
hidden the second -- and 239 hits is exactly the volume that makes silencing feel
like housekeeping rather than like a decision.

Two things generalise from that, and they are the argument for adopting at
inception rather than later:

- **The exemption you write on day one is the one nobody ever revisits.** A
  retrofit's exemption list is understood to be debt and gets a ticket. A
  greenfield ignore is written as policy, reads as intent, and is invisible
  thereafter.
- **A high hit count is evidence about the rule's reach, not about its
  worthlessness.** 239 findings across a young repo means the pattern is
  load-bearing there. That is a reason to look at what it is covering, not a
  reason to turn it off.

Where the discipline is worth its cost is not evenly spread, and item 4's hook is
what makes any of it survive contact: a gate that runs only in CI is a gate the
author meets after they have stopped thinking about the change.

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

3. **Check coverage, and check what it is measuring.** `.coveragerc` traces
   subprocesses and does **not** honour `pragma: no cover`, both deliberately.
   Untraced subprocesses cost 11 points on `spool-audit.py` and honouring the
   pragma added 10 it had not earned, so a naive setup reports a number that is
   wrong in the flattering direction. See `docs/coverage.md`.

4. **Read the exemptions**: `python list-exemptions.py <file>`. §7.6 says
   `pragma: no cover` belongs "only on IO leaves". On this branch it covered
   430 lines of 1350, holding 51 branch, loop and try statements, and every
   serious defect came from inside it. The `no-cover` check refuses an
   unjustified branchy exemption, but **a reason is not a justification**: two
   of the first four written here said three words and justified nothing.
   Nothing but a person reading the list catches that.

5. **Confirm CI actually covers the file, by name.** The list in
   `.github/workflows/check.yml` is manual. `spool-audit.py` was merge-ready
   with a green badge while no job in that file touched it, so "CI is green"
   was true and said nothing about the code. Green on a file nobody checks is
   not a pass, it is a check that did not run.

6. **Run the thing itself, including its destructive paths.** Against a real
   fixture, and on a real machine if it touches one. Every real-machine run on
   this branch found something the fixtures did not, because a fixture you
   invented can only contain bugs you already suspect.

7. **Make sure the decisions have seams.** If a function mixes a decision with
   the IO around it, the decision cannot be tested and that is where the bugs
   will be. Extract the decision; leave a wrapper with no branches in it. That
   is what §2.6 has always meant by "thin".

8. **Push, then confirm CI by SHA.** Not by badge, not by the last run you
   remember. A force-push leaves `gh pr checks` reporting a stale commit.

9. **Have nothing outstanding before you request review, and freeze while it
   runs.** If the same message that launches a review also proposes more work,
   the launch was premature. A round that reports on a tree that has moved is
   worth nothing, and cancelling it costs nothing.

The through-line, and the one worth keeping if only one survives: **a check
that did not run looks exactly like a check that passed.** Twelve instances in
a single session, in twelve different tools, three of them committed while
actively writing about that failure mode.
---

## 9. Adopting this in a codebase you inherited

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

   This is also a defect to fix before quoting the toolbox at anything.
   `install-hooks.py` states in its own docstring that it "runs check.py
   against staged files", collects them into `$STAGED`, uses that list only to
   test whether anything was staged at all, and then runs `check.py .` against
   the repo root. On greenfield the difference is invisible, because the whole
   tree is clean either way. On anything inherited it is the difference between
   an on-ramp and a wall.

2. **Record the baseline, and hold the line at "no worse".** Ratcheting needs a
   number to ratchet against. Commit the output of a full run, and have the
   gate compare against it rather than against zero. Without that stored
   number, a file that got worse and a file that was always bad are
   indistinguishable, so no one can tell progress from noise and the effort
   stops being visible to anyone including you.

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

## 10. Anti-patterns

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
- **Reimplementing what the platform, or this fleet, already does (§2.10).** The only anti-pattern on this list that passes every gate in §2.5, because a duplicate is correct code. `spool-audit.py`'s `--fix` was `cancel -a -x` plus one config line; fifteen review rounds priced the implementation and none of them asked whether it should exist. Search first, and treat "could not look" as distinct from "not there".
- **Reading a search result as an answer.** A hit is an address: `collection / book / nid / title / snippet`. Summarising snippets back is the same error as quoting a grep hit instead of reading the function it points at, and a pointer that no longer resolves is a finding about the pointer rather than evidence the thing is gone.

---

## 11. Related reading

- `~/projects/iteration8-core/CLAUDE.md`, domain layer rules (24 lines, prescriptive).
- `~/projects/iteration8-utilities/CLAUDE.md`, infrastructure layer rules (30 lines), and the FileScanner reference implementation itself.
- `~/projects/iteration8-site/CLAUDE.md`, delivery layer rules, CSS token/adapter pattern (55 lines).
- `find-dupes.php` in this repo, the composition root that consumes FileScanner.
- `~/portfolio_notes/iteration8-design-doc.md`, the full content-and-aesthetic model for the portfolio site, including the silence rule and the planned MBE/spoken-word convergence. **`jap-m18` only** -- `~/portfolio_notes/` exists on no other fleet machine.
- `~/portfolio_notes/TODO.md`, live task state across all projects. **`jap-m18` only**, same as above.
- `~/projects/mandelbrotexplorer/`, displacement formulas as research hypotheses (domain example from MBE). **The `JULIA_TUNNEL_LINEAGE.md` this line used to name resolves nowhere**, at either the old `~/mandelbrotexplorer/` path or the real one; the tree holds `julia-tunnel.html` and a `docs/` directory that does not contain it. Left pointing at the tree rather than deleted, because the subject is real and only the address is lost.
- `~/projects/brots-alive/CONTINUATION.md`, same-math-different-medium example (domain example from Brots Alive). Verified present.
- `~/projects/iteration8-continuation.md`, live state of the production stack applying this methodology. **Unverified: absent from `~/projects` on `joe-MacBookAir`.**

**Five of the nine pointers above did not resolve on this box when checked on 2026-09-08, and that is a finding about this section rather than about the projects.** The `~/mandelbrotexplorer/` path is the instructive one: `CLAUDE.md` had already corrected exactly that path once, on 2026-09-06, after a session looked there, found nothing, and reported the tree as m18-only when it is on two machines. The same wrong address survived here because nothing checks a link in a document. Per §2.10 a pointer that does not resolve is a finding about the pointer, so verify before quoting one, and fix it where it was read.

---

*The toolbox comes with a manual. Read it, apply it, improve it.*
