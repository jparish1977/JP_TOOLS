## 10. Reading an instrument

§8 is about verifying claims on code you wrote. This is about the tools you point
at something else to find out what is true: a driver, a running service, another
machine, a message spool, your own archive. The code under test may not be yours,
may not be readable, and may not be changeable. The instrument is the only thing
you control, and it is the thing that will be wrong.

Written from 2026-08-16/17, when five sessions across two machines produced these
faster than anyone could count them: the running tallies on that thread reached
"the fifth instance today" in four separate domains, and one session reported
twelve in its own day, all caught, none reaching an artefact. The domains were a
wireless driver, a PowerShell launcher, `argparse`, `git`, a coverage baseline, a
security validator and a message spool. They have nothing in common, which is the
argument for the section existing.

1. **Ask whether the instrument can return a confident nothing.** An empty list, a
   zero count, an absent file, no output, exit 0. This is the single best predictor
   of whether you will catch your own error, and it is a property of the tool that
   you can check before running it.

   `iw dev wlp3s0 station dump` exits 0 and prints nothing on a BCM4360, because
   `wl` implements `get_station` and not `dump_station`. `iw dev wlp3s0 station get
   <bssid>` returns `signal: -54 dBm` on the same card, the same second. An entire
   night's central finding — *this hardware cannot report per-association
   telemetry* — rested on the first command, was called airtight in writing, and
   was wrong. Nothing in an empty list looks wrong. An empty list is a completely
   reasonable thing for a healthy system to return.

   The classification held across every instance anyone could attribute that night:
   instruments returning a wrong *something* were caught by the person who ran them,
   instruments returning a confident *nothing* were not.

2. **Before running one that can return nothing, write down what a non-nothing
   would look like.** This is the positive control, stated as a prediction, and it
   is the only thing that made a confident nothing self-catchable.

   Two were caught alone that night. In both the observer had already staked a
   specific claim: one had told Joe an install would buy channel utilisation, so an
   empty `survey dump` contradicted something they were on record for; one knew the
   archive held 411 rows, so `NO ROWS IN WINDOW` contradicted a number already in
   hand. Neither read the output harder. The output was still uncheckable from the
   inside. **The prediction was what got checked.**

   The same rule arrived independently thirty-four hours earlier, from a watcher
   throttle with no hardware in it: *failing state and expected state were both
   silence, and only a positive control separates them.*

   **And the control has to run the instrument's configuration, or its non-nothing
   answers a different question.** camhelp-docs, 2026-09-01, checking three files
   the JP_TOOLS gate had just called clean: `ruff check --isolated --select F,E`
   over the same files returned **19 errors** against the gate's 0. That is the
   positive control this item asks for, it fired, and filing it would still have
   been a false finding -- all nineteen were E501 under isolated defaults, which
   ignore the project's configured line length. Zero and nineteen were both correct
   answers to different questions.

   Worth its own paragraph because it was a false finding built on a REAL defect:
   the gate genuinely does mask a refused tool (item 3), so the number looked like
   confirmation of something already known to be true. A control that diverges from
   the instrument in ANY setting is measuring a second instrument, and the closer
   its answer sits to the one you expect, the less you will check it.

   **And a control that can only be run where the bug is absent is not a control.**
   romtools-helper, 2026-09-01, on a positive control pinned for the gate defect in
   item 3: it ran inside a project where `../JP_TOOLS` is a sibling, so the config
   resolved, ruff ran, and the control passed. It will pass forever, including in
   the world where the defect fires, because the environment that triggers the
   defect is the one environment the control never enters. Their words: a green
   light with extra steps.

   So pin the ENVIRONMENT alongside the violation. The matrix has three rows and
   the middle one is the one that never gets written:

       sibling present, file dirty  ->  fail, issue count > 0
       sibling ABSENT,  file dirty  ->  ERROR. never pass, never skip
       sibling present, file clean  ->  pass, 0 issues

   The last row is the guard proving it can stay quiet. The middle row is the only
   one that reproduces the defect, it costs a temp directory, and every suite that
   has ever tested this has had the outer two and not the middle. This is item 14
   one level up: a verdict without its denominator, and a control without its
   environment.

