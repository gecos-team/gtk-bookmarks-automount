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
import signal
import shlex
import subprocess
import gobject
import urlparse
import syslog
import gnomekeyring
import dbus
from dbus.mainloop.glib import DBusGMainLoop
from multiprocessing import Process

loop = None
sm_client = None

LOCK_FILE = os.path.join(os.environ['HOME'], '.gtk-bookmarks-automount.lock')
GTK_BOOKMARKS = os.path.join(os.environ['HOME'], '.gtk-bookmarks')
GVFS_MOUNT = '/usr/bin/env gvfs-mount'
WATCHED_PROTOCOLS = ('smb://',)

DESKTOP_AUTOSTART_ID = os.getenv('DESKTOP_AUTOSTART_ID')

SM_DBUS_SERVICE = 'org.gnome.SessionManager'
SM_DBUS_OBJECT_PATH = '/org/gnome/SessionManager'
SM_DBUS_CLIENT_PRIVATE_PATH = 'org.gnome.SessionManager.ClientPrivate'
SM_DBUS_CLIENT_ID = None

NM_DBUS_SERVICE = 'org.freedesktop.NetworkManager'
NM_DBUS_OBJECT_PATH = '/org/freedesktop/NetworkManager'

NM_STATE_UNKNOWN = 0    # Networking state is unknown.
NM_STATE_ASLEEP = 10    # Networking is inactive and all devices are disabled.
NM_STATE_DISCONNECTED = 20    # There is no active network connection.
NM_STATE_DISCONNECTING = 30    # Network connections are being cleaned up.
NM_STATE_CONNECTING = 40    # A network device is connecting to a network and there is no other available network connection.
NM_STATE_CONNECTED_LOCAL = 50    # A network device is connected, but there is only link-local connectivity.
NM_STATE_CONNECTED_SITE = 60    # A network device is connected, but there is only site-local connectivity.
NM_STATE_CONNECTED_GLOBAL = 70    # A network device is connected, with global network connectivity.


def log(message, priority=syslog.LOG_INFO):
    syslog.syslog(priority, message)

def run_command(cmd):
    args = shlex.split(cmd)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    exit_code = os.waitpid(process.pid, 0)
    output = process.communicate()

    pid = exit_code[0]
    retval = exit_code[1]
    stdout = output[0].strip()
    stderr = output[1].strip()

    return (pid, retval, stdout, stderr)

def read_shares():
    ''' Read the remote shared resources and filter them
    by protocol according to WATCHED_PROTOCOLS list.
    Return a tuple of shares.
    '''

    shares = []

    try:
        f = open(GTK_BOOKMARKS, 'r')
        lines = f.readlines()
        f.close()
        def f(x): return x.startswith(WATCHED_PROTOCOLS)
        shares = filter(f, lines)

    except IOError as e:
        log('Could not read the shared resources from %s' % (GTK_BOOKMARKS,), syslog.LOG_ERR)

    return shares

def shared_has_credentials(shared):
    ''' Search for shared resource credentials in gnome-keyring.
    This script only will try to connect to the shared resource if the
    credentials were stored in the keyring.
    '''

    parsed_uri = list(urlparse.urlparse(shared))
    protocol = parsed_uri[0]
    host = parsed_uri[1]
    attrs = {'server': host, 'protocol': protocol}
    try:
        items = gnomekeyring.find_items_sync(gnomekeyring.ITEM_NETWORK_PASSWORD, attrs)
        ret = len(items) > 0
    except gnomekeyring.NoMatchError as e:
        ret = False
    return ret

def mount_shared(shared):
    log('Trying to mount %s ...' % (shared,))
    cmd = '%s %s' % (GVFS_MOUNT, shared)
    pid, retval, stdout, stderr = run_command(cmd)
    msg = '%s %s: ret_val == %s' % (stdout.strip(), stderr.strip(), retval)
    log('%s: %s' % (shared, msg.strip()))

