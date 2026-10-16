/* Feature-test macros must precede every system header. __GLIBC__ is only
 * defined after a libc header has been included, so it cannot select
 * _GNU_SOURCE here; on glibc that left POSIX_SPAWN_SETSID undeclared and
 * every pinned spawn failed with ENOTSUP. */
#if defined(__APPLE__)
#define _DARWIN_C_SOURCE
#else
#define _GNU_SOURCE
#endif

#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <spawn.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#if defined(__APPLE__)
#include <sys/proc.h>
#include <sys/sysctl.h>
#endif
#include <unistd.h>
#include <string.h>

int archive_fullsync_fd(int fd);
int archive_fullsync_directory_fd(int fd);

static int archive_sync_tree_directory(int directory_fd) {
  int copy = dup(directory_fd);
  if (copy == -1)
    return -1;
  DIR *directory = fdopendir(copy);
  if (directory == NULL) {
    int saved_errno = errno;
    close(copy);
    errno = saved_errno;
    return -1;
  }
  rewinddir(directory);
  struct dirent *entry;
  while ((errno = 0, entry = readdir(directory)) != NULL) {
    const char *name = entry->d_name;
    if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0)
      continue;
    struct stat before;
    if (fstatat(directory_fd, name, &before, AT_SYMLINK_NOFOLLOW) == -1)
      goto fail;
    if (S_ISDIR(before.st_mode)) {
      int child = openat(directory_fd, name,
                         O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
      if (child == -1)
        goto fail;
      struct stat opened;
      if (fstat(child, &opened) == -1) {
        int saved_errno = errno;
        close(child);
        errno = saved_errno;
        goto fail;
      }
      if (!S_ISDIR(opened.st_mode) || opened.st_dev != before.st_dev ||
          opened.st_ino != before.st_ino) {
        close(child);
        errno = ESTALE;
        goto fail;
      }
      int result = archive_sync_tree_directory(child);
      int saved_errno = errno;
      if (close(child) == -1 && result == 0) {
        result = -1;
        saved_errno = errno;
      }
      if (result == -1) {
        errno = saved_errno;
        goto fail;
      }
    } else if (S_ISREG(before.st_mode)) {
      int file = openat(directory_fd, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
      if (file == -1)
        goto fail;
      struct stat opened;
      int result = fstat(file, &opened);
      if (result == 0 &&
          (!S_ISREG(opened.st_mode) || opened.st_dev != before.st_dev ||
           opened.st_ino != before.st_ino)) {
        errno = ESTALE;
        result = -1;
      }
      if (result == 0)
        result = archive_fullsync_fd(file);
      int saved_errno = errno;
      if (close(file) == -1 && result == 0) {
        result = -1;
        saved_errno = errno;
      }
      if (result == -1) {
        errno = saved_errno;
        goto fail;
      }
    } else {
      errno = EINVAL;
      goto fail;
    }
  }
  if (errno != 0)
    goto fail;
  if (archive_fullsync_directory_fd(directory_fd) == -1)
    goto fail;
  if (closedir(directory) == -1)
    return -1;
  return 0;
fail: {
    int saved_errno = errno;
    closedir(directory);
    errno = saved_errno;
    return -1;
  }
}

/* Strongly synchronize every regular file and each containing directory
 * below an already-open directory. Symlinks and special files fail closed. */
int archive_sync_tree(int directory_fd) {
  struct stat status;
  if (fstat(directory_fd, &status) == -1)
    return -1;
  if (!S_ISDIR(status.st_mode)) {
    errno = ENOTDIR;
    return -1;
  }
  return archive_sync_tree_directory(directory_fd);
}

/* Enumerate an already-open directory through a fresh descriptor. This keeps
 * fdopendir's stream offset separate from the caller's pinned descriptor. */
void *archive_open_directory_stream(int directory_fd) {
  int copy = openat(directory_fd, ".",
                    O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  if (copy == -1)
    return NULL;
  DIR *directory = fdopendir(copy);
  if (directory == NULL) {
    int saved_errno = errno;
    close(copy);
    errno = saved_errno;
    return NULL;
  }
  return directory;
}

/* Return 1 for an entry, 0 at end, and -1 for an error. POSIX filenames are
 * bounded by NAME_MAX, and the caller supplies a buffer larger than that. */
int archive_read_directory_stream(void *stream, char *name, size_t capacity) {
  DIR *directory = stream;
  errno = 0;
  struct dirent *entry = readdir(directory);
  if (entry == NULL)
    return errno == 0 ? 0 : -1;
  size_t length = strlen(entry->d_name);
  if (length + 1 > capacity) {
    errno = ENAMETOOLONG;
    return -1;
  }
  memcpy(name, entry->d_name, length + 1);
  return 1;
}

int archive_close_directory_stream(void *stream) {
  return closedir((DIR *)stream);
}

/* Remove one directory entry relative to an already-open directory without
 * resolving a pathname supplied by the caller. */
int archive_unlinkat_directory(int directory_fd, const char *name) {
  return unlinkat(directory_fd, name, AT_REMOVEDIR);
}
#if defined(__APPLE__)
/* Darwin may report EPERM when signaling a process group that contains only
 * an exited, unreaped leader.  Treat that as already stopped only when the
 * kernel's process-group listing proves that every member is a zombie. */
static int archive_group_has_live_member(pid_t process_group) {
  int mib[4] = {CTL_KERN, KERN_PROC, KERN_PROC_PGRP, process_group};
  size_t length = 0;
  if (sysctl(mib, 4, NULL, &length, NULL, 0) == -1) {
    return -errno;
  }
  if (length % sizeof(struct kinfo_proc) != 0)
    return -EIO;
  if (length == 0)
    return 0;
  struct kinfo_proc *members = malloc(length);
  if (members == NULL)
    return -ENOMEM;
  size_t actual_length = length;
  if (sysctl(mib, 4, members, &actual_length, NULL, 0) == -1) {
    int error = errno;
    free(members);
    return -error;
  }
  if (actual_length > length ||
      actual_length % sizeof(struct kinfo_proc) != 0) {
    free(members);
    return -EIO;
  }
  int found_live_member = 0;
  size_t count = actual_length / sizeof(struct kinfo_proc);
  for (size_t i = 0; i < count; ++i) {
    if (members[i].kp_proc.p_stat != SZOMB) {
      found_live_member = 1;
      break;
    }
  }
  free(members);
  return found_live_member;
}
#endif

/* Scan a pinned directory for abandoned Door indexes without resolving its
 * pathname again.  Return 1 when a matching entry exists, 0 when clear, and
 * a negative errno value when the directory could not be inspected. */
int archive_has_entry_prefix(int directory_fd, const char *prefix) {
  int copy = dup(directory_fd);
  if (copy == -1)
    return -errno;
  DIR *directory = fdopendir(copy);
  if (directory == NULL) {
    int error = errno;
    close(copy);
    return -error;
  }
  size_t prefix_length = strlen(prefix);
  int found = 0;
  rewinddir(directory);
  struct dirent *entry;
  while ((errno = 0, entry = readdir(directory)) != NULL) {
    if (strncmp(entry->d_name, prefix, prefix_length) == 0) {
      found = 1;
      break;
    }
  }
  int error = errno;
  closedir(directory);
  return error == 0 ? found : -error;
}

static int archive_is_hex_string(const char *value) {
  if (*value == '\0')
    return 0;
  for (const unsigned char *cursor = (const unsigned char *)value;
       *cursor != '\0'; ++cursor) {
    if (!((*cursor >= '0' && *cursor <= '9') ||
          (*cursor >= 'a' && *cursor <= 'f')))
      return 0;
  }
  return 1;
}

static int archive_is_hex_span(const char *value, size_t length) {
  if (length == 0)
    return 0;
  for (size_t index = 0; index < length; ++index) {
    unsigned char byte = (unsigned char)value[index];
    if (!((byte >= '0' && byte <= '9') || (byte >= 'a' && byte <= 'f')))
      return 0;
  }
  return 1;
}

static int archive_sync_regular_at(int directory_fd, const char *name) {
  struct stat status;
  if (fstatat(directory_fd, name, &status, AT_SYMLINK_NOFOLLOW) == -1)
    return -1;
  if (!S_ISREG(status.st_mode)) {
    errno = EINVAL;
    return -1;
  }
  int file_fd = openat(directory_fd, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
  if (file_fd == -1)
    return -1;
  int result = fstat(file_fd, &status);
  int saved_errno = 0;
  if (result == 0 && !S_ISREG(status.st_mode)) {
    errno = EINVAL;
    result = -1;
  }
  if (result == 0 && archive_fullsync_fd(file_fd) == -1)
    result = -1;
  if (result == -1)
    saved_errno = errno;
  if (close(file_fd) == -1 && result == 0) {
    result = -1;
    saved_errno = errno;
  }
  if (result == -1)
    errno = saved_errno;
  return result;
}

static int archive_pack_basename(const char *name, const char *suffix,
                                 char *base, size_t base_size) {
  size_t name_length = strlen(name);
  size_t suffix_length = strlen(suffix);
  if (name_length <= suffix_length ||
      strcmp(name + name_length - suffix_length, suffix) != 0)
    return 0;
  size_t base_length = name_length - suffix_length;
  if (base_length < 5 || base_length >= base_size ||
      strncmp(name, "pack-", 5) != 0 ||
      !archive_is_hex_span(name + 5, base_length - 5) ||
      ((base_length - 5) != 40 && (base_length - 5) != 64)) {
    errno = EINVAL;
    return -1;
  }
  memcpy(base, name, base_length);
  base[base_length] = '\0';
  return 1;
}

static int archive_sync_pack_directory(int pack_fd) {
  int copy = dup(pack_fd);
  if (copy == -1)
    return -1;
  DIR *directory = fdopendir(copy);
  if (directory == NULL) {
    int saved_errno = errno;
    close(copy);
    errno = saved_errno;
    return -1;
  }
  rewinddir(directory);
  struct dirent *entry;
  while ((errno = 0, entry = readdir(directory)) != NULL) {
    const char *name = entry->d_name;
    if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0)
      continue;
    struct stat status;
    if (fstatat(pack_fd, name, &status, AT_SYMLINK_NOFOLLOW) == -1)
      goto fail;
    if (S_ISDIR(status.st_mode)) {
      /* Multi-pack indexes are derived accelerators. Door disables them for
       * closure traversal; their loss cannot remove an object or its index. */
      if (strcmp(name, "multi-pack-index.d") != 0) {
        errno = EINVAL;
        goto fail;
      }
      continue;
    }
    if (!S_ISREG(status.st_mode)) {
      errno = EINVAL;
      goto fail;
    }
    size_t name_length = strlen(name);
    if (name_length >= 9 &&
        strcmp(name + name_length - 9, ".promisor") == 0) {
      errno = ENOTSUP;
      goto fail;
    }
    if (archive_sync_regular_at(pack_fd, name) == -1)
      goto fail;
    char base[80];
    int is_pack = archive_pack_basename(name, ".pack", base, sizeof(base));
    if (is_pack == -1)
      goto fail;
    if (is_pack == 1) {
      char index_name[84];
      if (snprintf(index_name, sizeof(index_name), "%s.idx", base) >=
          (int)sizeof(index_name)) {
        errno = ENAMETOOLONG;
        goto fail;
      }
      struct stat index_status;
      if (fstatat(pack_fd, index_name, &index_status, AT_SYMLINK_NOFOLLOW) == -1)
        goto fail;
      if (!S_ISREG(index_status.st_mode)) {
        errno = EINVAL;
        goto fail;
      }
      continue;
    }
    int is_index = archive_pack_basename(name, ".idx", base, sizeof(base));
    if (is_index == -1)
      goto fail;
    if (is_index == 1) {
      char pack_name[84];
      if (snprintf(pack_name, sizeof(pack_name), "%s.pack", base) >=
          (int)sizeof(pack_name)) {
        errno = ENAMETOOLONG;
        goto fail;
      }
      struct stat pack_status;
      if (fstatat(pack_fd, pack_name, &pack_status, AT_SYMLINK_NOFOLLOW) == -1)
        goto fail;
      if (!S_ISREG(pack_status.st_mode)) {
        errno = EINVAL;
        goto fail;
      }
    }
  }
  if (errno != 0)
    goto fail;
  if (archive_fullsync_directory_fd(pack_fd) == -1)
    goto fail;
  if (closedir(directory) == -1)
    return -1;
  return 0;
fail: {
    int saved_errno = errno;
    closedir(directory);
    errno = saved_errno;
    return -1;
  }
}

static int archive_sync_object_fanout(int objects_fd, const char *name,
                                      size_t object_tail_length) {
  if (strlen(name) != 2 || !archive_is_hex_string(name)) {
    errno = EINVAL;
    return -1;
  }
  int fanout_fd = openat(objects_fd, name,
                         O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  if (fanout_fd == -1)
    return -1;
  int copy = dup(fanout_fd);
  if (copy == -1) {
    int saved_errno = errno;
    close(fanout_fd);
    errno = saved_errno;
    return -1;
  }
  DIR *directory = fdopendir(copy);
  if (directory == NULL) {
    int saved_errno = errno;
    close(copy);
    close(fanout_fd);
    errno = saved_errno;
    return -1;
  }
  rewinddir(directory);
  struct dirent *entry;
  while ((errno = 0, entry = readdir(directory)) != NULL) {
    const char *object_name = entry->d_name;
    if (strcmp(object_name, ".") == 0 || strcmp(object_name, "..") == 0)
      continue;
    if (strlen(object_name) != object_tail_length ||
        !archive_is_hex_string(object_name)) {
      errno = EINVAL;
      goto fail;
    }
    if (archive_sync_regular_at(fanout_fd, object_name) == -1)
      goto fail;
  }
  if (errno != 0)
    goto fail;
  if (archive_fullsync_directory_fd(fanout_fd) == -1)
    goto fail;
  closedir(directory);
  return close(fanout_fd);
fail: {
    int saved_errno = errno;
    closedir(directory);
    close(fanout_fd);
    errno = saved_errno;
    return -1;
  }
}

/* Synchronize the primary object store while it is protected by the Archive
 * lock. This deliberately syncs all local loose objects and pack/index pairs,
 * not only the object chosen by Git for each reachable id: duplicate copies
 * and concurrent pack selection are otherwise difficult to map safely. */
int archive_sync_object_store(int objects_fd, size_t object_tail_length) {
  int copy = dup(objects_fd);
  if (copy == -1)
    return -1;
  DIR *directory = fdopendir(copy);
  if (directory == NULL) {
    int saved_errno = errno;
    close(copy);
    errno = saved_errno;
    return -1;
  }
  int pack_fd = -1;
  rewinddir(directory);
  struct dirent *entry;
  while ((errno = 0, entry = readdir(directory)) != NULL) {
    const char *name = entry->d_name;
    if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0)
      continue;
    struct stat status;
    if (fstatat(objects_fd, name, &status, AT_SYMLINK_NOFOLLOW) == -1)
      goto fail;
    if (!S_ISDIR(status.st_mode)) {
      errno = EINVAL;
      goto fail;
    }
    if (strcmp(name, "pack") == 0) {
      if (pack_fd != -1) {
        errno = EINVAL;
        goto fail;
      }
      pack_fd = openat(objects_fd, name,
                       O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
      if (pack_fd == -1 || archive_sync_pack_directory(pack_fd) == -1)
        goto fail;
    } else if (strcmp(name, "info") == 0) {
      int info_fd = openat(objects_fd, name,
                           O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
      if (info_fd == -1)
        goto fail;
      struct stat alternate_status;
      if (fstatat(info_fd, "alternates", &alternate_status,
                  AT_SYMLINK_NOFOLLOW) == 0) {
        close(info_fd);
        errno = ENOTSUP;
        goto fail;
      }
      if (errno != ENOENT) {
        int saved_errno = errno;
        close(info_fd);
        errno = saved_errno;
        goto fail;
      }
      if (archive_fullsync_directory_fd(info_fd) == -1) {
        int saved_errno = errno;
        close(info_fd);
        errno = saved_errno;
        goto fail;
      }
      close(info_fd);
    } else if (strlen(name) == 2 && archive_is_hex_string(name)) {
      if (archive_sync_object_fanout(objects_fd, name, object_tail_length) == -1)
        goto fail;
    } else {
      errno = EINVAL;
      goto fail;
    }
  }
  if (errno != 0)
    goto fail;
  if (archive_fullsync_directory_fd(objects_fd) == -1)
    goto fail;
  if (pack_fd != -1 && close(pack_fd) == -1) {
    pack_fd = -1;
    goto fail;
  }
  pack_fd = -1;
  return closedir(directory);
fail: {
    int saved_errno = errno;
    if (pack_fd != -1)
      close(pack_fd);
    closedir(directory);
    errno = saved_errno;
    return -1;
  }
}

/* Flush a regular file through the strongest primitive this implementation
 * supports. Darwin's fsync(2) does not flush device write caches; require its
 * F_FULLFSYNC request. Linux fsync(2) is the available barrier here. Other
 * platforms fail closed until their power-loss contract is reviewed. */
int archive_fullsync_fd(int fd) {
#if defined(__APPLE__)
  return fcntl(fd, F_FULLFSYNC);
#elif defined(__linux__)
  return fsync(fd);
#else
  (void)fd;
  errno = ENOTSUP;
  return -1;
#endif
}

/* F_FULLFSYNC is the strongest directory namespace barrier exposed by
 * Darwin. Do not silently downgrade to fsync if the filesystem or descriptor
 * does not support it. Linux fsync is its available directory barrier. */
int archive_fullsync_directory_fd(int fd) {
  struct stat status;
  if (fstat(fd, &status) == -1)
    return -1;
  if (!S_ISDIR(status.st_mode)) {
    errno = ENOTDIR;
    return -1;
  }
#if defined(__APPLE__)
  return fcntl(fd, F_FULLFSYNC);
#elif defined(__linux__)
  return fsync(fd);
#else
  errno = ENOTSUP;
  return -1;
#endif
}

/* Spawn directly into an already-open Git directory.  The descriptor is
 * consumed by the spawn file action before exec; no pathname lookup of .git
 * occurs in the child. */
int archive_spawn_pinned(int directory_fd, const char *executable,
                         char *const arguments[], char *const environment[],
                         int *process_id, int *stdout_fd, int *stderr_fd) {
  int output_pipe[2] = {-1, -1};
  int error_pipe[2] = {-1, -1};
  int result = pipe(output_pipe);
  if (result != 0)
    return errno;
  result = pipe(error_pipe);
  if (result != 0) {
    result = errno;
    close(output_pipe[0]);
    close(output_pipe[1]);
    return result;
  }

  for (int i = 0; i < 2; ++i) {
    if (fcntl(output_pipe[i], F_SETFD, FD_CLOEXEC) == -1 ||
        fcntl(error_pipe[i], F_SETFD, FD_CLOEXEC) == -1) {
      result = errno;
      goto close_pipes;
    }
  }

  posix_spawn_file_actions_t actions;
  result = posix_spawn_file_actions_init(&actions);
  if (result != 0)
    goto close_pipes;

  posix_spawnattr_t attributes;
  result = posix_spawnattr_init(&attributes);
  if (result != 0) {
    posix_spawn_file_actions_destroy(&actions);
    goto close_pipes;
  }
#if defined(POSIX_SPAWN_SETSID)
  /* A private session makes the child process group exclusive to this spawn.
   * With only SETPGROUP, an unrelated process in the caller's session could
   * join the group and make later group signaling unsafe. */
  result = posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETSID);
#else
  result = ENOTSUP;
#endif

  if (result == 0) {
#if defined(__APPLE__)
    result = posix_spawn_file_actions_addfchdir_np(&actions, directory_fd);
#elif defined(__GLIBC__)
    char descriptor_path[64];
    int path_length = snprintf(descriptor_path, sizeof(descriptor_path),
                               "/proc/self/fd/%d", directory_fd);
    result = path_length < 0 || (size_t)path_length >= sizeof(descriptor_path)
                 ? ENAMETOOLONG
                 : posix_spawn_file_actions_addchdir_np(&actions, descriptor_path);
#else
    result = ENOTSUP;
#endif
  }
  if (result == 0)
    result = posix_spawn_file_actions_addopen(
        &actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0);
  if (result == 0)
    result = posix_spawn_file_actions_adddup2(
        &actions, output_pipe[1], STDOUT_FILENO);
  if (result == 0)
    result = posix_spawn_file_actions_adddup2(
        &actions, error_pipe[1], STDERR_FILENO);
  if (result == 0)
    result = posix_spawn_file_actions_addclose(&actions, output_pipe[0]);
  if (result == 0)
    result = posix_spawn_file_actions_addclose(&actions, error_pipe[0]);
  if (result == 0)
    result = posix_spawn_file_actions_addclose(&actions, output_pipe[1]);
  if (result == 0)
    result = posix_spawn_file_actions_addclose(&actions, error_pipe[1]);
  if (result == 0) {
    pid_t child = -1;
    result = posix_spawn(&child, executable, &actions, &attributes, arguments,
                         environment);
    if (result == 0) {
      *process_id = (int)child;
      *stdout_fd = output_pipe[0];
      *stderr_fd = error_pipe[0];
      output_pipe[0] = -1;
      error_pipe[0] = -1;
    }
  }
  posix_spawnattr_destroy(&attributes);
  posix_spawn_file_actions_destroy(&actions);

close_pipes:
  if (output_pipe[0] != -1)
    close(output_pipe[0]);
  if (output_pipe[1] != -1)
    close(output_pipe[1]);
  if (error_pipe[0] != -1)
    close(error_pipe[0]);
  if (error_pipe[1] != -1)
    close(error_pipe[1]);
  return result;
}

/* Return -1 while the child is still running, 0 on success, and an errno-style
 * status on failure. Polling lets the Haskell capture threads run even when
 * an embedding executable uses the non-threaded RTS. */
int archive_wait_pinned(int process_id, int *status) {
  /* Observe exit without reaping first, so the process-group id cannot be
   * reused while we remove any wrapper descendants that outlived the leader. */
  siginfo_t information;
  memset(&information, 0, sizeof(information));
  int observed;
  do {
    observed = waitid(P_PID, (id_t)process_id, &information,
                      WEXITED | WNOWAIT | WNOHANG);
  } while (observed == -1 && errno == EINTR);
  if (observed == -1)
    return errno;
  if (information.si_pid == 0)
    return -1;
  int group_error = 0;
  /* Once the configured executable exits, no process in its private group
   * may continue work or keep a captured pipe open, regardless of its status. */
  if (kill(-(pid_t)process_id, SIGKILL) == -1 && errno != ESRCH) {
    group_error = errno;
#if defined(__APPLE__)
    /* Darwin reports EPERM when a process group contains only the exited,
     * unreaped leader.  Accept that only after the kernel member list proves
     * there is no live group member. */
    if (group_error == EPERM) {
      int mib[4] = {CTL_KERN, KERN_PROC, KERN_PROC_PGRP, process_id};
      size_t length = 0;
      if (sysctl(mib, 4, NULL, &length, NULL, 0) == 0 &&
          length % sizeof(struct kinfo_proc) == 0) {
        struct kinfo_proc *members = length == 0 ? NULL : malloc(length);
        if (length == 0 || members != NULL) {
          size_t actual_length = length;
          if (length == 0 || sysctl(mib, 4, members, &actual_length, NULL, 0) == 0) {
            if (actual_length <= length &&
                actual_length % sizeof(struct kinfo_proc) == 0) {
              size_t count = actual_length / sizeof(struct kinfo_proc);
              group_error = 0;
              for (size_t i = 0; i < count; ++i) {
                if (members[i].kp_proc.p_stat != SZOMB) {
                  group_error = EPERM;
                  break;
                }
              }
            }
          }
          free(members);
        }
      }
    }
#endif
  }

  pid_t result;
  do {
    result = waitpid((pid_t)process_id, status, 0);
  } while (result == -1 && errno == EINTR);
  if (result == -1)
    return errno;
  if (group_error != 0)
    return group_error;
  if (WIFEXITED(*status))
    *status = WEXITSTATUS(*status);
  else if (WIFSIGNALED(*status))
    *status = 128 + WTERMSIG(*status);
  else
    *status = 255;
  return 0;
}

int archive_terminate_pinned(int process_id) {
  /* The caller uses this only for bounded-output or exception cleanup.  A
   * child that ignores TERM could otherwise keep the captured pipes open
   * forever and prevent the bound from taking effect. */
  if (kill(-(pid_t)process_id, SIGKILL) == 0 || errno == ESRCH)
    return 0;
  int signal_error = errno;
  if (signal_error == EPERM) {
#if defined(__APPLE__)
    siginfo_t information;
    memset(&information, 0, sizeof(information));
    int observed;
    do {
      observed = waitid(P_PID, (id_t)process_id, &information,
                        WEXITED | WNOWAIT | WNOHANG);
    } while (observed == -1 && errno == EINTR);
    if (observed == 0 && information.si_pid == process_id) {
      int has_live_member = archive_group_has_live_member((pid_t)process_id);
      if (has_live_member == 0)
        return 0;
      if (has_live_member < 0)
        return -has_live_member;
    }
#endif
  }
  return signal_error;
}
