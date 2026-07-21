import os
import signal
import sys
import logging
import re

from asgiref.sync import sync_to_async

from django.core.management.base import BaseCommand
from django.conf import settings as main_settings
from django.utils.html import strip_tags
from django.db.models import Q
from django.db import close_old_connections
from django.contrib.auth.models import User
from django.utils.translation import gettext as _
from django.utils import translation

from opds_catalog.models import Book
from opds_catalog import settings, dl
from opds_catalog.opds_paginator import Paginator as OPDS_Paginator
from sopds_web_backend.settings import HALF_PAGES_LINKS
from constance import config

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import InvalidToken
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackQueryHandler, ContextTypes,
)

query_delimiter = "####"


def cmdtrans(func):
    """Activate the configured language for the duration of a handler."""
    async def wrapper(self, update, context):
        translation.activate(config.SOPDS_LANGUAGE)
        try:
            return await func(self, update, context)
        finally:
            translation.deactivate()
    return wrapper


def CheckAuthDecorator(func):
    """Restrict handlers to known active users when SOPDS_TELEBOT_AUTH is on."""
    async def wrapper(self, update, context):
        if not config.SOPDS_TELEBOT_AUTH:
            return await func(self, update, context)

        message = update.message if update.message else update.callback_query.message
        from_user = update.message.from_user if update.message else update.callback_query.from_user
        username = from_user.username

        @sync_to_async
        def _is_allowed():
            close_old_connections()
            users = User.objects.filter(username__iexact=username)
            return bool(users and users[0].is_active)

        if await _is_allowed():
            return await func(self, update, context)

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=_("Hello %s!\nUnfortunately you do not have access to information. Please contact the bot administrator.") % username)
        self.logger.info(_("Denied access for user: %s") % username)
        return
    return wrapper


