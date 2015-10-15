""" Proxy for parrot devices.
"""
import json
import re
import socket
import telnetlib
import time
import threading
import zeroconf
import SocketServer


### CONFIGURATION
#
# The PROXY_IP is the IP of your interface that is *not* connected to your
# Jumping Sumo.
PROXY_IP = '192.168.20.3'


RECV_MAX = 10240


def get_first_sumo():
    """ Return the zeroconf name for the first Jumping Sumo you can find.

        This is a painful three-step process because the "service type"
        changes with updates to the firmware.
    """
    # First we need to detect the tcp:9 interface via zeroconf, this gives us
    # the IP of the bot.
    zc = zeroconf.Zeroconf()
    ip_list = []
    class TcpListener(object):

        def remove_service(self, zc, type, name):
            pass

        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name)
            if info.name.startswith('JumpingSumo-'):
                ip_list.append(socket.inet_ntoa(info.address))

    tcp_browser = zeroconf.ServiceBrowser(
        zc, '_ssh._tcp.local.', TcpListener()
    )
    while len(ip_list) == 0:
        time.sleep(0.1)
    tcp_browser.cancel()

    ip = ip_list[0]

    # Now we have the IP, we can connect via telnet and get the init port
    # zeroconf type.
    tconn = telnetlib.Telnet(ip, timeout=1)
    tconn.read_until('[JS] $ ')
    tconn.write('cat /etc/avahi/services/ardiscovery.service\r\n')
    data = tconn.read_until('[JS] $ ').replace('\r\n', '')

    service_type = re.search(r'>(_arsdk-\d+\._udp)<', data).groups()[0]
    init_port = int(re.search(r'<port>(\d+)</port>', data).groups()[0])

    return service_type + '.local.', ip, init_port


def announce_proxy_sumo(service_type, ip, init_port, service_name='JumpingSumo-SumoProxy'):
    """ Announce the proxied Jumping Sumo.
    """
    zc = zeroconf.Zeroconf()

    info = zeroconf.ServiceInfo(
        service_type,
        '.'.join((service_name, service_type)),
        socket.inet_aton(ip),
        init_port,
        properties={},
    )

    zc.register_service(info)


def proxy_init(sumo_ip, init_port):
    """ Proxy the init.
    """
    init_server = None
    return_data = []

    class InitHandler(SocketServer.BaseRequestHandler):
        """ SocketServer handler for init handshake.
        """
        def handle(self):

            client_ip = self.client_address[0]

            # Get and pass on the init request, capturing the d2c_port
            data = self.request.recv(RECV_MAX)
            d2c_port = json.loads(data[:-1])['d2c_port']
            sumo_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sumo_sock.connect((sumo_ip, init_port))
            sumo_sock.sendall(data)

            # Get and pass on the init response, capturing the c2d_port
            data = sumo_sock.recv(RECV_MAX)
            sumo_sock.close()

            c2d_port = json.loads(data[:-1])['c2d_port']
            self.request.sendall(data)

            return_data.extend((client_ip, c2d_port, d2c_port))

            def tidy():
                """ Clean up the server.
                """
                init_server.shutdown()
                init_server.server_close()
            threading.Thread(target=tidy).start()

    init_server = SocketServer.TCPServer(('', init_port), InitHandler)
    server_thread = threading.Thread(target=init_server.serve_forever)
    server_thread.start()
    server_thread.join()

    return return_data


def proxy_session(client_ip, sumo_ip, c2d_port, d2c_port):
    """ Proxy a UDP session between client and sumo.
    """
    # If the c2d and d2c ports are the same, we start a single server.
    if c2d_port == d2c_port:

        send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        class Handler(SocketServer.BaseRequestHandler):
            """ Handle all comms.
            """
            def handle(self):
                data = self.request[0]

                # From client to sumo
                if self.client_address[0] == client_ip:
                    print '>', repr(data)
                    send_socket.sendto(data, (sumo_ip, c2d_port))
                # From sumo to client
                else:
                    print '<', repr(data)
                    send_socket.sendto(data, (client_ip, c2d_port))

        server = SocketServer.UDPServer(('', c2d_port), Handler)
        threading.Thread(target=server.serve_forever).start()

    else:

        send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        class C2DHandler(SocketServer.BaseRequestHandler):
            """ Handle client to sumo comms.
            """
            def handle(self):
                data = self.request[0]
                #print '>', repr(data)
                send_socket.sendto(data, (sumo_ip, c2d_port))

        class D2CHandler(SocketServer.BaseRequestHandler):
            """ Handle sumo to client comms.
            """
            def handle(self):
                data = self.request[0]
                #print '<', repr(data)
                send_socket.sendto(data, (client_ip, d2c_port))

        c2d_server = SocketServer.UDPServer(('', c2d_port), C2DHandler)
        d2c_server = SocketServer.UDPServer(('', d2c_port), D2CHandler)
        threading.Thread(target=c2d_server.serve_forever).start()
        threading.Thread(target=d2c_server.serve_forever).start()


def main():
    """ Handle all the things.
    """
    # Find the robot
    print 'Searching for Jumping Sumo...',
    service_type, sumo_ip, init_port = get_first_sumo()
    print 'Done!'

    # Announce equivalent sumo
    print 'Announcing Sumo Proxy...',
    announce_proxy_sumo(
        service_type,
        PROXY_IP,
        init_port,
    )
    print 'Done!'

    print 'Waiting for client initiation...',
    client_ip, c2d_port, d2c_port = proxy_init(sumo_ip, init_port)
    print 'Done!'

    print 'Serving session...',
    proxy_session(client_ip, sumo_ip, c2d_port, d2c_port)
    print 'Done!'

    if c2d_port == 0:
        print 'Another client already connected!'
        import sys; sys.exit(1)
    else:
        print 'Press Ctrl-C to quit...'
        while True:
            time.sleep(0.1)


if __name__ == '__main__':

    main()
