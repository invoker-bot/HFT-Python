#! /usr/bin/env python
# -*- coding: utf-8 -*-
from setuptools import setup, find_packages


with open('README.md', 'r', encoding='utf-8') as f:
    long_description = f.read()

_version = {}

with open('hft/_version.py', 'r', encoding='utf-8') as f:
    exec(f.read(), _version)  # pylint: disable=exec-used
    __version__ = _version['__version__']
    _appname = _version['__appname__']

with open('requirements.txt', 'r', encoding='utf-8') as f:
    requirements = f.read().splitlines()

tests_require = [
    'pytest >= 7.4.3',
    'pytest-retry >= 1.5.0',
    'pytest-mock >= 3.12.0',
]

setup(
    name=_appname,
    version=__version__,  # dev[n] .alpha[n] .beta[n] .rc[n] .post[n] .final
    author='Invoker Bot',
    author_email='invoker-bot@outlook.com',
    description='A trading bot framework for cryptocurrency exchanges.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='',
    packages=find_packages(),
    classifiers=[
        'Development Status :: 3 - Alpha',
        # 'Development Status :: 4 - Beta',
        # 'Development Status :: 5 - Production/Stable',
        # 'Development Status :: 6 - Mature',
        # 'Development Status :: 7 - Inactive',
        'Intended Audience :: Developers',
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: The Unlicense (Unlicense)',
        'Operating System :: Microsoft :: Linux',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],
    python_requires='>=3.13',
    install_requires=requirements,
    setup_requires=['setuptools_scm>=8', 'pytest-runner>=6.0.1'],
    tests_require=tests_require,
    extras_require={
        'test': tests_require,
    },
    license='Unlicense',
    entry_points={
        'console_scripts': [
            f'{_appname} = {_appname}.__main__:main',
        ]
    },
    include_package_data=True,
    package_dir={'': '.'},
    package_data={
        '': []
    }
)
