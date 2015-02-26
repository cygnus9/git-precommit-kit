# Git Precommit Kit

Because remembering stuff is a computer's work.

## Introduction

This is a toolset to help you validate your source code, in any way you please,
before you commit it. Typically, you'd run style checkers, static analysis,
unit test suites, and so on, to ensure that the code you check into your
version control system is (a) of good quality and (b) conforms to your teams
coding style to minimize merge conflicts.

GPK is a tool for keeping you honest, as early as possible, so you don't have
to go back and fix things.

## How does it work?

Git already has `pre-commit` hooks. GPK uses those to do its work. However,
hooks live outside your git repository and are finicky to write. GPK has a
single precommit hook that shells out to scripts and config files that live
inside your repository to make the checks part of your project, version
controlled and configurable by anyone. 

Everybody still has to install GPK locally once, but after that any changes you
make go through regularly versioned git files.

## Limitations

Currently, GPK is client-side only, and it's not possible to FORCE people to
use it. As checks can be skipped, and it seems like this is something you'd
occassionally want, I'm still debating how to implement this in the best way.

So GPK is currently still opt-in.

## Installing into the repository (once per repository)

For the repository administrator, installing GPK comes down to dropping the
contents of this repository into a `gpk` subdirectory in your repository.  You
can download it as plain files, or use `git submodules` or `git subtree` to get
it.

    git remote add gpk git@github.com:rix0rrr/git-precommit-kit.git
    git subtree add --prefix=gpk/ -m "Add Git Precommit Kit" --squash gpk master

Updating:

    git subtree pull --prefix=gpk/ --squash gpk master

## Installing for developers (once per user)

As a developer, from the top of your repository, run:

    gpk/install

To (try to) automatically install all the dependencies for the static code
checkers you're going to be using, run:

    gpk/install-deps

## Configuring

GPK is configured using files named `PRECOMMIT` in your repository. These files
can be put in any directory, and apply to the directory they're in and those
below. The simples configuration would be one `PRECOMMIT` file in the root of
your repository.

`PRECOMMIT` files have the following format:

    pattern: check
    pattern: check
    ...

For example:

    *.py: pylint
    *.js: jshint

Etc. Every line specifies a file pattern (space separated), and a check that
needs to be run on the files that match the pattern. Multiple checks for the
same file type should be specified on multiple lines.

"checks" are simply the scripts that exist in the `gpk/checks` directory, and
their exit code determines whether the file passes the check or not.

### no-new

There's a special meta-script called `no-new`, which takes another script as
argument. Those scripts are expected to produce a list of errors, and the exit
code of `no-new` depends on whether the current commit didn't introduce any
_new_ errors into the script.

This is useful if you have an existing codebase that will not pass all checks
cleanly at the moment, but you want to avoid introducing any new violations.

For example:

`PRECOMMIT`

    *.py: no-new pylint

## Check scripts

Checks are simply scripts in the `gpk/checks` directory, and are referred to by
name. In fact, the "check" part of a `PRECOMMIT` file is simply the start of a
shell command, run from the `checks` directory, with `.` added to the current
`PATH`.

Scripts are invoked as follows:

    script NEW OLD 

Where `NEW` and `OLD` are the new and old versions of the file. `OLD` may be
empty if the file is new. Output of the script will be captured, and used as
the script's error message when it exits with a nonzero exit code.

To be compatible with `no-new`, a script is supposed to produce its list of
violations on `stdout`, and potential related error messages on `stderr`.

If a check script is accompanied by a script with the suffix `-deps`, that
script will be executed when the user invokes `install-deps`, and is supposed
to install the checking tools required by the main script.
