"""Core GPK routines."""

import datetime
import difflib
import doctest
import fnmatch
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import threading

import cStringIO as StringIO
from os import path

import gcl



#--------------------------------------------------------------------
# Interrupt handling
#
# I'd like to continue after killing a single test, but git has already died
# after the initial SIGINT, so we have no other opportunity than to simply die.
#

interrupted = False
OPEN_PROCESSES = []

def signal_handler(signum, frame):
    global interrupted
    interrupted = True
    for p in OPEN_PROCESSES:
        p.kill()
signal.signal(signal.SIGINT, signal_handler)

#--------------------------------------------------------------------

class SourceTree(object):
    """Temporary directory with context manager semantics.

    For storing source trees.
    """
    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.available = False

    def exists(self, filename):
        return path.exists(self.full_path(filename))

    def full_path(self, filename):
        if not self.available:
            raise RuntimeError('Trying to get filename from nonexistent source tree: %s/%s' % (self.dir, self.filename))
        return path.join(self.dir, filename)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        shutil.rmtree(self.dir)


class Aborted(RuntimeError):
    pass


class CheckFailedError(RuntimeError):
    def __init__(self, check, filename, report, hint=''):
        self.check = check
        self.filename = filename
        self.report = report
        self.hint = hint

        super(CheckFailedError, self).__init__('%s: %s failed: %s' % (check, filename, report))


class Colored(object):
    yellow = '\033[93m'
    endc = '\033[0m'
    red = '\033[91m'
    green = '\033[92m'

    @classmethod
    def colorize(cls, text, color):
        return '%s%s%s' % (getattr(cls, color), text, cls.endc)


class ConsoleWriter(object):
    def warn(self, title, msg):
        sys.stderr.write('[%s] %s\n' % (Colored.colorize(title, 'yellow'), msg))

    def error(self, title, msg):
        sys.stderr.write('[%s] %s\n' % (Colored.colorize(title, 'red'), msg))

    def success(self, title, msg):
        sys.stderr.write('[%s] %s\n' % (Colored.colorize(title, 'green'), msg))

    def write(self, msg):
        sys.stderr.write('%s\n' % msg)

    def ok(self):
        sys.stderr.write('.')
        sys.stderr.flush()

    def fail(self):
        sys.stderr.write('X')
        sys.stderr.flush()

    def done(self):
        sys.stderr.write('\n')
        sys.stderr.flush()


class Check(object):
    """A check that needs to be performed."""
    def __init__(self, rule, filename):
        self.rule = rule
        self.filename = filename
        self.prerequisite_satisfied = False

    def __repr__(self):
        return 'Check(%r, %r)' % (self.rule, self.filename)

    @property
    def requires_old_source(self):
        return self.rule.get('no_new', False)

    @property
    def hint(self):
        return self.rule.get('hint', '')

    @property
    def check_script(self):
        if 'check' not in self.rule:
            raise RuntimeError("Rule should have a 'check' member.")
        return self.rule['check']

    @property
    def env(self):
        return self.rule.get('env', {})

    def check_prerequisites(self, context):
        deps_script = self.check_script + '-deps'

        if self.check_script not in context.prereqs_checked:
            if not context.script_exists(deps_script):
                context.prereqs_checked[self.check_script] = True
            else:
                context.prereqs_checked[self.check_script] = False
                context.run_script([deps_script], env=self.env)
                context.prereqs_checked[self.check_script] = True
        self.prerequisite_satisfied = context.prereqs_checked[self.check_script]

    def run(self, context):
        if not self.prerequisite_satisfied:
            return False

        no_new = self.rule.get('no_new', False)

        old_errors = ''
        if no_new and context.old_source.exists(self.filename):
            old_errors = context.run_script([
                    self.check_script,
                    context.old_source.full_path(self.filename)],
                    env=self.env,
                    timeout=10)

        new_errors = context.run_script([
                self.check_script,
                context.new_source.full_path(self.filename)],
                env=self.env,
                timeout=10)

        self._diff_errors(old_errors, new_errors)

    def _diff_errors(self, old_errors, new_errors):
        if not old_errors:
            # Nothing to compare
            if new_errors:
                raise CheckFailedError(self.check_script, self.filename, new_errors, hint=self.hint)
            return

        # Compare errors, retain only new ones
        old_lines = old_errors.split('\n')
        new_lines = new_errors.split('\n')
        s = difflib.SequenceMatcher(a=old_lines, b=new_lines,
                                    isjunk=lambda x: not x.strip()) # Ignore empty lines

        added = []
        for op, a0, a1, b0, b1 in s.get_opcodes():
            if op in ['insert', 'replace']:
                added.extend(new_lines[b0:b1])
        if added:
            raise CheckFailedError(self.check_script, self.filename, '\n'.join(added), hint=self.hint)


