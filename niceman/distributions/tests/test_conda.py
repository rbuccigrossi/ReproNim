# emacs: -*- mode: python; py-indent-offset: 4; tab-width: 4; indent-tabs-mode: nil; coding: utf-8 -*-
# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the niceman package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
import collections
import io
import logging
import os
import pytest

import sys
from appdirs import AppDirs
from mock import mock
from subprocess import call
from unittest import SkipTest

import yaml
import attr
from niceman.formats.niceman import NicemanProvenance
from niceman.tests.utils import create_pymodule
from niceman.tests.utils import skip_if_no_network, assert_is_subset_recur

import json

from niceman.distributions.conda import CondaTracer, \
    get_conda_platform_from_python


@pytest.fixture(scope="session")
@skip_if_no_network
def get_conda_test_dir():
    dirs = AppDirs('niceman')
    test_dir = os.path.join(dirs.user_cache_dir, 'conda_test')
    if os.path.exists(test_dir):
        return test_dir
    # Miniconda isn't installed, so install it
    if sys.platform.startswith('darwin'):
        miniconda_sh = "Miniconda2-latest-MacOSX-x86_64.sh"
    elif sys.platform.startswith('linux'):
        miniconda_sh = "Miniconda2-latest-Linux-x86_64.sh"
    else:
        raise ValueError("Conda test not supported with platform %s " %
                         sys.platform)

    pymod_dir = os.path.join(test_dir, "minimal_pymodule")
    create_pymodule(pymod_dir)

    call("mkdir -p " + test_dir + "; "
         "cd " + test_dir + "; "
         "curl -O https://repo.continuum.io/miniconda/" + miniconda_sh + "; "
         "bash -b " + miniconda_sh + " -b -p ./miniconda; "
         "./miniconda/bin/conda create -y -n mytest python=2.7; "
         "./miniconda/bin/conda create -y -n empty; "
         "./miniconda/bin/conda install -y xz -n mytest; "
         "./miniconda/envs/mytest/bin/pip install rpaths; "
         "./miniconda/envs/mytest/bin/pip install -e " + pymod_dir + ";",
         shell=True)
    return test_dir


def test_get_conda_platform_from_python():
    assert get_conda_platform_from_python("linux2") == "linux"
    assert get_conda_platform_from_python("darwin") == "osx"

def test_conda_manager_identify_distributions(get_conda_test_dir):
    # Skip if network is not available (skip_if_no_network fails with fixtures)
    test_dir = get_conda_test_dir
    files = [os.path.join(test_dir, "miniconda/bin/sqlite3"),
             os.path.join(test_dir, "miniconda/envs/empty/conda-meta/history"),
             os.path.join(test_dir, "miniconda/envs/mytest/bin/xz"),
             os.path.join(test_dir, "miniconda/envs/mytest/lib/python2.7/site-packages/pip/index.py"),
             os.path.join(test_dir, "miniconda/envs/mytest/lib/python2.7/site-packages/rpaths.py"),
             "/sbin/iptables"]
    tracer = CondaTracer()
    dists = list(tracer.identify_distributions(files))

    assert len(dists) == 1, "Exactly one Conda distribution expected."

    (distributions, unknown_files) = dists[0]

    NicemanProvenance.write(sys.stdout, distributions)

    assert unknown_files == {
        "/sbin/iptables",
        os.path.join(test_dir, "minimal_pymodule"),
        os.path.join(test_dir, "miniconda/envs/empty/conda-meta/history")}

    assert distributions.platform.startswith(
        get_conda_platform_from_python(sys.platform)), \
        "A conda platform is expected."

    assert len(distributions.environments) == 3, \
        "Two conda environments are expected."

    out = {'environments': [{'name': 'root',
                             'packages': [{'files': ['bin/sqlite3'],
                                           'name': 'sqlite'}]},
                            {'name': 'mytest',
                             'packages': [{'files': ['bin/xz'],
                                           'name': 'xz'},
                                          {'files': ['lib/python2.7/site-packages/pip/index.py'],
                                           'name': 'pip'},
                                          {'files': ['lib/python2.7/site-packages/rpaths.py'],
                                           'installer': 'pip',
                                           'name': 'rpaths',
                                           'editable': False},
                                          {"files": [],
                                           "installer": "pip",
                                           "name": "nmtest",
                                           "editable": True}
                                          ]
                             }
                            ]
           }
    assert_is_subset_recur(out, attr.asdict(distributions), [dict, list])

    # conda packages are not repeated as "pip" packages.
    for pkg in distributions.environments[1].packages:
        if pkg.name == "pip":
            assert pkg.installer is None


def test_get_conda_env_export_exceptions():
    # Mock to capture logs
    def log_warning(msg, *args):
        log_warning.val = msg % args if args else msg

    # Mock to throw unrecognized argument exception
    def raise_unrec_args(_):
        raise Exception("conda-env: error: unrecognized arguments: -p"
                        "/home/butch/old_conda/")

    # Mock to raise some other exception
    def raise_other(_):
        raise Exception("unknown")

    from niceman.distributions.conda import lgr

    tracer = CondaTracer()
    with mock.patch.object(tracer._session, "execute_command",
                           raise_unrec_args), \
         mock.patch.object(lgr, "warning", log_warning):
        tracer._get_conda_env_export("", "/conda")
        assert "Please use Conda 4.3.19" in log_warning.val

    with mock.patch.object(tracer._session, "execute_command",
                           raise_other), \
        mock.patch.object(lgr, "warning", log_warning):
        tracer._get_conda_env_export("", "/conda")
        assert "unknown" in log_warning.val
