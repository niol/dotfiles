import os
import glob
import os.path
import shutil
import fnmatch
import platform

from .utils import realpath_expanduser, is_link_to
from .compat import symlink


class Dotfile(object):

    def __init__(self, name, target, home, add_dot=True, dry_run=False):
        if name.startswith('/'):
            self.name = name
        else:
            if add_dot:
                self.name = os.path.join(home, '.%s' % name.strip('.'))
            else:
                self.name = os.path.join(home, name)
        self.relpath = self.name[len(home)+1:]
        self.basename = os.path.basename(self.name)
        self.target = target.rstrip('/')
        self.dry_run = dry_run
        self.status = ''
        if not os.path.lexists(self.name):
            self.status = 'missing'
        elif not is_link_to(self.name, self.target):
            self.status = 'unsynced'

    def _symlink(self):
        if not self.dry_run:
            dirname = os.path.dirname(self.name)
            if not os.path.isdir(dirname):
                os.makedirs(dirname)
            symlink(os.path.relpath(self.target, dirname), self.name)
        else:
            print("Creating symlink %s => %s" % (self.target, self.name))

    def _rmtree(self, path):
        if not self.dry_run:
            shutil.rmtree(path)
        else:
            print("Removing %s and everything under it" % path)

    def _remove(self, path):
        if not self.dry_run:
            os.remove(path)
        else:
            print("Removing %s" % path)

    def _move(self, src, dst):
        if not self.dry_run:
            shutil.move(src, dst)
        else:
            print("Moving %s => %s" % (src, dst))

    def sync(self, force):
        if self.status == 'missing':
            self._symlink()
        elif self.status == 'unsynced':
            if not force:
                print("Skipping \"%s\", use --force to override"
                      % self.relpath)
                return
            if os.path.isdir(self.name) and not os.path.islink(self.name):
                self._rmtree(self.name)
            else:
                os.remove(self.name)
            self._symlink()

    def add(self):
        if self.status == 'missing':
            print("Skipping \"%s\", file not found" % self.relpath)
            return
        if self.status == '':
            print("Skipping \"%s\", already managed" % self.relpath)
            return

        target_dir = os.path.dirname(self.target)
        if not os.path.isdir(target_dir):
            os.makedirs(target_dir)
        self._move(self.name, self.target)
        self._symlink()

    def remove(self):

        if self.status != '':
            print("Skipping \"%s\", file is %s" % (self.relpath, self.status))
            return

        # remove the existing symlink
        self._remove(self.name)

        # return dotfile to its original location
        if os.path.exists(self.target):
            self._move(self.target, self.name)

    def __str__(self):
        user_home = os.environ['HOME']
        common_prefix = os.path.commonprefix([user_home, self.name])
        if common_prefix:
            name = '~%s' % self.name[len(common_prefix):]
        else:
            name = self.name
        return '%-18s %-s' % (name, self.status)