3. **When you build the instrument, make absence countable — never encode it in a
   value.** Everything above is about reading someone else's tool. This is the one
   move that stops you from writing the next one, and it is the only constructive
   item here.

   A wrapper printing `key=value` pairs emitted `beacon_loss=` for a counter the
   card does not implement. It emitted exactly the same thing for a counter reading
   zero. **Zero and absent, one encoding** — inside the tool built to investigate a
   fault whose entire character is silence. It was caught only because the two boxes
   disagreed; on the healthy card all eight fields are populated and the collision
   never appears.

   The fix was not a better sentinel. `tx_retries=unavailable` puts a string where a
   consumer expects a number, and the next parser either crashes on it or coerces it
   — which recreates the overloaded encoding one layer up, in the consumer. The fix
   was to **omit the field and print a count**: `fields=2/8`. Omission cannot be
   misread as a value, the count is stable per card so a change in it is itself a
   signal, and absence became something a consumer can see rather than something it
   has to infer.

   The rule that generalises: emit the same field set on every call, and publish the
   count. If a reader has to distinguish "no data" from "data is zero" by looking at
   an empty string, you have built the confident nothing from item 1 with your own
   hands.

   **JP_TOOLS' own gate does exactly this, which is why the item is here rather
   than in a footnote.** `check.py:60 run_ruff` shells out to ruff, discards
   `returncode`, and parses stdout. Ruff writes a config-load failure to stderr and
   exits **2** with empty stdout, so the wrapper reads `[]` and publishes
   `"status": "pass"`, `"total": 0`, exit 0. Reproduced 2026-09-01 in three lines, with a positive control proving the
   sample dirty:

       echo 'extend = "./does-not-exist.toml"' > ruff.toml
       printf 'import os\nx=1;y=2\n' > sample.py

       ruff check --isolated --select F,E .  ->  Found 2 errors   (control)
       ruff check .                          ->  exit 2
       check.py . --lang python              ->  ruff pass, total 0, exit 0

   `check=False` is not the bug, and swapping it for `check=True` is not the fix:
   ruff exits **1** for "I found violations" and **2** for "I could not run", so
   the wrapper needs both codes rather than an exception. Discarding the code
   collapses those two into the one value a reader trusts most. **"The tool found
   nothing" and "the tool never ran" arrive here as the same empty list, and the
   wrapper publishes the more flattering reading.**

   The uncomfortable half is that this is the program which ENFORCES this document.
   A methodology whose gate cannot tell a refusal from a pass certifies compliance
   it never measured, once per adopting project. Scope, from camhelp-docs the same
   morning and it narrows the claim: no config means no trigger, proven by a fresh
   `.ruff_cache` rather than asserted, so the defect needs a config that fails to
   LOAD. Not universal -- and it fires precisely where a project has configured its
   own rules, which is to say on the projects that took the methodology seriously
   enough to write a config at all.

   **CORRECTION, and it is the sharpest instance in this section because it is
   mine.** This item first said `run_mypy` "has the identical shape", and it does
   not. `run_mypy` passes `result.returncode` to `_status`, which returns `"error"`
   when the code is not in (0, 1). It was already correct, and so is `run_prettier`.
   I read the function in a window that ended at line 176 and the returncode use is
   at line 178, then reported absence from a view that stopped two lines short. That
   is item 6 below, committed to a file, by the person writing the file about it.

   The measured picture, which is better than the one I published:

       13 _status call sites, 12 of them subprocess-backed
        2 pass returncode and are correct      mypy :178, prettier :434
       10 do not, so the error path cannot fire for them
        9 of those 10 are caught by claude-config's lint
        1 is not: cppcheck :672, which parses result.STDERR

   **The mechanism was never missing. `_status` already implements exactly the rule
   the seven-row table in item 17 arrives at independently** -- not in (0, 1) is a
   refusal, 0 and 1 both still need stdout parsed. Eleven callers just never hand it
   the argument. So the fix is ten one-line changes rather than a redesign, and the
   report that called for a redesign was wrong in the direction that makes the
   author look more thorough. That is the direction to distrust in your own work.

   It also re-scopes the lint's blind spot, which I had guessed at and had wrong.
   It does not miss "wrapped stdout" reads: it skipped mypy and prettier because
   they are CORRECT, which is the rule working. It misses the runner that parses
   stderr instead.

4. **Prefer the instrument that returns a quantity to the one that returns a
   verdict.** A number, a name or a unit is partly self-checking; a verdict is not.
   `443071.182s` in a column headed milliseconds is five days, and the units made it
   absurd on sight. A boot-history scan for "ended without a shutdown record"
   returned six candidates, and three of them said `PM: hibernation entry` — the
   output named the event, and the name was not the event being looked for. Both
   were caught in seconds, by the person who made the error, with nobody else
   involved.

   **A quantity also carries a moment, and a report can move it.** A stress harness
   counted its open sockets, slept five seconds to hold the burst, then wrote
   `SURVIVED n=254 opened=254`. The count was taken *before* the sleep and printed
   *after* it, on a line whose plain reading is "254 were held for five seconds."
   Measured separately: sockets to addresses that answer no ARP are failed by the
   kernel with `EHOSTUNREACH` somewhere between three and five seconds, and vanish
   from `/proc/net/tcp` **while their file descriptors stay open and countable.** So
   `opened=` counts retained descriptors, not live connections, and the true
   concurrency at the moment of the SURVIVED line was near zero.

   The burst was real — 247 sustained 2.9 seconds — and shorter than the harness
   believed. Nothing was wrong with the number. What was wrong was the instant it
   described versus the instant it was printed beside.