class Command(BaseCommand):
    help = 'SimpleOPDS Telegram Bot engine.'
    can_import_settings = True
    leave_locale_alone = True

    def add_arguments(self, parser):
        parser.add_argument('command', help='Use [ start | stop | restart ]')
        parser.add_argument('--verbose', action='store_true', dest='verbose', default=False, help='Set verbosity level for SimpleOPDS telebot.')
        parser.add_argument('--daemon', action='store_true', dest='daemonize', default=False, help='Daemonize server')

    def handle(self, *args, **options):
        self.pidfile = os.path.join(main_settings.BASE_DIR, config.SOPDS_TELEBOT_PID)
        action = options['command']
        self.logger = logging.getLogger('')
        self.logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s')

        if settings.LOGLEVEL != logging.NOTSET:
            fh = logging.FileHandler(config.SOPDS_TELEBOT_LOG)
            fh.setLevel(settings.LOGLEVEL)
            fh.setFormatter(formatter)
            self.logger.addHandler(fh)

        if options['verbose']:
            ch = logging.StreamHandler()
            ch.setLevel(logging.DEBUG)
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)

        if (options["daemonize"] and (action in ["start"])):
            if sys.platform == "win32":
                self.stdout.write("On Windows platform Daemonize not working.")
            else:
                daemonize()

        if action == "start":
            self.start()
        elif action == "stop":
            pid = open(self.pidfile, "r").read()
            self.stop(pid)
        elif action == "restart":
            pid = open(self.pidfile, "r").read()
            self.restart(pid)

    @cmdtrans
    @CheckAuthDecorator
    async def startCommand(self, update, context):
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=_("%(subtitle)s\nHello %(username)s! To search for a book, enter part of her title or author:") %
                 {'subtitle': settings.SUBTITLE, 'username': update.message.from_user.username})
        self.logger.info("Start talking with user: %s" % update.message.from_user)

    # --- synchronous ORM helpers (run in a worker thread via sync_to_async) ---

    def BookFilter(self, query):
        q_objects = Q()
        q_objects.add(Q(search_title__contains=query.upper()), Q.OR)
        q_objects.add(Q(authors__search_full_name__contains=query.upper()), Q.OR)
        return Book.objects.filter(q_objects).order_by('search_title', '-docdate').distinct()

    def BookPager(self, books, page_num, query):
        books_count = books.count()
        op = OPDS_Paginator(books_count, 0, page_num, config.SOPDS_TELEBOT_MAXITEMS, HALF_PAGES_LINKS)
        items = []

        prev_title = ''
        prev_authors_set = set()

        summary_DOUBLES_HIDE = config.SOPDS_DOUBLES_HIDE
        start = op.d1_first_pos if ((op.d1_first_pos == 0) or (not summary_DOUBLES_HIDE)) else op.d1_first_pos - 1
        finish = op.d1_last_pos

        for row in books[start:finish + 1]:
            p = {'doubles': 0, 'lang_code': row.lang_code, 'filename': row.filename, 'path': row.path,
                 'registerdate': row.registerdate, 'id': row.id, 'annotation': strip_tags(row.annotation),
                 'docdate': row.docdate, 'format': row.format, 'title': row.title, 'filesize': row.filesize // 1000,
                 'authors': row.authors.values(), 'genres': row.genres.values(), 'series': row.series.values(),
                 'ser_no': row.bseries_set.values('ser_no')}
            if summary_DOUBLES_HIDE:
                title = p['title']
                authors_set = {a['id'] for a in p['authors']}
                if title.upper() == prev_title.upper() and authors_set == prev_authors_set:
                    items[-1]['doubles'] += 1
                else:
                    items.append(p)
                prev_title = title
                prev_authors_set = authors_set
            else:
                items.append(p)

        if summary_DOUBLES_HIDE:
            double_flag = True
            while ((finish + 1) < books_count) and double_flag:
                finish += 1
                if books[finish].title.upper() == prev_title.upper() and {a['id'] for a in books[finish].authors.values()} == prev_authors_set:
                    items[-1]['doubles'] += 1
                else:
                    double_flag = False

            if op.d1_first_pos != 0:
                items.pop(0)

        response = ''
        for b in items:
            authors = ', '.join([a['full_name'] for a in b['authors']])
            doubles = _("(doubles:%s) ") % b['doubles'] if b['doubles'] else ''
            response += '<b>%(title)s</b>\n%(author)s\n%(dbl)s/download%(link)s\n\n' % {'title': b['title'], 'author': authors, 'link': b['id'], 'dbl': doubles}

        # fix for rare empty response
        if not response:
            response = self.BookPager(books, page_num - 1, query)['message']
            op.number = page_num - 1
            op.next_page_number = op.number
            op.num_pages = op.number

        buttons = [InlineKeyboardButton('1 <<', callback_data='%s%s%s' % (query, query_delimiter, 1)),
                   InlineKeyboardButton('%s <' % op.previous_page_number, callback_data='%s%s%s' % (query, query_delimiter, op.previous_page_number)),
                   InlineKeyboardButton('[ %s ]' % op.number, callback_data='%s%s%s' % (query, query_delimiter, 'current')),
                   InlineKeyboardButton('> %s' % op.next_page_number, callback_data='%s%s%s' % (query, query_delimiter, op.next_page_number)),
                   InlineKeyboardButton('>> %s' % op.num_pages, callback_data='%s%s%s' % (query, query_delimiter, op.num_pages))]

        markup = InlineKeyboardMarkup([buttons]) if op.num_pages > 1 else None

        return {'message': response, 'buttons': markup}

    def _search(self, query, page_num):
        """Filter + paginate. Returns {'count': 0} or {'count', 'message', 'buttons'}."""
        close_old_connections()
        translation.activate(config.SOPDS_LANGUAGE)
        try:
            books = self.BookFilter(query)
            count = books.count()
            if count == 0:
                return {'count': 0}
            data = self.BookPager(books, page_num, query)
            return {'count': count, 'message': data['message'], 'buttons': data['buttons']}
        finally:
            translation.deactivate()

    def _download_payload(self, text):
        """Build the download-options message for a /downloadNNN link, or None."""
        close_old_connections()
        translation.activate(config.SOPDS_LANGUAGE)
        try:
            ids = re.findall(r'\d+$', text)
            if len(ids) != 1:
                return None
            try:
                book = Book.objects.get(id=int(ids[0]))
            except Book.DoesNotExist:
                return None

            authors = ', '.join([a['full_name'] for a in book.authors.values()])
            response = ('<b>%(title)s</b>\n%(author)s\n<b>' + _("Annotation:") + '</b>%(annotation)s\n') % {'title': book.title, 'author': authors, 'annotation': book.annotation}

            buttons = [InlineKeyboardButton(book.format.upper(), callback_data='/getfileorig%s' % book.id)]
            if book.format not in settings.NOZIP_FORMATS:
                buttons += [InlineKeyboardButton(book.format.upper() + '.ZIP', callback_data='/getfilezip%s' % book.id)]
            if (config.SOPDS_FB2TOEPUB != "") and (book.format == 'fb2'):
                buttons += [InlineKeyboardButton('EPUB', callback_data='/getfileepub%s' % book.id)]
            if (config.SOPDS_FB2TOMOBI != "") and (book.format == 'fb2'):
                buttons += [InlineKeyboardButton('MOBI', callback_data='/getfilemobi%s' % book.id)]

            return {'message': response, 'buttons': InlineKeyboardMarkup([buttons])}
        finally:
            translation.deactivate()

    def _file_payload(self, data):
        """Resolve a /getfile* callback to (document, filename), or None."""
        close_old_connections()
        ids = re.findall(r'\d+$', data)
        if len(ids) != 1:
            return None
        try:
            book = Book.objects.get(id=int(ids[0]))
        except Book.DoesNotExist:
            return None

        filename = dl.getFileName(book)
        document = None
        if re.match(r'/getfileorig', data):
            document = dl.getFileData(book)
        elif re.match(r'/getfilezip', data):
            document = dl.getFileDataZip(book)
            filename = filename + '.zip'
        elif re.match(r'/getfileepub', data):
            document = dl.getFileDataEpub(book)
            filename = filename.replace('.fb2', '.epub')
        elif re.match(r'/getfilemobi', data):
            document = dl.getFileDataMobi(book)
            filename = filename.replace('.fb2', '.mobi')

        if not document:
            return None
        return document, filename

    # --- async handlers -----------------------------------------------------

    @cmdtrans
    @CheckAuthDecorator
    async def getBooks(self, update, context):
        query = update.message.text
        chat_id = update.message.chat_id
        self.logger.info("Got message from user %s: %s" % (update.message.from_user.username, query))

        if len(query) < 3:
            await context.bot.send_message(chat_id=chat_id, text=_("Too short for search, please try again."))
            return

        await context.bot.send_message(chat_id=chat_id, text=_("I'm searching for the book: %s") % query)

        data = await sync_to_async(self._search)(query, 1)
        if data['count'] == 0:
            await context.bot.send_message(chat_id=chat_id, text=_("No results were found for your query, please try again."))
            return

        await context.bot.send_message(chat_id=chat_id, text=_("Found %s books.\nI create list, after a few seconds, select the file to download:") % data['count'])
        await context.bot.send_message(chat_id=chat_id, text=data['message'], parse_mode='HTML', reply_markup=data['buttons'])

    async def getBooksPage(self, update, context):
        callback_query = update.callback_query
        query, page_num = callback_query.data.split(query_delimiter, maxsplit=1)
        if page_num == 'current':
            return
        try:
            page_num = int(page_num)
        except (TypeError, ValueError):
            page_num = 1

        data = await sync_to_async(self._search)(query, page_num)
        if data['count'] == 0:
            return
        await context.bot.edit_message_text(
            chat_id=callback_query.message.chat_id, message_id=callback_query.message.message_id,
            text=data['message'], parse_mode='HTML', reply_markup=data['buttons'])

    @cmdtrans
    @CheckAuthDecorator
    async def downloadBooks(self, update, context):
        payload = await sync_to_async(self._download_payload)(update.message.text)
        if payload is None:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=_("The book on the link you specified is not found, try to repeat the book search first."),
                parse_mode='HTML')
            self.logger.info("Not find download links.")
            return
        await context.bot.send_message(chat_id=update.message.chat_id, text=payload['message'], parse_mode='HTML', reply_markup=payload['buttons'])
        self.logger.info("Send download buttons.")

    async def getBookFile(self, update, context):
        callback_query = update.callback_query
        result = await sync_to_async(self._file_payload)(callback_query.data)
        if result is None:
            await context.bot.send_message(
                chat_id=callback_query.message.chat_id,
                text=_("There was a technical error, please contact the Bot administrator."),
                parse_mode='HTML')
            self.logger.info("Book get error.")
            return
        document, filename = result
        await context.bot.send_document(chat_id=callback_query.message.chat_id, document=document, filename=filename)
        document.close()
        self.logger.info("Send file: %s" % filename)

    @cmdtrans
    @CheckAuthDecorator
    async def botCallback(self, update, context):
        if re.match(r'/getfile', update.callback_query.data):
            return await self.getBookFile(update, context)
        return await self.getBooksPage(update, context)

    def start(self):
        writepid(self.pidfile)
        quit_command = 'CTRL-BREAK' if sys.platform == 'win32' else 'CONTROL-C'
        self.stdout.write("Quit the sopds_telebot with %s.\n" % quit_command)
        try:
            application = Application.builder().token(config.SOPDS_TELEBOT_API_TOKEN).build()
            application.add_handler(CommandHandler('start', self.startCommand))
            # /downloadNNN is matched before free-text book queries.
            application.add_handler(MessageHandler(filters.Regex(r'^/download\d+$'), self.downloadBooks))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.getBooks))
            application.add_handler(CallbackQueryHandler(self.botCallback))

            application.run_polling(drop_pending_updates=True)
        except InvalidToken:
            self.stdout.write('Invalid telegram token.\nSet correct token for telegram API by command:\n python3 manage.py sopds_util setconf SOPDS_TELEBOT_API_TOKEN "<token>"')
            self.logger.error('Invalid telegram token.')
        except (KeyboardInterrupt, SystemExit):
            pass

    def stop(self, pid):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError as e:
            self.stdout.write("Error stopping sopds_telebot: %s" % str(e))

    def restart(self, pid):
        self.stop(pid)
        self.start()


def writepid(pid_file):
    """Write the process ID to disk."""
    fp = open(pid_file, "w")
    fp.write(str(os.getpid()))
    fp.close()


def daemonize():
    """Detach from the terminal and continue as a daemon."""
    # swiped from twisted/scripts/twistd.py
    if os.fork():
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    os.umask(0)

    std_in = open("/dev/null", 'r')
    std_out = open(config.SOPDS_TELEBOT_LOG, 'a+')
    os.dup2(std_in.fileno(), sys.stdin.fileno())
    os.dup2(std_out.fileno(), sys.stdout.fileno())
    os.dup2(std_out.fileno(), sys.stderr.fileno())

    os.close(std_in.fileno())
    os.close(std_out.fileno())
