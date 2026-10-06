# JP_TOOLS Methodology

*Framework-agnostic architecture, distributed as discipline.*

This is the manual for the toolbox. JP_TOOLS isn't a library, it's a set of practices that travel across languages, frameworks, problem domains, and mediums. The practices are for shipping anything: portfolio sites, research visualizers, interactive fiction, data-recovery tools, archive management, generative art, hardware driver abstractions.

If it's software you expect to survive the decade, this is how it should be built.

---

## 1. Thesis

**The methodology is the asset. Everything downstream is replaceable.**

Software that outlasts its first framework has one thing in common: the domain is cleanly separated from the delivery mechanism. When it is, the domain survives. When it isn't, the rewrite starts from zero.

The rule: **if it can't be reimplemented in Zend 1.x, it's not abstract enough.**

Domain logic belongs in a framework-zero core. Everything that touches the outside world (databases, filesystems, image libraries, rendering surfaces, HTTP, authentication, audio analysers, GPUs) goes through a port interface defined by the domain, with concrete adapters living outside the domain. When the framework changes (Laravel to whatever comes next, Three.js to WebGPU, disk to S3), you rewrite the adapters. The core doesn't move.

This isn't new theory. It's hexagonal architecture / ports-and-adapters, applied strictly enough that the separation actually survives contact with deadlines.

### This document is expected to be tested

Every rule here is stated with more confidence than it has permanently earned, on purpose: a rule hedged into vagueness cannot be checked. The confidence is a claim, not a status.

So a conflict with this document is **a test, not a verdict.** When a proposal clashes with a rule here, either the proposal is wrong or the rule is. Rejecting the proposal means showing that the conflict is real and which side of it fails. "It goes against the methodology" is not, on its own, an answer.

A rule is proven by use and disproven the same way, and a trial beats an argument. A rule that has not yet been measured says so, and stays that way until someone produces an instance or kills it.

---

## 2. Principles

### 2.1 Zero framework dependencies in the domain

Your domain package declares only:
- The language runtime (e.g. `php ^8.3`, `python ^3.11`, `typescript ^5`).
- First-party utility libraries you control.

It declares nothing else. No Laravel, no Symfony, no Flask, no React, no Three.js, no numpy, no ImageMagick. If the domain needs to read a file or render a frame or analyse audio, it declares a *port interface* for "thing that reads files" / "thing that renders" / "thing that analyses audio" and accepts an instance of that interface via constructor injection. Concrete implementations live in the infrastructure layer, not the domain.

### 2.2 Ports define all external boundaries

A port is an interface. It names what the domain needs in terms the domain cares about: `ProjectRepositoryInterface`, `HasherInterface`, `ImageProcessorInterface`, `RendererInterface`, `AudioAnalyserInterface`. It does NOT say "MySQL" or "ImageMagick" or "WebGL" or "FFmpeg."

An adapter is a concrete implementation: `EloquentProjectRepository`, `NativeHasher`, `ImageMagickProcessor`, `ThreeJsRenderer`, `WebAudioAnalyser`. It lives in the infrastructure or delivery layer, not the domain.

The test: can you swap an adapter without touching domain code? If yes, the port is doing its job. If you find yourself reaching into the adapter's guts from domain code, the port is too thin.

### 2.3 Value objects and entities are immutable, but mind the `final` trap

Constructor-promoted properties (PHP 8.1+), `@dataclass(frozen=True)` (Python), `readonly` modifiers (TS). `toArray()` or equivalent for serialization. No setters. No mutation.

Immutability buys two things: thread-safety by default, and the ability to reason locally about state. If you have a `FileEntry` and you need a modified version, you construct a new `FileEntry`. The old one is still valid. In tests this removes a whole class of false positives ("the test mutated the fixture"). In production it removes a whole class of concurrency bugs.