5. **Ask what question the tool actually answers, not what you asked it.** This is
   the general form of everything above and it is worth stating separately, because
   the failing case is a tool that *succeeds*. `station dump` did not error. It
   answered "does this driver implement enumeration" perfectly, having been asked
   "does this card publish statistics." Nothing anywhere says *you asked for
   enumeration and this driver only does lookup.*

   §8's through-line — a check that did not run looks exactly like a check that
   passed — is one case of this. So is a green CI badge on a file no job touches.

   **The sharpest version is querying the wrong artefact and reporting the absence
   as a property of the thing.** A driver blob was searched for channel-congestion
   support by grepping its *header*, which declares numbered ioctls — and the answer
   came back "no chanim, no CCA, no survey counters, nothing", which went into the
   thread as a fact about the silicon. The capability is there. It is reached as
   *named iovars* through one generic ioctl, and those names live only in the blob's
   string table: `cca_get_stats`, `cca_stats`, `chanim_enab`, `interference`,
   `obss_coex`. One `strings` run — which the human asked for "for grins" — found
   them in minutes.

   The session that made the error tabulated it against its own two earlier ones,
   and the table is the best statement of this section anyone produced:

   | queried | actual source | what was concluded |
   |---|---|---|
   | `station dump` | `station get` | empty list read as *no data* |
   | `/sys/fs/pstore` | the archived record | cleared mountpoint read as *no crashes* |
   | `wlioctl.h` | the iovar namespace | numbered ioctls read as *the whole API* |

   Its own summary: *the data existed and my query could not see it — and every one
   of them I reported as a property of the hardware.* Three times, three artefacts,
   one shape. **Before concluding a system cannot do something, establish that you
   queried the place where the answer would live.**

6. **Your own output truncation manufactures silent failures.** Three sessions in
   one night each produced or nearly produced a confident wrong conclusion from a
   reading path, not from a measurement: a 100-character watcher excerpt that
   invented a disagreement which did not exist; a `tail -30` that turned a crash
   into a partial read looking complete; a `head` that dropped rows and reported a
   clean count. Two more read `$?` immediately after a pipe and got `head`'s status
   rather than the command's — twice, independently, hours apart, in different
   sessions on different boxes.

   The same session logged three of these inside a single install: `sudo -n` that
   "proved" nothing was whitelisted while reading a cached credential timestamp; an
   exit 1 that belonged to `sudo` refusing rather than to the guard under test; and
   the pipe. **Every one produced a plausible number.** The guard's real behaviour
   was established only on the third attempt, with no pipe and a live credential.

   Truncated output is indistinguishable from complete output, which makes this
   worse than mangling the input: a bad fixture at least produces a wrong answer.
   Measure exit status without a pipe, and never conclude from an excerpt you did
   not choose the boundaries of.

7. **The adequate sampling window is a function of a period you do not know yet.**
   Two machines, same room, same AP, same SSID, same band, same channel, the same
   `iw` query. On one the cache refreshes from received beacons every ~100 ms; on
   the other it refreshes only on explicit scans, roughly every 300 s. **The
   adequate window differs by three orders of magnitude between them.** Fourteen
   seconds was 140 cycles on the fast box and settled its cadence completely. Sixty
   seconds on the slow one was a fifth of a single cycle, and produced "it never
   refreshed once in sixty seconds of healthy connected operation" — the wrong
   conclusion, arrived at honestly, from a run four times longer than the one that
   worked.

   So *sample for longer* is weak advice. Measure the period first, or state the
   window as a fraction of a period you are declaring you do not know.

8. **A control that varies many variables at once feels stronger than a
   single-machine comparison and is usually weaker.** Two machines, a table of
   differences, real measurements from both sides — it reads as rigour, and the
   reason it reads that way is that **it produces an artefact**. Columns and units
   are not evidence of isolation.

   The only elimination that survived that night was one box varying one thing
   across its own history: four kernels, same card, same driver, the fault present
   on every one. It excluded kernel version on identical hardware and it needed
   `journalctl --list-boots`. An evening was spent reaching for a second machine
   while the answer sat in the first one's own log.

   State what a control is worth on each axis separately. The second box was a real
   co-located RF and AP control and near-worthless as evidence about the driver, and
   both halves needed saying.

   **The artefact whose shape implies a comparison it cannot support is the specific
   trap.** Once the same wrapper ran on both machines, the readable overlap was two
   fields of eight. A field-for-field table would have looked like the most rigorous
   thing anyone produced, and two thirds of its rows would have been columns with a
   number on one side only. Both sessions agreed in advance never to present one.
   Agreeing *before* the artefact exists is the only time that agreement is cheap.

   **This paragraph originally said those counters "have no counterpart on the
   degraded card and never will," and that was wrong.** Forty minutes later the
   session that supplied it retracted: the blob's own header carries `txfail`,
   `txretry`, `rxcrc`, `txnoack` and the rest, one `WLC_GET_VAR "counters"` call
   away. **Not absent — not exposed.** The practical advice survives unchanged,
   because a field you cannot read today is not a column you can put in a table
   today. The *reason* was wrong, and the reason is what the next person builds on:
   "structurally impossible" closes an avenue that "not plumbed out" leaves open.

   It is left in rather than quietly corrected because it is this section's own
   item 12 landing on this section. A peer's conclusion, repeated approvingly into a
   durable document while the thread that produced it was still moving, hardened
   into "never will" somewhere between their message and this paragraph. Nobody
   added the word. Distance from the measurement added it.

   **And the second machine earned its place for a reason nobody predicted.** Not as
   a control that proved anything — as the only place a difference was *visible*. The
   `beacon_loss=` encoding collision in item 3 could not be seen from the healthy
   card, where every field is populated. It took the degraded box to expose a defect
   in the tool built to measure it.

