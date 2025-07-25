# SPDX-License-Identifier: GPL-2.0+
#
# Copyright 2025 Google LLC
#
"""Handles the 'send' subcommand
"""

import os
import sys

from patman import checkpatch
from patman import patchstream
from patman import settings
from u_boot_pylib import gitutil
from u_boot_pylib import terminal


def check_patches(series, patch_files, run_checkpatch, verbose, use_tree, cwd):
    """Run some checks on a set of patches

    This santiy-checks the patman tags like Series-version and runs the patches
    through checkpatch

    Args:
        series (Series): Series object for this series (set of patches)
        patch_files (list): List of patch filenames, each a string, e.g.
            ['0001_xxx.patch', '0002_yyy.patch']
        run_checkpatch (bool): True to run checkpatch.pl
        verbose (bool): True to print out every line of the checkpatch output as
            it is parsed
        use_tree (bool): If False we'll pass '--no-tree' to checkpatch.
        cwd (str): Path to use for patch files (None to use current dir)

    Returns:
        bool: True if the patches had no errors, False if they did
    """
    # Do a few checks on the series
    series.DoChecks()

    # Check the patches
    if run_checkpatch:
        ok = checkpatch.check_patches(verbose, patch_files, use_tree, cwd)
    else:
        ok = True
    return ok


def email_patches(col, series, cover_fname, patch_files, process_tags, its_a_go,
                  ignore_bad_tags, add_maintainers, get_maintainer_script, limit,
                  dry_run, in_reply_to, thread, smtp_server, cwd=None):
    """Email patches to the recipients

    This emails out the patches and cover letter using 'git send-email'. Each
    patch is copied to recipients identified by the patch tag and output from
    the get_maintainer.pl script. The cover letter is copied to all recipients
    of any patch.

    To make this work a CC file is created holding the recipients for each patch
    and the cover letter. See the main program 'cc_cmd' for this logic.

    Args:
        col (terminal.Color): Colour output object
        series (Series): Series object for this series (set of patches)
        cover_fname (str): Filename of the cover letter as a string (None if
            none)
        patch_files (list): List of patch filenames, each a string, e.g.
            ['0001_xxx.patch', '0002_yyy.patch']
        process_tags (bool): True to process subject tags in each patch, e.g.
            for 'dm: spi: Add SPI support' this would be 'dm' and 'spi'. The
            tags are looked up in the configured sendemail.aliasesfile and also
            in ~/.patman (see README)
        its_a_go (bool): True if we are going to actually send the patches,
            False if the patches have errors and will not be sent unless
            @ignore_errors
        ignore_bad_tags (bool): True to just print a warning for unknown tags,
            False to halt with an error
        add_maintainers (bool): Run the get_maintainer.pl script for each patch
        get_maintainer_script (str): The script used to retrieve which
            maintainers to cc
        limit (int): Limit on the number of people that can be cc'd on a single
            patch or the cover letter (None if no limit)
        dry_run (bool): Don't actually email the patches, just print out what
            would be sent
        in_reply_to (str): If not None we'll pass this to git as --in-reply-to.
            Should be a message ID that this is in reply to.
        thread (bool): True to add --thread to git send-email (make all patches
            reply to cover-letter or first patch in series)
        smtp_server (str): SMTP server to use to send patches (None for default)
        cwd (str): Path to use for patch files (None to use current dir)

    Return:
        Git command that was/would be run
    """
    cc_file = series.MakeCcFile(process_tags, cover_fname, not ignore_bad_tags,
                                add_maintainers, limit, get_maintainer_script,
                                settings.alias, cwd)

    # Email the patches out (giving the user time to check / cancel)
    cmd = ''
    if its_a_go:
        cmd = gitutil.email_patches(
            series, cover_fname, patch_files, dry_run, not ignore_bad_tags,
            cc_file, alias=settings.alias, in_reply_to=in_reply_to,
            thread=thread, smtp_server=smtp_server, cwd=cwd)
    else:
        print(col.build(col.RED, "Not sending emails due to errors/warnings"))

    # For a dry run, just show our actions as a sanity check
    if dry_run:
        series.ShowActions(patch_files, cmd, process_tags, settings.alias)
        if not its_a_go:
            print(col.build(col.RED, "Email would not be sent"))

    os.remove(cc_file)
    return cmd


def prepare_patches(col, branch, count, start, end, ignore_binary, signoff,
                    keep_change_id=False, git_dir=None, cwd=None):
    """Figure out what patches to generate, then generate them

    The patch files are written to the current directory, e.g. 0001_xxx.patch
    0002_yyy.patch

    Args:
        col (terminal.Color): Colour output object
        branch (str): Branch to create patches from (None = current)
        count (int): Number of patches to produce, or -1 to produce patches for
            the current branch back to the upstream commit
        start (int): Start patch to use (0=first / top of branch)
        end (int): End patch to use (0=last one in series, 1=one before that,
            etc.)
        ignore_binary (bool): Don't generate patches for binary files
        keep_change_id (bool): Preserve the Change-Id tag.
        git_dir (str): Path to git repository (None to use default)
        cwd (str): Path to use for git operations (None to use current dir)

    Returns:
        Tuple:
            Series object for this series (set of patches)
            Filename of the cover letter as a string (None if none)
            patch_files: List of patch filenames, each a string, e.g.
                ['0001_xxx.patch', '0002_yyy.patch']
    """
    if count == -1:
        # Work out how many patches to send if we can
        count = (gitutil.count_commits_to_branch(branch, git_dir=git_dir) -
                 start)

    if not count:
        msg = 'No commits found to process - please use -c flag, or run:\n' \
              '  git branch --set-upstream-to remote/branch'
        sys.exit(col.build(col.RED, msg))

    # Read the metadata from the commits
    to_do = count - end
    series = patchstream.get_metadata(branch, start, to_do, git_dir)
    cover_fname, patch_files = gitutil.create_patches(
        branch, start, to_do, ignore_binary, series, signoff, git_dir=git_dir,
        cwd=cwd)

    # Fix up the patch files to our liking, and insert the cover letter
    patchstream.fix_patches(series, patch_files, keep_change_id,
                            insert_base_commit=not cover_fname, cwd=cwd)
    if cover_fname and series.get('cover'):
        patchstream.insert_cover_letter(cover_fname, series, to_do, cwd=cwd)
    return series, cover_fname, patch_files


def send(args, git_dir=None, cwd=None):
    """Create, check and send patches by email

    Args:
        args (argparse.Namespace): Arguments to patman
        cwd (str): Path to use for git operations

    Return:
        bool: True if the patches were likely sent, else False
    """
    col = terminal.Color()
    series, cover_fname, patch_files = prepare_patches(
        col, args.branch, args.count, args.start, args.end,
        args.ignore_binary, args.add_signoff,
        keep_change_id=args.keep_change_id, git_dir=git_dir, cwd=cwd)
    ok = check_patches(series, patch_files, args.check_patch,
                       args.verbose, args.check_patch_use_tree, cwd)

    ok = ok and gitutil.check_suppress_cc_config()

    its_a_go = ok or args.ignore_errors
    cmd = email_patches(
        col, series, cover_fname, patch_files, args.process_tags,
        its_a_go, args.ignore_bad_tags, args.add_maintainers,
        args.get_maintainer_script, args.limit, args.dry_run,
        args.in_reply_to, args.thread, args.smtp_server, cwd=cwd)

    return cmd and its_a_go and not args.dry_run
