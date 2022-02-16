import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from pytest_embedded.app import App


class IdfApp(App):
    """
    Idf App class

    Attributes:
        app_path (str): App path
        binary_path (str): binary file path
        elf_file (str): elf file path
        parttool_path (str): partition tool path
        sdkconfig (dict[str, str]): dict contains all k-v pairs from the sdkconfig file
        flash_files (list[Tuple[int, str, str]]): list of (offset, file path, encrypted) of files need to be flashed in
        flash_settings (dict[str, Any]): dict of flash settings
        partition_table (dict[str, Any]): dict generated by partition tool
    """

    FLASH_ARGS_FILENAME = 'flasher_args.json'

    def __init__(
        self,
        app_path: Optional[str] = None,
        build_dir: Optional[str] = None,
        part_tool: Optional[str] = None,
        **kwargs,
    ):
        """
        Args:
            app_path: App path
            build_dir: Build directory
            part_tool: Partition tool path
        """
        super().__init__(app_path, build_dir, **kwargs)
        if not self.binary_path:
            logging.debug('Binary path not specified, skipping parsing app...')
            return

        self.elf_file = self._get_elf_file()
        self.bin_file = self._get_bin_file()
        self.parttool_path = self._get_parttool_file(part_tool)

        self.flash_files, self.flash_settings = self._parse_flash_args()
        self.partition_table = self._parse_partition_table()

        self.sdkconfig = self._parse_sdkconfig()
        if not self.sdkconfig:
            return

        self.target = self._get_target_from_sdkconfig()

    def _get_elf_file(self) -> Optional[str]:
        for fn in os.listdir(self.binary_path):
            if os.path.splitext(fn)[-1] == '.elf':
                return os.path.realpath(os.path.join(self.binary_path, fn))
        return None

    def _get_bin_file(self) -> Optional[str]:
        for fn in os.listdir(self.binary_path):
            if os.path.splitext(fn)[-1] == '.bin':
                return os.path.realpath(os.path.join(self.binary_path, fn))
        return None

    def _parse_sdkconfig(self) -> Optional[Dict[str, Any]]:
        sdkconfig_json_path = os.path.join(self.binary_path, 'config', 'sdkconfig.json')
        if not os.path.isfile(sdkconfig_json_path):
            logging.warning(f'{sdkconfig_json_path} doesn\'t exist. Skipping...')
            return None

        return json.load(open(sdkconfig_json_path))

    def _get_flash_args_file(self) -> Optional[str]:
        for fn in os.listdir(self.binary_path):
            if fn == self.FLASH_ARGS_FILENAME:
                return os.path.realpath(os.path.join(self.binary_path, fn))
        return None

    @staticmethod
    def _is_encrypted(flash_args: Dict[str, Any], offset: int, file_path: str):
        for entry in flash_args.values():
            try:
                if (entry['offset'], entry['file']) == (offset, file_path):
                    return entry['encrypted'] == 'true'
            except (TypeError, KeyError):
                continue

        return False

    def _parse_flash_args(
        self,
    ) -> Tuple[Optional[List[Tuple[int, str, bool]]], Optional[Dict[str, Any]]]:
        """
        Returns:
            (flash_files: [(offset, file_path, encrypted), ...], flash_settings: dict[str, str])
        """
        flash_args_filepath = self._get_flash_args_file()
        if not flash_args_filepath:
            return None, None

        with open(flash_args_filepath) as fr:
            flash_args = json.load(fr)

        res = []
        for (offset, file_path) in flash_args['flash_files'].items():
            encrypted = self._is_encrypted(flash_args, offset, file_path)
            res.append((int(offset, 0), os.path.join(self.binary_path, file_path), encrypted))

        flash_files = sorted(res)
        flash_settings = flash_args['flash_settings']
        flash_settings['encrypt'] = any([file[2] for file in res])

        return flash_files, flash_settings

    def _get_parttool_file(self, parttool: Optional[str]) -> Optional[str]:
        parttool_filepath = parttool or os.path.join(
            os.getenv('IDF_PATH', ''),
            'components',
            'partition_table',
            'gen_esp32part.py',
        )
        if os.path.isfile(parttool_filepath):
            return os.path.realpath(parttool_filepath)
        logging.warning('Partition Tool not found. (Default: $IDF_PATH/components/partition_table/gen_esp32part.py)')
        return None

    def _parse_partition_table(self) -> Optional[Dict[str, Any]]:
        if not (self.parttool_path and self.flash_files):
            return None

        errors = []
        for _, file, _ in self.flash_files:
            if 'partition' in os.path.split(file)[1]:
                partition_file = os.path.join(self.binary_path, file)
                process = subprocess.Popen(
                    [sys.executable, self.parttool_path, partition_file],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = process.communicate()
                raw_data = stdout.decode() if isinstance(stdout, bytes) else stdout
                raw_error = stderr.decode() if isinstance(stderr, bytes) else stderr

                if 'Traceback' in raw_error:
                    # Some exception occurred. It is possible that we've tried the wrong binary file.
                    errors.append((file, raw_error))
                    continue

                break
        else:
            traceback_msg = '\n'.join([f'{self.parttool_path} {p}:{os.linesep}{msg}' for p, msg in errors])
            raise ValueError(f'No partition table found under {self.binary_path}\n' f'{traceback_msg}')

        partition_table = {}
        for line in raw_data.splitlines():
            if line[0] != '#':
                try:
                    _name, _type, _subtype, _offset, _size, _flags = line.split(',')
                    if _size[-1] == 'K':
                        _size = int(_size[:-1]) * 1024
                    elif _size[-1] == 'M':
                        _size = int(_size[:-1]) * 1024 * 1024
                    else:
                        _size = int(_size)
                    _offset = int(_offset, 0)
                except ValueError:
                    continue
                partition_table[_name] = {
                    'type': _type,
                    'subtype': _subtype,
                    'offset': _offset,
                    'size': _size,
                    'flags': _flags,
                }
        return partition_table

    def _get_target_from_sdkconfig(self):
        return self.sdkconfig.get('IDF_TARGET', 'esp32')
