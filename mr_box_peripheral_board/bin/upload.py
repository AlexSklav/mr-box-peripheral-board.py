from typing import List

from platformio_helpers.upload import upload_conda
import platformio_helpers as pioh


def upload():
    upload_conda('mr-box-peripheral-board')


def firmware_environments() -> List[str]:
    """
    List the available firmware environments (i.e., hardware versions).

    Returns
    -------
    list of str
        Sorted firmware environment names.

    Raises
    ------
    IOError
        If the firmware directory does not exist or contains no environments.
    """
    firmware_dir = pioh.conda_bin_path().joinpath('mr-box-peripheral-board')
    if not firmware_dir.isdir():
        raise IOError('Firmware directory not found: '
                      f'{firmware_dir}.  Is the `mr-box-peripheral-board` '
                      'firmware package installed?')
    environments = sorted([dir_i.name for dir_i in firmware_dir.dirs()])
    if not environments:
        raise IOError(f'No firmware environments found in: {firmware_dir}.')
    return environments


if __name__ == '__main__':
    from argparse import ArgumentParser

    environments = firmware_environments()
    parser = ArgumentParser(description='Upload firmware to board.')
    parser.add_argument('-p', '--port', default=None)
    parser.add_argument('-b', '--hardware-version', default=environments[-1],
                        choices=environments)
    args = parser.parse_args()
    extra_args = [] if args.port is None else ['--upload-port', args.port]

    upload_conda('mr-box-peripheral-board',
                 env_name=args.hardware_version, extra_args=extra_args)