**The test-suite trap.** Marking these classes `final` (or `final readonly class` in PHP 8.2+, which blocks subclassing even when properties aren't themselves final) breaks:

- **PHPUnit `createMock()` / `getMockBuilder()`**, which generate subclasses under the hood. Final classes fatal-error at mock-creation.
- **Test-specific subclasses for IO leaves.** The `protected` + `@codeCoverageIgnore` pattern in §2.6 relies on subclassing the adapter to fake the IO method. If the adapter is `final`, that escape hatch is gone.
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

PHPStan level 8 / mypy strict / tsc strict. PHPCS PSR-12 / ruff / eslint. PHPUnit / pytest / jest. Coverage threshold at 95%+ for domain code.

Enforcement is mechanical: the pre-commit hook (`python /path/to/JP_TOOLS/install-hooks.py /path/to/repo`) runs `check.py` before every commit. CI (`python /path/to/JP_TOOLS/init-ci.py /path/to/repo`) runs the same checks on push and PR. A failed check blocks the commit and the merge.

These gates are not tech-debt mitigation. They are architectural enforcement: if a piece of code can't pass static analysis at level 8, it is probably structured wrong.

**Suppress only when it is necessary, never for convenience.** A `# noqa`, a `# type: ignore`, a linter's own directive (`# shellcheck disable`, `// cppcheck-suppress`, `@phpstan-ignore`, `phpcs:ignore`, `eslint-disable`, `stylelint-disable`), a coverage pragma, a baseline entry or an ignore-file line is a claim that the rule is wrong for this code and that the code cannot be written to satisfy it. When the code can be changed to pass, change the code: fix the import order, restructure the function, remove the hack that made the finding. The suppression states why it is necessary on the same line, so the claim can be read and disagreed with, and `check.py` refuses one without a reason. An existing finding is not a licence either: a smell or error the baseline grandfathers is debt to pay down, not a pass.

### 2.6 IO leaves are allowed to be untested

At the boundary between your code and the OS, there's always a line that actually opens the file / dispatches the HTTP request / invokes the subprocess / writes to the GPU. Don't chase 100% coverage through that line with brittle mocks. Instead, factor the adapter so the IO-touching method is a thin `protected` wrapper tagged `@codeCoverageIgnore` (`pragma: no cover` in Python), and unit-test subclasses that fake it.

Business logic stays at 100%. OS-level error handling is acknowledged as an integration concern, not a unit-test concern.

**The exemption is a claim, not a permission.** It claims the function is a thin wrapper with nothing in it worth testing. Nothing rechecks that claim, so it goes stale silently as the function grows, and the exempt region becomes the region nobody looks at. A wrapper has no branches; an exempt region full of branches, loops and `try` blocks is untested decision-making.

So **an exemption must say why, so the claim is visible and can be disagreed with**:

    def _unlink(p): ...    # pragma: no cover
    def restart():  ...    # pragma: no cover -- reason: runs systemctl

A pragma on a function with no branches needs no reason: it is self-evidently a wrapper. One with branches is making a decision, and decisions are testable. `check.py` enforces this, and `list-exemptions.py` lists every exemption so a person can read them.

### 2.7 The silence rule

In domain code: when a field or feature is absent, let it be absent. No placeholder strings, no "no description available." Absence is part of the design, and that applies equally to APIs, CLIs, and internal data models.

---

### 2.8 Parallel first

**Do not run a serial test 900 times if you can run it in parallel once.**

This is not "make everything concurrent", and it is not a performance principle. It is about the shape of an investigation, in two phases that a serial loop collapses into one:

1. **Sweep in parallel, over everything, cheap and uniform.** Its job is classification, not analysis: which units are interesting.
2. **Triage what it found, in an order you choose.** Severity, or cheapness, or likelihood. That ordering is only available because you hold the complete set.

If 20 of 900 files are bad, nobody hand-tests 900 to find 20. A serial scan does not merely cost more, it makes you investigate in arrival order and call that a priority, stopping at the first problem found rather than at the worst one. It also produces a conclusion that changes every time a new unit is measured, where a sweep produces one distribution in one report.

**The precondition: three outcomes per unit, never two.** Clean, finding, and *could not determine*. A worker that crashed is not a clean unit. With two buckets, a 900-to-20 sieve quietly becomes 900-to-20-plus-3-nobody-looked-at, and the sweep has encoded an absence as a pass. With three, the aggregate is trustworthy at any width.

That precondition is not overhead added by parallelism. It was already required; a serial run merely lets you survive without it, because it shows you each failure as it happens and an aggregate does not.

Applies to test suites, corpus scans, machine sweeps, and any check run over more than a handful of units.

### 2.9 Keep the tool, and make it carry its own evidence

Good tools take work, and the default outcome is that the work is thrown away: a measurement, a probe or a harness answers a real question and then sits in a scratch directory that gets deleted.

**Give them a landing zone with no bar at entry**, such as `tools/lab/`. Nothing is required to put something there. The failure mode is work being lost, so any bar at entry costs you tools. **Dusty is fine, lost is not.**

**The landing zone owes one thing: an index, kept current by a test.** The lab's README has a row per tool saying what question it answers. A test fails when a tool lands without a row, or when a row outlives its tool (`tests/test_lab_index.py` here). A tool nobody can find is lost in place: the next person checks for prior art, finds nothing, and builds it again. So **before writing a tool, read the index.** An index kept by hand goes stale the first time someone is in a hurry; one held by a test does not.

**Each lab tool's header says three things, and nothing else is required:**

- what question it answers
- what it was tested against, including the positive control
- **what it was NOT tested against**

A directory is a blanket claim ("lesser tested") with no per-tool justification, which is §2.6's defect at directory scale. The header is the per-tool justification. The third line is the one people leave out, and it is the most useful: it tells the next user where the tool's answer stops meaning anything.

**A recorded result never freezes the code that produced it.** A lab tool is fixed and restructured like any other code. Keeping the record true means re-running the tool against what it was tested on and saying in its header what changed and whether the output did; the revision that produced a recorded number stays in version control. A second copy kept "as run" is two homes for one tool (§2.13), and the copy's bugs are kept with it.

**A standard met by copying is worth a generator.** Standards like the index row, the header and a common test shape are cheap for one tool and expensive by the dozen, because everyone retypes them and the copies drift. When a standard is being met by copying, write a generator for it.

**A generator writes the shape, never the substance.** It emits the interface, the wiring and the index row. What differs every time (the assertion, the control, the reason) stays with the person, because a generator that fills those in writes a test that cannot fail. A generated test stub fails until it is filled in. A generator is a tool like any other: it lands in the lab with the header, and its index row names the standard it makes cheap.

**A lab tool that grows a dependency requires promotion out of the lab.** Once something else depends on a lab tool, its untestedness is inherited by that thing: a wrapper over an untested tool makes the untested tool part of whatever trusts the wrapper. Promotion costs a test file that pins the invariants the tool was built to keep, not its happy path.

Unsettled: whether writing a tool's evidence down should also be a promotion trigger. For: the dependency trigger fires after the risk has already moved. Against: a tax on keeping things produces silence rather than headers.

### 2.10 Check the record before you build

This fires before any code is written, and it is the only principle whose failure costs the whole piece of work rather than a review round.

**The failure is building something that already exists, and it never announces itself, because the thing you build works.** Static analysis passes, coverage passes, the tests you wrote pass. A duplicate is not a defect in any property §2.5 measures, so no gate can see it. It is found by another person, or not at all.

Review does not catch it either. A review asks whether the code is correct, not whether it should exist. A feature can go through many review rounds, each finding real bugs, when the right change was to delete it and call a command the platform already provides.

**Find out what already exists before you build it**, with the instrument that fits the question:

- the project's own history, tracker and documentation for anything already done or decided;
- the platform's documentation for anything the platform might already do;
- the dependency ecosystem for anything someone else has already published.

Reaching for the wrong one of those and getting a clean nothing is the failure, not an exemption from the rule.

**A search that could not run is not a search that found nothing.** A tool that reports "could not look" and "looked and found nothing" with the same result is collapsing three outcomes into two (§2.8). Before believing a zero, check what the search actually covered: which sources were indexed, and whether they were built before the thing you are asking about.

**A wrapper is documented with its own contract, not its wrappee's.** A wrapper that passes its command's exit status through is easy to describe with the wrapped tool's exit codes, and the description will survive review because it is true of the tool underneath. Check the wrapper itself.

### 2.11 Clean up after yourself

**What a change leaves behind is read by other tools.** A fixture under `/tmp` with a credential-shaped string in it becomes a finding for any secret scanner that reads the disk, and someone has to rule on it. Deleting the tree afterwards does not close the finding. The cheapest finding is the one that was never written to disk.

It never announces itself, for the same reason as §2.10: the run that left the mess succeeded, and the mess sits on a path nobody opens. The usual leftovers are fixtures removed only on a test's last line, probes that print their temporary directory and never remove it, long runs started and forgotten, and worktrees deleted with `rm` so git still has them registered.

**The rules:**

1. **Register the cleanup when you create the thing, not on your last line.** Call `atexit.register(shutil.rmtree, d, ignore_errors=True)` right after `mkdtemp`, or use a context manager. A cleanup on the last line runs only if every line before it did.
2. **A killed process runs no cleanup at all, so whatever kills a run cleans up after it.** A harness with a timeout gives each run its own `TMPDIR`, kills the run's whole process group at the timeout, and removes the directory whatever happens.
3. **Never put a secret-shaped value in a fixture.** Use placeholders scanners accept: a `$VAR` for any assignment, `example` for a user or password, a reserved host (`example.com`, `host.invalid`, `*.test`).
4. **A long-running job gets its own output directory, and a note where the next person will look**: its PID, its directory, and how to stop it.
5. **Remove a worktree with `git worktree remove`, never `rm`.** Do it only after checking that what it held has landed.
6. **A scratch script either earns a place in the lab (§2.9) or is deleted.** A draft of something that already landed goes; anything that exists only in scratch is read before it goes.
7. **Before you stop, list what you created and account for each item:** temporary directories, worktrees, background jobs, branches. Remove each one or hand it off. For a test suite the check is one command: list `/tmp` for your fixture prefixes after the suite has run, and expect nothing.

### 2.12 One long run for everything that is ready

The sibling of §2.8. That rule is about the shape of a sweep; this one is about how many times you pay for a slow one. When several changes are waiting on the same slow suite, the suite runs once, over all of them together.

1. **Stack every ready branch onto one integration branch** (each cherry-picked onto main) and run the suite once, main against the stack.
2. **If the results match, fast-forward main to the stack.** If main moved during the run, rebase the stack onto it again first.
3. **If they differ, bisect the stack.** Drop half the branches and rerun; never fall back to one run per branch. A failure costs a few extra runs; running every branch alone costs one run per branch, every time.
4. **One suite per machine at a time** when the suite binds fixed ports or other shared resources, or two runs skew each other.
5. **A branch that is not ready waits for the next stack.** It does not get a run of its own because it finished first.

Rule 3's bisection cost is argued, not yet measured.

### 2.13 One fact, one home

Every fact has exactly one home: a number, a list, a contract, a decision, a version pin. Everything else points to that home, or is generated from it. A copy is allowed only when something checks it against the home and fails when they disagree.

A second copy is correct the day it is written and wrong after the first change to either one, and nothing reports the drift. From then on a reader holding one copy cannot tell whether it is the current one, and the reader who finds out is usually the one who acted on the stale copy.

The shapes it takes:

- **One name, defined twice.** A module constant assigned in two places with different contents. The later one wins at runtime, and every linter passes, because redefining a name is legal.
- **One behaviour, described in several places.** A docstring, a README and the code each state what an exit code means, and they disagree. The code is the only one that runs.
- **One pin, in several manifests.** A tool version pinned in a requirements file and again in each CI workflow's install line (§7, "Declare the instrument's own dependencies").
- **One component, in two repos** (§9 item 8).
- **A comment that restates a fact.** "Nothing in this repo measures coverage" is true until coverage is wired up, and then it is a false statement someone will quote. A comment that says **why** cannot go stale that way; a comment that says **what the repo does** is a copy of a fact whose home is elsewhere.

How to apply it:

- **Point, don't restate.** A doc that describes behaviour links to the code, or to a test that asserts the behaviour. A README's exit-code line becomes a test that runs the tool and checks the codes, so a wrong README fails a build instead of misleading a reader.
- **A copy must be generated or checked.** If a CI line or a README table has to exist, generate it from the home, or add a check that fails when they disagree.
- **A pointer is not a copy.** A note that lists open items drifts as soon as the list changes; a note that points at the list cannot.
- **Splitting along a false seam makes two homes** (§2.14).

Nothing in the gate detects duplication yet.

### 2.14 Size is a smell; responsibility is the rule

**The rule is responsibility.** A file should have one reason to change. That is §2.2 and §4 at file scale: a file that parses argv, holds domain decisions and performs IO has collapsed the layering into one place, whatever its length. A 300-line file doing three jobs is the defect. A 1,400-line file doing one well-documented job may not be.

**The number is the smell.** Past **1,000 physical lines** a file answers, in its own header, what its responsibilities are and why they belong together, so the claim is visible and can be disagreed with (§2.6, §2.9). A file that cannot state its responsibilities in a few lines has usually answered the question already. Count physical lines, because that is what a reader scrolls; counting only code rewards a large file that has stopped explaining itself.

Crossing the line is not a violation, and it is not a reason to split on its own. Splitting a file to get under a number, along a seam that is not a responsibility, makes two files that must change together (§2.13). That is worse than one large file, and it hides the coupling the number was pointing at.

**The gate holds the line where it is.** A file or a function already over a limit may stay as it is or shrink; a change that makes it bigger fails, the same as one that crosses the limit. So a large file that does one job may stay large, and the next feature goes into a module of its own, or comes with something taken out.

Complexity is the better measure and belongs per function, counted by each language's standard tool (§2.15). **Code outside every function is the file's main**, and is measured as a function under the same limits. No linter measures it, so without that a script with all its logic at its top level reads clean, and a split that moves logic out of functions looks like an improvement. Measure what a rule would find before selecting it; a rule that is red everywhere on day one is how a gate gets switched off (§9 item 1).

JP_TOOLS was past the smell itself and has been split on its responsibilities: `check.py` into `jpcheck/` (one module per runner family, the baseline, the smells, the suppression rules), `spool-audit.py` into `spoolaudit/`, and its test into one file per module. No tracked source file is over 1,000 lines (`tools/lab/file-sizes.py`).

### 2.15 A standard job gets the standard tool

When a job has a standard tool, use it: a workflow file is linted by actionlint, YAML is read by a YAML parser, a CI workflow is proven by running it on the forge it targets, text is searched by the search tool. Install it and declare it (`requirements-dev.txt`, `requirements-system.txt`, `package.json`, `composer.json`, §7 item 9) instead of approximating it.

**An approximation is a weaker check that reads as a finished one.** A regex over YAML answers "does this text contain that string", not "is this workflow valid", and it cannot find what nobody thought to write a pattern for; the standard linter can. "The repo has no library for that" describes a manifest line nobody has written yet, not a constraint. The one case for doing without is a runtime dependency of the product itself, which is a design decision with its own cost; a tool the gate or the tests use is not that case.

---

## 3. The canonical exemplar: FileScanner

`FileScanner` is the reference implementation. It's in PHP, but the pattern translates directly to any language.

**Where it lives.** FileScanner is *not* in this repo. It ships in the `iteration8/utilities` package under the `Iteration8\Utilities\FileScanner` namespace, and JP_TOOLS consumes it through composer as a declared dev dependency. `find-dupes.php` here is the composition root that wires it up. Keeping the library out of the toolbox is itself the methodology working: the toolbox depends on the contract, not on a vendored copy.

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

Use it as the template for any new library or tool.

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

A typical web stack: a `core` package (entities, port interfaces, services, language runtime only), a `utilities` package (adapters such as an image processor or file scanner, depending only on core's ports), and a `site` package (the framework, a service provider binding ports to framework adapters, thin controllers). If the framework is ever replaced, core and utilities don't move; you write new adapters in a new delivery package.

### Reference application: the JP_TOOLS toolbox itself

This repo is methodology applied to itself. `find-dupes.php` is a delivery layer over the FileScanner library, and it depends on that package's contracts rather than vendoring a copy. `check.py` and `fix.py` are language-agnostic delivery layers that dispatch to adapters per language (`ruff` for Python, `phpstan` for PHP, `eslint` for JS). `install-hooks.py` and `init-ci.py` are the propagation mechanism: they install the methodology into other repos.

---

## 5. Applying the methodology across mediums

The principles are medium-agnostic. The same structure works for:

### Content site / CMS
Domain = content items, projects, posts, layouts derived from content. Ports = repositories, image processor, authenticator, session. Adapters = an ORM, an image library, an OAuth provider, the framework's session. Delivery = the web framework and templates. Design tokens in plain CSS are the port (they survive any framework); a utility-CSS framework's theme layer is the adapter (disposable). A lint step that fails on colours outside the tokens keeps the boundary honest.

### Research visualizer
Domain = the pure computation (for a fractal explorer, the iteration and its trajectory, never modified). Ports = the formulas under study, the rendering backend, an audio analyser. Adapters = concrete formula implementations, a WebGL renderer. Delivery = the UI. When each formula is a port, each is a hypothesis about the data and swapping one is a one-line change: the method of investigation becomes the structure of the code.

### Simulation / generative art
Domain = the model (cellular automata, wave equations, iteration). Ports = rendering backend, sampling, clock coupling. Adapters = a plotting library for a proof of concept, a GPU renderer later. The proof of concept is a delivery layer over the same core.

### Networked interactive fiction
Domain = game state, player session, room graph, message bus. Ports = input/output, session store, persistence, broadcast. Adapters = telnet, WebSocket, in-memory store, SQLite. Two delivery surfaces on the same domain. A bug where session cleanup is entangled with one transport's lifecycle is a missing port; factoring it out is the hexagonal move.

### Data recovery and archive management (the JP_TOOLS recovery tools)
Domain = file integrity, hunk-level dedup, provenance tracking. Ports = raw disk access, hash algorithm, CHD operations, photorec profile. Adapters = `ddrescue`, native vs shell hashing, `chdman`, photorec. Delivery = `recover.py`, `image-disk.py`, `chd.py`, `undelete.py`, each a thin composition root picking adapters per invocation.

### Hardware and firmware tooling
Domain = device capabilities, driver abstraction, boot-mode state. Ports = USB, ACPI, sensors, display. Adapters = kernel modules, userspace drivers, vendor blobs. Delivery = installer scripts, service units. Hardware is another framework.

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

0. **Search before you scaffold (§2.10).** Ask the project record, the platform's own documentation, and the tracker whether this exists already. Numbered zero because it is the only step that can make the other ten unnecessary, and because its omission never produces a failure: a duplicate passes every gate below.

1. **Keep the layers visible: a package per layer is the default, not the rule.** Minimum: domain. If the project has IO, add infrastructure. If the project has a UI or CLI, add delivery. Related layers may instead live under one parent, as sub-namespaces of a single package, when each layer is its own namespace and a test enforces that dependencies only flow downward (§4). What is ruled out is the mega-package: layers mixed together so that nothing shows where one ends and the next begins. When a single package's namespaces start getting out of hand, it is approaching one.

2. **Declare zero framework deps in the domain package.** Check `composer.json` / `pyproject.toml` / `package.json` after scaffolding.

3. **Port interfaces before implementations.** Sketch the interfaces first. The first adapter can be `InMemoryFooRepository`, good enough to run the domain tests without touching a real database.

4. **Install the hook**: `python /path/to/JP_TOOLS/install-hooks.py /path/to/repo`. Commits fail until the code is clean.

5. **Install the CI**: `python /path/to/JP_TOOLS/init-ci.py /path/to/repo`. PR blocks on the same gates.

6. **Set coverage gate to 95%** in your test config. Use `@codeCoverageIgnore` / `pragma: no cover` sparingly and only on IO leaves.

7. **Write the contributor rules** for each package: what the layer may and may not depend on, and how it is tested. This is the prescriptive doc people read before touching the code.

8. **Composition root is a single file.** Service provider in Laravel, `main()` in Python, app entry in JS. One place where "this project uses these adapters" is declared.

9. **Declare the tools the GATE itself runs, in a manifest, pinned.** Not in a CI `pip install` line, not in a README, not in a wiki. A manifest a person can read, install from, and diff. Then add the check that every declared pin is actually installed at that version, because a manifest nobody verifies is a second thing that can be quietly wrong.

10. **Declare the project itself in its language's project file, from the first commit.** `pyproject.toml` for Python, `package.json` for JS, `composer.json` for PHP: its name, the language version it needs, and its runtime dependencies. Item 9 covers the gate's tools, not the project. The minimum for Python:

    ```toml
    [project]
    name = "example"
    version = "0.1.0"
    requires-python = ">=3.12"
    dependencies = []
    ```

    Write `dependencies = []` when the project uses only the standard library; an empty list is a declaration, a missing one is not. Take `requires-python` from what the code uses, not from whatever the machine has. Add `[project.scripts]` when the project installs a command.

    **Leave `[tool.ruff]` and `[tool.mypy]` out unless the project means to replace the JP_TOOLS defaults.** Either table makes the file that tool's config, and `check.py` then uses the project's settings instead of the defaults.

### Declare the instrument's own dependencies

Item 9 fails in one characteristic shape: not total neglect, but one language's tools declared nowhere while the others are declared properly. Check every arm the gate runs.

**A pin that lives only in a CI install line is not a declaration.** You cannot install from it locally and it does not diff against anything, and two copies of such a line drift apart without anyone noticing.

**The cost of an undeclared tool is silent.** A runner whose tool is missing contributes no findings, so a run where an arm never executed looks like a run where everything passed. `check.py` exits 2 when a check could not run, so a missing tool is reported rather than read as a pass.

**Declaring is not enough: the declaration and the resolution have to agree.** If the manifest installs a tool into `vendor/bin` or `node_modules/.bin`, the runner must look there too. Otherwise installing from the manifest satisfies the manifest and still leaves the gate reporting the tool as unavailable.

**Over-declaring is the mirror error.** A manifest that pins a tool nothing consumes is the same defect pointed the other way, and harder to notice, because installing more than you need always succeeds. The check that catches both directions is short: walk the manifest, run each tool's `--version`, and assert the pin matches.

### Adopting from day one

Adopting the discipline at inception, rather than as a retrofit (§9), is stricter: there is no baseline and no exemption list to measure against. Two things follow:

- **The exemption you write on day one is the one nobody revisits.** A retrofit's exemption list is understood to be debt. A greenfield ignore is written as policy, reads as intent, and is invisible thereafter.
- **A high hit count is evidence about a rule's reach, not about its worthlessness.** Hundreds of findings for one rule in a young repo means the pattern is load-bearing there. That is a reason to look at what it covers, not to turn it off. Style rules hide bugs: a one-line `except` folded onto its `try` can hide a reused variable that a reader would otherwise catch.

A gate that runs only in CI is a gate the author meets after they have stopped thinking about the change. The pre-commit hook (item 4) is what makes the rest survive.

---

## 8. Before opening a PR

Section 7 inverted. Those are the claims you make at the start of a project; these are the same claims **verified** at the end of a change, which is the only point at which anyone finds out whether they were true. Linters catch very little of what review finds; each item below is a review round saved.

1. **Read your own diff as if someone else wrote it.** Most of what review finds is visible in the diff. A reviewer's attention is the scarcest thing in the loop; don't spend it on work nobody has read once.

2. **Run the real gate, not a convenient approximation.** `python check.py <file>`, not `ruff check <file>`. They disagree: check.py enables a stricter ruleset. Two tools disagreeing about one file is a bug signal on its own.

3. **Check coverage, and check what it is measuring.** `.coveragerc` traces subprocesses and does **not** honour `pragma: no cover`, both deliberately. Without subprocess tracing, code exercised through a CLI reads as untested; honouring the pragma adds coverage nobody earned. A naive setup is wrong in the flattering direction. See `docs/coverage.md`.

4. **Read the exemptions and the suppressions**: `python list-exemptions.py <file>` and `python list-suppressions.py <file>`. §2.6 says `pragma: no cover` belongs only on IO leaves. The `no-cover`, `noqa` and `directive-reason` checks refuse an exemption or suppression that states no reason, but **a reason is not a justification**: three words can satisfy the check and justify nothing. Only a person reading the list catches that.

5. **Confirm CI actually covers the file, by name.** The file list in `.github/workflows/check.yml` is manual. A green badge on a file no job touches is not a pass, it is a check that did not run.

6. **Run the thing itself, including its destructive paths.** Against a real fixture, and on a real machine if it touches one. A fixture you invented can only contain bugs you already suspect.

7. **Make sure the decisions have seams.** If a function mixes a decision with the IO around it, the decision cannot be tested and that is where the bugs will be. Extract the decision; leave a wrapper with no branches in it. That is what §2.6 means by "thin".

8. **Push, then confirm CI by SHA.** Not by badge, not by the last run you remember. After a force-push, a PR's checks can still be reporting the old commit.

9. **Have nothing outstanding before you request review, and freeze while it runs.** If the message that launches a review also proposes more work, the launch was premature. A round that reports on a tree that has moved is worth nothing.

10. **Leave nothing behind (§2.11).** Every temporary directory, worktree, background job and scratch script this change created is either removed or handed off, and no fixture carries a secret-shaped value. After the tests have run, list `/tmp` for your fixture prefixes and expect nothing.

The one to keep if only one survives: **a check that did not run looks exactly like a check that passed.**

---

## 9. Adopting this in a codebase you inherited

§7 is for a repo that starts clean. This section is for the common case: a codebase that is already valuable, already load-bearing, has never met the bar, and cannot be stopped while it learns. Adoption is a rate, not an event.

1. **Make the gate incremental before you make it mandatory.** A whole-repo gate on a repo with hundreds of pre-existing errors has two outcomes: a cleanup nobody scheduled, or `--no-verify` as a daily habit. Check the **staged files**, so the only code that has to meet the bar is code you were already editing. The number then falls as a side effect of doing the work. `install-hooks.py` does this.

2. **Record the baseline the same way the gate measures, and hold the line at "no worse".** Ratcheting needs a number to ratchet against. Commit a **per-file** run (`check.py --record-baseline`) and have the gate compare against it rather than against zero. Without that stored number, a file that got worse and a file that was always bad are indistinguishable.

   **Name it `quality-baseline.json` at the repo root.** The pre-commit hook looks for exactly that path and passes `--baseline` when it is there; with no such file the gate fails on any finding, which is what a clean repo wants. A fixed path rather than a setting, so a repo either has a baseline the hook uses or has none, never a baseline that is recorded, committed, and invoked by nothing.

   **Per-file, because that is what step 1 made the gate do.** A whole-repo run and a per-file run are different quantities in the same units, and comparing one against the other looks exactly like tool drift. Only tools that resolve across files (`mypy`, `phpstan`) differ between the two modes; tools that judge a file in isolation (`ruff`, `phpcs`, `rector`) do not. If a baseline disagrees with a fresh run on exactly the import-sensitive tools and nothing else, suspect scope before versions. Say "per-file" when you write the instruction down: "a full run" reads as "the full repo" as easily as "every tool".

   A per-file baseline is the **worse** measurement for `mypy` and `phpstan`: in isolation they cannot resolve imports and report more. That is the right trade: the gate can only compare against what it can reproduce, and the excess is mostly real findings rather than noise.

3. **Find out which checks are not running before believing the total.** An inherited repo will be missing toolchains a greenfield one installs on day one, and a check that did not run contributes zero findings. A total that covers one language while another is entirely unmeasured reads as authoritative. On unfamiliar code you have no intuition to contradict the number.

4. **Map the dependency graph before planning the order.** One search for cross-imports settles whether piecemeal extraction is cheap or a fantasy. Plans drawn before that measurement tend to be built around avoiding a cost that does not exist. Measure the coupling first.

5. **Let the work choose the order.** The component that most deserves a rewrite and the component you have open are rarely the same one, and only the second rewrite is free, because you were paying to understand that code anyway. `git log --since=<a month ago> --name-only` names the real order in one command.

6. **One component leaves at a time, and the tree runs between every step.** No freeze, no big-bang split, no branch that lives for a month. If a component's rewrite cannot be finished in a sitting, the unit is too large. The test of a good unit is that abandoning the project immediately after it lands leaves the repo better than before, with nothing half-migrated.

7. **Extract when you touch, never on a schedule.** The new repo gets created the day real work lands on that component, with the hook and CI on from its first commit. Whatever never moves is what nobody needed, and the effort spent on it is correctly zero.

8. **A component leaves by moving, and leaves nothing behind.** Copying it forward produces two versions that both keep receiving fixes, each holding a repair the other lacks. This is the vendoring anti-pattern arriving one convenient copy at a time.

9. **Treat the gotchas document as a coverage report.** A long file of "do not step here" is a list of defects no test was in a position to catch, so the mitigation lives in prose and has to be re-read before every session. Each entry is a test case waiting for a seam. Port them into the suite as components move, and let the shrinking of that file measure whether the seams arrived.

10. **Say in the README which packages meet the bar and which are grandfathered.** At any moment part of the repo does not comply, and a reader cannot tell an exempt package from a neglected one by looking. A gap that is written down is a decision; a gap that is discovered is a surprise.

11. **When a component cannot be extracted, strangle it instead.** Some components are one program rather than a collection. The strangler fig pattern (Fowler, 2004) routes call by call: find a point where the program already dispatches (a route switch, a file-extension switch), send one case to a new implementation, leave the rest on the old path, and repeat until the old path has no callers and can be deleted. Deleting the host is part of the pattern: a strangler that never completes leaves two systems and a facade.

12. **Pin current behaviour before rewriting it, including the parts that look wrong.** A characterisation test (Feathers, *Working Effectively with Legacy Code*, 2004) asserts what the code does today rather than what it should do. Write an assertion you know is false, run it, read the real value out of the failure, and paste it in as expected. Some surprising behaviour is load-bearing, and you will not know which until later. The gotchas file from item 9 is the shortlist. The bulk form, a golden master, is often already on disk as a cache of past results: freeze a copy and diff after the rewrite. Manufacture determinism first (timestamps, ordering, live dependencies), and delete a characterisation test the day you decide the behaviour it pins was wrong.

13. **Add the project file with the first change, not in a sweep.** §7.10 on a repo that has none. It is a few lines and changes nothing that runs, so it rides along with whatever change first touches the repo, per item 7. Fill `dependencies` from the imports, not from memory.

**The discipline arrives one file at a time, or it does not arrive.** On a repo with history, the only budget that reliably exists is the file already open on the screen, and any adoption plan costing more than that will be abandoned while still looking, from the commit log, like it is going fine.

---

## 10. Reading an instrument

§8 is about verifying claims on code you wrote. This is about the tools you point at something else to find out what is true: a driver, a running service, another machine, a message queue, an archive. The code under test may not be yours, may not be readable, and may not be changeable. The instrument is the only thing you control, and it is the thing that will be wrong.

1. **Ask whether the instrument can return a confident nothing.** An empty list, a zero count, an absent file, no output, exit 0. This is the best predictor of whether you will catch your own error, and it is a property of the tool you can check before running it. A wireless tool's "dump all stations" command can exit 0 with no output on a driver that implements only per-station lookup, while "get station X" on the same card returns a signal level. Nothing in an empty list looks wrong. Instruments that return a wrong *something* get caught by whoever ran them; instruments that return a confident *nothing* do not.

2. **Before running one that can return nothing, write down what a non-nothing would look like.** This is the positive control, stated as a prediction. The output is uncheckable from the inside; the prediction is what gets checked.

   **The control must run the instrument's configuration**, or it answers a different question. `ruff --isolated` against files the gate calls clean can report dozens of errors that are all line-length findings the project's own config allows. Zero and dozens are both correct answers to different questions.

   **A control that can only run where the bug is absent is not a control.** Pin the environment alongside the violation. For a gate that can fail to load its config, the matrix has three rows, and the middle one is the one that never gets written:

       config resolves, file dirty   ->  fail, issue count > 0
       config MISSING,  file dirty   ->  error. never pass, never skip
       config resolves, file clean   ->  pass, 0 issues

3. **When you build the instrument, make absence countable; never encode it in a value.** A wrapper that emits `counter=` both for a counter the hardware does not implement and for a counter reading zero has one encoding for two facts. A sentinel string (`unavailable`) is no better: the next parser crashes on it or coerces it. **Omit the field and print a count** (`fields=2/8`). Omission cannot be misread as a value, and a change in the count is itself a signal.

   **A wrapper that discards the exit status does this by construction.** A tool that writes a config-load failure to stderr and exits 2 with empty stdout parses as `[]`, and a wrapper that ignores the code publishes "pass, 0 issues". `check=True` is not the fix: many tools exit 1 for "found violations" and 2 for "could not run", so the wrapper needs both codes rather than an exception.

4. **Prefer the instrument that returns a quantity to the one that returns a verdict.** A number, a name or a unit is partly self-checking; a verdict is not. `443071.182s` in a column headed milliseconds is absurd on sight. A quantity also carries a moment: a count taken before a sleep and printed after it describes the earlier instant. Print a count beside the instant it describes.

5. **Ask what question the tool actually answers, not what you asked it.** The failing case is a tool that *succeeds*. A green CI badge on a file no job touches answers "did the jobs pass", not "is this file clean". The sharpest version is querying the wrong artefact and reporting the absence as a property of the thing: a driver's header lists numbered ioctls while its capabilities live as named variables in the binary's string table; a crash-log mount is empty because it was cleared. **Before concluding a system cannot do something, establish that you queried the place where the answer would live.**

6. **Your own output truncation manufactures silent failures.** An excerpt, a `tail` that turns a crash into a partial read that looks complete, a `head` that drops rows from a count, `$?` read after a pipe (the last command's status, not the tool's). Every one produces a plausible number. Measure exit status without a pipe (or use `PIPESTATUS`), and never conclude from an excerpt whose boundaries you did not choose.

7. **The adequate sampling window depends on a period you do not know yet.** Two machines running the same query can refresh a cache every 100 ms and every 300 s; a window that settles one is a fifth of a cycle on the other. Measure the period first, or state the window as a fraction of a period you are declaring unknown.

8. **A control that varies many variables at once feels stronger than a single-machine comparison and is usually weaker.** A table of differences between two machines reads as rigour because it produces an artefact; columns are not evidence of isolation. One machine varying one thing across its own history excludes a variable. State what a control is worth on each axis separately. And "not exposed" is not "absent": "structurally impossible" closes an avenue that "not plumbed out" leaves open.

9. **When two measurements disagree, suspect the instruments before the fact. When two agree, ask whether the agreement was designed.** Two people independently running the same command is one measurement, not a cross-check; label an agreement *designed cross-check* or *collision*. When two accurate instruments disagree, suspect the question: sockets in `SYN_SENT` and sockets in `ESTABLISHED` are both "connections".

10. **A guard often holds for a different reason than its comment claims.** The behaviour is right, the stated reason is wrong, and the next person edits against the reason. Prove a guard fires; do not prove it exists.

11. **Whoever is on the machine owns the facts about that machine.** Ask rather than infer. **A quoted measurement becomes the quoter's measurement in one hop, and the hedge does not survive the hop**: write *X measured this*, not *this is true*. Relay people verbatim or say plainly that you are inferring; never paraphrase a person into a table, because a table reads as settled.

12. **A brief written before a retraction is a confident wrong brief, and it is the durable artefact.** Correct it by stating **what survives**, not by striking text. Put a tool's known limits in the tool's own header, where the next reader already has it open.

13. **Before a destructive test, the thing at risk is whatever nobody owns.** The pre-flight has two halves: *what do I hold*, from everyone on the target, **and** *what does this machine hold that nobody claimed*: logs, caches, un-backed-up history. The second half needs someone assigned to it.

14. **A denominator you printed is not a bound the reader will apply.** `checked 3 file(s)` above `no errors` is read as "the corpus is clean". Put the denominator inside the verdict (`PASS (3 of 47 files)`), or refuse to emit a verdict when the scope is narrower than the target named. A tool that cannot say how much it covered says INCONCLUSIVE.

15. **A detector for this family is itself an instrument, and it returns a confident nothing about everything it has no rule for.** Its coverage is its rule list. And **a detector establishes that instances share a SHAPE, never that they share a FIX**: ten identically phrased findings invite ten identical repairs, and some of them will need a different one.

16. **An absence encoded as a number gets arithmetic done to it, and comes out plausible.** A parser that returns `-1` on failure, subtracted from a baseline, publishes a crash as a delta in range for a bad day. Return `None`, abort on a failed baseline instead of printing differences from nothing, and count unmeasurable rows separately.

17. **`returncode != 0` is a proxy for "the tool failed", and its strength is a property of the tool. Measure it per tool, with the arguments and config the wrapper actually uses.** `curl -s` exits 0 on a 404; `curl -fsS` is the fix. Four conventions among common linters:

        ruff, prettier      0 clean, 1 findings, 2 refusal     (0,1) rule correct
        cppcheck            0 clean AND findings, 1 refusal     (0,1) rule wrong
        node-run linters    0 ran, nonzero = runner crashed     (0,1) rule wrong
        phpcs               0 clean, 2 findings, 3 refusal      (0,1) rule harmful

    Even for ruff, rc=0 does not mean "nothing found" (a syntax error reports at rc=0) and rc=1 does not only mean "violations" (a missing path reports there too). The refusal code must never be a pass, and the success codes still require parsing the output. When two unrelated checks agree on which case to skip, that case is unrepresentative of the model both were built from: measure it by hand.

18. **A fix that makes the test pass can leave the defect class untouched. Ask which way the next gap fails.** Of two incomplete lists (terminators or continuations), prefer the one whose gaps degrade toward "unknown", because item 3 gives you a way to count unknowns.

19. **Nobody catches their own error by looking harder. It is caught by observing differently.** Move the window, add a control, drop the pipe, use real input. More attention on the same observation returns the same answer with more conviction.

20. **A success message over a no-op is the strongest form of this defect.** A tool that prints `sent (1 recipient)` with exit 0 for an empty body has reported a delivery that carried nothing. Check the payload before sending. And when a tool offers no way to correct a record, people stop correcting; fix the coupling in the tool.

21. **A peer's better-phrased claim will overwrite your own measurement, and you will not notice.** When you adopt someone else's formulation, re-read your own record first, because agreement is exactly the condition under which nobody re-reads anything.

**Knives and broken glass.** A knife is meant to be sharp; broken glass is sharp by accident. The test is whether the edge was designed. Ruff exiting 2 on an unloadable config, or cppcheck exiting 0 whether or not it finds defects, is a knife: documented and intended, and the reading is yours to get right. A linter that cannot start on the installed runtime, or a parser returning `-1` into a subtraction, is broken glass: a defect the next person will meet too. Documentation is evidence of intent, not intent itself.

The usual mishandling is **widening the question**: an instrument answers narrowly and the reader promotes it to a broader answer with nothing marking it. `"ruff": "pass"` honestly answers "did the parsed output contain issues", not "did ruff find nothing". As a tool author, stop issuing the invitation: report what was asked, at the width it was answered.

The through-line, §8 one layer out: **an instrument reports on itself, not on the world.** Exit 0 means the tool ran. An empty list means the tool had nothing to say.

---

## 11. Anti-patterns

Things that look like they save time but defeat the methodology:

- **Framework imports in the domain.** A framework `use`/`import` in the core package is a bug, not a shortcut. Once it's there, the package is no longer framework-agnostic.
- **Service location inside domain code.** `$container->get(...)` inside a service means the service has a hidden dependency. Constructor inject it.
- **Mutable value objects.** If `FileEntry` has setters, every piece of code that ever saw it is a suspect when state looks wrong. Make it `readonly`.
- **Framework-coupled tests in the core suite.** If the core's tests need the framework to run, the core isn't pure. Move the test to the delivery package's suite.
- **God adapters.** An adapter implementing six port interfaces at once is a sign the ports are carved wrong. Split the adapter or merge the ports.
- **Vendoring a shared library instead of depending on it.** Copying a library into a consumer gives two divergent copies and no contract. Depend on the package and let the package manager resolve it.
- **Skipping the hook.** `git commit --no-verify` is sometimes legitimate (you're mid-refactor, CI will catch it). Make it rare. If you're doing it daily, the gate is misconfigured or the code has accumulated debt that should be paid.
- **One mega-package.** Mixing domain, infrastructure, and delivery with no boundary between them makes the layering invisible. One package is not the problem; one package whose layers you cannot see is, and a package whose namespaces get out of hand is on its way there (§7). The layers are the point.
- **Placeholder content where silence would do.** Don't synthesize fake data to fill a slot that's meant to be optional (§2.7).
- **Reimplementing what the platform already does (§2.10).** The only anti-pattern here that passes every gate in §2.5, because a duplicate is correct code. Search first, and treat "could not look" as distinct from "not there".
- **Reading a search result as an answer.** A hit is an address. Summarising snippets back is the same error as quoting a search hit instead of reading the function it points at, and a pointer that no longer resolves is a finding about the pointer, not evidence the thing is gone.

---

## 12. Related reading

- `README.md`: what each tool does and how to install the toolbox.
- `check.py`: the gate itself, and the source of truth for what it enforces.
- `install-hooks.py` and `init-ci.py`: how the gate is installed into another repo.
- `list-exemptions.py` and `docs/coverage.md`: coverage exemptions and how coverage is measured.
- `tools/lab/README.md`: the lab index (§2.9).
- `find-dupes.php`: the composition root that consumes FileScanner (§3).
- Alistair Cockburn, *Hexagonal Architecture* (2005); Martin Fowler, *StranglerFigApplication* (2004); Michael Feathers, *Working Effectively with Legacy Code* (2004).