9. **When two measurements disagree, suspect the instruments before the fact.**
   Cheap, and it was right more often than not that night.

   **And when two agree, ask whether the agreement was designed.** Twice in one hour
   two sessions on the same machine independently ran the same read-only command —
   a fleet sweep, then `dkms status` — and posted matching results inside the same
   minute. Matching numbers from two sources read as corroboration. These were
   collisions: nobody planned a cross-check, so the agreement carries no more
   information than one run of the command, while *looking* like the strongest
   evidence in the thread.

   Read-only measurement feels free, which is why it duplicates: nothing is
   contended, no claim is needed, and the second runner has no way to know. The fix
   is not to stop duplicating. It is to **label the provenance of an agreement** —
   *designed cross-check* or *collision* — because the reader cannot tell them apart
   and the difference is the entire evidential value.

   **And when two accurate instruments disagree, suspect the question.** A burst test
   reported 254 sockets; a kernel-side sampler on the same machine, during the same
   firing, peaked at 20. Both numbers were correct. One counted sockets in
   `SYN_SENT`, the other counted `ESTABLISHED` — and the sweep targeted a /24 where
   most addresses are empty, so nothing ever reached `ESTABLISHED` and a count of it
   read approximately zero, *correctly*, while 247 connections were genuinely
   pending in the driver.

   Neither instrument was wrong and neither measurement was wasted. **The question
   "how many connections" had not specified a state**, and two sessions spent
   fifteen minutes reconciling numbers that were never in conflict. A 150 ms trace
   settled it: 247 sustained for 2.9 seconds. Before comparing two counts of the
   same thing, agree on which state you are counting — the ambiguity lives in the
   noun, not in the tools.

10. **A guard often holds for a different reason than its comment claims.** A
   `switch -CaseSensitive` was inert because the conditions were scriptblocks;
   deleting it changed no observed behaviour. A comment claimed an argument was
   *refused, not passed through* — true in effect, wrong in mechanism. The
   behaviour is right, the stated reason is wrong, and the next person edits against
   the reason. Prove a guard fires; do not prove it exists.

11. **The session on the box owns every fact about that box.** Three times in one
    night a session asserted a fact about a machine it was not on — a grep, a kernel
    version, and a package hold — and each time the other session caught it. Never
    the speaker. Ask rather than infer, and take the answer.

    **Whether you are on the box is itself a fact to check.** This section's author
    told five recipients "I am on neither box" while sitting on the subject machine,
    having read a peer listing whose first line said the other session was *local*.
    The evidence was in the first thirty seconds of the session and a human had to
    supply the correction.

    **A quoted measurement becomes the quoter's measurement in one hop, and the
    hedge does not survive the hop.** The same author re-quoted two other sessions'
    agreement that two machines shared an SSID, BSSID and channel — approvingly,
    inside an argument about something else. Two messages later a third session had
    marked its own blocking objection *satisfied* on the strength of it, attributed
    to the quoter, who had measured none of it. Two smaller attributions drifted the
    same way in the same message. Nobody did anything wrong at any step.

    So mark provenance in the text, not in your memory of where it came from: *X
    measured this*, not *this is true*. And when a fact arrives attributed to
    someone, check whether they measured it or repeated it — the outbound half of
    "verify what peers tell you", which is the half nobody writes down.

    **An inferred instruction is the expensive version of this, and it has no
    defence at the receiving end.** The same author published a four-row table of
    who-does-what under the heading *routed by Joe just now*. Three rows were what
    Joe said. The fourth was an inference, unmarked, wearing his name — and it
    happened to be correct, which is worse, because a wrong guess gets contradicted
    and a right one just quietly becomes policy. Another session spent an hour
    weighing a real instruction it had been given against that guess, unable to tell
    them apart, and logged it as an unresolved conflict rather than resolving it.

    That session named the distinction that matters, and it is not the obvious one:

    - **Mis-heard** — a session takes an instruction meant for someone else as its
      own. Catchable at the receiving end, by asking.
    - **Mis-delivered** — an instruction meant for another session is typed into
      *this* one, correctly addressed, with no marker of any kind. Nothing in the
      message distinguishes it from every other instruction that session received.

    This paragraph first said mis-delivery was "not catchable at the receiving end
    by any means." **That was too strong, and it is the same error this section
    warns about twice already** — an impossibility asserted where a mechanism
    exists. The case *was* caught, within the hour, by one: **verbatim relay.** A
    third party quoted the human's words rather than summarising them, the receiving
    session compared that copy against what it had been handed directly, and the
    mismatch was visible in a single read.

    So the rule is constructive rather than despairing. **Relay a human verbatim and
    misrouting becomes detectable; paraphrase and it does not.** An addressee filter
    cannot substitute — it faithfully delivers a wrongly-addressed instruction and
    suppresses nothing, because the addressing is the thing that was wrong. A filter
    reduces volume. Only a second copy of the original words detects misdelivery.

    Quote a person verbatim, or say plainly that you are inferring. Never paraphrase
    a human into a table: a table reads as settled, and the format is itself a claim.

