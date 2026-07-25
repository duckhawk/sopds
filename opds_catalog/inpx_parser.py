'''
Created on 14 нояб. 2016 г.

@author: Shelepnev, Dmitry
'''

# -*- coding: utf-8 -*-
import os
import zipfile
#from opds_catalog import settings
from constance import config

from opds_catalog.ziptools import open_zipfile

sAuthor = 'AUTHOR'
sGenre  = 'GENRE'
sTitle  = 'TITLE'
sSeries = 'SERIES'
sSerNo  = 'SERNO'
sFile   = 'FILE'
sSize   = 'SIZE'
sLibId  = 'LIBID'
sDel    = 'DEL'
sExt    = 'EXT'
sDate   = 'DATE'
sLang   = 'LANG'
sInsNo  = 'INSNO'
sFolder = 'FOLDER'
sLibRate= 'LIBRATE'
sKeyWords='KEYWORDS'


class Inpx:
    def __init__(self, inpx_file, append_callback, inpskip_callback = lambda inpx,inp,size:0):
        self.inpx_file = inpx_file
        self.inpx_catalog = os.path.dirname(inpx_file)
        self.inpx_structure = False
        self.inpx_folders = False
        self.inpx_format = []
        self.inpx_archive = False
        self.inpx_arch_fnames = []
        self.inpx_encoding = 'utf-8'
        self.inpx_separator = b'\x04'
        self.inpx_itemseparator = ':'
        self.append_callback = append_callback
        self.inpskip_callback = inpskip_callback
        self.TEST_ZIP = config.SOPDS_INPX_TEST_ZIP
        self.TEST_FILES = config.SOPDS_INPX_TEST_FILES
        self.error = 0       
        
    @staticmethod
    def _safe_folder(folder):
        """Return a FOLDER value safe to join under inpx_catalog, or None.

        FOLDER comes from untrusted INP content; an absolute path or a ``..``
        segment would let a crafted INPX escape the collection directory
        (path traversal / existence oracle). Sub-paths with ``/`` are allowed.
        """
        folder = (folder or '').strip()
        if not folder or os.path.isabs(folder):
            return None
        if '..' in folder.replace('\\', '/').split('/'):
            return None
        return folder

    def parse(self):
        with zipfile.ZipFile(self.inpx_file, "r") as finpx:
            filelist = finpx.namelist()
            # здесь читаем формат файлов inp, если есть, если нет, то по умолчанию
            if 'structure.info' in filelist:
                self.inpx_structure = True
                with finpx.open('structure.info') as fsds:
                    fsb = str(fsds.read(), 'utf-8', 'replace')
                self.inpx_format = fsb.split(';')
                self.inpx_folders = sFolder in self.inpx_format
            else:
                self.inpx_format = [sAuthor,sGenre,sTitle,sSeries,sSerNo,sFile,sSize,sLibId,sDel,sExt,sDate,sLang]

            # здесь читаем список архивов в коллекции, если указано явно
            # эту информацию надо как-то использовать, чтобы протестировать наличие zip
            #if 'archives.info' in filelist:
            #    self.inpx_archive = True
            #    self.inpx_arch_fnames = finpx.open('archives.info').readlines()

            for inp_file in filelist:
                (inp_name,inp_ext) = os.path.splitext(inp_file)

                # Если файл не INP то пропускаем
                if inp_ext.upper() != '.INP':
                    continue

                # Пропускаем разбор INP файла, если его размер не изменился
                if self.inpskip_callback(self.inpx_file, inp_file, finpx.getinfo(inp_file).file_size):
                    continue

                with finpx.open(inp_file) as finp:
                    for line in finp:
                        meta_list = line.split(self.inpx_separator)
                        meta_data = {}

                        # Добавляем sFolder если он не определен
                        if not self.inpx_folders:
                            meta_data[sFolder] = "%s%s" % (inp_name, '.zip')

                        for idx, key in enumerate(self.inpx_format):
                            try:
                                # INP records are frequently cp1251, not utf-8;
                                # replace undecodable bytes instead of raising
                                # (which used to abort the whole INPX/scan).
                                if key in [sAuthor,sGenre,sSeries]:
                                    meta_data[key] = meta_list[idx].decode(self.inpx_encoding, 'replace').split(self.inpx_itemseparator)
                                    if '' in  meta_data[key]:
                                        meta_data[key].remove('')
                                else:
                                    meta_data[key] = meta_list[idx].decode(self.inpx_encoding, 'replace')
                            except IndexError:
                                meta_data[key] = ''

                        # Если книга помечена как удаленная в INP, то пропускаем вызов callback
                        if not (meta_data.get(sDel, '').strip() in ['','0']):
                            continue

                        # FOLDER is untrusted: reject path-traversal before use.
                        # Store the sanitised value back (also drops the stray
                        # trailing newline when FOLDER is the last INP column).
                        folder = self._safe_folder(meta_data.get(sFolder, ''))
                        if folder is None:
                            continue
                        meta_data[sFolder] = folder

                        # Если решили проверять на наличие ZIP файла или книги в ZIP, а самого ZIP файла нет - то пропускаем вызов callback
                        zip_file = os.path.join(self.inpx_catalog, folder)
                        if (self.TEST_ZIP or self.TEST_FILES) and not os.path.isfile(zip_file):
                            continue

                        # Если нужно выполнить проверку книги в ZIP, а ее там не оказалось, то пропускаем вызов callback
                        if self.TEST_FILES:
                            # open_zipfile decodes cp866 member names, so cyrillic
                            # book filenames match instead of being wrongly skipped.
                            with open_zipfile(zip_file) as zf:
                                names = zf.namelist()
                            if not "%s.%s"%(meta_data[sFile],meta_data[sExt]) in names:
                                continue

                        self.append_callback(self.inpx_file, inp_name, meta_data)
        