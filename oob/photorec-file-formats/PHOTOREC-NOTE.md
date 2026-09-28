# PhotoRec's file-format list, and the interface that is not in it

Kept because `recover.py` drives PhotoRec non-interactively, and type names
taken from this page are not all names PhotoRec accepts. The page is the
vendor's own list of what PhotoRec can recover; PROVENANCE.tsv records where
and when it was fetched, with its hash.

## Read the version line before using this list

The page describes the CURRENT PhotoRec. Ubuntu 24.04 ships **PhotoRec 7.1,
July 2019** (`testdisk 7.1-5+nmu1build2`), and the two do not agree.

## The trap this find exists to record

**The documented list is not the list of `fileopt` keywords, and they overlap
enough to look like the same list.** `html`, `php`, `py`, `rb`, `pl`, `java`,
`csv`, `tex`, `rtf`, `svg`, `docx`, `xls`, `xlsx`, `odt`, `ods`, `pptx` and
`epub` all appear on this page, and PhotoRec 7.1 refuses every one of them as a
`fileopt` name with `Syntax error in command line`.

They are not missing. They are **sub-signatures of a family**: enabling `txt`
alone turns on 167 first-level signatures, which is where the source and markup
formats live. The page lists what comes back; `fileopt` selects the family that
brings it back.

## The other half is documented nowhere

`/cmd` and `fileopt` appear in neither `man photorec` (which documents `/log`,
`/debug`, `/d` and nothing else) nor this wiki. The syntax `recover.py` depends
on -- `fileopt,everything,disable,<family>,enable,...,search`, with a toggle
after EVERY family name -- was established by running PhotoRec 7.1 against a
fixture, not by reading anything. `tests/test_photorec_fileopt.py` is where
that evidence lives, and it re-derives it on every run rather than trusting
this note.

So: this find answers "what can PhotoRec recover". It does not answer "what may
I put in a `/cmd` line", and nothing published does.