12. **A brief written before a retraction is a confident wrong brief, and it is the
    durable artefact.** The memory file for that investigation still said
    *per-association telemetry does not exist — THIS IS THE PART THAT IS AIRTIGHT*
    and *the candidate detector is DEAD*, hours after both had been retracted on the
    thread. The conversation self-corrected continuously; the file a future session
    loads first did not.

    Correct it by stating **what survives**, not by striking text. A reader who sees
    only strikethrough concludes the whole section was wrong, and usually most of it
    was not.

    The positive form, done well the same night and worth copying: on learning from
    driver source that six of eight counters are structurally absent on that card,
    the session put it in the **tool's own header** — under *read this before
    investigating a blank*, naming the two ioctls and stating plainly that
    `fields=2/8` is the healthy reading — rather than in the thread where it was
    discovered. Its reasoning is the rule: **a limit that exists only in a mailbox is
    not a limit anyone will meet again.** Put the finding in the file the next reader
    will already have open, and record the uncomfortable half there too — in that
    case, that the three counters which would reveal a link holding association and
    passing nothing are exactly the three that do not exist.

13. **Before a destructive test, the thing at risk is whatever no session owns.**
    A deliberate whole-machine hard-lock was authorised on a box running three
    sessions. Each of the three could enumerate what *it* held — a draft in a temp
    directory, a sampler's output file, a modified memory file — and each secured
    its own. **The largest volatile thing on the machine belonged to none of them:
    three hours of session transcripts, in no restic snapshot since 00:22, holding
    every measurement and every retraction the night had produced.**

    It was caught with minutes to spare by the session that thought to ask what the
    *fleet* would lose rather than what it would lose. Nobody's state report covered
    it, and no state report ever would have: the template asks each session what it
    holds, and shared, unowned state falls precisely between the answers.

    So the pre-flight for a destructive act has two halves, and only the first is
    natural: *what do I hold* from every session on the target, **and** *what does
    this machine hold that nobody claimed*. Backups, logs, transcripts, caches, the
    board itself. The second half needs someone assigned to it, because it is nobody's
    by construction — which is the same reason it is the half that gets skipped.


14. **A denominator you printed is not a bound the reader will apply.** Item 4 says
    prefer a quantity to a verdict. This is the failure that survives doing so: the
    quantity is right there, and the verdict is still read wider than it.

    camhelp-docs, 2026-09-01: `checkdocs` printed `checked 3 file(s), 811 lines` and
    reported no errors. Three files, not the corpus. The scope line was accurate,
    present, and unread, because a reader takes the verdict and skips the line under
    it. Every other instance in this section is an instrument that stayed SILENT
    about its scope, so this is a different failure reaching the same outcome -- and
    it is the one that defeats an honest tool.

    The fix is not a louder scope line. Put the denominator INSIDE the verdict, so
    that no reading of the verdict is available without it (`PASS (3 of 47 files)`,
    not `PASS` above a census), or refuse to emit a verdict at all when the scope is
    narrower than the target the caller named. A tool that cannot say how much it
    covered should say INCONCLUSIVE, which is the one word no reader rounds up to
    good news.

15. **A standing detector for this family is itself an instrument, and it returns a
    confident nothing about everything it has no rule for.** The last place anyone
    thinks to apply item 1 is the guard written to enforce item 1.

    Measured by claude-config-advisor, 2026-09-01, pointing this fleet's
    overloaded-empty lint at the `check.py` defect above: it **ran**, parsed the
    file, and reported three findings at other lines, with a positive control
    proving it fires. None of the three was `run_ruff` ignoring an exit status. It
    carries two Python rules -- an except handler returning a falsy value, and
    `.get()` with no default in an f-string -- and neither can see "ran a
    subprocess, never read its exit status, treated empty stdout as clean."

    Note what a CLEAN run of that lint would have licensed: a claim that the file
    holds no overloaded empties, over the file carrying the worst one in the
    toolkit. **A detector's coverage is its rule list, and its output is a verdict**
    -- item 14, arriving through the door marked "we already have a lint for this".

    **A clean run of that detector is still not evidence of absence, by its own
    design.** The rule skips a result that ESCAPES the enclosing scope, because the
    caller might check it and the lint cannot see that. Correct, and the cost is
    exact: sunblade2000-publish ran it over their own files, got CLEAN, and had the
    defect anyway -- their result escaped and no caller checked it. **The lint was
    right and the bug was real at the same time.** Any count it produces is a count
    of instances of the shape it can see, which is the reading item 14 asks for and
    the reading nobody applies to a tool they just installed.

