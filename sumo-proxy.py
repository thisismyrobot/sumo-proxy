""" Proxy for parrot devices.
"""
import collections
import json
import netifaces
import socket
import time
import threading
import zeroconf
import SocketServer


# UDP Port to listen to data from, and port to send data to
REPEAT_PORT = 65432

# Host that UDP data is sent to
REPEAT_HOST = '127.0.0.1'


def ip_addresses():
    """ Return all my IP addresses.
    """
    addresses = []
    for interface in netifaces.interfaces():
        try:
            for link in netifaces.ifaddresses(interface)[netifaces.AF_INET]:
                addresses.append(link['addr'])
        except KeyError:
            pass
    return sorted(addresses)


class SumoProxy(object):
    """ Proxy for Jumping Sumo to display data.
    """
    RECV_MAX = 102400

    def __init__(self):
        self._zc = zeroconf.Zeroconf()

        # Monkey-patch the UDP socket server to recieve video packets.
        SocketServer.UDPServer.max_packet_size = 65000

    def get_first_sumo(self, service_type='_arsdk-0902._udp.local.'):
        """ Return the IP and INIT port for the first Jumping Sumo you find.
        """
        connection_info = []

        class Listener(object):
            """ A simple listener for the sumo init service.
            """
            def remove_service(self, zc, type_, name):
                """ We're not concerned with the remove_service event.
                """
                pass

            def add_service(self, zc, type_, name):
                """ If we've found the JumpingSumo service, get the info.
                """
                info = zc.get_service_info(type_, name)
                connection_info.extend(
                    (socket.inet_ntoa(info.address), info.port)
                )

        browser = zeroconf.ServiceBrowser(
            self._zc, service_type, Listener()
        )

        wait_time = 30  # Seconds
        started = time.time()
        while len(connection_info) < 2:
            if not browser.is_alive():
                raise Exception('Zeroconf Browser crashed')
            if time.time() - started > wait_time:
                raise Exception(
                    'No Sumo found within {} seconds'.format(wait_time)
                )
            time.sleep(0.1)
        browser.cancel()

        return connection_info[:2]

    def announce_proxy_sumo(self, init_port,
                            service_name='Sumo',
                            service_type='_arsdk-0902._udp.local.'):
        """ Announce the proxied Jumping Sumo on all interfaces.
        """
        for address in ip_addresses():
            iface_service_name = '{}-{}'.format(
                service_name,
                address.replace('.', '-')
            )
            info = zeroconf.ServiceInfo(
                service_type,
                '.'.join((iface_service_name, service_type)),
                socket.inet_aton(address),
                init_port,
                properties={},
            )
            self._zc.register_service(info)

    def proxy_init(self, sumo_ip, init_port):
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
                data = self.request.recv(SumoProxy.RECV_MAX)
                d2c_port = json.loads(data[:-1])['d2c_port']
                sumo_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sumo_sock.connect((sumo_ip, init_port))
                sumo_sock.sendall(data)

                # Get and pass on the init response, capturing the c2d_port
                data = sumo_sock.recv(SumoProxy.RECV_MAX)
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

        wait_time = 30
        server_thread.join(wait_time)

        # If thread's still alive we didn't have an init
        if server_thread.is_alive():
            raise Exception(
                'No init within {} seconds of announce'.format(wait_time)
            )

        return return_data

    def proxy_session(self, client_ip, sumo_ip, c2d_port, d2c_port):
        """ Proxy a UDP session between client and sumo.
        """
        data_queue = collections.deque([True], maxlen=1)
        send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # If the c2d and d2c ports are the same, we start a single server.
        if c2d_port == d2c_port:

            class Handler(SocketServer.BaseRequestHandler):
                """ Handle all comms.
                """
                def handle(self):
                    data_queue.append(True)
                    data = self.request[0]

                    # From client to sumo
                    if self.client_address[0] == client_ip:
                        send_socket.sendto(data, (sumo_ip, c2d_port))

                        # Tee-off the data to another host
                        send_socket.sendto('>'+data, (REPEAT_HOST, REPEAT_PORT))

                    # From sumo to client
                    else:
                        send_socket.sendto(data, (client_ip, c2d_port))

                        # Tee-off the data to another host
                        send_socket.sendto('<'+data, (REPEAT_HOST, REPEAT_PORT))

            server = SocketServer.UDPServer(('', c2d_port), Handler)
            t = threading.Thread(target=server.serve_forever)
            t.daemon = True
            t.start()

        else:

            class C2DHandler(SocketServer.BaseRequestHandler):
                """ Handle client to sumo comms.
                """
                def handle(self):
                    data_queue.append(True)
                    data = self.request[0]
                    send_socket.sendto(data, (sumo_ip, c2d_port))
                    # Tee-off the data to another host
                    send_socket.sendto('>'+data, (REPEAT_HOST, REPEAT_PORT))

            class D2CHandler(SocketServer.BaseRequestHandler):
                """ Handle sumo to client comms.
                """
                def handle(self):
                    data_queue.append(True)
                    data = self.request[0]
                    send_socket.sendto(data, (client_ip, d2c_port))

                    # Tee-off the data to another host
                    send_socket.sendto('<'+data, (REPEAT_HOST, REPEAT_PORT))

            c2d_server = SocketServer.UDPServer(('', c2d_port), C2DHandler)
            d2c_server = SocketServer.UDPServer(('', d2c_port), D2CHandler)
            t1 = threading.Thread(target=c2d_server.serve_forever)
            t1.daemon = True
            t1.start()
            t2 = threading.Thread(target=d2c_server.serve_forever)
            t2.daemon = True
            t2.start()

        comms_time = 1
        while True:
            try:
                data_queue.pop()
            except IndexError:
                raise Exception(
                    'No comms for more than {} seconds'.format(comms_time)
                )
            time.sleep(comms_time)

    def start(self):
        """ Handle all the things.
        """
        # Find the robot
        print 'Searching for Jumping Sumo...',
        sumo_ip, init_port = self.get_first_sumo()
        print 'Done!'

        # Announce equivalent sumo
        print 'Announcing Sumo Proxy...',
        self.announce_proxy_sumo(init_port)
        print 'Done!'

        print 'Waiting for client initiation...',
        client_ip, c2d_port, d2c_port = self.proxy_init(sumo_ip, init_port)
        print 'Done!'

        if c2d_port == 0:
            raise Exception('Another client already connected!')

        print 'Serving session...',
        self.proxy_session(client_ip, sumo_ip, c2d_port, d2c_port)
        print 'Done!'


def proc_wrapper():
    """ Run the proxy.
    """
    try:
        proxy = SumoProxy()
        proxy.start()
    except Exception as ex:
        print 'Ex: {}'.format(ex)


if __name__ == '__main__':

    import multiprocessing
    print 'Starting...'
    while True:
        proc = multiprocessing.Process(target=proc_wrapper)
        proc.start()
        proc.join()
        print 'Restarting...'
        time.sleep(1)
