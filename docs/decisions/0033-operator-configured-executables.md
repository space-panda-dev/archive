# 0033. Git executable paths are operator configuration

Status: accepted

Door and Archive invoke Git and git-annex as privileged mechanisms for records,
content, and location evidence. Looking them up through the ambient `PATH` at
use time lets an unrelated environment entry substitute an executable. Requiring
only system-owned installation paths would also exclude an executable installed
and maintained by the operator.

## Decision

**The operator may configure absolute paths to Git and git-annex.** These paths
are operational configuration under S-6, not policy or archival evidence. The
operator decides which installation to trust, including one they manage. The
path must be absolute, must resolve to an executable, and must be checked at the
execution boundary. Relative names and `PATH` lookup are not trusted defaults
for a mutation. Configuration may point through a package-manager symlink; an
operation must resolve the configured path to a concrete absolute executable
and invoke that resolved path. A changed executable identity between planning
and mutation must cause refusal and require a new plan. A new installation can be
selected by changing operator configuration, without changing archive meaning.

The selected executable and every path component used to reach it are trusted
operator-managed installation state. The operator keeps that installation
stable for the duration of each operation, including the instant the operating
system resolves the path for process creation. Identity checks before and after
execution refuse changes they observe; they cannot rule out a path or ancestor
that is replaced and restored between those checks. Archive does not promise
to defend against a concurrent writer with access to the selected installation.
This trust boundary applies equally to an operator-managed installation and a
package-manager symlink.

Arguments are passed as an argv vector with no shell evaluation. Proposal,
record, content, or remote data cannot select an executable or insert options.
Git and git-annex child processes use the same bounded control-output and
interruption-safe execution seam. This decision does not make an operator-chosen
executable safe by itself; it makes its selection explicit and checkable.

## Why

The trust source is the operator, not a fixed filesystem prefix. Explicit
selection supports user-managed installs while preventing a proposal or an
ambient `PATH` entry from silently choosing the program that changes archival
state. The exact configuration file format is mechanism and remains an
implementation choice.

On macOS, pathname-based process creation does not pin the executable image to
the file checked immediately beforehand. Requiring only protected system paths
would contradict the operator choice above; accepting stable operator-managed
paths makes that responsibility explicit without claiming a race-free spawn.

## Alternatives rejected

- **Find Git and git-annex on `PATH` for every operation.** The execution
  environment could substitute the program after validation.
- **Allow only system-owned directories.** It unnecessarily prevents the
  operator from managing their own installation.
- **Accept a command string.** Shell parsing would turn configuration into
  code and make argument boundaries ambiguous.