16. **An absence encoded as a number gets arithmetic done to it, and comes out
    plausible.** Item 3 is about absence encoded in a value. This is what happens
    next, and it is worse than a confident nothing, because a confident nothing at
    least looks like nothing.

    romtools-helper, 2026-09-01, in `coverage_gate._measure_one`: it parsed a
    subprocess's stdout, never read returncode, and returned **-1** on a failed
    parse. That -1 was then SUBTRACTED from a baseline, so a crashed measurement
    would have been published as a coverage delta of about **-460**. Not an error,
    not a blank, not a zero. A number, in range for a bad day, in the column people
    read. In the file whose entire subject is instruments that misreport.

    Blast radius, measured rather than assumed: the published deltas ran -164 to
    +17, nowhere near -460, so it was latent and never fired. That measurement is
    the second half of the finding and it is the half usually skipped.

    **A CORRECTION'S blast radius needs measuring too, and I skipped it on my own.**
    Retracting the `run_mypy` claim in item 3, I told four seats they were "carrying
    the wrong version because I sent it". romtools-helper checked rather than
    accepting it: everything they had published named the ruff arm only, and the
    per-arm split they had relayed came from romtools-docs' measurement, which had
    mypy on the working side. One of the four was clean. They were careful to call
    that luck rather than care -- they only ever had a ruff-shaped probe -- which is
    the right way to report it and does not make the count less wrong. **An
    apology is a claim about other people's state, and it is as assertable without
    evidence as any other.**

    The fix is the shape item 3 already prescribes, and worth restating because the
    arithmetic makes it non-obvious: return None rather than a sentinel, let a
    failed BASELINE abort the table rather than print differences from nothing, and
    count unmeasurable rows separately instead of letting them read as a delta of
    zero. **A sentinel that is a number will be treated as a number by the next line
    of code, and that line is usually a subtraction.**

17. **`returncode != 0` is a proxy for "the tool failed", and its strength is a
    property of the tool. Measure it before you build on it.**

    sunblade2000-publish, 2026-09-01, having just made this exact fix badly: they
    had the item 3 shape, fixed it by checking returncode, then ran a negative
    control against deliberately bad input and the fix did **nothing** -- because
    `curl -s` exits 0 on a 404 and hands back the server's HTML error page. The
    failure was never an empty stdout with a nonzero exit. It was a plausible file
    with a clean exit. `curl -fsS` was the actual fix.

    Taking that caution seriously is what produced the numbers below, and they came
    out the other way for ruff, which is the point: the answer is per tool and you
    do not get to assume either result. Measured 2026-09-01, ruff 0.16.1:

        clean config, dirty file      rc=1   2 issues on stdout
        unresolvable extend           rc=2   stdout EMPTY
        malformed TOML                rc=2   stdout EMPTY
        invalid rule code in select   rc=2   stdout EMPTY
        unknown top-level key         rc=2   stdout EMPTY
        target does not exist         rc=1   1 issue on stdout (E902 io-error)
        file with a syntax error      rc=0   1 issue on stdout (invalid-syntax)

    Every config failure is rc=2 with empty stdout, so for THIS tool returncode is a
    strong discriminator and the fix is sound. But the naive version of that fix is
    still wrong in both directions: **rc=0 does not mean "nothing found"** (a syntax
    error reports at rc=0) and **rc=1 does not only mean "violations"** (a missing
    path reports there too). The rule that survives is narrower than "check the exit
    code": rc=2 is the refusal and must never be a pass, while 0 and 1 both still
    require parsing stdout.

    **Then the same measurement on a second tool inverted the convention, which is
    the whole reason this item is not "check the exit code".** cppcheck, measured
    2026-09-01 with check.py's own argument list:

        file with defects             rc=0   6 findings on STDERR
        clean file                    rc=0   0 findings
        target does not exist         rc=1   stderr COMPLETELY EMPTY
        unknown flag                  rc=1   stderr COMPLETELY EMPTY

    Backwards from ruff in both halves. Findings exit 0, and rc=1 is the REFUSAL.
    So `check.py`'s existing `_status` rule -- error when the code is not in (0, 1)
    -- returns **"pass"** for a cppcheck that never ran, and there is no second
    signal to fall back on, because the refusal prints nothing at all.

    **The trap this sets is the point.** The obvious fix for the ten runners that
    discard their exit status is to pass `result.returncode` into the function that
    already handles it. For nine of them that is correct and complete. On cppcheck
    it changes nothing, silently, while looking exactly like the other nine in the
    diff -- a fix that closes the review and leaves the defect. That is
    sunblade2000-publish's curl warning arriving on a different tool, and the reason
    to take a caution as a named test is that the test finds the case the caution
    did not predict.

    **Two independent instruments failed at the same site.** claude-config's lint
    flags nine of the ten and misses cppcheck, because it parses stderr rather than
    stdout. The `(0, 1)` convention covers nine of the ten and misses cppcheck,
    because its codes are inverted. Neither miss caused the other, and a reader
    trusting either one would have shipped the same hole. **When two unrelated
    checks agree on which case to skip, that case is not rare, it is
    unrepresentative of the model both were built from** -- and it is the one to
    measure by hand.

    **Measuring the rest found a SECOND deviation, and this one is not synthetic.**
    Installing the five runners this box was missing made the stylelint arm
    *appear* to work and not work. stylelint 17 uses `import ... with { type:
    'json' }`, Node here is v18.19.1, and the runner dies on startup. Measured
    against `a{color:#FFF;;}`, a file with several real violations:

        node jp_stylelint.mjs bad.css   ->  rc=1, stdout 0 bytes, stderr 837
        check.py bad.css --lang css     ->  stylelint status "pass", 0 issues

    A manufactured pass sitting next to a genuine `fail` from prettier in the same
    output, so the block looks healthy. No broken config, no contrived input: an
    adopter whose Node lags a major version gets this, silently, today.

    **And rc=1 is the crash.** These arms shell out to a Node script, so a nonzero
    code means the RUNNER died, while findings come back as JSON on stdout with
    rc=0. Under the `(0, 1)` rule that crash reads as "found violations", falls
    through to `"fail" if issues else "pass"`, and with no issues it is a pass. So
    the obvious remediation -- hand `result.returncode` to the function that already
    handles it -- fails here exactly as it fails on cppcheck.

    Three conventions among six runners measured:

        ruff, prettier      0 clean, 1 findings, 2 refusal   (0,1) rule CORRECT
        cppcheck            0 clean AND findings, 1 refusal   (0,1) rule WRONG
        stylelint via node  0 ran, nonzero = runner crashed    (0,1) rule WRONG

    **The sharper half is that the lint DID flag stylelint** at :257, unlike
    cppcheck. So it named the right site and the standard fix for that site is
    still wrong. A detector can be correct about WHERE and silent about WHETHER THE
    OBVIOUS REPAIR WORKS, and nothing about a green lint run distinguishes the two.

    The table is kept whole rather than compressed to its conclusion. The
    conclusion is one line and cheap to restate; the seven rows are what a later
    reader cannot re-derive without a temp directory and an afternoon, and a
    conclusion outlives the method that produced it far too easily. For the same
    reason it lives in exactly one file. sunblade2000-publish declined to copy
    these numbers into their own notes on the grounds that two copies is how a
    measurement drifts from its method, which is the right call and the opposite
    of the instinct to spread a useful finding around.

    **And a caution can be wrong about its direction and still be the reason you
    got the answer.** The warning here was that rc=0 might hide a FAILURE. What the
    table found was rc=0 hiding a FINDING, which is the same defect in the proxy
    with the sign flipped, and the more dangerous of the two: `rc == 0 -> pass`
    would have turned every syntax error in the fleet into a clean bill of health,
    shipped under a commit message about making failures loud. The prediction
    missed. The test it prescribed did not. **Treat a peer's caution as a named
    test rather than as a claim to be graded** -- the version of it that turns out
    wrong is still the version that gets run, and running it is the whole value.

    **None of which you can do if your shell hands you the wrong number.** `$?`
    after a pipeline is the LAST command's status, so `tool ... | tail -12; echo $?`
    reports on `tail`, which essentially always succeeds. It manufactures a clean
    exit in precisely the investigation where a dirty one is what you are hunting
    for, and the reading looks like the good news you were hoping not to find.

    **The seven-row table above was wrong on its first run, for exactly this
    reason.** `ruff check . 2>&1 | tail -10; echo "exit=$?"` printed `exit=0` for
    the unresolvable-extend case that actually exits 2, and that number went into
    this file. It exists in its corrected form only because it got re-run without
    the pipe. Put that first, ahead of the tidier version of the same slip:
    romtools-lead read a control's exit as 0 the same hour and nearly filed it as an
    overloaded success, their third instance of the family that day, and theirs was
    a commit they could re-run. Ours silently produced a wrong table in a document
    whose subject is instruments that misreport, and that is the version which would
    have shipped. Use `PIPESTATUS`, or do not pipe the command whose status is the
    measurement.

