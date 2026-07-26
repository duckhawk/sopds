import os
import signal
import sys
import logging

try:
    import fcntl
except ImportError:   # Windows has no fcntl; locking is then a no-op.
    fcntl = None

from django.core.management.base import BaseCommand
from django.db import connection
from django.conf import settings as main_settings

from opds_catalog import opdsdb
from opds_catalog.models import Counter
from opds_catalog.sopdscan import opdsScanner

# Fixed key for the scan's PostgreSQL session-level advisory lock. Arbitrary but
# stable; shared by every scanner process against the same database.
SCAN_ADVISORY_LOCK_KEY = 0x50D5_5CA4  # "SOPDS SCAN" mnemonic, fits a signed bigint
#from opds_catalog.settings import SCANNER_LOG, SCAN_SHED_DAY, SCAN_SHED_DOW, SCAN_SHED_HOUR, SCAN_SHED_MIN, LOGLEVEL, SCANNER_PID
from opds_catalog import settings 
from constance import config

class Command(BaseCommand):
    help = 'Scan Books Collection.'
    scan_is_active = False

    def add_arguments(self, parser):
        parser.add_argument('command', help='Use [ scan | start | stop | restart ]')
        parser.add_argument('--verbose',action='store_true', dest='verbose', default=False, help='Set verbosity level for books collection scan.')
        parser.add_argument('--daemon',action='store_true', dest='daemonize', default=False, help='Daemonize server')
        
    def handle(self, *args, **options): 
        self.pidfile = os.path.join(main_settings.BASE_DIR, config.SOPDS_SCANNER_PID)
        action = options['command']            
        self.logger = logging.getLogger('')
        self.logger.setLevel(logging.DEBUG)
        formatter=logging.Formatter('%(asctime)s %(levelname)-8s %(message)s')

        if settings.LOGLEVEL!=logging.NOTSET:
            # Создаем обработчик для записи логов в файл
            fh = logging.FileHandler(config.SOPDS_SCANNER_LOG)
            fh.setLevel(settings.LOGLEVEL)
            fh.setFormatter(formatter)
            self.logger.addHandler(fh)

        if options['verbose']:
            # Создадим обработчик для вывода логов на экран с максимальным уровнем вывода
            ch = logging.StreamHandler()
            ch.setLevel(logging.DEBUG)
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)
            
        if (options["daemonize"] and (action in ["start", "scan"])):
            if sys.platform == "win32":
                self.stdout.write("On Windows platform Daemonize not working.")
            else:         
                daemonize()            

        if action=='scan':
            self.stdout.write('Startup once book-scan.')
            self.scan()   
            self.stdout.write('Complete book-scan.')        
        elif action == "start":
            self.start()
        elif action == "stop":
            pid = open(self.pidfile, "r").read()
            self.stop(pid)
        elif action == "restart":
            pid = open(self.pidfile, "r").read()
            self.restart(pid)            

    def _acquire_lock(self):
        """Take a cross-process exclusive lock (non-blocking) on <pidfile>.lock.

        The in-process ``scan_is_active`` flag does not stop a second *process*
        (e.g. a cron `start` overlapping a manual `scan`) from running the
        avail 1->2->delete sweep concurrently and deleting live books. Returns
        an open file object to keep held for the scan's duration, or None if
        another process holds the lock. On platforms without fcntl, locking is
        skipped (returns the file object so the scan still runs).
        """
        try:
            fd = open(self.pidfile + '.lock', 'w')
        except OSError:
            return None
        if fcntl is None:
            return fd
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fd.close()
            return None
        return fd

    @staticmethod
    def _release_lock(fd):
        if fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            fd.close()

    def _acquire_scan_lock(self):
        """Acquire an exclusive scan lock; return a release callable, or None
        if another scan already holds it.

        On PostgreSQL use a **session-level advisory lock**: it is bound to the
        DB connection, so it is released automatically when the scanner
        process/pod dies mid-scan. A pidfile flock (below) is per-pod and goes
        stale across pod restarts, which is why an interrupted scan could
        overlap the next one. On other backends (dev/sqlite) fall back to the
        flock.
        """
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_try_advisory_lock(%s)', [SCAN_ADVISORY_LOCK_KEY])
                if not cursor.fetchone()[0]:
                    return None
            return self._release_pg_lock
        fd = self._acquire_lock()
        if fd is None:
            return None
        return lambda: self._release_lock(fd)

    @staticmethod
    def _release_pg_lock():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [SCAN_ADVISORY_LOCK_KEY])

    def scan(self):
        if self.scan_is_active:
            self.stdout.write('Scan process already active. Skip currend job.')
            return

        # Refresh a stale pooled connection before locking/scanning on it.
        if connection.connection and not connection.is_usable():
            connection.close()

        release_lock = self._acquire_scan_lock()
        if release_lock is None:
            self.stdout.write('Another scan is already running (locked). Skip current job.')
            return

        self.scan_is_active = True
        try:
            scanner=opdsScanner(self.logger)
            # scan_all() commits per directory (and keeps the delete-sweep
            # atomic on its own), so it is not wrapped in one giant transaction.
            scanner.scan_all()
            Counter.objects.update_known_counters()
            # Reclaim the dead tuples the avail sweep churns and refresh stats /
            # the visibility map, so post-scan reads keep their Index-Only Scans
            # instead of slowing to tens of seconds until autovacuum catches up.
            opdsdb.vacuum_analyze()
        finally:
            self.scan_is_active = False
            release_lock()
        
    def update_shedule(self):
        self.SCAN_SHED_DAY = config.SOPDS_SCAN_SHED_DAY
        self.SCAN_SHED_DOW = config.SOPDS_SCAN_SHED_DOW
        self.SCAN_SHED_HOUR = config.SOPDS_SCAN_SHED_HOUR
        self.SCAN_SHED_MIN = config.SOPDS_SCAN_SHED_MIN
        self.stdout.write('Reconfigure scheduled book-scan (min=%s, hour=%s, day_of_week=%s, day=%s).'%(self.SCAN_SHED_MIN,self.SCAN_SHED_HOUR,self.SCAN_SHED_DOW,self.SCAN_SHED_DAY))
        self.sched.reschedule_job('scan', trigger='cron', day=self.SCAN_SHED_DAY, day_of_week=self.SCAN_SHED_DOW, hour=self.SCAN_SHED_HOUR, minute=self.SCAN_SHED_MIN)
    
    def check_settings(self):
        if connection.connection and not connection.is_usable():
            connection.close()
        settings.constance_update_all()
        if not (self.SCAN_SHED_MIN==config.SOPDS_SCAN_SHED_MIN and \
           self.SCAN_SHED_HOUR==config.SOPDS_SCAN_SHED_HOUR and \
           self.SCAN_SHED_DOW==config.SOPDS_SCAN_SHED_DOW and \
           self.SCAN_SHED_DAY==config.SOPDS_SCAN_SHED_DAY):
            self.update_shedule()
        if config.SOPDS_SCAN_START_DIRECTLY:
            config.SOPDS_SCAN_START_DIRECTLY = False
            self.stdout.write('Startup scannyng directly by SOPDS_SCAN_START_DIRECTLY flag.')
            self.sched.add_job(self.scan, id='scan_directly')
                       
    def start(self):
        # Imported lazily: apscheduler is only needed to run the scheduler, so
        # the module (and its lock helpers) can be imported without it present.
        from apscheduler.schedulers.blocking import BlockingScheduler
        writepid(self.pidfile)
        self.SCAN_SHED_DAY = config.SOPDS_SCAN_SHED_DAY
        self.SCAN_SHED_DOW = config.SOPDS_SCAN_SHED_DOW
        self.SCAN_SHED_HOUR = config.SOPDS_SCAN_SHED_HOUR
        self.SCAN_SHED_MIN = config.SOPDS_SCAN_SHED_MIN
        self.stdout.write('Startup scheduled book-scan (min=%s, hour=%s, day_of_week=%s, day=%s).'%(self.SCAN_SHED_MIN,self.SCAN_SHED_HOUR,self.SCAN_SHED_DOW,self.SCAN_SHED_DAY))
        self.sched = BlockingScheduler()
        self.sched.add_job(self.scan, 'cron', day=self.SCAN_SHED_DAY, day_of_week=self.SCAN_SHED_DOW, hour=self.SCAN_SHED_HOUR, minute=self.SCAN_SHED_MIN, id='scan')
        self.sched.add_job(self.check_settings, 'cron', minute='*/10', id='check')
        quit_command = 'CTRL-BREAK' if sys.platform == 'win32' else 'CONTROL-C'
        self.stdout.write("Quit the server with %s.\n"%quit_command)  
        try:
            self.sched.start()
        except (KeyboardInterrupt, SystemExit):
            pass            
    
    def stop(self, pid):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError as e:
            self.stdout.write("Error stopping sopds_scanner: %s"%str(e))
    
    def restart(self, pid):
        self.stop(pid)
        self.start()

def writepid(pid_file):
    """
    Write the process ID to disk.
    """
    fp = open(pid_file, "w")
    fp.write(str(os.getpid()))
    fp.close()
    
def daemonize():
    """
    Detach from the terminal and continue as a daemon.
    """
    # swiped from twisted/scripts/twistd.py
    # See http://www.erlenstar.demon.co.uk/unix/faq_toc.html#TOC16
    if os.fork():   # launch child and...
        os._exit(0) # kill off parent
    os.setsid()
    if os.fork():   # launch child and...
        os._exit(0) # kill off parent again.
    os.umask(0)

    std_in = open("/dev/null", 'r')
    std_out = open(config.SOPDS_SCANNER_LOG, 'a+')
    os.dup2(std_in.fileno(), sys.stdin.fileno())
    os.dup2(std_out.fileno(), sys.stdout.fileno())
    os.dup2(std_out.fileno(), sys.stderr.fileno())    
    
#    null = os.open("/dev/null", os.O_RDWR)
#    for i in range(3):
#        try:
#            os.dup2(null, i)
#        except OSError as e:
#            if e.errno != errno.EBADF:
#                raise
    os.close(std_in.fileno())
    os.close(std_out.fileno())


    

        
 