def get_lock():
    if os.path.exists(LOCK_FILE):
        try:
            f = open(LOCK_FILE, 'r')
            pid = f.read()
            pid = 'PID ' + pid.strip()
            f.close()

        except IOError as e:
            pid = 'another PID.'

        log('Could not get lock, process exists with %s' % (pid,), syslog.LOG_ERR)
        return False

    try:
        f = open(LOCK_FILE, 'w')
        f.write(str(os.getpid()))
        f.close()
        return True

    except IOError as e:
        log('Could not write lock file in %s' % (LOCK_FILE,), syslog.LOG_ERR)
        return False

def on_nm_state_changed(state):
    ''' Handle the NetworkManager state changed signal. If we have
    global network connectivity then try to connect to the resources.
    We launch a new thread for each resource.
    '''

    if state == NM_STATE_CONNECTED_GLOBAL:

        log('NM_STATE_CONNECTED_GLOBAL signal received.')
        shares = read_shares()

        for shared in shares:
            shared = shared.strip()
            if shared_has_credentials(shared):
                Process(target=mount_shared, args=(shared,)).start()

def on_query_end_session(flags):
    ''' The QueryEndSession signal is emited by gnome-session when
    someone / something is asking for logout the session.
    We have to respond in one second if we agree or not.
    '''

    try:
        end_session_response = sm_client.get_dbus_method('EndSessionResponse', SM_DBUS_CLIENT_PRIVATE_PATH)
        end_session_response(True, '')

    except Exception as e:
        log(str(e))

def on_end_session(flags):
    ''' The EndSession signal is emited by gnome-session when
    the session is about to end. We have to respond in ten second if
    we agree or not.
    '''

    try:
        end_session_response = sm_client.get_dbus_method('EndSessionResponse', SM_DBUS_CLIENT_PRIVATE_PATH)
        end_session_response(True, '')

    except Exception as e:
        log(str(e))

def on_cancel_end_session():
    ''' The CancelEndSession signal is emited by gnome-session when
    some application respond not-OK in the QueryEndSession or EndSession
    signals.
    '''
    pass

def on_stop_session():
    ''' The Stop signal is emited by gnome-session when
    the session is going to be terminated.
    '''
    loop.quit()

def register_dbus_client():

    global SM_DBUS_CLIENT_ID

    session_bus = dbus.SessionBus()
    sm = session_bus.get_object(SM_DBUS_SERVICE, SM_DBUS_OBJECT_PATH)

    register_client = sm.get_dbus_method('RegisterClient', SM_DBUS_SERVICE)
    SM_DBUS_CLIENT_ID = register_client('gtk-bookmarks-automount', DESKTOP_AUTOSTART_ID)

def connect_dbus_signals():

    global sm_client

    session_bus = dbus.SessionBus()
    sm_client = session_bus.get_object(SM_DBUS_SERVICE, SM_DBUS_CLIENT_ID)
    sm_client.connect_to_signal('QueryEndSession', on_query_end_session)
    sm_client.connect_to_signal('EndSession', on_end_session)
    sm_client.connect_to_signal('CancelEndSession', on_cancel_end_session)
    sm_client.connect_to_signal('Stop', on_stop_session)

    system_bus = dbus.SystemBus()
    nm = system_bus.get_object(NM_DBUS_SERVICE, NM_DBUS_OBJECT_PATH)
    nm.connect_to_signal('StateChanged', on_nm_state_changed)

def main():

    global loop

    if DESKTOP_AUTOSTART_ID is None:
        log('This script is intended to be executed from xdg-autostart, \
inside a gnome-session context.', syslog.LOG_ERR)
        return

    if not get_lock():
        return

    DBusGMainLoop(set_as_default = True)

    try:
        register_dbus_client()
        connect_dbus_signals()

        log('Starting gtk-bookmarks automount script...')
        loop = gobject.MainLoop()
        loop.run()

    except Exception as e:
        log(str(e), syslog.LOG_ERR)

    try:
        os.unlink(LOCK_FILE)

    except Exception as e:
        log(str(e), syslog.LOG_ERR)

    log('gtk-bookmarks automount script stoped.')

if __name__ == '__main__':
    main()