18. **A fix that makes the test pass can leave the defect class untouched. Ask
    which way the NEXT gap fails.** This item was first written here as foresight
    and corrected by the person it describes, which matters, because the foresight
    version teaches the wrong thing. What happened was a repair.

    romtools-lead, 2026-09-01: a CHR$ falsifier whose `_const` read a PREFIX of an
    expression as the argument, so `CHR$(-45 * (A$ = ""))` was reported as the
    constant -45. Ordinary MSX BASIC, where a true comparison is -1, so `-n * (cond)`
    emits character n or nothing and never leaves 0..255. **The falsifier reported
    five violations and all five were its own defect**, three hours after shipping.
    Their verdict is the rule: a falsifier that reports violations the corpus does
    not contain is worse than none, because it is trusted.

    **The obvious approach was tried FIRST and it was the wrong one.** They wrote
    the enumeration, `CLOSERS = (0x29, 0x2C)`, and it failed inside about thirty
    seconds against a real corpus case -- `IF STICK(0) = 1 THEN`, where `THEN` ends
    an operand and the list lacked it. The inversion came after that, not before.

    **The near-miss is the content of this item.** Adding `THEN` to the list would
    have worked. It would have passed, it would have looked like the fix, and it
    would have left the next missing terminator free to invent a constant later.
    The repair that closes the class instead of the instance is in the `controls.py`
    comment, quoted rather than reconstructed:

        enumerating everything that can END an operand means listing ) , THEN :
        AND OR and end-of-line, and missing one produces a FALSE constant. Missing
        a continuation instead produces None, which this tool already counts as a
        variable or an expression -- a real answer. Err toward None.

    That two-failure-directions reasoning is theirs. The generalisation that
    **completeness was never the axis, since neither list is ever complete**, is
    mine and they neither said nor thought it; it is recorded as a separate claim
    rather than folded into the quote, because a reconstruction attributed to
    someone else is a fact about them that they cannot correct once it is in a file.
    Both readings agree on the move: of two incomplete lists, prefer the one whose
    gaps degrade toward None, because silence is the failure item 3 gives you a way
    to count.

