# Sumo Proxy

![Terrible Visio](/visio_is_awesome.png?raw=true)

Proxy server for Parrot Jumping Sumo.

## Operation

First, connect your computer to the Jumping Sumo.

Running proxy.py will find that Sumo and create a proxy version hosted on
every network interface (including the one with the Sumo on it).

You can use your normal controller to (e.g. iPad) to connect to the Sumo on
any of these interfaces, while sumo-proxy repeats the UDP data (e.g. Battery
Voltages or Video) over UDP to another host (use sumo-proxy-printer.py to
view).
