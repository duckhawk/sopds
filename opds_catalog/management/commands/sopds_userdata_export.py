# -*- coding: utf-8 -*-
#
# Write out the part of the database a rescan cannot rebuild: shelves, reading
# progress, usage counters and the metadata sopds_enrich fetched.
#
# See sopds.userdata for why `dumpdata` does not serve here — every one of those
# tables points at Book by id, and a rebuilt catalogue assigns different ids.
#
# python manage.py sopds_userdata_export --output backup.json
import json
import sys

from django.core.management.base import BaseCommand

from sopds import userdata


class Command(BaseCommand):
    help = 'Export shelves, reading progress, usage counters and enriched metadata.'

    def add_arguments(self, parser):
        parser.add_argument('--output', default='-',
                            help='File to write to; "-" (the default) is stdout.')
        parser.add_argument('--indent', type=int, default=1,
                            help='JSON indentation. 0 for the most compact output.')

    def handle(self, *args, **options):
        payload = userdata.export()
        text = json.dumps(payload, ensure_ascii=False,
                          indent=options['indent'] or None, sort_keys=True)

        if options['output'] == '-':
            self.stdout.write(text)
        else:
            with open(options['output'], 'w', encoding='utf-8') as handle:
                handle.write(text)

        # To stderr, so it cannot end up inside the JSON when piping to a file.
        print('Exported %d book record(s), %d shelf row(s), %d theme(s), '
              '%d kosync row(s).'
              % (len(payload['books']), len(payload['shelves']),
                 len(payload['themes']), len(payload['kosync'])), file=sys.stderr)
