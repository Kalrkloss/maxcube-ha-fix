"""
MAX! Cube TCP commander.

Manages a persistent TCP connection to the MAX! Cube LAN Gateway.
A background RX thread continuously receives messages (push notifications),
while synchronous commands (l:, s:) use threading.Event to await replies.

Protocol:
  - H: cube header (serial, firmware, duty cycle)
  - M: room/device metadata
  - L: device list with current status (temperatures, valve, window, battery)
  - C: device configuration (comfort/eco temps, weekly program)
  - S: radio command response (duty cycle, free slots)
  - N: new device discovered during scan
  - A: acknowledge
"""

import base64
import logging
import threading
from time import sleep
from typing import List

from .connection import Connection
from .deadline import Deadline, Timeout
from .message import Message

logger = logging.getLogger(__name__)

# ── Protocol message constants ──────────────────────────────────────────

QUIT_MSG = Message("q")        # Request clean connection close
L_MSG = Message("l")           # Request device list (current status)
L_REPLY_CMD = L_MSG.reply_cmd()  # 'L' — expected response command

# ── Timeouts ────────────────────────────────────────────────────────────

UPDATE_TIMEOUT = Timeout("update", 5.0)
CONNECT_TIMEOUT = Timeout("connect", 3.0)
SEND_RADIO_MSG_TIMEOUT = Timeout("send-radio-msg", 30.0)
CMD_REPLY_TIMEOUT = Timeout("cmd-reply", 2.0)
# RX thread uses a short poll timeout so it can detect shutdown signals
# within at most this many seconds
RX_POLL_TIMEOUT = Timeout("rx-poll", 1.0)


