# coding: utf-8
import os
import argparse
import subprocess

import versioneer

import platformio_helpers as pioh

from typing import Dict
from importlib import import_module

from path_helpers import path
from base_node_rpc.helpers import generate_all_code

DEFAULT_ARDUINO_BOARDS = []

PLATFORMIO_ENVS = ['default']


def get_properties(**kwargs) -> Dict:
    version = versioneer.get_version()

    try:
        base_node_version = import_module('base_node_rpc').__version__
        print(f"\033[94mBase node version: {base_node_version}\033[0m")
    except ImportError:
        base_node_version = None

    module_name = kwargs.get('module_name')
    package_name = kwargs.get('package_name')
    url = f'https://github.com/wheeler-microfluidics/{package_name}.py'

    properties = dict(package_name=package_name,
                      display_name=package_name,
                      manufacturer='Sci-bots Inc.',
                      software_version=version,
                      base_node_software_version=base_node_version,
                      url=url
                      )

    meta = dict(short_description='Firmware for MR-Box controller board',
                long_description='Firmware for MR-Box controller board for various peripherals (e.g., motorized magnet, LED panels, micro pump, temperature/humidity sensor)',
                author='Christian Fobel',
                author_email='christian@fobel.net',
                version=version,
                license='BSD',
                category='Communication',
                architectures='avr',
                )

    lib_properties = {**properties, **meta}

    options = dict(pointer_width=32,
                   rpc_module=import_module(module_name) if module_name else None,
                   PROPERTIES=properties,
                   LIB_PROPERTIES=lib_properties,
                   base_classes=['BaseNodeSerialHandler',
                                 'BaseNodeEeprom',
                                 'BaseNodeConfig<ConfigMessage, Address>'],
                   rpc_classes=[
                                # f'{module_name}::PMT',
                                # f'{module_name}::Pump',
                                # f'{module_name}::Max11210Adc',
                                f'{module_name}::Node',
                                f'base_node_rpc::ZStage',
                                ],
                   DEFAULT_ARDUINO_BOARDS=DEFAULT_ARDUINO_BOARDS,
                   PLATFORMIO_ENVS=PLATFORMIO_ENVS,
                   )

    return {**kwargs, **options}


def transfer(**kwargs) -> None:
    # Copy Arduino library to Conda include directory
    source_dir = kwargs.get('source_dir')
    lib_name = kwargs.get('lib_name')
    # source_dir = path(source_dir).joinpath(module_name, 'Arduino', 'library', lib_name) # Use this for Arduino libs
    source_dir = path(source_dir).joinpath('lib', lib_name)
    install_dir = pioh.conda_arduino_include_path().joinpath(lib_name)
    if install_dir.exists():
        install_dir.rmtree()
    source_dir.copytree(install_dir)
    print(f"Copied tree from '{source_dir}' to '{install_dir}'")


def compile_protobufs(**kwargs) -> None:
    import nanopb_helpers as pbh

    package_name = kwargs.get('package_name')
    source_path = path(kwargs.get('source_dir')).joinpath(package_name, 'metadata.proto')
    code = pbh.compile_pb(source_path)
    output_path = source_path.with_suffix('.py')
    print(f'Generated {source_path} > {output_path}')
    output_path.write_text(code['python'])
    print(f'Generated {output_path.name} > {output_path}')


def copy_compiled_firmware(**kwargs) -> None:
    # Copy compiled firmware to Conda bin directory
    source_dir = kwargs.get('source_dir')
    package_name = kwargs.get('package_name')
    src_dir = path(source_dir)
    pio_bin_dir = pioh.conda_bin_path().joinpath(package_name)

    pio_bin_dir.makedirs(exist_ok=True)

    src_dir.joinpath('platformio.ini').copy2(pio_bin_dir.joinpath('platformio.ini'))

    for pio_platform in PLATFORMIO_ENVS:
        hex_dir = pio_bin_dir.joinpath(pio_platform)
        hex_dir.makedirs(exist_ok=True)
        src = src_dir.joinpath('.pio', 'build', pio_platform, 'firmware.hex')
        dest = hex_dir.joinpath('firmware.hex')
        src.copy2(dest)
        print(f"Copied '{dest.name}' > '{dest}'")


def transfer_icons(**kwargs) -> None:
    prefix = kwargs.get('prefix')
    src = path(__file__).joinpath('.conda-recipe')
    dest = path(prefix).joinpath('Menu')
    dest.makedirs(exist_ok=True)
    for file in src.files('*[.ico|.json]'):
        file.copy2(dest)
        print(f"Copied '{file.name}' > '{file}'")


def cli_parser():
    parser = argparse.ArgumentParser(description='Transfer header files to include directory.')
    parser.add_argument('source_dir')
    parser.add_argument('prefix')
    parser.add_argument('package_name')

    args = parser.parse_args()
    args_dict = vars(args)
    args_dict['module_name'] = args_dict['package_name'].replace('-', '_')
    args_dict['lib_name'] = ''.join(word.capitalize() for word in args_dict['package_name'].split('-'))
    execute(**args_dict)


def execute(**kwargs):
    properties = get_properties(**kwargs)

    top = '>' * 180
    print(top)

    generate_all_code(properties)
    # compile_protobufs(**kwargs)
    transfer(**kwargs)
    try:
        # Set up environment with PLATFORMIO_LIB_EXTRA_DIRS
        env = os.environ.copy()
        env['PLATFORMIO_LIB_EXTRA_DIRS'] = str(pioh.conda_arduino_include_path())
        print(f"Setting PLATFORMIO_LIB_EXTRA_DIRS={env['PLATFORMIO_LIB_EXTRA_DIRS']}")

        # Run platformio with the modified environment
        subprocess.run(['pio', 'run'], env=env)
        copy_compiled_firmware(**kwargs)
    except FileNotFoundError:
        print('Failed to generate firmware')
    transfer_icons(**kwargs)

    print('<' * len(top))

import os
os.getcwd()
if __name__ == '__main__':
    cli_parser()
