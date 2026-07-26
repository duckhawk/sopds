# -*- coding: utf-8 -*-
#
# Restore an export produced by sopds_userdata_export.
#
# Books are matched by the (path, filename) pair the scanner itself uses, with
# the content digest as a fallback for a book renamed since; users by name.
# Anything that cannot be matched is counted and reported rather than guessed at.
#
# Nothing already present is overwritten unless --force, so this is safe to run
# against a live catalogue and not only into an empty one.
#
# python manage.py sopds_userdata_import backup.json [--dry-run] [--force]
import json

from django.core.management.base import BaseCommand, CommandError

from sopds import userdata


class Command(BaseCommand):
    help = 'Restore shelves, reading progress, usage counters and enriched metadata.'

    def add_arguments(self, parser):
        parser.add_argument('path', help='The file written by sopds_userdata_export.')
        parser.add_argument('--dry-run', action='store_true', default=False,
                            help='Report what would be restored without writing.')
        parser.add_argument('--force', action='store_true', default=False,
                            help='Overwrite rows that already exist.')

    def handle(self, *args, **options):
        try:
            with open(options['path'], encoding='utf-8') as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as err:
            raise CommandError('Cannot read %s: %s' % (options['path'], err))

        try:
            stats = userdata.load(payload, force=options['force'],
                                  dry_run=options['dry_run'])
        except ValueError as err:
            raise CommandError(str(err))

        prefix = 'DRY-RUN: ' if options['dry_run'] else ''
        self.stdout.write(
            '%sRestored %d book record(s), %d shelf row(s), %d theme(s), '
            '%d kosync row(s). Left alone: %d. '
            'Books not in this catalogue: %d, unknown users: %d.'
            % (prefix, stats['books'], stats['shelves'], stats['themes'],
               stats['kosync'], stats['skipped'],
               stats['unknown_books'], stats['unknown_users']))