class Commander(object):
    """
    TCP client for the MAX! Cube LAN Gateway.

    Architecture:
    - Single persistent TCP connection (no close/reconnect on every poll)
    - Background RX thread reads all incoming data
    - Synchronous __call() sends a command and waits for its reply via
      threading.Event, while the RX thread dispatches the response
    - Unsolicited messages (push notifications from the cube) accumulate
      in __unsolicited_messages and are collected by get_unsolicited_messages()
    """

    def __init__(self, host: str, port: int):
        self.__host: str = host
        self.__port: int = port
        self.__connection: Connection = None
        # Buffer for unsolicited / non-reply messages (push data)
        self.__unsolicited_messages: List[Message] = []
        # Protects __unsolicited_messages, __expected_cmd, and __reply
        self.__msg_lock = threading.Lock()
        # Signalled by RX thread when a message matching __expected_cmd arrives
        self.__reply_event = threading.Event()
        self.__expected_cmd: str = None  # Cmd the main thread is waiting for
        self.__reply: Message = None     # The matched reply message
        self.__rx_running = False
        self.__rx_thread: threading.Thread = None

    # ── Public API ─────────────────────────────────────────────────────

    def disconnect(self):
        """Close the connection and stop the RX thread."""
        self.__stop_rx_thread()
        self.__close()

    def get_unsolicited_messages(self) -> List[Message]:
        """
        Return and clear the buffer of unsolicited messages received
        since the last call. Thread-safe.
        """
        with self.__msg_lock:
            result = self.__unsolicited_messages
            self.__unsolicited_messages = []
        return result

    def update(self) -> List[Message]:
        """
        Request fresh device list from the cube.

        Sends 'l:' on the persistent connection and collects the response
        plus any unsolicited messages that arrived since the last update.

        Returns: list of Message objects (L, C, H, etc.)
        """
        deadline = Deadline(UPDATE_TIMEOUT)
        try:
            # Auto-connect on first call
            if not self.__is_connected():
                self.__connect(deadline)
                self.__start_rx_thread()
            # Send "l:" to request fresh device list and wait for L response.
            # The RX thread picks up the reply and signals __reply_event.
            reply = self.__call(L_MSG, deadline)
            if reply:
                with self.__msg_lock:
                    self.__unsolicited_messages.append(reply)
        except Exception:
            # On any error, close and reconnect next time
            self.__close()
            self.__stop_rx_thread()
        # Return all accumulated messages (including the L reply above)
        return self.get_unsolicited_messages()

    def send_radio_msg(self, hex_radio_msg: str) -> bool:
        """
        Send a radio command to the cube (e.g. set temperature, set mode).

        The cube will relay the message via RF to the target device.
        Retries until the deadline expires if the cube reports duty cycle
        saturation.
        """
        deadline = Deadline(SEND_RADIO_MSG_TIMEOUT)
        request = Message(
            "s", base64.b64encode(bytearray.fromhex(hex_radio_msg)).decode("utf-8")
        )
        while not deadline.is_expired():
            if self.__cmd_send_radio_msg(request, deadline):
                return True
        return False

    # ── Internal helpers ───────────────────────────────────────────────

    def __cmd_send_radio_msg(self, request: Message, deadline: Deadline) -> bool:
        """Send one radio message and check the response status."""
        try:
            response = self.__call(request, deadline)
            duty_cycle, status_code, free_slots = response.arg.split(",", 3)
            if status_code == "0":
                return True
            logger.debug(
                "Radio message %s was not send [DutyCycle:%s, StatusCode:%s, FreeSlots:%s]"
                % (request, duty_cycle, status_code, free_slots)
            )
            # If cube is at 100% duty cycle, wait before retrying
            if int(duty_cycle, 16) == 100 and int(free_slots, 16) == 0:
                sleep(deadline.remaining(upper_bound=10.0))
        except Exception as ex:
            logger.error("Error sending radio message to Max! Cube: " + str(ex))
        return False

    def __call(self, msg: Message, deadline: Deadline) -> Message:
        """
        Send a message and wait for its expected reply.

        Thread-safe: the RX thread reads the response and signals
        __reply_event when it matches msg.reply_cmd().

        On failure, if we were previously connected, retry once with a
        fresh connection (transparent recovery).
        """
        already_connected = self.__is_connected()
        if not already_connected:
            self.__connect(deadline.subtimeout(CONNECT_TIMEOUT))
            self.__start_rx_thread()

        expected_cmd = msg.reply_cmd()

        # Register expected reply BEFORE sending to avoid races:
        # the RX thread could receive the response between send() and
        # the __reply_event.wait() call if we set it up after.
        with self.__msg_lock:
            self.__expected_cmd = expected_cmd
            self.__reply = None
        self.__reply_event.clear()

        try:
            self.__connection.send(msg)
            remaining = deadline.remaining(lower_bound=0.001)
            if not self.__reply_event.wait(timeout=remaining):
                raise TimeoutError(str(deadline))
            with self.__msg_lock:
                result = self.__reply
                self.__reply = None
            if result is None:
                raise TimeoutError(str(deadline))
            return result
        except Exception:
            self.__close()
            self.__stop_rx_thread()
            # Retry once with a new connection
            if already_connected:
                return self.__call(msg, deadline)
            raise
        finally:
            # Always clear expected cmd so subsequent push messages
            # are treated as unsolicited
            with self.__msg_lock:
                self.__expected_cmd = None

    def __is_connected(self) -> bool:
        return self.__connection is not None

    def __connect(self, deadline: Deadline):
        """
        Open TCP connection to the cube and read initial messages.

        On connect, the cube automatically sends:
          H: header (serial, firmware)
          M: metadata (rooms, device list)
          L: device list with current status

        We collect all until we see the L reply.
        """
        with self.__msg_lock:
            self.__unsolicited_messages = []
        self.__connection = Connection(self.__host, self.__port)
        # Loop: read messages until we get the L reply or timeout
        while not deadline.is_expired():
            msg = self.__connection.recv(deadline)
            if msg is None:
                break
            with self.__msg_lock:
                if msg.cmd == L_REPLY_CMD:
                    self.__unsolicited_messages.append(msg)
                    break
                self.__unsolicited_messages.append(msg)

    # ── Background RX thread ──────────────────────────────────────────

    def __start_rx_thread(self):
        """Start the background RX thread if not already running."""
        if self.__rx_running:
            return
        self.__rx_running = True
        self.__rx_thread = threading.Thread(target=self.__rx_loop, daemon=True)
        self.__rx_thread.start()

    def __stop_rx_thread(self):
        """
        Stop the RX thread.

        Sets the running flag to False; the thread will wake up within
        RX_POLL_TIMEOUT seconds and exit. Joins with a 2s timeout.
        """
        self.__rx_running = False
        if self.__rx_thread:
            self.__rx_thread.join(timeout=2)
            self.__rx_thread = None

    def __rx_loop(self):
        """
        Background thread: continuously read messages from the cube.

        Runs as long as __rx_running is True and the connection exists.
        Uses a short timeout (RX_POLL_TIMEOUT = 1s) so it can detect
        shutdown requests promptly.

        Message dispatch:
        - If a message matches __expected_cmd, it's delivered to the
          waiting __call() via __reply_event + __reply
        - Everything else goes to __unsolicited_messages (push data)
        """
        conn = self.__connection
        while self.__rx_running and conn is not None:
            try:
                msg = conn.recv(Deadline(RX_POLL_TIMEOUT))
                if msg is None:
                    # Timeout or no data yet — keep polling
                    continue
                # Dispatch: expected reply goes to __call,
                # everything else is an unsolicited push message
                with self.__msg_lock:
                    if self.__expected_cmd and msg.cmd == self.__expected_cmd:
                        self.__reply = msg
                        self.__reply_event.set()
                    else:
                        self.__unsolicited_messages.append(msg)
            except Exception:
                if self.__rx_running:
                    logger.debug("RX thread error, stopping", exc_info=True)
                break
        self.__rx_running = False

    # ── Connection lifecycle ────────────────────────────────────────────

    def __close(self):
        """
        Close the connection cleanly.

        Stops the RX thread first, then sends 'q:' to tell the cube
        we're disconnecting (so it frees the connection slot), then
        closes the TCP socket.
        """
        self.__stop_rx_thread()
        if self.__connection:
            try:
                # Send quit message so the cube cleans up its side of
                # the connection and accepts new ones immediately
                self.__connection.send(QUIT_MSG)
            except Exception:
                logger.debug("Failed to send quit message, ignoring")
        self.__connection.close()
        self.__connection = None