class Checks(object):
    """A collection of checks that need to be performed."""

    def __init__(self, checks):
        self.checks = checks

    @property
    def requires_old_source(self):
        return any(c.requires_old_source for c in self.checks)

    def run(self, context):
        for check in self.checks:
            with context.error_catcher(print_progress=False):
                check.check_prerequisites(context)
        if context.errors:
            return

        for check in self.checks:
            with context.error_catcher():
                check.run(context)
        context.writer.done()

    def __repr__(self):
        return 'Checks(%r)' % self.checks


class RunResult(object):
    def __init__(self, exit_code, stdout, aborted):
        self.exit_code = exit_code
        self.stdout = stdout
        self.aborted = aborted

    @property
    def ok(self):
        return not self.aborted and self.exit_code == 0


class BackgroundPipeReader(threading.Thread):
    def __init__(self, fd):
        super(BackgroundPipeReader, self).__init__()
        self.fd = fd
        self.buffer = StringIO.StringIO()

    def run(self):
        x = self.fd.read(4096)
        while x:
            self.buffer.write(x)
            x = self.fd.read(4096)

    def str(self):
        return self.buffer.getvalue()


class RunContext(object):
    def __init__(self, gpk_path, new_source, old_source, writer):
        self.gpk_path = gpk_path
        self.new_source = new_source
        self.old_source = old_source
        self.writer = writer

        self.prereqs_checked = {}
        self.errors = []

    def report(self):
        if self.errors:
            self.writer.warn('gpk', 'Found %d error%s.' % (len(self.errors), 's' if len(self.errors) != 1 else ''))
            for error in self.errors:
                if isinstance(error, CheckFailedError):
                    self.writer.error(error.check, error.filename)
                    for line in error.report.strip().split('\n'):
                        self.writer.error(error.check, '    ' + line)
                    if error.hint:
                        self.writer.success(error.check, error.hint)
                else:
                    self.writer.error(type(error).__name__, str(error).strip())
            self.writer.warn('gpk', 'One or more pre-commit checks failed. Please fix them, or commit with --no-verify.')

    @property
    def checks_path(self):
        return path.join(self.gpk_path, 'checks')

    @property
    def internal_path(self):
        return path.join(self.gpk_path, 'internal')

    def script_exists(self, name):
        return path.isfile(path.join(self.checks_path, name))

    def run_script(self, cmd, timeout=None, ignore_exitcode=False, env=None):
        try:
            # FIXME: Do I need to capture stderr?
            cmd[0] = path.join(self.checks_path, cmd[0])

            # Make GPK Python helper modules available to scripts in case they're
            # written in Python.
            run_env = os.environ.copy()
            run_env['PYTHONPATH'] = self.internal_path + ':' + env.get('PYTHONPATH', '')

            for k in (env or {}).keys():
                run_env[k] = env[k]

            p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=None,
                                 cwd=self.checks_path,
                                 env=run_env)
            OPEN_PROCESSES.append(p)
            try:
                reader = BackgroundPipeReader(p.stdout)
                reader.start()

                if timeout is None:
                    p.wait()
                    reader.join()
                else:
                    t_warning = datetime.datetime.now() + datetime.timedelta(seconds=timeout)
                    while p.poll() is None and datetime.datetime.now() < t_warning:
                        time.sleep(0.01)
                    if p.poll() is None:
                        self.writer.warn('gpk',
                                         ('Command is taking a long time to complete: %s' %
                                          ' '.join(cmd)))
                    p.wait()
                if p.returncode != 0 and not ignore_exitcode:
                    raise RuntimeError('%r exited with non-zero exit code %d' % (' '.join(cmd), p.returncode))

                if interrupted:
                    # I'd like to continue, but we can't
                    raise Aborted('Interrupted by user')
                return reader.str()
            finally:
                OPEN_PROCESSES.remove(p)
        except OSError, e:
            raise RuntimeError('Error while executing %r: %s' % (cmd, e))
        except KeyboardInterrupt:
            raise Aborted('Command %r interrupted by user' % cmd)

    def error_catcher(self, **kwargs):
        return ErrorCatcher(self, **kwargs)


class ErrorCatcher(object):
    def __init__(self, context, print_progress=True):
        self.context = context
        self.print_progress = print_progress

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if type is Aborted:
            return False

        if type:
            self.context.errors.append(value)
            if self.print_progress:
                self.context.writer.fail()
        else:
            if self.print_progress:
                self.context.writer.ok()

        return True


