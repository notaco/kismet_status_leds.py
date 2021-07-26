#!/usr/bin/env python3

# "THE BEER-WARE LICENSE" (Revision 42):
# notaco (http://github.com/notaco) wrote this file.  As long as you retain this
# notice you can do whatever you want with this stuff. If we meet some day, and
# you think this stuff is worth it, you can buy me a beer in return.

### CONFIGURATION
#
# websocket connection configuration process
#
# - configuration priority is given to command line argument, see below or --help for usage. when used as a
#   plugin connection arguments are not available so credentials can be loaded elsewhere as defined.
# - if connection arguments are not passed, then an apikey from kismet session db if defined here:
session_db_file = '~/.kismet/session.db' # location of session db file
session_db_name = 'external plugin' # name of api key to load
#   an apikey named 'external plugin' is created and loaded by kismetexternal python library, default if undefined
#   an apikey named 'web logon' is created and used by kismet web interface
#   a dedicated apikey and name can by generated in the settings of kismet web interface, a role of readonly
#   should work without issue.
# - if session db configuration is not defined or fails then try to read the user/password from the conf file
#   note: looking for httpd_username and httpd_password in kismet config, the web interface creates file in the
#   home directory, but only if they are not already defined in other config files (ie kismet_site.conf)
httpd_config_file = '~/.kismet/kismet_httpd.conf'
# - if all else fails attempt to import kismetexternal and look for IPC arguments which are passed to plugins.
#   using IPC request an apikey to make a websocket connection.
# - additionally when a localhost connection is set up using files or kismetexternal the KISMET_ETC environmental
#   variable (provided to kismet plugins) is used to check config files for port and uri prefix. if these are
#   set to non-default values and files are setting up the connection consider setting the environmental variable.
#   alternatively set httpd_local_port here if configuring by file with non-default local port.
#httpd_local_port = 2501
# - lastly a test websocket connection is made to check the configuration and will quit on fail. if the
#   configuration is known good but kismet is not yet running or reachable use argument --skip-test to proceed
#   to main loop without testing
#
# gpio configuration
#
# libgpiod should provide support across manufacturer. this gpio_chip setting provided is for raspberry pi boards.
# the chip for other manufacturers, if not published, can be found using gpiodetect and gpioinfo programs available
# in gpiod package commonly provided in linux distros.
gpio_chip = "pinctrl-bcm2835" # raspberry pi
# line offset for led that is illuminated when websocket is connected. undefined leds will be ignored (unused)
gpio_led_ws_connected = 12
# blink the ws led when a datasource error is caught, keep blinking until ws disconnection or a event (new
# datasource or datasource open) suggesting the device has restarted. some remote datasource reconnections seem
# to be missed.
gpio_led_ws_err_blink = True
gpio_led_ws_err_blink_duration = .5
# line offset for led that is illuminated when gps fix is reported
gpio_led_gps_fix = 16
# 1/2 second blink for 2d fixes, event is published every second
gpio_led_gps_2d_fix_duration = .5
# stay lit for 3d fixes
gpio_led_gps_3d_fix_duration = -1
# line offset for led that is illuminated when new device is found and duration (in seconds) to remain on. a
# duration <= 0 will stay on (ie. if you are only looking for a rare phy type to investigate) Note: that
# currently packet blink does not check dev status
gpio_led_dev_found = 26
gpio_led_dev_found_duration = .5
# blink dev led if we are receiving packets
gpio_led_dev_packet = True
# short blink, event 
gpio_led_dev_packet_duration = .2
#
### END CONFIGURATION

#load modules
import argparse, json, sys, os, traceback, asyncio, socket
try:
    import websockets
except ImportError:
    print("Failed to load websockets python3 module. installation is available from pip")
    sys.exit(1)

