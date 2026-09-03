/* hivewrite -- set named string values in ONE registry key, preserving
 * every other value that key holds.
 *
 * Why this exists (#33): hivexsh's `setval N` replaces the key's entire
 * value list, so writing Hostname into Tcpip\Parameters with it would
 * erase every other TCP/IP setting of the restored machine. libhivex's
 * hivex_node_set_value() replaces or adds a single (name, value) pair
 * and leaves the rest alone -- but no packaged tool exposes it, hence
 * this helper, compiled by tools/build_initramfs.sh like fanout.
 *
 * Usage:
 *   hivewrite HIVE KEY\PATH NAME VALUE [NAME VALUE]...   write values
 *   hivewrite -g HIVE KEY\PATH NAME                      print one value
 *
 * All pairs are written into the same key and committed atomically at
 * the end -- either the hive gains all of them or none. Values are
 * REG_SZ, ASCII only: the agent writes hostnames it already validated
 * ([A-Za-z0-9-]), and widening ASCII to UTF-16LE needs no conversion
 * tables. Exit is nonzero on any failure, with the reason on stderr;
 * the caller still verifies by reading back (no silent failure).
 *
 * -g exists because the packaged readers cannot run in the initramfs:
 * hivexget is a shell wrapper around hivexsh, and both broke there for
 * months without anyone noticing (#33). Reading back through the same
 * binary that wrote keeps the verification honest and self-contained.
 * NOTE: libhivex converts key names with glibc iconv -- without the
 * gconv modules (packed by tools/build_initramfs.sh) every lookup
 * fails as "key not found". That was the real root cause of #33.
 */

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <hivex.h>

static hive_node_h walk(hive_h *h, const char *path)
{
    /* Descend HIVE\SUB\KEY from the root, one component at a time.
     * hivex matches child names case-insensitively, like Windows. */
    hive_node_h node = hivex_root(h);
    char *copy = strdup(path);
    if (copy == NULL)
        return 0;
    for (char *part = strtok(copy, "\\"); part && node;
         part = strtok(NULL, "\\"))
        node = hivex_node_get_child(h, node, part);
    free(copy);
    return node;
}

static int set_ascii_sz(hive_h *h, hive_node_h node,
                        const char *name, const char *ascii)
{
    size_t chars = strlen(ascii);
    hive_set_value val;

    /* REG_SZ is UTF-16LE with a two-byte terminator. */
    val.len = (chars + 1) * 2;
    val.value = calloc(chars + 1, 2);
    if (val.value == NULL) {
        fprintf(stderr, "hivewrite: out of memory\n");
        return -1;
    }
    for (size_t i = 0; i < chars; i++) {
        if ((unsigned char)ascii[i] > 0x7e || (unsigned char)ascii[i] < 0x20) {
            fprintf(stderr, "hivewrite: value for '%s' is not printable "
                            "ASCII\n", name);
            free(val.value);
            return -1;
        }
        val.value[i * 2] = ascii[i];
    }
    val.key = (char *)name;
    val.t = hive_t_REG_SZ;

    if (hivex_node_set_value(h, node, &val, 0) == -1) {
        fprintf(stderr, "hivewrite: setting '%s' failed: %s\n",
                name, strerror(errno));
        free(val.value);
        return -1;
    }
    free(val.value);
    return 0;
}

static int get_value(const char *file, const char *path, const char *name)
{
    hive_h *h = hivex_open(file, 0);
    if (h == NULL) {
        fprintf(stderr, "hivewrite: cannot open %s: %s\n",
                file, strerror(errno));
        return 2;
    }
    hive_node_h node = walk(h, path);
    if (node == 0) {
        fprintf(stderr, "hivewrite: key not found: %s\n", path);
        hivex_close(h);
        return 3;
    }
    hive_value_h val = hivex_node_get_value(h, node, name);
    if (val == 0) {
        fprintf(stderr, "hivewrite: value not found: %s\n", name);
        hivex_close(h);
        return 3;
    }
    hive_type t;
    size_t len;
    if (hivex_value_type(h, val, &t, &len) == -1)
        t = hive_t_REG_NONE;
    if (t == hive_t_REG_SZ || t == hive_t_REG_EXPAND_SZ) {
        char *s = hivex_value_string(h, val);
        if (s == NULL) {
            fprintf(stderr, "hivewrite: cannot decode '%s': %s\n",
                    name, strerror(errno));
            hivex_close(h);
            return 4;
        }
        printf("%s\n", s);
        free(s);
    } else if (t == hive_t_REG_DWORD || t == hive_t_REG_DWORD_BIG_ENDIAN) {
        printf("%d\n", hivex_value_dword(h, val));
    } else {
        fprintf(stderr, "hivewrite: unsupported value type %d for '%s'\n",
                (int)t, name);
        hivex_close(h);
        return 4;
    }
    hivex_close(h);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 5 && strcmp(argv[1], "-g") == 0)
        return get_value(argv[2], argv[3], argv[4]);
    if (argc < 5 || (argc - 3) % 2 != 0 || strcmp(argv[1], "-g") == 0) {
        fprintf(stderr,
                "usage: hivewrite HIVE KEY\\PATH NAME VALUE [NAME VALUE]...\n"
                "       hivewrite -g HIVE KEY\\PATH NAME\n");
        return 1;
    }

    hive_h *h = hivex_open(argv[1], HIVEX_OPEN_WRITE);
    if (h == NULL) {
        fprintf(stderr, "hivewrite: cannot open %s: %s\n",
                argv[1], strerror(errno));
        return 2;
    }

    hive_node_h node = walk(h, argv[2]);
    if (node == 0) {
        fprintf(stderr, "hivewrite: key not found: %s\n", argv[2]);
        hivex_close(h);
        return 3;
    }

    for (int i = 3; i < argc; i += 2) {
        if (set_ascii_sz(h, node, argv[i], argv[i + 1]) != 0) {
            hivex_close(h);   /* no commit -- the file is untouched */
            return 4;
        }
    }

    if (hivex_commit(h, NULL, 0) == -1) {
        fprintf(stderr, "hivewrite: commit failed: %s\n", strerror(errno));
        hivex_close(h);
        return 5;
    }
    hivex_close(h);
    return 0;
}
