# 0017. Licensing: copyleft code, public-domain contracts

Status: accepted

Archive is meant to be open and to outlive its author. Two things would defeat
that: a closed fork that takes the code and gives nothing back, and a hosted
service that runs a modified Archive for other people without sharing the
changes. The contracts pull the other way. They are copied into every archive
and must be readable, and implementable, by anyone decades from now, with no
question of who owns them.

## Decision

**The code is licensed under the GNU Affero General Public License, version 3
or later** (`AGPL-3.0-or-later`). Anyone may use, change, and share it; anyone
who shares a modified version, or runs one as a service that others use over a
network, must offer its source under the same terms. "Or later" lets the code
move to a future version of the licence without needing every contributor, or
their heirs, to agree.

**Everything else is dedicated to the public domain under CC0 1.0**
(`CC0-1.0`): the specification, contracts, and schemas, and all
documentation. Anything that ends up inside an archive must carry no conditions,
so an independent reader or a whole new implementation never has to ask whether
it is a derivative work. The documentation shares the dedication because the
specification restates the vocabulary and invariants word for word, and text
should move between them without changing terms.

**Archives are never covered.** An archive created or managed with Archive is
not a derivative work of Archive. Its content is its owner's, under whatever
terms the owner chooses, and the copy of the specification inside it is CC0.
Publishing Archive's source is a distribution of the code, never of anyone's
archive.

**Which files are which is declared in one place:** `REUSE.toml` at the
repository root, following the [REUSE](https://reuse.software) convention, with
the licence texts in `LICENSES/`. `make check` fails when a file matches no
annotation, so a new kind of file forces the choice rather than inheriting one
silently.

**Copyright belongs to the Archive contributors.** William Britt Mathis started
the project and holds the copyright in the original work. Contributions are
accepted under the [Developer Certificate of Origin](https://developercertificate.org):
each commit is signed off (`git commit -s`), and its author keeps their
copyright while licensing it under the terms above.

Licensing is settled before the source is public. While there is one author,
changing it is a single commit; after the first outside contribution, it is
not.

## Rejected

- **MIT or Apache-2.0 for the code.** Both allow exactly the closed fork and
  closed service this is meant to prevent.
- **GPL-3.0.** Its obligations start at distribution, so a hosted service on
  modified code owes nothing.
- **GPL-2.0.** No patent grant, no network clause, and incompatible with
  GPL-3.0 and Apache-2.0 code. Nothing here needs it.
- **Version 3 only.** It freezes the licence when the author can no longer
  agree to a change.
- **One licence for everything.** Copyleft on the specification would make every
  archive, and every independent reader, a licensing question.
- **Copyfarleft, such as the Peer Production License.** It charges capitalist
  firms and frees co-operatives, which takes the code out of the free software
  commons and needs enforcement no one will do.
- **Ethical-source licences, such as the Hippocratic License.** They hand the
  definition of harm to whoever litigates it, and they are not free software.
- **A contributor licence agreement.** It would keep the option of selling
  other terms, which this project does not want, and it puts off the
  contributors a copyleft project exists for.