19. **Nobody in this section caught their own error by looking harder. Every one
    was caught by observing differently.** Put last because it is the only item that
    answers "so what do I actually do", and because the answer is not the one the
    situation invites.

    Four from 2026-09-01, all inside this one investigation:

        the window ending at 176 when the answer was at 178
            caught by re-reading with the window MOVED
        a returncode check reading the wrong process's status
            caught by pointing a POSITIVE CONTROL at it
        item 17's seven-row exit-code table, wrong on first run
            caught by RE-RUNNING WITHOUT THE PIPE
        a falsifier reporting five violations that were its own
            caught in thirty seconds by a REAL CORPUS CASE

    romtools-helper's framing, on the first two: neither of us found ours by reading
    code, and both remedies are "run the thing again differently" rather than "look
    harder". Move the window, add a control, drop the pipe, use real input.

    The reason looking harder cannot work is item 1. The error is invisible from
    inside the reading that produced it -- that is what makes a confident nothing
    confident -- so more attention spent on the same observation returns the same
    answer with more conviction behind it. **Attention is not the scarce resource.
    A second, differently-shaped observation is.**

Joe, 2026-09-01, on this section's whole subject, and it is better than the
sentence that used to open here:

> a knife is meant to be sharp, if it cuts you then you have mishandled it

His own immediate qualification, and it is the half that makes the metaphor
usable:

> broken glass is ALSO sharp

So sharpness alone acquits nothing. **The test is whether the edge was designed.**
A knife's edge is a specification; broken glass's edge is a defect. Both cut, and
only one of them is your fault for grabbing. That splits this section cleanly, and
the split is not the one I first wrote:

**Knives -- documented, intended, and the reading was mine to get right.**
`iw station dump` exits 0 and prints nothing because the driver implements
`get_station` and not `dump_station`. ruff exits 2 on a config it cannot load.
cppcheck exits 0 whether it finds defects or not and 1 when it refuses, which is
its published behaviour. The overloaded-empty lint skips results escaping their
scope on purpose, as a false-positive budget. Nothing there needs fixing but the
handling.

**Broken glass -- sharp by accident, and nobody signed off on the edge.**
stylelint 17 cannot start on Node v18.19.1. `coverage_gate` returned -1 from a
failed parse into a subtraction. The CHR$ falsifier read a prefix as a whole
operand. And `check.py` labels a claim about a parsed list `{"tool": "ruff",
"status": "pass"}` -- a field nobody specified to mean that, which is exactly why
it reads as the broader claim. These are defects. Handling them better is worth
doing and is not the whole answer, because the next person meets the same edge.

The practical discriminator is cheap: **ask whether the surprising behaviour is
documented.** If the vendor wrote it down, you are holding a knife and the fix is
in your grip. If nobody ever specified it, you are holding broken glass and
somebody should sweep it up.

**And the specific mishandling has a name.** Joe, the same day, after correcting
romtools-lead twice on this one lesson:

> thats not the probes fault, thats the users fault.

The three versions matter because they give an adopter different instructions.
"Probes are the unguarded surface" says harden the probe. "It dissolved into the
probe's crudeness" still blames the instrument. The third says the instrument
answered correctly and **the reader promoted a narrow answer into a broader one,
in their head, with nothing marking it** -- so the instruction is to name the
question the tool actually answered and notice the moment you widen it. The
widening is invisible, happens in the reader, and leaves no artifact to review.

romtools-helper turned that on this section's own headline finding, and they are
right: **`check.py`'s `"ruff": "pass"` is not a lie.** It is the honest answer to
"did the parsed output contain issues". It becomes wrong only when read as "ruff
found nothing". The same re-reading applies to a `9 uses` count, to a solver's
`0x8002 x445`, and to a control that answered "did any module exit non-zero over
THESE five disks" three times correctly and was read as "does any module fail".
None of those tools lied. Each answered narrowly and was read broadly.

**One qualification, mine.** A narrow answer labelled with a broad word is an
invitation, and a wrapper that emits `{"tool": "ruff", "status": "pass"}` is
labelling a claim about a parsed list with the name of the tool and the word
`pass`. The promotion is still the reader's, and the fault sits where Joe put it.
But the design lesson from item 14 runs the other way and both hold at once: stop
issuing the invitation. Report what was actually asked, at the width it was
actually answered.

Which is the through-line, and it is §8's one layer out: **an instrument reports
on itself, not on the world.** Exit 0 means the tool ran. An empty list means the tool had
nothing to say. Neither is a statement about the thing you were pointing it at, and
the gap between those two readings is where a night's work goes.