class Dotfiles(object):
    """A Dotfiles Repository."""

    defaults = {
        'prefix': '',
        'packages': set(),
        'externals': dict(),
        'ignore': set(['.dotfilesrc']),
        'homedir': os.path.expanduser('~/'),
        'path': os.path.expanduser('~/Dotfiles'),
        'no_dot_prefix': False,
        'hostname': 'all',
    }

    def __init__(self, **kwargs):

        # merge provided arguments with defaults into configuration
        configuration = {key: kwargs.get(key, self.defaults[key])
                         for key in self.defaults}

        # map configuration items to instance-local variables
        for k, v in configuration.items():
            setattr(self, k, v)

        # FIXME: compatibility shims, remove these
        self.dry_run = False
        self.repository = self.path

        self._load()

    def hosts_mode(self):
        return os.path.isdir(os.path.join(self.repository, 'all.host'))

    def host_dirname(self, hostname=None):
        if hostname is None and not self.hosts_mode():
            return self.repository
        else:
            if hostname is None:
                hostname = 'all'
            return os.path.join(self.repository, '%s.host' % hostname)

    def this_host_dotfiles(self, hostname=None):
        dotfiles = list(self.dotfiles['all'])  # make a copy

        if self.hosts_mode():
            if hostname is None:
                hostname = platform.node()
            try:
                dotfiles.extend(self.dotfiles[hostname])
            except KeyError:
                pass

        return dotfiles

    def _load(self):
        """Load each dotfile in the repository."""

        self.dotfiles = {}

        if self.hosts_mode():
            for hostdir in glob.glob("%s/*.host" % self.repository):
                if os.path.isdir(hostdir):
                    hostname = os.path.basename(hostdir).split('.')[0]
                    self.dotfiles[hostname] = self._load_host(hostname)
        else:
            self.dotfiles['all'] = self._load_host()

    def _load_host(self, hostname=None):
        """Load each dotfile for the supplied host."""

        directory = self.host_dirname(hostname)

        dotfiles = list()

        all_repofiles = list()
        pkg_dirs = list(map(lambda p: os.path.join(directory, p),
                            self.packages))
        for root, dirs, files in os.walk(directory):
            for f in files:
                f_rel_path = os.path.join(root, f)[len(directory)+1:]
                all_repofiles.append(f_rel_path)
            for d in dirs:
                # ignore VCS and other config directories
                if d[0] == '.':
                    dirs.remove(d)
                    continue

                dotdir_or_package = False
                # discover packages
                if root in pkg_dirs:
                    dotdir_or_package = True
                # discover symlinks to dot-directories in home
                dotdir = self._home_fqpn(os.path.join(root, d), hostname)
                if os.path.islink(dotdir):
                    dotdir_or_package = True

                if dotdir_or_package:
                    dirs.remove(d)
                    d_rel_path = os.path.join(root, d)[len(directory)+1:]
                    all_repofiles.append(d_rel_path)
        repofiles_to_symlink = set(all_repofiles)

        for pat in self.ignore:
            repofiles_to_symlink.difference_update(
                fnmatch.filter(all_repofiles, pat))

        for dotfile in repofiles_to_symlink:
            add_dot = not self.no_dot_prefix
            dotfiles.append(Dotfile(dotfile,
                                    os.path.join(directory, dotfile),
                                    self.homedir,
                                    add_dot=add_dot, dry_run=self.dry_run))

        for dotfile in self.externals.keys():
            dotfiles.append(
                Dotfile(dotfile,
                        os.path.expanduser(self.externals[dotfile]),
                        self.homedir, dry_run=self.dry_run))

        return dotfiles

    def _repo_fqpn(self, homepath, pkg_name=None, hostname=None):
        """Return the fully qualified path to a dotfile in the repository."""

        dotfile_rel_path = homepath[len(self.homedir)+1:]
        dotfile_rel_repopath = self.prefix\
            + dotfile_rel_path[1:]  # remove leading '.'
        return os.path.join(self.host_dirname(hostname), dotfile_rel_repopath)

    def _home_fqpn(self, repopath, hostname=None):
        """Return the fully qualified path to a dotfile in the home dir."""

        dotfile_rel_path = repopath[len(self.host_dirname(hostname)) + 1 +
                                    len(self.prefix):]
        return os.path.join(self.homedir, '.%s' % dotfile_rel_path)

    def list(self, verbose=True):
        """List the contents of this repository."""

        for dotfile in sorted(self.this_host_dotfiles(),
                              key=lambda dotfile: dotfile.name):
            if dotfile.status or verbose:
                print(dotfile)

    def check(self):
        """List only unsynced and/or missing dotfiles."""

        self.list(verbose=False)

    def sync(self, files=None, force=False, hostname=None):
        """Synchronize this repository, creating and updating the necessary
        symbolic links."""

        # unless a set of files is specified, operate on all files
        if not files:
            dotfiles = self.this_host_dotfiles(hostname)
        else:
            files = set(map(lambda x: os.path.join(self.homedir, x), files))
            dotfiles = [x for x in self.this_host_dotfiles(hostname)
                        if x.name in files]
            if not dotfiles:
                raise Exception("file not found")

        for dotfile in dotfiles:
            dotfile.sync(force)

    def add(self, files, hostname=None):
        """Add dotfile(s) to the repository."""

        self._perform_action('add', hostname, files)

    def remove(self, files, hostname=None):
        """Remove dotfile(s) from the repository."""

        self._perform_action('remove', hostname, files)

    def _perform_action(self, action, hostname, files):
        for file in files:
            file = file.rstrip('/')
            file = os.path.abspath(os.path.expanduser(file))
            # See if file is inside a package
            file_dir, file_name = os.path.split(file)
            common_prefix = os.path.commonprefix([self.homedir, file_dir])
            sub_dir = file_dir[len(common_prefix) + 1:]
            pkg_name = sub_dir.lstrip('.')
            if pkg_name in self.packages:
                home = os.path.join(self.homedir, sub_dir)
                target = self._repo_fqpn(file, pkg_name=pkg_name,
                                         hostname=hostname)
            else:
                home = self.homedir
                target = self._repo_fqpn(file, hostname=hostname)
                pkg_name = False

            if sub_dir.startswith('.') or file_name.startswith('.') or\
               pkg_name:
                dotfile = Dotfile(file, target, home, dry_run=self.dry_run)
                getattr(dotfile, action)()
            else:
                print("Skipping \"%s\", not a dotfile" % file)

    def move(self, target):
        """Move the repository to another location."""
        target = realpath_expanduser(target)

        if os.path.exists(target):
            raise ValueError('Target already exists: %s' % (target))

        if not self.dry_run:
            shutil.copytree(self.repository, target, symlinks=True)
            shutil.rmtree(self.repository)
        else:
            print("Recursive copy %s => %s" % (self.repository, target))
            print("Removing %s and everything under it" % self.repository)

        self.repository = target

        if not self.dry_run:
            self._load()
            self.sync(force=True)
