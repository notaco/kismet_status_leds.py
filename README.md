# kismet_status_leds.py
a python plugin/script using gpio attached leds to display status of kismet wireless scanner

## Requirements

* Kismet (local or remote) with eventbus websocket endpoint
* Python 3 with gpiod and websockets modules (available from pip)
* Gpio controller supported by libgpiod
* Leds, resistors and proper wiring

## Configuration

Currently configuration is done using variables at top of the script with plenty of comments explaining usage. Assuming connecting to a Kismet session on localhost, the section covering websockets connection should not require modification. The section covering gpio should at least be edited to reflect gpio pins to which leds are wired. If chip and line settings are not known use gpiodetect and gpioinfo programs often included in gpiod package offered by distros. *test-led.py* is provided to test configuration, it accepts chip and pin as arguments and will blink the specified led. Note user needs read/write permissions for the gpiochip device (ie "/dev/gpiochip0") being used. If gpio group is available adding the user should ensure access.

## Usage

The script can be called in a shell with arguments passed. Passing arguments will take priority so any configuration for reading credentials from files. When no arguments are passed configured kismet_httpd.conf and/or session.db file will be read for connecting to local Kismet session.

**--user / --password**

**---apikey**

Specify credential used for websocket connection. When used alone a local connection will be attempted

**--connect** *hostname:port*

Use to provide remote host or specify port for localhost. Arguments for user/password or apikey will also be required.

**--skip-test**

A test websocket connection is made prior to starting the main loop, if the test fails then improper configuration is assumed and the script exits. This overrides the test and moves to main loop. Use this if Kismet is not *yet* running or reachable.

### Plugin

After configuring leds, install using 

    sudo make install
When running as a plugin, websocket connection configuration can fail back to using the optional python module *kismetexternal* (available through pip).

## Events and Lights

Currently the script is set up to use 3 leds:

* websocket connected (gpio 12) will stay on while websocket is connected
* websocket led will blink when there is a datasource error, it will continue to blink until there is a reconnection or an event (NEW_DATASOURCE or DATASOURCE_OPEN) that implies reconnection of the source
* gps lock (gpio 16) will blink on/off while Kismet reports a 2d gps fix
* gps lock will stay on while Kismet reports a 3d gps fix
* dev found (gpio 26) will flash on for .5 second when Kismet reports a new device
* dev led will blink on for .2 second every second if Kismet reports packets were gathered

Configuration presently is limited to disabling datasource error blinking and/or packet seen blinking, as well as adjusting blink durations.

## Todo

* Field test
* Add more led state awareness
* Clean up main loop (ws_listener), event parsing and gpio control
* Structure gpio lines, events and state changing configuration in more coherent and extensible way
* Add more events to reflect need/interest

## Wrapping up

In the way of OSS this was written to fill personal need (scratch an itch) to easily (and safely) keep an eye on my crappy gps module while cycling. Maybe others will find use in it, let me know (open to bug reports, feature request etc.) Lastly, folks who find this usefully might take a look at [my fork of elkentaro's KismetMobileDashboard](https://github.com/notaco/KismetMobileDashboard)

*notaco*

Usage is under "THE BEER-WARE LICENSE"