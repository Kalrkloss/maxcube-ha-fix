import base64
import logging
import threading
from time import sleep
from typing import List

from .connection import Connection
from .deadline import Deadline, Timeout
from .message import Message

logger = logging.getLogger(__name__)

QUIT_MSG = Message("q")
L_MSG = Message("l")
L_REPLY_CMD = L_MSG.reply_cmd()

UPDATE_TIMEOUT = Timeout("update", 5.0)
CONNECT_TIMEOUT = Timeout("connect", 3.0)
SEND_RADIO_MSG_TIMEOUT = Timeout("send-radio-msg", 30.0)
CMD_REPLY_TIMEOUT = Timeout("cmd-reply", 2.0)
RX_POLL_TIMEOUT = Timeout("rx-poll", 1.0)


class Commander(object):
    def __init__(self, host: str, port: int):
        self.__host: str = host
        self.__port: int = port
        self.__connection: Connection = None
        self.__unsolicited_messages: List[Message] = []
        self.__msg_lock = threading.Lock()
        self.__reply_event = threading.Event()
        self.__expected_cmd: str = None
        self.__reply: Message = None
        self.__rx_running = False
        self.__rx_thread: threading.Thread = None

    def disconnect(self):
        self.__stop_rx_thread()
        self.__close()

    def get_unsolicited_messages(self) -> List[Message]:
        with self.__msg_lock:
            result = self.__unsolicited_messages
            self.__unsolicited_messages = []
        return result

    def update(self) -> List[Message]:
        deadline = Deadline(UPDATE_TIMEOUT)
        try:
            if not self.__is_connected():
                self.__connect(deadline)
                self.__start_rx_thread()
            # Send "l:" to request fresh device list and wait for L response
            reply = self.__call(L_MSG, deadline)
            if reply:
                with self.__msg_lock:
                    self.__unsolicited_messages.append(reply)
        except Exception:
            self.__close()
            self.__stop_rx_thread()
        return self.get_unsolicited_messages()

    def send_radio_msg(self, hex_radio_msg: str) -> bool:
        deadline = Deadline(SEND_RADIO_MSG_TIMEOUT)
        request = Message(
            "s", base64.b64encode(bytearray.fromhex(hex_radio_msg)).decode("utf-8")
        )
        while not deadline.is_expired():
            if self.__cmd_send_radio_msg(request, deadline):
                return True
        return False

    def __cmd_send_radio_msg(self, request: Message, deadline: Deadline) -> bool:
        try:
            response = self.__call(request, deadline)
            duty_cycle, status_code, free_slots = response.arg.split(",", 3)
            if status_code == "0":
                return True
            logger.debug(
                "Radio message %s was not send [DutyCycle:%s, StatusCode:%s, FreeSlots:%s]"
                % (request, duty_cycle, status_code, free_slots)
            )
            if int(duty_cycle, 16) == 100 and int(free_slots, 16) == 0:
                sleep(deadline.remaining(upper_bound=10.0))
        except Exception as ex:
            logger.error("Error sending radio message to Max! Cube: " + str(ex))
        return False

    def __call(self, msg: Message, deadline: Deadline) -> Message:
        already_connected = self.__is_connected()
        if not already_connected:
            self.__connect(deadline.subtimeout(CONNECT_TIMEOUT))
            self.__start_rx_thread()

        expected_cmd = msg.reply_cmd()

        # Register expected reply before sending to avoid races
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
            if already_connected:
                return self.__call(msg, deadline)
            raise
        finally:
            with self.__msg_lock:
                self.__expected_cmd = None

    def __is_connected(self) -> bool:
        return self.__connection is not None

    def __connect(self, deadline: Deadline):
        with self.__msg_lock:
            self.__unsolicited_messages = []
        self.__connection = Connection(self.__host, self.__port)
        # Read all initial messages (H, L, maybe M) until L reply or timeout
        while not deadline.is_expired():
            msg = self.__connection.recv(deadline)
            if msg is None:
                break
            with self.__msg_lock:
                if msg.cmd == L_REPLY_CMD:
                    self.__unsolicited_messages.append(msg)
                    break
                self.__unsolicited_messages.append(msg)

    def __start_rx_thread(self):
        if self.__rx_running:
            return
        self.__rx_running = True
        self.__rx_thread = threading.Thread(target=self.__rx_loop, daemon=True)
        self.__rx_thread.start()

    def __stop_rx_thread(self):
        self.__rx_running = False
        if self.__rx_thread:
            self.__rx_thread.join(timeout=2)
            self.__rx_thread = None

    def __rx_loop(self):
        """Background thread: continuously read messages from the cube."""
        conn = self.__connection
        while self.__rx_running and conn is not None:
            try:
                msg = conn.recv(Deadline(RX_POLL_TIMEOUT))
                if msg is None:
                    continue
                # Dispatch: expected reply goes to __call, everything else is unsolicited
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

    def __close(self):
        self.__stop_rx_thread()
        if self.__connection:
            try:
                self.__connection.send(QUIT_MSG)
            except Exception:
                logger.debug("Failed to send quit message, ignoring")
        self.__connection.close()
        self.__connection = None
