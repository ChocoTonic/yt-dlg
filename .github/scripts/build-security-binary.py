"""Rebuild the desktop executable and inspect its bundled trust store offline."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


run(sys.executable, '-m', 'pip', 'install', '--upgrade', 'pip>=26.2.2',
    'setuptools>=83.0.0', 'wheel>=0.46.0')
run(sys.executable, '-m', 'pip', 'install', '-r', 'requirements/requirements.in',
    '-r', 'requirements/requirements-binaries.in', 'pytest>=8')
run(sys.executable, 'setup.py', 'build_trans')
run(sys.executable, '-m', 'pytest', '-q')
run(sys.executable, '-m', 'PyInstaller', '--onefile', '--windowed', '--noconfirm',
    '--clean', '--name', 'yt-dlg', '--distpath', 'security-dist',
    '--workpath', '.binary-work', '--specpath', '.binary-work',
    '--add-data', f'{Path("youtube_dl_gui/data").resolve()}{os.pathsep}youtube_dl_gui/data',
    '--add-data', f'{Path("youtube_dl_gui/locale").resolve()}{os.pathsep}youtube_dl_gui/locale',
    '--collect-data', 'certifi', '--exclude-module', 'tests',
    'youtube_dl_gui/__main__.py')

certifi = importlib.import_module('certifi')
Version = importlib.import_module('packaging.version').Version
CArchiveReader = importlib.import_module('PyInstaller.archive.readers').CArchiveReader

assert Version(importlib.metadata.version('certifi')) >= Version('2026.10.19')
output = Path('security-dist')
binary = output / ('yt-dlg.exe' if os.name == 'nt' else 'yt-dlg')
archive = CArchiveReader(str(binary))
bundle_name = next(name for name in archive.toc
                   if name.replace('\\', '/') == 'certifi/cacert.pem')
bundle = archive.extract(bundle_name)
assert bundle == Path(certifi.where()).read_bytes(), 'Bundled CA store differs from patched certifi'
runtime_libraries = {}
for name in archive.toc:
    basename = name.replace('\\', '/').rsplit('/', 1)[-1].lower()
    if basename.startswith(('libcrypto', 'libssl', 'libpython', 'python3')):
        library = archive.extract(name)
        runtime_libraries[name] = hashlib.sha256(library).hexdigest()
        if basename.startswith('libcrypto'):
            assert ssl.OPENSSL_VERSION.encode() in library, 'Bundled OpenSSL differs from build runtime'
assert any('libcrypto' in name.lower() for name in runtime_libraries), 'Missing bundled OpenSSL'
with tempfile.TemporaryDirectory(prefix='yt-dlg-offline-smoke-') as config:
    environment = dict(os.environ, APPDATA=config, XDG_CONFIG_HOME=config, HOME=config)
    result = run(str(binary.resolve()), '--version', env=environment, timeout=60,
                 capture_output=True, text=True)
    print(result.stdout)

revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
metadata = {
    'source_revision': revision,
    'platform': sys.platform,
    'python': sys.version,
    'openssl': ssl.OPENSSL_VERSION,
    'bundled_runtime_library_sha256': runtime_libraries,
    'binary': binary.name,
    'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
    'certifi_bundle_sha256': hashlib.sha256(bundle).hexdigest(),
    'bundled_ca_store_verified': True,
    'packaged_offline_version_startup_exit_code': result.returncode,
    'dependencies': {name: importlib.metadata.version(name)
                     for name in ['certifi', 'PyInstaller', 'wxPython', 'Pillow', 'numpy', 'pypubsub']},
}
if sys.platform == 'linux':
    metadata['distribution_security_package_versions'] = subprocess.check_output(
        ['dpkg-query', '-W', 'libssl3t64', 'openssl', 'python3.12', 'python3-wxgtk4.0'],
        text=True).splitlines()
(output / 'provenance.json').write_text(json.dumps(metadata, indent=2) + '\n')
print(json.dumps(metadata, indent=2))