def shell(cmds):
    return subprocess.check_output(cmds).strip()


def all_directories(filename):
    if filename == '.' or filename == '':
        return []
    parent = path.dirname(filename)
    return [path.dirname(filename)] + all_directories(parent)


def non_empty(xs):
    return filter(None, xs)


def deep_fnmatch(pattern, filename):
    """Match a pattern against a filename, like glob.

    Uses fnmatch, but expands on path separators, which
    fnmatch doesn't. Grmbl.

    >>> deep_fnmatch('foo/*.py', 'foo.py')
    False
    >>> deep_fnmatch('foo/*.py', 'foo/bar.py')
    True
    >>> deep_fnmatch('foo/*.py', 'foo/bar/baz.py')
    False
    >>> deep_fnmatch('*.py', 'foo/bar.py')
    False
    """
    patterns = pattern.split('/')
    filenames = filename.split('/')
    if len(patterns) != len(filenames):
        return False
    for p, f in zip(patterns, filenames):
        if not fnmatch.fnmatchcase(f, p):
            return False
    return True


def pattern_matches(pattern, file_spec):
    """Return whether a pattern matches filespec.

    Pattern can be a string or list.

    >>> pattern_matches('*.py', 'test.py')
    True
    >>> pattern_matches(['*.py', '*.txt'], 'test.txt')
    True
    >>> pattern_matches('*.py', 'foo/test.py')
    False
    """
    if isinstance(pattern, str) or isinstance(pattern, unicode):
        pattern = [pattern]
    return any(deep_fnmatch(p, file_spec) for p in pattern)


def possible_matches(prefix, filename):
    """Return all path segments where filename could be matched under prefix.

    >>> list(possible_matches('', 'bar'))
    ['bar']
    >>> list(possible_matches('foo', 'bar'))
    []
    >>> list(possible_matches('baz', 'baz/bar/foo'))
    ['bar/foo', 'foo']
    """
    prefix_parts = non_empty(prefix.split('/'))
    parts = non_empty(filename.split('/'))
    for a, b in zip(prefix_parts, parts):
        if a != b:
            # No prefix match, done
            return
    for i in range(len(prefix_parts), len(parts)):
        yield '/'.join(parts[i:])


def pattern_matches_anywhere(prefix, pattern, fname):
    """Return whether the given pattern matches fname anywhere under prefix."""
    for spec in possible_matches(prefix, fname):
        if pattern_matches(pattern, spec):
            return True
    return False


def validate_precommit(precommit):
    if 'rules' not in precommit:
        raise RuntimeError("Precommit should have a 'rules' member")
    for rule in precommit['rules']:
        if 'pattern' not in rule:
            raise RuntimeError("Rule should have a 'pattern' member")
        if 'check' not in rule:
            raise RuntimeError("Rule should have a 'check' member")


def apply_precommit(prefix, precommit, all_files):
    """Apply a precommit object to a list of files.

    Returns a set of (rule, filename) tuples.
    """
    ret = set()
    for rule in precommit['rules']:
        for fname in all_files:
            if pattern_matches_anywhere(prefix, rule['pattern'], fname):
                ret.add((rule, fname))
    return ret


def apply_precommit_file(prefix, precommit_file, all_files, gpk_path):
    precommit_obj = gcl.load(precommit_file,
                             loader=gcl.loader_with_search_path([path.join(gpk_path, 'checks')]))
    try:
        validate_precommit(precommit_obj)
    except RuntimeError, e:
        raise RuntimeError('Error parsing %s: %s' % (precommit_file, e))
    return apply_precommit(prefix, precommit_obj, all_files)


def find_checks(root_dir, changed_files, gpk_path):
    """Find all checks that should be applied to the given file set.

    Do this by looking at all the PRECOMMIT files in the directories of the
    files, and matching the precommit rules against the file list.
    """
    # FIXME: Directory checks
    directories = set(d for f in changed_files
                        for d in all_directories(f))

    potential_precommits = ((d, path.join(root_dir, d, 'PRECOMMIT'))
                            for d in directories)

    actual_precommits = ((d, precommit)
                         for d, precommit in potential_precommits
                         if path.isfile(precommit))

    checks = (c for (prefix, precommit) in actual_precommits
                for c in apply_precommit_file(prefix, precommit, changed_files, gpk_path))

    # checks is a collection of (rule, filename) tuples, turn into objects
    return Checks([Check(*c) for c in checks])


if __name__ == '__main__':
    doctest.testmod()
