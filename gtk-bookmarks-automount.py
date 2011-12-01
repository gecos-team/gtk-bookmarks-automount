# -*- Mode: Python; coding: utf-8; indent-tabs-mode: nil; tab-width: 4 -*-

# This file is part of Guadalinex
#
# This software is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this package; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

__author__ = "Antonio Hernández <ahernandez@emergya.com>"
__copyright__ = "Copyright (C) 2011, Junta de Andalucía <devmaster@guadalinex.org>"
__license__ = "GPL-2"

import os
import shlex
import subprocess
import gobject
#import gtk
import urlparse
import gnomekeyring
import dbus
from dbus.mainloop.glib import DBusGMainLoop

LOCK_FILE = os.path.join(os.environ['HOME'], '.gtk-bookmarks-automount.lock')
GTK_BOOKMARKS = os.path.join(os.environ['HOME'], '.gtk-bookmarks')
GVFS_MOUNT = '/usr/bin/env gvfs-mount'

NM_DBUS_SERVICE = "org.freedesktop.NetworkManager"
NM_DBUS_OBJECT_PATH = "/org/freedesktop/NetworkManager"

NM_STATE_UNKNOWN = 0    # Networking state is unknown.
NM_STATE_ASLEEP = 10    # Networking is inactive and all devices are disabled.
NM_STATE_DISCONNECTED = 20    # There is no active network connection.
NM_STATE_DISCONNECTING = 30    # Network connections are being cleaned up.
NM_STATE_CONNECTING = 40    # A network device is connecting to a network and there is no other available network connection.
NM_STATE_CONNECTED_LOCAL = 50    # A network device is connected, but there is only link-local connectivity.
NM_STATE_CONNECTED_SITE = 60    # A network device is connected, but there is only site-local connectivity.
NM_STATE_CONNECTED_GLOBAL = 70    # A network device is connected, with global network connectivity.

def log(str):
    print str

def run_command(cmd):
    args = shlex.split(cmd)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    exit_code = os.waitpid(process.pid, 0)
    output = process.communicate()[0]
    output = output.strip()

    #print cmd, exit_code
    #if exit_code[1] != 0:
    #    raise Exception(output)

    # PID, exit code, output
    return (exit_code[0], exit_code[1], output)

def read_shares():

    shares = []

    try:
        f = open(GTK_BOOKMARKS, 'r')
        lines = f.readlines()
        f.close()
        def f(x): return x.startswith('smb://')
        shares = filter(f, lines)

    except IOError as e:
        log('Could not read the shared resources from %s' % (GTK_BOOKMARKS,))

    return shares

def has_credentials(share):
    ret = False
    parsed_uri = list(urlparse.urlparse(share))
    protocol = parsed_uri[0]
    host = parsed_uri[1]
    attrs = {'server': host, 'protocol': protocol}
    items = gnomekeyring.find_items_sync(gnomekeyring.ITEM_NETWORK_PASSWORD, attrs)
    log(items)
    ret = len(items) > 0
    return ret

def on_nm_state_changed(state):
    if state == NM_STATE_CONNECTED_GLOBAL:
        log('NM_STATE_CONNECTED_GLOBAL signal received.')
        shares = read_shares()
        for share in shares:

            share = share.strip()
            if not has_credentials(share):
                continue

            log('Trying to mount %s ...' % (share,))
            cmd = '%s %s' % (GVFS_MOUNT, share)
            pid, ret, output = run_command(cmd)

            if ret == 0:
                msg = '\tShared %s has been mounted: ret_val == %s' % (share, ret)
            else:
                msg = '\tShared %s could not be mounted: ret_val == %s' % (share, ret)

            log(msg)

def get_lock():
    if os.path.exists(LOCK_FILE):
        return False

    try:
        f = open(LOCK_FILE, 'w')
        f.write(str(os.getpid()))
        f.close()
        return True

    except IOError as e:
        log('Could not write lock file in %s' % (LOCK_FILE,))
        return False

def main():

    if not get_lock():
        return

    DBusGMainLoop(set_as_default = True)
    bus = dbus.SystemBus()
    proxy = bus.get_object(NM_DBUS_SERVICE, NM_DBUS_OBJECT_PATH)
    proxy.connect_to_signal('StateChanged', on_nm_state_changed)

    try:
        #gtk.main()
        loop = gobject.MainLoop()
        loop.run()
    finally:
        os.unlink(LOCK_FILE)

if __name__ == '__main__':
    main()