class KismetStatusLeds(object):
    def __init__(self):
        # initialize config
        self.apikey = None
        self.username = None
        self.password = None
        self.remote_host = None
        try: self.remote_port = httpd_local_port
        except: self.remote_port = None
        self.httpd_uri_prefix = ''
        self.endpoint = '/eventbus/events.ws'
        self.timeout = 30
        self.reconnect_sec = 5

        # init config status indicator
        self.ws_ready = False

        # set up argument parser
        self.parser = argparse.ArgumentParser(description='Kismet status leds, a python plugin for Kismet')
        self.parser.add_argument('--in-fd', action="store", type=int, dest="infd", help="incoming fd pair (IPC mode only)")
        self.parser.add_argument('--out-fd', action="store", type=int, dest="outfd", help="outgoing fd pair (IPC mode only)")
        self.parser.add_argument('--connect', action="store", dest="connect", help="remote Kismet server on host:port")
        self.parser.add_argument("--user", action="store", dest="user", help="Kismet username for websocket eventbus")
        self.parser.add_argument("--password", action="store", dest="password", help="Kismet password for websocket eventbus")
        self.parser.add_argument("--apikey", action="store", dest="apikey", help="Kismet API key for websocket eventbus")
        self.parser.add_argument("--skip-test", action="store_true", default=False, dest="skip_test", help="skip test connection and go to main loop")
        self.parser.add_argument("--no-gpio", action="store_true", default=False, dest="no_gpio", help="don't use gpio code, used for testing")
        self.results = self.parser.parse_args()

        # event bus subscriptions to send
        self.subscriptions = ['GPS_LOCATION', 'MESSAGE', 'DATASOURCE_ERROR', 'DATASOURCE_OPENED', 'NEW_DATASOURCE', 'PACKETCHAIN_STATS']

        # set up gpio
        self.gpio = { 'blinking': {} }
        if self.results.no_gpio:
            self.gpio['ignore'] = True
        else:
            self.gpio['ignore'] = False
            try:
                import gpiod
                self.gpio['chip'] = gpiod.chip(gpio_chip)
            except ImportError:
                print("Failed to load gpiod python3 module. installation is available from pip")
                sys.exit(1)
            except Exception as err:
                traceback.print_tb(err.__traceback__)
                print(err)
                print("kismet_status_leds.py: Unable to setup gpio chip!")
                sys.exit(1)
            try: gpio_led_ws_connected
            except NameError: print("kismet_status_leds.py: no gpio pin set for websocket connection status")
            else:
                self.gpio['ws'] = self.gpio['chip'].get_line(gpio_led_ws_connected)
                config = gpiod.line_request()
                config.consumer = "kismet_status_leds.py"
                config.request_type = gpiod.line_request.DIRECTION_OUTPUT
                self.gpio['ws'].request(config)
                self.gpio['blinking']['ws'] = 0
            try: gpio_led_gps_fix
            except NameError: print("kismet_status_leds.py: no gpio pin set for gps fix status")
            else:
                self.gpio['gps'] = self.gpio['chip'].get_line(gpio_led_gps_fix)
                config = gpiod.line_request()
                config.consumer = "kismet_status_leds.py"
                config.request_type = gpiod.line_request.DIRECTION_OUTPUT
                self.gpio['gps'].request(config)
                self.gpio['blinking']['gps'] = 0
            try: gpio_led_dev_found
            except NameError: print("kismet_status_leds.py: no gpio pin set for device found indication")
            else:
                self.gpio['devs'] = self.gpio['chip'].get_line(gpio_led_dev_found)
                config = gpiod.line_request()
                config.consumer = "kismet_status_leds.py"
                config.request_type = gpiod.line_request.DIRECTION_OUTPUT
                self.gpio['devs'].request(config)
                self.gpio['blinking']['devs'] = 0

        # start configuring connection with arguments passed
        # case using connect argument (remote session or non-default port), split and require apikey or user/pass
        if not self.results.connect is None:
            eq = self.results.connect.find(":")
            if eq == -1:
                print("Error: Expected host:port for remote websocket connect.")
                sys.exit(1)

            self.remote_host = self.results.connect[:eq]
            self.remote_port = int(self.results.connect[eq+1:])

            if (self.results.user is None or self.results.password is None) and self.results.apikey is None:
                print("Error: username and password or API key required with remote websocket.")
                sys.exit(1)
            elif not self.results.apikey is None:
                self.apikey = self.results.apikey
            else:
                self.username = self.results.user
                self.password = self.results.password
            self.ws_ready = True
            print("Remote websocket connection configured via arguments.")
        # case using apikey or user/pass for localhost with default port, priority to apikey or check for both user/pass
        elif not self.results.apikey is None or not self.results.user is None or not self.results.password is None:
            if not self.results.apikey is None:
                self.apikey = self.results.apikey
            elif self.results.user is None or self.results.password is None:
                print("Error: username and password required for local websocket.")
                sys.exit(1)
            else:
                self.username = self.results.user
                self.password = self.results.password
            self.remote_host = 'localhost'
            self.remote_port = 2501
            self.ws_ready = True
            print("Local websocket credential configured via arguments")
            print("Defaulting to http port 2501, use --connect if kismet is on different port.")

        # if no arguments try to configure using session.db file and name if defined
        if not self.ws_ready:
            global session_db_name
            try: session_db_file
            except NameError: print("kismet_status_leds.py: no session_db_file set for reading apikey")
            else:
                if not os.path.isfile(os.path.expanduser(session_db_file)) or not os.access(os.path.expanduser(session_db_file), os.R_OK):
                    print("kismet_status_leds.py: session_db_file ({}) not found or unable to read".format(session_db_file))
                else:
                    with open(os.path.expanduser(session_db_file)) as f:
                        fc = f.read()
                    session_db = json.loads(fc)
                    try: session_db_name
                    except NameError:
                        print('kismet_staus_leds.py: session_db_name not defined defaulting to "external plugin"')
                        # defaulting of name
                        session_db_name = "external plugin"
                    sdb_key = None
                    for ob in session_db:
                        if 'name' in ob.keys():
                            if ob['name'] == session_db_name:
                                sdb_key = ob["token"]
                    if sdb_key is None:
                        print("kismet_status_leds.py: unable to find key for '{}' in {}".format(session_db_name, session_db_file))
                    else:
                        self.apikey = sdb_key
                        self.remote_host = 'localhost'
                        self.remote_port = self.get_local_port()
                        self.ws_ready = True
                        print("kismet_status_leds.py: apikey loaded from {}".format(session_db_file))

        # still not set up? try to find user/pass from defined config file
        if not self.ws_ready:
            try: httpd_config_file
            except NameError: print("kismet_status_leds.py: no httpd_config_file set for reading username/password")
            else:
                if not os.path.isfile(os.path.expanduser(httpd_config_file)) or not os.access(os.path.expanduser(httpd_config_file), os.R_OK):
                    print("kismet_status_leds.py: httpd_config_file ({}) not found or unable to read".format(httpd_config_file))
                else:
                    httpd_conf = {}
                    with open(os.path.expanduser(httpd_config_file)) as f:
                        for l in f:
                            key, value = l.strip().partition("=")[::2]
                            httpd_conf[key] = value
                    if not 'httpd_password' in httpd_conf or not 'httpd_username' in httpd_conf:
                        print("kismet_status_leds.py: unable to find http_password and http_username in {}".format(httpd_config_file))
                    else:
                        self.username = httpd_conf['httpd_username']
                        self.password = httpd_conf['httpd_password']
                        self.remote_host = 'localhost'
                        self.remote_port = self.get_local_port()
                        self.ws_ready = True
                        print("kismet_status_leds.py: username and password loaded from {}".format(httpd_config_file))

        # still not configured, check if kismetexternal is available
        if not self.ws_ready and (not self.results.infd is None and not self.results.outfd is None):
            try:
                import kismetexternal
            except ImportError:
                print("ERROR: Using kismet_status_leds.py as plugin requires configuration")
                print("       in the script or the installation of kismetexternal python3 library")
                print("       from pip or source (available using git).")
                sys.exit(1)
            print("kismet_status_leds.py: loaded as plugin using KismetExternal {}".format(kismetexternal.__version__))

            if self.version_check(kismetexternal.__version__):
                self.kei = kismetexternal.ExternalInterface(self.results)
            else:
                self.kei = kismetexternal.ExternalInterface(self.results.infd, self.results.outfd)

            self.kei.start()
            if self.kei.auth_token is None:
                # this sets up a callback to http_auth()
                self.kei.request_http_auth(self.http_auth)
            else:
                self.http_auth()
            self.kei.run()

        # either configured or out of options to find configuration
        if self.ws_ready:
            self.check_config()
        else:
            print("kismet_status_leds.py: Failed to find configuration for websocket connection")
            print("                       For command line config options add --help")
            sys.exit(1)

    # found credential from file or kismetexternal see if a non-default port is used
    def get_local_port(self):
        # actually respect prior definition of remote_port
        if isinstance(self.remote_port, int):
            return self.remote_port
        etc_dir = None
        if not "KISMET_ETC" in os.environ:
            print("kismet_status_leds.py: KISMET_ETC environmental variable not set, unable to check configs for httpd_port. using 2501")
            return 2501
        etc_dir = os.environ["KISMET_ETC"]
        etc_files = ['/kismet_httpd.conf', '/kismet_site.conf']
        etc_port = None
        for conf_file in etc_files:
            if not os.path.isfile(etc_dir + conf_file) or not os.access(etc_dir + conf_file, os.R_OK):
                print("kismet_status_leds.py: skipping kismet config file ({}) not found or unable to read".format(etc_dir + conf_file))
            else:
                kis_conf = {}
                with open(etc_dir + conf_file) as f:
                    for l in f:
                        key, value = l.strip().partition("=")[::2]
                        kis_conf[key] = value
                if 'httpd_port' in kis_conf:
                    etc_port = int(kis_conf['httpd_port'])
                    print("kismet_status_leds.py: httpd_port {} loaded from {}".format(etc_port, etc_dir + conf_file))
                if 'httpd_uri_prefix' in kis_conf:
                    self.config.httpd_uri_prefix = kis_conf['httpd_uri_prefix']
                    print("kismet_status_leds.py: httpd_uri_prefix '{}' loaded from {}".format(kis_conf['httpd_uri_prefix'], etc_dir + conf_file))
        if etc_port is None:
            print("kismet_status_leds.py: unable to load httpd_port from config files. using 2501")
            return 2501
        else:
            return etc_port

    # check kismetexternal version for init parameters (change coming in git version)
    def version_check(self, version):
        part = version.split(".");
        if int(part[0]) > 2020:
            return 1
        elif int(part[0]) < 2020:
            return 0
        else:
            if int(part[1]) >= 10:
                return 1
            else:
                return 0

    # callback from kismetexternal once it has an auth_token
    def http_auth(self):
        self.apikey = self.kei.auth_token
        self.remote_host = 'localhost'
        self.remote_port = self.get_local_port()
        self.ws_ready = True
        self.kei.kill()
        self.check_config()

    # check the ws config and create an uri for the connection
    def check_config(self):
        self.ws_ready = False
        uri = None
        if not self.remote_host or not self.remote_port:
            print("kismet_status_leds.py: remote_host or remote_port not found in config")
            sys.exit(1)
        if self.apikey:
            self.ws_uri = "ws://{}:{}{}{}?KISMET={}".format(self.remote_host, self.remote_port,
                                    self.httpd_uri_prefix, self.endpoint, self.apikey)
        elif self.username and self.password:
            self.ws_uri = "ws://{}:{}@{}:{}{}{}".format(self.username, self.password,
                                    self.remote_host, self.remote_port, self.httpd_uri_prefix, self.endpoint)
        # using asyncio to call test of connnection
        self.ws_loop = asyncio.get_event_loop()
        self.ws_loop.run_until_complete(self.ws_test())
        if not self.ws_ready:
            print("kismet_status_leds.py: initial websocket connection failed!")
            sys.exit(1)

    # make ws connection to test configuration
    async def ws_test(self):
        # argument says not to bother
        if self.results.skip_test:
            self.ws_ready = True
        else:
            ws_con = None
            try:
                ws_con = await asyncio.wait_for(websockets.connect(self.ws_uri), self.timeout)
            except websockets.exceptions.InvalidStatusCode as err:
                print(err)
                print("kismet_status_leds.py: websocket connection returned bad status, check credentials!")
            except ConnectionRefusedError as err:
                print(err)
                print("kismet_status_leds.py: websocket connection refused, check config!")
            except socket.gaierror as err:
                print(err)
                print("kismet_status_leds.py: name resolution failed, check config!")
            except Exception as err:
                traceback.print_tb(err.__traceback__)
                print(err)
            if ws_con is None:
                self.ws_ready = False
            else:
                try:
                    await asyncio.wait_for(ws_con.send('{"SUBSCRIBE": "TIMESTAMP"}'), self.timeout)
                    data = await asyncio.wait_for(ws_con.recv(), self.timeout)
                    ts = json.loads(data)
                    ts['TIMESTAMP']['kismet.system.timestamp.usec']
                    await asyncio.wait_for(ws_con.send('{"UNSUBSCRIBE": "TIMESTAMP"}'), self.timeout)
                    self.ws_ready = True
                    await ws_con.close()
                except Exception as err:
                    traceback.print_tb(err.__traceback__)
                    print(err)
                    self.ws_ready = False

    async def ws_listener(self):
        while True:
            try:
                async with websockets.connect(self.ws_uri) as ws_con:
                    try:
                        for event in self.subscriptions:
                            await ws_con.send(json.dumps({'SUBSCRIBE': event}))
                    except Exception as err:
                        traceback.print_tb(err.__traceback__)
                        print(err)
                        print("Error sending subscibe statments!")
                        continue
                    while True:
                        await self.gpio_on('ws')
                        self.ws_ready = True
                        try:
                            ev_msg = await asyncio.wait_for(ws_con.recv(), self.timeout)
                            event = json.loads(ev_msg)
                            if 'GPS_LOCATION' in event:
                                if self.parse_gps_3d_fix(event['GPS_LOCATION']):
                                    asyncio.ensure_future(self.gpio_on('gps', gpio_led_gps_3d_fix_duration))
                                elif self.parse_gps_2d_fix(event['GPS_LOCATION']):
                                    asyncio.ensure_future(self.gpio_on('gps', gpio_led_gps_2d_fix_duration))
                                else:
                                    await self.gpio_off('gps')
                            if 'MESSAGE' in event:
                                if self.parse_new_dev(event['MESSAGE']):
                                    asyncio.ensure_future(self.gpio_on('devs', gpio_led_dev_found_duration))
                            if 'DATASOURCE_ERROR' in event and gpio_led_ws_err_blink:
                                self.gpio['blinking']['ws'] = gpio_led_ws_err_blink_duration
                                asyncio.ensure_future(self.gpio_on('ws', gpio_led_ws_err_blink_duration))
                            if 'DATASOURCE_OPENED' in event and gpio_led_ws_err_blink:
                                self.gpio['blinking']['ws'] = 0
                                await self.gpio_on('ws')
                            if 'NEW_DATASOURCE' in event and gpio_led_ws_err_blink:
                                self.gpio['blinking']['ws'] = 0
                                await self.gpio_on('ws')
                            if 'PACKETCHAIN_STATS' in event:
                                if self.parse_packetchain_stat(event['PACKETCHAIN_STATS']) and gpio_led_dev_packet:
                                    await self.gpio_on('devs', gpio_led_dev_packet_duration)
                        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                            try:
                                pong = await ws_con.ping()
                                await asyncio.wait_for(pong, self.timeout)
                                continue
                            except:
                                break
                        except Exception as err:
                            traceback.print_tb(err.__traceback__)
                            print(err)
                            print("Error with handling received data!")
                            continue
            except Exception as err:
                if self.ws_ready:
                    print("kismet_status_leds.py: websocket connection error, is kismet running?")
                self.ws_ready = False
                await self.gpio_off('gps')
                self.gpio['blinking']['ws'] = 0
                await self.gpio_off('ws')
                await asyncio.sleep(self.reconnect_sec)
                continue

    def parse_gps_2d_fix(self, gps_msg):
        if 'kismet.common.location.fix' in gps_msg:
            if gps_msg['kismet.common.location.fix'] == 2:
                return True
            else:
                return False
        else:
            return False

    def parse_gps_3d_fix(self, gps_msg):
        if 'kismet.common.location.fix' in gps_msg:
            if gps_msg['kismet.common.location.fix'] == 3:
                return True
            else:
                return False
        else:
            return False

    def parse_new_dev(self, msg):
        if 'kismet.messagebus.message_string' in msg:
            if "Detected new " in msg['kismet.messagebus.message_string'] and " device " in msg['kismet.messagebus.message_string']:
                return True
            else:
                return False
        else:
            return False

    def parse_packetchain_stat(self, stats):
        try:
            prrd = stats['kismet.packetchain.packets_rrd']
            offset = prrd['kismet.common.rrd.serial_time'] % 60
            if prrd['kismet.common.rrd.minute_vec'][offset-1] > 0:
                return True
            else:
                return False
        except:
            return False

    async def gpio_on(self, led, on_time=-1):
        if led in self.gpio:
            self.gpio[led].set_value(1)
            if on_time > 0:
                await asyncio.sleep(on_time)
                self.gpio[led].set_value(0)
                if self.gpio['blinking'][led] > 0:
                    await asyncio.sleep(self.gpio['blinking'][led])
                    if self.gpio['blinking'][led] > 0:
                        asyncio.ensure_future(self.gpio_on(led, self.gpio['blinking'][led]))
        elif self.gpio['ignore']:
            print(f"LED name '{led}' set on for {on_time} seconds!")

    async def gpio_off(self, led):
        if led in self.gpio:
            self.gpio[led].set_value(0)
        elif self.gpio['ignore']:
            print(f"LED name '{led}' set off!")

    def main_loop(self):
        self.ws_loop.create_task(self.ws_listener())
        self.ws_loop.run_forever()

if __name__ == "__main__":
    ksl = KismetStatusLeds()
    try:
        ksl.main_loop()
    except KeyboardInterrupt:
        sys.exit(0)
