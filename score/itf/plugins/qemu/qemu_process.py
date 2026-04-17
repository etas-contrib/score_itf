# *******************************************************************************
# Copyright (c) 2025 Contributors to the Eclipse Foundation
#
# See the NOTICE file(s) distributed with this work for additional
# information regarding copyright ownership.
#
# This program and the accompanying materials are made available under the
# terms of the Apache License Version 2.0 which is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0
# *******************************************************************************
import logging
import subprocess
import time             # DEBUG

from score.itf.core.process.console import PipeConsole
from score.itf.plugins.qemu.qemu import Qemu

logger = logging.getLogger(__name__)


class QemuProcess:
    def __init__(
        self,
        qemu_arch,
        path_to_qemu_image,
        qemu_disk,
        available_ram,
        available_cores,
        network_adapters=[],
        host_qemu_network=None,
    ):
        self._path_to_qemu_image = path_to_qemu_image
        self._available_ram = available_ram
        self._available_cores = available_cores
        self._network_adapters = network_adapters
        self._host_qemu_network = host_qemu_network
        self._qemu = Qemu(
            qemu_arch,
            self._path_to_qemu_image,
            qemu_disk,
            self._available_ram,
            self._available_cores,
            network_adapters=self._network_adapters,
            host_qemu_network=self._host_qemu_network,
        )
        self._console = None

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        logger.info("Starting Qemu...")
        logger.info(f"Using QEMU image: {self._path_to_qemu_image}")
        subprocess_params = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
        }
        # pylint: disable=too-many-function-args
        qemu_subprocess = self._qemu.start(subprocess_params)
        self._console = PipeConsole("QEMU", qemu_subprocess)
        #############################################################################
        # To analyze a solution for pre_tests_phase failures on pipelines
        # SLEEP 5s for DEBUG HERE:
        logging.info("Sleeping for 20s to debug pre_tests_phase failures on pipelines...")
        time.sleep(20)
        logging.info("Finished sleeping for 20s.")
        # then evaluate a while loop with a 10s timeout on the "stdout": subprocess.PIPE
        # waiting at least for the "---> Starting sshd" deamon up and running or
        # "---> Starting sshd" to ensure that the OS is up and running
        #############################################################################
        return self

    def stop(self):
        logger.info("Stopping Qemu...")
        self._qemu.stop()

    def restart(self):
        self.stop()
        self.start()

    @property
    def console(self):
        return self._console
